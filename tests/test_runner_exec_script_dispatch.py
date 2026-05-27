"""Medium tests: WorkflowRunner exec_script dispatch (Issue #204)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import SkillMetadata


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _verdict(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  ok\n"
        f"evidence: |\n  ok\nsuggestion: |\n  none\n---END_VERDICT---\n"
    )


@pytest.mark.medium
class TestExecScriptDispatch:
    def test_runner_dispatches_to_execute_script(self, tmp_path: Path) -> None:
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="poll", skill="review-poll", on={"PASS": "end", "ABORT": "end"}),
            ],
        )
        config = _make_config(tmp_path)

        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )

        def fake_execute_script(**kwargs: object) -> CLIResult:
            assert kwargs["module"] == "some.entry"
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["KAJI_STEP_ID"] == "poll"
            assert "KAJI_ISSUE_ID" in env
            return CLIResult(full_output=_verdict("PASS"))

        metadata = SkillMetadata(name="review-poll", description="", exec_script="some.entry")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch(
                "kaji_harness.runner.execute_script", side_effect=fake_execute_script
            ) as mock_exec,
            patch("kaji_harness.runner.execute_cli") as mock_cli,
        ):
            state = runner.run()

        mock_exec.assert_called_once()
        mock_cli.assert_not_called()
        assert state.last_completed_step == "poll"

    def test_runner_skips_ai_formatter_on_exec_script(self, tmp_path: Path) -> None:
        """exec_script 経路では VerdictNotFound 時に AI formatter を呼ばない。"""
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"}),
            ],
        )
        config = _make_config(tmp_path)
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        metadata = SkillMetadata(name="rp", description="", exec_script="m")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch(
                "kaji_harness.runner.execute_script",
                return_value=CLIResult(full_output="no verdict here"),
            ),
            patch("kaji_harness.runner.create_verdict_formatter") as mock_formatter,
        ):
            from kaji_harness.errors import VerdictNotFound

            with pytest.raises(VerdictNotFound):
                runner.run()

        mock_formatter.assert_not_called()

    def test_runner_fail_fast_when_agent_none_no_exec_script(self, tmp_path: Path) -> None:
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="s", skill="plain", on={"PASS": "end"}),
            ],
        )
        config = _make_config(tmp_path)
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
        ):
            with pytest.raises(WorkflowValidationError):
                runner.run()

    def test_runner_agent_path_unchanged_when_no_exec_script(self, tmp_path: Path) -> None:
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="s", skill="plain", agent="claude", on={"PASS": "end"}),
            ],
        )
        config = _make_config(tmp_path)
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_cli",
                return_value=CLIResult(full_output=_verdict("PASS"), session_id="s1"),
            ) as mock_cli,
            patch("kaji_harness.runner.execute_script") as mock_exec,
        ):
            state = runner.run()
        mock_cli.assert_called_once()
        mock_exec.assert_not_called()
        assert state.last_completed_step == "s"

    def test_cost_and_session_none_for_exec_script(self, tmp_path: Path) -> None:
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="poll", skill="rp", on={"PASS": "end"}),
            ],
        )
        config = _make_config(tmp_path)
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        metadata = SkillMetadata(name="rp", description="", exec_script="m")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch(
                "kaji_harness.runner.execute_script",
                return_value=CLIResult(full_output=_verdict("PASS")),
            ),
        ):
            state = runner.run()
        assert state.get_session_id("poll") is None
