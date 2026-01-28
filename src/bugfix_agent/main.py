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
    """Run the bugfix workflow (9-state) via external orchestrator.

    Calls the v5 orchestrator from external/bugfix-v5/ as a subprocess
    to maintain backward compatibility while keeping the orchestrator
    code as a reference snapshot.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    import subprocess
    from pathlib import Path

    # Locate the orchestrator script
    # src/bugfix_agent/main.py → project root → external/bugfix-v5/
    project_root = Path(__file__).resolve().parent.parent.parent
    orchestrator_path = project_root / "external" / "bugfix-v5" / "bugfix_agent_orchestrator.py"

    if not orchestrator_path.exists():
        print(
            f"Error: v5 orchestrator not found at {orchestrator_path}\n"
            f"Please ensure external/bugfix-v5/ is properly set up.",
            file=sys.stderr,
        )
        return 1

    # Build command arguments
    cmd = [sys.executable, str(orchestrator_path), "--issue", args.issue_url]

    # Pass through workdir if specified
    # Note: v5 orchestrator doesn't have --workdir, but we can use cwd
    cwd = None
    if getattr(args, "workdir", None):
        cwd = args.workdir

    # Note: v5 orchestrator doesn't support --dry-run or --verbose directly
    # These options are handled at the CLI level for design workflow only

    print(f"Running bugfix workflow for: {args.issue_url}")
    print(f"Orchestrator: {orchestrator_path}")

    try:
        # Run the orchestrator as subprocess
        # Use PYTHONPATH to include the v5 directory for its internal imports
        env = dict(__import__("os").environ)
        v5_dir = orchestrator_path.parent
        existing_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{v5_dir}:{existing_path}" if existing_path else str(v5_dir)

        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            check=False,  # Don't raise on non-zero exit
        )
        return result.returncode
    except FileNotFoundError as e:
        print(f"Error: Failed to run orchestrator: {e}", file=sys.stderr)
        return 1
    except subprocess.SubprocessError as e:
        print(f"Error: Orchestrator subprocess failed: {e}", file=sys.stderr)
        return 1


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
