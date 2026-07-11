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
    ControlCharFinding,
    _build_relaxed_status_patterns,
    _extract_block_relaxed,
    _extract_block_strict,
    _parse_relaxed_fields,
    _parse_yaml_fields,
    _sanitize_yaml_control_chars,
    parse_verdict,
    parse_verdict_block,
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
# 8b. BACK_DESIGN / BACK_IMPLEMENT suggestion requirement
# ============================================================


@pytest.mark.small
class TestBackPrefixSuggestionRequired:
    """BACK_* prefixed statuses require non-empty suggestion (same as BACK)."""

    def test_back_design_missing_suggestion_raises(self) -> None:
        valid = {"PASS", "RETRY", "BACK_DESIGN", "BACK_IMPLEMENT", "ABORT"}
        body = 'status: BACK_DESIGN\nreason: "設計起因"\nevidence: "影響ドキュメント漏れ"'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, valid)

    def test_back_implement_missing_suggestion_raises(self) -> None:
        valid = {"PASS", "RETRY", "BACK_DESIGN", "BACK_IMPLEMENT", "ABORT"}
        body = 'status: BACK_IMPLEMENT\nreason: "実装起因"\nevidence: "テスト証跡欠落"'
        output = _wrap_verdict(body)

        with pytest.raises(VerdictParseError):
            parse_verdict(output, valid)

    def test_back_design_with_suggestion_succeeds(self) -> None:
        valid = {"PASS", "RETRY", "BACK_DESIGN", "BACK_IMPLEMENT", "ABORT"}
        body = (
            'status: BACK_DESIGN\nreason: "設計起因"\n'
            'evidence: "影響ドキュメント漏れ"\nsuggestion: "/issue-design に戻る"'
        )
        output = _wrap_verdict(body)
        result = parse_verdict(output, valid)

        assert result.status == "BACK_DESIGN"
        assert result.suggestion == "/issue-design に戻る"


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
        output = "ステータス: PASS\n理由: OK\n根拠: テスト全通過\n"
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

        # delimiter exists but inner YAML is malformed → Step 3 gate passes
        output = "---VERDICT---\nstatus: ???\n---END_VERDICT---"
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

        # delimiter exists but inner YAML is malformed → Step 3 gate passes
        output = "---VERDICT---\nstatus: ???\n---END_VERDICT---"
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

        # delimiter exists but inner YAML is malformed → Step 3 gate passes
        output = "---VERDICT---\nstatus: ???\n---END_VERDICT---"
        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter, max_retries=3)
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

        # delimiter exists but inner YAML is malformed → Step 3 gate passes
        output = "---VERDICT---\nstatus: ???\n---END_VERDICT---"
        with pytest.raises(VerdictParseError):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter, max_retries=1)
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
        assert "$valid_statuses_str" in FORMATTER_PROMPT.template


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

        # delimiter exists but inner YAML is malformed → Step 3 gate passes
        output = "---VERDICT---\nstatus: ???\n---END_VERDICT---"
        with pytest.raises(InvalidVerdictValue):
            parse_verdict(
                output,
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

        # Tail must contain a delimiter so the Step 3 gate passes, otherwise
        # the truncation strategy is never exercised (head/tail strategy keeps
        # the tail, where the delimiter lives, intact).
        long_output = (
            "x" * (AI_FORMATTER_MAX_INPUT_CHARS + 5000)
            + "\n---VERDICT---\nstatus: ???\n---END_VERDICT---"
        )
        result = parse_verdict(
            long_output,
            VALID_STATUSES,
            ai_formatter=capturing_formatter,
            max_retries=1,
        )
        assert result.status == "PASS"
        assert len(call_args[0]) <= AI_FORMATTER_MAX_INPUT_CHARS
        assert "[truncated]" in call_args[0]
        # The trailing delimiter must survive truncation (gate prerequisite).
        assert "---VERDICT---" in call_args[0]


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

    def test_non_control_char_parse_failure_unaffected(self) -> None:
        """禁止制御文字以外の parse 失敗（非 mapping）は従来どおり。"""
        with pytest.raises(VerdictParseError):
            _parse_yaml_fields("just a plain string, not a mapping")


# ============================================================
# Issue #298: YAML 禁止制御文字の sanitize 境界
# ============================================================


@pytest.mark.small
class TestSanitizeYamlControlCharsBoundary:
    """YAML 1.2 の c-printable 範囲外の制御文字を検出順に U+FFFD へ置換する。

    許可 / 禁止の境界（0x1F禁止/0x20許可、0x7E許可/0x7F禁止、0x84禁止/0x85許可/
    0x86禁止、0x9F禁止/0xA0許可）を明示的に固定する。
    """

    @pytest.mark.parametrize(
        "codepoint",
        [0x09, 0x0A, 0x0D, 0x20, 0x85, 0x7E],
        ids=["TAB", "LF", "CR", "SPACE", "NEL", "TILDE"],
    )
    def test_allowed_codepoints_are_not_replaced(self, codepoint: int) -> None:
        text = f"a{chr(codepoint)}b"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == text
        assert findings == []

    @pytest.mark.parametrize(
        "codepoint",
        [
            0x00,
            0x08,
            0x0B,
            0x0C,
            0x0E,
            0x1B,  # ESC — Issue #137/#298 の一次障害文字
            0x1F,
            0x7F,
            0x80,
            0x84,
            0x86,
            0x9F,
        ],
        ids=[
            "NUL",
            "BS",
            "VT",
            "FF",
            "SO",
            "ESC",
            "US",
            "DEL",
            "0x80",
            "0x84",
            "0x86",
            "0x9F",
        ],
    )
    def test_forbidden_codepoints_are_replaced(self, codepoint: int) -> None:
        text = f"a{chr(codepoint)}b"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "a�b"
        assert findings == [ControlCharFinding(position=1, codepoint=codepoint)]
        assert findings[0].label == f"U+{codepoint:04X}"

    def test_boundary_0x1f_forbidden_0x20_allowed(self) -> None:
        text = f"{chr(0x1F)}{chr(0x20)}"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "� "
        assert [f.codepoint for f in findings] == [0x1F]

    def test_boundary_0x7e_allowed_0x7f_forbidden(self) -> None:
        text = f"{chr(0x7E)}{chr(0x7F)}"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "~�"
        assert [f.codepoint for f in findings] == [0x7F]

    def test_boundary_0x84_forbidden_0x85_allowed_0x86_forbidden(self) -> None:
        text = f"{chr(0x84)}{chr(0x85)}{chr(0x86)}"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "�\x85�"
        assert [f.codepoint for f in findings] == [0x84, 0x86]

    def test_boundary_0x9f_forbidden_0xa0_allowed(self) -> None:
        text = f"{chr(0x9F)}{chr(0xA0)}"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "�\xa0"
        assert [f.codepoint for f in findings] == [0x9F]

    def test_no_forbidden_chars_returns_text_unchanged_and_empty_findings(self) -> None:
        text = "plain ascii and 日本語"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == text
        assert findings == []

    def test_multiple_forbidden_chars_detected_in_order(self) -> None:
        text = f"a{chr(0x1B)}b{chr(0x00)}c"
        sanitized, findings = _sanitize_yaml_control_chars(text)
        assert sanitized == "a�b�c"
        assert [f.position for f in findings] == [1, 3]
        assert [f.codepoint for f in findings] == [0x1B, 0x00]


# ============================================================
# Issue #298: 禁止制御文字混入 verdict の再現 → 修正後の意味的解決
# ============================================================


@pytest.mark.small
class TestVerdictWithControlCharsResolvesSemantically:
    """#137 実障害（evidence への生 ESC 混入）の再現と修正後の解決確認。"""

    def test_esc_in_evidence_resolves_to_pass(self) -> None:
        body = 'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""'
        verdict = _parse_yaml_fields(body)
        assert verdict.status == "PASS"
        assert verdict.evidence == "done�here"

    def test_findings_sink_receives_sanitize_findings(self) -> None:
        sink: list[ControlCharFinding] = []
        body = 'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""'
        _parse_yaml_fields(body, findings_sink=sink)
        assert len(sink) == 1
        assert sink[0].codepoint == 0x1B
        assert sink[0].label == "U+001B"

    def test_no_findings_sink_leaves_sink_none_unaffected(self) -> None:
        body = 'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""'
        verdict = _parse_yaml_fields(body, findings_sink=None)
        assert verdict.status == "PASS"

    def test_three_paths_converge_on_same_sanitize_boundary(self) -> None:
        """stdout（parse_verdict）/ comment（parse_verdict_block）が同一入力を
        同じ境界（_parse_yaml_fields）で救済すること。"""
        control_char_block = (
            "---VERDICT---\n"
            'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""\n'
            "---END_VERDICT---"
        )

        stdout_result = parse_verdict(control_char_block, VALID_STATUSES)
        comment_result = parse_verdict_block(control_char_block, VALID_STATUSES)

        assert stdout_result.status == "PASS"
        assert stdout_result.evidence == "done�here"
        assert comment_result is not None
        assert comment_result.status == "PASS"
        assert comment_result.evidence == "done�here"


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


# ============================================================
# Issue #193: delimiter-presence-only Step 3 gate
# ============================================================


@pytest.mark.small
class TestStep3DelimiterGate:
    """Step 3 (AI formatter) must only run when a verdict delimiter was extracted.

    Without this gate, the formatter has been observed to fabricate PASS
    verdicts from natural-language progress reports (Issue #193 / #184).
    """

    def test_step3_rejects_output_without_delimiter_natural_language_only(self) -> None:
        """Issue #184 reproduction: progress report only, no delimiter, no status keyword."""
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return _make_verdict_block(status="PASS", reason="fabricated", evidence="fabricated")

        # Synthetic sample mirroring Issue #184 console.log tail.
        output = (
            "Phase A-G の実装完了、品質ゲート改善確認、pytest baseline clean から続行中\n"
            "baseline 改善確認OK。pytest 完了待ち。\n"
        )
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert call_count == 0, "Step 3 must not be invoked when no delimiter is present"

    def test_step3_rejects_output_with_status_keyword_only_no_delimiter(self) -> None:
        """Status keyword in natural language without delimiter → VerdictNotFound, no formatter."""
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return _make_verdict_block(status="PASS", reason="fabricated", evidence="fabricated")

        output = "I will set Status: PASS once tests finish.\npytest waiting"
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert call_count == 0, "Step 3 must not be invoked from status keyword alone"

    def test_step3_invoked_when_delimiter_present_but_malformed(self) -> None:
        """Delimiter present + malformed YAML → formatter IS invoked (gate passes)."""
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return _make_verdict_block(status="PASS", reason="recovered", evidence="recovered")

        output = "prefix\n---VERDICT---\nstatus: ???\nmalformed yaml :: ::\n---END_VERDICT---\n"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert result.status == "PASS"
        assert call_count == 1

    def test_step3_invoked_when_relaxed_delimiter_only_present(self) -> None:
        """Relaxed delimiter (space / case variation) + malformed → formatter invoked."""
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return _make_verdict_block(status="RETRY", reason="recovered", evidence="recovered")

        output = "--- VERDICT ---\nstatus: ???\nbroken\n--- END VERDICT ---\n"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert result.status == "RETRY"
        assert call_count == 1

    def test_step2b_still_succeeds_with_status_and_fields_no_delimiter(self) -> None:
        """V5/V6 compat: Status + Reason + Evidence without delimiter → Step 2b success."""
        call_count = 0

        def mock_formatter(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return _make_verdict_block(status="PASS", reason="should not be called", evidence="x")

        output = "Status: PASS\nReason: tests passed\nEvidence: 1384 passed"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert result.status == "PASS"
        assert result.reason == "tests passed"
        assert call_count == 0, "Step 2b should succeed without invoking the formatter"


@pytest.mark.small
class TestFormatterSentinel:
    """Formatter may emit NO_VERDICT_SENTINEL to signal that no verdict exists."""

    def test_formatter_sentinel_response_raises_verdict_not_found(self) -> None:
        """Formatter returning the sentinel alone → VerdictNotFound."""

        def mock_formatter(text: str) -> str:
            return "---NO_VERDICT_FOUND---"

        # Delimiter exists so Step 3 runs; formatter then returns sentinel.
        output = "---VERDICT---\n(progress report only)\n---END_VERDICT---"
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)

    def test_formatter_prompt_contains_sentinel_instruction(self) -> None:
        """FORMATTER_PROMPT documents the NO_VERDICT_FOUND sentinel for the AI."""
        from kaji_harness.verdict import FORMATTER_PROMPT

        assert "---NO_VERDICT_FOUND---" in FORMATTER_PROMPT.template

    def test_formatter_sentinel_with_surrounding_whitespace(self) -> None:
        """Sentinel surrounded by whitespace/newlines is still treated as sentinel."""

        def mock_formatter(text: str) -> str:
            return "\n\n---NO_VERDICT_FOUND---\n"

        output = "---VERDICT---\n(progress report only)\n---END_VERDICT---"
        with pytest.raises(VerdictNotFound):
            parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)

    def test_valid_verdict_quoting_sentinel_literal_is_not_misclassified(self) -> None:
        """A valid verdict whose body quotes the sentinel literal must parse normally.

        Regression: previously ``NO_VERDICT_SENTINEL in formatted`` used substring
        matching, so any verdict referencing the literal sentinel in reason /
        evidence (e.g. when documenting the failure mode) was misclassified as
        ``VerdictNotFound``.
        """

        def mock_formatter(text: str) -> str:
            return (
                "---VERDICT---\n"
                "status: PASS\n"
                'reason: "documented the ---NO_VERDICT_FOUND--- sentinel handling"\n'
                'evidence: "tests/test_verdict_parser.py mentions ---NO_VERDICT_FOUND---"\n'
                'suggestion: ""\n'
                "---END_VERDICT---\n"
            )

        # Delimiter present so Step 3 runs; strict/relaxed body parsing fails
        # (empty body) so the formatter is invoked.
        output = "---VERDICT---\n\n---END_VERDICT---"
        result = parse_verdict(output, VALID_STATUSES, ai_formatter=mock_formatter)
        assert result.status == "PASS"
        assert "---NO_VERDICT_FOUND---" in result.reason
