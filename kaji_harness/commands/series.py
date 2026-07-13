"""CLI handlers for sequential series validation and execution."""

from __future__ import annotations

import argparse
import sys

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    SeriesAbortedError,
    SeriesInputError,
    SeriesValidationError,
)
from ..providers import actual_provider_type, get_provider
from ..providers.github import GitHubProviderError
from ..series import SeriesRunner, load_series, series_fingerprint
from .exit_codes import EXIT_ABORT, EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR


def _discover_series_config(args: argparse.Namespace) -> KajiConfig:
    """Discover repository configuration from the explicit workdir."""
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        raise SeriesInputError(f"--workdir is not a directory: {args.workdir}")
    return KajiConfig.discover(start_dir=start_dir)


def cmd_validate_series(args: argparse.Namespace) -> int:
    """Validate a series without accessing provider or creating artifacts."""
    try:
        config = _discover_series_config(args)
    except (ConfigNotFoundError, ConfigLoadError, SeriesInputError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    failed = False
    for path in args.series:
        try:
            series = load_series(path.resolve(), config)
        except SeriesValidationError as exc:
            failed = True
            print(f"✗ {path}", file=sys.stderr)
            for error in exc.errors:
                print(f"  - {error}", file=sys.stderr)
        else:
            print(f"✓ {path} ({len(series.members)} members)")
    return EXIT_ABORT if failed else EXIT_OK


def cmd_run_series(args: argparse.Namespace) -> int:
    """Print a dry-run plan or execute a validated sequential series."""
    try:
        config = _discover_series_config(args)
        provider = get_provider(config)
        if actual_provider_type(config) != "github":
            raise SeriesInputError("run-series requires provider.type='github'")
        series = load_series(args.series.resolve(), config)
        fingerprint = series_fingerprint(series)
        if args.dry_run:
            print(f"✓ {args.series} ({len(series.members)} members)")
            print(f"fingerprint: {fingerprint}")
            for index, member in enumerate(series.members, start=1):
                print(f"{index}. issue #{member.issue}: {member.workflow}")
            return EXIT_OK
        runner = SeriesRunner(
            config=series,
            repo_root=config.repo_root,
            artifacts_dir=resolve_artifacts_dir(config),
            provider=provider,
            quiet=args.quiet,
        )
        return runner.run(resume=args.resume)
    except SeriesValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except (ConfigNotFoundError, ConfigLoadError, SeriesInputError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except SeriesAbortedError as exc:
        print(f"Series stopped: {exc}", file=sys.stderr)
        return EXIT_ABORT
    except HarnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except (GitHubProviderError, OSError) as exc:
        print(f"Runtime error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
