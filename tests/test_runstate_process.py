"""Small tests: process liveness records and probing (Issue #540)."""

from __future__ import annotations

import pytest

from kaji_harness.runstate.process import (
    Liveness,
    complete_with_run_log,
    current_process_record,
    is_finished_by_log,
    parse_proc_stat,
    probe_liveness,
)
from tests.runstate_helpers import end, ev, fake_probe, record, start, stat_line, step_start

pytestmark = pytest.mark.small


class TestProbeLivenessTable:
    def test_no_record(self) -> None:
        assert probe_liveness(None, probe=fake_probe()) == Liveness(None, "no_record")

    def test_record_unreadable(self) -> None:
        assert probe_liveness(None, unreadable=True, probe=fake_probe()) == Liveness(
            None, "record_unreadable"
        )

    def test_other_host(self) -> None:
        probe = fake_probe({10: 100})
        assert probe_liveness(record(10, host="other"), probe=probe) == Liveness(None, "other_host")

    def test_rebooted(self) -> None:
        probe = fake_probe({10: 100}, boot="boot-2")
        assert probe_liveness(record(10), probe=probe) == Liveness(False, "rebooted")

    def test_boot_id_null_on_one_side_is_not_rebooted(self) -> None:
        probe = fake_probe({10: 100}, boot=None)
        assert probe_liveness(record(10), probe=probe) == Liveness(True, "pid_verified")

    def test_pid_absent_esrch(self) -> None:
        assert probe_liveness(record(10), probe=fake_probe()) == Liveness(False, "pid_absent")

    def test_pid_vanishes_during_probe(self) -> None:
        probe = fake_probe(vanish_on_stat=frozenset({10}))
        assert probe_liveness(record(10), probe=probe) == Liveness(False, "pid_absent")

    @pytest.mark.parametrize("state", ["Z", "X"])
    def test_zombie_or_dead(self, state: str) -> None:
        probe = fake_probe({10: 100}, zombies=frozenset({10}), zombie_state=state)
        assert probe_liveness(record(10), probe=probe) == Liveness(False, "zombie")

    def test_pid_verified(self) -> None:
        assert probe_liveness(record(10), probe=fake_probe({10: 100})) == Liveness(
            True, "pid_verified"
        )

    def test_eperm_is_treated_as_existing_and_checks_ticks(self) -> None:
        probe = fake_probe({10: 100}, eperm=frozenset({10}))
        assert probe_liveness(record(10), probe=probe) == Liveness(True, "pid_verified")
        probe = fake_probe({10: 555}, eperm=frozenset({10}))
        assert probe_liveness(record(10), probe=probe) == Liveness(False, "pid_reused")

    def test_pid_reused(self) -> None:
        assert probe_liveness(record(10), probe=fake_probe({10: 999})) == Liveness(
            False, "pid_reused"
        )

    def test_unverifiable_without_proc(self) -> None:
        probe = fake_probe({10: 100}, proc=False)
        assert probe_liveness(record(10), probe=probe) == Liveness(None, "unverifiable")

    def test_unverifiable_without_recorded_ticks(self) -> None:
        probe = fake_probe({10: 100})
        assert probe_liveness(record(10, ticks=None), probe=probe) == Liveness(None, "unverifiable")


def test_parse_proc_stat_handles_parenthesised_comm() -> None:
    assert parse_proc_stat(stat_line(42, 777, "R")) == ("R", 777)
    assert parse_proc_stat("garbage") is None


def test_current_process_record_reads_own_ticks() -> None:
    rec = current_process_record("launcher", probe=fake_probe({4242: 31}), pid=4242)
    assert rec.role == "launcher"
    assert rec.pid == 4242
    assert rec.pid_start_ticks == 31
    assert rec.run_id is None


class TestFinishedByLog:
    @pytest.mark.parametrize("basis", ["no_record", "other_host", "unverifiable"])
    def test_complete_run_is_finished(self, basis: str) -> None:
        events = [start(), end("COMPLETE")]
        result = complete_with_run_log(Liveness(None, basis), events)  # type: ignore[arg-type]
        assert result == Liveness(False, "finished_by_log")

    def test_non_resume_decision_is_finished(self) -> None:
        events = [start(), end("ERROR"), ev("recovery_decision", decision="comment_only")]
        assert is_finished_by_log(events)

    def test_recovery_attempt_end_is_finished(self) -> None:
        events = [
            start(),
            end("ERROR"),
            ev("recovery_decision", decision="resume"),
            ev("recovery_attempt_end", child_run_id="x", child_final_status="COMPLETE"),
        ]
        assert is_finished_by_log(events)

    @pytest.mark.parametrize(
        "events",
        [
            [start(), step_start("implement")],
            [start(), end("ERROR")],
            [start(), end("ERROR"), ev("recovery_decision", decision="resume")],
        ],
    )
    def test_unfinished_runs_stay_null(self, events: list[dict[str, object]]) -> None:
        assert complete_with_run_log(Liveness(None, "no_record"), events) == Liveness(
            None, "no_record"
        )

    def test_verified_true_is_not_overridden(self) -> None:
        events = [start(), end("COMPLETE")]
        assert complete_with_run_log(Liveness(True, "pid_verified"), events) == Liveness(
            True, "pid_verified"
        )

    def test_record_unreadable_is_not_overridden(self) -> None:
        events = [start(), end("COMPLETE")]
        assert complete_with_run_log(Liveness(None, "record_unreadable"), events).basis == (
            "record_unreadable"
        )
