"""Tests for workdir configuration: workflow-level and step-level workdir fields.

Issue #127: Allow workdir to be specified in workflow YAML at workflow level
and step level, with fallback: step.workdir → workflow.workdir → project_root.

Covers:
- workdir field parsing via _parse_workflow() (S)
- validate_workflow() workdir validation for directly constructed models (S)
- Fallback resolution logic (S)
- Runner integration with workdir (M)
- Runtime directory existence check (M)
- kaji validate E2E with workdir in workflow YAML (L)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import Step, Workflow
from kaji_harness.workflow import load_workflow_from_str, validate_workflow

# ============================================================
# Helpers
# ============================================================


def _step(
    id: str = "s1",
    *,
    workdir: str | None = None,
    on: dict[str, str] | None = None,
) -> Step:
    return Step(
        id=id,
        skill="test-skill",
        agent="claude",
        workdir=workdir,
        on=on or {"PASS": "end"},
    )


def _workflow(
    steps: list[Step] | None = None,
    *,
    workdir: str | None = None,
) -> Workflow:
    return Workflow(
        name="test-wf",
        description="Test",
        execution_policy="auto",
        steps=steps or [_step()],
        workdir=workdir,
    )


# ============================================================
# Small tests — workdir field parsing in _parse_workflow()
# ============================================================


@pytest.mark.small
class TestWorkflowWorkdirParsing:
    """Workflow.workdir parsing via _parse_workflow()."""

    def test_workflow_workdir_parsed(self) -> None:
        """workdir in workflow YAML is parsed correctly."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: /home/user/project
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.workdir == "/home/user/project"

    def test_workflow_workdir_omitted_is_none(self) -> None:
        """Omitting workdir results in None."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.workdir is None

    def test_workflow_workdir_tilde_expanded(self) -> None:
        """Tilde in workflow workdir is expanded via expanduser()."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: ~/project
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert "~" not in wf.workdir  # type: ignore[operator]
        assert Path(wf.workdir).is_absolute()  # type: ignore[arg-type]

    def test_workflow_workdir_empty_string_raises(self) -> None:
        """Empty string workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: ""
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)

    def test_workflow_workdir_non_string_raises(self) -> None:
        """Non-string workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: 123
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)

    def test_workflow_workdir_relative_path_raises(self) -> None:
        """Relative path workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: relative/path
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)


@pytest.mark.small
class TestStepWorkdirParsing:
    """Step.workdir parsing via _parse_workflow()."""

    def test_step_workdir_parsed(self) -> None:
        """workdir in step YAML is parsed correctly."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: /home/user/project/apps/web
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.steps[0].workdir == "/home/user/project/apps/web"

    def test_step_workdir_omitted_is_none(self) -> None:
        """Omitting step workdir results in None."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.steps[0].workdir is None

    def test_step_workdir_tilde_expanded(self) -> None:
        """Tilde in step workdir is expanded via expanduser()."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: ~/project/apps/web
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert "~" not in wf.steps[0].workdir  # type: ignore[operator]
        assert Path(wf.steps[0].workdir).is_absolute()  # type: ignore[arg-type]

    def test_step_workdir_empty_string_raises(self) -> None:
        """Empty string step workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: ""
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)

    def test_step_workdir_non_string_raises(self) -> None:
        """Non-string step workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: 42
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)

    def test_step_workdir_relative_path_raises(self) -> None:
        """Relative path step workdir raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: apps/web
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="workdir"):
            load_workflow_from_str(yaml_str)

    def test_both_workflow_and_step_workdir_parsed(self) -> None:
        """Both workflow-level and step-level workdir are parsed independently."""
        yaml_str = dedent("""\
            name: test
            description: test
            workdir: /home/user/project
            steps:
              - id: s1
                skill: sk
                agent: claude
                workdir: /home/user/project/apps/web
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.workdir == "/home/user/project"
        assert wf.steps[0].workdir == "/home/user/project/apps/web"


# ============================================================
# Small tests — validate_workflow() workdir validation
# ============================================================


@pytest.mark.small
class TestValidateWorkflowWorkdir:
    """validate_workflow() catches invalid workdir on directly constructed models."""

    def test_workflow_workdir_non_string_rejected(self) -> None:
        """Directly constructed Workflow with non-string workdir is rejected."""
        wf = _workflow(workdir=123)  # type: ignore[arg-type]
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_workflow_workdir_empty_string_rejected(self) -> None:
        """Directly constructed Workflow with empty string workdir is rejected."""
        wf = _workflow(workdir="")
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_workflow_workdir_relative_path_rejected(self) -> None:
        """Directly constructed Workflow with relative path workdir is rejected."""
        wf = _workflow(workdir="relative/path")
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_step_workdir_non_string_rejected(self) -> None:
        """Directly constructed Step with non-string workdir is rejected."""
        wf = _workflow(steps=[_step(workdir=42)])  # type: ignore[arg-type]
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_step_workdir_empty_string_rejected(self) -> None:
        """Directly constructed Step with empty string workdir is rejected."""
        wf = _workflow(steps=[_step(workdir="")])
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_step_workdir_relative_path_rejected(self) -> None:
        """Directly constructed Step with relative path workdir is rejected."""
        wf = _workflow(steps=[_step(workdir="apps/web")])
        with pytest.raises(WorkflowValidationError, match="workdir"):
            validate_workflow(wf)

    def test_valid_absolute_workdir_passes(self) -> None:
        """Valid absolute workdir values pass validation."""
        wf = _workflow(
            steps=[_step(workdir="/home/user/project/apps/web")],
            workdir="/home/user/project",
        )
        validate_workflow(wf)  # should not raise

    def test_none_workdir_passes(self) -> None:
        """None workdir (omitted) passes validation."""
        wf = _workflow(
            steps=[_step(workdir=None)],
            workdir=None,
        )
        validate_workflow(wf)  # should not raise


# ============================================================
# Small tests — workdir fallback resolution logic
# ============================================================


@pytest.mark.small
class TestWorkdirResolution:
    """Workdir fallback: step.workdir → workflow.workdir → project_root."""

    def test_step_workdir_takes_priority(self) -> None:
        """step.workdir is used when set, regardless of workflow/project_root."""
        step = _step(workdir="/step/dir")
        wf = _workflow(steps=[step], workdir="/workflow/dir")
        project_root = Path("/project/root")

        raw_workdir = step.workdir or wf.workdir
        effective = Path(raw_workdir) if raw_workdir else project_root
        assert effective == Path("/step/dir")

    def test_workflow_workdir_when_step_none(self) -> None:
        """workflow.workdir is used when step.workdir is None."""
        step = _step(workdir=None)
        wf = _workflow(steps=[step], workdir="/workflow/dir")
        project_root = Path("/project/root")

        raw_workdir = step.workdir or wf.workdir
        effective = Path(raw_workdir) if raw_workdir else project_root
        assert effective == Path("/workflow/dir")

    def test_project_root_when_both_none(self) -> None:
        """project_root is used when both step and workflow workdir are None."""
        step = _step(workdir=None)
        wf = _workflow(steps=[step], workdir=None)
        project_root = Path("/project/root")

        raw_workdir = step.workdir or wf.workdir
        effective = Path(raw_workdir) if raw_workdir else project_root
        assert effective == project_root


# ============================================================
# Medium tests — Runner integration
# ============================================================


@pytest.mark.medium
class TestWorkdirRunnerIntegration:
    """WorkflowRunner passes correct workdir to execute_cli."""

    def _write_config(self, tmp_path: Path) -> Path:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n'
        )
        return config_file

    def test_runner_passes_step_workdir(self, tmp_path: Path) -> None:
        """WorkflowRunner passes step.workdir to execute_cli when specified."""
        from kaji_harness.config import KajiConfig
        from kaji_harness.models import CLIResult, CostInfo
        from kaji_harness.runner import WorkflowRunner

        self._write_config(tmp_path)
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        step_dir = tmp_path / "step-dir"
        step_dir.mkdir()

        wf = _workflow(
            steps=[_step(workdir=str(step_dir))],
            workdir=str(tmp_path),
        )

        cli_result = CLIResult(
            full_output=(
                "output\n---VERDICT---\nstatus: PASS\nreason: ok\n"
                'evidence: "test"\nsuggestion: ""\n---END_VERDICT---\n'
            ),
            session_id="s-1",
            cost=CostInfo(usd=0.01),
            stderr="",
        )

        with (
            patch("kaji_harness.runner.execute_cli", return_value=cli_result) as mock_exec,
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = WorkflowRunner(
                workflow=wf,
                issue_number=1,
                project_root=tmp_path,
                artifacts_dir=tmp_path / ".kaji-artifacts",
                config=config,
            )
            runner.run()

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["workdir"] == step_dir

    def test_runner_passes_workflow_workdir(self, tmp_path: Path) -> None:
        """WorkflowRunner passes workflow.workdir when step.workdir is None."""
        from kaji_harness.config import KajiConfig
        from kaji_harness.models import CLIResult, CostInfo
        from kaji_harness.runner import WorkflowRunner

        self._write_config(tmp_path)
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf_dir = tmp_path / "wf-dir"
        wf_dir.mkdir()

        wf = _workflow(
            steps=[_step(workdir=None)],
            workdir=str(wf_dir),
        )

        cli_result = CLIResult(
            full_output=(
                "output\n---VERDICT---\nstatus: PASS\nreason: ok\n"
                'evidence: "test"\nsuggestion: ""\n---END_VERDICT---\n'
            ),
            session_id="s-1",
            cost=CostInfo(usd=0.01),
            stderr="",
        )

        with (
            patch("kaji_harness.runner.execute_cli", return_value=cli_result) as mock_exec,
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = WorkflowRunner(
                workflow=wf,
                issue_number=1,
                project_root=tmp_path,
                artifacts_dir=tmp_path / ".kaji-artifacts",
                config=config,
            )
            runner.run()

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["workdir"] == wf_dir

    def test_runner_falls_back_to_project_root(self, tmp_path: Path) -> None:
        """WorkflowRunner falls back to project_root when no workdir specified."""
        from kaji_harness.config import KajiConfig
        from kaji_harness.models import CLIResult, CostInfo
        from kaji_harness.runner import WorkflowRunner

        self._write_config(tmp_path)
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(
            steps=[_step(workdir=None)],
            workdir=None,
        )

        cli_result = CLIResult(
            full_output=(
                "output\n---VERDICT---\nstatus: PASS\nreason: ok\n"
                'evidence: "test"\nsuggestion: ""\n---END_VERDICT---\n'
            ),
            session_id="s-1",
            cost=CostInfo(usd=0.01),
            stderr="",
        )

        with (
            patch("kaji_harness.runner.execute_cli", return_value=cli_result) as mock_exec,
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = WorkflowRunner(
                workflow=wf,
                issue_number=1,
                project_root=tmp_path,
                artifacts_dir=tmp_path / ".kaji-artifacts",
                config=config,
            )
            runner.run()

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["workdir"] == tmp_path

    def test_runner_raises_on_nonexistent_workdir(self, tmp_path: Path) -> None:
        """WorkflowRunner raises error when workdir directory does not exist."""
        from kaji_harness.config import KajiConfig
        from kaji_harness.runner import WorkflowRunner

        self._write_config(tmp_path)
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(
            steps=[_step(workdir="/nonexistent/path/that/does/not/exist")],
        )

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = WorkflowRunner(
                workflow=wf,
                issue_number=1,
                project_root=tmp_path,
                artifacts_dir=tmp_path / ".kaji-artifacts",
                config=config,
            )
            with pytest.raises(Exception, match="workdir"):
                runner.run()


# ============================================================
# Large tests — CLI E2E
# ============================================================


@pytest.mark.large
class TestWorkdirConfigE2E:
    """E2E: kaji validate accepts workflow with workdir."""

    def test_validate_workflow_with_workdir(self, tmp_path: Path) -> None:
        """kaji validate accepts a workflow YAML containing workdir."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n'
        )

        skills_dir = project_dir / ".claude" / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Test skill\n")

        wf_path = tmp_path / "workflow.yaml"
        wf_path.write_text(
            dedent("""\
            name: test-workdir
            description: Test workflow with workdir
            workdir: /tmp
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                workdir: /tmp
                on:
                  PASS: end
        """)
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "validate",
                str(wf_path),
                "--project-root",
                str(project_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_validate_workflow_with_relative_workdir_fails(self, tmp_path: Path) -> None:
        """kaji validate rejects a workflow YAML with relative workdir."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n'
        )

        wf_path = tmp_path / "workflow.yaml"
        wf_path.write_text(
            dedent("""\
            name: test-bad-workdir
            description: Test workflow with bad workdir
            workdir: relative/path
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                on:
                  PASS: end
        """)
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "validate",
                str(wf_path),
                "--project-root",
                str(project_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
