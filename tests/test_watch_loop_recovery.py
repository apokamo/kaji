"""Medium tests: ``run_once`` sequences with automatic recovery records (Issue #545)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.errors import AutoRecoveryLimitReached
from kaji_harness.watch import loop, service
from kaji_harness.watch.labels import WatchIssue
from kaji_harness.watch.loop import Facts, WatchDeps, run_once
from kaji_harness.watch.store import WatchStore
from tests.runstate_helpers import end, start, step_start
from tests.test_watch_loop import (
    NOW,
    FakeHerdr,
    FakeIssue,
    FakeProvider,
    _deps,
    _notify_script,
    _write_ended_run,
)

pytestmark = pytest.mark.medium

RUN = "261115000000"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now = self.now + timedelta(minutes=minutes)


def _running(status: str) -> Facts:
    issue = WatchIssue(
        101,
        "open",
        None,
        ("state:running",),
        {
            "issue": "101",
            "any_run_alive": True,
            "launching": False,
            "read_errors": [],
            "latest_run": {
                "run_id": RUN,
                "termination": "running",
                "last_step": {"id": "implement", "attempt": 1},
                "pane": {"pane_id": "p_step", "origin_pane": "p_o"},
            },
        },
    )
    return Facts([issue], [], [], {101: status}, NOW.isoformat())


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notify: Path | None = None
) -> tuple[WatchDeps, FakeHerdr, Clock, dict[str, Any]]:
    herdr = FakeHerdr()
    clock = Clock()
    deps = replace(
        _deps(tmp_path, FakeProvider({101: FakeIssue(labels={"state:running"})}), herdr, notify),
        now=clock,
    )
    current: dict[str, Any] = {"facts": _running("idle")}

    def facts(*_: object) -> Facts:
        tabs = herdr.list_tabs()
        return replace(current["facts"], tabs=tabs)

    monkeypatch.setattr(loop, "collect_facts", facts)
    return deps, herdr, clock, current


def _close_triage(herdr: FakeHerdr) -> None:
    herdr.tabs = [t for t in herdr.tabs if not t.label.startswith("watch-triage-")]


def _event_path(store: WatchStore, key: str) -> Path:
    return Path(store.load_state().events[key].event_file)


def test_pane_idle_wait_delays_then_reemits_with_waited_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, herdr, clock, _ = _setup(tmp_path, monkeypatch)
    run_once(deps)
    clock.advance(3)
    run_once(deps)
    key = f"101:pane_idle:{RUN}:implement:1:0"
    assert len(herdr.runs) == 1
    event = json.loads(_event_path(deps.store, key).read_text(encoding="utf-8"))
    assert event["anchor"] == "none"
    until = clock.now + timedelta(minutes=60)
    service.record(
        deps,
        _event_path(deps.store, key),
        "wait",
        comment_url=None,
        summary=None,
        reason="usage limit",
        until=until.isoformat(),
    )
    _close_triage(herdr)
    for _ in range(3):
        clock.advance(15)
        run_once(deps)
    assert len(herdr.runs) == 1
    clock.advance(20)
    run_once(deps)
    state = deps.store.load_state()
    assert f"{key}:w1" in state.events
    assert len(herdr.runs) == 2
    reemitted = json.loads(_event_path(deps.store, f"{key}:w1").read_text(encoding="utf-8"))
    assert reemitted["waited_until"] == until.isoformat()


def test_pane_working_before_until_detects_new_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, herdr, clock, current = _setup(tmp_path, monkeypatch)
    run_once(deps)
    clock.advance(3)
    run_once(deps)
    key = f"101:pane_idle:{RUN}:implement:1:0"
    service.record(
        deps,
        _event_path(deps.store, key),
        "wait",
        comment_url=None,
        summary=None,
        reason="usage limit",
        until=(clock.now + timedelta(hours=2)).isoformat(),
    )
    _close_triage(herdr)
    current["facts"] = _running("working")
    clock.advance(3)
    run_once(deps)
    current["facts"] = _running("idle")
    for _ in range(2):
        clock.advance(3)
        run_once(deps)
    assert f"101:pane_idle:{RUN}:implement:1:1" in deps.store.load_state().events


def _completed() -> Facts:
    facts = _running("idle")
    issue = facts.issues[0]
    run = dict(issue.status["latest_run"])
    run.update(termination="ended", end_status="COMPLETE", pane=None)
    status = {**issue.status, "any_run_alive": False, "latest_run": run}
    return replace(facts, issues=[replace(issue, status=status)], pane_status={101: None})


def test_pane_idle_wait_then_complete_does_not_relaunch_triage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MF-2: wait 中に同じ run が COMPLETE で終わったら、予定時刻の後に pane_idle を出し直さない
    deps, herdr, clock, current = _setup(tmp_path, monkeypatch)
    run_once(deps)
    clock.advance(3)
    run_once(deps)
    key = f"101:pane_idle:{RUN}:implement:1:0"
    service.record(
        deps,
        _event_path(deps.store, key),
        "wait",
        comment_url=None,
        summary=None,
        reason="usage limit",
        until=(clock.now + timedelta(minutes=30)).isoformat(),
    )
    _close_triage(herdr)
    current["facts"] = _completed()
    for _ in range(3):
        clock.advance(15)
        run_once(deps)
    assert sorted(deps.store.load_state().events) == [key]
    assert len(herdr.runs) == 1


def test_aborted_wait_reports_once_and_launches_after_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, RUN, [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    clock = Clock()
    script, out = _notify_script(tmp_path)
    deps = replace(_deps(tmp_path, provider, herdr, script), now=clock)
    run_once(deps)
    key = f"101:aborted:{RUN}"
    assert provider.comment_calls == 1
    until = clock.now + timedelta(minutes=30)
    service.record(
        deps,
        _event_path(deps.store, key),
        "wait",
        comment_url=None,
        summary=None,
        reason="usage limit",
        until=until.isoformat(),
    )
    _close_triage(herdr)
    clock.advance(10)
    run_once(deps)
    assert len(herdr.runs) == 1
    clock.advance(25)
    run_once(deps)
    assert f"{key}:w1" in deps.store.load_state().events
    assert len(herdr.runs) == 2
    assert provider.comment_calls == 1
    kinds = [
        json.loads(line)["kind"] for line in out.read_text(encoding="utf-8").split("\n") if line
    ]
    assert kinds == ["aborted"]


def test_recovered_between_polls_then_limit_then_handoff_suppresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, herdr, clock, _ = _setup(tmp_path, monkeypatch)
    run_once(deps)
    keys: list[str] = []
    for episode in range(3):
        clock.advance(3)
        run_once(deps)
        key = f"101:pane_idle:{RUN}:implement:1:{episode}"
        assert key in deps.store.load_state().events
        keys.append(key)
        path = _event_path(deps.store, key)
        service.record(deps, path, "continue-sent", comment_url=None, summary=None, reason="cap")
        service.record(deps, path, "recovered", comment_url=None, summary="continued")
        _close_triage(herdr)
        clock.advance(1)
        run_once(deps)  # recovered を反映（idle 1 周目）
    clock.advance(3)
    run_once(deps)
    fourth = f"101:pane_idle:{RUN}:implement:1:3"
    assert fourth in deps.store.load_state().events
    path = _event_path(deps.store, fourth)
    with pytest.raises(AutoRecoveryLimitReached):
        service.record(deps, path, "continue-sent", comment_url=None, summary=None, reason="cap")
    service.record(deps, path, "handoff", comment_url=None, summary="limit")
    _close_triage(herdr)
    launched = len(herdr.runs)
    for _ in range(3):
        clock.advance(3)
        run_once(deps)
    assert len(herdr.runs) == launched
    assert len(keys) == 3
