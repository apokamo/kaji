"""Tests for kaji --version option."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import _get_version, create_parser


class TestGetVersionSmall:
    """Small tests for _get_version() helper."""

    @pytest.mark.small
    def test_returns_version_string(self) -> None:
        """_get_version() returns the installed package version."""
        from importlib.metadata import version

        expected = version("kaji")
        assert _get_version() == expected

    @pytest.mark.small
    def test_returns_unknown_on_package_not_found(self) -> None:
        """_get_version() returns 'unknown' when package is not installed."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "kaji_harness.cli_main.version",
            side_effect=PackageNotFoundError("kaji"),
        ):
            assert _get_version() == "unknown"


class TestVersionOptionSmall:
    """Small tests for --version CLI argument."""

    @pytest.mark.small
    def test_version_flag_causes_system_exit(self) -> None:
        """--version triggers SystemExit(0)."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0

    @pytest.mark.small
    def test_version_output_contains_prog_and_version(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--version output matches 'kaji X.Y.Z' format."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])
        captured = capsys.readouterr()
        assert captured.out.startswith("kaji ")
        from importlib.metadata import version

        expected_version = version("kaji")
        assert expected_version in captured.out

    @pytest.mark.small
    def test_version_via_cli_entrypoint(self) -> None:
        """python -m kaji_harness.cli_main --version outputs correct format."""
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.startswith("kaji ")
        from importlib.metadata import version

        assert version("kaji") in result.stdout
