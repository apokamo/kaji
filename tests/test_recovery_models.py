"""Small tests: recovery data models / constants / pure derivations (Issue #288).

``FailureClassification`` / ``RecoveryDecision`` の直列化と値域、``schema_version``、
recovery budget / wait の固定値、child exit code → ``child_final_status`` mapping、
newer-run 検出の run_id 辞書順比較を検証する。ファイル I/O を伴う永続化は
``test_recovery_artifacts.py``（Medium）が担う。
"""

from __future__ import annotations

import pytest

from kaji_harness.recovery.models import (
    FAILURE_CAUSES,
    NON_RESUMABLE_STEPS,
    RECOVERY_BUDGET,
    RECOVERY_DECISIONS,
    RECOVERY_SCHEMA_VERSION,
    RECOVERY_WAIT_SECONDS,
    FailureClassification,
    RecoveryDecision,
    derive_child_final_status,
    recovery_budget_consumed,
    select_newer_run_ids,
)

pytestmark = pytest.mark.small


def _classification() -> FailureClassification:
    return FailureClassification(
        cause="verdict_resolution_failure",
        synthetic=True,
        source="agent",
        recoverability_hint="candidate",
    )


def test_module_constants_are_fixed() -> None:
    assert RECOVERY_SCHEMA_VERSION == 1
    assert RECOVERY_BUDGET == 1
    assert RECOVERY_WAIT_SECONDS == 600
    assert NON_RESUMABLE_STEPS == frozenset({"issue-start", "i-pr", "issue-close"})


def _decision(**overrides: object) -> RecoveryDecision:
    base: dict[str, object] = {
        "run_id": "260710120000",
        "recoverable": False,
        "decision": "comment_only",
        "classification": _classification(),
        "failed_step": "review-code",
    }
    base.update(overrides)
    return RecoveryDecision(**base)  # type: ignore[arg-type]


def test_recovery_budget_not_consumed_by_non_resuming_decision() -> None:
    assert recovery_budget_consumed(_decision(decision="comment_only")) is False
    assert recovery_budget_consumed(_decision(decision="not_resumable")) is False


@pytest.mark.parametrize(
    "overrides",
    [
        # child 起動を確約した時点で budget は消費済み（ウェイト中の強制終了に fail-closed）。
        {"decision": "resume"},
        {"auto_recovery_attempted": True},
        {"auto_recovery_attempt_no": RECOVERY_BUDGET},
        {"recovery_child_run_id": "260710121500"},
    ],
)
def test_recovery_budget_consumed_markers(overrides: dict[str, object]) -> None:
    assert recovery_budget_consumed(_decision(**overrides)) is True


def test_failure_cause_domain() -> None:
    assert FAILURE_CAUSES == frozenset(
        {
            "dispatch_failure",
            "verdict_resolution_failure",
            "cycle_exhausted",
            "agent_declared_abort",
            "ambiguous_worktree_abort",
            "config_or_definition_error",
            "kaji_bug_suspected",
            "runtime_error",
            "unknown_external_error",
            "external_upstream_anomaly",
        }
    )


def test_recovery_decision_domain() -> None:
    assert RECOVERY_DECISIONS == frozenset(
        {
            "resume",
            "not_resumable",
            "exhausted",
            "comment_only",
            "bug_issue_created",
            "cancelled_newer_run_detected",
            "cancelled_interrupted",
        }
    )


def test_classification_round_trip() -> None:
    c = _classification()
    assert c.to_dict() == {
        "cause": "verdict_resolution_failure",
        "synthetic": True,
        "source": "agent",
        "recoverability_hint": "candidate",
    }
    assert FailureClassification.from_dict(c.to_dict()) == c


def test_decision_round_trip_preserves_schema_version_and_fields() -> None:
    decision = RecoveryDecision(
        run_id="260710120000",
        recoverable=True,
        decision="resume",
        classification=_classification(),
        failed_step="review-code",
        resume_from="review-code",
        resume_mode="from",
        resume_command="kaji run .kaji/wf/dev.yaml 288 --from review-code",
        reason="VerdictNotFound after successful dispatch",
        evidence=["run.log workflow_end status=ERROR"],
        recovery_root_run_id="260710120000",
        resume_scheduled_at="2026-07-10T12:10:00+00:00",
        workflow_path=".kaji/wf/dev.yaml",
    )
    data = decision.to_dict()
    assert data["schema_version"] == RECOVERY_SCHEMA_VERSION
    assert data["classification"]["cause"] == "verdict_resolution_failure"
    assert data["auto_recovery_attempted"] is False
    assert data["auto_recovery_attempt_no"] == 0
    assert data["recovery_child_run_id"] is None
    assert data["triage_comment_ref"] is None
    assert data["bug_issue"] is None
    assert data["discarded_resume_session"] is False
    assert RecoveryDecision.from_dict(data) == decision


def test_decision_rejects_unknown_decision_value() -> None:
    with pytest.raises(ValueError, match="unknown recovery decision"):
        RecoveryDecision(
            run_id="1",
            recoverable=False,
            decision="teleport",  # type: ignore[arg-type]
            classification=_classification(),
            failed_step=None,
        )


def test_decision_rejects_unknown_cause() -> None:
    with pytest.raises(ValueError, match="unknown failure cause"):
        FailureClassification(
            cause="cosmic_ray",  # type: ignore[arg-type]
            synthetic=False,
            source="runner",
            recoverability_hint="no",
        )


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (0, "COMPLETE"),
        (1, "ABORT"),
        (2, "DEFINITION_ERROR"),
        (3, "ERROR"),
        (143, "ERROR"),
        (-9, "ERROR"),
        (None, "ERROR"),
    ],
)
def test_derive_child_final_status(exit_code: int | None, expected: str) -> None:
    assert derive_child_final_status(exit_code) == expected


def test_select_newer_run_ids_uses_lexicographic_order() -> None:
    run_ids = [
        "260710115959",
        "260710120000",
        "260710120000-002",
        "260710120000-010",
        "260710120001",
    ]
    assert select_newer_run_ids(run_ids, "260710120000") == [
        "260710120000-002",
        "260710120000-010",
        "260710120001",
    ]


def test_select_newer_run_ids_excludes_self_and_older() -> None:
    assert select_newer_run_ids(["260710120000"], "260710120000") == []
    assert select_newer_run_ids(["250101000000"], "260710120000") == []


def test_select_newer_run_ids_suffix_beats_bare_base() -> None:
    # ``-002`` suffix は同一秒内の後続 run。辞書順で bare base より新しい。
    assert select_newer_run_ids(["260710120000-002"], "260710120000") == ["260710120000-002"]
    assert select_newer_run_ids(["260710120000"], "260710120000-002") == []
