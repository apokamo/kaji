"""Deterministic command-line generator for series YAML files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from kaji_harness.series import SeriesConfig, generate_series_yaml


def _member(value: str) -> dict[str, object]:
    """Parse ``ISSUE=WORKFLOW`` into one schema input mapping."""
    issue_text, separator, workflow = value.partition("=")
    if not separator or not issue_text.isascii() or not issue_text.isdecimal() or not workflow:
        raise argparse.ArgumentTypeError("member must use ISSUE=WORKFLOW with a positive integer")
    return {"issue": int(issue_text), "workflow": workflow}


def create_parser() -> argparse.ArgumentParser:
    """Build the standalone generator parser."""
    parser = argparse.ArgumentParser(description="Generate a validated series YAML file")
    parser.add_argument("--id", required=True, dest="series_id")
    parser.add_argument("--parent", type=int, default=None)
    parser.add_argument("--member", type=_member, action="append", required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--update", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate normalized YAML and return a process-style exit code."""
    args = create_parser().parse_args(argv)
    try:
        config = SeriesConfig.model_validate(
            {
                "id": args.series_id,
                "parent_issue": args.parent,
                "strategy": "sequential",
                "members": args.member,
                "on_failure": "stop",
            }
        )
        output = args.output or Path(".kaji/series") / f"{config.id}.yaml"
        generate_series_yaml(config, output, update=args.update)
    except (ValidationError, FileExistsError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
