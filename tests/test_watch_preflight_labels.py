"""Medium tests: watch preconditions and ``watch_labels`` script (Issue #543 D1・M1・M7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import CLINotFoundError, WatchPreconditionError
from kaji_harness.providers import Label
from kaji_harness.scripts import watch_labels
from kaji_harness.watch.constants import CONTRACT_LABELS
from kaji_harness.watch.preflight import check_watch_preconditions
from tests.runstate_helpers import write_config

pytestmark = pytest.mark.medium

HERDR_ENV = {"HERDR_ENV": "1", "HERDR_PANE_ID": "p_1"}


class FakeProvider:
    """``list_labels`` / ``edit_issue`` だけを持つ fake。"""

    def __init__(self, labels: tuple[str, ...] = CONTRACT_LABELS, fail_edit: bool = False):
        self.labels = labels
        self.fail_edit = fail_edit
        self.edits: list[tuple[str, list[str] | None, list[str] | None]] = []

    def list_labels(self) -> list[Label]:
        return [Label(name=name) for name in self.labels]

    def edit_issue(
        self,
        issue_id: str,
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        **_: Any,
    ) -> None:
        if self.fail_edit:
            raise RuntimeError("gh issue edit failed")
        self.edits.append((issue_id, add_labels, remove_labels))


@pytest.fixture
def repo(outside_project_tmp_path: Path) -> Path:
    write_config(outside_project_tmp_path)
    return outside_project_tmp_path


def _declare(repo: Path, body: str = "") -> None:
    path = repo / ".kaji" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + f"\n[watch]\n{body}\n", encoding="utf-8")


def _config(repo: Path) -> KajiConfig:
    return KajiConfig.discover(start_dir=repo)


def test_preconditions_not_declared(repo: Path) -> None:
    with pytest.raises(WatchPreconditionError) as exc:
        check_watch_preconditions(_config(repo), require_herdr=False, provider=FakeProvider())
    assert exc.value.reason == "not_declared"


def test_preconditions_missing_labels(repo: Path) -> None:
    _declare(repo)
    with pytest.raises(WatchPreconditionError, match="state:hold") as exc:
        check_watch_preconditions(
            _config(repo),
            require_herdr=False,
            provider=FakeProvider(labels=("state:planned", "state:running")),
        )
    assert exc.value.reason == "labels"


def test_preconditions_herdr_required_outside_herdr(repo: Path) -> None:
    _declare(repo)
    provider = FakeProvider()
    with pytest.raises(WatchPreconditionError) as exc:
        check_watch_preconditions(_config(repo), require_herdr=True, provider=provider, env={})
    assert exc.value.reason == "herdr"


def test_preconditions_herdr_not_installed(repo: Path) -> None:
    _declare(repo)

    def missing() -> str:
        raise CLINotFoundError("CLI 'herdr' not found")

    with pytest.raises(WatchPreconditionError) as exc:
        check_watch_preconditions(
            _config(repo),
            require_herdr=True,
            provider=FakeProvider(),
            env=HERDR_ENV,
            herdr_resolver=missing,
        )
    assert exc.value.reason == "herdr"


def test_preconditions_ok(repo: Path) -> None:
    _declare(repo, 'notify_command = ["notify.sh"]')
    context = check_watch_preconditions(
        _config(repo),
        require_herdr=True,
        provider=FakeProvider(),
        env=HERDR_ENV,
        herdr_resolver=lambda: "/usr/bin/herdr",
    )
    assert context.herdr_bin == "/usr/bin/herdr"
    assert context.herdr_pane_id == "p_1"
    assert context.watch.notify_command == ("notify.sh",)


def test_watch_labels_skips_without_watch(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    provider = FakeProvider()
    code = watch_labels.main(["mark-done", "101", "--workdir", str(repo)], provider=provider)
    assert code == 0
    assert "skipped (no [watch])" in capsys.readouterr().out
    assert provider.edits == []


def test_watch_labels_missing_labels_exit_2(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _declare(repo)
    provider = FakeProvider(labels=("state:done",))
    code = watch_labels.main(["mark-done", "101", "--workdir", str(repo)], provider=provider)
    assert code == 2
    assert "state:planned" in capsys.readouterr().err
    assert provider.edits == []


def test_watch_labels_mark_done_removes_all_other_state_labels(repo: Path) -> None:
    # M1: hold / needs-maintainer / 2 個以上付きでも、結果は state:done の 1 個だけになる
    _declare(repo)
    provider = FakeProvider()
    code = watch_labels.main(["mark-done", "101", "--workdir", str(repo)], provider=provider)
    assert code == 0
    issue_id, add, remove = provider.edits[0]
    assert issue_id == "101"
    assert add == ["state:done"]
    assert set(remove or []) == set(CONTRACT_LABELS) - {"state:done"}


def test_watch_labels_mark_follow_up(repo: Path) -> None:
    _declare(repo)
    provider = FakeProvider()
    code = watch_labels.main(["mark-follow-up", "102", "--workdir", str(repo)], provider=provider)
    assert code == 0
    assert provider.edits == [("102", ["state:needs-maintainer"], None)]


def test_watch_labels_write_failure_exit_3(repo: Path) -> None:
    _declare(repo)
    provider = FakeProvider(fail_edit=True)
    code = watch_labels.main(["mark-done", "101", "--workdir", str(repo)], provider=provider)
    assert code == 3
