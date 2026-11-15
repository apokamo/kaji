"""Small tests: ``run_watched`` plan helpers (Issue #543)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from kaji_harness.scripts.run_watched import (
    IssueFacts,
    WorkflowResolutionError,
    approval_required,
    classify,
    make_series_id,
    resolve_wf_label,
    resolve_workflow_arg,
)
from kaji_harness.series import is_valid_series_id

pytestmark = pytest.mark.small


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    wf = tmp_path / ".kaji" / "wf"
    (wf / "official").mkdir(parents=True)
    (wf / "custom" / "dev").mkdir(parents=True)
    (wf / "official" / "dev.yaml").write_text("name: dev\n", encoding="utf-8")
    (wf / "official" / "docs.yaml").write_text("name: docs\n", encoding="utf-8")
    (wf / "custom" / "dev" / "dev-small.yaml").write_text("name: dev-small\n", encoding="utf-8")
    (wf / "custom" / "dev" / "docs.yaml").write_text("name: docs-copy\n", encoding="utf-8")
    (wf / "custom" / "dev" / "twin.yaml").write_text("name: dev\n", encoding="utf-8")
    return tmp_path


def test_resolve_workflow_arg_by_stem_file_and_path(repo: Path) -> None:
    expected = repo / ".kaji" / "wf" / "custom" / "dev" / "dev-small.yaml"
    assert resolve_workflow_arg("dev-small", repo) == expected
    assert resolve_workflow_arg("dev-small.yaml", repo) == expected
    assert resolve_workflow_arg(".kaji/wf/custom/dev/dev-small.yaml", repo) == expected.resolve()


def test_resolve_workflow_arg_errors(repo: Path) -> None:
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_workflow_arg("nope", repo)
    assert exc.value.reason == "unresolvable"
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_workflow_arg("docs", repo)
    assert exc.value.reason == "ambiguous"


def test_resolve_wf_label(repo: Path) -> None:
    assert resolve_wf_label("dev-small", repo).name == "dev-small.yaml"
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_wf_label("dev", repo)
    assert exc.value.reason == "ambiguous"
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_wf_label("missing", repo)
    assert exc.value.reason == "unresolvable"


def test_series_id_format() -> None:
    series_id = make_series_id([101, 102], datetime(2026, 11, 15, 3, 1))
    assert series_id == "rw-101-102-2611150301"
    assert is_valid_series_id(series_id)


def _facts(**overrides: object) -> IssueFacts:
    data: dict[str, object] = {
        "issue": 101,
        "state": "open",
        "labels": (),
        "any_run_alive": False,
        "launching": False,
        "has_worktree_or_pr": False,
    }
    data.update(overrides)
    return IssueFacts(**data)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"state": "closed"}, "closed"),
        ({"labels": ("state:hold",)}, "state_label"),
        ({"labels": ("state:planned",)}, "state_label"),
        ({"labels": ("state:done", "state:running")}, "state_conflict"),
        ({"any_run_alive": True}, "run_alive_or_unknown"),
        ({"any_run_alive": None}, "run_alive_or_unknown"),
        ({"launching": None}, "run_alive_or_unknown"),
        ({"has_worktree_or_pr": True}, "worktree_or_pr"),
    ],
)
def test_classify_exclusions(overrides: dict[str, object], reason: str) -> None:
    assert classify(_facts(**overrides), workflow_arg=None, wf_label_path=None) == reason


def test_classify_workflow_mismatch_and_ok() -> None:
    a, b = Path("/wf/dev.yaml"), Path("/wf/docs.yaml")
    assert classify(_facts(), workflow_arg=a, wf_label_path=b) == "workflow_mismatch"
    assert classify(_facts(), workflow_arg=a, wf_label_path=a) is None
    assert classify(_facts(labels=("type:feature",)), workflow_arg=a, wf_label_path=None) is None


def test_approval_required_conditions() -> None:
    assert approval_required(chosen_by_skill=False, order_ambiguous=False, excluded=0) is False
    assert approval_required(chosen_by_skill=True, order_ambiguous=False, excluded=0) is True
    assert approval_required(chosen_by_skill=False, order_ambiguous=True, excluded=0) is True
    assert approval_required(chosen_by_skill=False, order_ambiguous=False, excluded=1) is True
