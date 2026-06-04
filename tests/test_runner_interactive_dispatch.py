"""Medium tests: WorkflowRunner runner-backend dispatch (Issue #224).

Verifies that ``config.execution.agent_runner`` routes the agent step to either
``execute_interactive_terminal`` (kitty path) or ``execute_cli`` (headless),
without changing the existing headless behavior.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import SkillMetadata

_PASS_YAML = "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n"


def _make_config(tmp_path: Path, *, execution_extra: str = "") -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f"[execution]\ndefault_timeout = 60\n{execution_extra}\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _make_runner(config: KajiConfig, tmp_path: Path) -> WorkflowRunner:
    workflow = Workflow(
        name="t",
        description="",
        execution_policy="auto",
        steps=[Step(id="design", skill="plain", agent="claude", on={"PASS": "end"})],
    )
    return WorkflowRunner(
        workflow=workflow,
        issue_number=99,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=config,
    )


@pytest.mark.medium
class TestRunnerBackendDispatch:
    def test_interactive_terminal_config_routes_to_interactive_runner(self, tmp_path: Path) -> None:
        config = _make_config(
            tmp_path,
            execution_extra=(
                'agent_runner = "interactive_terminal"\n'
                "interactive_terminal_close_on_verdict = false"
            ),
        )
        runner = _make_runner(config, tmp_path)
        captured: dict[str, Any] = {}

        def fake_interactive(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            kwargs["verdict_path"].write_text(_PASS_YAML, encoding="utf-8")
            return CLIResult(full_output="", session_id="sess-it")

        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive
            ) as mock_it,
            patch("kaji_harness.runner.execute_cli") as mock_cli,
        ):
            state = runner.run()

        mock_it.assert_called_once()
        mock_cli.assert_not_called()
        assert state.last_completed_step == "design"
        # close_on_verdict flag is threaded from config into the runner call.
        assert captured["close_on_verdict"] is False
        assert captured["prompt_path"].name == "prompt.txt"
        assert captured["verdict_path"].name == "verdict.yaml"

    def test_headless_config_routes_to_execute_cli(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)  # default agent_runner = headless
        runner = _make_runner(config, tmp_path)
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_cli",
                return_value=CLIResult(
                    full_output=(
                        "---VERDICT---\nstatus: PASS\nreason: |\n  ok\nevidence: |\n  ok\n"
                        "suggestion: |\n  none\n---END_VERDICT---\n"
                    ),
                    session_id="s1",
                ),
            ) as mock_cli,
            patch("kaji_harness.runner.execute_interactive_terminal") as mock_it,
        ):
            state = runner.run()

        mock_cli.assert_called_once()
        mock_it.assert_not_called()
        assert state.last_completed_step == "design"
