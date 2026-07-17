"""Medium tests for series command parsing, dispatch, and dry-run boundaries."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.commands.main import main
from kaji_harness.commands.parser import create_parser
from kaji_harness.commands.series import cmd_run_series
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.errors import SeriesAbortedError, SeriesInputError, SeriesRuntimeError
from kaji_harness.providers.github import GitHubProviderError
from kaji_harness.scripts.series_generate import main as generate_main
from kaji_harness.series import SeriesConfig

pytestmark = pytest.mark.medium


def _config(tmp_path: Path) -> KajiConfig:
    return KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir="artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=60),
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/name"),
        ),
    )


def _series() -> SeriesConfig:
    return SeriesConfig.model_validate(
        {
            "id": "cli-series",
            "strategy": "sequential",
            "members": [{"issue": 10, "workflow": ".kaji/wf/dev.yaml"}],
            "on_failure": "stop",
        }
    )


def _write_series_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    workflow_dir = repo / ".kaji" / "wf"
    series_dir = repo / ".kaji" / "series"
    workflow_dir.mkdir(parents=True)
    series_dir.mkdir()
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = "artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n',
        encoding="utf-8",
    )
    valid = workflow_dir / "valid.yaml"
    valid.write_text(
        "name: valid\n"
        "description: valid\n"
        "requires_provider: github\n"
        "execution_policy: auto\n"
        "steps:\n"
        "  - id: done\n"
        '    exec: ["true"]\n'
        "    on:\n"
        "      PASS: end\n",
        encoding="utf-8",
    )
    candidate = workflow_dir / "candidate.yaml"
    candidate.write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")
    skill_dir = repo / ".claude" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# test-skill\n", encoding="utf-8")
    series = series_dir / "test.yaml"
    series.write_text(
        "id: preflight-test\n"
        "strategy: sequential\n"
        "members:\n"
        "  - issue: 10\n    workflow: .kaji/wf/valid.yaml\n"
        "  - issue: 11\n    workflow: .kaji/wf/candidate.yaml\n"
        "on_failure: stop\n",
        encoding="utf-8",
    )
    return repo, series, candidate


def _invalid_workflow(reason: str) -> str:
    if reason == "l2":
        return (
            "name: invalid\ndescription: invalid\nrequires_provider: github\n"
            "execution_policy: auto\nsteps:\n  - id: broken\n"
            '    exec: ["true"]\n    on:\n      PASS: missing\n'
        )
    if reason == "l3":
        return (
            "name: invalid\ndescription: invalid\nrequires_provider: github\n"
            "execution_policy: auto\nsteps:\n  - id: broken\n"
            "    skill: missing-skill\n    agent: claude\n    on:\n      PASS: end\n"
        )
    return (
        "name: invalid\ndescription: invalid\nrequires_provider: github\n"
        "execution_policy: auto\nsteps:\n"
        "  - id: first\n    skill: test-skill\n    agent: claude\n"
        "    on:\n      PASS: second\n"
        "  - id: second\n    skill: test-skill\n    agent: codex\n"
        "    resume: first\n    on:\n      PASS: end\n"
    )


def test_parser_accepts_series_commands() -> None:
    parser = create_parser()
    validate = parser.parse_args(["validate-series", "series.yaml"])
    run = parser.parse_args(["run-series", "series.yaml", "--dry-run", "--quiet"])
    assert validate.command == "validate-series"
    assert validate.series == [Path("series.yaml")]
    assert run.command == "run-series"
    assert run.dry_run is True
    assert run.quiet is True


def test_main_dispatches_series_handlers() -> None:
    with patch("kaji_harness.commands.main.cmd_validate_series", return_value=7) as validate:
        assert main(["validate-series", "series.yaml"]) == 7
    validate.assert_called_once()
    with patch("kaji_harness.commands.main.cmd_run_series", return_value=8) as run:
        assert main(["run-series", "series.yaml", "--dry-run"]) == 8
    run.assert_called_once()


def test_dry_run_does_not_access_provider_or_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = MagicMock()
    args = argparse.Namespace(
        series=tmp_path / "series.yaml",
        workdir=tmp_path,
        dry_run=True,
        resume=False,
        quiet=False,
    )
    with (
        patch("kaji_harness.commands.series.KajiConfig.discover", return_value=_config(tmp_path)),
        patch("kaji_harness.commands.series.get_provider", return_value=provider),
        patch("kaji_harness.commands.series.load_series", return_value=_series()),
        patch("kaji_harness.commands.series.resolve_artifacts_dir") as artifacts,
        patch("kaji_harness.commands.series.SeriesRunner") as runner,
    ):
        assert cmd_run_series(args) == 0
    provider.view_issue.assert_not_called()
    artifacts.assert_not_called()
    runner.assert_not_called()
    assert "1. issue #10" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SeriesAbortedError("member failed"), 1),
        (SeriesInputError("state mismatch"), 2),
        (SeriesRuntimeError("write failed"), 3),
        (GitHubProviderError("provider failed"), 3),
    ],
)
def test_run_series_maps_runner_failures_to_public_exit_codes(
    tmp_path: Path, error: Exception, expected: int
) -> None:
    args = argparse.Namespace(
        series=tmp_path / "series.yaml",
        workdir=tmp_path,
        dry_run=False,
        resume=False,
        quiet=False,
    )
    runner = MagicMock()
    runner.run.side_effect = error
    with (
        patch("kaji_harness.commands.series.KajiConfig.discover", return_value=_config(tmp_path)),
        patch("kaji_harness.commands.series.get_provider", return_value=MagicMock()),
        patch("kaji_harness.commands.series.load_series", return_value=_series()),
        patch("kaji_harness.commands.series.resolve_artifacts_dir", return_value=tmp_path),
        patch("kaji_harness.commands.series.SeriesRunner", return_value=runner),
    ):
        assert cmd_run_series(args) == expected


def test_generator_cli_preserves_order_and_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "series.yaml"
    argv = [
        "--id",
        "cli-series",
        "--parent",
        "291",
        "--member",
        "10=.kaji/wf/dev.yaml",
        "--member",
        "11=.kaji/wf/docs.yaml",
        "--output",
        str(output),
    ]
    assert generate_main(argv) == 0
    text = output.read_text(encoding="utf-8")
    assert text.index("issue: 10") < text.index("issue: 11")
    assert generate_main(argv) == 1
    assert generate_main([*argv, "--update"]) == 0


@pytest.mark.parametrize(
    ("invalid_kind", "expected"),
    [
        ("l2", "transitions to unknown step 'missing'"),
        ("l3", "missing-skill/SKILL.md not found"),
        ("resume", "resumes 'first' but agents differ (codex != claude)"),
    ],
)
def test_series_entrypoints_share_full_member_preflight(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid_kind: str,
    expected: str,
) -> None:
    repo, series, candidate = _write_series_repo(tmp_path)
    candidate.write_text(_invalid_workflow(invalid_kind), encoding="utf-8")

    assert main(["validate-series", str(series), "--workdir", str(repo)]) == 1
    assert expected in capsys.readouterr().err
    assert main(["run-series", str(series), "--dry-run", "--workdir", str(repo)]) == 2
    assert expected in capsys.readouterr().err
    with patch("kaji_harness.commands.series.SeriesRunner") as runner:
        assert main(["run-series", str(series), "--workdir", str(repo)]) == 2
    runner.assert_not_called()
    assert expected in capsys.readouterr().err
    assert not (repo / "artifacts" / "_series").exists()


def test_run_series_revalidates_current_plan_after_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, series, candidate = _write_series_repo(tmp_path)
    assert main(["run-series", str(series), "--dry-run", "--workdir", str(repo)]) == 0
    capsys.readouterr()
    candidate.write_text(_invalid_workflow("l2"), encoding="utf-8")

    with patch("kaji_harness.commands.series.SeriesRunner") as runner:
        assert main(["run-series", str(series), "--workdir", str(repo)]) == 2

    runner.assert_not_called()
    assert "transitions to unknown step 'missing'" in capsys.readouterr().err
    assert not (repo / "artifacts" / "_series").exists()


def test_validate_series_renders_every_invalid_member(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, series, candidate = _write_series_repo(tmp_path)
    candidate.write_text(_invalid_workflow("l2"), encoding="utf-8")
    series.write_text(
        series.read_text(encoding="utf-8").replace(
            "on_failure: stop",
            "  - issue: 12\n    workflow: .kaji/wf/missing.yaml\non_failure: stop",
        ),
        encoding="utf-8",
    )

    assert main(["validate-series", str(series), "--workdir", str(repo)]) == 1

    stderr = capsys.readouterr().err
    assert "members.1.workflow is invalid (.kaji/wf/candidate.yaml)" in stderr
    assert "members.2.workflow not found: .kaji/wf/missing.yaml" in stderr
