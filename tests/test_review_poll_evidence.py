"""Tests for kaji_harness.review_poll_evidence (schema v1 approval evidence)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from kaji_harness.review_poll_evidence import (
    EVIDENCE_FILENAME,
    ReviewPollEvidence,
    load_evidence,
    parse_timestamp,
    save_evidence,
)

HEAD = "91d11b151e50a90ff02ffd427205f78017205bb4"


def _valid_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "kaji.review-poll.approval",
        "result": "PASS",
        "provider": "github",
        "repository": {"owner": "apokamo", "name": "fullstack-agent-template"},
        "pull_request": {
            "number": 163,
            "url": "https://github.com/apokamo/fullstack-agent-template/pull/163",
        },
        "reviewed_head": {"sha": HEAD, "committed_at": "2026-09-08T01:23:50Z"},
        "approval": {
            "bot": {"id": 199175422, "login": "chatgpt-codex-connector[bot]"},
            "reaction": {
                "id": 123456789,
                "content": "+1",
                "created_at": "2026-09-08T01:33:35Z",
                "api_path": "repos/apokamo/fullstack-agent-template/issues/163/reactions",
            },
            "review_summary": {
                "comment_id": 5577710898,
                "url": (
                    "https://github.com/apokamo/fullstack-agent-template/pull/163"
                    "#issuecomment-5577710898"
                ),
                "commit_short_sha": "91d11b1",
                "status": "Completed",
                "completed_at": "2026-09-08T01:33:32.391219Z",
                "comment_updated_at": "2026-09-08T01:33:33Z",
            },
        },
        "checks": {
            "head_sha_at_start": HEAD,
            "head_sha_before_decision": HEAD,
            "head_sha_after_decision": HEAD,
            "short_sha_matching_pr_commits": 1,
            "current_head_bot_reviews": 0,
        },
        "fetched_at": "2026-09-08T01:33:41Z",
        "decided_at": "2026-09-08T01:33:42Z",
    }


def _set(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def _delete(payload: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    node = out
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return out


_OTHER_HEAD = "0" * 40

# (id, mutated payload) — 各ケースが不変条件 I1〜I9 の違反を 1 つ表す。
_VIOLATIONS: list[tuple[str, dict[str, Any]]] = [
    # I1
    ("schema_version_2", _set(_valid_payload(), ("schema_version",), 2)),
    ("kind_other", _set(_valid_payload(), ("kind",), "kaji.other")),
    ("result_retry", _set(_valid_payload(), ("result",), "RETRY")),
    ("provider_gitlab", _set(_valid_payload(), ("provider",), "gitlab")),
    ("top_level_extra_key", _set(_valid_payload(), ("unexpected",), 1)),
    ("approval_missing", _delete(_valid_payload(), ("approval",))),
    ("repository_missing", _delete(_valid_payload(), ("repository",))),
    ("checks_missing", _delete(_valid_payload(), ("checks",))),
    ("fetched_at_missing", _delete(_valid_payload(), ("fetched_at",))),
    ("section_extra_key", _set(_valid_payload(), ("approval", "bot", "extra"), 1)),
    ("section_missing_key", _delete(_valid_payload(), ("approval", "reaction", "api_path"))),
    ("checks_missing_key", _delete(_valid_payload(), ("checks", "head_sha_at_start"))),
    # I2
    ("repository_owner_empty", _set(_valid_payload(), ("repository", "owner"), "")),
    ("pull_number_zero", _set(_valid_payload(), ("pull_request", "number"), 0)),
    ("pull_url_not_github", _set(_valid_payload(), ("pull_request", "url"), "https://x/pull/163")),
    (
        "pull_url_number_mismatch",
        _set(_valid_payload(), ("pull_request", "url"), "https://github.com/a/b/pull/164"),
    ),
    # I3
    ("sha_39_chars", _set(_valid_payload(), ("reviewed_head", "sha"), HEAD[:39])),
    ("sha_uppercase", _set(_valid_payload(), ("reviewed_head", "sha"), HEAD.upper())),
    ("sha_non_hex", _set(_valid_payload(), ("reviewed_head", "sha"), "g" * 40)),
    ("committed_at_unparsable", _set(_valid_payload(), ("reviewed_head", "committed_at"), "x")),
    (
        "committed_at_invalid_date",
        _set(_valid_payload(), ("reviewed_head", "committed_at"), "2026-13-45T00:00:00Z"),
    ),
    # I4
    ("bot_id_mismatch", _set(_valid_payload(), ("approval", "bot", "id"), 1)),
    ("bot_login_mismatch", _set(_valid_payload(), ("approval", "bot", "login"), "someone")),
    # I5
    ("reaction_id_zero", _set(_valid_payload(), ("approval", "reaction", "id"), 0)),
    ("reaction_id_string", _set(_valid_payload(), ("approval", "reaction", "id"), "123")),
    (
        "reaction_content_heart",
        _set(_valid_payload(), ("approval", "reaction", "content"), "heart"),
    ),
    (
        "reaction_created_at_missing",
        _delete(_valid_payload(), ("approval", "reaction", "created_at")),
    ),
    (
        "reaction_created_at_unparsable",
        _set(_valid_payload(), ("approval", "reaction", "created_at"), "soon"),
    ),
    (
        "reaction_api_path_mismatch",
        _set(
            _valid_payload(),
            ("approval", "reaction", "api_path"),
            "repos/apokamo/other/issues/163/reactions",
        ),
    ),
    # I6
    (
        "summary_comment_id_zero",
        _set(_valid_payload(), ("approval", "review_summary", "comment_id"), 0),
    ),
    (
        "summary_url_mismatch",
        _set(
            _valid_payload(),
            ("approval", "review_summary", "url"),
            "https://github.com/apokamo/fullstack-agent-template/pull/163#issuecomment-1",
        ),
    ),
    (
        "summary_status_in_progress",
        _set(_valid_payload(), ("approval", "review_summary", "status"), "In progress"),
    ),
    (
        "summary_short_sha_6_chars",
        _set(_valid_payload(), ("approval", "review_summary", "commit_short_sha"), "91d11b"),
    ),
    (
        "summary_short_sha_not_prefix",
        _set(_valid_payload(), ("approval", "review_summary", "commit_short_sha"), "abcdef0"),
    ),
    (
        "summary_completed_at_missing",
        _delete(_valid_payload(), ("approval", "review_summary", "completed_at")),
    ),
    (
        "summary_updated_before_completed",
        _set(
            _valid_payload(),
            ("approval", "review_summary", "comment_updated_at"),
            "2026-09-08T01:33:31Z",
        ),
    ),
    # I7
    (
        "reaction_same_second_as_completed",
        _set(_valid_payload(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:32Z"),
    ),
    (
        "reaction_before_completed",
        _set(_valid_payload(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:31Z"),
    ),
    (
        "reaction_before_head_commit",
        _set(_valid_payload(), ("reviewed_head", "committed_at"), "2026-09-08T01:40:00Z"),
    ),
    # I8
    (
        "checks_at_start_mismatch",
        _set(_valid_payload(), ("checks", "head_sha_at_start"), _OTHER_HEAD),
    ),
    (
        "checks_before_decision_mismatch",
        _set(_valid_payload(), ("checks", "head_sha_before_decision"), _OTHER_HEAD),
    ),
    (
        "checks_after_decision_mismatch",
        _set(_valid_payload(), ("checks", "head_sha_after_decision"), _OTHER_HEAD),
    ),
    (
        "short_sha_matches_two",
        _set(_valid_payload(), ("checks", "short_sha_matching_pr_commits"), 2),
    ),
    (
        "current_head_bot_reviews_one",
        _set(_valid_payload(), ("checks", "current_head_bot_reviews"), 1),
    ),
    # I9
    ("fetched_before_reaction", _set(_valid_payload(), ("fetched_at",), "2026-09-08T01:33:34Z")),
    ("fetched_after_decided", _set(_valid_payload(), ("decided_at",), "2026-09-08T01:33:40Z")),
    ("decided_at_unparsable", _set(_valid_payload(), ("decided_at",), "later")),
]


@pytest.mark.small
class TestReviewPollEvidenceModel:
    def test_valid_payload_passes(self) -> None:
        evidence = ReviewPollEvidence.model_validate(_valid_payload())
        assert evidence.reviewed_head.sha == HEAD
        assert evidence.approval.reaction.id == 123456789
        assert evidence.approval.review_summary.comment_id == 5577710898

    @pytest.mark.parametrize(
        "payload", [p for _, p in _VIOLATIONS], ids=[name for name, _ in _VIOLATIONS]
    )
    def test_invariant_violation_is_rejected(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ReviewPollEvidence.model_validate(payload)

    def test_reaction_in_next_second_after_completed_is_accepted(self) -> None:
        payload = _set(
            _valid_payload(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:33Z"
        )
        ReviewPollEvidence.model_validate(payload)

    def test_model_is_frozen(self) -> None:
        evidence = ReviewPollEvidence.model_validate(_valid_payload())
        with pytest.raises(ValidationError):
            evidence.result = "PASS"  # type: ignore[misc]


@pytest.mark.small
class TestParseTimestamp:
    def test_second_and_microsecond_forms_are_comparable(self) -> None:
        assert parse_timestamp("2026-09-08T01:33:32Z") < parse_timestamp(
            "2026-09-08T01:33:32.391219Z"
        )

    @pytest.mark.parametrize(
        "value", ["", "2026-09-08", "2026-09-08T01:33:32", "x", "2026-13-01T00:00:00Z"]
    )
    def test_invalid_values_raise(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_timestamp(value)


@pytest.mark.medium
class TestSaveEvidence:
    def test_round_trip(self, tmp_path: Path) -> None:
        evidence = ReviewPollEvidence.model_validate(_valid_payload())
        path = tmp_path / "attempt-001" / EVIDENCE_FILENAME
        save_evidence(path, evidence)
        assert json.loads(path.read_text(encoding="utf-8")) == _valid_payload()
        assert load_evidence(path) == evidence

    def test_filename_constant(self) -> None:
        assert EVIDENCE_FILENAME == "review-poll-evidence.json"
