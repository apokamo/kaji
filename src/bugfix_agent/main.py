"""CLI entry point for bugfix_agent with subcommand support.

This module provides the main CLI interface with:
- `design`: Run DesignWorkflow (DESIGN -> DESIGN_REVIEW -> COMPLETE)
- `bugfix`: Run bugfix workflow (9-state)
- Backward compatibility: URL-only argument routes to bugfix

Issue #36: v5 Phase2 - DesignWorkflow + CLI Subcommands

Examples:
    # Design workflow
    python -m bugfix_agent design https://github.com/owner/repo/issues/123

    # Bugfix workflow (explicit)
    python -m bugfix_agent bugfix https://github.com/owner/repo/issues/123

    # Backward compatibility (URL-only → bugfix)
    python -m bugfix_agent https://github.com/owner/repo/issues/123
"""

from __future__ import annotations

import argparse
import sys
from argparse import Namespace

from src.core.url_utils import is_valid_issue_url
from src.workflows.design.runner import run_design_workflow


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser with subcommands.

    Returns:
        Configured ArgumentParser with design/bugfix subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="bugfix_agent",
        description="AI-driven software development workflow orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Design workflow
  %(prog)s design https://github.com/owner/repo/issues/123

  # Bugfix workflow (explicit)
  %(prog)s bugfix https://github.com/owner/repo/issues/123

  # Backward compatibility (URL-only)
  %(prog)s https://github.com/owner/repo/issues/123
        """,
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command")

    # Common options for all subcommands
    def add_common_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "issue_url",
            type=str,
            help="GitHub Issue URL (e.g., https://github.com/owner/repo/issues/123)",
        )
        subparser.add_argument(
            "--workdir",
            "-w",
            type=str,
            default=None,
            help="Working directory (default: current directory)",
        )
        subparser.add_argument(
            "--dry-run",
            action="store_true",
            help="Don't actually modify issues (for testing)",
        )
        subparser.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Enable verbose output",
        )

    # design subcommand
    design_parser = subparsers.add_parser(
        "design",
        help="Run DesignWorkflow (DESIGN -> DESIGN_REVIEW -> COMPLETE)",
    )
    add_common_options(design_parser)
    design_parser.add_argument(
        "--input",
        "-i",
        type=str,
        default=None,
        help="Requirements input file (optional)",
    )

    # bugfix subcommand
    bugfix_parser = subparsers.add_parser(
        "bugfix",
        help="Run bugfix workflow (9-state)",
    )
    add_common_options(bugfix_parser)

    return parser


def route_command(args: Namespace) -> Namespace:
    """Route command based on args, handling backward compatibility.

    If no subcommand is specified but a valid URL is provided,
    route to 'bugfix' for backward compatibility.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Updated args with command set.

    Raises:
        ValueError: If no valid command can be determined.
    """
    if args.command is not None:
        # Explicit subcommand specified
        return args

    # No subcommand - check if issue_url is a valid GitHub Issue URL
    if hasattr(args, "issue_url") and args.issue_url:
        if is_valid_issue_url(args.issue_url):
            # Backward compatibility: URL-only → bugfix
            args.command = "bugfix"
            return args
        raise ValueError(
            f"Invalid GitHub Issue URL: {args.issue_url}\n"
            f"Expected format: https://github.com/{{owner}}/{{repo}}/issues/{{number}}"
        )

    raise ValueError("No command or issue URL specified")


def run_bugfix_workflow(args: Namespace) -> int:
    """Run the bugfix workflow (9-state).

    This is a placeholder that will call the existing v5 orchestrator.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    # TODO: Integrate with existing v5 orchestrator
    print(f"Running bugfix workflow for: {args.issue_url}")
    print("Note: Full bugfix workflow integration pending")
    return 0


def _convert_args_for_design_runner(args: Namespace) -> Namespace:
    """Convert CLI args to format expected by design runner.

    The design runner expects 'issue' instead of 'issue_url'.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Converted args for design runner.
    """
    # Create new namespace with renamed field
    return Namespace(
        issue=args.issue_url,
        input=getattr(args, "input", None),
        workdir=getattr(args, "workdir", None),
        dry_run=getattr(args, "dry_run", False),
        verbose=getattr(args, "verbose", False),
    )


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point.

    Args:
        argv: Command line arguments (default: sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = create_parser()

    # Handle URL-only argument (backward compatibility)
    # If first arg looks like a URL and no subcommand, insert 'bugfix'
    if argv and not argv[0].startswith("-") and argv[0] not in ("design", "bugfix"):
        if is_valid_issue_url(argv[0]):
            # Insert 'bugfix' as the command for backward compatibility
            argv = ["bugfix"] + list(argv)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise

    # Validate command
    try:
        args = route_command(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Validate URL format
    if not is_valid_issue_url(args.issue_url):
        print(
            f"Error: Invalid GitHub Issue URL: {args.issue_url}\n"
            f"Expected format: https://github.com/{{owner}}/{{repo}}/issues/{{number}}",
            file=sys.stderr,
        )
        return 1

    # Route to appropriate workflow
    if args.command == "design":
        runner_args = _convert_args_for_design_runner(args)
        return run_design_workflow(runner_args)
    elif args.command == "bugfix":
        return run_bugfix_workflow(args)
    else:
        print(f"Error: Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
