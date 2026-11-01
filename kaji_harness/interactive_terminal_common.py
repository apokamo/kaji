"""Terminal-backend independent helpers for the interactive terminal runner.

Wrapper command assembly, Codex session resolution, abnormal-exit session resolution and
terminal transcript diagnostics used by the Herdr backend. This module never touches a
terminal multiplexer: it must not import the dispatch, Herdr or recorder
modules (``tests/test_interactive_terminal_common.py`` checks the import boundary).
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .cli import find_high_confidence_sensitive_pattern, find_transient_pattern
from .errors import SessionResolution
from .models import Step
from .prompt import skill_invocation_line
from .recovery import FINGERPRINT_LIMIT, normalize_error_text

_CODEX_RESUME_RE = re.compile(
    r"\bcodex\s+resume\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_CODEX_SESSION_FILE_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)
_CODEX_SESSION_SCAN_LIMIT = 100
_TERMINAL_LOG_TAIL_CHARS = 2000
# Issue #296: provider エラー行の周辺を人間向け抜粋として保つ半径（文字数）。
_DIAGNOSTIC_EXCERPT_RADIUS = 200
# TUI redraw が撒き散らす ANSI CSI / OSC / その他 escape シーケンス。
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OTHER_ESCAPE_RE = re.compile(r"\x1b[@-Z\\\]^_]")
# 改行/タブ以外の C0 制御文字、DEL、C1 制御文字（Issue #137 の JSON エスケープ規約とは
# 別目的: こちらは可読な診断テキストを作るための除去）。
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Issue #527: wrapper.sh が agent 起動前に出す banner の終端行（wrapper.sh と契約テストで固定）。
# banner は ``model:`` 等の正規化で消えない実行ごとの値を含むため、原因行の候補から外す。
_WRAPPER_BANNER_END = (
    "The agent must read prompt_path and write a pure YAML verdict to verdict_path."
)
# Issue #527: 原因行とみなすエラー行（行頭 anchor）。Node / Python / Java の ``<Name>Error:`` /
# ``Exception:``、Rust/clap の ``error:`` / ``error[E0433]:``、git の ``fatal:``、Go の ``panic:``、
# Node heap OOM の ``FATAL ERROR:``、Rust の ``thread '<name>' [(<id>)] panicked at``。
_RUST_PANIC_RE = re.compile(r"^\s*thread '[^']*'(?: \(\d+\))? panicked at ")
_ERROR_LINE_RE = re.compile(
    r"^\s*(?:"
    r"(?:[\w.]*(?:Error|Exception)|(?i:fatal\s+error|error|fatal|panic))(?:\s*\[[^\]]*\])?:"
    r"|thread '[^']*'(?: \(\d+\))? panicked at "
    r")"
)
# Rust panic の本文（見出しの次行以降）の連結を止める行頭。
_RUST_PANIC_BODY_STOP = ("note: ", "stack backtrace:")
# 要約前に除く実行固有値（signature.py の正規化が扱わない形）。``_ABS_PATH_RE`` は lookbehind の
# ため ``file:///...`` の path を置換しない。thread ID は 3 桁以下だと ``_LONG_NUM_RE`` に掛からない。
_FILE_URI_PATH_RE = re.compile(r"file://(?:/[\w.\-@+%~]+)+/?")
_RUST_PANIC_THREAD_ID_RE = re.compile(r"(thread '[^']*') \(\d+\)( panicked at )")
# 複数行の要約の区切り。transient pattern は ``|`` を含まないので、行をまたぐ一致を作らない。
_CAUSE_LINE_SEPARATOR = " | "
# 原因行の候補にする banner 後の末尾の行数（review-design DR1）。文字数の窓は path 長などの実行固有値で
# どの行が候補に入るかが変わり原因行を落とすため、行の長さに依存しない行数で限定する。Node の uncaught
# exception（原因行 + 既定 10 frame + 空行 + ``Node.js vX``）・Python traceback・Rust panic の原因行を含み、
# 長い session の途中で表示された古い ``error:`` 行を最後の原因と誤認しにくい大きさ。
_CAUSE_TAIL_LINES = 40
# transient guard が一致箇所の空白に置く印。``<`` ``>`` は transient pattern に含まれない。
_TRANSIENT_WHITESPACE_MARKER = "<WS>"
_NO_AGENT_OUTPUT_SUMMARY = "no agent output line in transcript tail"
# Issue #238: pane scoped user options (`set-option -p`) are used as the kaji


@dataclass(frozen=True)
class TerminalDiagnostic:
    """pane 早期終了時の transcript 診断結果。

    ``kind`` は ``"provider_error"`` / ``"no_pattern"`` / ``"no_log"`` / ``"empty"`` の
    4 値で、provider 側の一時障害と kaji 側の診断抽出失敗を語彙上区別する。
    ``matched_pattern`` は ``kind == "provider_error"`` のときのみ非 ``None``。
    ``clean_excerpt`` / ``clean_tail`` は ``pane-metadata.json`` 専用の人間向け参考情報
    であり、classification / sensitive gate の入力には使わない。
    ``sensitive_marker`` は ``kind == "provider_error"`` の transcript に高確信 auth/
    permission marker（bare token は除くが、``invalid token`` の高確信複合語は含む）
    が同居する場合のみ非 ``None``。transient と sensitive が同一 transcript に共存
    するケースで safety gate を構造的に迂回しないため、``_terminal_exit_detail`` が
    組み立てるメッセージにこの値を含める。
    ``cause_summary`` は ``kind == "no_pattern"`` のときのみ非 ``None``（Issue #527）。
    banner 後の末尾 ``_CAUSE_TAIL_LINES`` 行から選んだ失敗原因行を正規化した、例外メッセージ用の
    要約である。
    ``clean_excerpt`` / ``clean_tail`` と違いメッセージに載るが、``no_pattern`` の
    メッセージは transient / sensitive の分類で候補にならない（transcript 全文に transient
    pattern が無く、要約もそれを作らない）。``pane-metadata.json`` には書かない。
    """

    kind: str
    matched_pattern: str | None
    clean_excerpt: str | None
    clean_tail: str
    sensitive_marker: str | None = None
    cause_summary: str | None = None


def _strip_ansi(text: str) -> str:
    """ANSI CSI/OSC escape と C0/C1 制御文字を除去し、可読テキストにする。"""
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OTHER_ESCAPE_RE.sub("", text)
    return _CONTROL_CHARS_RE.sub("", text)


def extract_terminal_diagnostic(text: str) -> TerminalDiagnostic:
    """transcript 全文から transient provider error を走査する純関数。

    ANSI/制御除去後の全文に対し ``find_transient_pattern`` を適用する。末尾窓に
    限定しないため、TUI redraw で肥大した transcript でも中盤〜先頭のエラーを
    取りこぼさない。
    """
    clean = _strip_ansi(text).strip()
    if not clean:
        return TerminalDiagnostic(
            kind="empty", matched_pattern=None, clean_excerpt=None, clean_tail=""
        )
    tail = clean[-_TERMINAL_LOG_TAIL_CHARS:]
    matched = find_transient_pattern(clean)
    if matched is None:
        return TerminalDiagnostic(
            kind="no_pattern",
            matched_pattern=None,
            clean_excerpt=None,
            clean_tail=tail,
            cause_summary=_transcript_cause_summary(clean),
        )
    idx = clean.lower().find(matched.lower())
    start = max(0, idx - _DIAGNOSTIC_EXCERPT_RADIUS)
    end = min(len(clean), idx + len(matched) + _DIAGNOSTIC_EXCERPT_RADIUS)
    return TerminalDiagnostic(
        kind="provider_error",
        matched_pattern=matched,
        clean_excerpt=clean[start:end],
        clean_tail=tail,
        sensitive_marker=find_high_confidence_sensitive_pattern(clean),
    )


def _transcript_cause_summary(clean: str) -> str:
    """``no_pattern`` の transcript から失敗原因行の要約を作る（Issue #527）。

    ``clean`` を完全な行に分け、wrapper banner より後の末尾 ``_CAUSE_TAIL_LINES`` 行を候補にし、
    後ろから見て最初のエラー行（無ければ最初の空でない出力行）を選ぶ。文字数の窓は使わないので、
    実行固有値の長さで候補や選ばれる行は変わらない。Rust panic の見出しは次行以降の本文を連結する。
    選んだ行は実行固有値を除いて ``normalize_error_text`` に 1 回だけ通すので、同じ原因なら実行ごとの
    値の違いによらず同じ文字列になる。有界化は正規化と、guard 後の最終出力への ``FINGERPRINT_LIMIT``
    の適用だけで行い、raw の文字数では切らない。

    Args:
        clean: ANSI/制御文字を除去し前後の空白を落とした transcript 全文。transient pattern を
            含まないこと（``no_pattern`` の前提）。

    Returns:
        ``"last error line: ..."`` / ``"last output line: ..."`` /
        ``"no agent output line in transcript tail"`` のいずれか。改行を含まず、transient pattern
        を含まず、ラベルの後ろは ``FINGERPRINT_LIMIT`` 文字以下。
    """
    lines = clean.splitlines()
    banner_end = max(
        (index for index, line in enumerate(lines) if line.strip() == _WRAPPER_BANNER_END),
        default=-1,
    )
    candidates = lines[banner_end + 1 :][-_CAUSE_TAIL_LINES:]

    selected = _find_last_line(candidates, lambda line: _ERROR_LINE_RE.match(line) is not None)
    label = "last error line"
    if selected is None:
        selected = _find_last_line(candidates, lambda line: bool(normalize_error_text(line)))
        label = "last output line"
    if selected is None:
        return _NO_AGENT_OUTPUT_SUMMARY

    cause_lines = [candidates[selected].strip()]
    if label == "last error line" and _RUST_PANIC_RE.match(candidates[selected]):
        # 現行の Rust は panic の原因本文を見出しの次行以降に出す。
        for line in candidates[selected + 1 :]:
            body = line.strip()
            if body.startswith(_RUST_PANIC_BODY_STOP) or not normalize_error_text(body):
                break
            cause_lines.append(body)

    summary = normalize_error_text(
        _CAUSE_LINE_SEPARATOR.join(_strip_run_specific(line) for line in cause_lines)
    )
    # guard の <WS> 置換は文字数を増やすので、最終出力にも上限を掛ける（review-code R2）。
    # transient 判定は部分文字列一致なので、一致を含まない文字列の先頭部分も一致を含まない。
    return f"{label}: {_break_transient_match(summary)[:FINGERPRINT_LIMIT]}"


def _find_last_line(lines: list[str], predicate: Callable[[str], bool]) -> int | None:
    """``predicate`` を満たす最後の行の index を返す。無ければ ``None``。"""
    for index in range(len(lines) - 1, -1, -1):
        if predicate(lines[index]):
            return index
    return None


def _break_transient_match(summary: str) -> str:
    """正規化済み要約に成立した transient pattern の一致箇所の空白を ``<WS>`` にする（Issue #527）。

    ``no_pattern`` の transcript は transient pattern を含まないが、空白正規化（TAB・連続空白・NBSP
    を 1 空白にする）で一致が成立しうる。一致箇所の空白だけを印に置き換え、実行固有値の除去・
    正規化・有界化を済ませた ``summary`` だけの決定的関数として transient 判定を変えない。
    置換は空白を減らし英字同士を新たに隣接させないので、新しい一致を作らず有限回で止まる。

    Args:
        summary: ``normalize_error_text`` 済みの要約。

    Returns:
        transient pattern を含まない要約。一致が無ければ ``summary`` そのもの。
    """
    while (matched := find_transient_pattern(summary)) is not None:
        words = matched.split()
        if len(words) < 2:
            # 空白を含まない pattern は空白正規化では成立しない（no_pattern の前提に反する）。
            break
        pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
        summary = pattern.sub(
            lambda match: re.sub(r"\s+", _TRANSIENT_WHITESPACE_MARKER, match.group(0)), summary
        )
    return summary


def _strip_run_specific(line: str) -> str:
    """file URI の path と Rust panic の thread ID を除く（Issue #527 review-design R2/R1）。"""
    line = _FILE_URI_PATH_RE.sub("file://<PATH>", line)
    return _RUST_PANIC_THREAD_ID_RE.sub(r"\1\2", line)


def read_terminal_diagnostic(terminal_log: Path) -> TerminalDiagnostic:
    """``terminal_log`` を読み、診断抽出失敗（no_log/empty）も含めて判定する薄い I/O ラッパ。"""
    if not terminal_log.is_file():
        return TerminalDiagnostic(
            kind="no_log", matched_pattern=None, clean_excerpt=None, clean_tail=""
        )
    text = terminal_log.read_text(encoding="utf-8", errors="replace")
    return extract_terminal_diagnostic(text)


def _terminal_exit_detail(terminal_log: Path, *, prefix: str) -> str:
    """Build a diagnostic string for early pane exits from the transcript.

    Issue #296: ``kind`` で分岐する。``provider_error`` は transcript の部分文字列を
    一切載せず、一致した canonical transient pattern の literal のみを載せる
    （sensitive gate の誤発火を構造的に避けるため）。ただし transcript に高確信
    auth/permission marker（bare token は除くが、``invalid token`` の高確信複合語は
    含む）が同居する場合は、その canonical marker literal も併記する。
    safety gate（``recovery.handler._safety_gates``）は
    result.json / run.log の構造化エラー文字列のみを読み、pane-metadata.json の
    生 transcript は読まないため、ここで literal を残さないと transient+sensitive
    混在 failure の gate が構造的に迂回されてしまう。
    """
    return _format_terminal_exit_detail(
        read_terminal_diagnostic(terminal_log), log_name=terminal_log.name, prefix=prefix
    )


def _format_terminal_exit_detail(
    diagnostic: TerminalDiagnostic, *, log_name: str, prefix: str
) -> str:
    """診断結果から早期終了の detail 文字列を組み立てる純関数。

    Issue #527: ``no_pattern`` は端末ログ全体の tail ではなく ``cause_summary``（原因行の
    要約）を載せる。他の ``kind`` の文言は変えない。

    Args:
        diagnostic: ``read_terminal_diagnostic`` / ``extract_terminal_diagnostic`` の結果。
        log_name: ``no_log`` / ``empty`` の文言に入れる transcript のファイル名。
        prefix: 早期終了の種類を表す先頭の固定文。

    Returns:
        ``CLIExecutionError`` に渡す detail（改行を含まない）。
    """
    if diagnostic.kind == "provider_error":
        detail = (
            f"{prefix}; transient provider error detected (pattern: '{diagnostic.matched_pattern}')"
        )
        if diagnostic.sensitive_marker is not None:
            detail += (
                f"; sensitive marker also detected in transcript "
                f"(pattern: '{diagnostic.sensitive_marker}')"
            )
        return detail
    if diagnostic.kind == "no_pattern":
        summary = diagnostic.cause_summary or _NO_AGENT_OUTPUT_SUMMARY
        return f"{prefix}; no known provider error pattern in transcript; {summary}"
    if diagnostic.kind == "no_log":
        return f"{prefix}; diagnostic unavailable: no {log_name}"
    return f"{prefix}; diagnostic unavailable: {log_name} empty"


def _wrapper_path() -> Path:
    """Resolve the packaged ``assets/interactive-terminal/wrapper.sh``.

    The wrapper ships as package data under ``kaji_harness/assets`` so it is
    available both from a source checkout and from an installed wheel/sdist.
    """
    return Path(__file__).resolve().parent / "assets" / "interactive-terminal" / "wrapper.sh"


def _wrapper_skill_invocation(step: Step) -> str:
    """Return the wrapper's ``skill_invocation`` argument for ``step`` (Issue #408).

    Only Codex resolves a ``$<skill>`` mention in the user's input, so only codex
    gets a non-empty line. Other agents keep the legacy initial message.
    """
    return skill_invocation_line(step) if step.agent == "codex" else ""


def _build_wrapper_command(
    wrapper: Path,
    *,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
    execution_policy: str = "auto",
    env: Mapping[str, str] | None = None,
    skill_invocation: str = "",
) -> str:
    """Build the single shell command that starts the wrapper in the new pane.

    The pane launcher runs one command line, so the wrapper argv is
    shell-quoted with ``shlex.join``. The 10 wrapper arguments follow the
    Wrapper 契約 order exactly: ``agent prompt_path verdict_path workdir
    resume_session_id launch_session_id model effort execution_policy
    skill_invocation``. ``skill_invocation`` (Issue #408) is the codex ``$<skill>``
    line prepended to the agent's initial message; it is empty for other agents.

    A terminal pane inherits the multiplexer's environment, not kaji's, so ``env``
    (Issue #407) is delivered by prefixing ``env K=V ...`` to the command. The
    ``env(1)`` form (rather than shell ``K=V cmd``) keeps values containing
    spaces intact after quoting. The wrapper's own argv is unchanged.
    """
    env_prefix = ["env", *(f"{key}={value}" for key, value in env.items())] if env else []
    return shlex.join(
        [
            *env_prefix,
            str(wrapper),
            agent,
            str(prompt_path),
            str(verdict_path),
            str(workdir),
            resume_session_id,
            launch_session_id,
            model,
            effort,
            execution_policy,
            skill_invocation,
        ]
    )


def _resolve_abnormal_exit_session(
    agent: str,
    *,
    prompt_path: Path,
    verdict_path: Path,
    resume_session_id: str | None,
    launch_session_id: str,
    pane_alive: bool,
) -> SessionResolution:
    """verdict を得ずに終わった attempt の session を解決する（Issue #403）。

    戻り値は常に確定値であり、``SessionResolution(None)`` は「当該 attempt に一意対応
    する session は無い」という結論を表す。呼び出し側（runner）が resume 入力などで
    埋め直してはならない。verdict 検出経路の newest-match-wins 規則はここでは使わない
    （異常終了経路だけが一意性を要求する。詳細は設計書 § 方針 1）。

    Args:
        agent: step の agent 名（``claude`` / ``codex`` / ``antigravity``）。
        prompt_path: 当該 attempt の ``prompt.txt``。marker かつ attempt 開始時刻の代理。
        verdict_path: 当該 attempt の ``verdict.yaml``。marker の一方。
        resume_session_id: ``resume:`` step で wrapper へ渡した親 session ID。
        launch_session_id: claude fresh 起動時に runner が採番した UUID（他は空文字）。
        pane_alive: pane が生存したまま終わったか（timeout=True / pane-dead=False）。

    Returns:
        当該 attempt の session として検証できた ID を持つ ``SessionResolution``。
        検証できない場合は ``SessionResolution(None)``。
    """
    if agent == "claude":
        # `claude --resume <id>` は同一 session を継続するため、resume 入力そのものが
        # 当該 attempt の session ID（先行 attempt が作成済みで実在が保証される）。
        if resume_session_id:
            return SessionResolution(resume_session_id)
        # fresh は `claude --session-id <uuid>` で session を新規作成する。pane が全区間
        # 生存した = wrapper の起動が受理された証拠。pane-dead は起動失敗を含むため、
        # 採番 UUID に対応する session が実在しない可能性があり採用しない。
        if pane_alive:
            return SessionResolution(launch_session_id or None)
        return SessionResolution(None)
    if agent != "codex":
        # antigravity は公開 session ID を持たない。
        return SessionResolution(None)
    # codex は resume でも履歴を引き継ぐ *新規* rollout を作るため、resume 入力は当該
    # attempt の session ではない。store 照合で一意特定できなければ None にする
    # （親 ID への fallback は「推測」にあたる）。
    return SessionResolution(
        _unique_codex_session_id_from_store(prompt_path=prompt_path, verdict_path=verdict_path)
    )


def _unique_codex_session_id_from_store(*, prompt_path: Path, verdict_path: Path) -> str | None:
    """当該 attempt に **一意** 対応する Codex rollout の UUID を返す（Issue #403）。

    ``CODEX_HOME/sessions/**/*.jsonl`` のうち、``prompt.txt`` の mtime 以降に更新された
    rollout だけを走査し、本文が当該 attempt の marker（prompt / verdict の絶対 path）を
    含むものを数える。mtime 下限は (a) ``resume:`` step で正当に複数一致する親 rollout を
    除外し、(b) 走査コストを attempt 開始後のファイルへ限定するために置く。

    異常終了経路は ``terminal.log`` を読まない: timeout 時は pane が生存しており Codex の
    resume 行がまだ出力されていないうえ、実障害の transcript は 50MB 規模で読込コストが
    大きい。

    Returns:
        一致がちょうど 1 件のときその UUID。0 件 / 2 件以上 / 読取失敗 / ``prompt.txt`` の
        stat 失敗はすべて None（fail-safe。推測で埋めない）。
    """
    markers = [str(prompt_path), str(verdict_path)]
    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return None
    try:
        prompt_mtime = prompt_path.stat().st_mtime
        candidates = [
            (path.stat().st_mtime, path)
            for path in sessions_dir.rglob("*.jsonl")
            if _CODEX_SESSION_FILE_RE.match(path.name)
        ]
    except OSError:
        return None

    fresh = sorted(
        (entry for entry in candidates if entry[0] >= prompt_mtime),
        key=lambda entry: entry[0],
        reverse=True,
    )[:_CODEX_SESSION_SCAN_LIMIT]

    matched: list[str] = []
    for _, path in fresh:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # 読めない候補があると「ちょうど 1 件」を保証できない。
            return None
        if not any(marker in text for marker in markers):
            continue
        match = _CODEX_SESSION_FILE_RE.match(path.name)
        if match is None:  # pragma: no cover - fresh は match 済みのみを含む
            continue
        matched.append(match.group(1))
        if len(matched) > 1:
            return None
    return matched[0] if len(matched) == 1 else None


def _extract_codex_session_id(
    terminal_log: Path, *, prompt_path: Path | None = None, verdict_path: Path | None = None
) -> str | None:
    """Extract Codex's session UUID from ``terminal.log`` or the session store."""
    if not terminal_log.is_file():
        return _extract_codex_session_id_from_store(
            prompt_path=prompt_path, verdict_path=verdict_path
        )
    text = terminal_log.read_text(encoding="utf-8", errors="replace")
    matches = _CODEX_RESUME_RE.findall(text)
    if matches:
        return str(matches[-1])
    return _extract_codex_session_id_from_store(prompt_path=prompt_path, verdict_path=verdict_path)


def _extract_codex_session_id_from_store(
    *, prompt_path: Path | None, verdict_path: Path | None
) -> str | None:
    """Find Codex's rollout session file when the final resume line was not printed.

    Scans ``CODEX_HOME/sessions/**/*.jsonl`` (then ``~/.codex/sessions``) by
    descending mtime and adopts the UUID of the first rollout file whose body
    references this attempt's ``prompt_path`` / ``verdict_path`` marker.
    """
    markers = [str(path) for path in (prompt_path, verdict_path) if path is not None]
    if not markers:
        return None

    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return None

    candidates = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:_CODEX_SESSION_SCAN_LIMIT]
    for candidate in candidates:
        match = _CODEX_SESSION_FILE_RE.match(candidate.name)
        if match is None:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if any(marker in text for marker in markers):
            return match.group(1)
    return None


def _codex_home() -> Path:
    if value := os.environ.get("CODEX_HOME"):
        return Path(value)
    return Path.home() / ".codex"
