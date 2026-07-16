"""Medium tests: 実 workflow YAML 全 variant の再開禁止 skill 検査 (Issue #349).

`.kaji/wf/*.yaml` を glob + `load_workflow()` でロードするため file I/O を伴う（Medium。
`docs/dev/testing-convention.md` § 判定基準「DB / ファイル / 内部サービス結合あり → Medium」）。
`plan_recovery()` が全 tracked built-in workflow・全 variant で `Step.skill` のみに基づいて
denylist を判定することを機械的に検証する。step ID ≠ skill の任意 alias の一般則自体は
`tests/test_recovery_plan.py`（Small・合成 workflow）が担い、本ファイルは実 YAML の
網羅性のみを担当する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaji_harness.recovery.classify import classify_failure
from kaji_harness.recovery.handler import plan_recovery
from kaji_harness.recovery.models import NON_RESUMABLE_SKILLS
from kaji_harness.recovery.snapshot import FailureEvent, FailureSnapshot, GitStateSummary
from kaji_harness.workflow import load_workflow

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATHS = sorted((REPO_ROOT / ".kaji" / "wf").glob("*.yaml"))

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _non_resumable_targets() -> list[tuple[Path, str, str]]:
    """全 tracked workflow から `(workflow_path, step_id, skill)` を re-open 禁止 skill の分だけ返す。"""
    targets: list[tuple[Path, str, str]] = []
    for path in WORKFLOW_PATHS:
        wf = load_workflow(path)
        for step in wf.steps:
            if step.skill in NON_RESUMABLE_SKILLS:
                targets.append((path, step.id, step.skill))
    return targets


TARGETS = _non_resumable_targets()
TARGET_IDS = [f"{path.name}:{step_id}" for path, step_id, _skill in TARGETS]


def _snapshot(*, failed_step: str) -> FailureSnapshot:
    return FailureSnapshot(
        run_id="260710120000",
        run_dir=REPO_ROOT / ".kaji-artifacts" / "349" / "runs" / "260710120000",
        run_log_schema_version=1,
        workflow_end_status="ERROR",
        failure_event=FailureEvent(
            kind="dispatch_exception", step_id=failed_step, exception_type="StepTimeoutError"
        ),
        failed_step=failed_step,
        attempt_error="StepTimeoutError: timed out",
        attempt_result_present=True,
        state_loaded=True,
        state_worktree_dir=str(REPO_ROOT),
        state_branch_name="fix/349",
        git=GitStateSummary(branch="fix/349", available=True, changed_files=0),
        provider_available=True,
        evidence=(),
    )


def test_tracked_workflow_inventory_is_not_empty() -> None:
    # vacuous pass 防止: glob 誤りで対象が消えていないこと。
    assert WORKFLOW_PATHS, "tracked .kaji/wf/*.yaml が 1 件も見つからない"


def test_non_resumable_skill_targets_are_not_empty() -> None:
    # vacuous pass 防止: denylist skill を持つ step が全体で 1 件以上あること。
    assert TARGETS, "denylist skill を使う step が tracked workflow に 1 件も見つからない"


@pytest.mark.parametrize("skill", sorted(NON_RESUMABLE_SKILLS))
def test_each_non_resumable_skill_appears_at_least_once(skill: str) -> None:
    # vacuous pass 防止（強化）: skill 別に最低 1 件検出されること（review-design 改善提案対応）。
    matched = [s for _path, _step_id, s in TARGETS if s == skill]
    assert matched, f"denylist skill {skill!r} を使う step が tracked workflow に見つからない"


@pytest.mark.parametrize(("workflow_path", "step_id", "skill"), TARGETS, ids=TARGET_IDS)
def test_non_resumable_skill_step_is_blocked_regardless_of_workflow(
    workflow_path: Path, step_id: str, skill: str
) -> None:
    wf = load_workflow(workflow_path)
    snapshot = _snapshot(failed_step=step_id)
    decision = plan_recovery(
        snapshot=snapshot,
        classification=classify_failure(snapshot),
        workflow=wf,
        workflow_path=workflow_path,
        issue_id="349",
        auto_recover=True,
        now=_NOW,
    )
    assert decision.decision == "not_resumable"
    assert decision.recoverable is False
    assert decision.resume_command is None
    assert decision.resume_scheduled_at is None
    assert any(skill in e for e in decision.evidence)
