"""Tests for dao validate subcommand.

Covers S/M/L test sizes for the `dao validate <file>...` CLI subcommand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dao_harness.cli_main import cmd_validate, create_parser, main

# ============================================================
# Shared fixtures
# ============================================================

VALID_WORKFLOW_YAML = """\
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

INVALID_SCHEMA_YAML = """\
name: bad
steps: not_a_list
"""

INVALID_SYNTAX_YAML = """\
name: bad
steps:
  - id: step1
    on: {
"""


@pytest.fixture()
def valid_yaml(tmp_path: Path) -> Path:
    """Create a valid workflow YAML file."""
    p = tmp_path / "valid.yaml"
    p.write_text(VALID_WORKFLOW_YAML)
    return p


@pytest.fixture()
def invalid_schema_yaml(tmp_path: Path) -> Path:
    """Create an invalid (schema violation) workflow YAML file."""
    p = tmp_path / "invalid_schema.yaml"
    p.write_text(INVALID_SCHEMA_YAML)
    return p


@pytest.fixture()
def invalid_syntax_yaml(tmp_path: Path) -> Path:
    """Create an invalid (YAML syntax error) workflow YAML file."""
    p = tmp_path / "invalid_syntax.yaml"
    p.write_text(INVALID_SYNTAX_YAML)
    return p


# ============================================================
# Small tests — cmd_validate logic
# ============================================================


class TestCmdValidateSmall:
    """Small: cmd_validate() unit logic with capsys."""

    @pytest.mark.small
    def test_valid_yaml_exit_0(self, valid_yaml: Path, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = _cmd_validate_with_args(str(valid_yaml))
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert str(valid_yaml) in captured.out

    @pytest.mark.small
    def test_invalid_schema_exit_1(
        self, invalid_schema_yaml: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = _cmd_validate_with_args(str(invalid_schema_yaml))
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.err
        assert str(invalid_schema_yaml) in captured.err

    @pytest.mark.small
    def test_invalid_syntax_exit_1(
        self, invalid_syntax_yaml: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = _cmd_validate_with_args(str(invalid_syntax_yaml))
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.err

    @pytest.mark.small
    def test_nonexistent_file_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = _cmd_validate_with_args("/no/such/file.yaml")
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.err
        assert "not found" in captured.err.lower() or "File not found" in captured.err

    @pytest.mark.small
    def test_multiple_valid_files_exit_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text(VALID_WORKFLOW_YAML)
        f2.write_text(VALID_WORKFLOW_YAML)
        exit_code = _cmd_validate_with_args(str(f1), str(f2))
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.count("✓") == 2

    @pytest.mark.small
    def test_multiple_files_partial_failure_exit_1(
        self, valid_yaml: Path, invalid_schema_yaml: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = _cmd_validate_with_args(str(valid_yaml), str(invalid_schema_yaml))
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert "✗" in captured.err
        assert "Validation failed" in captured.err

    @pytest.mark.small
    def test_no_args_exit_2(self) -> None:
        """argparse should exit 2 when no files are provided."""
        with pytest.raises(SystemExit) as exc_info:
            main(["validate"])
        assert exc_info.value.code == 2


# ============================================================
# Medium tests — real file I/O integration
# ============================================================


class TestCmdValidateMedium:
    """Medium: integration with real file I/O."""

    @pytest.mark.medium
    def test_real_file_pipeline(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """End-to-end: write YAML to disk, validate via cmd_validate."""
        f = tmp_path / "workflow.yaml"
        f.write_text(VALID_WORKFLOW_YAML)
        exit_code = _cmd_validate_with_args(str(f))
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "✓" in captured.out

    @pytest.mark.medium
    def test_mixed_files_all_processed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """All files are processed even when some fail (no early abort)."""
        good = tmp_path / "good.yaml"
        bad = tmp_path / "bad.yaml"
        good.write_text(VALID_WORKFLOW_YAML)
        bad.write_text(INVALID_SCHEMA_YAML)
        exit_code = _cmd_validate_with_args(str(good), str(bad))
        assert exit_code == 1
        captured = capsys.readouterr()
        # Both files should appear in output
        assert str(good) in captured.out
        assert str(bad) in captured.err
        assert "Validation failed: 1 of 2" in captured.err

    @pytest.mark.medium
    def test_permission_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Unreadable file should produce exit 1 with error message."""
        f = tmp_path / "noperm.yaml"
        f.write_text(VALID_WORKFLOW_YAML)
        f.chmod(0o000)
        try:
            exit_code = _cmd_validate_with_args(str(f))
            assert exit_code == 1
            captured = capsys.readouterr()
            assert "✗" in captured.err
        finally:
            f.chmod(0o644)  # restore for cleanup

    @pytest.mark.medium
    def test_main_validate_returns_exit_code(self, valid_yaml: Path) -> None:
        """main(["validate", ...]) returns correct exit code."""
        exit_code = main(["validate", str(valid_yaml)])
        assert exit_code == 0

    @pytest.mark.medium
    def test_main_validate_invalid_returns_1(self, invalid_schema_yaml: Path) -> None:
        exit_code = main(["validate", str(invalid_schema_yaml)])
        assert exit_code == 1


# ============================================================
# Large tests — real subprocess execution
# ============================================================


class TestCLIValidateLarge:
    """Large: real subprocess execution of `dao validate`."""

    @pytest.mark.large
    def test_dao_validate_valid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "workflow.yaml"
        f.write_text(VALID_WORKFLOW_YAML)
        result = subprocess.run(
            [sys.executable, "-m", "dao_harness.cli_main", "validate", str(f)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "✓" in result.stdout

    @pytest.mark.large
    def test_dao_validate_invalid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text(INVALID_SCHEMA_YAML)
        result = subprocess.run(
            [sys.executable, "-m", "dao_harness.cli_main", "validate", str(f)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "✗" in result.stderr

    @pytest.mark.large
    def test_dao_validate_no_args_exit_2(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "dao_harness.cli_main", "validate"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2

    @pytest.mark.large
    def test_dao_validate_mixed_files(self, tmp_path: Path) -> None:
        good = tmp_path / "good.yaml"
        bad = tmp_path / "bad.yaml"
        good.write_text(VALID_WORKFLOW_YAML)
        bad.write_text(INVALID_SCHEMA_YAML)
        result = subprocess.run(
            [sys.executable, "-m", "dao_harness.cli_main", "validate", str(good), str(bad)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "✓" in result.stdout
        assert "✗" in result.stderr
        assert "Validation failed" in result.stderr


# ============================================================
# Helpers
# ============================================================


def _cmd_validate_with_args(*args: str) -> int:
    """Parse args and call cmd_validate, returning exit code."""
    parser = create_parser()
    parsed = parser.parse_args(["validate", *args])
    return cmd_validate(parsed)
