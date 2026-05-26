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
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import Any

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
