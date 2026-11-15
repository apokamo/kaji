"""Small tests: ``kaji_harness.watch.detect`` (Issue #543)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kaji_harness.watch.detect import (
    DetectTracking,
    Ledger,
    PaneIdleCounter,
    RecordEntry,
    detect_events,
)
from kaji_harness.watch.labels import Anchor, Target, WatchIssue, compute_target

pytestmark = pytest.mark.small

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
NO_ANCHOR = Anchor(run_id=None, inclusive=False, pending_member=False)


def _issue(
    *,
    run_id: str = "261115000000",
    termination: str = "running",
    end_status: str | None = None,
    pane: bool = True,
    step: str = "implement",
    attempt: int = 1,
    labels: tuple[str, ...] = ("state:running",),
) -> WatchIssue:
    run: dict[str, Any] = {
        "run_id": run_id,
        "termination": termination,
        "end_status": end_status,
        "last_step": {"id": step, "attempt": attempt},
        "pane": {"backend": "herdr", "pane_id": "p_9", "origin_pane": "p_1"} if pane else None,
    }
    return WatchIssue(
        issue=101,
        github_state="open",
        state_reason=None,
        state_labels=labels,
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


def _detect(
    issue: WatchIssue,
    *,
    pane: str | None = None,
    tracking: DetectTracking | None = None,
    ledger: Ledger | None = None,
    now: datetime = NOW,
    open_triage: frozenset[int] = frozenset(),
) -> Any:
    return detect_events(
        [issue],
        _targets(issue),
        {issue.issue: pane},
        tracking or DetectTracking(),
        ledger or Ledger(),
        now,
        open_triage=open_triage,
    )


def _record(key: str, outcome: Any, at: datetime, progress: Any = None) -> RecordEntry:
    return RecordEntry(event_key=key, issue=101, outcome=outcome, recorded_at=at, progress=progress)


def test_aborted_event_for_failed_run() -> None:
    result = _detect(_issue(termination="ended", end_status="ERROR"))
    assert [e.key for e in result.events] == ["101:aborted:261115000000"]
    assert result.events[0].kind == "aborted"


def test_aborted_event_not_repeated_when_in_ledger() -> None:
    ledger = Ledger(event_keys=frozenset({"101:aborted:261115000000"}))
    assert _detect(_issue(termination="ended", end_status="ERROR"), ledger=ledger).events == []


def test_aborted_event_for_new_failed_run_while_already_aborted() -> None:
    # M5: run A で既に aborted、retry した run B も失敗 → B の event が出る
    ledger = Ledger(event_keys=frozenset({"101:aborted:261115000000"}))
    issue = _issue(
        run_id="261115010000", termination="ended", end_status="ABORT", labels=("state:aborted",)
    )
    assert [e.key for e in _detect(issue, ledger=ledger).events] == ["101:aborted:261115010000"]


def test_complete_open_has_no_event() -> None:
    assert _detect(_issue(termination="ended", end_status="COMPLETE")).events == []


def test_pane_idle_after_two_polls() -> None:
    first = _detect(_issue(), pane="idle")
    assert first.events == []
    second = _detect(_issue(), pane="idle", tracking=first.tracking)
    assert [e.key for e in second.events] == ["101:pane_idle:261115000000:implement:1:0"]


def test_pane_idle_not_for_headless() -> None:
    first = _detect(_issue(pane=False), pane="idle")
    second = _detect(_issue(pane=False), pane="idle", tracking=first.tracking)
    assert second.events == []


def test_pane_idle_episode_increments_after_working() -> None:
    tracking = _detect(_issue(), pane="idle").tracking
    tracking = _detect(_issue(), pane="working", tracking=tracking).tracking
    tracking = _detect(_issue(), pane="blocked", tracking=tracking).tracking
    result = _detect(_issue(), pane="unknown", tracking=tracking)
    assert [e.key for e in result.events] == ["101:pane_idle:261115000000:implement:1:1"]


def test_pane_idle_counter_resets_on_new_attempt() -> None:
    tracking = _detect(_issue(), pane="idle").tracking
    result = _detect(_issue(attempt=2), pane="idle", tracking=tracking)
    assert result.events == []


def test_still_working_snooze_then_recheck() -> None:
    key = "101:pane_idle:261115000000:implement:1:0"
    tracking = _detect(_issue(), pane="idle").tracking
    ledger = Ledger(event_keys=frozenset({key}), records=(_record(key, "still-working", NOW),))
    soon = _detect(_issue(), pane="idle", tracking=tracking, ledger=ledger, now=NOW)
    assert soon.events == []
    later = NOW + timedelta(seconds=1200)
    result = _detect(_issue(), pane="idle", tracking=tracking, ledger=ledger, now=later)
    assert [e.key for e in result.events] == [f"{key}:r1"]


def test_log_stall_after_threshold_and_not_stalled_recheck() -> None:
    tracking = _detect(_issue(), now=NOW).tracking
    assert _detect(_issue(), tracking=tracking, now=NOW + timedelta(seconds=5399)).events == []
    at = NOW + timedelta(seconds=5400)
    result = _detect(_issue(), tracking=tracking, now=at)
    key = "101:log_stall:261115000000:implement:1:0"
    assert [e.key for e in result.events] == [key]
    ledger = Ledger(event_keys=frozenset({key}), records=(_record(key, "not-stalled", at),))
    assert _detect(_issue(), tracking=tracking, ledger=ledger, now=at).events == []
    recheck = _detect(_issue(), tracking=tracking, ledger=ledger, now=at + timedelta(seconds=5400))
    assert [e.key for e in recheck.events] == ["101:log_stall:261115000000:implement:1:1"]


def test_log_stall_resets_on_progress() -> None:
    tracking = _detect(_issue(), now=NOW).tracking
    moved = _detect(_issue(step="review"), tracking=tracking, now=NOW + timedelta(seconds=5400))
    assert moved.events == []


def test_open_triage_tab_still_detects_aborted() -> None:
    # 抑止 1 は triage の起動だけ。新しい失敗の event（M5 の記録・報告）は出す
    result = _detect(_issue(termination="ended", end_status="ERROR"), open_triage=frozenset({101}))
    assert [e.key for e in result.events] == ["101:aborted:261115000000"]


def test_open_triage_tab_suppresses_pane_idle_and_log_stall() -> None:
    tracking = _detect(_issue(), now=NOW).tracking
    later = NOW + timedelta(seconds=5400)
    idle = DetectTracking(
        pane_idle={101: PaneIdleCounter("261115000000", "implement", 1, idle_polls=5)},
        stall=tracking.stall,
    )
    opened = _detect(_issue(), pane="idle", tracking=idle, now=later, open_triage=frozenset({101}))
    assert opened.events == []
    closed = _detect(_issue(), pane="idle", tracking=idle, now=later)
    assert {e.kind for e in closed.events} == {"pane_idle", "log_stall"}


def test_suppressed_reports_handoff_until_progress_or_recovered() -> None:
    key = "101:pane_idle:261115000000:implement:1:0"
    progress = ("state:running", "261115000000", "implement", 1, 0)
    handoff = _record(key, "handoff", NOW, progress)
    ledger = Ledger(event_keys=frozenset({key}), records=(handoff,))
    assert _detect(_issue(), ledger=ledger).suppressed == frozenset({101})
    recovered = Ledger(
        event_keys=frozenset({key}),
        records=(handoff, _record(key, "recovered", NOW + timedelta(seconds=1), progress)),
    )
    assert _detect(_issue(), ledger=recovered).suppressed == frozenset()
    # 進み具合が変われば（run が失敗して aborted）抑止中ではない
    failed = _detect(_issue(termination="ended", end_status="ERROR"), ledger=ledger)
    assert failed.suppressed == frozenset()


def test_handoff_suppresses_until_progress_changes() -> None:
    key = "101:pane_idle:261115000000:implement:1:0"
    progress = ("state:running", "261115000000", "implement", 1, 0)
    ledger = Ledger(event_keys=frozenset({key}), records=(_record(key, "handoff", NOW, progress),))
    tracking = _detect(_issue(), now=NOW).tracking
    # 同じ進み具合の log_stall は抑止される（event の種類によらない）
    stalled = _detect(_issue(), tracking=tracking, ledger=ledger, now=NOW + timedelta(seconds=5400))
    assert stalled.events == []
    # run が失敗して進み具合が変われば解除
    failed = _detect(_issue(termination="ended", end_status="ERROR"), ledger=ledger)
    assert [e.kind for e in failed.events] == ["aborted"]
