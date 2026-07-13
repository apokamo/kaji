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
