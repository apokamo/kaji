"""Tests for bugfix_agent CLI subcommands.

This module tests the CLI entry point with subcommands:
- `design`: Run DesignWorkflow
- `bugfix`: Run bugfix workflow (9-state)
- Backward compatibility: URL-only argument → bugfix workflow

Issue #36: v5 Phase2 - DesignWorkflow + CLI Subcommands
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from src.bugfix_agent.main import create_parser, main, route_command
from src.core.url_utils import is_valid_issue_url


class TestUrlValidation:
    """URL validation function tests (shared with IssueProvider)."""

    def test_valid_issue_url(self) -> None:
        """Valid GitHub Issue URL returns True."""
        assert is_valid_issue_url("https://github.com/owner/repo/issues/123")

    def test_valid_issue_url_with_trailing_slash(self) -> None:
        """Trailing slash is accepted."""
        assert is_valid_issue_url("https://github.com/owner/repo/issues/123/")

    def test_invalid_pr_url(self) -> None:
        """PR URL returns False."""
        assert not is_valid_issue_url("https://github.com/owner/repo/pull/123")

    def test_invalid_http_url(self) -> None:
        """HTTP (not HTTPS) returns False."""
        assert not is_valid_issue_url("http://github.com/owner/repo/issues/123")

    def test_invalid_no_protocol(self) -> None:
        """URL without protocol returns False."""
        assert not is_valid_issue_url("github.com/owner/repo/issues/123")


class TestCreateParser:
    """Tests for CLI argument parser creation."""

    def test_design_subcommand_accepts_issue_url(self) -> None:
        """design subcommand accepts issue_url argument."""
        parser = create_parser()
        args = parser.parse_args(["design", "https://github.com/owner/repo/issues/1"])
        assert args.command == "design"
        assert args.issue_url == "https://github.com/owner/repo/issues/1"

    def test_bugfix_subcommand_accepts_issue_url(self) -> None:
        """bugfix subcommand accepts issue_url argument."""
        parser = create_parser()
        args = parser.parse_args(["bugfix", "https://github.com/owner/repo/issues/1"])
        assert args.command == "bugfix"
        assert args.issue_url == "https://github.com/owner/repo/issues/1"

    def test_workdir_option(self) -> None:
        """--workdir option is accepted."""
        parser = create_parser()
        args = parser.parse_args(
            ["design", "https://github.com/owner/repo/issues/1", "--workdir", "/tmp/work"]
        )
        assert args.workdir == "/tmp/work"

    def test_workdir_short_option(self) -> None:
        """-w short option for workdir."""
        parser = create_parser()
        args = parser.parse_args(
            ["design", "https://github.com/owner/repo/issues/1", "-w", "/tmp/work"]
        )
        assert args.workdir == "/tmp/work"

    def test_dry_run_flag(self) -> None:
        """--dry-run flag is accepted."""
        parser = create_parser()
        args = parser.parse_args(["design", "https://github.com/owner/repo/issues/1", "--dry-run"])
        assert args.dry_run is True

    def test_verbose_flag(self) -> None:
        """--verbose flag is accepted."""
        parser = create_parser()
        args = parser.parse_args(["design", "https://github.com/owner/repo/issues/1", "--verbose"])
        assert args.verbose is True

    def test_verbose_short_flag(self) -> None:
        """-v short flag for verbose."""
        parser = create_parser()
        args = parser.parse_args(["design", "https://github.com/owner/repo/issues/1", "-v"])
        assert args.verbose is True


class TestBackwardCompatibility:
    """Tests for backward compatibility with URL-only argument."""

    def test_url_only_argument_returns_bugfix_command(self) -> None:
        """URL as first argument routes to bugfix command."""
        # Parser doesn't directly support URL-only, but route_command does
        args = Namespace(
            command=None,
            issue_url="https://github.com/owner/repo/issues/1",
            workdir=None,
            dry_run=False,
            verbose=False,
        )
        routed = route_command(args)
        assert routed.command == "bugfix"
        assert routed.issue_url == "https://github.com/owner/repo/issues/1"

    def test_non_url_first_argument_raises_error(self) -> None:
        """Non-URL, non-subcommand first argument raises error."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["invalid-command"])


class TestRouteCommand:
    """Tests for command routing logic."""

    def test_explicit_design_command_preserved(self) -> None:
        """Explicit design command is preserved."""
        args = Namespace(
            command="design",
            issue_url="https://github.com/owner/repo/issues/1",
            workdir=None,
            dry_run=False,
            verbose=False,
        )
        routed = route_command(args)
        assert routed.command == "design"

    def test_explicit_bugfix_command_preserved(self) -> None:
        """Explicit bugfix command is preserved."""
        args = Namespace(
            command="bugfix",
            issue_url="https://github.com/owner/repo/issues/1",
            workdir=None,
            dry_run=False,
            verbose=False,
        )
        routed = route_command(args)
        assert routed.command == "bugfix"

    def test_none_command_with_valid_url_routes_to_bugfix(self) -> None:
        """None command with valid URL routes to bugfix."""
        args = Namespace(
            command=None,
            issue_url="https://github.com/owner/repo/issues/1",
            workdir=None,
            dry_run=False,
            verbose=False,
        )
        routed = route_command(args)
        assert routed.command == "bugfix"

    def test_none_command_with_invalid_url_raises_error(self) -> None:
        """None command with invalid URL raises ValueError."""
        args = Namespace(
            command=None,
            issue_url="not-a-valid-url",
            workdir=None,
            dry_run=False,
            verbose=False,
        )
        with pytest.raises(ValueError, match="Invalid GitHub Issue URL"):
            route_command(args)


class TestMainFunction:
    """Tests for main() entry point."""

    @patch("src.bugfix_agent.main.run_design_workflow")
    def test_design_command_calls_design_workflow(self, mock_run_design: MagicMock) -> None:
        """design command calls run_design_workflow."""
        mock_run_design.return_value = 0

        result = main(["design", "https://github.com/owner/repo/issues/1"])

        assert result == 0
        mock_run_design.assert_called_once()
        call_args = mock_run_design.call_args[0][0]
        # Design runner expects 'issue' not 'issue_url'
        assert call_args.issue == "https://github.com/owner/repo/issues/1"

    @patch("src.bugfix_agent.main.run_bugfix_workflow")
    def test_bugfix_command_calls_bugfix_workflow(self, mock_run_bugfix: MagicMock) -> None:
        """bugfix command calls run_bugfix_workflow."""
        mock_run_bugfix.return_value = 0

        result = main(["bugfix", "https://github.com/owner/repo/issues/1"])

        assert result == 0
        mock_run_bugfix.assert_called_once()
        call_args = mock_run_bugfix.call_args[0][0]
        assert call_args.issue_url == "https://github.com/owner/repo/issues/1"

    @patch("src.bugfix_agent.main.run_bugfix_workflow")
    def test_url_only_routes_to_bugfix(self, mock_run_bugfix: MagicMock) -> None:
        """URL-only argument routes to bugfix workflow (backward compat)."""
        mock_run_bugfix.return_value = 0

        # Use special handling for URL-only
        result = main(["https://github.com/owner/repo/issues/1"])

        assert result == 0
        mock_run_bugfix.assert_called_once()

    def test_invalid_url_returns_error_code(self) -> None:
        """Invalid URL returns error code 1."""
        result = main(["design", "not-a-valid-url"])
        assert result == 1

    def test_no_arguments_returns_error(self) -> None:
        """No arguments returns error code."""
        result = main([])
        assert result == 1


class TestInputOption:
    """Tests for --input option (design workflow)."""

    @patch("src.bugfix_agent.main.run_design_workflow")
    def test_input_option_passed_to_workflow(self, mock_run: MagicMock) -> None:
        """--input option is passed to design workflow."""
        mock_run.return_value = 0

        main(["design", "https://github.com/owner/repo/issues/1", "--input", "req.md"])

        call_args = mock_run.call_args[0][0]
        assert call_args.input == "req.md"


class TestWorkdirOption:
    """Tests for --workdir option."""

    @patch("src.bugfix_agent.main.run_design_workflow")
    def test_workdir_option_passed_to_design_workflow(self, mock_run: MagicMock) -> None:
        """--workdir option is passed to design workflow."""
        mock_run.return_value = 0

        main(["design", "https://github.com/owner/repo/issues/1", "--workdir", "/tmp/work"])

        call_args = mock_run.call_args[0][0]
        assert call_args.workdir == "/tmp/work"

    @patch("src.bugfix_agent.main.run_bugfix_workflow")
    def test_workdir_option_passed_to_bugfix_workflow(self, mock_run: MagicMock) -> None:
        """--workdir option is passed to bugfix workflow."""
        mock_run.return_value = 0

        main(["bugfix", "https://github.com/owner/repo/issues/1", "--workdir", "/tmp/work"])

        call_args = mock_run.call_args[0][0]
        assert call_args.workdir == "/tmp/work"


class TestDryRunOption:
    """Tests for --dry-run option."""

    @patch("src.bugfix_agent.main.run_design_workflow")
    def test_dry_run_option_passed_to_design_workflow(self, mock_run: MagicMock) -> None:
        """--dry-run option is passed to design workflow."""
        mock_run.return_value = 0

        main(["design", "https://github.com/owner/repo/issues/1", "--dry-run"])

        call_args = mock_run.call_args[0][0]
        assert call_args.dry_run is True

    @patch("src.bugfix_agent.main.run_bugfix_workflow")
    def test_dry_run_option_passed_to_bugfix_workflow(self, mock_run: MagicMock) -> None:
        """--dry-run option is passed to bugfix workflow."""
        mock_run.return_value = 0

        main(["bugfix", "https://github.com/owner/repo/issues/1", "--dry-run"])

        call_args = mock_run.call_args[0][0]
        assert call_args.dry_run is True
