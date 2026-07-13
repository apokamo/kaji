"""Tests for `kaji run` execution overrides (Issue #224).

Covers the new `--agent-runner` / `--interactive-terminal-close-on-verdict`
CLI options: argparse three-state parsing, hyphen→underscore normalization,
and precedence over the resolved config (precedence 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.commands.parser import create_parser
from kaji_harness.commands.run import _apply_execution_overrides
from kaji_harness.config import ExecutionConfig, KajiConfig, PathsConfig


def _config(*, agent_runner: str = "headless", close: bool = True) -> KajiConfig:
    return KajiConfig(
        repo_root=Path("/repo"),
        paths=PathsConfig(artifacts_dir=".kaji/artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(
            default_timeout=1800,
            agent_runner=agent_runner,  # type: ignore[arg-type]
            interactive_terminal_close_on_verdict=close,
        ),
    )


def _parse(argv: list[str]):
    return create_parser().parse_args(["run", "wf.yaml", "1", *argv])


@pytest.mark.small
class TestRunParserThreeState:
    """argparse exposes the three close-on-verdict states and the runner choice."""

    def test_no_flags_default_to_none(self) -> None:
        args = _parse([])
        assert args.agent_runner is None
        assert args.close_on_verdict is None

    def test_close_flag_sets_true(self) -> None:
        assert _parse(["--interactive-terminal-close-on-verdict"]).close_on_verdict is True

    def test_no_close_flag_sets_false(self) -> None:
        assert _parse(["--no-interactive-terminal-close-on-verdict"]).close_on_verdict is False

    def test_close_flags_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            _parse(
                [
                    "--interactive-terminal-close-on-verdict",
                    "--no-interactive-terminal-close-on-verdict",
                ]
            )

    def test_agent_runner_choice_accepts_hyphen_form_only(self) -> None:
        assert _parse(["--agent-runner", "interactive-terminal"]).agent_runner == (
            "interactive-terminal"
        )
        with pytest.raises(SystemExit):
            _parse(["--agent-runner", "interactive_terminal"])


@pytest.mark.small
class TestApplyExecutionOverrides:
    """`_apply_execution_overrides` applies CLI options over the config."""

    def test_no_overrides_returns_same_config(self) -> None:
        config = _config()
        result = _apply_execution_overrides(config, _parse([]))
        assert result is config

    def test_agent_runner_normalized_to_underscore(self) -> None:
        config = _config(agent_runner="headless")
        result = _apply_execution_overrides(
            config, _parse(["--agent-runner", "interactive-terminal"])
        )
        assert result.execution.agent_runner == "interactive_terminal"
        # close_on_verdict untouched when not specified.
        assert result.execution.interactive_terminal_close_on_verdict is True

    def test_cli_overrides_config_runner(self) -> None:
        # Config says interactive_terminal; CLI forces headless for this run.
        config = _config(agent_runner="interactive_terminal")
        result = _apply_execution_overrides(config, _parse(["--agent-runner", "headless"]))
        assert result.execution.agent_runner == "headless"

    def test_no_close_flag_overrides_config_true(self) -> None:
        config = _config(agent_runner="interactive_terminal", close=True)
        result = _apply_execution_overrides(
            config, _parse(["--no-interactive-terminal-close-on-verdict"])
        )
        assert result.execution.interactive_terminal_close_on_verdict is False
        # agent_runner preserved from config.
        assert result.execution.agent_runner == "interactive_terminal"

    def test_close_flag_overrides_config_false(self) -> None:
        config = _config(agent_runner="interactive_terminal", close=False)
        result = _apply_execution_overrides(
            config, _parse(["--interactive-terminal-close-on-verdict"])
        )
        assert result.execution.interactive_terminal_close_on_verdict is True

    def test_other_config_fields_preserved(self) -> None:
        config = _config()
        result = _apply_execution_overrides(
            config, _parse(["--agent-runner", "interactive-terminal"])
        )
        assert result.repo_root == config.repo_root
        assert result.paths == config.paths
        assert result.execution.default_timeout == 1800
