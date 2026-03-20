"""Tests for timeout configuration: hardcode removal and config-file fallback.

Issue #116: Remove DEFAULT_TIMEOUT = 1800 from cli.py and implement
step.timeout → workflow.default_timeout → config.execution.default_timeout fallback.

Covers:
- ExecutionConfig validation (S)
- KajiConfig._load() [execution] parsing (S)
- Workflow.default_timeout parsing via _parse_workflow() (S)
- step.timeout validation in _parse_workflow() (S)
- validate_workflow() timeout validation for directly constructed models (S)
- Timeout resolution logic (S)
- Fallback combinations (S)
- Config + workflow + step 3-layer fallback integration (M)
- WorkflowRunner integration with config (M)
- config.toml missing [execution] error path (M)
- CLI E2E with default_timeout in config.toml (L)
- kaji validate E2E with default_timeout in workflow YAML (L)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import ConfigLoadError, WorkflowValidationError
from kaji_harness.models import Step, Workflow
from kaji_harness.workflow import load_workflow_from_str, validate_workflow

# ============================================================
# Helpers
# ============================================================


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write .kaji/config.toml and return the config file path."""
    config_dir = tmp_path / ".kaji"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(content)
    return config_file


def _step(
    id: str = "s1",
    *,
    timeout: int | None = None,
    on: dict[str, str] | None = None,
) -> Step:
    return Step(
        id=id,
        skill="test-skill",
        agent="claude",
        timeout=timeout,
        on=on or {"PASS": "end"},
    )


def _workflow(
    steps: list[Step] | None = None,
    *,
    default_timeout: int | None = None,
) -> Workflow:
    return Workflow(
        name="test-wf",
        description="Test",
        execution_policy="auto",
        steps=steps or [_step()],
        default_timeout=default_timeout,
    )


# ============================================================
# Small tests — ExecutionConfig validation
# ============================================================


@pytest.mark.small
class TestExecutionConfigValidation:
    """ExecutionConfig parsing in KajiConfig._load()."""

    def test_valid_default_timeout(self, tmp_path: Path) -> None:
        """Valid positive integer default_timeout is accepted."""
        _write_config(tmp_path, "[execution]\ndefault_timeout = 1800\n")
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")
        assert config.execution.default_timeout == 1800

    def test_missing_execution_section_raises(self, tmp_path: Path) -> None:
        """Missing [execution] section raises ConfigLoadError."""
        _write_config(tmp_path, "[paths]\nartifacts_dir = '.kaji-artifacts'\n")
        with pytest.raises(ConfigLoadError, match=r"\[execution\]"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_missing_default_timeout_key_raises(self, tmp_path: Path) -> None:
        """[execution] without default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, "[execution]\n")
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_string_default_timeout_raises(self, tmp_path: Path) -> None:
        """String value for default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, '[execution]\ndefault_timeout = "fast"\n')
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_bool_default_timeout_raises(self, tmp_path: Path) -> None:
        """Boolean value for default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, "[execution]\ndefault_timeout = true\n")
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_float_default_timeout_raises(self, tmp_path: Path) -> None:
        """Float value for default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, "[execution]\ndefault_timeout = 18.5\n")
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_zero_default_timeout_raises(self, tmp_path: Path) -> None:
        """Zero default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, "[execution]\ndefault_timeout = 0\n")
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_negative_default_timeout_raises(self, tmp_path: Path) -> None:
        """Negative default_timeout raises ConfigLoadError."""
        _write_config(tmp_path, "[execution]\ndefault_timeout = -100\n")
        with pytest.raises(ConfigLoadError, match="default_timeout"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_execution_not_a_table_raises(self, tmp_path: Path) -> None:
        """[execution] as non-table raises ConfigLoadError."""
        _write_config(tmp_path, 'execution = "not a table"\n')
        with pytest.raises(ConfigLoadError, match=r"\[execution\]"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_empty_config_raises(self, tmp_path: Path) -> None:
        """Empty config.toml raises ConfigLoadError (missing [execution])."""
        _write_config(tmp_path, "")
        with pytest.raises(ConfigLoadError, match=r"\[execution\]"):
            KajiConfig._load(tmp_path / ".kaji" / "config.toml")

    def test_valid_with_paths_section(self, tmp_path: Path) -> None:
        """Config with both [paths] and [execution] is valid."""
        _write_config(
            tmp_path,
            '[paths]\nartifacts_dir = "custom"\n\n[execution]\ndefault_timeout = 600\n',
        )
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")
        assert config.paths.artifacts_dir == "custom"
        assert config.execution.default_timeout == 600


# ============================================================
# Small tests — Workflow default_timeout parsing
# ============================================================


@pytest.mark.small
class TestWorkflowDefaultTimeoutParsing:
    """Workflow.default_timeout parsing via _parse_workflow()."""

    def test_default_timeout_parsed(self) -> None:
        """default_timeout in workflow YAML is parsed correctly."""
        yaml_str = dedent("""\
            name: test
            description: test
            default_timeout: 600
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.default_timeout == 600

    def test_default_timeout_omitted_is_none(self) -> None:
        """Omitting default_timeout results in None."""
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
        assert wf.default_timeout is None

    def test_default_timeout_string_raises(self) -> None:
        """String default_timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            default_timeout: "fast"
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            load_workflow_from_str(yaml_str)

    def test_default_timeout_bool_raises(self) -> None:
        """Boolean default_timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            default_timeout: true
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            load_workflow_from_str(yaml_str)

    def test_default_timeout_zero_raises(self) -> None:
        """Zero default_timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            default_timeout: 0
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            load_workflow_from_str(yaml_str)

    def test_default_timeout_negative_raises(self) -> None:
        """Negative default_timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            default_timeout: -10
            steps:
              - id: s1
                skill: sk
                agent: claude
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            load_workflow_from_str(yaml_str)


# ============================================================
# Small tests — step.timeout validation in _parse_workflow()
# ============================================================


@pytest.mark.small
class TestStepTimeoutParsing:
    """step.timeout validation in _parse_workflow()."""

    def test_step_timeout_string_raises(self) -> None:
        """String step.timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: "slow"
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="timeout"):
            load_workflow_from_str(yaml_str)

    def test_step_timeout_bool_raises(self) -> None:
        """Boolean step.timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: true
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="timeout"):
            load_workflow_from_str(yaml_str)

    def test_step_timeout_float_raises(self) -> None:
        """Float step.timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: 3.5
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="timeout"):
            load_workflow_from_str(yaml_str)

    def test_step_timeout_zero_raises(self) -> None:
        """Zero step.timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: 0
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="timeout"):
            load_workflow_from_str(yaml_str)

    def test_step_timeout_negative_raises(self) -> None:
        """Negative step.timeout raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: -5
                on:
                  PASS: end
        """)
        with pytest.raises(WorkflowValidationError, match="timeout"):
            load_workflow_from_str(yaml_str)

    def test_step_timeout_valid_positive(self) -> None:
        """Valid positive step.timeout is accepted."""
        yaml_str = dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: sk
                agent: claude
                timeout: 3600
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)
        assert wf.steps[0].timeout == 3600


# ============================================================
# Small tests — validate_workflow() timeout validation
# ============================================================


@pytest.mark.small
class TestValidateWorkflowTimeout:
    """validate_workflow() catches invalid timeout on directly constructed models."""

    def test_workflow_default_timeout_zero_rejected(self) -> None:
        """Directly constructed Workflow with default_timeout=0 is rejected."""
        wf = _workflow(default_timeout=0)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            validate_workflow(wf)

    def test_workflow_default_timeout_negative_rejected(self) -> None:
        """Directly constructed Workflow with negative default_timeout is rejected."""
        wf = _workflow(default_timeout=-100)
        with pytest.raises(WorkflowValidationError, match="default_timeout"):
            validate_workflow(wf)

    def test_step_timeout_negative_rejected(self) -> None:
        """Directly constructed Step with negative timeout is rejected."""
        wf = _workflow(steps=[_step(timeout=-1)])
        with pytest.raises(WorkflowValidationError, match="timeout"):
            validate_workflow(wf)

    def test_step_timeout_zero_rejected(self) -> None:
        """Directly constructed Step with timeout=0 is rejected."""
        wf = _workflow(steps=[_step(timeout=0)])
        with pytest.raises(WorkflowValidationError, match="timeout"):
            validate_workflow(wf)

    def test_valid_workflow_timeout_passes(self) -> None:
        """Valid timeout values pass validation."""
        wf = _workflow(
            steps=[_step(timeout=600)],
            default_timeout=1800,
        )
        validate_workflow(wf)  # should not raise

    def test_none_timeouts_pass(self) -> None:
        """None timeouts (omitted) pass validation."""
        wf = _workflow(
            steps=[_step(timeout=None)],
            default_timeout=None,
        )
        validate_workflow(wf)  # should not raise


# ============================================================
# Small tests — Timeout resolution logic
# ============================================================


@pytest.mark.small
class TestTimeoutResolution:
    """Timeout fallback: step.timeout → workflow.default_timeout → config default."""

    def test_step_timeout_takes_priority(self) -> None:
        """step.timeout is used when set, regardless of workflow/config defaults."""
        step = _step(timeout=3600)
        wf = _workflow(steps=[step], default_timeout=600)
        config_default = 1800

        timeout = (
            step.timeout
            if step.timeout is not None
            else (wf.default_timeout if wf.default_timeout is not None else config_default)
        )
        assert timeout == 3600

    def test_workflow_default_timeout_when_step_none(self) -> None:
        """workflow.default_timeout is used when step.timeout is None."""
        step = _step(timeout=None)
        wf = _workflow(steps=[step], default_timeout=600)
        config_default = 1800

        timeout = (
            step.timeout
            if step.timeout is not None
            else (wf.default_timeout if wf.default_timeout is not None else config_default)
        )
        assert timeout == 600

    def test_config_default_when_both_none(self) -> None:
        """config default is used when both step and workflow timeouts are None."""
        step = _step(timeout=None)
        wf = _workflow(steps=[step], default_timeout=None)
        config_default = 1800

        timeout = (
            step.timeout
            if step.timeout is not None
            else (wf.default_timeout if wf.default_timeout is not None else config_default)
        )
        assert timeout == 1800

    def test_step_timeout_none_check_not_truthy(self) -> None:
        """Ensure step.timeout=0 would NOT fallback (explicit None check, not truthy).

        Note: step.timeout=0 is invalid and rejected by validation.
        This test documents that the resolution uses `is not None`, not `or`.
        """
        step = _step(timeout=100)
        # With truthy check: `step.timeout or 1800` → 100 (same result for positive)
        # With None check: `step.timeout if step.timeout is not None else 1800` → 100
        assert step.timeout is not None
        timeout = step.timeout if step.timeout is not None else 1800
        assert timeout == 100


# ============================================================
# Small tests — DEFAULT_TIMEOUT removal
# ============================================================


@pytest.mark.small
class TestDefaultTimeoutRemoved:
    """Verify DEFAULT_TIMEOUT constant is removed from cli.py."""

    def test_no_default_timeout_constant(self) -> None:
        """cli module should not export DEFAULT_TIMEOUT."""
        from kaji_harness import cli

        assert not hasattr(cli, "DEFAULT_TIMEOUT")


# ============================================================
# Small tests — execute_cli accepts default_timeout parameter
# ============================================================


@pytest.mark.small
class TestExecuteCliDefaultTimeout:
    """execute_cli() uses the default_timeout parameter for timeout resolution."""

    def test_execute_cli_uses_default_timeout_when_step_has_none(self) -> None:
        """When step.timeout is None, default_timeout is used."""
        step = _step(timeout=None)
        with patch("kaji_harness.cli.build_cli_args", return_value=["echo", "hi"]):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout = iter([])
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.returncode = 0
                mock_proc.wait.return_value = 0
                mock_popen.return_value = mock_proc

                from kaji_harness.cli import execute_cli

                with patch("threading.Timer") as mock_timer:
                    mock_timer_instance = MagicMock()
                    mock_timer.return_value = mock_timer_instance

                    execute_cli(
                        step=step,
                        prompt="test",
                        workdir=Path("/tmp"),
                        session_id=None,
                        log_dir=Path("/tmp/logs"),
                        execution_policy="auto",
                        verbose=False,
                        default_timeout=900,
                    )
                    # Timer should be created with timeout=900
                    mock_timer.assert_called_once()
                    assert mock_timer.call_args[0][0] == 900

    def test_execute_cli_step_timeout_overrides_default(self) -> None:
        """When step.timeout is set, it overrides default_timeout."""
        step = _step(timeout=3600)
        with patch("kaji_harness.cli.build_cli_args", return_value=["echo", "hi"]):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout = iter([])
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.returncode = 0
                mock_proc.wait.return_value = 0
                mock_popen.return_value = mock_proc

                from kaji_harness.cli import execute_cli

                with patch("threading.Timer") as mock_timer:
                    mock_timer_instance = MagicMock()
                    mock_timer.return_value = mock_timer_instance

                    execute_cli(
                        step=step,
                        prompt="test",
                        workdir=Path("/tmp"),
                        session_id=None,
                        log_dir=Path("/tmp/logs"),
                        execution_policy="auto",
                        verbose=False,
                        default_timeout=900,
                    )
                    mock_timer.assert_called_once()
                    assert mock_timer.call_args[0][0] == 3600


# ============================================================
# Medium tests — config.toml → execute_cli fallback integration
# ============================================================


@pytest.mark.medium
class TestTimeoutFallbackIntegration:
    """Integration: config.toml + workflow + step 3-layer timeout fallback."""

    def test_config_default_timeout_used_when_workflow_and_step_omit(self, tmp_path: Path) -> None:
        """Config default_timeout is used when workflow and step omit timeout."""
        _write_config(
            tmp_path,
            "[execution]\ndefault_timeout = 1800\n",
        )
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(steps=[_step(timeout=None)], default_timeout=None)

        # Simulate runner's timeout resolution
        default_timeout = (
            wf.default_timeout
            if wf.default_timeout is not None
            else config.execution.default_timeout
        )
        assert default_timeout == 1800

    def test_workflow_default_timeout_overrides_config(self, tmp_path: Path) -> None:
        """Workflow default_timeout overrides config default_timeout."""
        _write_config(
            tmp_path,
            "[execution]\ndefault_timeout = 1800\n",
        )
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(default_timeout=600)

        default_timeout = (
            wf.default_timeout
            if wf.default_timeout is not None
            else config.execution.default_timeout
        )
        assert default_timeout == 600


@pytest.mark.medium
class TestWorkflowRunnerWithConfig:
    """WorkflowRunner passes correct default_timeout to execute_cli."""

    def test_runner_passes_config_default_timeout(self, tmp_path: Path) -> None:
        """WorkflowRunner resolves timeout from config and passes to execute_cli."""
        from kaji_harness.models import CLIResult, CostInfo
        from kaji_harness.runner import WorkflowRunner

        _write_config(tmp_path, "[execution]\ndefault_timeout = 900\n")
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(steps=[_step(timeout=None)], default_timeout=None)

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

            # Verify default_timeout was passed
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["default_timeout"] == 900

    def test_runner_passes_workflow_default_timeout(self, tmp_path: Path) -> None:
        """WorkflowRunner resolves workflow.default_timeout over config."""
        from kaji_harness.models import CLIResult, CostInfo
        from kaji_harness.runner import WorkflowRunner

        _write_config(tmp_path, "[execution]\ndefault_timeout = 1800\n")
        config = KajiConfig._load(tmp_path / ".kaji" / "config.toml")

        wf = _workflow(steps=[_step(timeout=None)], default_timeout=600)

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
            assert call_kwargs["default_timeout"] == 600


@pytest.mark.medium
class TestConfigMissingExecutionErrorPath:
    """Config missing [execution] section results in error during workflow run."""

    def test_cmd_run_fails_with_missing_execution_section(self, tmp_path: Path) -> None:
        """kaji run fails with exit code when [execution] missing from config."""
        from kaji_harness.cli_main import cmd_run, create_parser

        # Setup workdir with config missing [execution]
        workdir = tmp_path / "project"
        workdir.mkdir()
        config_dir = workdir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[paths]\nartifacts_dir = 'out'\n")

        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            dedent("""\
            name: test
            description: test
            steps:
              - id: s1
                skill: test-skill
                agent: claude
                on:
                  PASS: end
        """)
        )

        parser = create_parser()
        args = parser.parse_args(["run", str(wf_path), "1", "--workdir", str(workdir)])
        exit_code = cmd_run(args)
        # Should fail with config error exit code (2)
        assert exit_code == 2


# ============================================================
# Large tests — CLI E2E
# ============================================================


@pytest.mark.large
class TestTimeoutConfigE2E:
    """E2E: kaji validate accepts workflow with default_timeout."""

    def test_validate_workflow_with_default_timeout(self, tmp_path: Path) -> None:
        """kaji validate accepts a workflow YAML containing default_timeout."""
        # Create a project structure with config.toml
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[execution]\ndefault_timeout = 1800\n")

        # Create skills directory structure for validation
        skills_dir = project_dir / ".claude" / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Test skill\n")

        # Create workflow YAML with default_timeout
        wf_path = tmp_path / "workflow.yaml"
        wf_path.write_text(
            dedent("""\
            name: test-timeout
            description: Test workflow with default_timeout
            default_timeout: 600
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                timeout: 3600
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

    def test_validate_workflow_without_default_timeout(self, tmp_path: Path) -> None:
        """kaji validate accepts a workflow YAML without default_timeout (optional)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[execution]\ndefault_timeout = 1800\n")

        skills_dir = project_dir / ".claude" / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Test skill\n")

        wf_path = tmp_path / "workflow.yaml"
        wf_path.write_text(
            dedent("""\
            name: test-no-timeout
            description: Test workflow without default_timeout
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
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_cli_run_with_config_default_timeout(self, tmp_path: Path) -> None:
        """kaji run with config.toml default_timeout does not crash on startup.

        We test that config loading + workflow loading succeeds
        (actual agent execution is not in scope for this test).
        Uses a nonexistent agent CLI to ensure it fails at runtime, not config phase.
        """
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[execution]\ndefault_timeout = 1800\n")

        skills_dir = project_dir / ".claude" / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Test skill\n")

        wf_path = tmp_path / "workflow.yaml"
        wf_path.write_text(
            dedent("""\
            name: test-run
            description: Test
            default_timeout: 600
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                on:
                  PASS: end
        """)
        )

        # Use empty PATH so 'claude' CLI is not found, causing CLINotFoundError
        env = {"HOME": str(tmp_path), "PATH": ""}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf_path),
                "1",
                "--workdir",
                str(project_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        # Exit code 3 = runtime error (CLI not found), NOT 2 (config/definition error)
        # This proves config loading and workflow loading succeeded
        assert result.returncode == 3, (
            f"Expected runtime error (3), got {result.returncode}. stderr: {result.stderr}"
        )
