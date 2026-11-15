"""Medium tests: ``kaji watch`` CLI exit codes, lock and record (Issue #543 D3・M7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.commands.main import main
from kaji_harness.providers import Issue, Label
from kaji_harness.watch import preflight, service
from kaji_harness.watch.constants import CONTRACT_LABELS
from kaji_harness.watch.models import WatchEventFile
from kaji_harness.watch.store import WatchStore
from tests.runstate_helpers import write_config

pytestmark = pytest.mark.medium


class FakeProvider:
    def __init__(self, labels: tuple[str, ...] = CONTRACT_LABELS) -> None:
        self.labels = labels
        self.writes: list[str] = []

    def list_labels(self) -> list[Label]:
        return [Label(name=name) for name in self.labels]

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        return []

    def view_issue(self, issue_id: str) -> Issue:
        raise AssertionError("not expected")

    def edit_issue(self, issue_id: str, **_: object) -> Issue:
        self.writes.append(issue_id)
        raise AssertionError("not expected")

    def comment_issue(self, issue_id: str, body: str) -> object:
        self.writes.append(issue_id)
        raise AssertionError("not expected")

    def list_issue_comments_all(self, issue_id: str) -> list[object]:
        return []


@pytest.fixture
def repo(outside_project_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_config(outside_project_tmp_path)
    for name in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_BIN_PATH"):
        monkeypatch.delenv(name, raising=False)
    return outside_project_tmp_path


def _declare(repo: Path) -> None:
    path = repo / ".kaji" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[watch]\n", encoding="utf-8")


def _provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> FakeProvider:
    monkeypatch.setattr(preflight, "get_provider", lambda config: provider)
    return provider


def _herdr_env(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = repo / "fake-herdr"
    fake.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "p_1")
    monkeypatch.setenv("HERDR_BIN_PATH", str(fake))


@pytest.mark.parametrize(
    "argv",
    [
        ["watch", "status"],
        ["watch", "events"],
        ["watch", "once", "--dry-run"],
        ["watch", "start"],
    ],
)
def test_without_watch_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    _provider(monkeypatch, FakeProvider())
    assert main([*argv, "--workdir", str(repo)]) == 2


@pytest.mark.parametrize("argv", [["watch", "status"], ["watch", "events"]])
def test_missing_labels_exit_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider(labels=("state:planned",)))
    assert main([*argv, "--workdir", str(repo)]) == 2


def test_once_outside_herdr_exit_2_and_writes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _declare(repo)
    provider = _provider(monkeypatch, FakeProvider())
    assert main(["watch", "once", "--workdir", str(repo)]) == 2
    assert provider.writes == []
    assert not (repo / ".kaji-artifacts" / "_watch").exists()


def test_start_outside_herdr_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    assert main(["watch", "start", "--workdir", str(repo)]) == 2


def test_dry_run_outside_herdr_reports_unavailable(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    assert main(["watch", "once", "--dry-run", "--workdir", str(repo)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["herdr"] == "unavailable"
    assert not (repo / ".kaji-artifacts" / "_watch").exists()


@pytest.mark.parametrize(
    "argv",
    [["watch", "once", "--dry-run"], ["watch", "status", "--json"]],
)
def test_read_only_commands_observe_herdr_inside_herdr(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """status / once --dry-run は Herdr の中なら pane を観測する（PR #576 review）。"""
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    _herdr_env(repo, monkeypatch)
    seen: list[str | None] = []

    class ListOnlyHerdr:
        def list_tabs(self) -> list[object]:
            return []

    def fake_default_herdr(context: object) -> ListOnlyHerdr:
        seen.append(getattr(context, "herdr_bin", None))
        return ListOnlyHerdr()

    monkeypatch.setattr(service, "default_herdr", fake_default_herdr)
    assert main([*argv, "--workdir", str(repo)]) == 0
    assert seen == [str(repo / "fake-herdr")]
    document = json.loads(capsys.readouterr().out)
    if argv[1] == "once":
        assert document["herdr"] == "available"
    assert not (repo / ".kaji-artifacts" / "_watch").exists()


def test_status_json_schema(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    assert main(["watch", "status", "--json", "--workdir", str(repo)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 1
    assert set(document) >= {"generated_at", "watcher", "issues", "pending_labels"}
    assert document["watcher"]["running"] is False


def test_once_with_lock_held_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    _herdr_env(repo, monkeypatch)
    holder = WatchStore(repo / ".kaji-artifacts")
    holder.acquire_lock("p_12")
    try:
        assert main(["watch", "once", "--workdir", str(repo)]) == 2
    finally:
        holder.release_lock()


def test_start_with_lock_held_exit_0(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    _herdr_env(repo, monkeypatch)
    holder = WatchStore(repo / ".kaji-artifacts")
    holder.acquire_lock("p_12")
    try:
        assert main(["watch", "start", "--workdir", str(repo)]) == 0
    finally:
        holder.release_lock()
    assert "watch already running (pane p_12)" in capsys.readouterr().out


def _event(repo: Path, kind: str) -> Path:
    store = WatchStore(repo / ".kaji-artifacts")
    return store.write_event(
        WatchEventFile(key=f"101:{kind}:1", kind=kind, issue=101, detected_at="t")  # type: ignore[arg-type]
    )


def test_record_outcome_kind_mismatch_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    event = _event(repo, "aborted")
    argv = ["watch", "record", "--event", str(event), "--outcome", "still-working"]
    assert main([*argv, "--workdir", str(repo)]) == 2
    argv = ["watch", "record", "--event", str(event), "--outcome", "not-stalled"]
    assert main([*argv, "--workdir", str(repo)]) == 2


def test_record_appends_and_events_lists(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    event = _event(repo, "pane_idle")
    argv = ["watch", "record", "--event", str(event), "--outcome", "still-working"]
    assert main([*argv, "--workdir", str(repo)]) == 0
    capsys.readouterr()
    assert main(["watch", "events", "--json", "--workdir", str(repo)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 1
    assert document["records"][0]["outcome"] == "still-working"
    assert document["records"][0]["comment_url"] is None


def test_record_outside_events_dir_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(repo)
    _provider(monkeypatch, FakeProvider())
    outside = repo / "evil.json"
    outside.write_text("{}", encoding="utf-8")
    argv = ["watch", "record", "--event", str(outside), "--outcome", "handoff"]
    assert main([*argv, "--workdir", str(repo)]) == 2


def test_unexpected_error_exit_3(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(repo)

    class Boom(FakeProvider):
        def list_labels(self) -> list[Label]:
            raise RuntimeError("gh down")

    _provider(monkeypatch, Boom())
    assert main(["watch", "status", "--workdir", str(repo)]) == 3
