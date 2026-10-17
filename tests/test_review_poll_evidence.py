"""Tests for kaji_harness.review_poll_evidence (schema v1 / v2 approval evidence)."""

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
    ReviewPollEvidenceV2,
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


def _valid_payload_v2() -> dict[str, Any]:
    payload = _valid_payload()
    payload["schema_version"] = 2
    payload["approval"] = {
        "bot": payload["approval"]["bot"],
        "reaction": payload["approval"]["reaction"],
        "review_state": {
            "comment_id": 5577710898,
            "url": (
                "https://github.com/apokamo/fullstack-agent-template/pull/163"
                "#issuecomment-5577710898"
            ),
            "comment_updated_at": "2026-09-08T01:33:33Z",
            "state_version": "v1",
            "code_review": {"commit_sha": HEAD, "status": "completed", "has_any_findings": False},
            "security_review": {"commit_sha": None, "status": None, "has_any_findings": None},
        },
    }
    del payload["checks"]["short_sha_matching_pr_commits"]
    return payload


_STATE = ("approval", "review_state")
_CODE = (*_STATE, "code_review")
_SECURITY = (*_STATE, "security_review")

# (id, mutated payload) — 各ケースが schema v2 の不変条件違反を 1 つ表す。
_VIOLATIONS_V2: list[tuple[str, dict[str, Any]]] = [
    # I1(v2)
    ("v2_schema_version_3", _set(_valid_payload_v2(), ("schema_version",), 3)),
    ("v2_top_level_extra_key", _set(_valid_payload_v2(), ("unexpected",), 1)),
    (
        "v2_with_review_summary",
        _set(_valid_payload_v2(), ("approval", "review_summary"), {"comment_id": 1}),
    ),
    (
        "v2_with_short_sha_check",
        _set(_valid_payload_v2(), ("checks", "short_sha_matching_pr_commits"), 1),
    ),
    ("v2_state_extra_key", _set(_valid_payload_v2(), (*_STATE, "extra"), 1)),
    ("v2_entry_missing_key", _delete(_valid_payload_v2(), (*_CODE, "status"))),
    ("v2_entry_extra_key", _set(_valid_payload_v2(), (*_SECURITY, "extra"), 1)),
    ("v2_review_state_missing", _delete(_valid_payload_v2(), _STATE)),
    # I2〜I5（v1 と共通）
    ("v2_pull_number_zero", _set(_valid_payload_v2(), ("pull_request", "number"), 0)),
    ("v2_sha_uppercase", _set(_valid_payload_v2(), ("reviewed_head", "sha"), HEAD.upper())),
    ("v2_bot_id_mismatch", _set(_valid_payload_v2(), ("approval", "bot", "id"), 1)),
    (
        "v2_reaction_api_path_mismatch",
        _set(_valid_payload_v2(), ("approval", "reaction", "api_path"), "repos/a/b/issues/1/x"),
    ),
    # I6(v2)
    ("v2_comment_id_zero", _set(_valid_payload_v2(), (*_STATE, "comment_id"), 0)),
    (
        "v2_url_mismatch",
        _set(_valid_payload_v2(), (*_STATE, "url"), "https://github.com/a/b/pull/163#x-1"),
    ),
    ("v2_state_version_v2", _set(_valid_payload_v2(), (*_STATE, "state_version"), "v2")),
    ("v2_updated_at_unparsable", _set(_valid_payload_v2(), (*_STATE, "comment_updated_at"), "x")),
    ("v2_code_other_head", _set(_valid_payload_v2(), (*_CODE, "commit_sha"), _OTHER_HEAD)),
    ("v2_code_sha_null", _set(_valid_payload_v2(), (*_CODE, "commit_sha"), None)),
    ("v2_code_not_completed", _set(_valid_payload_v2(), (*_CODE, "status"), "in_progress")),
    ("v2_code_findings", _set(_valid_payload_v2(), (*_CODE, "has_any_findings"), True)),
    ("v2_code_findings_null", _set(_valid_payload_v2(), (*_CODE, "has_any_findings"), None)),
    ("v2_code_findings_zero", _set(_valid_payload_v2(), (*_CODE, "has_any_findings"), 0)),
    (
        "v2_security_pending_on_head",
        _set(
            _set(_valid_payload_v2(), (*_SECURITY, "status"), "in_progress"),
            (*_SECURITY, "commit_sha"),
            HEAD,
        ),
    ),
    (
        "v2_security_pending_without_sha",
        _set(_valid_payload_v2(), (*_SECURITY, "status"), "in_progress"),
    ),
    (
        "v2_security_findings_on_head",
        _set(
            _set(
                _set(_valid_payload_v2(), (*_SECURITY, "status"), "completed"),
                (*_SECURITY, "commit_sha"),
                HEAD,
            ),
            (*_SECURITY, "has_any_findings"),
            True,
        ),
    ),
    ("v2_security_sha_not_hex", _set(_valid_payload_v2(), (*_SECURITY, "commit_sha"), "g" * 40)),
    ("v2_security_status_empty", _set(_valid_payload_v2(), (*_SECURITY, "status"), "")),
    # I7(v2)
    (
        "v2_reaction_same_second_as_update",
        _set(_valid_payload_v2(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:33Z"),
    ),
    (
        "v2_update_after_reaction",
        _set(_valid_payload_v2(), (*_STATE, "comment_updated_at"), "2026-09-08T01:33:35Z"),
    ),
    (
        "v2_reaction_before_head_commit",
        _set(_valid_payload_v2(), ("reviewed_head", "committed_at"), "2026-09-08T01:40:00Z"),
    ),
    # I8(v2)
    (
        "v2_checks_after_decision_mismatch",
        _set(_valid_payload_v2(), ("checks", "head_sha_after_decision"), _OTHER_HEAD),
    ),
    (
        "v2_current_head_bot_reviews_one",
        _set(_valid_payload_v2(), ("checks", "current_head_bot_reviews"), 1),
    ),
    # I9
    (
        "v2_fetched_before_reaction",
        _set(_valid_payload_v2(), ("fetched_at",), "2026-09-08T01:33:34Z"),
    ),
]

# schema v2 で受理される security_review の組み合わせ（I6(v2)）。
_VALID_SECURITY_V2: list[tuple[str, dict[str, Any]]] = [
    ("null", {"commit_sha": None, "status": None, "has_any_findings": None}),
    (
        "other_head_with_findings",
        {"commit_sha": _OTHER_HEAD, "status": "completed", "has_any_findings": True},
    ),
    (
        "other_head_pending",
        {"commit_sha": _OTHER_HEAD, "status": "in_progress", "has_any_findings": None},
    ),
    (
        "head_completed_without_findings",
        {"commit_sha": HEAD, "status": "completed", "has_any_findings": False},
    ),
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


@pytest.mark.small
class TestReviewPollEvidenceV2Model:
    def test_valid_payload_passes(self) -> None:
        evidence = ReviewPollEvidenceV2.model_validate(_valid_payload_v2())
        assert evidence.approval.review_state.comment_id == 5577710898

    @pytest.mark.parametrize(
        "payload", [p for _, p in _VIOLATIONS_V2], ids=[name for name, _ in _VIOLATIONS_V2]
    )
    def test_invariant_violation_is_rejected(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ReviewPollEvidenceV2.model_validate(payload)

    @pytest.mark.parametrize(
        "security", [s for _, s in _VALID_SECURITY_V2], ids=[n for n, _ in _VALID_SECURITY_V2]
    )
    def test_allowed_security_review_states(self, security: dict[str, Any]) -> None:
        ReviewPollEvidenceV2.model_validate(_set(_valid_payload_v2(), _SECURITY, security))

    def test_reaction_in_next_second_after_update_is_accepted(self) -> None:
        payload = _set(
            _valid_payload_v2(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:34Z"
        )
        payload = _set(payload, ("fetched_at",), "2026-09-08T01:33:34Z")
        ReviewPollEvidenceV2.model_validate(payload)


@pytest.mark.medium
class TestLoadEvidenceVersions:
    def test_v1_and_v2_are_dispatched_by_schema_version(self, tmp_path: Path) -> None:
        v1_path = tmp_path / "v1.json"
        v2_path = tmp_path / "v2.json"
        save_evidence(v1_path, ReviewPollEvidence.model_validate(_valid_payload()))
        save_evidence(v2_path, ReviewPollEvidenceV2.model_validate(_valid_payload_v2()))
        assert isinstance(load_evidence(v1_path), ReviewPollEvidence)
        assert isinstance(load_evidence(v2_path), ReviewPollEvidenceV2)
        assert json.loads(v2_path.read_text(encoding="utf-8")) == _valid_payload_v2()

    @pytest.mark.parametrize("version", [3, 0, "2"])
    def test_unknown_version_is_rejected(self, tmp_path: Path, version: object) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_valid_payload_v2(), ("schema_version",), version)))
        with pytest.raises(ValidationError):
            load_evidence(path)

    def test_v1_shape_with_version_2_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_valid_payload(), ("schema_version",), 2)))
        with pytest.raises(ValidationError):
            load_evidence(path)
