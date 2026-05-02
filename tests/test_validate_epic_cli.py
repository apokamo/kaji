"""Tests for `kaji validate-epic` CLI subcommand.

Issue: #164
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kaji_harness.cli_main import main

VALID_EPIC = textwrap.dedent(
    """
    name: rel-v1
    description: Release v1
    members:
      - issue: 1
        merge_order: 1
      - issue: 2
        depends_on: [1]
        merge_order: 2
    """
).strip()

INVALID_EPIC_CYCLE = textwrap.dedent(
    """
    name: bad
    members:
      - issue: 1
        depends_on: [2]
      - issue: 2
        depends_on: [1]
    """
).strip()


# ============================================================
# Small — main() in-process
# ============================================================


class TestValidateEpicSmall:
    @pytest.mark.small
    def test_valid_returns_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "epic.yaml"
        path.write_text(VALID_EPIC)
        exit_code = main(["validate-epic", str(path)])
        assert exit_code == 0
        out = capsys.readouterr()
        assert "✓" in out.out

    @pytest.mark.small
    def test_invalid_cycle_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "epic.yaml"
        path.write_text(INVALID_EPIC_CYCLE)
        exit_code = main(["validate-epic", str(path)])
        assert exit_code == 1
        out = capsys.readouterr()
        assert "cyclic dependency" in out.err

    @pytest.mark.small
    def test_missing_file_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["validate-epic", str(tmp_path / "missing.yaml")])
        assert exit_code == 1
        out = capsys.readouterr()
        assert "File not found" in out.err


# ============================================================
# Medium — subprocess kaji command
# ============================================================


class TestValidateEpicMedium:
    @pytest.mark.medium
    def test_subprocess_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "epic.yaml"
        path.write_text(VALID_EPIC)
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "validate-epic", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "✓" in result.stdout

    @pytest.mark.medium
    def test_subprocess_invalid(self, tmp_path: Path) -> None:
        path = tmp_path / "epic.yaml"
        path.write_text(INVALID_EPIC_CYCLE)
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "validate-epic", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "cyclic" in result.stderr
