"""Medium tests: recovery artifact の永続化 / 再読込 と snapshot 収集 (Issue #288).

``recovery.json`` / ``recovery-chain.json`` の書き出し・読み戻し、``run.log`` の
recovery event 5 種、``collect_snapshot()`` の run.log / result.json /
session-state.json / git state / newer-run 走査を実 filesystem で検証する。
"""

from __future__ import annotations

import json
import subprocess as _sp
from pathlib import Path

import pytest

from kaji_harness.logger import RunLogger
from kaji_harness.recovery.models import (
    RECOVERY_CHAIN_FILE,
    RECOVERY_FILE,
    FailureClassification,
    RecoveryDecision,
    read_recovery_chain,
    read_recovery_json,
    write_recovery_chain,
    write_recovery_json,
)
from kaji_harness.recovery.snapshot import collect_snapshot, probe_git_state

pytestmark = pytest.mark.medium

_ISSUE = "local-pc1-99"


def _classification() -> FailureClassification:
    return FailureClassification(
        cause="verdict_resolution_failure",
        synthetic=True,
        source="agent",
        recoverability_hint="candidate",
    )


def _decision(run_id: str = "260710120000") -> RecoveryDecision:
    return RecoveryDecision(
        run_id=run_id,
        recoverable=True,
        decision="resume",
        classification=_classification(),
        failed_step="review-code",
        resume_from="review-code",
        resume_mode="from",
        resume_command="kaji run wf.yaml 99 --from review-code",
        reason="VerdictNotFound",
        evidence=["run.log: workflow_end status=ERROR"],
        recovery_root_run_id=run_id,
        resume_scheduled_at="2026-07-10T12:10:00+00:00",
        workflow_path="wf.yaml",
    )


# --- recovery.json / recovery-chain.json ---


def test_recovery_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / RECOVERY_FILE
    write_recovery_json(path, _decision())
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert read_recovery_json(path) == _decision()


def test_recovery_json_overwrites_previous_decision(tmp_path: Path) -> None:
    path = tmp_path / RECOVERY_FILE
    write_recovery_json(path, _decision())
    updated = _decision()
    updated.decision = "cancelled_newer_run_detected"
    updated.recoverable = False
    write_recovery_json(path, updated)
    assert read_recovery_json(path).decision == "cancelled_newer_run_detected"


def test_recovery_chain_round_trip(tmp_path: Path) -> None:
    path = tmp_path / RECOVERY_CHAIN_FILE
    write_recovery_chain(path, root_run_id="r1", parent_run_id="p1")
    assert read_recovery_chain(path) == ("r1", "p1")
    assert read_recovery_chain(tmp_path / "missing.json") is None


# --- run.log recovery events ---


def test_run_logger_emits_five_recovery_events(tmp_path: Path) -> None:
    logger = RunLogger(log_path=tmp_path / "run.log")
    logger.log_failure_event(
        kind="verdict_exception", step_id="review-code", exception_type="VerdictNotFound"
    )
    logger.log_recovery_decision(_decision())
    logger.log_recovery_scheduled(resume_scheduled_at="2026-07-10T12:10:00+00:00", wait_seconds=600)
    logger.log_recovery_attempt_start(
        resume_command="kaji run wf.yaml 99 --from review-code",
        resume_started_at="2026-07-10T12:10:01+00:00",
    )
    logger.log_recovery_attempt_end(
        child_run_id="260710121001", child_final_status="COMPLETE", exit_code=0
    )

    events = [json.loads(line) for line in (tmp_path / "run.log").read_text().splitlines()]
    assert [e["event"] for e in events] == [
        "failure_event",
        "recovery_decision",
        "recovery_scheduled",
        "recovery_attempt_start",
        "recovery_attempt_end",
    ]
    assert events[0]["synthetic"] is True
    assert events[1]["decision"] == "resume"
    assert events[1]["cause"] == "verdict_resolution_failure"
    assert events[2]["wait_seconds"] == 600
    assert events[3]["resume_command"].endswith("--from review-code")
    assert events[4]["child_final_status"] == "COMPLETE"


# --- snapshot 収集 ---


def _git_repo(tmp_path: Path, branch: str = "feat/99") -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    _sp.run(["git", "init", "-q", f"--initial-branch={branch}", str(wt)], check=True)
    (wt / "a.txt").write_text("dirty\n")
    return wt


def _build_run(
    tmp_path: Path,
    run_id: str,
    *,
    events: list[dict[str, object]],
    result: dict[str, object] | None = None,
    step_id: str = "review-code",
    chain: tuple[str, str] | None = None,
) -> Path:
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    if result is not None:
        attempt = run_dir / "steps" / step_id / "attempt-001"
        attempt.mkdir(parents=True)
        (attempt / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if chain is not None:
        write_recovery_chain(
            run_dir / RECOVERY_CHAIN_FILE, root_run_id=chain[0], parent_run_id=chain[1]
        )
    return run_dir


def _seed_state(tmp_path: Path, worktree: Path, branch: str = "feat/99") -> None:
    d = tmp_path / ".kaji-artifacts" / _ISSUE
    d.mkdir(parents=True, exist_ok=True)
    (d / "session-state.json").write_text(
        json.dumps(
            {
                "issue_number": _ISSUE,
                "sessions": {},
                "step_history": [],
                "cycle_counts": {},
                "last_completed_step": "implement",
                "last_transition_verdict": None,
                "worktree_dir": str(worktree),
                "branch_name": branch,
            }
        ),
        encoding="utf-8",
    )


def _collect(tmp_path: Path, run_dir: Path, provider_available: bool = True):
    return collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        issue_id=_ISSUE,
        provider_available=provider_available,
    )


def _failing_events() -> list[dict[str, object]]:
    return [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev"},
        {
            "event": "failure_event",
            "kind": "verdict_exception",
            "step_id": "review-code",
            "exception_type": "VerdictNotFound",
            "cycle_name": None,
            "synthetic": True,
        },
        {"event": "workflow_end", "status": "ERROR", "error": "VerdictNotFound: missing block"},
    ]


def _result(error: str = "VerdictNotFound: missing block") -> dict[str, object]:
    return {
        "step_id": "review-code",
        "attempt": 1,
        "status": "ABORT",
        "exit_code": 0,
        "signal": None,
        "started_at": "t",
        "ended_at": "t",
        "duration_ms": 1,
        "session_id": "sess",
        "dispatch": "agent",
        "error": error,
        "synthetic": True,
    }


def test_collect_snapshot_reads_run_log_result_and_state(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path, "260710120000", events=_failing_events(), result=_result())

    snap = _collect(tmp_path, run_dir)

    assert snap.run_id == "260710120000"
    assert snap.workflow_end_status == "ERROR"
    assert snap.workflow_end_error == "VerdictNotFound: missing block"
    assert snap.failure_event is not None
    assert snap.failure_event.kind == "verdict_exception"
    assert snap.failure_event.exception_type == "VerdictNotFound"
    assert snap.failed_step == "review-code"
    assert snap.attempt_result_present is True
    assert snap.attempt_synthetic is True
    assert snap.attempt_error == "VerdictNotFound: missing block"
    assert snap.state_loaded is True
    assert snap.state_worktree_dir == str(wt)
    assert snap.state_branch_name == "feat/99"
    assert snap.git.available is True
    assert snap.git.branch == "feat/99"
    assert snap.git.changed_files == 1
    assert snap.is_recovery_child is False
    assert snap.artifact_read_errors == ()
    assert snap.evidence  # 根拠が列挙できている


def test_collect_snapshot_detects_recovery_child(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(
        tmp_path,
        "260710120000",
        events=_failing_events(),
        result=_result(),
        chain=("260710110000", "260710110000"),
    )

    snap = _collect(tmp_path, run_dir)

    assert snap.is_recovery_child is True
    assert snap.recovery_root_run_id == "260710110000"
    assert snap.recovery_parent_run_id == "260710110000"


def test_collect_snapshot_detects_newer_runs(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path, "260710120000", events=_failing_events(), result=_result())
    (run_dir.parent / "260710130000").mkdir()
    (run_dir.parent / "260710110000").mkdir()

    snap = _collect(tmp_path, run_dir)

    assert snap.newer_run_ids == ("260710130000",)


def test_collect_snapshot_reports_missing_attempt_result(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path, "260710120000", events=_failing_events(), result=None)

    snap = _collect(tmp_path, run_dir)

    assert snap.attempt_result_present is False
    assert snap.attempt_error is None


def test_collect_snapshot_reports_unreadable_run_log(tmp_path: Path) -> None:
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / "260710120000"
    run_dir.mkdir(parents=True)

    snap = _collect(tmp_path, run_dir)

    assert snap.artifact_read_errors
    assert any("run.log" in e for e in snap.artifact_read_errors)


def test_collect_snapshot_reports_broken_state(tmp_path: Path) -> None:
    d = tmp_path / ".kaji-artifacts" / _ISSUE
    d.mkdir(parents=True)
    (d / "session-state.json").write_text("{ not json", encoding="utf-8")
    run_dir = _build_run(tmp_path, "260710120000", events=_failing_events(), result=_result())

    snap = _collect(tmp_path, run_dir)

    assert snap.state_loaded is False


def test_collect_snapshot_masks_credentials_in_evidence(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(
        tmp_path,
        "260710120000",
        events=_failing_events(),
        result=_result(error="CLIExecutionError: Bearer ghp_abcdefghijklmnopqrst"),
    )

    snap = _collect(tmp_path, run_dir)

    assert all("ghp_abcdefghijklmnopqrst" not in e for e in snap.evidence)


def test_probe_git_state_for_missing_directory(tmp_path: Path) -> None:
    summary = probe_git_state(tmp_path / "nope")
    assert summary.available is False
    assert summary.branch is None


# --- Issue #403: 診断用 session ID / 孤児 pane の evidence 提示 ---


def _interrupted_events(step_id: str = "review-code") -> list[dict[str, object]]:
    return [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev", "schema_version": 1},
        {
            "event": "failure_event",
            "kind": "interrupted",
            "step_id": step_id,
            "exception_type": "KeyboardInterrupt",
            "cycle_name": None,
            "synthetic": True,
        },
        {
            "event": "workflow_end",
            "status": "ERROR",
            "error": "KeyboardInterrupt: workflow interrupted by user",
        },
    ]


def _write_pane_metadata(
    run_dir: Path,
    step_id: str,
    pane_id: str,
    *,
    attempt_name: str = "attempt-001",
    pane_dead: str = "0",
) -> None:
    attempt = run_dir / "steps" / step_id / attempt_name
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "pane-metadata.json").write_text(
        json.dumps({"pane_id": pane_id, "pane_dead": pane_dead}), encoding="utf-8"
    )


def test_collect_snapshot_exposes_attempt_session_id_in_evidence(tmp_path: Path) -> None:
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120010", events=_failing_events(), result=_result())

    snap = _collect(tmp_path, run_dir)

    assert snap.attempt_session_id == "sess"
    assert any("steps/review-code/result.json session_id=sess" in e for e in snap.evidence)


def test_collect_snapshot_omits_session_id_line_when_null(tmp_path: Path) -> None:
    _seed_state(tmp_path, _git_repo(tmp_path))
    result = _result()
    result["session_id"] = None
    run_dir = _build_run(tmp_path, "260710120011", events=_failing_events(), result=result)

    snap = _collect(tmp_path, run_dir)

    assert snap.attempt_session_id is None
    assert not any("session_id=" in e for e in snap.evidence)


def test_collect_snapshot_reports_orphan_pane_for_interrupted_run(tmp_path: Path) -> None:
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120012", events=_interrupted_events())
    _write_pane_metadata(run_dir, "review-code", "%42")

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id == "%42"
    assert any(
        "attempt-001/pane-metadata.json: orphan pane pane_id=%42 (not killed)" in e
        for e in snap.evidence
    )


def test_collect_snapshot_interrupted_without_pane_metadata_adds_no_read_error(
    tmp_path: Path,
) -> None:
    # pane metadata の不在 / 破損を artifact_read_errors に入れると headless run で
    # kaji_bug_suspected を誤判定する。
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120013", events=_interrupted_events())

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id is None
    assert snap.artifact_read_errors == ()
    assert not any("pane-metadata" in e for e in snap.evidence)


def test_collect_snapshot_ignores_broken_pane_metadata(tmp_path: Path) -> None:
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120014", events=_interrupted_events())
    attempt = run_dir / "steps" / "review-code" / "attempt-001"
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "pane-metadata.json").write_text("{not json", encoding="utf-8")

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id is None
    assert snap.artifact_read_errors == ()


def test_collect_snapshot_skips_orphan_pane_for_completed_attempt(tmp_path: Path) -> None:
    # 割込みが新 attempt 作成前（in_flight_step_id 設定後）に入ると、最新 attempt は
    # 同 step の直前の完了済み attempt になる。完了済み attempt の pane は cleanup 済みなので
    # 孤児 pane evidence に採用しない（採用すると人手 recovery を誤誘導する）。
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120016", events=_interrupted_events(), result=_result())
    _write_pane_metadata(run_dir, "review-code", "%old", pane_dead="1")

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id is None
    assert not any("orphan pane" in e for e in snap.evidence)


def test_collect_snapshot_reports_orphan_pane_from_in_flight_attempt(tmp_path: Path) -> None:
    # 完了済み attempt-001 の後に in-flight の attempt-002（result.json なし）がある場合は、
    # in-flight 側の pane を孤児として提示する。
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120017", events=_interrupted_events(), result=_result())
    _write_pane_metadata(run_dir, "review-code", "%old", pane_dead="1")
    _write_pane_metadata(run_dir, "review-code", "%new", attempt_name="attempt-002")

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id == "%new"
    assert any(
        "attempt-002/pane-metadata.json: orphan pane pane_id=%new (not killed)" in e
        for e in snap.evidence
    )


def test_collect_snapshot_skips_orphan_pane_for_non_interrupted_run(tmp_path: Path) -> None:
    _seed_state(tmp_path, _git_repo(tmp_path))
    run_dir = _build_run(tmp_path, "260710120015", events=_failing_events(), result=_result())
    _write_pane_metadata(run_dir, "review-code", "%42")

    snap = _collect(tmp_path, run_dir)

    assert snap.orphan_pane_id is None
    assert not any("orphan pane" in e for e in snap.evidence)
