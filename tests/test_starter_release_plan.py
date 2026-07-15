"""Deterministic starter release planning tests."""

from __future__ import annotations

import pytest

from kaji_harness.starter_release import (
    ReleasePlanInput,
    ReleaseReviewContext,
    ReleaseReviewEvidence,
    build_release_plan,
    is_na_candidate,
    is_review_current,
)

pytestmark = pytest.mark.small


def _input(**overrides: object) -> ReleasePlanInput:
    values: dict[str, object] = {
        "target_kaji_release": "v0.16.0",
        "candidate_sha": "bbb",
        "tags": [],
        "releases": [],
        "state_table_row_exists": True,
        "state_table_status": "PENDING",
        "tracking_issue_state": "open",
    }
    values.update(overrides)
    return ReleasePlanInput.model_validate(values)


def test_no_tag_plans_initial_release() -> None:
    plan = build_release_plan(_input())

    assert plan.route == 1
    assert plan.tag == "kaji-v0.16.0"
    assert plan.requires_push is True


def test_latest_candidate_reuses_tag_and_resumes_missing_work() -> None:
    plan = build_release_plan(
        _input(
            tags=[{"name": "kaji-v0.16.0", "sha": "bbb", "annotated": True}],
        )
    )

    assert plan.route == 2
    assert plan.tag == "kaji-v0.16.0"
    assert plan.requires_push is False
    assert plan.remaining_actions == [
        "create_release",
        "update_state_table",
        "close_tracking_issue",
    ]


def test_existing_release_resumes_state_table_and_close_only() -> None:
    plan = build_release_plan(
        _input(
            tags=[{"name": "kaji-v0.16.0", "sha": "bbb", "annotated": True}],
            releases=["kaji-v0.16.0"],
        )
    )

    assert plan.remaining_actions == ["update_state_table", "close_tracking_issue"]


def test_passed_state_table_resumes_close_only() -> None:
    plan = build_release_plan(
        _input(
            tags=[{"name": "kaji-v0.16.0", "sha": "bbb", "annotated": True}],
            releases=["kaji-v0.16.0"],
            state_table_status="PASS",
        )
    )

    assert plan.remaining_actions == ["close_tracking_issue"]


def test_completed_release_is_idempotent() -> None:
    plan = build_release_plan(
        _input(
            tags=[{"name": "kaji-v0.16.0", "sha": "bbb", "annotated": True}],
            releases=["kaji-v0.16.0"],
            state_table_status="PASS",
            tracking_issue_state="closed",
        )
    )

    assert plan.route == 2
    assert plan.remaining_actions == []


def test_changed_candidate_uses_max_revision_plus_one() -> None:
    plan = build_release_plan(
        _input(
            tags=[
                {"name": "kaji-v0.16.0", "sha": "aaa", "annotated": True},
                {"name": "kaji-v0.16.0-r2", "sha": "ccc", "annotated": True},
                {"name": "kaji-v0.16.0-r7", "sha": "ddd", "annotated": True},
            ]
        )
    )

    assert plan.route == 3
    assert plan.tag == "kaji-v0.16.0-r8"


def test_old_revision_candidate_aborts() -> None:
    plan = build_release_plan(
        _input(
            tags=[
                {"name": "kaji-v0.16.0", "sha": "bbb", "annotated": True},
                {"name": "kaji-v0.16.0-r1", "sha": "ccc", "annotated": True},
            ]
        )
    )

    assert plan.route == 4
    assert plan.decision == "ABORT"


@pytest.mark.parametrize(
    "overrides",
    [
        {"state_table_row_exists": False},
        {"releases": ["kaji-v0.16.0"]},
        {"tags": [{"name": "kaji-v0.16.0", "sha": "bbb", "annotated": False}]},
    ],
)
def test_observation_contradictions_abort(overrides: dict[str, object]) -> None:
    plan = build_release_plan(_input(**overrides))

    assert plan.route == 5
    assert plan.decision == "ABORT"
    assert plan.reason


def test_na_candidate_uses_sha_equality() -> None:
    assert is_na_candidate("abc", "abc") is True
    assert is_na_candidate("abc", "def") is False


@pytest.mark.parametrize(
    "change",
    [
        {"candidate": "changed"},
        {"target": "v0.15.0"},
        {"base": "changed"},
        {"status": "RETRY"},
    ],
)
def test_review_freshness_rejects_each_independent_mismatch(change: dict[str, str]) -> None:
    evidence_values = {
        "status": "PASS",
        "target": "v0.16.0",
        "base": "aaa",
        "candidate": "bbb",
    }
    evidence_values.update(change)
    evidence = ReleaseReviewEvidence(**evidence_values)
    context = ReleaseReviewContext(
        target="v0.16.0",
        local_head="bbb",
        remote_main="aaa",
        published_candidate=False,
    )

    assert is_review_current(evidence, context) is False


def test_published_review_freshness_compares_remote_to_candidate() -> None:
    evidence = ReleaseReviewEvidence(status="PASS", target="v0.16.0", base="aaa", candidate="bbb")
    context = ReleaseReviewContext(
        target="v0.16.0",
        local_head="bbb",
        remote_main="bbb",
        published_candidate=True,
    )

    assert is_review_current(evidence, context) is True


def test_published_review_freshness_rejects_remote_candidate_mismatch() -> None:
    evidence = ReleaseReviewEvidence(status="PASS", target="v0.16.0", base="aaa", candidate="bbb")
    context = ReleaseReviewContext(
        target="v0.16.0",
        local_head="bbb",
        remote_main="changed",
        published_candidate=True,
    )

    assert is_review_current(evidence, context) is False
