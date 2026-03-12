"""Verdict parser for kaji_harness.

3-stage fallback strategy (restored from V5/V6):
- Step 1: Strict Parse — exact delimiter + YAML
- Step 2a: Relaxed Parse — flexible delimiters + YAML
- Step 2b: Relaxed Parse — key-value pattern extraction
- Step 3: AI Formatter Retry — LLM-based output reformatting
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
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

FORMATTER_PROMPT: str = """以下の出力から VERDICT を抽出し、正確な YAML フォーマットで出力してください。

## 入力
{raw_output}

## 出力フォーマット（厳密に従ってください）
---VERDICT---
status: <{valid_statuses_str} のいずれか1つ>
reason: "判定理由"
evidence: "判定根拠"
suggestion: "次のアクション提案"
---END_VERDICT---

重要: status 行は必ず {valid_statuses_str} のいずれかを出力してください。それ以外の値は使用禁止です。
"""

# Step 2b: Relaxed field extraction patterns
_RELAXED_REASON_PATTERNS = [
    re.compile(r"Reason:\s*(.+)", re.IGNORECASE),
    re.compile(r"-\s*Reason:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Reason\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"リーズン:\s*(.+)"),
]

_RELAXED_EVIDENCE_PATTERNS = [
    re.compile(r"Evidence:\s*(.+)", re.IGNORECASE),
    re.compile(r"-\s*Evidence:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Evidence\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"エビデンス:\s*(.+)"),
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
    if verdict.status in ("ABORT", "BACK") and not verdict.suggestion:
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
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound):
            pass  # Fall through to Step 2

    # Step 2a: Relaxed delimiter + YAML
    block = _extract_block_relaxed(output)
    if block is not None:
        try:
            verdict = _parse_yaml_fields(block)
            _validate(verdict, valid_statuses)
            return verdict
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound):
            pass  # Fall through to Step 2b

    # Step 2b: Key-value pattern extraction
    try:
        verdict = _parse_relaxed_fields(output, valid_statuses)
        _validate(verdict, valid_statuses)
        return verdict
    except InvalidVerdictValue:
        raise
    except VerdictParseError:
        pass  # Fall through to Step 3

    # Step 3: AI Formatter Retry
    if ai_formatter is None:
        # Determine the most appropriate error
        if block is None and _extract_block_relaxed(output) is None:
            raise VerdictNotFound(f"No verdict block found. Last 500 chars: {output[-500:]}")
        raise VerdictParseError(
            "All parse attempts failed (Step 1-2). Provide ai_formatter for Step 3 retry."
        )

    truncated_text = _truncate_for_formatter(output)

    last_error: VerdictParseError | VerdictNotFound | None = None
    for _attempt in range(max_retries):
        try:
            formatted = ai_formatter(truncated_text)
            # Re-run Step 1 + 2 on formatted output
            return _parse_formatted_output(formatted, valid_statuses)
        except InvalidVerdictValue:
            raise
        except (VerdictParseError, VerdictNotFound) as e:
            last_error = e
            continue

    raise VerdictParseError(
        f"All {max_retries} AI formatter attempts failed. Last error: {last_error}"
    )


def _truncate_for_formatter(text: str) -> str:
    """Truncate text for AI formatter using head+tail strategy."""
    if len(text) <= AI_FORMATTER_MAX_INPUT_CHARS:
        return text

    truncate_delimiter = "\n...[truncated]...\n"
    usable_chars = AI_FORMATTER_MAX_INPUT_CHARS - len(truncate_delimiter)
    half_limit = usable_chars // 2
    return text[:half_limit] + truncate_delimiter + text[-half_limit:]


def _parse_formatted_output(formatted: str, valid_statuses: set[str]) -> Verdict:
    """Parse AI formatter output through Step 1 → 2a → 2b."""
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
    """Build CLI args for the formatter subprocess."""
    match agent:
        case "claude":
            args = ["claude", "-p", "--output-format", "text"]
            if model:
                args += ["--model", model]
            args.append(prompt)
        case "codex":
            args = ["codex", "exec", "--json"]
            if model:
                args += ["-m", model]
            args.append(prompt)
        case "gemini":
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
        prompt = FORMATTER_PROMPT.format(
            raw_output=raw_output,
            valid_statuses_str=statuses_str,
        )
        args = _build_formatter_cli_args(agent, model, prompt)
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workdir,
        )
        return result.stdout

    return formatter
