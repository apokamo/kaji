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
