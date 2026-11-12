"""Tests for kaji_harness.review_poll_evidence (schema v2 / v3 approval evidence)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from kaji_harness.review_poll_evidence import (
    EVIDENCE_FILENAME,
    ReviewPollEvidenceV2,
    ReviewPollEvidenceV3,
    load_evidence,
    parse_timestamp,
    save_evidence,
)

HEAD = "91d11b151e50a90ff02ffd427205f78017205bb4"


def _v1_payload() -> dict[str, Any]:
    """撤去した schema v1 の（撤去前は妥当だった）payload。拒否されることの確認にだけ使う。"""
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


def _valid_payload_v2() -> dict[str, Any]:
    payload = _v1_payload()
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


# (id, mutated payload) — 各ケースが schema v2 と共通の検査（I1〜I5・I7〜I9）の違反を 1 つ表す。
_VIOLATIONS: list[tuple[str, dict[str, Any]]] = [
    # I1
    ("kind_other", _set(_valid_payload_v2(), ("kind",), "kaji.other")),
    ("result_retry", _set(_valid_payload_v2(), ("result",), "RETRY")),
    ("provider_gitlab", _set(_valid_payload_v2(), ("provider",), "gitlab")),
    ("top_level_extra_key", _set(_valid_payload_v2(), ("unexpected",), 1)),
    ("approval_missing", _delete(_valid_payload_v2(), ("approval",))),
    ("repository_missing", _delete(_valid_payload_v2(), ("repository",))),
    ("checks_missing", _delete(_valid_payload_v2(), ("checks",))),
    ("fetched_at_missing", _delete(_valid_payload_v2(), ("fetched_at",))),
    ("section_extra_key", _set(_valid_payload_v2(), ("approval", "bot", "extra"), 1)),
    ("section_missing_key", _delete(_valid_payload_v2(), ("approval", "reaction", "api_path"))),
    ("checks_missing_key", _delete(_valid_payload_v2(), ("checks", "head_sha_at_start"))),
    # I2
    ("repository_owner_empty", _set(_valid_payload_v2(), ("repository", "owner"), "")),
    ("pull_number_zero", _set(_valid_payload_v2(), ("pull_request", "number"), 0)),
    (
        "pull_url_not_github",
        _set(_valid_payload_v2(), ("pull_request", "url"), "https://x/pull/163"),
    ),
    (
        "pull_url_number_mismatch",
        _set(_valid_payload_v2(), ("pull_request", "url"), "https://github.com/a/b/pull/164"),
    ),
    # I3
    ("sha_39_chars", _set(_valid_payload_v2(), ("reviewed_head", "sha"), HEAD[:39])),
    ("sha_uppercase", _set(_valid_payload_v2(), ("reviewed_head", "sha"), HEAD.upper())),
    ("sha_non_hex", _set(_valid_payload_v2(), ("reviewed_head", "sha"), "g" * 40)),
    ("committed_at_unparsable", _set(_valid_payload_v2(), ("reviewed_head", "committed_at"), "x")),
    (
        "committed_at_invalid_date",
        _set(_valid_payload_v2(), ("reviewed_head", "committed_at"), "2026-13-45T00:00:00Z"),
    ),
    # I4
    ("bot_id_mismatch", _set(_valid_payload_v2(), ("approval", "bot", "id"), 1)),
    ("bot_login_mismatch", _set(_valid_payload_v2(), ("approval", "bot", "login"), "someone")),
    # I5
    ("reaction_id_zero", _set(_valid_payload_v2(), ("approval", "reaction", "id"), 0)),
    ("reaction_id_string", _set(_valid_payload_v2(), ("approval", "reaction", "id"), "123")),
    (
        "reaction_content_heart",
        _set(_valid_payload_v2(), ("approval", "reaction", "content"), "heart"),
    ),
    (
        "reaction_created_at_missing",
        _delete(_valid_payload_v2(), ("approval", "reaction", "created_at")),
    ),
    (
        "reaction_created_at_unparsable",
        _set(_valid_payload_v2(), ("approval", "reaction", "created_at"), "soon"),
    ),
    (
        "reaction_api_path_mismatch",
        _set(
            _valid_payload_v2(),
            ("approval", "reaction", "api_path"),
            "repos/apokamo/other/issues/163/reactions",
        ),
    ),
    # I7
    (
        "reaction_before_head_commit",
        _set(_valid_payload_v2(), ("reviewed_head", "committed_at"), "2026-09-08T01:40:00Z"),
    ),
    # I8
    (
        "checks_at_start_mismatch",
        _set(_valid_payload_v2(), ("checks", "head_sha_at_start"), _OTHER_HEAD),
    ),
    (
        "checks_before_decision_mismatch",
        _set(_valid_payload_v2(), ("checks", "head_sha_before_decision"), _OTHER_HEAD),
    ),
    (
        "checks_after_decision_mismatch",
        _set(_valid_payload_v2(), ("checks", "head_sha_after_decision"), _OTHER_HEAD),
    ),
    (
        "current_head_bot_reviews_one",
        _set(_valid_payload_v2(), ("checks", "current_head_bot_reviews"), 1),
    ),
    # I9
    ("fetched_before_reaction", _set(_valid_payload_v2(), ("fetched_at",), "2026-09-08T01:33:34Z")),
    ("fetched_after_decided", _set(_valid_payload_v2(), ("decided_at",), "2026-09-08T01:33:40Z")),
    ("decided_at_unparsable", _set(_valid_payload_v2(), ("decided_at",), "later")),
]


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
    # I2〜I5
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
    (
        # Issue #557: v2 は security の completed/null を受け入れない（null 経路は v3 だけ）
        "v2_security_null_on_head",
        _set(
            _valid_payload_v2(),
            _SECURITY,
            {"commit_sha": HEAD, "status": "completed", "has_any_findings": None},
        ),
    ),
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


# --- schema v3（Issue #557: security_review の completed/null 経路） -----------------

_NULL_CHECKS = ("checks", "security_null_checks")
_ZERO_NULL_CHECKS: dict[str, Any] = {
    "unresolved_head_bot_threads": 0,
    "bot_non_summary_issue_comments": 0,
}
_SECURITY_FALSE_ON_HEAD: dict[str, Any] = {
    "commit_sha": HEAD,
    "status": "completed",
    "has_any_findings": False,
}
_SECURITY_NULL_ON_HEAD: dict[str, Any] = {
    "commit_sha": HEAD,
    "status": "completed",
    "has_any_findings": None,
}


def _to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """v2 の payload を v3 の形（版 3・``checks.security_null_checks: null``）へ写す。"""
    out = _set(payload, ("schema_version",), 3)
    if isinstance(out.get("checks"), dict):
        out["checks"]["security_null_checks"] = None
    return out


def _valid_payload_v3() -> dict[str, Any]:
    """false 経路（security の観測なし）の v3 証跡。"""
    return _to_v3(_valid_payload_v2())


def _valid_payload_v3_null() -> dict[str, Any]:
    """null 経路（security が head の completed/null、追加確認がともに 0 件）の v3 証跡。"""
    payload = _set(_valid_payload_v3(), _SECURITY, _SECURITY_NULL_ON_HEAD)
    return _set(payload, _NULL_CHECKS, _ZERO_NULL_CHECKS)


def _null_checks(threads: Any, comments: Any) -> dict[str, Any]:
    return {"unresolved_head_bot_threads": threads, "bot_non_summary_issue_comments": comments}


# schema v3 で受理される (security_review, checks.security_null_checks) の組み合わせ（I6(v3)）。
_VALID_SECURITY_V3: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = [
    *[(f"{name}", security, None) for name, security in _VALID_SECURITY_V2],
    ("head_completed_null_checked", _SECURITY_NULL_ON_HEAD, _ZERO_NULL_CHECKS),
]


def _v3_with(security: dict[str, Any], checks: Any) -> dict[str, Any]:
    return _set(_set(_valid_payload_v3(), _SECURITY, security), _NULL_CHECKS, checks)


# (id, mutated payload) — 各ケースが schema v3 の不変条件違反を 1 つ表す。
_VIOLATIONS_V3: list[tuple[str, dict[str, Any]]] = [
    # I1(v3): checks のキー集合と security_null_checks の形
    ("v3_null_checks_key_missing", _delete(_valid_payload_v3(), _NULL_CHECKS)),
    ("v3_checks_extra_key", _set(_valid_payload_v3(), ("checks", "extra"), 1)),
    (
        "v3_null_checks_missing_count",
        _set(_valid_payload_v3_null(), _NULL_CHECKS, {"unresolved_head_bot_threads": 0}),
    ),
    (
        "v3_null_checks_extra_count",
        _set(_valid_payload_v3_null(), _NULL_CHECKS, {**_ZERO_NULL_CHECKS, "other": 0}),
    ),
    ("v3_null_checks_not_object", _set(_valid_payload_v3_null(), _NULL_CHECKS, 0)),
    (
        "v3_null_checks_bool_count",
        _set(_valid_payload_v3_null(), _NULL_CHECKS, _null_checks(False, 0)),
    ),
    (
        "v3_null_checks_str_count",
        _set(_valid_payload_v3_null(), _NULL_CHECKS, _null_checks(0, "0")),
    ),
    ("v3_null_checks_negative", _set(_valid_payload_v3_null(), _NULL_CHECKS, _null_checks(-1, 0))),
    (
        "v3_with_short_sha_check",
        _set(_valid_payload_v3(), ("checks", "short_sha_matching_pr_commits"), 1),
    ),
    # I6(v3): null 経路は追加確認 0 件が必須
    ("v3_null_without_checks", _set(_valid_payload_v3_null(), _NULL_CHECKS, None)),
    ("v3_null_unresolved_thread", _set(_valid_payload_v3_null(), _NULL_CHECKS, _null_checks(1, 0))),
    ("v3_null_bot_comment", _set(_valid_payload_v3_null(), _NULL_CHECKS, _null_checks(0, 1))),
    # I6(v3): null 経路以外に追加確認の結果を持たせない
    ("v3_false_with_checks", _v3_with(_SECURITY_FALSE_ON_HEAD, _ZERO_NULL_CHECKS)),
    ("v3_no_security_with_checks", _set(_valid_payload_v3(), _NULL_CHECKS, _ZERO_NULL_CHECKS)),
    # I6(v3): head 以外・SHA なしの completed/null は受け入れない
    (
        "v3_null_other_head",
        _v3_with({**_SECURITY_NULL_ON_HEAD, "commit_sha": _OTHER_HEAD}, None),
    ),
    (
        "v3_null_other_head_checked",
        _v3_with({**_SECURITY_NULL_ON_HEAD, "commit_sha": _OTHER_HEAD}, _ZERO_NULL_CHECKS),
    ),
    (
        "v3_null_sha_missing",
        _v3_with({**_SECURITY_NULL_ON_HEAD, "commit_sha": None}, _ZERO_NULL_CHECKS),
    ),
    (
        "v3_security_findings_on_head",
        _v3_with({**_SECURITY_FALSE_ON_HEAD, "has_any_findings": True}, None),
    ),
    (
        "v3_security_pending_on_head",
        _v3_with({**_SECURITY_NULL_ON_HEAD, "status": "in_progress"}, _ZERO_NULL_CHECKS),
    ),
    # I6(v3) の code 条件は v2 と同じ
    ("v3_code_findings_null", _set(_valid_payload_v3_null(), (*_CODE, "has_any_findings"), None)),
    ("v3_code_other_head", _set(_valid_payload_v3_null(), (*_CODE, "commit_sha"), _OTHER_HEAD)),
    # I7(v3)
    (
        "v3_reaction_same_second_as_update",
        _set(
            _valid_payload_v3_null(), ("approval", "reaction", "created_at"), "2026-09-08T01:33:33Z"
        ),
    ),
    # I8(v3)
    (
        "v3_current_head_bot_reviews_one",
        _set(_valid_payload_v3_null(), ("checks", "current_head_bot_reviews"), 1),
    ),
    # 版の取り違え: v2 の形に版 3、v3 の形に版 2
    ("v3_v2_shape_with_version_3", _set(_valid_payload_v2(), ("schema_version",), 3)),
    ("v3_v3_shape_with_version_2", _set(_valid_payload_v3_null(), ("schema_version",), 2)),
]

# v2 と共通の検査（I1〜I5・I7〜I9）の違反を v3 の形へ写したもの。
_VIOLATIONS_V3_COMMON: list[tuple[str, dict[str, Any]]] = [
    (f"v3_{name}", _to_v3(payload)) for name, payload in _VIOLATIONS
]


@pytest.mark.small
class TestReviewPollEvidenceModel:
    @pytest.mark.parametrize(
        "payload", [p for _, p in _VIOLATIONS], ids=[name for name, _ in _VIOLATIONS]
    )
    def test_invariant_violation_is_rejected(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ReviewPollEvidenceV2.model_validate(payload)

    def test_model_is_frozen(self) -> None:
        evidence = ReviewPollEvidenceV2.model_validate(_valid_payload_v2())
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
    @pytest.mark.parametrize(
        "payload", [_valid_payload_v3(), _valid_payload_v3_null()], ids=["false_path", "null_path"]
    )
    def test_round_trip(self, tmp_path: Path, payload: dict[str, Any]) -> None:
        evidence = ReviewPollEvidenceV3.model_validate(payload)
        path = tmp_path / "attempt-001" / EVIDENCE_FILENAME
        save_evidence(path, evidence)
        assert json.loads(path.read_text(encoding="utf-8")) == payload
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
    def test_v2_is_loaded(self, tmp_path: Path) -> None:
        # upgrade 前に作られた attempt の v2 証跡も読める（consumer は v2 と v3 を受け付ける）
        v2_path = tmp_path / "v2.json"
        v2_path.write_text(json.dumps(_valid_payload_v2()), encoding="utf-8")
        loaded = load_evidence(v2_path)
        assert isinstance(loaded, ReviewPollEvidenceV2)
        assert loaded == ReviewPollEvidenceV2.model_validate(_valid_payload_v2())

    @pytest.mark.parametrize(
        "payload", [_valid_payload_v3(), _valid_payload_v3_null()], ids=["false_path", "null_path"]
    )
    def test_v3_is_loaded(self, tmp_path: Path, payload: dict[str, Any]) -> None:
        path = tmp_path / "v3.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert isinstance(load_evidence(path), ReviewPollEvidenceV3)

    def test_v2_with_security_null_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_valid_payload_v2(), _SECURITY, _SECURITY_NULL_ON_HEAD)))
        with pytest.raises(ValidationError):
            load_evidence(path)

    def test_broken_json_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.json"
        path.write_text('{"schema_version": 3,', encoding="utf-8")
        with pytest.raises(ValidationError):
            load_evidence(path)

    def test_removed_v1_is_rejected(self, tmp_path: Path) -> None:
        # schema v1 は Issue #478 で撤去した。撤去前に妥当だった v1 の証跡も読めない
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_v1_payload()))
        with pytest.raises(ValidationError):
            load_evidence(path)

    @pytest.mark.parametrize("version", [4, 1, 0, "2", "3", True, 3.0, None])
    def test_unknown_version_is_rejected(self, tmp_path: Path, version: object) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_valid_payload_v3(), ("schema_version",), version)))
        with pytest.raises(ValidationError):
            load_evidence(path)

    def test_v2_shape_with_version_3_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_valid_payload_v2(), ("schema_version",), 3)))
        with pytest.raises(ValidationError):
            load_evidence(path)

    def test_v1_shape_with_version_2_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(_set(_v1_payload(), ("schema_version",), 2)))
        with pytest.raises(ValidationError):
            load_evidence(path)


@pytest.mark.small
class TestReviewPollEvidenceV3Model:
    @pytest.mark.parametrize(
        "payload",
        [_valid_payload_v3(), _valid_payload_v3_null()],
        ids=["false_path", "null_path"],
    )
    def test_valid_payload_passes(self, payload: dict[str, Any]) -> None:
        evidence = ReviewPollEvidenceV3.model_validate(payload)
        assert evidence.schema_version == 3

    def test_null_path_records_zero_counts(self) -> None:
        evidence = ReviewPollEvidenceV3.model_validate(_valid_payload_v3_null())
        checks = evidence.checks.security_null_checks
        assert checks is not None
        assert (checks.unresolved_head_bot_threads, checks.bot_non_summary_issue_comments) == (0, 0)

    @pytest.mark.parametrize(
        ("security", "checks"),
        [(s, c) for _, s, c in _VALID_SECURITY_V3],
        ids=[n for n, _, _ in _VALID_SECURITY_V3],
    )
    def test_allowed_security_review_states(
        self, security: dict[str, Any], checks: dict[str, Any] | None
    ) -> None:
        ReviewPollEvidenceV3.model_validate(_v3_with(security, checks))

    @pytest.mark.parametrize(
        "payload",
        [p for _, p in [*_VIOLATIONS_V3, *_VIOLATIONS_V3_COMMON]],
        ids=[n for n, _ in [*_VIOLATIONS_V3, *_VIOLATIONS_V3_COMMON]],
    )
    def test_invariant_violation_is_rejected(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ReviewPollEvidenceV3.model_validate(payload)

    def test_v2_model_rejects_v3_payload(self) -> None:
        with pytest.raises(ValidationError):
            ReviewPollEvidenceV2.model_validate(_valid_payload_v3())
