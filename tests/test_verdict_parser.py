"""Small tests for verdict parser.

Tests the parse_verdict function which extracts structured Verdict
data from CLI output containing ---VERDICT--- / ---END_VERDICT--- blocks.
"""

from __future__ import annotations

import pytest

from kaji_harness.errors import InvalidVerdictValue, VerdictNotFound, VerdictParseError
from kaji_harness.models import Verdict
from kaji_harness.verdict import parse_verdict

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
# 1. Normal extraction
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
