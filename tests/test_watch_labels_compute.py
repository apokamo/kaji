"""Small tests: ``kaji_harness.watch.labels`` target computation (Issue #543)."""

from __future__ import annotations

from typing import Any

import pytest

from kaji_harness.watch.labels import (
    Anchor,
    TargetBasis,
    WatchIssue,
    compute_target,
    count_active,
    resolve_anchor,
    should_exit,
)

pytestmark = pytest.mark.small


def _run(
    run_id: str = "261115000000",
    termination: str | None = "ended",
    end_status: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "termination": termination,
        "end_status": end_status,
        "last_step": {"id": "implement", "attempt": 1},
        "pane": None,
    }


def _issue(
    labels: tuple[str, ...] = ("state:planned",),
    *,
    github_state: str = "open",
    state_reason: str | None = None,
    run: dict[str, Any] | None = None,
    alive: bool | None = False,
    launching: bool | None = False,
) -> WatchIssue:
    return WatchIssue(
        issue=101,
        github_state=github_state,
        state_reason=state_reason,
        state_labels=labels,
        status={
            "issue": "101",
            "latest_run": run,
            "any_run_alive": alive,
            "launching": launching,
            "read_errors": [],
        },
    )


_NO_ANCHOR = Anchor(run_id=None, inclusive=False, pending_member=False)


def test_closed_completed_is_done() -> None:
    target = compute_target(
        _issue(("state:running",), github_state="closed", state_reason="completed"), _NO_ANCHOR
    )
    assert target.label == "state:done"
    assert target.write is True


def test_closed_not_planned_clears() -> None:
    target = compute_target(
        _issue(("state:aborted",), github_state="closed", state_reason="not_planned"), _NO_ANCHOR
    )
    assert target.label is None
    assert target.write is True
    assert target.basis is TargetBasis.CLEAR


def test_alive_is_running() -> None:
    assert compute_target(_issue(alive=True, run=_run()), _NO_ANCHOR).label == "state:running"


def test_launching_only_is_running() -> None:
    # #1909 と同じ形: series の子が run dir を作る前の窓
    target = compute_target(_issue(alive=False, launching=True, run=None), _NO_ANCHOR)
    assert target.label == "state:running"


def test_alive_unknown_holds() -> None:
    target = compute_target(_issue(alive=None, run=_run()), _NO_ANCHOR)
    assert target.write is False
    assert target.hold_reason == "unknown_liveness"


def test_no_run_is_planned() -> None:
    assert compute_target(_issue(("state:running",), run=None), _NO_ANCHOR).label == (
        "state:planned"
    )


@pytest.mark.parametrize(
    ("termination", "end_status"),
    [("ended", "ABORT"), ("ended", "ERROR"), ("vanished", None)],
)
def test_failed_run_is_aborted_with_event_basis(termination: str, end_status: str | None) -> None:
    target = compute_target(
        _issue(("state:running",), run=_run(termination=termination, end_status=end_status)),
        _NO_ANCHOR,
    )
    assert target.label == "state:aborted"
    assert target.basis is TargetBasis.FAILED_RUN
    assert target.run_id == "261115000000"


def test_complete_open_is_aborted_without_event() -> None:
    target = compute_target(_issue(("state:running",), run=_run(end_status="COMPLETE")), _NO_ANCHOR)
    assert target.label == "state:aborted"
    assert target.basis is TargetBasis.COMPLETE_OPEN


@pytest.mark.parametrize("termination", ["unknown", None])
def test_unknown_termination_holds(termination: str | None) -> None:
    target = compute_target(_issue(run=_run(termination=termination)), _NO_ANCHOR)
    assert target.write is False
    assert target.hold_reason == "unknown_liveness"


@pytest.mark.parametrize(
    "labels", [("state:hold",), ("state:needs-maintainer",), ("state:planned", "state:hold")]
)
def test_user_labels_are_not_touched(labels: tuple[str, ...]) -> None:
    target = compute_target(_issue(labels, alive=True, run=_run()), _NO_ANCHOR)
    assert target.write is False
    assert target.hold_reason == "user_label"


def test_two_managed_labels_conflict() -> None:
    target = compute_target(_issue(("state:planned", "state:running"), alive=True), _NO_ANCHOR)
    assert target.write is False
    assert target.hold_reason == "conflict"


def test_pending_label_resolves_own_conflict() -> None:
    target = compute_target(
        _issue(("state:planned", "state:running"), alive=True), _NO_ANCHOR, pending=True
    )
    assert target.write is True
    assert target.label == "state:running"


def test_no_state_label_is_not_written() -> None:
    target = compute_target(_issue((), alive=True), _NO_ANCHOR)
    assert target.write is False


def test_anchor_excludes_older_runs() -> None:
    anchor = Anchor(run_id="261115100000", inclusive=True, pending_member=False)
    old_failed = _run(run_id="261115000000", end_status="ERROR")
    assert compute_target(_issue(run=old_failed), anchor).label == "state:planned"
    same = _run(run_id="261115100000", end_status="ERROR")
    assert compute_target(_issue(run=same), anchor).label == "state:aborted"


def test_baseline_anchor_is_exclusive() -> None:
    anchor = Anchor(run_id="261115100000", inclusive=False, pending_member=False)
    same = _run(run_id="261115100000", end_status="ERROR")
    assert compute_target(_issue(run=same), anchor).label == "state:planned"


def test_pending_member_counts_no_runs() -> None:
    anchor = Anchor(run_id=None, inclusive=True, pending_member=True)
    failed = _run(end_status="ERROR")
    assert compute_target(_issue(run=failed), anchor).label == "state:planned"
    assert compute_target(_issue(run=failed, alive=True), anchor).label == "state:running"


def test_reopened_done_without_new_run_is_kept() -> None:
    target = compute_target(
        _issue(("state:done",), run=_run(end_status="COMPLETE")),
        _NO_ANCHOR,
        reopen_baseline="261115000000",
    )
    assert target.write is False
    assert target.label is None


def test_reopened_done_with_alive_run_is_running() -> None:
    target = compute_target(
        _issue(("state:done",), run=_run(), alive=True),
        _NO_ANCHOR,
        reopen_baseline="261115000000",
    )
    assert target.label == "state:running"


def test_reopened_done_with_newer_failed_run_is_aborted() -> None:
    newer = _run(run_id="261115200000", end_status="ERROR")
    target = compute_target(
        _issue(("state:done",), run=newer), _NO_ANCHOR, reopen_baseline="261115000000"
    )
    assert target.label == "state:aborted"


def test_resolve_anchor_prefers_latest_series() -> None:
    series = [
        {
            "id": "old",
            "updated_at": "2026-11-14T00:00:00+00:00",
            "members": [{"issue": 101, "run_id": "261114000000"}],
        },
        {
            "id": "new",
            "updated_at": "2026-11-15T00:00:00+00:00",
            "members": [{"issue": 101, "run_id": "261115000000"}],
        },
    ]
    assert resolve_anchor(101, series, {}) == Anchor("261115000000", True, False)


def test_resolve_anchor_pending_member() -> None:
    series = [{"id": "s", "updated_at": None, "members": [{"issue": 101, "run_id": None}]}]
    assert resolve_anchor(101, series, {}) == Anchor(None, True, True)


def test_resolve_anchor_falls_back_to_baseline() -> None:
    assert resolve_anchor(101, [], {"101": "261115000000"}) == Anchor("261115000000", False, False)
    assert resolve_anchor(101, [], {"101": ""}) == Anchor(None, False, False)
    assert resolve_anchor(101, [], {}) == _NO_ANCHOR


def test_count_active_excludes_kept_done() -> None:
    planned = _issue(("state:planned",))
    kept_done = _issue(("state:done",))
    targets = {
        101: compute_target(planned, _NO_ANCHOR),
    }
    assert count_active([planned], targets) == 1
    assert count_active([kept_done], {101: compute_target(kept_done, _NO_ANCHOR, "x")}) == 0


def test_should_exit_requires_two_empty_polls_and_no_pending() -> None:
    # #1910 と同じ形: 1 周目に 0 件でも終了しない
    assert should_exit(empty_polls=1, has_pending=False) is False
    assert should_exit(empty_polls=2, has_pending=False) is True
    assert should_exit(empty_polls=5, has_pending=True) is False
