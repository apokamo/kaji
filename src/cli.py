"""Command-line interface for dev-agent-orchestra."""

import argparse
import sys


def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="dao",
        description="AI-driven software development workflow orchestrator",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="workflow", help="Workflow to execute")

    # Design workflow
    design_parser = subparsers.add_parser("design", help="Design workflow")
    design_parser.add_argument("--input", "-i", required=True, help="Input requirements file")
    design_parser.add_argument("--output", "-o", help="Output design file")

    # Implement workflow
    impl_parser = subparsers.add_parser("implement", help="Implementation workflow")
    impl_parser.add_argument("--input", "-i", required=True, help="Input design file")
    impl_parser.add_argument("--workdir", "-w", default=".", help="Working directory")

    # Bugfix workflow
    bugfix_parser = subparsers.add_parser("bugfix", help="Bugfix workflow")
    bugfix_parser.add_argument("--issue", "-i", required=True, help="GitHub issue URL")

    # List workflows
    subparsers.add_parser("list", help="List available workflows")

    args = parser.parse_args()

    if args.workflow is None:
        parser.print_help()
        return 1

    if args.workflow == "list":
        print("Available workflows:")
        print("  design    - Design workflow (DESIGN <-> DESIGN_REVIEW loop)")
        print("  implement - Implementation workflow (IMPLEMENT <-> IMPLEMENT_REVIEW loop)")
        print("  bugfix    - Full bugfix workflow (9 states)")
        return 0

    # TODO: Implement workflow execution
    print(f"Workflow '{args.workflow}' is not yet implemented.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
