"""Tests for kaji_harness.cli_main — kaji run CLI entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import cmd_run, create_parser, main
from kaji_harness.errors import (
    CLIExecutionError,
    CLINotFoundError,
    HarnessError,
    InvalidTransition,
    InvalidVerdictValue,
    MissingResumeSessionError,
    SecurityError,
    SkillNotFound,
    StepTimeoutError,
    VerdictNotFound,
    VerdictParseError,
    WorkflowValidationError,
)
from kaji_harness.models import Verdict

# ============================================================
# Fixtures
# ============================================================

MINIMAL_WORKFLOW_YAML = """\
name: test
description: test workflow
steps:
  - id: step1
    skill: test-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


@pytest.fixture()
def workflow_file(tmp_path: Path) -> Path:
    """Create a minimal valid workflow YAML file."""
    p = tmp_path / "workflow.yaml"
    p.write_text(MINIMAL_WORKFLOW_YAML)
    return p


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """Create a temporary working directory."""
    d = tmp_path / "workdir"
    d.mkdir()
    return d


# ============================================================
# Small tests — argument parsing
# ============================================================


class TestParserSmall:
    """Small: argparse argument parsing."""

    @pytest.mark.small
    def test_run_subcommand_basic_args(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "workflow.yaml", "42"])
        assert args.workflow == Path("workflow.yaml")
        assert args.issue == 42
        assert args.from_step is None
        assert args.single_step is None
        assert args.quiet is False

    @pytest.mark.small
    def test_run_subcommand_all_options(self, tmp_path: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(
            [
                "run",
                "w.yaml",
                "99",
                "--from",
                "step-a",
                "--workdir",
                str(tmp_path),
                "--quiet",
            ]
        )
        assert args.from_step == "step-a"
        assert args.workdir == Path(str(tmp_path))
        assert args.quiet is True

    @pytest.mark.small
    def test_run_subcommand_step_option(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "w.yaml", "1", "--step", "impl"])
        assert args.single_step == "impl"

    @pytest.mark.small
    def test_no_subcommand_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([])
        assert exc_info.value.code != 0


# ============================================================
# Small tests — --from / --step mutual exclusion
# ============================================================


class TestMutualExclusionSmall:
    """Small: --from and --step are mutually exclusive."""

    @pytest.mark.small
    def test_from_and_step_exclusive(self, workflow_file: Path, workdir: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--from",
            "a",
            "--step",
            "b",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2

    @pytest.mark.small
    def test_from_alone_is_valid(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--from",
                "step1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0

    @pytest.mark.small
    def test_step_alone_is_valid(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--step",
                "step1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0


# ============================================================
# Small tests — --workdir validation
# ============================================================


class TestWorkdirValidationSmall:
    """Small: --workdir pre-validation."""

    @pytest.mark.small
    def test_nonexistent_workdir(self, workflow_file: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            "/nonexistent/path/abc",
        )
        assert exit_code == 2

    @pytest.mark.small
    def test_file_as_workdir(self, workflow_file: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            str(workflow_file),  # file, not dir
        )
        assert exit_code == 2


# ============================================================
# Small tests — exit code mapping
# ============================================================


class TestExitCodeMappingSmall:
    """Small: exception → exit code mapping."""

    @pytest.mark.small
    @pytest.mark.parametrize(
        "exception,expected_code",
        [
            (WorkflowValidationError("bad"), 2),
            (SkillNotFound("missing"), 2),
            (SecurityError("traversal"), 2),
            (CLIExecutionError("s", 1, "err"), 3),
            (CLINotFoundError("not found"), 3),
            (StepTimeoutError("s", 30), 3),
            (MissingResumeSessionError("s", "t"), 3),
            (InvalidTransition("s", "v"), 3),
            (VerdictNotFound("no verdict"), 3),
            (VerdictParseError("bad parse"), 3),
            (InvalidVerdictValue("bad value"), 3),
        ],
        ids=[
            "WorkflowValidationError",
            "SkillNotFound",
            "SecurityError",
            "CLIExecutionError",
            "CLINotFoundError",
            "StepTimeoutError",
            "MissingResumeSessionError",
            "InvalidTransition",
            "VerdictNotFound",
            "VerdictParseError",
            "InvalidVerdictValue",
        ],
    )
    def test_harness_error_exit_codes(
        self,
        workflow_file: Path,
        workdir: Path,
        exception: HarnessError,
        expected_code: int,
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = exception
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == expected_code

    @pytest.mark.small
    def test_unexpected_exception_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = RuntimeError("boom")
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1

    @pytest.mark.small
    def test_abort_verdict_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("ABORT", "reason", "ev", "sug")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1


# ============================================================
# Medium tests — integration with file I/O and mocked runner
# ============================================================


class TestCmdRunMedium:
    """Medium: cmd_run with real files + mocked WorkflowRunner."""

    @pytest.mark.medium
    def test_successful_run(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "done", "all good", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "42",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "42" in captured.out  # issue number in summary

    @pytest.mark.medium
    def test_invalid_yaml_exit_2(
        self, tmp_path: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("steps: not_a_list")
        exit_code = cmd_run_with_args(
            str(bad_yaml),
            "1",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.err  # error message in stderr

    @pytest.mark.medium
    def test_nonexistent_workflow_file(
        self, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            "/no/such/file.yaml",
            "1",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "No such file" in captured.err

    @pytest.mark.medium
    def test_cli_execution_error_exit_3(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = CLIExecutionError("s1", 1, "fail")
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 3
        captured = capsys.readouterr()
        assert captured.err

    @pytest.mark.medium
    def test_abort_verdict_exit_1(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("ABORT", "blocked", "ev", "sug")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "ABORT" in captured.err or "blocked" in captured.err

    @pytest.mark.medium
    def test_workdir_nonexistent_exit_2_with_message(
        self, workflow_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            "/nonexistent/dir",
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "workdir" in captured.err.lower() or "directory" in captured.err.lower()

    @pytest.mark.medium
    def test_quiet_flag_passed_to_runner(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
                "--quiet",
            )
            call_kwargs = mock_runner.call_args
            assert call_kwargs.kwargs.get("verbose") is False or (
                len(call_kwargs.args) > 0 and not call_kwargs.kwargs.get("verbose", True)
            )


class TestMainMedium:
    """Medium: main() function integration."""

    @pytest.mark.medium
    def test_main_returns_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = main(
                [
                    "run",
                    str(workflow_file),
                    "1",
                    "--workdir",
                    str(workdir),
                ]
            )
        assert exit_code == 0

    @pytest.mark.medium
    def test_help_output(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "run", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "workflow" in result.stdout.lower()
        assert "issue" in result.stdout.lower()


# ============================================================
# Large tests — real subprocess execution
# ============================================================


class TestCLILarge:
    """Large: real subprocess execution of kaji CLI."""

    @pytest.mark.large
    def test_kaji_entrypoint_help(self) -> None:
        """The `kaji` console script entrypoint should be functional."""
        import shutil

        kaji_path = shutil.which("kaji")
        if kaji_path is None:
            pytest.skip("kaji entrypoint not installed (run pip install -e .)")
        result = subprocess.run(
            ["kaji", "run", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "--from" in result.stdout
        assert "--step" in result.stdout
        assert "--workdir" in result.stdout
        assert "--quiet" in result.stdout

    @pytest.mark.large
    def test_kaji_run_with_valid_workflow_missing_agent_cli(
        self,
        tmp_path: Path,
    ) -> None:
        """With a valid workflow and skill, missing agent CLI should yield exit 3."""
        # Create workflow YAML referencing a skill
        wf = tmp_path / "workflow.yaml"
        wf.write_text(MINIMAL_WORKFLOW_YAML)

        # Create minimal skill directory so skill validation passes
        workdir = tmp_path / "project"
        workdir.mkdir()
        skill_dir = workdir / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test Skill\n")

        # Restrict PATH to only the Python executable's directory so that
        # agent CLIs (claude, codex, gemini) are guaranteed not to be found,
        # regardless of the host environment.
        python_dir = str(Path(sys.executable).parent)
        env = {**__import__("os").environ, "PATH": python_dir}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "999",
                "--workdir",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        # Should fail with exit 3 (runtime error) because the agent CLI
        # (claude) cannot be found on the restricted PATH.
        assert result.returncode == 3
        assert "not found" in result.stderr.lower()


# ============================================================
# Helpers
# ============================================================


def cmd_run_with_args(*args: str) -> int:
    """Parse args and call cmd_run, returning exit code."""
    parser = create_parser()
    parsed = parser.parse_args(["run", *args])
    return cmd_run(parsed)
