"""Tests for VERDICT parser."""

import pytest

from src.core.verdict import (
    InvalidVerdictValueError,
    Verdict,
    VerdictParseError,
    extract_verdict_field,
    parse_verdict,
)


class TestParseVerdict:
    """Tests for parse_verdict function."""

    def test_parse_pass(self) -> None:
        text = "## VERDICT\n- Result: PASS\n- Reason: All checks passed"
        assert parse_verdict(text) == Verdict.PASS

    def test_parse_retry(self) -> None:
        text = "## VERDICT\n- Result: RETRY\n- Reason: Minor issues found"
        assert parse_verdict(text) == Verdict.RETRY

    def test_parse_back_design(self) -> None:
        text = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Design issues"
        assert parse_verdict(text) == Verdict.BACK_DESIGN

    def test_parse_abort(self) -> None:
        text = "## VERDICT\n- Result: ABORT\n- Reason: Cannot continue"
        assert parse_verdict(text) == Verdict.ABORT

    def test_parse_case_insensitive(self) -> None:
        text = "Result: pass"
        assert parse_verdict(text) == Verdict.PASS

    def test_parse_with_extra_whitespace(self) -> None:
        text = "Result:   PASS  "
        assert parse_verdict(text) == Verdict.PASS

    def test_parse_invalid_value_raises_error(self) -> None:
        text = "Result: PENDING"
        with pytest.raises(InvalidVerdictValueError):
            parse_verdict(text)

    def test_parse_no_result_raises_error(self) -> None:
        text = "No verdict here"
        with pytest.raises(VerdictParseError):
            parse_verdict(text)

    def test_parse_relaxed_list_format(self) -> None:
        text = "- Result: PASS"
        assert parse_verdict(text) == Verdict.PASS

    def test_parse_relaxed_bold_format(self) -> None:
        text = "**Result**: PASS"
        assert parse_verdict(text) == Verdict.PASS

    def test_parse_status_keyword(self) -> None:
        text = "Status: RETRY"
        assert parse_verdict(text) == Verdict.RETRY


class TestExtractVerdictField:
    """Tests for extract_verdict_field function."""

    def test_extract_reason(self) -> None:
        text = "## VERDICT\n- Result: PASS\n- Reason: All tests passed"
        assert extract_verdict_field(text, "Reason") == "All tests passed"

    def test_extract_evidence(self) -> None:
        text = "- Evidence: Found 3 issues\n- Suggestion: Fix them"
        assert extract_verdict_field(text, "Evidence") == "Found 3 issues"

    def test_extract_missing_field(self) -> None:
        text = "- Result: PASS"
        assert extract_verdict_field(text, "Reason") is None

    def test_extract_multiline_value(self) -> None:
        text = "- Reason: Line 1\nLine 2\n- Evidence: test"
        result = extract_verdict_field(text, "Reason")
        assert result is not None
        assert "Line 1" in result
