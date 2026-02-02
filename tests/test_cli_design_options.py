"""Tests for CLI design command options (--workdir, --dry-run)."""

import sys
from io import StringIO
from unittest.mock import patch

import pytest


class TestDesignParserOptions:
    """design コマンドの --workdir と --dry-run オプションのテスト."""

    def test_workdir_option_exists(self) -> None:
        """--workdir オプションが design サブコマンドに存在すること."""
        from src.cli import main

        with patch.object(sys, "argv", ["dao", "design", "--help"]):
            with patch("sys.stdout", new=StringIO()) as mock_stdout:
                with pytest.raises(SystemExit):
                    main()

        help_output = mock_stdout.getvalue()
        assert "--workdir" in help_output and "-w" in help_output

    def test_dry_run_option_exists(self) -> None:
        """--dry-run オプションが design サブコマンドに存在すること."""
        from src.cli import main

        with patch.object(sys, "argv", ["dao", "design", "--help"]):
            with patch("sys.stdout", new=StringIO()) as mock_stdout:
                with pytest.raises(SystemExit):
                    main()

        help_output = mock_stdout.getvalue()
        assert "--dry-run" in help_output

    def test_workdir_accepts_path(self) -> None:
        """--workdir がパスを受け入れること."""
        with patch.object(
            sys,
            "argv",
            [
                "dao",
                "design",
                "--issue",
                "https://github.com/test/repo/issues/1",
                "--workdir",
                "/tmp/test",
            ],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.workdir == "/tmp/test"

    def test_workdir_short_option(self) -> None:
        """--workdir の短縮形 -w が使えること."""
        with patch.object(
            sys,
            "argv",
            ["dao", "design", "--issue", "https://github.com/test/repo/issues/1", "-w", "."],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.workdir == "."

    def test_dry_run_is_boolean_flag(self) -> None:
        """--dry-run が真偽値フラグであること."""
        with patch.object(
            sys,
            "argv",
            ["dao", "design", "--issue", "https://github.com/test/repo/issues/1", "--dry-run"],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.dry_run is True

    def test_dry_run_default_is_false(self) -> None:
        """--dry-run 未指定時は False であること."""
        with patch.object(
            sys,
            "argv",
            ["dao", "design", "--issue", "https://github.com/test/repo/issues/1"],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.dry_run is False

    def test_workdir_default_is_none(self) -> None:
        """--workdir 未指定時は None であること."""
        with patch.object(
            sys,
            "argv",
            ["dao", "design", "--issue", "https://github.com/test/repo/issues/1"],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.workdir is None

    def test_both_options_together(self) -> None:
        """--workdir と --dry-run の組み合わせが動作すること."""
        with patch.object(
            sys,
            "argv",
            [
                "dao",
                "design",
                "--issue",
                "https://github.com/test/repo/issues/1",
                "-w",
                "/tmp/test",
                "--dry-run",
            ],
        ):
            with patch("src.cli.run_design_workflow") as mock_runner:
                mock_runner.return_value = 0
                from src.cli import main

                result = main()

        assert result == 0
        call_args = mock_runner.call_args[0][0]
        assert call_args.workdir == "/tmp/test"
        assert call_args.dry_run is True
