"""Medium tests: ``kaji status`` CLI contract (Issue #540)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from kaji_harness.commands.exit_codes import EXIT_CONFIG_NOT_FOUND, EXIT_INVALID_INPUT, EXIT_OK
from kaji_harness.commands.main import main
from tests.runstate_helpers import (
    end,
    record,
    start,
    step_start,
    write_config,
    write_run,
    write_state,
)

pytestmark = pytest.mark.medium
ISSUE = "540"


@pytest.fixture
def repo(outside_project_tmp_path: Path) -> Path:
    # tmp_path は workflow 内ではリポジトリ内になり、config 探索と main worktree 解決が
    # 実リポジトリの artifacts を読んでしまう（testing-convention § workflow 内実行）。
    write_config(outside_project_tmp_path)
    return outside_project_tmp_path


def _artifacts(repo: Path) -> Path:
    return repo / ".kaji-artifacts"


def _run_json(
    repo: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, object]]:
    code = main(["status", "--json", "--workdir", str(repo), *argv])
    out = capsys.readouterr().out
    return code, json.loads(out) if code == EXIT_OK else {}


def _tree(root: Path) -> dict[str, tuple[float, str]]:
    snapshot: dict[str, tuple[float, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            snapshot[str(path.relative_to(root))] = (path.stat().st_mtime, digest)
    return snapshot


def test_issue_json_document(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_run(
        _artifacts(repo), ISSUE, "261112000000", [start(), step_start("implement"), end("ERROR")]
    )
    write_state(_artifacts(repo), ISSUE)
    code, doc = _run_json(repo, [ISSUE, "gh:541"], capsys)
    assert code == EXIT_OK
    assert doc["schema_version"] == 1
    assert doc["series"] is None
    issues = doc["issues"]
    assert isinstance(issues, list)
    assert [item["issue"] for item in issues] == ["540", "541"]
    first, second = issues
    assert first["latest_run"]["termination"] == "ended"
    assert first["latest_run"]["end_status"] == "ERROR"
    assert set(first) == {"issue", "latest_run", "any_run_alive", "launching", "read_errors"}
    assert set(first["latest_run"]) == {
        "run_id",
        "workflow",
        "started_at",
        "process",
        "termination",
        "end_status",
        "ended_at",
        "last_step",
        "failed_step",
        "failure",
        "worktree_dir",
        "branch_name",
        "pane",
        "recovery",
    }
    assert second == {
        "issue": "541",
        "latest_run": None,
        "any_run_alive": False,
        "launching": False,
        "read_errors": [],
    }


def test_table_output_is_default(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_run(_artifacts(repo), ISSUE, "261112000000", [start(), end("COMPLETE")])
    assert main(["status", "--workdir", str(repo), ISSUE]) == EXIT_OK
    assert "261112000000" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [[], ["540", "--series", "lane-a"], ["abc"], ["--series", "Bad_ID"]],
)
def test_argument_errors_exit_2(
    repo: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status", "--workdir", str(repo), *argv]) == EXIT_INVALID_INPUT
    assert "Error:" in capsys.readouterr().err


def test_missing_config_exits_2(outside_project_tmp_path: Path) -> None:
    assert main(["status", "--workdir", str(outside_project_tmp_path), ISSUE]) == (
        EXIT_CONFIG_NOT_FOUND
    )


def test_broken_run_log_is_exit_0_with_read_errors(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_run(_artifacts(repo), ISSUE, "261112000000", None, raw_log="{oops\n")
    code, doc = _run_json(repo, [ISSUE], capsys)
    assert code == EXIT_OK
    issue = doc["issues"][0]  # type: ignore[index]
    assert issue["latest_run"]["termination"] is None
    assert issue["read_errors"][0]["error"] == "invalid"


def test_status_is_read_only(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifacts = _artifacts(repo)
    write_run(
        artifacts, ISSUE, "261112000000", [start(), step_start("implement")], process=record(1)
    )
    write_state(artifacts, ISSUE)
    before = _tree(repo)
    assert main(["status", "--json", "--workdir", str(repo), ISSUE]) == EXIT_OK
    capsys.readouterr()
    assert _tree(repo) == before
    assert not (artifacts / ISSUE / "launches").exists()


def test_status_never_builds_provider(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import kaji_harness.providers as providers

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("status must not build a provider")

    monkeypatch.setattr(providers, "get_provider", _forbidden)
    write_run(_artifacts(repo), ISSUE, "261112000000", [start(), end("ERROR")])
    assert main(["status", "--json", "--workdir", str(repo), ISSUE]) == EXIT_OK
    capsys.readouterr()


class TestSeries:
    def _write_state(self, repo: Path, content: str) -> None:
        path = _artifacts(repo) / "_series" / "lane-a" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _state(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "series_id": "lane-a",
            "fingerprint": "f",
            "status": "stopped",
            "stop_reason": "member 540 failed: exit:1",
            "updated_at": "2026-11-12T00:00:00+00:00",
            "members": [
                {
                    "issue": 540,
                    "workflow": "wf.yaml",
                    "status": "failed",
                    "child_pid": 1,
                    "run_id": "261112000000",
                    "exit_code": 1,
                    "gate": "exit:1",
                    "started_at": "s",
                    "finished_at": "f",
                },
                {"issue": 541, "workflow": "wf.yaml", "status": "pending"},
            ],
        }
        base.update(overrides)
        return base

    def test_normal_state(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_state(repo, json.dumps(self._state()))
        code, doc = _run_json(repo, ["--series", "lane-a"], capsys)
        assert code == EXIT_OK
        series = doc["series"]
        assert isinstance(series, dict)
        assert series["status"] == "stopped"
        assert series["stop_reason"] == "member 540 failed: exit:1"
        assert [m["status"] for m in series["members"]] == ["failed", "pending"]
        assert series["members"][0]["gate"] == "exit:1"
        assert series["read_errors"] == []
        assert [i["issue"] for i in doc["issues"]] == ["540", "541"]  # type: ignore[union-attr]

    @pytest.mark.parametrize("content", [None, "{broken", "[]"])
    def test_missing_or_broken_state(
        self, repo: Path, content: str | None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        if content is not None:
            self._write_state(repo, content)
        code, doc = _run_json(repo, ["--series", "lane-a"], capsys)
        assert code == EXIT_OK
        series = doc["series"]
        assert isinstance(series, dict)
        assert series["status"] is None
        assert series["members"] == []
        assert doc["issues"] == []
        assert series["read_errors"][0]["error"] == ("missing" if content is None else "invalid")
        assert series["read_errors"][0]["path"] == "_series/lane-a/state.json"

    def test_members_not_array(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_state(repo, json.dumps(self._state(members={"x": 1})))
        code, doc = _run_json(repo, ["--series", "lane-a"], capsys)
        assert code == EXIT_OK
        series = doc["series"]
        assert isinstance(series, dict)
        assert series["status"] == "stopped"
        assert series["members"] == []
        assert series["read_errors"][0]["path"].endswith("#members")

    def test_member_with_status_outside_enum(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = self._state()
        state["members"][0]["status"] = "skipped"  # type: ignore[index]
        self._write_state(repo, json.dumps(state))
        code, doc = _run_json(repo, ["--series", "lane-a"], capsys)
        assert code == EXIT_OK
        series = doc["series"]
        assert isinstance(series, dict)
        assert series["members"][0]["status"] is None
        assert series["members"][0]["issue"] == 540
        assert series["read_errors"][0]["path"].endswith("members[0].status")
        assert [i["issue"] for i in doc["issues"]] == ["540", "541"]  # type: ignore[union-attr]
