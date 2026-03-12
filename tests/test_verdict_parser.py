"""Small tests for verdict parser.

Tests the parse_verdict function which extracts structured Verdict
data from CLI output containing ---VERDICT--- / ---END_VERDICT--- blocks.

Covers the 3-stage fallback strategy:
- Step 1: Strict Parse (existing V7)
- Step 2a: Delimiter relaxed (V5/V6)
- Step 2b: Key-Value pattern extraction (V5/V6)
- Step 3: AI Formatter Retry (V5/V6)
- Output collection layer (cli.py / adapters.py)
- Cross-cutting concerns
"""

from __future__ import annotations

import pytest

from kaji_harness.adapters import CodexAdapter
from kaji_harness.errors import InvalidVerdictValue, VerdictNotFound, VerdictParseError
from kaji_harness.models import Verdict
from kaji_harness.verdict import (
    AI_FORMATTER_MAX_INPUT_CHARS,
    _build_relaxed_status_patterns,
    _extract_block_relaxed,
    _extract_block_strict,
    _parse_relaxed_fields,
    _parse_yaml_fields,
    parse_verdict,
)

VALID_STATUSES = {"PASS", "RETRY", "BACK", "ABORT"}


def _wrap_verdict(body: str) -> str:
    """Wrap a YAML body in verdict delimiters."""
    return f"---VERDICT---\n{body}\n---END_VERDICT---"


def _make_verdict_block(
    status: str = "PASS",
    reason: str = "テスト成功",
    evidence: str = "全テスト通過",
    suggestion: str = "",
) -> str:
    """Build a valid verdict block string."""
    lines = [
        f"status: {status}",
        f'reason: "{reason}"',
        f'evidence: "{evidence}"',
    ]
    if suggestion:
        lines.append(f'suggestion: "{suggestion}"')
    return _wrap_verdict("\n".join(lines))


# ============================================================
# 1. Normal extraction (Step 1: Strict)
# ============================================================


@pytest.mark.small
class TestNormalExtraction:
    """Valid verdict blocks are parsed into correct Verdict objects."""

    def test_valid_verdict_returns_correct_dataclass(self) -> None:
        output = _make_verdict_block(
            status="PASS",
            reason="全テスト通過",
            evidence="pytest: 10 passed",
            suggestion="",
        )
        result = parse_verdict(output, VALID_STATUSES)

        assert isinstance(result, Verdict)
        assert result.status == "PASS"
        assert result.reason == "全テスト通過"
        assert result.evidence == "pytest: 10 passed"


# ============================================================
# 2. Multi-line evidence (YAML block scalar)
# ============================================================


@pytest.mark.small
class TestMultiLineEvidence:
    """Evidence field using YAML block scalar | is parsed as multi-line string."""

    def test_multiline_evidence_preserved(self) -> None:
        body = (
            "status: PASS\n"
            'reason: "テスト結果確認"\n'
            "evidence: |\n"
            "  line1: pytest 10 passed\n"
            "  line2: coverage 85%\n"
            'suggestion: ""'
        )
        output = _wrap_verdict(body)
        result = parse_verdict(output, VALID_STATUSES)

        assert "line1" in result.evidence
        assert "line2" in result.evidence
        assert "\n" in result.evidence


# ============================================================
# 3. Multi-line suggestion (YAML block scalar)
# ============================================================


@pytest.mark.small
class TestMultiLineSuggestion:
    """Suggestion field using YAML block scalar | is parsed as multi-line string."""

    def test_multiline_suggestion_preserved(self) -> None:
        body = (
            "status: RETRY\n"
            'reason: "修正必要"\n'
            'evidence: "エラーあり"\n'
            "suggestion: |\n"
            "  step1: fix imports\n"
            "  step2: re-run tests"
        )
        output = _wrap_verdict(body)
        result = parse_verdict(output, VALID_STATUSES)

        assert "step1" in result.suggestion
        assert "step2" in result.suggestion
        assert "\n" in result.suggestion


# ============================================================
# 4. Verdict in middle of output
# ============================================================


@pytest.mark.small
class TestVerdictInMiddleOfOutput:
    """Verdict block surrounded by other text is still extracted."""

    def test_verdict_extracted_from_surrounding_text(self) -> None:
        verdict_block = _make_verdict_block(
            status="PASS",
            reason="OK",
            evidence="all green",
        )
        output = (
            f"Running tests...\nSome log output here\n{verdict_block}\nMore trailing output\nDone."
        )
        result = parse_verdict(output, VALID_STATUSES)

        assert result.status == "PASS"
        assert result.reason == "OK"


# ============================================================
# 5. All status values
# ============================================================


@pytest.mark.small
class TestAllStatusValues:
    """Each valid status value (PASS, RETRY, BACK, ABORT) is accepted."""

    @pytest.mark.parametrize("status", ["PASS", "RETRY", "BACK", "ABORT"])
    def test_each_status_accepted(self, status: str) -> None:
        suggestion = "次のステップ" if status in ("BACK", "ABORT") else ""
        output = _make_verdict_block(
            status=status,
            reason="理由",
            evidence="根拠",
            suggestion=suggestion,
        )
        result = parse_verdict(output, VALID_STATUSES)

        assert result.status == status


# ============================================================
# 6. ABORT with suggestion
# ============================================================


@pytest.mark.small
class TestAbortWithSuggestion:
    """ABORT status with a suggestion is valid."""

    def test_abort_with_suggestion_succeeds(self) -> None:
        output = _make_verdict_block(
            status="ABORT",
            reason="致命的エラー",
            evidence="segfault detected",
            suggestion="手動対応が必要",
        )
        result = parse_verdict(output, VALID_STATUSES)

        assert result.status == "ABORT"
        assert result.suggestion == "手動対応が必要"


# ============================================================
# 7. ABORT without suggestion → VerdictParseError
# ============================================================


@pytest.mark.small
class TestAbortWithoutSuggestion:
    """ABORT status without suggestion raises VerdictParseError."""

    def test_abort_missing_suggestion_raises(self) -> None:
        body = 'status: ABORT\nreason: "致命的エラー"\nevidence: "segfault"'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 8. BACK without suggestion → VerdictParseError
# ============================================================


@pytest.mark.small
class TestBackWithoutSuggestion:
    """BACK status without suggestion raises VerdictParseError."""

    def test_back_missing_suggestion_raises(self) -> None:
        body = 'status: BACK\nreason: "設計の見直し必要"\nevidence: "仕様不一致"'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 9. PASS without suggestion → defaults to empty string
# ============================================================


@pytest.mark.small
class TestPassWithoutSuggestion:
    """PASS status without suggestion defaults to empty string (no error)."""

    def test_pass_missing_suggestion_defaults_empty(self) -> None:
        body = 'status: PASS\nreason: "全テスト通過"\nevidence: "pytest: 10 passed"'
        output = _wrap_verdict(body)
        result = parse_verdict(output, VALID_STATUSES)

        assert result.status == "PASS"
        assert result.suggestion == ""


# ============================================================
# 10. VerdictNotFound — no verdict block
# ============================================================


@pytest.mark.small
class TestVerdictNotFoundNoBlock:
    """Output without ---VERDICT--- block raises VerdictNotFound."""

    def test_no_verdict_block_raises(self) -> None:
        output = "Some random output\nwithout any verdict block\n"

        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 11. VerdictNotFound — empty output
# ============================================================


@pytest.mark.small
class TestVerdictNotFoundEmpty:
    """Empty string raises VerdictNotFound."""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(VerdictNotFound):
            parse_verdict("", VALID_STATUSES)


# ============================================================
# 12. InvalidVerdictValue — unknown status
# ============================================================


@pytest.mark.small
class TestInvalidVerdictValue:
    """Status not in valid_statuses raises InvalidVerdictValue."""

    def test_unknown_status_raises(self) -> None:
        output = _make_verdict_block(
            status="UNKNOWN",
            reason="不明",
            evidence="N/A",
        )

        with pytest.raises(InvalidVerdictValue):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 13. Missing status field → VerdictParseError
# ============================================================


@pytest.mark.small
class TestMissingStatusField:
    """YAML without status field raises VerdictParseError."""

    def test_missing_status_raises(self) -> None:
        body = 'reason: "理由"\nevidence: "根拠"\nsuggestion: "提案"'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 14. Missing reason field → VerdictParseError
# ============================================================


@pytest.mark.small
class TestMissingReasonField:
    """Empty/missing reason field raises VerdictParseError."""

    def test_missing_reason_raises(self) -> None:
        body = 'status: PASS\nevidence: "根拠"\nsuggestion: ""'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 15. Missing evidence field → VerdictParseError
# ============================================================


@pytest.mark.small
class TestMissingEvidenceField:
    """Empty/missing evidence field raises VerdictParseError."""

    def test_missing_evidence_raises(self) -> None:
        body = 'status: PASS\nreason: "理由"\nsuggestion: ""'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 16. Invalid YAML → VerdictParseError
# ============================================================


@pytest.mark.small
class TestInvalidYaml:
    """Garbage between delimiters raises VerdictParseError."""

    def test_garbage_yaml_raises(self) -> None:
        body = "{{{{not valid yaml at all:::}}}}"
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 17. Not a YAML mapping → VerdictParseError
# ============================================================


@pytest.mark.small
class TestNotYamlMapping:
    """Plain string (not a mapping) between delimiters raises VerdictParseError."""

    def test_plain_string_raises(self) -> None:
        body = "just a plain string, not key-value pairs"
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# 18. Multiple verdict blocks → first one is used
# ============================================================


@pytest.mark.small
class TestMultipleVerdictBlocks:
    """When multiple verdict blocks exist, the first one is used."""

    def test_first_verdict_block_used(self) -> None:
        first = _make_verdict_block(
            status="PASS",
            reason="最初の判定",
            evidence="first evidence",
        )
        second = _make_verdict_block(
            status="RETRY",
            reason="2番目の判定",
            evidence="second evidence",
            suggestion="リトライ提案",
        )
        output = f"prefix\n{first}\nmiddle\n{second}\nsuffix"

        result = parse_verdict(output, VALID_STATUSES)

        assert result.status == "PASS"
        assert result.reason == "最初の判定"


# ============================================================
# Step 2a: Delimiter 緩和 (Relaxed delimiter matching)
# ============================================================


@pytest.mark.small
class TestRelaxedDelimiterEndSpace:
    """---END VERDICT--- (space instead of underscore) is accepted. #73 real case."""

    def test_end_verdict_with_space(self) -> None:
        output = (
            "---VERDICT---\n"
            "status: PASS\n"
            'reason: "PR作成成功"\n'
            'evidence: "gh pr create OK"\n'
            'suggestion: ""\n'
            "---END VERDICT---"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert result.reason == "PR作成成功"


@pytest.mark.small
class TestRelaxedDelimiterLowercase:
    """---end_verdict--- (lowercase) is accepted."""

    def test_lowercase_delimiters(self) -> None:
        output = (
            "---verdict---\n"
            "status: RETRY\n"
            'reason: "テスト失敗"\n'
            'evidence: "2 failed"\n'
            'suggestion: "再実行"\n'
            "---end_verdict---"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "RETRY"


@pytest.mark.small
class TestRelaxedDelimiterSurroundingSpaces:
    """--- VERDICT --- (surrounding spaces) is accepted."""

    def test_spaces_around_verdict(self) -> None:
        output = (
            "--- VERDICT ---\n"
            "status: PASS\n"
            'reason: "OK"\n'
            'evidence: "all green"\n'
            "--- END VERDICT ---"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestRelaxedDelimiterMixedCase:
    """Start normal, end with space — mixed delimiter styles."""

    def test_mixed_start_strict_end_relaxed(self) -> None:
        output = (
            "---VERDICT---\n"
            "status: PASS\n"
            'reason: "mixed"\n'
            'evidence: "delimiters"\n'
            "---END VERDICT---"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestRelaxedDelimiterExtraWhitespace:
    """Delimiter with extra blank lines and log lines around it."""

    def test_extra_lines_around_delimiters(self) -> None:
        output = (
            "Some log output\n"
            "\n"
            "---VERDICT---\n"
            "status: PASS\n"
            'reason: "OK"\n'
            'evidence: "green"\n'
            "\n"
            "--- END_VERDICT ---\n"
            "\n"
            "More trailing log"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


# ============================================================
# Step 2b: Key-Value パターン (Relaxed field extraction)
# ============================================================


@pytest.mark.small
class TestRelaxedPatternResultColon:
    """'Result: PASS' pattern is recognized."""

    def test_result_colon_pass(self) -> None:
        output = (
            "## VERDICT\n"
            "- Result: PASS\n"
            "- Reason: テスト成功\n"
            "- Evidence: pytest 10 passed\n"
            "- Suggestion: なし\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert result.reason == "テスト成功"


@pytest.mark.small
class TestRelaxedPatternStatusColon:
    """'Status: PASS' legacy pattern is recognized."""

    def test_status_colon_pass(self) -> None:
        output = (
            "## Review Result\n"
            "- Status: PASS\n"
            "- Reason: 全チェック通過\n"
            "- Evidence: ruff/mypy clean\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestRelaxedPatternDashResult:
    """'- Result: RETRY' list form pattern."""

    def test_dash_result_retry(self) -> None:
        output = "Some output\n- Result: RETRY\n- Reason: 修正必要\n- Evidence: 3 errors\n"
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "RETRY"


@pytest.mark.small
class TestRelaxedPatternDashStatus:
    """'- Status: BACK' list form with Status key."""

    def test_dash_status_back(self) -> None:
        output = (
            "Review complete\n"
            "- Status: BACK\n"
            "- Reason: 設計見直し\n"
            "- Evidence: API不整合\n"
            "- Suggestion: 再設計必要\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "BACK"


@pytest.mark.small
class TestRelaxedPatternMarkdownBold:
    """'**Status**: ABORT' markdown bold form."""

    def test_markdown_bold_status(self) -> None:
        output = (
            "## Result\n"
            "**Status**: ABORT\n"
            "**Reason**: 環境エラー\n"
            "**Evidence**: DB接続失敗\n"
            "**Suggestion**: DB再起動\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "ABORT"


@pytest.mark.small
class TestRelaxedPatternJapanese:
    """'ステータス: PASS' Japanese pattern."""

    def test_japanese_status(self) -> None:
        output = "ステータス: PASS\nリーズン: OK\nエビデンス: テスト全通過\n"
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestRelaxedPatternAssignmentForm:
    """'Status = PASS' / 'Result = PASS' assignment forms."""

    @pytest.mark.parametrize("key", ["Status", "Result"])
    def test_assignment_form(self, key: str) -> None:
        output = f"{key} = PASS\nReason: OK\nEvidence: テスト通過\n"
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestRelaxedPatternReasonEvidenceSuggestion:
    """reason / evidence / suggestion fields extracted via relaxed patterns."""

    def test_all_fields_extracted(self) -> None:
        output = (
            "Result: BACK\n"
            "Reason: 設計変更が必要\n"
            "Evidence: インターフェース不整合を検出\n"
            "Suggestion: issue-design を再実行\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "BACK"
        assert "設計変更" in result.reason
        assert "インターフェース不整合" in result.evidence
        assert "issue-design" in result.suggestion


@pytest.mark.small
class TestRelaxedPatternStatusOnlyFallsThrough:
    """Status found but reason/evidence missing → falls to Step 3 or raises."""

    def test_status_only_without_formatter_raises(self) -> None:
        output = "Result: PASS\n"  # No reason or evidence
        with pytest.raises((VerdictNotFound, VerdictParseError)):
            parse_verdict(output, VALID_STATUSES)


@pytest.mark.small
class TestRelaxedPatternFalsePositiveExclusion:
    """Invalid values like 'Status: 200' or 'Result = success' don't match."""

    def test_status_200_does_not_match(self) -> None:
        output = "HTTP Status: 200\nResult = success\nAll done"
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES)

    def test_status_running_does_not_match(self) -> None:
        output = "Status: running\nResult: pending\n"
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES)


# ============================================================
# Step 3: AI Formatter Retry
# ============================================================


@pytest.mark.small
class TestAIFormatterSuccess:
    """ai_formatter returns strict-parseable output → success."""

    def test_formatter_strict_success(self) -> None:
        def mock_formatter(text: str) -> str:
            return _make_verdict_block(status="PASS", reason="formatted OK", evidence="AI fixed it")

        output = "garbage that can't be parsed at all"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter, max_retries=2)
        assert result.status == "PASS"
        assert result.reason == "formatted OK"


@pytest.mark.small
class TestAIFormatterRelaxedSuccess:
    """ai_formatter returns relaxed-only parseable output → success."""

    def test_formatter_relaxed_success(self) -> None:
        def mock_formatter(text: str) -> str:
            return (
                "--- VERDICT ---\n"
                "status: RETRY\n"
                'reason: "AI formatted"\n'
                'evidence: "relaxed match"\n'
                "--- END VERDICT ---"
            )

        output = "unparseable text"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter, max_retries=2)
        assert result.status == "RETRY"


@pytest.mark.small
class TestAIFormatterAllRetriesFail:
    """ai_formatter always returns garbage → VerdictParseError."""

    def test_all_retries_fail(self) -> None:
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return "still garbage"

        with pytest.raises(VerdictParseError):
            parse_verdict("unparseable", VALID_STATUSES, ai_formatter=mock_formatter, max_retries=3)
        assert call_count == 3


@pytest.mark.small
class TestAIFormatterNotProvidedStep2Fails:
    """No ai_formatter + Step 2 failure → VerdictParseError (not VerdictNotFound)."""

    def test_no_formatter_raises_parse_error(self) -> None:
        # This output has no verdict block and no matching patterns
        with pytest.raises((VerdictNotFound, VerdictParseError)):
            parse_verdict("completely empty of verdicts", VALID_STATUSES)


@pytest.mark.small
class TestAIFormatterMaxRetries1:
    """max_retries=1 → exactly 1 retry attempt."""

    def test_single_retry(self) -> None:
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return "bad"

        with pytest.raises(VerdictParseError):
            parse_verdict("unparseable", VALID_STATUSES, ai_formatter=mock_formatter, max_retries=1)
        assert call_count == 1


@pytest.mark.small
class TestAIFormatterMaxRetriesInvalid:
    """max_retries < 1 → ValueError."""

    def test_zero_retries_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_verdict("text", VALID_STATUSES, ai_formatter=lambda t: t, max_retries=0)


@pytest.mark.small
class TestAIFormatterValidStatusesRestriction:
    """Formatter prompt should respect valid_statuses."""

    def test_formatter_prompt_contains_valid_statuses(self) -> None:
        from kaji_harness.verdict import FORMATTER_PROMPT

        # The prompt template should have placeholders for valid_statuses
        assert "{valid_statuses_str}" in FORMATTER_PROMPT


# ============================================================
# Output collection layer
# ============================================================


@pytest.mark.small
class TestCodexAdapterMcpToolCall:
    """CodexAdapter extracts text from mcp_tool_call items."""

    def test_mcp_tool_call_text_extracted(self) -> None:
        adapter = CodexAdapter()
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {
                    "content": [
                        {"type": "text", "text": "---VERDICT---\nstatus: PASS\n---END_VERDICT---"}
                    ]
                },
            },
        }
        text = adapter.extract_text(event)
        assert text is not None
        assert "VERDICT" in text

    def test_mcp_tool_call_empty_content(self) -> None:
        adapter = CodexAdapter()
        event = {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "result": {"content": []}},
        }
        assert adapter.extract_text(event) is None

    def test_mcp_tool_call_no_result(self) -> None:
        adapter = CodexAdapter()
        event = {"type": "item.completed", "item": {"type": "mcp_tool_call"}}
        assert adapter.extract_text(event) is None

    def test_agent_message_still_works(self) -> None:
        """Existing agent_message behavior is preserved."""
        adapter = CodexAdapter()
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "hello"},
        }
        assert adapter.extract_text(event) == "hello"


# ============================================================
# Cross-cutting: InvalidVerdictValue from Step 1 and Step 3
# ============================================================


@pytest.mark.small
class TestInvalidVerdictValueStrictRaise:
    """InvalidVerdictValue from Step 1 (strict) is raised immediately, no fallback."""

    def test_strict_invalid_value_immediate_raise(self) -> None:
        output = _make_verdict_block(
            status="INVALID_STATUS",
            reason="bad",
            evidence="bad",
        )
        with pytest.raises(InvalidVerdictValue):
            parse_verdict(output, VALID_STATUSES)


@pytest.mark.small
class TestInvalidVerdictValueFormatterRaise:
    """InvalidVerdictValue from Step 3 (formatter output) is raised immediately."""

    def test_formatter_invalid_value_raise(self) -> None:
        def mock_formatter(text: str) -> str:
            return _make_verdict_block(
                status="BOGUS",
                reason="formatter made bad value",
                evidence="bad",
            )

        with pytest.raises(InvalidVerdictValue):
            parse_verdict(
                "unparseable",
                VALID_STATUSES,
                ai_formatter=mock_formatter,
                max_retries=2,
            )


@pytest.mark.small
class TestRelaxedPatternNoInvalidVerdictValue:
    """Step 2b patterns only match valid_statuses, so InvalidVerdictValue is structurally impossible."""

    def test_patterns_match_only_valid_statuses(self) -> None:
        patterns = _build_relaxed_status_patterns({"PASS", "ABORT"})
        # "RETRY" is not in valid_statuses, so should not match
        text = "Status: RETRY\nResult: RETRY"
        for p in patterns:
            assert p.search(text) is None

    def test_patterns_match_valid_statuses(self) -> None:
        patterns = _build_relaxed_status_patterns({"PASS", "ABORT"})
        text = "Status: PASS"
        matched = any(p.search(text) for p in patterns)
        assert matched


# ============================================================
# Cross-cutting: Input truncation
# ============================================================


@pytest.mark.small
class TestInputTruncation:
    """Texts exceeding AI_FORMATTER_MAX_INPUT_CHARS are truncated."""

    def test_long_input_truncated_for_formatter(self) -> None:
        call_args: list[str] = []

        def capturing_formatter(text: str) -> str:
            call_args.append(text)
            return _make_verdict_block(status="PASS", reason="OK", evidence="green")

        long_output = "x" * (AI_FORMATTER_MAX_INPUT_CHARS + 5000)
        result = parse_verdict(
            long_output,
            VALID_STATUSES,
            ai_formatter=capturing_formatter,
            max_retries=1,
        )
        assert result.status == "PASS"
        assert len(call_args[0]) <= AI_FORMATTER_MAX_INPUT_CHARS
        assert "[truncated]" in call_args[0]


# ============================================================
# Cross-cutting: Noise around verdict
# ============================================================


@pytest.mark.small
class TestNoiseAroundVerdict:
    """Verdict block with surrounding noise (logs, thinking traces) is extracted."""

    def test_noise_before_and_after(self) -> None:
        verdict = _make_verdict_block(status="PASS", reason="OK", evidence="clean")
        output = (
            "思考トレース: analyzing the output...\n"
            "[DEBUG] processing step result\n"
            f"{verdict}\n"
            "[INFO] step completed successfully\n"
            "additional trailing noise\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"


@pytest.mark.small
class TestVerdictMiddleWithTrailingNoise:
    """Verdict in middle of output (non-tail position) + trailing noise."""

    def test_verdict_not_at_end(self) -> None:
        verdict = _make_verdict_block(status="RETRY", reason="issues found", evidence="3 failures")
        output = (
            "Starting analysis...\n"
            f"{verdict}\n"
            "Post-verdict processing:\n"
            "- Saving results\n"
            "- Cleaning up\n"
            "- Done\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "RETRY"


# ============================================================
# Internal helpers: _extract_block_strict / _extract_block_relaxed
# ============================================================


@pytest.mark.small
class TestExtractBlockStrict:
    """_extract_block_strict returns YAML body from strict delimiters."""

    def test_extracts_body(self) -> None:
        output = "prefix\n---VERDICT---\nstatus: PASS\n---END_VERDICT---\nsuffix"
        body = _extract_block_strict(output)
        assert body is not None
        assert "status: PASS" in body

    def test_returns_none_on_no_match(self) -> None:
        assert _extract_block_strict("no verdict here") is None


@pytest.mark.small
class TestExtractBlockRelaxed:
    """_extract_block_relaxed handles delimiter variations."""

    def test_space_in_end_delimiter(self) -> None:
        output = "---VERDICT---\nstatus: PASS\n---END VERDICT---"
        body = _extract_block_relaxed(output)
        assert body is not None
        assert "status: PASS" in body

    def test_lowercase(self) -> None:
        output = "---verdict---\nstatus: PASS\n---end_verdict---"
        body = _extract_block_relaxed(output)
        assert body is not None

    def test_returns_none_on_no_match(self) -> None:
        assert _extract_block_relaxed("no verdict here") is None


# ============================================================
# Internal helpers: _parse_yaml_fields
# ============================================================


@pytest.mark.small
class TestParseYamlFields:
    """_parse_yaml_fields parses YAML body into Verdict."""

    def test_valid_yaml(self) -> None:
        body = 'status: PASS\nreason: "OK"\nevidence: "green"\nsuggestion: ""'
        verdict = _parse_yaml_fields(body)
        assert verdict.status == "PASS"

    def test_missing_status_raises(self) -> None:
        with pytest.raises(VerdictParseError):
            _parse_yaml_fields('reason: "OK"\nevidence: "green"')


# ============================================================
# Internal helpers: _build_relaxed_status_patterns
# ============================================================


@pytest.mark.small
class TestBuildRelaxedStatusPatterns:
    """_build_relaxed_status_patterns generates patterns restricted to valid_statuses."""

    def test_patterns_count(self) -> None:
        patterns = _build_relaxed_status_patterns({"PASS", "ABORT"})
        # Should generate patterns for all template forms
        assert len(patterns) >= 9

    def test_pattern_matches_valid(self) -> None:
        patterns = _build_relaxed_status_patterns({"PASS"})
        assert any(p.search("status: PASS") for p in patterns)

    def test_pattern_rejects_invalid(self) -> None:
        patterns = _build_relaxed_status_patterns({"PASS"})
        assert not any(p.search("status: RETRY") for p in patterns)


# ============================================================
# Internal helpers: _parse_relaxed_fields
# ============================================================


@pytest.mark.small
class TestParseRelaxedFields:
    """_parse_relaxed_fields extracts verdict from key-value patterns."""

    def test_full_extraction(self) -> None:
        text = "Result: PASS\nReason: good\nEvidence: all green\nSuggestion: none"
        verdict = _parse_relaxed_fields(text, VALID_STATUSES)
        assert verdict.status == "PASS"
        assert verdict.reason == "good"

    def test_no_status_raises(self) -> None:
        text = "No matching patterns here"
        with pytest.raises(VerdictParseError):
            _parse_relaxed_fields(text, VALID_STATUSES)

    def test_status_only_no_reason_raises(self) -> None:
        """Status found but reason/evidence missing → VerdictParseError."""
        text = "Result: PASS"
        with pytest.raises(VerdictParseError):
            _parse_relaxed_fields(text, VALID_STATUSES)
