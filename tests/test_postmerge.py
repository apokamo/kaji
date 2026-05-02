"""Tests for kaji_harness.postmerge — pure functions for post-merge skills.

Issue: #164
"""

from __future__ import annotations

import json

import pytest

from kaji_harness.postmerge import (
    judge_main_ci,
    parse_codex_review_output,
    parse_pr_state,
    select_review_range,
)

# ============================================================
# parse_pr_state
# ============================================================


class TestParsePRStateSmall:
    @pytest.mark.small
    def test_merged(self) -> None:
        assert (
            parse_pr_state(json.dumps({"state": "MERGED", "mergedAt": "2026-01-01T00:00:00Z"}))
            == "MERGED"
        )

    @pytest.mark.small
    def test_open(self) -> None:
        assert parse_pr_state(json.dumps({"state": "OPEN", "mergedAt": None})) == "OPEN"

    @pytest.mark.small
    def test_closed_not_merged(self) -> None:
        assert (
            parse_pr_state(json.dumps({"state": "CLOSED", "mergedAt": None})) == "CLOSED_NOT_MERGED"
        )

    @pytest.mark.small
    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pr_state("not json")

    @pytest.mark.small
    def test_missing_state_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pr_state(json.dumps({"mergedAt": None}))

    @pytest.mark.small
    def test_unknown_state_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pr_state(json.dumps({"state": "WEIRD"}))


# ============================================================
# judge_main_ci
# ============================================================


class TestJudgeMainCISmall:
    @pytest.mark.small
    def test_all_green(self) -> None:
        runs = [{"status": "completed", "conclusion": "success"} for _ in range(3)]
        assert judge_main_ci(json.dumps(runs)) == "GREEN"

    @pytest.mark.small
    def test_in_progress(self) -> None:
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "in_progress", "conclusion": None},
        ]
        assert judge_main_ci(json.dumps(runs)) == "IN_PROGRESS"

    @pytest.mark.small
    def test_failure(self) -> None:
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert judge_main_ci(json.dumps(runs)) == "FAILED"

    @pytest.mark.small
    def test_failure_priority_over_in_progress(self) -> None:
        """failure と in_progress が混在する境界: failure 優先で早期 ABORT。"""
        runs = [
            {"status": "in_progress", "conclusion": None},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert judge_main_ci(json.dumps(runs)) == "FAILED"

    @pytest.mark.small
    def test_empty_returns_not_found(self) -> None:
        assert judge_main_ci("[]") == "NOT_FOUND"

    @pytest.mark.small
    def test_skipped_neutral_treated_as_green(self) -> None:
        runs = [
            {"status": "completed", "conclusion": "skipped"},
            {"status": "completed", "conclusion": "success"},
        ]
        assert judge_main_ci(json.dumps(runs)) == "GREEN"

    @pytest.mark.small
    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError):
            judge_main_ci("not json")


# ============================================================
# select_review_range
# ============================================================


class TestSelectReviewRangeSmall:
    @pytest.mark.small
    def test_merge_commit_two_parents(self) -> None:
        assert select_review_range("abc123", 2) == ("abc123^1", "abc123")

    @pytest.mark.small
    def test_squash_one_parent(self) -> None:
        assert select_review_range("def456", 1) == ("def456~1", "def456")

    @pytest.mark.small
    def test_zero_parents_raises(self) -> None:
        with pytest.raises(ValueError):
            select_review_range("abc", 0)

    @pytest.mark.small
    def test_empty_sha_raises(self) -> None:
        with pytest.raises(ValueError):
            select_review_range("", 2)


# ============================================================
# parse_codex_review_output
# ============================================================


class TestParseCodexReviewOutputSmall:
    @pytest.mark.small
    def test_pass(self) -> None:
        output = """\
some preamble
---VERDICT---
status: PASS
reason: ok
---END_VERDICT---
"""
        assert parse_codex_review_output(output) == "PASS"

    @pytest.mark.small
    def test_retry(self) -> None:
        output = """---VERDICT---
status: RETRY
reason: needs fix
---END_VERDICT---"""
        assert parse_codex_review_output(output) == "RETRY"

    @pytest.mark.small
    def test_no_verdict_block_aborts(self) -> None:
        assert parse_codex_review_output("just text") == "ABORT"

    @pytest.mark.small
    def test_unknown_status_aborts(self) -> None:
        output = """---VERDICT---
status: WEIRD
---END_VERDICT---"""
        assert parse_codex_review_output(output) == "ABORT"

    @pytest.mark.small
    def test_missing_status_aborts(self) -> None:
        output = """---VERDICT---
reason: no status here
---END_VERDICT---"""
        assert parse_codex_review_output(output) == "ABORT"
