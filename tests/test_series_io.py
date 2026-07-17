"""Medium tests for series loading, state, locking, and generation."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.errors import SeriesInputError, SeriesValidationError
from kaji_harness.series import (
    SeriesConfig,
    SeriesLock,
    SeriesState,
    generate_series_yaml,
    load_series,
)

pytestmark = pytest.mark.medium


def _kaji_config(repo_root: Path) -> KajiConfig:
    return KajiConfig(
        repo_root=repo_root,
        paths=PathsConfig(artifacts_dir="artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=60),
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/name"),
        ),
    )


def _write_workflow(repo: Path, name: str = "dev.yaml", provider: str = "github") -> Path:
    path = repo / ".kaji" / "wf" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "name: test\n"
        "description: test\n"
        f"requires_provider: {provider}\n"
        "execution_policy: auto\n"
        "steps:\n"
        "  - id: done\n"
        '    exec: ["true"]\n'
        "    on:\n"
        "      PASS: end\n",
        encoding="utf-8",
    )
    return path


def _series_yaml(workflow: str = ".kaji/wf/dev.yaml") -> str:
    return (
        "id: test-series\n"
        "strategy: sequential\n"
        "members:\n"
        f"  - issue: 10\n    workflow: {workflow}\n"
        "on_failure: stop\n"
    )


def test_load_series_validates_workflow_and_provider(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    path = tmp_path / "series.yaml"
    path.write_text(_series_yaml(), encoding="utf-8")
    loaded = load_series(path, _kaji_config(tmp_path))
    assert loaded.id == "test-series"
    assert loaded.members[0].workflow == ".kaji/wf/dev.yaml"


@pytest.mark.parametrize(
    ("workflow", "provider", "match"),
    [
        (".kaji/wf/missing.yaml", "github", "not found"),
        (".kaji/wf/dev.yaml", "local", "requires provider"),
        ("../escape.yaml", "github", "inside repo root"),
    ],
)
def test_load_series_rejects_invalid_workflow(
    tmp_path: Path, workflow: str, provider: str, match: str
) -> None:
    _write_workflow(tmp_path, provider=provider)
    path = tmp_path / "series.yaml"
    path.write_text(_series_yaml(workflow), encoding="utf-8")
    with pytest.raises(SeriesValidationError, match=match):
        load_series(path, _kaji_config(tmp_path))


def test_load_series_rejects_malformed_workflow(tmp_path: Path) -> None:
    workflow = _write_workflow(tmp_path)
    workflow.write_text("name: broken\nsteps: not-a-list\n", encoding="utf-8")
    path = tmp_path / "series.yaml"
    path.write_text(_series_yaml(), encoding="utf-8")
    with pytest.raises(SeriesValidationError, match="is invalid"):
        load_series(path, _kaji_config(tmp_path))


def test_load_series_rejects_l2_invalid_workflow(tmp_path: Path) -> None:
    workflow = _write_workflow(tmp_path)
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("PASS: end", "PASS: missing"),
        encoding="utf-8",
    )
    path = tmp_path / "series.yaml"
    path.write_text(_series_yaml(), encoding="utf-8")

    with pytest.raises(SeriesValidationError) as exc_info:
        load_series(path, _kaji_config(tmp_path))

    assert exc_info.value.errors == [
        "members.0.workflow is invalid (.kaji/wf/dev.yaml): "
        "Step 'done' transitions to unknown step 'missing' on PASS"
    ]


def test_load_series_distinguishes_workflow_io_error(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    path = tmp_path / "series.yaml"
    path.write_text(_series_yaml(), encoding="utf-8")

    with (
        patch(
            "kaji_harness.series.loader.preflight_workflow_path",
            side_effect=OSError("disk error"),
        ),
        pytest.raises(SeriesValidationError, match="could not be loaded"),
    ):
        load_series(path, _kaji_config(tmp_path))


def test_load_series_aggregates_all_invalid_members(tmp_path: Path) -> None:
    l2_path = _write_workflow(tmp_path, "l2.yaml")
    l2_path.write_text(
        l2_path.read_text(encoding="utf-8").replace("PASS: end", "PASS: missing"),
        encoding="utf-8",
    )
    l3_path = _write_workflow(tmp_path, "l3.yaml")
    l3_path.write_text(
        l3_path.read_text(encoding="utf-8")
        .replace('exec: ["true"]', "skill: missing-skill")
        .replace("    on:", "    agent: claude\n    on:"),
        encoding="utf-8",
    )
    path = tmp_path / "series.yaml"
    path.write_text(
        "id: test-series\n"
        "strategy: sequential\n"
        "members:\n"
        "  - issue: 10\n    workflow: .kaji/wf/l2.yaml\n"
        "  - issue: 11\n    workflow: .kaji/wf/l3.yaml\n"
        "  - issue: 12\n    workflow: .kaji/wf/missing.yaml\n"
        "on_failure: stop\n",
        encoding="utf-8",
    )

    with pytest.raises(SeriesValidationError) as exc_info:
        load_series(path, _kaji_config(tmp_path))

    errors = exc_info.value.errors
    assert len(errors) == 3
    assert "members.0.workflow is invalid (.kaji/wf/l2.yaml)" in errors[0]
    assert "unknown step 'missing'" in errors[0]
    assert "members.1.workflow is invalid (.kaji/wf/l3.yaml)" in errors[1]
    assert "missing-skill/SKILL.md not found" in errors[1]
    assert errors[2] == "members.2.workflow not found: .kaji/wf/missing.yaml"


def test_state_round_trip_is_validated_and_atomic(tmp_path: Path) -> None:
    config = SeriesConfig.model_validate(
        {
            "id": "test-series",
            "strategy": "sequential",
            "members": [{"issue": 10, "workflow": ".kaji/wf/dev.yaml"}],
            "on_failure": "stop",
        }
    )
    state = SeriesState.create(config)
    path = tmp_path / "state.json"
    state.save(path)
    assert SeriesState.load(path) == state
    assert not (tmp_path / "state.json.tmp").exists()


def test_state_load_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"series_id":"x","unexpected":true}', encoding="utf-8")
    with pytest.raises(SeriesValidationError):
        SeriesState.load(path)


def test_series_lock_rejects_concurrent_holder(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with SeriesLock(path):
        with pytest.raises(SeriesInputError, match="already running"):
            with SeriesLock(path):
                pytest.fail("second lock must not be acquired")


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EAGAIN])
def test_series_lock_classifies_contention_errors(tmp_path: Path, error_number: int) -> None:
    with (
        patch(
            "kaji_harness.series.lock.fcntl.flock",
            side_effect=OSError(error_number, "busy"),
        ),
        pytest.raises(SeriesInputError, match="already running"),
    ):
        with SeriesLock(tmp_path / "lock"):
            pytest.fail("lock acquisition must fail")


def test_series_lock_propagates_unexpected_os_error(tmp_path: Path) -> None:
    with (
        patch(
            "kaji_harness.series.lock.fcntl.flock",
            side_effect=OSError(errno.EIO, "I/O error"),
        ),
        pytest.raises(OSError, match="I/O error"),
    ):
        with SeriesLock(tmp_path / "lock"):
            pytest.fail("lock acquisition must fail")


def test_generator_is_deterministic_and_bridges_to_loader(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    config = SeriesConfig.model_validate(
        {
            "id": "test-series",
            "parent_issue": 291,
            "strategy": "sequential",
            "members": [{"issue": 10, "workflow": ".kaji/wf/dev.yaml"}],
            "on_failure": "stop",
        }
    )
    output = tmp_path / ".kaji" / "series" / "test-series.yaml"
    generate_series_yaml(config, output)
    first = output.read_text(encoding="utf-8")
    assert "parent_issue: 291" in first
    assert "description:" not in first
    assert load_series(output, _kaji_config(tmp_path)) == config

    with pytest.raises(FileExistsError):
        generate_series_yaml(config, output)
    generate_series_yaml(config, output, update=True)
    assert output.read_text(encoding="utf-8") == first
