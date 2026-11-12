"""Medium tests: ``collect_snapshot(allow_unterminated=...)`` and ``process_vanished``."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.recovery.classify import classify_failure
from kaji_harness.recovery.snapshot import collect_snapshot
from tests.runstate_helpers import start, step_end, step_start, write_run, write_state

pytestmark = pytest.mark.medium
ISSUE = "540"


def _snapshot(artifacts: Path, run_dir: Path, *, allow: bool) -> object:
    return collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=artifacts,
        issue_id=ISSUE,
        provider_available=True,
        allow_unterminated=allow,
        probe_git=False,
    )


def test_unterminated_snapshot_is_process_vanished(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, ISSUE, "261112000000", [start(), step_start("implement")])
    write_state(tmp_path, ISSUE)
    snapshot = _snapshot(tmp_path, run_dir, allow=True)
    assert snapshot.unterminated is True  # type: ignore[attr-defined]
    assert snapshot.failed_step == "implement"  # type: ignore[attr-defined]
    assert snapshot.artifact_read_errors == ()  # type: ignore[attr-defined]
    classification = classify_failure(snapshot)  # type: ignore[arg-type]
    assert classification.cause == "process_vanished"
    assert classification.source == "external"
    assert classification.recoverability_hint == "unknown"


def test_unterminated_snapshot_without_session_state_is_not_a_read_error(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, ISSUE, "261112000000", [start(), step_start("issue-start")])
    snapshot = _snapshot(tmp_path, run_dir, allow=True)
    assert classify_failure(snapshot).cause == "process_vanished"  # type: ignore[arg-type]


def test_unterminated_snapshot_with_missing_ended_result_is_bug_suspected(tmp_path: Path) -> None:
    events = [start(), step_start("design"), step_end("design"), step_start("implement")]
    run_dir = write_run(tmp_path, ISSUE, "261112000000", events)
    write_state(tmp_path, ISSUE)
    snapshot = _snapshot(tmp_path, run_dir, allow=True)
    assert any("design/attempt-001/result.json" in e for e in snapshot.artifact_read_errors)  # type: ignore[attr-defined]
    assert classify_failure(snapshot).cause == "kaji_bug_suspected"  # type: ignore[arg-type]


def test_default_snapshot_keeps_workflow_end_missing_read_error(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, ISSUE, "261112000000", [start(), step_start("implement")])
    write_state(tmp_path, ISSUE)
    snapshot = _snapshot(tmp_path, run_dir, allow=False)
    assert "run.log: workflow_end event missing" in snapshot.artifact_read_errors  # type: ignore[attr-defined]
    assert snapshot.unterminated is False  # type: ignore[attr-defined]
    assert classify_failure(snapshot).cause == "kaji_bug_suspected"  # type: ignore[arg-type]
