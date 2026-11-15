"""Tests: ``kaji_harness.runstate.query`` (Issue #543).

watch が run / series の事実を取得する唯一の境界。既存の収集器の再利用だけで、
``kaji status --json`` と同じ文書を返すことを検査する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.commands.main import main
from kaji_harness.runstate.query import (
    StepArtifacts,
    collect_status,
    list_series,
    locate_step_artifacts,
)
from kaji_harness.runstate.schema import list_series_ids
from tests.runstate_helpers import end, start, step_start, write_config, write_run

ISSUE = "543"


def _write_series(artifacts: Path, series_id: str, members: list[dict[str, object]]) -> None:
    path = artifacts / "_series" / series_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "running",
                "stop_reason": None,
                "updated_at": "2026-11-15T03:00:00+00:00",
                "members": members,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.small
def test_list_series_ids_filters_invalid_ids(tmp_path: Path) -> None:
    (tmp_path / "_series" / "rw-1-2").mkdir(parents=True)
    (tmp_path / "_series" / "Bad_ID").mkdir(parents=True)
    (tmp_path / "_series" / "a-series").mkdir(parents=True)
    (tmp_path / "_series" / "file.txt").write_text("x", encoding="utf-8")
    assert list_series_ids(tmp_path) == ["a-series", "rw-1-2"]


@pytest.mark.small
def test_list_series_ids_without_dir(tmp_path: Path) -> None:
    assert list_series_ids(tmp_path) == []


@pytest.mark.small
def test_list_series_returns_series_objects(tmp_path: Path) -> None:
    _write_series(
        tmp_path,
        "rw-101-102",
        [{"issue": 101, "workflow": "dev", "status": "running", "run_id": "261115000000"}],
    )
    result = list_series(tmp_path)
    assert len(result) == 1
    assert result[0]["id"] == "rw-101-102"
    assert result[0]["members"][0]["issue"] == 101
    assert result[0]["members"][0]["run_id"] == "261115000000"
    assert result[0]["updated_at"] == "2026-11-15T03:00:00+00:00"


@pytest.mark.small
def test_locate_step_artifacts_existing_and_missing(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path,
        ISSUE,
        "261115000000",
        [start(), step_start("implement")],
        results={("implement", 1): {}},
    )
    found = locate_step_artifacts(tmp_path, ISSUE, "261115000000", "implement", 1)
    assert found == StepArtifacts(
        run_dir=run_dir, attempt_dir=run_dir / "steps" / "implement" / "attempt-001"
    )
    no_step = locate_step_artifacts(tmp_path, ISSUE, "261115000000", "review", 1)
    assert no_step == StepArtifacts(run_dir=run_dir, attempt_dir=None)
    no_run = locate_step_artifacts(tmp_path, ISSUE, "261115999999", "implement", 1)
    assert no_run == StepArtifacts(run_dir=None, attempt_dir=None)
    no_step_id = locate_step_artifacts(tmp_path, ISSUE, "261115000000", None, None)
    assert no_step_id == StepArtifacts(run_dir=run_dir, attempt_dir=None)


@pytest.mark.medium
def test_collect_status_matches_cli_document(
    outside_project_tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = outside_project_tmp_path
    write_config(repo)
    artifacts = repo / ".kaji-artifacts"
    write_run(artifacts, ISSUE, "261115000000", [start(), step_start("design"), end("ERROR")])
    code = main(["status", "--json", "--workdir", str(repo), ISSUE])
    assert code == 0
    cli_doc = json.loads(capsys.readouterr().out)
    doc = collect_status(artifacts, [ISSUE])
    cli_doc.pop("generated_at")
    doc.pop("generated_at")
    assert doc == cli_doc
    assert doc["schema_version"] == 1
    assert doc["issues"][0]["latest_run"]["end_status"] == "ERROR"
