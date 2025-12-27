"""VERDICT protocol parser.

This module provides the VERDICT parsing logic used across all workflows.
The VERDICT format is the standardized output format for AI agents.

Format:
    ## VERDICT
    - Result: PASS | RETRY | BACK_DESIGN | ABORT
    - Reason: <judgment reason>
    - Evidence: <evidence/findings>
    - Suggestion: <next action suggestion>
"""

import re
from enum import Enum


class Verdict(Enum):
    """VERDICT status keywords."""

    PASS = "PASS"
    RETRY = "RETRY"
    BACK_DESIGN = "BACK_DESIGN"
    ABORT = "ABORT"


class VerdictParseError(Exception):
    """Raised when VERDICT cannot be parsed from output."""

    pass


class InvalidVerdictValueError(VerdictParseError):
    """Raised when VERDICT contains an invalid value."""

    pass


def parse_verdict(text: str) -> Verdict:
    """Parse VERDICT from AI output text.

    Uses a hybrid fallback approach:
    1. Strict parse: Look for "Result: <KEYWORD>"
    2. Relaxed parse: Try multiple patterns
    3. (Future) AI formatter retry

    Args:
        text: Raw AI output text

    Returns:
        Parsed Verdict enum

    Raises:
        InvalidVerdictValueError: If Result contains invalid value
        VerdictParseError: If no VERDICT Result found
    """
    # Step 1: Strict parse
    match = re.search(r"Result:\s*(\w+)", text, re.IGNORECASE)
    if match:
        result_str = match.group(1).upper()
        try:
            return Verdict(result_str)
        except ValueError:
            raise InvalidVerdictValueError(f"Invalid VERDICT value: {result_str}")

    # Step 2: Relaxed parse
    valid_values = r"(PASS|RETRY|BACK_DESIGN|ABORT)"
    patterns = [
        rf"-\s*Result:\s*{valid_values}",  # List format
        rf"\*\*Result\*\*:\s*{valid_values}",  # Bold format
        rf"Status:\s*{valid_values}",  # Alternative keyword
        rf"\*\*Status\*\*:\s*{valid_values}",  # Bold Status
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return Verdict(match.group(1).upper())

    raise VerdictParseError("No VERDICT Result found in output")


def extract_verdict_field(text: str, field: str) -> str | None:
    """Extract a field value from VERDICT section.

    Args:
        text: Raw AI output text
        field: Field name (e.g., "Reason", "Evidence", "Suggestion")

    Returns:
        Field value or None if not found
    """
    pattern = rf"-?\s*{field}:\s*(.+?)(?=\n-|\n##|\Z)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
