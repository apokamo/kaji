"""Verdict parser for kaji_harness.

3-stage fallback strategy (restored from V5/V6):
- Step 1: Strict Parse — exact delimiter + YAML
- Step 2a: Relaxed Parse — flexible delimiters + YAML
- Step 2b: Relaxed Parse — key-value pattern extraction
- Step 3: AI Formatter Retry — LLM-based output reformatting
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any, Protocol

import yaml

from .errors import InvalidVerdictValue, VerdictNotFound, VerdictParseError
from .models import Verdict

# Step 1: Strict delimiter pattern (original V7)
STRICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)

# Step 2a: Relaxed delimiter pattern (V5/V6 restoration)
RELAXED_PATTERN = re.compile(
    r"---\s*VERDICT\s*---\s*\n(.*?)\n\s*---\s*END[\s_]VERDICT\s*---",
    re.DOTALL | re.IGNORECASE,
)

# Step 3: AI Formatter constants
AI_FORMATTER_MAX_INPUT_CHARS: int = 8000

NO_VERDICT_SENTINEL = "---NO_VERDICT_FOUND---"

FORMATTER_PROMPT = Template(
    "以下の出力から VERDICT を抽出し、正確な YAML フォーマットで出力してください。\n"
    "\n"
    "## 入力\n"
    "$raw_output\n"
    "\n"
    "## 出力フォーマット（厳密に従ってください）\n"
    "---VERDICT---\n"
    "status: <$valid_statuses_str のいずれか1つ>\n"
    'reason: "判定理由"\n'
    'evidence: "判定根拠"\n'
    'suggestion: "次のアクション提案"\n'
    "---END_VERDICT---\n"
    "\n"
    "重要: status 行は必ず $valid_statuses_str のいずれかを出力してください。それ以外の値は使用禁止です。\n"
    "\n"
    "## 例外: 入力の verdict ブロックが空 / 内容不足 / 非 verdict 内容で埋まっている場合\n"
    "前段の harness gate を通過した入力には `---VERDICT---` delimiter が必ず含まれていますが、\n"
    "その delimiter 内が以下のいずれかに該当する場合は **verdict を捏造せず**、下記 sentinel を\n"
    "**単独で** 出力してください。sentinel 以外の本文（推測 status、補足説明、コードブロック等）は\n"
    "一切付けないでください。\n"
    "\n"
    "  (a) delimiter 内が空、または空白 / 改行のみ\n"
    "  (b) delimiter 内に status / reason / evidence のいずれも明示されていない\n"
    "  (c) delimiter 内が agent の中間進捗報告（pytest 待ち / 作業継続中 等）であり、\n"
    "      step 完了の意思表示として読み取れない\n"
    "\n"
    "---NO_VERDICT_FOUND---\n"
    "\n"
    "中間進捗報告を PASS / ABORT 等の verdict と解釈してはいけません。delimiter が形式的に\n"
    "存在しても、内容が verdict として成立していないことそのものが harness への正規の応答です。\n"
)

logger = logging.getLogger(__name__)

# Step 2b: Relaxed field extraction patterns
_RELAXED_REASON_PATTERNS = [
    re.compile(r"Reason:\s*(.+)", re.IGNORECASE),
    re.compile(r"-\s*Reason:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Reason\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"理由:\s*(.+)"),
]

_RELAXED_EVIDENCE_PATTERNS = [
    re.compile(r"Evidence:\s*(.+)", re.IGNORECASE),
    re.compile(r"-\s*Evidence:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Evidence\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"根拠:\s*(.+)"),
]

_RELAXED_SUGGESTION_PATTERNS = [
    re.compile(r"Suggestion:\s*(.+)", re.IGNORECASE),
    re.compile(r"-\s*Suggestion:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Suggestion\*\*:\s*(.+)", re.IGNORECASE),
]


def _extract_block_strict(output: str) -> str | None:
    """Step 1: Extract verdict block body using strict delimiters."""
    match = STRICT_PATTERN.search(output)
    return match.group(1) if match else None


def _extract_block_relaxed(output: str) -> str | None:
    """Step 2a: Extract verdict block body using relaxed delimiters."""
    match = RELAXED_PATTERN.search(output)
    return match.group(1) if match else None


def _parse_yaml_fields(block: str) -> Verdict:
    """Parse a verdict block body as YAML and extract 4 fields.

    Raises:
        VerdictParseError: YAML parse failure or missing required fields.
    """
    try:
        fields: Any = yaml.safe_load(block)
    except yaml.YAMLError as e:
        raise VerdictParseError(f"YAML parse error in verdict block: {e}") from e

    if not isinstance(fields, dict):
        raise VerdictParseError(f"Verdict block is not a YAML mapping: {type(fields)}")

    if "status" not in fields:
        raise VerdictParseError("Missing required field: status")
    if "reason" not in fields or not fields["reason"]:
        raise VerdictParseError("Missing required field: reason")
    if "evidence" not in fields or not fields["evidence"]:
        raise VerdictParseError("Missing required field: evidence")

    return Verdict(
        status=str(fields["status"]).strip(),
        reason=str(fields["reason"]).strip(),
        evidence=str(fields["evidence"]).strip(),
        suggestion=str(fields.get("suggestion", "")).strip(),
    )


def _build_relaxed_status_patterns(valid_statuses: set[str]) -> list[re.Pattern[str]]:
    """Build Step 2b status patterns restricted to valid_statuses.

    Patterns are dynamically generated from valid_statuses to prevent
    false positives like 'Status: 200' or 'Result = success'.
    """
    alt = "|".join(re.escape(s) for s in sorted(valid_statuses))
    templates = [
        rf"status:\s*({alt})",
        rf"Status:\s*({alt})",
        rf"Result:\s*({alt})",
        rf"-\s*Result:\s*({alt})",
        rf"-\s*Status:\s*({alt})",
        rf"\*\*Status\*\*:\s*({alt})",
        rf"ステータス:\s*({alt})",
        rf"Status\s*=\s*({alt})",
        rf"Result\s*=\s*({alt})",
    ]
    return [re.compile(t, re.IGNORECASE) for t in templates]


def _extract_field_relaxed(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    """Extract a field value using multiple regex patterns."""
    for p in patterns:
        match = p.search(text)
        if match:
            return match.group(1).strip()
    return None


def _parse_relaxed_fields(text: str, valid_statuses: set[str]) -> Verdict:
    """Step 2b: Extract verdict from key-value patterns.

    Raises:
        VerdictParseError: No status match, or reason/evidence missing.
    """
    status_patterns = _build_relaxed_status_patterns(valid_statuses)

    status: str | None = None
    for p in status_patterns:
        match = p.search(text)
        if match:
            status = match.group(1).upper()
            break

    if status is None:
        raise VerdictParseError("No valid status found in relaxed patterns")

    reason = _extract_field_relaxed(text, _RELAXED_REASON_PATTERNS)
    evidence = _extract_field_relaxed(text, _RELAXED_EVIDENCE_PATTERNS)

    if not reason or not evidence:
        raise VerdictParseError(
            f"Status '{status}' found but reason/evidence missing. Falling through to AI formatter."
        )

    suggestion = _extract_field_relaxed(text, _RELAXED_SUGGESTION_PATTERNS) or ""

    return Verdict(
        status=status,
        reason=reason,
        evidence=evidence,
        suggestion=suggestion,
    )


def _validate(verdict: Verdict, valid_statuses: set[str]) -> None:
    """Validate verdict status against valid_statuses."""
    if verdict.status not in valid_statuses:
        raise InvalidVerdictValue(
            f"'{verdict.status}' not in {valid_statuses}. "
            "This indicates a prompt violation — do not retry."
        )
    if (verdict.status == "ABORT" or verdict.status.startswith("BACK")) and not verdict.suggestion:
        raise VerdictParseError(f"{verdict.status} verdict requires non-empty suggestion")


def parse_verdict(
    output: str,
    valid_statuses: set[str],
    *,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> Verdict:
    """Extract and validate a verdict from CLI output.

    3-stage fallback strategy:
    - Step 1: Strict delimiter + YAML parse
    - Step 2a: Relaxed delimiter + YAML parse
    - Step 2b: Key-value pattern extraction
    - Step 3: AI formatter retry (if provided)

    Args:
        output: CLI process full output text.
        valid_statuses: Set of valid verdict status values.
        ai_formatter: Optional AI formatting function for Step 3.
        max_retries: Max retry count for Step 3 (must be >= 1).

    Returns:
        Verdict with validated status, reason, evidence, suggestion.

    Raises:
        VerdictNotFound: No verdict found in any step.
        VerdictParseError: Parse/field errors after all steps exhausted.
        InvalidVerdictValue: Invalid status value (immediate, no fallback).
        ValueError: max_retries < 1.
    """
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")

    # Step 1: Strict Parse
    block = _extract_block_strict(output)
    if block is not None:
        try:
            verdict = _parse_yaml_fields(block)
            _validate(verdict, valid_statuses)
            logger.debug("Step 1 (strict) succeeded")
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound) as e:
            logger.debug("Step 1 (strict) failed: %s", e)

    # Step 2a: Relaxed delimiter + YAML
    relaxed_block = _extract_block_relaxed(output)
    if relaxed_block is not None:
        try:
            verdict = _parse_yaml_fields(relaxed_block)
            _validate(verdict, valid_statuses)
            logger.info("Step 2a (relaxed delimiter) succeeded — strict parse was insufficient")
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound) as e:
            logger.debug("Step 2a (relaxed delimiter) failed: %s", e)

    # Step 2b: Key-value pattern extraction
    try:
        verdict = _parse_relaxed_fields(output, valid_statuses)
        _validate(verdict, valid_statuses)
        logger.info(
            "Step 2b (key-value pattern) succeeded — delimiter-based parse was insufficient"
        )
        return verdict
    except InvalidVerdictValue:
        raise
    except VerdictParseError as e:
        logger.debug("Step 2b (key-value pattern) failed: %s", e)

    # Step 3 gate: delimiter-presence-only. Step 3 (AI formatter) is invoked
    # only when a verdict delimiter (strict or relaxed) was extracted. If no
    # delimiter was found, fail loudly with VerdictNotFound regardless of
    # whether ai_formatter is provided — the formatter has a fabrication
    # pathway when given marker-less natural-language input (Issue #193).
    if block is None and relaxed_block is None:
        raise VerdictNotFound(
            "No verdict delimiter found in output. Step 3 (AI formatter) skipped "
            f"to prevent fabrication. Last 500 chars: {output[-500:]}"
        )

    # Step 3: AI Formatter Retry
    if ai_formatter is None:
        raise VerdictParseError(
            "All parse attempts failed (Step 1-2). Provide ai_formatter for Step 3 retry."
        )

    logger.info("Step 3 (AI formatter) invoked — Steps 1-2 exhausted")
    truncated_text = _truncate_for_formatter(output)

    last_error: VerdictParseError | None = None
    for attempt in range(max_retries):
        try:
            formatted = ai_formatter(truncated_text)
            # Re-run Step 1 + 2 on formatted output
            verdict = _parse_formatted_output(formatted, valid_statuses)
            logger.info("Step 3 succeeded on attempt %d/%d", attempt + 1, max_retries)
            return verdict
        except (InvalidVerdictValue, VerdictNotFound):
            # VerdictNotFound here originates from NO_VERDICT_SENTINEL — the
            # formatter explicitly reported "no verdict exists". Retrying
            # cannot turn that into a verdict, so propagate immediately.
            raise
        except VerdictParseError as e:
            logger.debug("Step 3 attempt %d/%d failed: %s", attempt + 1, max_retries, e)
            last_error = e
            continue

    raise VerdictParseError(
        f"All {max_retries} AI formatter attempts failed. Last error: {last_error}"
    )


def _truncate_for_formatter(text: str) -> str:
    """Truncate text for AI formatter using head+tail strategy (tail-heavy).

    Verdict blocks typically appear near the end of output, so we allocate
    1/3 to head and 2/3 to tail to maximize the chance of preserving them.
    """
    if len(text) <= AI_FORMATTER_MAX_INPUT_CHARS:
        return text

    truncate_delimiter = "\n...[truncated]...\n"
    usable_chars = AI_FORMATTER_MAX_INPUT_CHARS - len(truncate_delimiter)
    head_limit = usable_chars // 3
    tail_limit = usable_chars - head_limit
    return text[:head_limit] + truncate_delimiter + text[-tail_limit:]


def _parse_formatted_output(formatted: str, valid_statuses: set[str]) -> Verdict:
    """Parse AI formatter output through Step 1 → 2a → 2b.

    Recognizes ``NO_VERDICT_SENTINEL`` as the formatter's explicit signal that
    the agent output contains no real verdict (e.g. delimiter present but body
    is empty / progress report only). Sentinel detection maps to
    ``VerdictNotFound`` so the harness fails loudly rather than retrying.

    The sentinel must be the entire formatter response (modulo surrounding
    whitespace). Substring matching would misclassify otherwise-valid verdicts
    whose ``reason`` / ``evidence`` happen to quote the literal sentinel string
    (e.g. docs or logs referencing it).
    """
    if formatted.strip() == NO_VERDICT_SENTINEL:
        raise VerdictNotFound("AI formatter reported no verdict block in agent output")

    # Try strict
    block = _extract_block_strict(formatted)
    if block is not None:
        try:
            verdict = _parse_yaml_fields(block)
            _validate(verdict, valid_statuses)
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound):
            pass

    # Try relaxed delimiter
    block = _extract_block_relaxed(formatted)
    if block is not None:
        try:
            verdict = _parse_yaml_fields(block)
            _validate(verdict, valid_statuses)
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound):
            pass

    # Try key-value patterns
    verdict = _parse_relaxed_fields(formatted, valid_statuses)
    _validate(verdict, valid_statuses)
    return verdict


def _build_formatter_cli_args(agent: str, model: str | None, prompt: str) -> list[str]:
    """Build CLI args for the formatter subprocess.

    Uses plain text output mode (no --json / no stream-json) since
    we only need the formatted text, not structured events.
    """
    match agent:
        case "claude":
            # Claude: -p is "print mode" (non-interactive), prompt is a positional arg.
            args = ["claude", "-p", "--output-format", "text"]
            if model:
                args += ["--model", model]
            args.append(prompt)
        case "codex":
            # No --json: plain text output for formatter (not JSONL).
            # With --json, Codex outputs JSONL events that would need
            # CodexAdapter decoding, which the formatter path doesn't have.
            args = ["codex", "exec"]
            if model:
                args += ["-m", model]
            args.append(prompt)
        case "gemini":
            # Gemini: -p takes an argument (prompt string), unlike Claude's -p flag.
            args = ["gemini", "-p", prompt]
            if model:
                args += ["-m", model]
        case _:
            raise ValueError(f"Unknown agent for formatter: {agent}")
    return args


def create_verdict_formatter(
    agent: str,
    valid_statuses: set[str],
    *,
    model: str | None = None,
    workdir: Path | None = None,
) -> Callable[[str], str]:
    """Create an AI verdict formatter function.

    Generates a callable that invokes a CLI agent to reformat
    raw output into a parseable verdict block.

    Args:
        agent: CLI agent name ("claude" | "codex" | "gemini").
        valid_statuses: Valid verdict status values for the prompt.
        model: Optional model override.
        workdir: Optional working directory for subprocess.

    Returns:
        Callable that takes raw output text and returns formatted text.
    """
    statuses_str = "|".join(sorted(valid_statuses))

    def formatter(raw_output: str) -> str:
        prompt = FORMATTER_PROMPT.safe_substitute(
            raw_output=raw_output,
            valid_statuses_str=statuses_str,
        )
        args = _build_formatter_cli_args(agent, model, prompt)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired as e:
            raise VerdictParseError(f"AI formatter timed out after 60s (agent={agent})") from e
        if result.returncode != 0:
            raise VerdictParseError(
                f"AI formatter CLI exited with code {result.returncode}: {result.stderr[:300]}"
            )
        if not result.stdout.strip():
            raise VerdictParseError("AI formatter returned empty output")
        return result.stdout

    return formatter


# ============================================================
# Issue #220: artifact verdict.yaml + comment fallback resolution
# ============================================================


class CommentLike(Protocol):
    """`resolve_verdict` の comment fallback が必要とする最小 interface。

    provider 層の ``Comment`` (``providers.models.Comment``) と構造的に互換。
    verdict.py を provider 実装へ依存させないため Protocol で受ける。
    属性は read-only property として宣言し、frozen dataclass である
    ``Comment`` と構造的に一致させる。
    """

    @property
    def body(self) -> str: ...

    @property
    def created_at(self) -> str: ...


def load_verdict_yaml(path: Path, valid_statuses: set[str]) -> Verdict:
    """artifact の ``verdict.yaml`` (delimiter 無しの pure YAML) を読み込む。

    既存 stdout verdict と同じ検証規則（``_parse_yaml_fields`` + ``_validate``）を
    通す。``verdict.yaml`` が「存在するが壊れている」場合は fail-loud（呼び出し側
    の ``resolve_verdict`` は comment / stdout へ fallthrough しない）。

    Args:
        path: ``verdict.yaml`` の絶対パス。存在前提（存在確認は呼び出し側）。
        valid_statuses: 当該 step の ``on:`` キー集合。

    Returns:
        検証済み ``Verdict``。

    Raises:
        VerdictParseError: YAML parse 失敗 / 必須欠落 / ABORT・BACK で suggestion 空。
        InvalidVerdictValue: status が ``valid_statuses`` 外。
    """
    text = path.read_text(encoding="utf-8")
    verdict = _parse_yaml_fields(text)
    _validate(verdict, valid_statuses)
    return verdict


def write_verdict_yaml(path: Path, verdict: Verdict) -> None:
    """``Verdict`` を ``status/reason/evidence/suggestion`` の pure YAML で書き出す。

    ``load_verdict_yaml`` と round-trip 可能。run_id / step_id / attempt_id は
    保存しない（attempt path 自体が現在の run / step / attempt を表す）。

    Args:
        path: 書き込み先（親ディレクトリは必要なら作成する）。
        verdict: 直列化する ``Verdict``。
    """
    data = {
        "status": verdict.status,
        "reason": verdict.reason,
        "evidence": verdict.evidence,
        "suggestion": verdict.suggestion,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def parse_verdict_block(text: str, valid_statuses: set[str]) -> Verdict | None:
    """comment 本文から末尾の ``---VERDICT---`` block を抽出して検証する。

    作業報告 comment の契約は「本文末尾に verdict block を追記」であるため、
    引用・過去ログ中の古い block を誤採用しないよう **本文中で成立する最後（末尾）
    の block** を採用する。STRICT delimiter を優先し、無ければ RELAXED を見る。

    Args:
        text: comment 本文。
        valid_statuses: 当該 step の ``on:`` キー集合。

    Returns:
        末尾 block の ``Verdict``。block が 1 つも無ければ ``None``。

    Raises:
        VerdictParseError: block は在るが YAML / 必須フィールドが不正。
        InvalidVerdictValue: status が ``valid_statuses`` 外（prompt 違反）。
    """
    matches = list(STRICT_PATTERN.finditer(text))
    if not matches:
        matches = list(RELAXED_PATTERN.finditer(text))
    if not matches:
        return None
    block = matches[-1].group(1)
    verdict = _parse_yaml_fields(block)
    _validate(verdict, valid_statuses)
    return verdict


def _parse_comment_timestamp(created_at: str) -> datetime | None:
    """comment の ISO8601 ``created_at`` を timezone-aware datetime に変換する。

    GitHub / local とも ``%Y-%m-%dT%H:%M:%SZ`` 形式。parse 不能なら ``None`` を
    返し、呼び出し側は fail-safe（現在 attempt 判定から除外）に倒す。
    """
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _is_current_comment(comment: CommentLike, attempt_started_at: datetime) -> bool:
    """comment が現在 attempt の dispatch 以降に投稿されたかを判定する。

    ``created_at >= attempt_started_at`` を満たす comment のみ現在 attempt 由来と
    みなす。parse 不能な ``created_at`` は fail-safe で除外する（古い comment を
    誤採用するより解決失敗を選ぶ）。

    比較の lower bound は **秒に切り捨てて** から用いる。``attempt_started_at`` は
    ``datetime.now(UTC)`` 由来でマイクロ秒精度を持つ一方、local comment / GitHub
    ``createdAt`` の timestamp は秒精度（``%Y-%m-%dT%H:%M:%SZ``）で保存される。
    dispatch と同一秒に投稿された fresh comment（例: dispatch ``12:00:00.5``、
    comment ``12:00:00Z``）が、マイクロ秒差だけで stale 扱いされ取りこぼされるのを
    防ぐ。
    """
    ts = _parse_comment_timestamp(comment.created_at)
    if ts is None:
        return False
    return ts >= attempt_started_at.replace(microsecond=0)


def resolve_verdict(
    *,
    attempt_dir: Path,
    full_output: str,
    valid_statuses: set[str],
    attempt_started_at: datetime,
    comment_loader: Callable[[], Sequence[CommentLike]] | None,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> tuple[Verdict, str]:
    """verdict を artifact → comment → stdout の順で解決する。

    1. ``attempt_dir/verdict.yaml`` が存在 → ``load_verdict_yaml``（壊れていれば
       fail-loud。comment / stdout へは落ちない）。``source="artifact"``。
    2. else ``comment_loader()`` を呼び、``created_at >= attempt_started_at`` の
       comment **のみ** を newest-first で走査し、最初に成立した末尾 block を採用。
       ``source="comment"``。provider 取得失敗時は WARN して stdout へ。
    3. else stdout の ``parse_verdict``（既存 3 段 fallback まるごと）。
       ``source="stdout"``。

    artifact が存在する間は comment / stdout を **見ない**ため、stale comment が
    fresh artifact を上書きすることはない。comment fallback は
    ``attempt_started_at`` を lower bound にすることで、retry / resume で当該
    attempt が verdict.yaml も stdout verdict も出さなかった場合に前 attempt の
    作業報告 comment を誤採用しない（Issue #220 完了条件）。

    Args:
        attempt_dir: 当該 attempt のディレクトリ（``verdict.yaml`` の親）。
        full_output: CLI / script の stdout 全文（stdout fallback 用）。
        valid_statuses: 当該 step の ``on:`` キー集合。
        attempt_started_at: dispatch 直前に記録した timezone-aware 時刻。
            comment fallback の lower bound。
        comment_loader: artifact 不在時のみ呼ぶ遅延 callable。comment 列を返す。
            ``None`` の場合 comment fallback を行わず stdout へ進む。
        ai_formatter: stdout 経路の Step 3（AI formatter）。exec_script では
            ``None``（formatter fallback を呼ばない）。
        max_retries: stdout 経路の AI formatter retry 回数。

    Returns:
        ``(Verdict, source)``。source は ``"artifact"`` / ``"comment"`` / ``"stdout"``。

    Raises:
        VerdictNotFound: 全 source で verdict が成立しない。
        VerdictParseError / InvalidVerdictValue: artifact が壊れている / status 不正。
    """
    artifact_path = attempt_dir / "verdict.yaml"
    if artifact_path.exists():
        return load_verdict_yaml(artifact_path, valid_statuses), "artifact"

    if comment_loader is not None:
        try:
            comments: Sequence[CommentLike] = comment_loader()
        except Exception as exc:  # noqa: BLE001 — provider 取得失敗は stdout へ fallthrough
            logger.warning("comment fallback loader failed: %s; falling through to stdout", exc)
            comments = []
        current = [c for c in comments if _is_current_comment(c, attempt_started_at)]
        for comment in reversed(current):  # newest-first
            verdict = parse_verdict_block(comment.body, valid_statuses)
            if verdict is not None:
                return verdict, "comment"

    return (
        parse_verdict(
            full_output, valid_statuses, ai_formatter=ai_formatter, max_retries=max_retries
        ),
        "stdout",
    )
