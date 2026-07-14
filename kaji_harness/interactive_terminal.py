"""Interactive terminal runner.

This runner starts a real interactive agent CLI (``claude`` / ``codex``) inside
a ``tmux`` pane and waits for the agent-written ``verdict.yaml`` artifact. It
intentionally avoids parsing stdout: completion is decided by the
artifact-primary verdict resolution introduced in Issue #220.

The terminal backend is tmux only (ADR 007 v2, Issue #230). ``kaji run`` must
run inside a tmux session; the runner adds a pane to the current window,
records the transcript with ``tmux pipe-pane``, decides liveness via
``#{pane_dead}``, and cleans up with ``tmux kill-pane``. There is no ``/proc``
scan and no util-linux ``script(1)`` dependency, so Linux and macOS share one
implementation.

Pane placement (Issue #238): the first agent pane is created to the right of
the origin pane (``split-window -h``); subsequent agent panes split the right
column vertically (``split-window -v``) so the origin/agent widths stay stable
instead of shrinking each step. The right column keeps at most
``_MAX_VISIBLE_AGENT_PANES`` kaji-managed agent panes — when a new pane would
exceed that, the oldest (top-most) managed pane is killed first. kaji-created
panes are tagged with a ``@kaji_interactive_terminal`` pane option so manually
created panes are never pruned.

The single completion trigger is the appearance of ``verdict.yaml``; the agent
process is not waited on. The post-verdict ``kill-pane`` is best-effort cleanup
with no latency contract.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .cli import find_high_confidence_sensitive_pattern, find_transient_pattern
from .errors import (
    CLIExecutionError,
    CLINotFoundError,
    StepTimeoutError,
    TmuxSessionRequiredError,
)
from .models import CLIResult, Step

# Issue #235: 起動コンソール向け progress logger（kaji.* 名前空間）。
_console = logging.getLogger("kaji.interactive_terminal")

_CODEX_RESUME_RE = re.compile(
    r"\bcodex\s+resume\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_CODEX_SESSION_FILE_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)
_SESSION_ID_GRACE_SECONDS = 5.0
_CODEX_SESSION_SCAN_LIMIT = 100
_VERDICT_POLL_INTERVAL_SECONDS = 2
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
# Issue #238: pane scoped user options (`set-option -p`) are used as the kaji
# marker; they were added in tmux 3.1, so the minimum is raised from 3.0.
_MIN_TMUX_VERSION = (3, 1)
# Pane option name marking a pane as kaji-created (value: ``origin=<pane_id>``).
_KAJI_PANE_OPTION = "@kaji_interactive_terminal"
# Max kaji-managed agent panes kept visible in the right column at once.
_MAX_VISIBLE_AGENT_PANES = 2


@dataclass(frozen=True)
class KajiAgentPane:
    """A kaji-created agent pane discovered via ``tmux list-panes``.

    ``pane_top`` is the y-offset used to order panes: in the right-column layout
    a newly created pane is added below, so a smaller ``pane_top`` means an older
    pane. ``created_at`` wall-clock is intentionally *not* used (NTP corrections
    can run it backwards), so the geometry is the single ordering source.
    """

    pane_id: str
    pane_top: int
    pane_left: int
    pane_width: int


@dataclass(frozen=True)
class _PaneLaunch:
    """Placement outcome of a single agent-pane launch (diagnostic metadata)."""

    pane_id: str
    split_target_pane: str
    split_flag: str
    panes_before: list[str] = field(default_factory=list)
    panes_pruned: list[str] = field(default_factory=list)


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
    """

    kind: str
    matched_pattern: str | None
    clean_excerpt: str | None
    clean_tail: str
    sensitive_marker: str | None = None


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
            kind="no_pattern", matched_pattern=None, clean_excerpt=None, clean_tail=tail
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


def read_terminal_diagnostic(terminal_log: Path) -> TerminalDiagnostic:
    """``terminal_log`` を読み、診断抽出失敗（no_log/empty）も含めて判定する薄い I/O ラッパ。"""
    if not terminal_log.is_file():
        return TerminalDiagnostic(
            kind="no_log", matched_pattern=None, clean_excerpt=None, clean_tail=""
        )
    text = terminal_log.read_text(encoding="utf-8", errors="replace")
    return extract_terminal_diagnostic(text)


def _terminal_exit_detail(terminal_log: Path) -> str:
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
    prefix = "tmux pane exited before writing verdict.yaml"
    diagnostic = read_terminal_diagnostic(terminal_log)
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
        return f"{prefix}; no known provider error pattern in transcript; log tail:\n{diagnostic.clean_tail}"
    if diagnostic.kind == "no_log":
        return f"{prefix}; diagnostic unavailable: no {terminal_log.name}"
    return f"{prefix}; diagnostic unavailable: {terminal_log.name} empty"


def _wrapper_path() -> Path:
    """Resolve the packaged ``assets/interactive-terminal/wrapper.sh``.

    The wrapper ships as package data under ``kaji_harness/assets`` so it is
    available both from a source checkout and from an installed wheel/sdist.
    """
    return Path(__file__).resolve().parent / "assets" / "interactive-terminal" / "wrapper.sh"


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
) -> str:
    """Build the single shell command tmux runs in the new pane.

    ``split-window`` takes one command argument, so the wrapper argv is
    shell-quoted with ``shlex.join``. The 8 wrapper arguments follow the
    Wrapper 契約 order exactly: ``agent prompt_path verdict_path workdir
    resume_session_id launch_session_id model effort``.
    """
    return shlex.join(
        [
            str(wrapper),
            agent,
            str(prompt_path),
            str(verdict_path),
            str(workdir),
            resume_session_id,
            launch_session_id,
            model,
            effort,
        ]
    )


def _build_tmux_split_argv(
    tmux: str,
    wrapper: Path,
    *,
    split_target_pane: str,
    split_flag: str,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
) -> list[str]:
    """Assemble the ``tmux split-window`` argv.

    ``-d`` keeps focus on the current pane; ``-P -F '#{pane_id}'`` prints the
    created pane id, which becomes the lifecycle handle for polling, transcript,
    and cleanup. The split direction and target are chosen by the caller (Issue
    #238): the first agent pane splits the origin pane horizontally (``-h``,
    right column), and later panes split the right column's bottom pane
    vertically (``-v``) so widths stay stable.

    Args:
        split_target_pane: The ``-t`` target the new pane splits off from.
        split_flag: ``"-h"`` (right) or ``"-v"`` (below). Any other value is a
            programming error.

    Raises:
        ValueError: ``split_flag`` is neither ``"-h"`` nor ``"-v"``.
    """
    if split_flag not in {"-h", "-v"}:
        raise ValueError(f"split_flag must be '-h' or '-v', got {split_flag!r}")
    return [
        tmux,
        "split-window",
        "-d",
        split_flag,
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        split_target_pane,
        _build_wrapper_command(
            wrapper,
            agent=agent,
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            workdir=workdir,
            resume_session_id=resume_session_id,
            launch_session_id=launch_session_id,
            model=model,
            effort=effort,
        ),
    ]


def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    close_on_verdict: bool = True,
) -> CLIResult:
    """Start a real interactive CLI in a tmux pane and wait for ``verdict.yaml``.

    Args:
        step: The workflow step (``agent`` must be ``claude`` or ``codex``).
        prompt_path: Absolute path to the attempt's ``prompt.txt``.
        verdict_path: Absolute path the agent must write ``verdict.yaml`` to.
        workdir: Trusted project worktree used as cwd / ``--cd``. Resolved by
            ``runner.py`` to the same ``effective_workdir`` as the headless
            runner (backend-independent).
        timeout: Seconds to wait for ``verdict.yaml`` before failing.
        session_id: Previous session id to resume (``None`` → fresh run).
        close_on_verdict: ``kill-pane`` after the verdict artifact appears
            (best-effort cleanup). When ``False`` the pane is left with
            ``remain-on-exit on`` so it survives the agent's natural exit.

    Returns:
        ``CLIResult(full_output="", session_id=<resolved id or None>)``.

    Raises:
        CLINotFoundError: ``tmux`` is missing, ``$TMUX`` / ``$TMUX_PANE`` is
            unset, or ``tmux`` is older than 3.1.
        CLIExecutionError: ``split-window`` failed, ``list-panes`` failed, the
            kaji marker could not be set, or the pane died before writing
            ``verdict.yaml``.
        StepTimeoutError: ``verdict.yaml`` did not appear before the deadline.
        ValueError: ``step.agent`` is missing or unsupported.
        FileNotFoundError: ``prompt.txt`` or the wrapper script is missing.
    """
    if step.agent is None:
        raise ValueError(f"interactive terminal runner requires step.agent (step={step.id})")
    if step.agent not in {"claude", "codex"}:
        raise ValueError(f"interactive terminal runner does not support agent: {step.agent}")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt.txt not found: {prompt_path}")

    tmux = _resolve_tmux()
    target_pane = _resolve_target_pane()
    _validate_tmux_version(tmux)

    wrapper = _wrapper_path()
    if not wrapper.is_file():
        raise FileNotFoundError(f"interactive terminal wrapper not found: {wrapper}")

    terminal_log = prompt_path.parent / "terminal.log"
    metadata_path = prompt_path.parent / "pane-metadata.json"
    # Claude fresh runs need a runner-generated UUID so resume can reuse it.
    # Resume runs and Codex (which mints its own id) pass an empty marker.
    launch_session_id = str(uuid.uuid4()) if step.agent == "claude" and session_id is None else ""

    launch = _launch_pane(
        tmux,
        wrapper,
        target_pane=target_pane,
        agent=step.agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=session_id or "",
        launch_session_id=launch_session_id,
        model=step.model or "",
        effort=step.effort or "",
    )
    pane_id = launch.pane_id
    # Issue #235: pane 起動成功直後に起動コンソールへ progress を出す。
    # Issue #232: step / agent / timeout を加え、親コンソール 1 行で追跡可能にする。
    _console.info(
        "pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s",
        step.id,
        step.agent,
        pane_id,
        timeout,
        verdict_path,
    )
    if not _pipe_pane(tmux, pane_id, terminal_log):
        # tmux rejected the pipe — most likely the pane already closed because a
        # short-running agent wrote verdict.yaml and exited before we could
        # attach the logging pipe. A present verdict means the step succeeded, so
        # do not let a transcript-only setup failure mask it. Only a missing
        # verdict is a genuine launch failure.
        if verdict_path.is_file():
            return CLIResult(full_output="", session_id=session_id or launch_session_id or None)
        _kill_pane(tmux, pane_id)
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            "tmux pipe-pane failed before any verdict appeared: "
            + _terminal_exit_detail(terminal_log),
        )
    if not close_on_verdict:
        _set_remain_on_exit(tmux, pane_id)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if verdict_path.is_file():
            # Snapshot the pane state at verdict detection (diagnostic evidence).
            # Under the verdict-trigger contract the agent CLI is still alive
            # here, so #{pane_dead} is normally 0.
            _write_pane_metadata(
                tmux,
                pane_id,
                metadata_path,
                target_pane=target_pane,
                close_on_verdict=close_on_verdict,
                layout=launch,
            )
            result_session_id = session_id or launch_session_id or None
            if result_session_id is None and step.agent == "codex":
                _wait_for_pane_exit_or_session_id(
                    tmux,
                    pane_id,
                    terminal_log,
                    prompt_path=prompt_path,
                    verdict_path=verdict_path,
                    deadline=min(deadline, time.monotonic() + _SESSION_ID_GRACE_SECONDS),
                )
                result_session_id = _extract_codex_session_id(
                    terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                )
            if close_on_verdict:
                _kill_pane(tmux, pane_id)
            if result_session_id is None and step.agent == "codex":
                # Re-scan after cleanup: the rollout file may finalize on exit.
                result_session_id = _extract_codex_session_id(
                    terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                )
            return CLIResult(full_output="", session_id=result_session_id)

        if _pane_dead(tmux, pane_id):
            # The pane exited before any verdict appeared (e.g. the agent failed
            # at launch). Fail loud with the real error instead of polling until
            # the much longer step timeout.
            _write_pane_metadata(
                tmux,
                pane_id,
                metadata_path,
                target_pane=target_pane,
                close_on_verdict=close_on_verdict,
                layout=launch,
                terminal_log=terminal_log,
            )
            raise CLIExecutionError(step.id, 1, _terminal_exit_detail(terminal_log))
        time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)

    # Timeout: verdict never appeared. Best-effort cleanup, then fail-loud.
    _write_pane_metadata(
        tmux,
        pane_id,
        metadata_path,
        target_pane=target_pane,
        close_on_verdict=close_on_verdict,
        layout=launch,
    )
    _kill_pane(tmux, pane_id)
    raise StepTimeoutError(step.id, timeout)


def _resolve_tmux() -> str:
    tmux = shutil.which("tmux")
    if tmux is None:
        raise CLINotFoundError("CLI 'tmux' not found. Install tmux or use agent_runner='headless'.")
    return tmux


def _resolve_target_pane() -> str:
    """現在の tmux pane を解決する。

    Raises:
        TmuxSessionRequiredError: tmux セッション外から起動された（``$TMUX`` 未設定）。
            既知のユーザー前提エラーとして incident 記録の対象外になる（Issue #322）。
        CLINotFoundError: tmux セッション内だが ``$TMUX_PANE`` が無い（tmux 側の異常）。
    """
    if not os.environ.get("TMUX"):
        raise TmuxSessionRequiredError(
            "interactive terminal runner requires tmux. Run `kaji run` inside tmux "
            "or use agent_runner='headless'."
        )
    target_pane = os.environ.get("TMUX_PANE")
    if not target_pane:
        raise CLINotFoundError("TMUX_PANE is not set; cannot target the current tmux pane.")
    return target_pane


def _validate_tmux_version(tmux: str) -> None:
    proc = subprocess.run([tmux, "-V"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stderr)
    match = re.search(r"tmux\s+(\d+)\.(\d+)", proc.stdout)
    if match is None:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stdout or proc.stderr)
    version = (int(match.group(1)), int(match.group(2)))
    if version < _MIN_TMUX_VERSION:
        raise CLINotFoundError(
            f"interactive terminal runner requires tmux >= 3.1, got {proc.stdout.strip()}"
        )


def _launch_pane(
    tmux: str,
    wrapper: Path,
    *,
    target_pane: str,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
) -> _PaneLaunch:
    """Place and launch one agent pane, returning its id and placement metadata.

    Placement (Issue #238): existing kaji-managed agent panes are listed; if the
    right column already holds the maximum, the oldest (top-most) panes are
    pruned so the new pane keeps the column at ``_MAX_VISIBLE_AGENT_PANES``. With
    no managed pane left the origin pane is split horizontally (right column);
    otherwise the bottom managed pane is split vertically.
    """
    existing = _list_kaji_agent_panes(tmux, target_pane)
    # keep one slot free for the pane we are about to create, so launch ends at
    # _MAX_VISIBLE_AGENT_PANES.
    remaining = _prune_kaji_agent_panes(tmux, existing, keep=_MAX_VISIBLE_AGENT_PANES - 1)
    remaining_ids = {pane.pane_id for pane in remaining}
    pruned_ids = [pane.pane_id for pane in existing if pane.pane_id not in remaining_ids]

    if remaining:
        # remaining is sorted by pane_top ascending; the last is the bottom pane.
        split_target_pane = remaining[-1].pane_id
        split_flag = "-v"
    else:
        split_target_pane = target_pane
        split_flag = "-h"

    argv = _build_tmux_split_argv(
        tmux,
        wrapper,
        split_target_pane=split_target_pane,
        split_flag=split_flag,
        agent=agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=resume_session_id,
        launch_session_id=launch_session_id,
        model=model,
        effort=effort,
    )
    proc = subprocess.run(argv, text=True, capture_output=True, check=False, cwd=workdir)
    if proc.returncode != 0:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stderr)
    pane_id = proc.stdout.strip()
    if not pane_id.startswith("%"):
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            f"tmux did not return a pane id: {proc.stdout!r}",
        )
    try:
        _set_kaji_agent_pane_marker(tmux, pane_id, target_pane=target_pane)
    except CLIExecutionError:
        # Without the marker the pane cannot be safely identified for later
        # cleanup; best-effort kill it before failing loud.
        _kill_pane(tmux, pane_id)
        raise
    return _PaneLaunch(
        pane_id=pane_id,
        split_target_pane=split_target_pane,
        split_flag=split_flag,
        panes_before=[pane.pane_id for pane in existing],
        panes_pruned=pruned_ids,
    )


def _parse_kaji_pane_marker(value: str) -> dict[str, str]:
    """Parse a ``@kaji_interactive_terminal`` marker into a field dict.

    The marker is a whitespace-separated list of ``key=value`` tokens (currently
    just ``origin=<pane_id>``). Unknown fields, empty values, and malformed
    tokens are tolerated: malformed tokens are dropped rather than raising, so a
    user-set or corrupted option never crashes pane discovery.
    """
    fields: dict[str, str] = {}
    for token in value.split():
        key, sep, val = token.partition("=")
        if sep and key:
            fields[key] = val
    return fields


def _list_kaji_agent_panes(tmux: str, target_pane: str) -> list[KajiAgentPane]:
    """List kaji-created agent panes in ``target_pane``'s window, oldest first.

    Only panes whose ``@kaji_interactive_terminal`` marker resolves to
    ``origin=<target_pane>`` are returned; the origin pane itself, unmarked
    panes, and panes from a different origin are ignored so manually created
    panes are never touched. A failed ``list-panes`` fails loud rather than
    risk mis-cleanup on broken tmux state.
    """
    format_string = "\t".join(
        [
            "#{pane_id}",
            "#{pane_top}",
            "#{pane_left}",
            "#{pane_width}",
            f"#{{{_KAJI_PANE_OPTION}}}",
        ]
    )
    proc = subprocess.run(
        [tmux, "list-panes", "-F", format_string, "-t", target_pane],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            proc.stderr or "tmux list-panes failed",
        )
    panes: list[KajiAgentPane] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        pane_id, top, left, width, marker = parts[0], parts[1], parts[2], parts[3], parts[4]
        if pane_id == target_pane:
            continue
        if _parse_kaji_pane_marker(marker).get("origin") != target_pane:
            continue
        try:
            pane = KajiAgentPane(
                pane_id=pane_id,
                pane_top=int(top),
                pane_left=int(left),
                pane_width=int(width),
            )
        except ValueError:
            continue
        panes.append(pane)
    panes.sort(key=lambda pane: pane.pane_top)
    return panes


def _prune_kaji_agent_panes(
    tmux: str, panes: list[KajiAgentPane], *, keep: int
) -> list[KajiAgentPane]:
    """Kill the oldest panes so at most ``keep`` remain, returning the survivors.

    Oldest = smallest ``pane_top`` (top of the right column). The survivors are
    the newest ``keep`` panes, returned sorted by ``pane_top`` ascending.
    """
    ordered = sorted(panes, key=lambda pane: pane.pane_top)
    if len(ordered) <= keep:
        return ordered
    survivors = ordered[len(ordered) - keep :] if keep > 0 else []
    for pane in ordered[: len(ordered) - len(survivors)]:
        _kill_pane(tmux, pane.pane_id)
    return survivors


def _set_kaji_agent_pane_marker(tmux: str, pane_id: str, *, target_pane: str) -> None:
    """Tag a freshly created pane as kaji-managed via a pane user option.

    Raises:
        CLIExecutionError: ``set-option`` failed; the caller cleans up the
            orphaned pane and fails loud, since an unmarked pane cannot be safely
            identified for later pruning.
    """
    proc = subprocess.run(
        [tmux, "set-option", "-p", "-t", pane_id, _KAJI_PANE_OPTION, f"origin={target_pane}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            proc.stderr or "tmux set-option (kaji marker) failed",
        )


def _pipe_pane(tmux: str, pane_id: str, terminal_log: Path) -> bool:
    """Attach a logging pipe to the pane's output.

    Returns:
        ``True`` if tmux accepted the pipe; ``False`` if it was rejected (e.g.
        the pane already closed because a short-running agent exited
        immediately). The caller decides whether that is fatal based on whether
        ``verdict.yaml`` is already present.
    """
    terminal_log.parent.mkdir(parents=True, exist_ok=True)
    command = f"cat >> {shlex.quote(str(terminal_log))}"
    proc = subprocess.run(
        [tmux, "pipe-pane", "-o", "-t", pane_id, command],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _set_remain_on_exit(tmux: str, pane_id: str) -> None:
    """Keep the pane (as ``[dead]``) after the agent exits, for inspection."""
    subprocess.run(
        [tmux, "set-option", "-p", "-t", pane_id, "remain-on-exit", "on"],
        text=True,
        capture_output=True,
        check=False,
    )


def _pane_dead(tmux: str, pane_id: str) -> bool:
    """Return whether the pane has died; a failed pane lookup counts as dead."""
    proc = subprocess.run(
        [tmux, "display-message", "-p", "-t", pane_id, "#{pane_dead}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return True
    return proc.stdout.strip() == "1"


def _kill_pane(tmux: str, pane_id: str) -> None:
    """Best-effort ``kill-pane``; ignores a pane that is already gone."""
    subprocess.run([tmux, "kill-pane", "-t", pane_id], text=True, capture_output=True, check=False)


def _write_pane_metadata(
    tmux: str,
    pane_id: str,
    destination: Path,
    *,
    target_pane: str,
    close_on_verdict: bool,
    layout: _PaneLaunch | None = None,
    terminal_log: Path | None = None,
) -> None:
    """Snapshot the pane's ``#{pane_dead}`` (and related) fields for diagnostics.

    ``terminal_log`` is only passed at the pane-death call site (Issue #296): its
    structured ``TerminalDiagnostic`` is attached under ``terminal_diagnostic`` so
    the provider-error/no-pattern/extraction-failure distinction survives even
    though the classifier-facing ``CLIExecutionError`` message stays canonical-only.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "tmux_version": _tmux_version_text(tmux),
        "pane_id": pane_id,
        "target_pane": target_pane,
        "close_on_verdict": close_on_verdict,
    }
    if terminal_log is not None:
        diagnostic = read_terminal_diagnostic(terminal_log)
        metadata["terminal_diagnostic"] = {
            "kind": diagnostic.kind,
            "matched_pattern": diagnostic.matched_pattern,
            "clean_excerpt": diagnostic.clean_excerpt,
            "clean_tail": diagnostic.clean_tail,
        }
    if layout is not None:
        # Issue #238: pane placement diagnostics (right-column layout).
        metadata["layout_target_pane"] = target_pane
        metadata["split_target_pane"] = layout.split_target_pane
        metadata["split_direction"] = "horizontal" if layout.split_flag == "-h" else "vertical"
        metadata["kaji_agent_panes_before"] = layout.panes_before
        metadata["kaji_agent_panes_pruned"] = layout.panes_pruned
    format_string = "\t".join(
        [
            "pane_id=#{pane_id}",
            "pane_pid=#{pane_pid}",
            "pane_current_command=#{pane_current_command}",
            "pane_dead=#{pane_dead}",
            "pane_dead_status=#{pane_dead_status}",
            "pane_dead_signal=#{pane_dead_signal}",
        ]
    )
    proc = subprocess.run(
        [tmux, "display-message", "-p", "-t", pane_id, format_string],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        for part in proc.stdout.strip().split("\t"):
            key, _, value = part.partition("=")
            metadata[key] = value
    else:
        metadata["display_error"] = proc.stderr or proc.stdout
    destination.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tmux_version_text(tmux: str) -> str:
    proc = subprocess.run([tmux, "-V"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return proc.stderr.strip()
    return proc.stdout.strip()


def _wait_for_pane_exit_or_session_id(
    tmux: str,
    pane_id: str,
    terminal_log: Path,
    *,
    prompt_path: Path,
    verdict_path: Path,
    deadline: float,
) -> None:
    """Give Codex a brief chance to print its explicit resume command."""
    while time.monotonic() < deadline:
        if _pane_dead(tmux, pane_id) or _extract_codex_session_id(
            terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
        ):
            return
        time.sleep(0.2)


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
