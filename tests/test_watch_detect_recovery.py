"""Small tests: ``detect_events`` with automatic recovery records (Issue #545)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kaji_harness.watch.detect import (
    DetectResult,
    DetectTracking,
    Ledger,
    RecordEntry,
    detect_events,
)
from kaji_harness.watch.labels import Anchor, Target, WatchIssue, compute_target

pytestmark = pytest.mark.small

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
NO_ANCHOR = Anchor(run_id=None, inclusive=False, pending_member=False)
RUN = "261115000000"
BASE = f"101:pane_idle:{RUN}:implement:1"


def _issue(*, termination: str = "running", end_status: str | None = None) -> WatchIssue:
    run: dict[str, Any] = {
        "run_id": RUN,
        "termination": termination,
        "end_status": end_status,
        "last_step": {"id": "implement", "attempt": 1},
        "pane": {"backend": "herdr", "pane_id": "p_9", "origin_pane": "p_1"},
    }
    return WatchIssue(
        issue=101,
        github_state="open",
        state_reason=None,
        state_labels=("state:running",),
        status={
            "issue": "101",
            "latest_run": run,
            "any_run_alive": termination == "running",
            "launching": False,
            "read_errors": [],
        },
    )


def _targets(issue: WatchIssue) -> dict[int, Target]:
    return {issue.issue: compute_target(issue, NO_ANCHOR)}


class Driver:
    """周の列で ``detect_events`` を駆動する（ledger は検知した event と記録を貯める）。"""

    def __init__(self) -> None:
        self.tracking = DetectTracking()
        self.keys: set[str] = set()
        self.records: list[RecordEntry] = []

    def poll(
        self, pane: str | None, now: datetime, issue: WatchIssue | None = None
    ) -> DetectResult:
        current = issue or _issue()
        result = detect_events(
            [current],
            _targets(current),
            {101: pane},
            self.tracking,
            Ledger(event_keys=frozenset(self.keys), records=tuple(self.records)),
            now,
        )
        self.tracking = result.tracking
        self.keys.update(e.key for e in result.events)
        return result

    def record(self, key: str, outcome: Any, at: datetime, until: datetime | None = None) -> None:
        if ":aborted:" in key:
            progress: Any = ("state:aborted", RUN, "implement", 1, 0)
        else:
            progress = ("state:running", RUN, "implement", 1, int(key.split(":")[5]))
        self.records.append(
            RecordEntry(
                event_key=key,
                issue=101,
                outcome=outcome,
                recorded_at=at,
                progress=progress,
                until=until,
            )
        )


def _t(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def _idle_event(driver: Driver, start: int) -> str:
    driver.poll("idle", _t(start))
    result = driver.poll("idle", _t(start + 3))
    assert [e.kind for e in result.events] == ["pane_idle"]
    return result.events[0].key


def test_recovered_between_polls_advances_episode() -> None:
    driver = Driver()
    driver.poll("working", _t(0))
    key = _idle_event(driver, 3)
    assert key == f"{BASE}:0"
    # triage が continue → working を観測して recovered（監視は working を見ていない）
    driver.record(key, "recovered", _t(7))
    first = driver.poll("idle", _t(9))
    assert first.events == []
    second = driver.poll("idle", _t(12))
    assert [e.key for e in second.events] == [f"{BASE}:1"]


def test_same_recovered_is_applied_once() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    driver.record(key, "recovered", _t(4))
    driver.poll("idle", _t(6))
    driver.poll("idle", _t(9))  # :1 を出す
    assert driver.tracking.pane_idle[101].episode == 1
    for minute in (12, 15, 18):
        assert driver.poll("idle", _t(minute)).events == []
    assert driver.tracking.pane_idle[101].episode == 1


def test_observed_working_control_has_same_episode() -> None:
    observed = Driver()
    key = _idle_event(observed, 0)
    observed.poll("working", _t(6))
    observed.poll("idle", _t(9))
    result = observed.poll("idle", _t(12))
    assert [e.key for e in result.events] == [f"{BASE}:1"]
    assert key == f"{BASE}:0"


def test_handoff_without_recovered_stays_suppressed() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    driver.record(key, "handoff", _t(4))
    for minute in (6, 9, 12):
        result = driver.poll("idle", _t(minute))
        assert result.events == []
    assert 101 in result.suppressed


def test_wait_suppresses_pane_idle_until_then_reemits() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    until = _t(60)
    driver.record(key, "wait", _t(4), until=until)
    for minute in (6, 9, 30, 57):
        result = driver.poll("idle", _t(minute))
        assert result.events == []
        assert result.waiting == {101: until}
    after = driver.poll("idle", _t(63))
    assert [e.key for e in after.events] == [f"{key}:w1"]
    assert after.events[0].waited_until == until
    assert after.waiting == {}
    # 同じ周の再実行で重ねて出さない
    assert driver.poll("idle", _t(66)).events == []


def test_second_wait_on_reemitted_event_uses_next_suffix() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    driver.record(key, "wait", _t(4), until=_t(30))
    first = driver.poll("idle", _t(33)).events[0].key
    assert first == f"{key}:w1"
    driver.record(first, "wait", _t(34), until=_t(60))
    assert driver.poll("idle", _t(40)).events == []
    assert [e.key for e in driver.poll("idle", _t(63)).events] == [f"{key}:w2"]


def test_wait_dropped_when_pane_returns_to_working() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    driver.record(key, "wait", _t(4), until=_t(120))
    driver.poll("working", _t(6))
    driver.poll("idle", _t(9))
    result = driver.poll("idle", _t(12))
    assert [e.key for e in result.events] == [f"{BASE}:1"]
    assert result.waiting == {}


def test_wait_dropped_when_run_changes() -> None:
    driver = Driver()
    key = _idle_event(driver, 0)
    driver.record(key, "wait", _t(4), until=_t(120))
    other = _issue()
    other.status["latest_run"]["run_id"] = "261115999999"
    driver.poll("idle", _t(6), other)
    result = driver.poll("idle", _t(9), other)
    assert [e.key for e in result.events] == ["101:pane_idle:261115999999:implement:1:0"]


def test_wait_on_aborted_delays_only_launch_then_reemits() -> None:
    driver = Driver()
    aborted = _issue(termination="ended", end_status="ERROR")
    first = driver.poll(None, _t(0), aborted)
    key = first.events[0].key
    assert key == f"101:aborted:{RUN}"
    until = _t(60)
    driver.record(key, "wait", _t(1), until=until)
    before = driver.poll(None, _t(30), aborted)
    assert before.events == [] and before.waiting == {101: until}
    after = driver.poll(None, _t(61), aborted)
    assert [(e.key, e.kind, e.waited_until) for e in after.events] == [
        (f"{key}:w1", "aborted", until)
    ]
