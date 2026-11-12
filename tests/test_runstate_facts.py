"""Medium tests: run / Issue facts from local artifacts (Issue #540)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from kaji_harness.runstate.facts import collect_issue_facts, decide_termination, latest_run_dir
from kaji_harness.runstate.launch import write_launch_record
from kaji_harness.runstate.process import current_process_record
from kaji_harness.runstate.schema import (
    MEMBER_STATUSES,
    SERIES_STATUSES,
    issue_to_json,
    literal_values,
)
from kaji_harness.series.state import MemberState, SeriesState
from tests.runstate_helpers import (
    end,
    ev,
    fake_probe,
    record,
    start,
    step_end,
    step_start,
    write_run,
    write_state,
)

ISSUE = "540"


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    path = tmp_path / "artifacts"
    path.mkdir()
    return path


@pytest.mark.small
class TestTerminationTable:
    @pytest.mark.parametrize(
        ("events", "alive", "expected"),
        [
            ([start(), end("ERROR")], True, "ended"),
            ([start()], True, "running"),
            ([start()], False, "vanished"),
            ([start()], None, "unknown"),
            (None, True, "running"),
            (None, False, None),
            (None, None, None),
        ],
    )
    def test_rows(
        self, events: list[dict[str, object]] | None, alive: bool | None, expected: str | None
    ) -> None:
        assert decide_termination(events, alive) == expected


@pytest.mark.small
def test_schema_enums_match_state_literals() -> None:
    assert literal_values(MemberState.model_fields["status"].annotation) == MEMBER_STATUSES
    assert literal_values(SeriesState.model_fields["status"].annotation) == SERIES_STATUSES


@pytest.mark.medium
class TestLatestRun:
    def test_same_second_suffix_is_newest(self, artifacts: Path) -> None:
        write_run(artifacts, ISSUE, "261112040509", [start(), end("ERROR")])
        write_run(artifacts, ISSUE, "261112040509-002", [start(), end("COMPLETE")])
        latest = latest_run_dir(artifacts, ISSUE)
        assert latest is not None and latest.name == "261112040509-002"

    def test_run_id_order_does_not_depend_on_utc_timestamps(self, artifacts: Path) -> None:
        # #1910 形: event の ts（UTC）が run_id（ローカル時刻）とずれていても順序は run_id で決まる
        older = [dict(start(), ts="2026-11-13T00:00:00+00:00"), end("ERROR")]
        newer = [dict(start(), ts="2026-11-11T00:00:00+00:00"), end("COMPLETE")]
        write_run(artifacts, ISSUE, "261112090000", older)
        write_run(artifacts, ISSUE, "261112100000", newer)
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe())
        assert facts.latest_run is not None and facts.latest_run.run_id == "261112100000"


@pytest.mark.medium
class TestSessionIdResolution:
    def _facts(self, artifacts: Path, **kwargs: object) -> dict[str, object]:
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe())
        doc = issue_to_json(facts)
        latest = doc["latest_run"]
        assert isinstance(latest, dict)
        step = latest["last_step"]
        assert isinstance(step, dict)
        return step

    def test_fresh_success_uses_result_json(self, artifacts: Path) -> None:
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            [start(), step_start("implement"), step_end("implement"), end("COMPLETE")],
            results={("implement", 1): {"session_id": "sess-result"}},
        )
        assert self._facts(artifacts)["session_id"] == "sess-result"

    def test_fresh_failure_keeps_diagnostic_id(self, artifacts: Path) -> None:
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            [start(), step_start("implement"), step_end("implement", 1, "ABORT"), end("ERROR")],
            results={("implement", 1): {"session_id": "sess-diag", "error": "boom"}},
        )
        assert self._facts(artifacts)["session_id"] == "sess-diag"

    def test_resume_step_in_flight_uses_step_start(self, artifacts: Path) -> None:
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            [start(), step_start("fix-code", session_id="sess-resumed")],
            process=record(77),
        )
        step = collect_issue_facts(artifacts, ISSUE, probe=fake_probe({77: 100})).latest_run
        assert step is not None and step.last_step is not None
        assert step.last_step.session_id == "sess-resumed"

    def test_fresh_in_flight_is_null_even_if_session_state_has_old_id(
        self, artifacts: Path
    ) -> None:
        write_run(artifacts, ISSUE, "261112000000", [start(), step_start("implement")])
        write_state(artifacts, ISSUE)
        state = artifacts / ISSUE / "session-state.json"
        state.write_text(
            state.read_text().replace('"sessions": {}', '"sessions": {"implement": "old"}')
        )
        assert self._facts(artifacts)["session_id"] is None


@pytest.mark.medium
class TestIssueFacts:
    def test_running_run_with_live_pid(self, artifacts: Path) -> None:
        write_run(
            artifacts, ISSUE, "261112000000", [start(), step_start("implement")], process=record(77)
        )
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe({77: 100}))
        assert facts.latest_run is not None
        assert facts.latest_run.termination == "running"
        assert facts.any_run_alive is True
        assert facts.latest_run.failed_step is None

    def test_killed_run_is_vanished_with_process_vanished(self, artifacts: Path) -> None:
        # R3 形: 生存していない pid、step_start だけの attempt、result.json なし
        write_run(
            artifacts, ISSUE, "261112000000", [start(), step_start("implement")], process=record(77)
        )
        write_state(artifacts, ISSUE)
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe())
        doc = issue_to_json(facts)
        latest = doc["latest_run"]
        assert isinstance(latest, dict)
        assert latest["termination"] == "vanished"
        assert latest["failed_step"] == "implement"
        assert latest["failure"] == {
            "cause": "process_vanished",
            "source": "external",
            "recoverability_hint": "unknown",
        }
        assert doc["read_errors"] == []
        assert doc["any_run_alive"] is False

    def test_ended_error_run_reports_failure_and_failed_step(self, artifacts: Path) -> None:
        events = [
            start(),
            step_start("implement"),
            ev(
                "failure_event",
                kind="dispatch_exception",
                step_id="implement",
                exception_type="StepTimeoutError",
                synthetic=True,
            ),
            step_end("implement", 1, "ABORT"),
            end("ERROR", "StepTimeoutError: timed out"),
        ]
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            events,
            process=record(77),
            results={("implement", 1): {"error": "timed out", "synthetic": True}},
        )
        write_state(artifacts, ISSUE)
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        latest = doc["latest_run"]
        assert isinstance(latest, dict)
        assert latest["termination"] == "ended"
        assert latest["end_status"] == "ERROR"
        assert latest["failed_step"] == "implement"
        assert latest["failure"]["cause"] == "dispatch_failure"  # type: ignore[index]
        assert latest["worktree_dir"] == "/wt/feat-540"
        assert latest["branch_name"] == "feat/540"

    def test_broken_artifacts_null_only_their_fields(self, artifacts: Path) -> None:
        events = [start(), step_start("implement"), step_end("implement", 1, "ABORT"), end("ERROR")]
        run_dir = write_run(
            artifacts, ISSUE, "261112000000", events, panes={("implement", 1): {"no": "pane"}}
        )
        (run_dir / "process.json").write_text("{broken", encoding="utf-8")
        (run_dir / "recovery-chain.json").write_text("[]", encoding="utf-8")
        write_state(artifacts, ISSUE, raw="not json")
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        latest = doc["latest_run"]
        assert isinstance(latest, dict)
        paths = {(e["path"], e["error"]) for e in doc["read_errors"]}  # type: ignore[index]
        base = f"{ISSUE}/runs/261112000000"
        assert (f"{base}/steps/implement/attempt-001/result.json", "missing") in paths
        assert (f"{base}/process.json", "invalid") in paths
        assert (f"{base}/recovery-chain.json", "invalid") in paths
        assert (f"{base}/steps/implement/attempt-001/pane-metadata.json", "invalid") in paths
        assert (f"{ISSUE}/session-state.json", "invalid") in paths
        assert latest["termination"] == "ended"
        assert latest["process"] == {"pid": None, "alive": None, "basis": "record_unreadable"}
        assert latest["worktree_dir"] is None
        assert latest["pane"] is None
        assert latest["recovery"] is None
        assert latest["last_step"]["id"] == "implement"  # type: ignore[index]

    def test_unparsable_run_log_is_null_termination_not_unknown(self, artifacts: Path) -> None:
        write_run(artifacts, ISSUE, "261112000000", None, raw_log="{not json\n")
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        latest = doc["latest_run"]
        assert isinstance(latest, dict)
        assert latest["termination"] is None
        assert latest["workflow"] is None
        assert doc["read_errors"][0]["path"] == f"{ISSUE}/runs/261112000000/run.log"  # type: ignore[index]

    def test_launch_record_without_run_dir_is_launching(self, artifacts: Path) -> None:
        # #1909 形: run dir 採番前（provider 解決中）の kaji run
        write_launch_record(artifacts, ISSUE, record(88))
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe({88: 100}))
        doc = issue_to_json(facts)
        assert doc["latest_run"] is None
        assert doc["any_run_alive"] is True
        assert doc["launching"] is True

    def test_dead_launch_record_is_not_launching(self, artifacts: Path) -> None:
        write_launch_record(artifacts, ISSUE, record(88))
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        assert doc["any_run_alive"] is False
        assert doc["launching"] is False

    def test_other_host_unfinished_run_makes_issue_unknown(self, artifacts: Path) -> None:
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            [start(), step_start("x")],
            process=record(5, host="other"),
        )
        write_run(
            artifacts, ISSUE, "261112010000", [start(), step_start("implement")], process=record(6)
        )
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        assert doc["any_run_alive"] is None

    def test_other_host_finished_run_is_false(self, artifacts: Path) -> None:
        write_run(
            artifacts,
            ISSUE,
            "261112000000",
            [start(), end("COMPLETE")],
            process=record(5, host="other"),
        )
        write_run(
            artifacts, ISSUE, "261112010000", [start(), step_start("implement")], process=record(6)
        )
        doc = issue_to_json(collect_issue_facts(artifacts, ISSUE, probe=fake_probe()))
        assert doc["any_run_alive"] is False

    def test_launch_record_with_run_id_marks_run_alive_when_process_json_missing(
        self, artifacts: Path
    ) -> None:
        write_run(artifacts, ISSUE, "261112000000", [start(), step_start("implement")])
        write_launch_record(artifacts, ISSUE, record(91, run_id="261112000000"))
        facts = collect_issue_facts(artifacts, ISSUE, probe=fake_probe({91: 100}))
        assert facts.latest_run is not None
        assert facts.latest_run.liveness.alive is True
        assert facts.latest_run.termination == "running"
        assert facts.launching is False


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.medium
def test_real_child_process_is_running_then_vanished(
    artifacts: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """#1909 形を実プロセスで: 生存中は running、終了後は vanished（実 /proc で判定）。"""
    rec = current_process_record("run", pid=sleeper.pid, run_id="261112000000")
    write_run(artifacts, ISSUE, "261112000000", [start(), step_start("implement")], process=rec)
    running = collect_issue_facts(artifacts, ISSUE)
    assert running.latest_run is not None
    assert running.latest_run.termination == "running"
    assert running.any_run_alive is True
    sleeper.kill()
    sleeper.wait()
    vanished = collect_issue_facts(artifacts, ISSUE)
    assert vanished.latest_run is not None
    assert vanished.latest_run.termination == "vanished"
    assert vanished.any_run_alive is False
