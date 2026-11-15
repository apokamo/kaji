"""Medium tests: one poll of ``kaji watch`` (Issue #543 M4・M5・UC4)."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaji_harness.providers import Comment, Issue, Label
from kaji_harness.watch.herdr import TabRef
from kaji_harness.watch.loop import WatchDeps, run_once
from kaji_harness.watch.store import WatchStore
from tests.runstate_helpers import end, record, start, step_start, write_run

pytestmark = pytest.mark.medium

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
#: 存在しない pid（同じ host・boot の生存記録で、生存判定が ``False`` に確定する）
DEAD_PID = 4194000


def _write_ended_run(artifacts: Path, run_id: str, events: list[dict[str, object]]) -> None:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    process = record(DEAD_PID, host=socket.gethostname(), boot=boot, run_id=run_id)
    write_run(artifacts, "101", run_id, events, process=process)


@dataclass
class FakeIssue:
    state: str = "open"
    state_reason: str = ""
    labels: set[str] = field(default_factory=set)
    comments: list[Comment] = field(default_factory=list)


class FakeProvider:
    """GitHub provider の fake（ラベル別の一覧・ラベル編集・コメント）。"""

    def __init__(self, issues: dict[int, FakeIssue]) -> None:
        self.issues = issues
        self.edits: list[tuple[str, list[str] | None, list[str] | None]] = []
        self.comment_calls = 0
        self.fail_comment = False

    def _issue(self, number: int) -> Issue:
        data = self.issues[number]
        return Issue(
            id=str(number),
            title="t",
            body="",
            state=data.state,
            labels=[Label(name=n) for n in sorted(data.labels)],
            state_reason=data.state_reason,
        )

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        return [
            self._issue(n)
            for n, data in self.issues.items()
            if set(labels) <= data.labels and (state == "all" or data.state == state)
        ]

    def view_issue(self, issue_id: str) -> Issue:
        return self._issue(int(issue_id))

    def edit_issue(
        self,
        issue_id: str,
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        **_: object,
    ) -> Issue:
        data = self.issues[int(issue_id)]
        data.labels |= set(add_labels or [])
        data.labels -= set(remove_labels or [])
        self.edits.append((issue_id, add_labels, remove_labels))
        return self._issue(int(issue_id))

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        self.comment_calls += 1
        if self.fail_comment:
            raise RuntimeError("gh issue comment failed")
        ref = f"https://github.com/o/r/issues/{issue_id}#issuecomment-{self.comment_calls}"
        comment = Comment(author="", body=body, created_at="", ref=ref)
        self.issues[int(issue_id)].comments.append(comment)
        return comment

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        return list(self.issues[int(issue_id)].comments)


class FakeHerdr:
    """Herdr の fake（タブ作成・pane run を記録する）。"""

    def __init__(self) -> None:
        self.tabs: list[TabRef] = []
        self.runs: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self.foreground: dict[str, tuple[str, list[str]]] = {}

    def list_tabs(self) -> list[TabRef]:
        return list(self.tabs)

    def create_tab(self, label: str, cwd: Path) -> tuple[str, str]:
        number = len(self.tabs) + 1
        self.tabs.append(TabRef(f"t{number}", f"p_{number}", label))
        return f"t{number}", f"p_{number}"

    def run_in_pane(self, pane_id: str, command: str) -> None:
        self.runs.append((pane_id, command))

    def close_tab(self, tab_id: str) -> None:
        self.closed.append(tab_id)
        self.tabs = [t for t in self.tabs if t.tab_id != tab_id]

    def pane_foreground(self, pane_id: str) -> tuple[str, list[str]]:
        return self.foreground.get(pane_id, ("unknown", []))

    def agent_status(self, pane_id: str, origin_pane: str | None, run_id: str | None) -> str:
        return "working"


def _deps(
    tmp_path: Path, provider: FakeProvider, herdr: FakeHerdr | None, notify: Path | None = None
) -> WatchDeps:
    return WatchDeps(
        provider=provider,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        repo_root=tmp_path,
        repo="o/r",
        notify_command=(str(notify),) if notify else None,
        herdr=herdr,
        now=lambda: NOW,
    )


def _notify_script(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "notified.jsonl"
    script = tmp_path / "notify.sh"
    script.write_text(f'#!/usr/bin/env bash\ncat >> "{out}"\necho >> "{out}"\n', encoding="utf-8")
    script.chmod(0o755)
    return script, out


def test_converges_and_second_poll_does_not_write(tmp_path: Path) -> None:
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    deps = _deps(tmp_path, provider, FakeHerdr())
    run_once(deps)
    assert provider.issues[101].labels == {"state:planned"}
    edits = len(provider.edits)
    run_once(deps)
    assert len(provider.edits) == edits


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    deps = _deps(tmp_path, provider, None)
    result = run_once(deps, dry_run=True)
    assert provider.edits == []
    assert not (tmp_path / ".kaji-artifacts" / "_watch").exists()
    assert result.plan["herdr"] == "unavailable"
    assert result.plan["issues"][0]["target_label"] == "state:planned"
    assert result.plan["schema_version"] == 1


def test_aborted_reports_once_launches_triage_and_notifies(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    script, out = _notify_script(tmp_path)
    deps = _deps(tmp_path, provider, herdr, script)
    run_once(deps)
    assert provider.issues[101].labels == {"state:aborted"}
    assert provider.comment_calls == 1
    assert (
        provider.issues[101]
        .comments[0]
        .body.startswith("<!-- kaji-watch: aborted key=101:aborted:261115000000 -->")
    )
    assert [label for label in (t.label for t in herdr.tabs)] == ["watch-triage-101"]
    assert "/watch-triage" in herdr.runs[0][1]
    event_file = artifacts / "_watch" / "events" / "101-aborted-261115000000.json"
    event = json.loads(event_file.read_text(encoding="utf-8"))
    assert event["schema_version"] == 1
    assert event["artifacts"]["run_dir"] == str(artifacts / "101" / "runs" / "261115000000")
    assert event["status_document"]["issues"][0]["latest_run"]["end_status"] == "ERROR"
    payloads = [json.loads(line) for line in out.read_text(encoding="utf-8").split("\n") if line]
    assert [p["kind"] for p in payloads] == ["aborted"]
    # 2 周目・再起動後もコメント・起動・通知を繰り返さない
    run_once(deps)
    run_once(_deps(tmp_path, provider, herdr, script))
    assert provider.comment_calls == 1
    assert len(herdr.runs) == 1
    assert len([line for line in out.read_text(encoding="utf-8").split("\n") if line]) == 1


def test_comment_failure_retries_next_poll_with_marker_check(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ABORT")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    provider.fail_comment = True
    deps = _deps(tmp_path, provider, FakeHerdr())
    run_once(deps)
    assert WatchStore(artifacts).load_state().pending_reports == {"101": "101:aborted:261115000000"}
    provider.fail_comment = False
    run_once(deps)
    assert len(provider.issues[101].comments) == 1
    assert WatchStore(artifacts).load_state().pending_reports == {}


def test_complete_open_is_aborted_without_event(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), end("COMPLETE")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    run_once(_deps(tmp_path, provider, herdr))
    assert provider.issues[101].labels == {"state:aborted"}
    assert provider.comment_calls == 0
    assert herdr.runs == []


def test_closed_completed_becomes_done_and_exit_after_two_empty_polls(tmp_path: Path) -> None:
    script, out = _notify_script(tmp_path)
    provider = FakeProvider(
        {101: FakeIssue(state="closed", state_reason="completed", labels={"state:running"})}
    )
    deps = _deps(tmp_path, provider, FakeHerdr(), script)
    first = run_once(deps)
    assert provider.issues[101].labels == {"state:done"}
    assert first.exit is False
    second = run_once(deps)
    assert second.exit is True
    payloads = [json.loads(line) for line in out.read_text(encoding="utf-8").split("\n") if line]
    assert payloads[-1]["kind"] == "watch_exit"


def test_label_failure_mid_way_is_fixed_next_poll(tmp_path: Path) -> None:
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    original = provider.edit_issue
    calls = {"n": 0}

    def flaky(issue_id: str, **kwargs: object) -> Issue:
        calls["n"] += 1
        if calls["n"] == 2:  # 付けた直後（外す前）に失敗
            raise RuntimeError("boom")
        return original(issue_id, **kwargs)  # type: ignore[arg-type]

    provider.edit_issue = flaky  # type: ignore[method-assign]
    deps = _deps(tmp_path, provider, FakeHerdr())
    run_once(deps)
    assert provider.issues[101].labels == {"state:running", "state:planned"}
    assert WatchStore(tmp_path / ".kaji-artifacts").load_state().pending_labels == {
        "101": "state:planned"
    }
    run_once(deps)
    assert provider.issues[101].labels == {"state:planned"}


def _context(tmp_path: Path) -> object:
    from kaji_harness.config import KajiConfig
    from kaji_harness.watch.preflight import WatchContext
    from tests.runstate_helpers import write_config

    write_config(tmp_path)
    path = tmp_path / ".kaji" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[watch]\n", encoding="utf-8")
    config = KajiConfig.discover(start_dir=tmp_path)
    assert config.watch is not None
    return WatchContext(
        config=config,
        watch=config.watch,
        provider=FakeProvider({}),  # type: ignore[arg-type]
        repo="o/r",
        herdr_bin="/usr/bin/herdr",
        herdr_pane_id="p_1",
    )


def test_start_creates_tab_then_reuses_shell_pane(outside_project_tmp_path: Path) -> None:
    from kaji_harness.watch import service

    context = _context(outside_project_tmp_path)
    herdr = FakeHerdr()
    line = service.start(context, herdr)  # type: ignore[arg-type]
    assert line == "watch started (pane p_1)"
    assert [t.label for t in herdr.tabs] == ["kaji-watch"]
    assert "kaji_harness.watch" in herdr.runs[0][1]
    herdr.foreground["p_1"] = ("shell_only", ["bash"])
    service.start(context, herdr)  # type: ignore[arg-type]
    assert len(herdr.tabs) == 1
    assert [run[0] for run in herdr.runs] == ["p_1", "p_1"]


def test_run_loop_exits_after_empty_polls(tmp_path: Path) -> None:
    from kaji_harness.watch import service

    provider = FakeProvider({})
    sleeps: list[int] = []
    line = service.run_loop(
        _deps(tmp_path, provider, FakeHerdr()), pane_id="p_1", sleep=sleeps.append
    )
    assert line == "watch finished"
    assert sleeps == [180]
    assert WatchStore(tmp_path / ".kaji-artifacts").is_locked() is False


def _aborted_setup(tmp_path: Path) -> tuple[FakeProvider, FakeHerdr, WatchDeps]:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    return provider, herdr, _deps(tmp_path, provider, herdr)


def test_launching_with_claude_in_tab_is_reconciled_without_relaunch(tmp_path: Path) -> None:
    # pane run の後・launched の保存前に落ちた: 照合で launched になり二重起動しない
    provider, herdr, deps = _aborted_setup(tmp_path)
    original = herdr.run_in_pane

    def crash_after_run(pane_id: str, command: str) -> None:
        original(pane_id, command)
        raise KeyboardInterrupt

    herdr.run_in_pane = crash_after_run  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_once(deps)
    store = WatchStore(tmp_path / ".kaji-artifacts")
    assert store.load_state().events["101:aborted:261115000000"].launch_status == "launching"
    herdr.run_in_pane = original  # type: ignore[method-assign]
    herdr.foreground["p_1"] = ("active", ["claude --dangerously-skip-permissions /watch-triage x"])
    run_once(deps)
    assert store.load_state().events["101:aborted:261115000000"].launch_status == "launched"
    assert len(herdr.runs) == 1


def test_launching_with_shell_only_tab_closes_and_relaunches(tmp_path: Path) -> None:
    # tab create の後・pane run の前に落ちた: シェルだけのタブを閉じて起動し直す
    provider, herdr, deps = _aborted_setup(tmp_path)

    def crash(pane_id: str, command: str) -> None:
        raise KeyboardInterrupt

    original = herdr.run_in_pane
    herdr.run_in_pane = crash  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_once(deps)
    herdr.run_in_pane = original  # type: ignore[method-assign]
    herdr.foreground["p_1"] = ("shell_only", ["bash"])
    run_once(deps)
    assert herdr.closed == ["t1"]
    assert len(herdr.runs) == 1
    state = WatchStore(tmp_path / ".kaji-artifacts").load_state()
    assert state.events["101:aborted:261115000000"].launch_status == "launched"


def test_launch_failures_reach_launch_failed(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)

    def fail(label: str, cwd: Path) -> tuple[str, str]:
        raise RuntimeError("herdr down")

    herdr.create_tab = fail  # type: ignore[method-assign]
    for _ in range(4):
        run_once(deps)
    event = WatchStore(tmp_path / ".kaji-artifacts").load_state().events["101:aborted:261115000000"]
    assert event.launch_status == "launch_failed"
    assert event.launch_attempts == 3


def test_reopened_done_without_run_is_kept(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), end("COMPLETE")])
    provider = FakeProvider({101: FakeIssue(labels={"state:done"})})
    deps = _deps(tmp_path, provider, FakeHerdr())
    first = run_once(deps)
    assert provider.edits == []
    assert provider.issues[101].labels == {"state:done"}
    run_once(deps)
    # 終了判定の件数に数えない（M6）→ 2 周で終了
    assert first.exit is False


def test_reopened_done_with_new_failed_run_becomes_aborted(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), end("COMPLETE")])
    provider = FakeProvider({101: FakeIssue(labels={"state:done"})})
    deps = _deps(tmp_path, provider, FakeHerdr())
    run_once(deps)
    _write_ended_run(artifacts, "261115010000", [start(), step_start("implement"), end("ERROR")])
    run_once(deps)
    assert provider.issues[101].labels == {"state:aborted"}


# M4: 永続化の順序（境界ごとの中断と再起動）


def test_crash_right_after_label_switch_launches_triage_next_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kaji_harness.watch import loop

    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    deps = _deps(tmp_path, provider, herdr)

    def crash(*_: object, **__: object) -> None:
        raise RuntimeError("injected: after label switch")

    monkeypatch.setattr(loop, "_launch_triage", crash)
    with pytest.raises(RuntimeError, match="after label switch"):
        run_once(deps)
    assert provider.issues[101].labels == {"state:aborted"}
    assert herdr.runs == []
    monkeypatch.undo()

    run_once(_deps(tmp_path, provider, herdr))
    assert [t.label for t in herdr.tabs] == ["watch-triage-101"]
    assert len(herdr.runs) == 1 and "/watch-triage" in herdr.runs[0][1]


def test_crash_after_report_saved_before_notify_does_not_notify_or_repost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kaji_harness.watch import loop

    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    script, out = _notify_script(tmp_path)

    def crash(*_: object, **__: object) -> None:
        raise RuntimeError("injected: after save, before notify")

    monkeypatch.setattr(loop, "send_notification", crash)
    with pytest.raises(RuntimeError, match="before notify"):
        run_once(_deps(tmp_path, provider, herdr, script))
    monkeypatch.undo()

    run_once(_deps(tmp_path, provider, herdr, script))
    assert provider.comment_calls == 1
    assert len(provider.issues[101].comments) == 1
    assert not out.exists()


def test_new_run_failed_while_watch_stopped_is_detected_on_first_poll(tmp_path: Path) -> None:
    artifacts = tmp_path / ".kaji-artifacts"
    _write_ended_run(artifacts, "261115000000", [start(), step_start("implement"), end("ERROR")])
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    herdr = FakeHerdr()
    run_once(_deps(tmp_path, provider, herdr))
    events_dir = artifacts / "_watch" / "events"
    assert (events_dir / "101-aborted-261115000000.json").is_file()

    # 保守者が triage タブを閉じ、watch の停止中に retry した run B も失敗した
    herdr.tabs = []
    _write_ended_run(artifacts, "261115010000", [start(), step_start("implement"), end("ERROR")])
    run_once(_deps(tmp_path, provider, herdr))
    assert (events_dir / "101-aborted-261115010000.json").is_file()
    assert provider.issues[101].labels == {"state:aborted"}


# review-code R1〜R6 の回帰（triage 起動の照合・抑止・status 表示）


def _real_shaped(herdr: FakeHerdr) -> None:
    """Herdr 0.9.5 の ``tab list`` と同じく pane ID を持たない行を返すようにする。"""
    from kaji_harness.watch.herdr import parse_tabs

    def list_tabs() -> list[TabRef]:
        return parse_tabs(
            [
                {
                    "tab_id": t.tab_id,
                    "label": t.label,
                    "workspace_id": "w",
                    "number": i + 1,
                    "pane_count": 1,
                    "agent_status": "working",
                    "focused": False,
                }
                for i, t in enumerate(herdr.tabs)
            ]
        )

    herdr.list_tabs = list_tabs  # type: ignore[method-assign]


def test_r1_real_shaped_tab_rows_do_not_duplicate_launch_after_crash(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)
    _real_shaped(herdr)
    original = herdr.run_in_pane

    def crash_after_run(pane_id: str, command: str) -> None:
        original(pane_id, command)
        raise KeyboardInterrupt

    herdr.run_in_pane = crash_after_run  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_once(deps)
    herdr.run_in_pane = original  # type: ignore[method-assign]
    herdr.foreground["p_1"] = ("active", ["claude --dangerously-skip-permissions /watch-triage x"])
    run_once(deps)
    event = deps.store.load_state().events["101:aborted:261115000000"]
    assert (event.launch_status, event.launch_attempts) == ("launched", 1)
    assert len(herdr.tabs) == 1 and len(herdr.runs) == 1


def test_r2_new_failure_while_triage_tab_open_is_recorded_and_reported(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)
    run_once(deps)
    assert [t.label for t in herdr.tabs] == ["watch-triage-101"]
    # run A の triage タブを開いたまま、retry した run B も失敗した
    _write_ended_run(
        deps.artifacts_dir, "261115010000", [start(), step_start("implement"), end("ERROR")]
    )
    run_once(deps)
    state = deps.store.load_state()
    assert set(state.events) == {"101:aborted:261115000000", "101:aborted:261115010000"}
    assert provider.comment_calls == 2
    assert "key=101:aborted:261115010000" in provider.issues[101].comments[-1].body
    # 追加の triage 起動はタブが開いている間は抑止する
    assert len(herdr.tabs) == 1 and len(herdr.runs) == 1
    assert state.events["101:aborted:261115010000"].launch_status == "detected"
    # タブを閉じたら次の周に起動する
    herdr.tabs = []
    run_once(deps)
    assert deps.store.load_state().events["101:aborted:261115010000"].launch_status == "launched"
    assert len(herdr.runs) == 2


def _running_facts() -> object:
    from kaji_harness.watch.labels import WatchIssue
    from kaji_harness.watch.loop import Facts

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
                "run_id": "261115000000",
                "termination": "running",
                "last_step": {"id": "implement", "attempt": 1},
                "pane": {"pane_id": "p_step", "origin_pane": "p_o"},
            },
        },
    )
    return Facts([issue], [], [], {101: "idle"}, NOW.isoformat())


def test_r4_pane_idle_and_log_stall_in_same_poll_launch_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from kaji_harness.watch import loop
    from kaji_harness.watch.models import PaneIdleState, StallState, WatchState

    herdr = FakeHerdr()
    provider = FakeProvider({101: FakeIssue(labels={"state:running"})})
    deps = _deps(tmp_path, provider, herdr)
    deps.store.save_state(
        WatchState(
            pane_idle={
                "101": PaneIdleState(
                    run_id="261115000000", step="implement", attempt=1, idle_polls=1
                )
            },
            stall={
                "101": StallState(
                    signature=["261115000000", "implement", "1", "running"],
                    since=(NOW - timedelta(hours=2)).isoformat(),
                )
            },
        )
    )
    facts = _running_facts()
    monkeypatch.setattr(loop, "collect_facts", lambda *_: facts)
    run_once(deps)
    events = deps.store.load_state().events.values()
    assert sorted(e.kind for e in events) == ["log_stall", "pane_idle"]
    assert sorted(e.launch_status for e in events) == ["detected", "launched"]
    assert [t.label for t in herdr.tabs] == ["watch-triage-101"]
    assert len(herdr.runs) == 1


def test_r5_pane_run_error_is_retried_on_own_tab(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)
    original = herdr.run_in_pane

    def fail(pane_id: str, command: str) -> None:
        raise RuntimeError("injected pane run error")

    herdr.run_in_pane = fail  # type: ignore[method-assign]
    run_once(deps)
    event = deps.store.load_state().events["101:aborted:261115000000"]
    assert (event.launch_status, event.triage_tab) == ("detected", "t1")
    herdr.run_in_pane = original  # type: ignore[method-assign]
    herdr.foreground["p_1"] = ("shell_only", ["bash"])
    run_once(deps)
    event = deps.store.load_state().events["101:aborted:261115000000"]
    assert herdr.closed == ["t1"]
    assert (event.launch_status, event.launch_attempts) == ("launched", 2)
    assert len(herdr.tabs) == 1 and len(herdr.runs) == 1


def test_r5_lost_create_tab_response_is_reconciled(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)
    original = herdr.create_tab

    def lost(label: str, cwd: Path) -> tuple[str, str]:
        original(label, cwd)
        raise RuntimeError("response lost")

    herdr.create_tab = lost  # type: ignore[method-assign]
    run_once(deps)
    event = deps.store.load_state().events["101:aborted:261115000000"]
    assert (event.launch_status, event.triage_tab) == ("detected", None)
    herdr.create_tab = original  # type: ignore[method-assign]
    herdr.foreground["p_1"] = ("shell_only", ["bash"])
    run_once(deps)
    event = deps.store.load_state().events["101:aborted:261115000000"]
    assert herdr.closed == ["t1"]
    assert event.launch_status == "launched"
    assert [t.label for t in herdr.tabs] == ["watch-triage-101"] and len(herdr.runs) == 1


def test_r5_tab_of_other_launched_event_still_suppresses(tmp_path: Path) -> None:
    provider, herdr, deps = _aborted_setup(tmp_path)
    run_once(deps)
    _write_ended_run(
        deps.artifacts_dir, "261115010000", [start(), step_start("implement"), end("ERROR")]
    )
    herdr.foreground["p_1"] = ("shell_only", ["bash"])
    for _ in range(3):
        run_once(deps)
    # run A の triage タブは run A の event のもの。run B の event はそれを閉じない
    assert herdr.closed == []
    assert deps.store.load_state().events["101:aborted:261115010000"].launch_attempts == 0


def test_r6_status_reflects_recovered_and_open_tabs(tmp_path: Path) -> None:
    from kaji_harness.watch import service

    provider, herdr, deps = _aborted_setup(tmp_path)
    run_once(deps)
    event = next(iter(deps.store.load_state().events.values()))
    service.record(deps, Path(event.event_file), "handoff", comment_url=None, summary="h")
    shown = service.status(deps)["issues"][0]
    assert shown["suppressed_until_progress"] is True
    assert shown["triage_tab"] == "p_1"
    service.record(deps, Path(event.event_file), "recovered", comment_url=None, summary="r")
    herdr.tabs = []
    shown = service.status(deps)["issues"][0]
    assert shown["suppressed_until_progress"] is False
    assert shown["triage_tab"] is None
