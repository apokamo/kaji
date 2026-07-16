"""Small tests: recovery decision の純ロジック（``plan_recovery``）(Issue #288).

budget guard / safety gate / 再開点決定 / resume command 構築 /
``resume_scheduled_at = 決定時刻 + 600s`` を、fs・provider・subprocess を触らずに検証する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaji_harness.models import Step, Workflow
from kaji_harness.recovery.classify import classify_failure
from kaji_harness.recovery.handler import plan_recovery
from kaji_harness.recovery.snapshot import FailureEvent, FailureSnapshot, GitStateSummary

pytestmark = pytest.mark.small

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_WORKFLOW_PATH = Path(".kaji/wf/dev.yaml")


def _workflow() -> Workflow:
    # step ID は実 built-in workflow と同じく skill 名と一致させない（Issue #349:
    # 旧 fixture は id == skill という架空の step を使っており、実 workflow 構造から
    # 乖離していた。`tests/test_recovery_models.py:45` 相当の値は保ちつつ、id/skill の
    # 対応は `.kaji/wf/dev.yaml` 等の tracked built-in workflow に合わせる）。
    return Workflow(
        name="dev",
        description="",
        execution_policy="auto",
        steps=[
            Step(id="start", skill="issue-start", agent="claude", on={"PASS": "implement"}),
            Step(
                id="implement", skill="issue-implement", agent="claude", on={"PASS": "review-code"}
            ),
            Step(
                id="review-code",
                skill="issue-review-code",
                agent="codex",
                on={"PASS": "verify-code"},
            ),
            Step(
                id="verify-code",
                skill="issue-verify-code",
                agent="codex",
                resume="review-code",
                on={"PASS": "pr"},
            ),
            Step(id="pr", skill="i-pr", agent="claude", resume="implement", on={"PASS": "close"}),
            Step(id="close", skill="issue-close", agent="claude", on={"PASS": "end"}),
            # 標準と異なる step ID から再開禁止 skill を参照する alias（一般則の検証用）。
            Step(id="publish", skill="i-pr", agent="claude", on={"PASS": "end"}),
            # 安全な skill が再開禁止 skill 側へ resume する組み合わせ（`resume:` 判定対象の検証用）。
            Step(id="fix-pr-meta", skill="issue-fix-code", resume="pr", agent="claude"),
            # exec-step: skill=None が gate に誤ヒットしないことの境界確認用。
            Step(id="baseline", exec=["true"], on={"PASS": "end"}),
        ],
    )


def _snapshot(**overrides: object) -> FailureSnapshot:
    base: dict[str, object] = {
        "run_id": "260710120000",
        "run_dir": Path("/repo/.kaji-artifacts/288/runs/260710120000"),
        "run_log_schema_version": 1,
        "workflow_end_status": "ERROR",
        "failure_event": FailureEvent(
            kind="verdict_exception", step_id="review-code", exception_type="VerdictNotFound"
        ),
        "failed_step": "review-code",
        "attempt_error": "VerdictNotFound: no verdict block",
        "attempt_result_present": True,
        "state_loaded": True,
        "state_worktree_dir": "/repo",
        "state_branch_name": "feat/288",
        "git": GitStateSummary(branch="feat/288", available=True, changed_files=2),
        "provider_available": True,
        "evidence": ("run.log: workflow_end status=ERROR",),
    }
    base.update(overrides)
    return FailureSnapshot(**base)  # type: ignore[arg-type]


def _plan(snapshot: FailureSnapshot, *, auto_recover: bool = True):
    return plan_recovery(
        snapshot=snapshot,
        classification=classify_failure(snapshot),
        workflow=_workflow(),
        workflow_path=_WORKFLOW_PATH,
        issue_id="288",
        auto_recover=auto_recover,
        now=_NOW,
    )


# --- resume 経路 ---


def test_candidate_with_auto_recover_resumes_from_failed_step() -> None:
    d = _plan(_snapshot())
    assert d.decision == "resume"
    assert d.recoverable is True
    assert d.resume_from == "review-code"
    assert d.resume_mode == "from"
    assert d.discarded_resume_session is False
    assert d.recovery_root_run_id == "260710120000"
    assert d.recovery_parent_run_id is None
    assert d.resume_command == (
        "kaji run .kaji/wf/dev.yaml 288 --from review-code "
        "--recovery-root 260710120000 --recovery-parent 260710120000"
    )


def test_resume_scheduled_at_is_decision_time_plus_600s() -> None:
    d = _plan(_snapshot())
    assert d.resume_scheduled_at == "2026-07-10T12:10:00+00:00"


def test_handler_never_auto_attaches_reset_cycle() -> None:
    # cycle exhaust は max_iterations という安全弁の正常作動であり、その自動解除は
    # 「無制限 auto retry 禁止」の実質的迂回になる（Issue #288 決定 14）。
    for decision in (_plan(_snapshot()), _plan(_snapshot(), auto_recover=False)):
        assert "--reset-cycle" not in (decision.resume_command or "")


def test_resume_step_rolls_back_to_session_origin_and_discards_session() -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="verdict_exception", step_id="verify-code", exception_type="VerdictParseError"
        ),
        failed_step="verify-code",
        attempt_error="VerdictParseError: missing reason",
    )
    d = _plan(snapshot)
    assert d.decision == "resume"
    assert d.resume_from == "review-code"
    assert d.discarded_resume_session is True
    assert "--from review-code" in (d.resume_command or "")


def test_auto_recover_disabled_yields_comment_only_but_keeps_resume_command() -> None:
    d = _plan(_snapshot(), auto_recover=False)
    assert d.decision == "comment_only"
    assert d.recoverable is True
    assert d.resume_command is not None
    assert d.resume_scheduled_at is None


# --- budget guard ---


def test_recovery_child_is_exhausted_regardless_of_cause() -> None:
    d = _plan(
        _snapshot(
            is_recovery_child=True,
            recovery_root_run_id="260710110000",
            recovery_parent_run_id="260710110000",
        )
    )
    assert d.decision == "exhausted"
    assert d.recoverable is False
    assert d.recovery_root_run_id == "260710110000"
    assert d.recovery_parent_run_id == "260710110000"


def test_independent_run_without_chain_flags_restores_budget() -> None:
    d = _plan(_snapshot(is_recovery_child=False))
    assert d.decision == "resume"


# --- safety gate ---


def test_branch_mismatch_gate() -> None:
    d = _plan(_snapshot(git=GitStateSummary(branch="main", available=True)))
    assert d.decision == "not_resumable"
    assert any("branch_mismatch" in e for e in d.evidence)


def test_missing_worktree_gate() -> None:
    d = _plan(_snapshot(state_worktree_dir=None, git=GitStateSummary(available=False)))
    assert d.decision == "not_resumable"
    assert any("worktree_unavailable" in e for e in d.evidence)


def test_provider_unavailable_gate() -> None:
    d = _plan(_snapshot(provider_available=False))
    assert d.decision == "not_resumable"
    assert any("provider_unavailable" in e for e in d.evidence)


@pytest.mark.parametrize(
    "error_text",
    [
        "CLIExecutionError: overloaded; credential rejected",
        "CLIExecutionError: try again later: permission denied",
        "CLIExecutionError: rate limit hit, HTTP 401 returned",
        "CLIExecutionError: overloaded (403 Forbidden)",
        "CLIExecutionError: try again: invalid token",
    ],
)
def test_sensitive_failure_pattern_gate(error_text: str) -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="review-code", exception_type="CLIExecutionError"
        ),
        attempt_error=error_text,
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert any("sensitive_failure_pattern" in e for e in d.evidence)


def test_rate_limit_token_quota_text_is_not_treated_as_credential_leak() -> None:
    # "tokens per minute" のような quota 文言で唯一の recovery budget を潰さない。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="review-code", exception_type="CLIExecutionError"
        ),
        attempt_error="CLIExecutionError: rate limit exceeded: 30000 input tokens per minute",
    )
    d = _plan(snapshot)
    assert d.decision == "resume"


# --- 副作用 skill gate（Issue #349: step ID ではなく Step.skill で判定する） ---


@pytest.mark.parametrize(
    ("failed_step_id", "skill"),
    [
        ("start", "issue-start"),
        ("pr", "i-pr"),
        ("close", "issue-close"),
        ("publish", "i-pr"),  # 標準と異なる step ID からの alias（一般則の検証）
    ],
)
def test_non_resumable_skill_gate_blocks_regardless_of_step_id(
    failed_step_id: str, skill: str
) -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id=failed_step_id, exception_type="StepTimeoutError"
        ),
        failed_step=failed_step_id,
        attempt_error="StepTimeoutError: timed out",
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert d.recoverable is False
    assert d.resume_command is None
    assert d.resume_scheduled_at is None
    assert d.resume_from is None
    assert failed_step_id in d.reason and skill in d.reason
    assert any(failed_step_id in e and skill in e for e in d.evidence)


@pytest.mark.parametrize("auto_recover", [True, False])
def test_non_resumable_skill_gate_ignores_auto_recover(auto_recover: bool) -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="pr", exception_type="StepTimeoutError"
        ),
        failed_step="pr",
        attempt_error="StepTimeoutError: timed out",
    )
    d = _plan(snapshot, auto_recover=auto_recover)
    assert d.decision == "not_resumable"
    assert d.recoverable is False
    assert d.resume_command is None
    assert d.resume_scheduled_at is None


@pytest.mark.parametrize(
    ("event", "workflow_end_status", "attempt_error"),
    [
        (
            FailureEvent(
                kind="dispatch_exception", step_id="pr", exception_type="StepTimeoutError"
            ),
            "ERROR",
            "StepTimeoutError: timed out",
        ),
        (
            FailureEvent(kind="verdict_exception", step_id="pr", exception_type="VerdictNotFound"),
            "ERROR",
            "VerdictNotFound: no verdict block",
        ),
        (FailureEvent(kind="agent_abort", step_id="pr", synthetic=False), "ABORT", ""),
        (
            FailureEvent(kind="cycle_exhausted", step_id="pr", cycle_name="pr-cycle"),
            "ABORT",
            "",
        ),
    ],
)
def test_non_resumable_skill_gate_is_cause_independent(
    event: FailureEvent, workflow_end_status: str, attempt_error: str
) -> None:
    snapshot = _snapshot(
        failure_event=event,
        failed_step="pr",
        workflow_end_status=workflow_end_status,
        attempt_error=attempt_error,
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert d.recoverable is False


def test_non_resumable_skill_gate_precedes_kaji_bug_suspected() -> None:
    # gate 0 は kaji_bug_suspected より優先する（Issue #349 § 方針 4）。bug issue は
    # 起票されないが、classification.cause は診断情報として保持される。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="pr", exception_type="StepTimeoutError"
        ),
        failed_step="pr",
        attempt_error="StepTimeoutError: timed out",
        state_loaded=False,
    )
    d = _plan(snapshot)
    assert d.classification.cause == "kaji_bug_suspected"
    assert d.decision == "not_resumable"
    assert d.recoverable is False


def test_non_resumable_skill_hits_deduplicate_when_failed_step_equals_resume_target() -> None:
    # `publish` に `resume:` はないため実再開先は自分自身。二重計上されないこと。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="publish", exception_type="StepTimeoutError"
        ),
        failed_step="publish",
        attempt_error="StepTimeoutError: timed out",
    )
    d = _plan(snapshot)
    hits = [e for e in d.evidence if "non_resumable_skill" in e]
    assert len(hits) == 1


# --- `resume:` の判定対象（failed step と実際の再開先の双方を検査する） ---


def test_resume_target_check_blocks_when_only_resume_target_is_non_resumable() -> None:
    # 失敗した step 自体は安全な skill だが、`resume:` の実再開先が再開禁止 skill。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="verdict_exception", step_id="fix-pr-meta", exception_type="VerdictNotFound"
        ),
        failed_step="fix-pr-meta",
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert any("resume_from=pr" in e and "skill=i-pr" in e for e in d.evidence)


def test_resume_target_check_blocks_when_only_failed_step_is_non_resumable() -> None:
    # 失敗した step が再開禁止 skill。`resume:` の実再開先自体は安全でも停止する。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="pr", exception_type="StepTimeoutError"
        ),
        failed_step="pr",
        attempt_error="StepTimeoutError: timed out",
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert any("failed_step=pr" in e and "skill=i-pr" in e for e in d.evidence)


def test_resume_target_check_continues_normally_when_both_sides_are_safe() -> None:
    # failed step・実再開先ともに安全な skill なら、既存の判定（resume 到達）を維持する。
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="verdict_exception", step_id="verify-code", exception_type="VerdictParseError"
        ),
        failed_step="verify-code",
        attempt_error="VerdictParseError: missing reason",
    )
    d = _plan(snapshot)
    assert d.decision == "resume"
    assert d.resume_from == "review-code"


# --- 境界: 未知 step / skill=None の exec-step ---


def test_exec_step_with_no_skill_does_not_match_non_resumable_gate() -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id="baseline", exception_type="StepTimeoutError"
        ),
        failed_step="baseline",
        attempt_error="StepTimeoutError: timed out",
    )
    d = _plan(snapshot)
    assert d.decision == "resume"
    assert not any("non_resumable_skill" in e for e in d.evidence)


def test_newer_run_detected_gate() -> None:
    d = _plan(_snapshot(newer_run_ids=("260710130000",)))
    assert d.decision == "not_resumable"
    assert any("newer_run_detected" in e for e in d.evidence)


def test_unknown_failed_step_is_not_resumable() -> None:
    snapshot = _snapshot(
        failure_event=FailureEvent(
            kind="verdict_exception", step_id="ghost-step", exception_type="VerdictNotFound"
        ),
        failed_step="ghost-step",
    )
    d = _plan(snapshot)
    assert d.decision == "not_resumable"
    assert any("unknown_failed_step" in e for e in d.evidence)
    assert not any("non_resumable_skill" in e for e in d.evidence)


# --- 非 candidate cause の decision mapping ---


@pytest.mark.parametrize(
    ("event", "workflow_end_status", "expected"),
    [
        (
            FailureEvent(kind="agent_abort", step_id="review-code", synthetic=False),
            "ABORT",
            "comment_only",
        ),
        (
            FailureEvent(kind="cycle_exhausted", step_id="review-code", cycle_name="code-review"),
            "ABORT",
            "not_resumable",
        ),
        (FailureEvent(kind="ambiguous_worktree"), "ABORT", "not_resumable"),
    ],
)
def test_non_candidate_decision_mapping(
    event: FailureEvent, workflow_end_status: str, expected: str
) -> None:
    d = _plan(_snapshot(failure_event=event, workflow_end_status=workflow_end_status))
    assert d.decision == expected
    assert d.recoverable is False


def test_user_precondition_error_is_not_resumable() -> None:
    # Issue #322: 新 cause は auto-resume 候補にしない（従来の挙動と同一の not_resumable）。
    d = _plan(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception",
                step_id="review-code",
                exception_type="TmuxSessionRequiredError",
            ),
            attempt_error="interactive terminal runner requires tmux.",
        )
    )
    assert d.classification.cause == "user_precondition_error"
    assert d.decision == "not_resumable"
    assert d.recoverable is False


def test_runtime_error_is_comment_only() -> None:
    d = _plan(
        _snapshot(
            failure_event=None,
            workflow_end_status="ERROR",
            workflow_end_error="RuntimeError: unreachable",
        )
    )
    assert d.decision == "comment_only"


def test_config_error_is_not_resumable() -> None:
    d = _plan(
        _snapshot(
            failure_event=None,
            workflow_end_status="ERROR",
            workflow_end_error="WorkdirNotFoundError: missing",
        )
    )
    assert d.decision == "not_resumable"


def test_budget_consumed_root_run_is_exhausted() -> None:
    # recovery child でなくても、自 run が過去に budget を消費していれば再開しない。
    d = _plan(_snapshot(is_recovery_child=False, budget_consumed=True))
    assert d.decision == "exhausted"
    assert d.recoverable is False
    assert "already consumed by a previous triage" in d.reason


def test_kaji_bug_suspected_with_evidence_plans_bug_issue() -> None:
    d = _plan(_snapshot(state_loaded=False))
    assert d.decision == "bug_issue_created"
    assert d.classification.cause == "kaji_bug_suspected"


def test_kaji_bug_suspected_without_evidence_is_not_resumable() -> None:
    d = _plan(_snapshot(state_loaded=False, evidence=()))
    assert d.decision == "not_resumable"
