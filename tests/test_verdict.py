"""Tests for VERDICT parser."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.core.verdict import (
    AgentAbortError,
    InvalidVerdictValueError,
    Verdict,
    VerdictParseError,
    create_ai_formatter,
    extract_verdict_field,
    handle_abort_verdict,
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


class TestParseVerdictWithAIFormatter:
    """Tests for parse_verdict with AI Formatter (Step 3)."""

    def test_step3_success_after_step1_step2_fail(self) -> None:
        """AI Formatter should succeed when Step 1 and 2 fail."""
        # Input that fails Step 1 and 2
        malformed_text = "The verdict is that everything passed successfully"

        # Mock AI formatter that returns properly formatted text
        def mock_formatter(text: str) -> str:
            return "## VERDICT\n- Result: PASS\n- Reason: Formatted by AI"

        result = parse_verdict(malformed_text, ai_formatter=mock_formatter, max_retries=2)
        assert result == Verdict.PASS

    def test_step3_skipped_when_ai_formatter_none(self) -> None:
        """Without ai_formatter, should raise VerdictParseError after Step 2."""
        malformed_text = "The verdict is that everything passed"

        with pytest.raises(VerdictParseError):
            parse_verdict(malformed_text, ai_formatter=None)

    def test_step3_retry_multiple_times(self) -> None:
        """AI Formatter should retry up to max_retries times."""
        malformed_text = "verdict unknown"
        call_count = 0

        def failing_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            # Return malformed text that still fails parsing
            return "still malformed"

        with pytest.raises(VerdictParseError):
            parse_verdict(malformed_text, ai_formatter=failing_formatter, max_retries=3)

        assert call_count == 3

    def test_step3_success_on_second_retry(self) -> None:
        """AI Formatter should succeed on second retry."""
        malformed_text = "verdict unknown"
        call_count = 0

        def eventually_succeeds(text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return "still bad"
            return "Result: RETRY"

        result = parse_verdict(malformed_text, ai_formatter=eventually_succeeds, max_retries=3)
        assert result == Verdict.RETRY
        assert call_count == 2

    def test_max_retries_validation(self) -> None:
        """max_retries < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            parse_verdict("text", ai_formatter=lambda x: x, max_retries=0)

    def test_invalid_verdict_value_not_retried(self) -> None:
        """InvalidVerdictValueError should be raised immediately, not retried."""

        # AI Formatter returns invalid value - should not retry
        def formatter_returns_invalid(text: str) -> str:
            return "Result: PENDING"

        with pytest.raises(InvalidVerdictValueError):
            parse_verdict("malformed", ai_formatter=formatter_returns_invalid, max_retries=3)

    def test_text_truncation_at_boundary(self) -> None:
        """Text should be truncated at 8000 char boundary."""
        # Create text > 8000 chars
        long_text = "x" * 9000
        formatter_received: list[str] = []

        def capture_formatter(text: str) -> str:
            formatter_received.append(text)
            return "Result: PASS"

        parse_verdict(long_text, ai_formatter=capture_formatter, max_retries=1)

        # Formatter should receive truncated text
        assert len(formatter_received) == 1
        # 4000 head + delimiter + 4000 tail
        assert len(formatter_received[0]) <= 8000 + len("\n...[truncated]...\n")

    def test_text_not_truncated_when_under_limit(self) -> None:
        """Text under 8000 chars should not be truncated."""
        short_text = "x" * 7000
        formatter_received: list[str] = []

        def capture_formatter(text: str) -> str:
            formatter_received.append(text)
            return "Result: PASS"

        parse_verdict(short_text, ai_formatter=capture_formatter, max_retries=1)

        assert len(formatter_received) == 1
        assert formatter_received[0] == short_text

    def test_backward_compatibility_no_ai_formatter(self) -> None:
        """parse_verdict should work without ai_formatter (backward compatible)."""
        text = "Result: PASS"
        result = parse_verdict(text)
        assert result == Verdict.PASS


class TestHandleAbortVerdict:
    """Tests for handle_abort_verdict function."""

    def test_non_abort_returns_verdict(self) -> None:
        """Non-ABORT verdicts should be returned as-is."""
        assert handle_abort_verdict(Verdict.PASS, "any text") == Verdict.PASS
        assert handle_abort_verdict(Verdict.RETRY, "any text") == Verdict.RETRY
        assert handle_abort_verdict(Verdict.BACK_DESIGN, "any text") == Verdict.BACK_DESIGN

    def test_abort_raises_exception(self) -> None:
        """ABORT verdict should raise AgentAbortError."""
        raw_output = "- Reason: System failure\n- Suggestion: Check logs"

        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(Verdict.ABORT, raw_output)

        assert exc_info.value.reason == "System failure"
        assert exc_info.value.suggestion == "Check logs"

    def test_abort_with_missing_reason(self) -> None:
        """ABORT without Reason should use default."""
        raw_output = "- Suggestion: Try again"

        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(Verdict.ABORT, raw_output)

        assert exc_info.value.reason == "No reason provided"
        assert exc_info.value.suggestion == "Try again"

    def test_abort_with_missing_suggestion(self) -> None:
        """ABORT without Suggestion should use empty string."""
        raw_output = "- Reason: Something went wrong"

        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(Verdict.ABORT, raw_output)

        assert exc_info.value.reason == "Something went wrong"
        assert exc_info.value.suggestion == ""

    def test_abort_with_no_fields(self) -> None:
        """ABORT without any fields should use defaults."""
        raw_output = "No fields here"

        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(Verdict.ABORT, raw_output)

        assert exc_info.value.reason == "No reason provided"
        assert exc_info.value.suggestion == ""


class TestCreateAIFormatter:
    """Tests for create_ai_formatter factory function."""

    def test_creates_callable(self) -> None:
        """create_ai_formatter should return a callable."""
        mock_tool = Mock()
        mock_tool.run.return_value = ("formatted text", None)

        formatter = create_ai_formatter(mock_tool)
        assert callable(formatter)

    def test_calls_tool_with_correct_arguments(self) -> None:
        """Formatter should call tool.run with correct arguments."""
        mock_tool = Mock()
        mock_tool.run.return_value = ("Result: PASS", None)

        formatter = create_ai_formatter(mock_tool, context="extra context")
        formatter("input text")

        mock_tool.run.assert_called_once()
        call_kwargs = mock_tool.run.call_args.kwargs
        assert "prompt" in call_kwargs
        assert "input text" in call_kwargs["prompt"]
        assert call_kwargs["context"] == "extra context"

    def test_returns_formatted_text(self) -> None:
        """Formatter should return the first element of tool response."""
        mock_tool = Mock()
        mock_tool.run.return_value = ("AI formatted output", "session-123")

        formatter = create_ai_formatter(mock_tool)
        result = formatter("raw input")

        assert result == "AI formatted output"

    def test_passes_log_dir_to_tool(self) -> None:
        """Formatter should pass log_dir to tool.run."""
        mock_tool = Mock()
        mock_tool.run.return_value = ("output", None)
        log_path = Path("/tmp/logs")

        formatter = create_ai_formatter(mock_tool, log_dir=log_path)
        formatter("input")

        call_kwargs = mock_tool.run.call_args.kwargs
        assert call_kwargs["log_dir"] == log_path

    def test_prompt_contains_security_warning(self) -> None:
        """Formatter prompt should contain injection prevention warning."""
        mock_tool = Mock()
        mock_tool.run.return_value = ("output", None)

        formatter = create_ai_formatter(mock_tool)
        formatter("malicious input")

        call_kwargs = mock_tool.run.call_args.kwargs
        prompt = call_kwargs["prompt"]
        # Should have code block protection
        assert "```" in prompt


class TestAgentAbortError:
    """Tests for AgentAbortError exception."""

    def test_error_message(self) -> None:
        """AgentAbortError should have proper message."""
        error = AgentAbortError("test reason", "test suggestion")
        assert str(error) == "Agent aborted: test reason"
        assert error.reason == "test reason"
        assert error.suggestion == "test suggestion"

    def test_default_suggestion(self) -> None:
        """AgentAbortError should have empty suggestion by default."""
        error = AgentAbortError("reason only")
        assert error.suggestion == ""


class TestRelaxedPatterns:
    """Tests for extended relaxed patterns."""

    def test_japanese_status_pattern(self) -> None:
        """Japanese 'ステータス:' should be recognized."""
        text = "ステータス: PASS"
        assert parse_verdict(text) == Verdict.PASS

    def test_equals_sign_pattern(self) -> None:
        """'Status = PASS' format should be recognized."""
        text = "Status = RETRY"
        assert parse_verdict(text) == Verdict.RETRY

    def test_result_equals_pattern(self) -> None:
        """'Result = PASS' format should be recognized."""
        text = "Result = BACK_DESIGN"
        assert parse_verdict(text) == Verdict.BACK_DESIGN
