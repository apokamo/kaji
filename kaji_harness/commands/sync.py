"""``kaji sync`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import KajiConfig
from ..errors import ConfigLoadError, ConfigNotFoundError
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR


def cmd_sync_from_github(args: argparse.Namespace) -> int:
    """``kaji sync from-github`` の dispatcher。

    将来予約 flag（``--include-closed`` / ``--state`` / ``--since``）は exit 2 で
    fail-fast する。
    """
    from ..errors import SyncError
    from ..sync import sync_from_github

    if args.include_closed:
        sys.stderr.write(
            "error: --include-closed is not implemented in this release; "
            "reopen tracking issue to add it.\n"
        )
        return EXIT_INVALID_INPUT
    if args.state is not None:
        sys.stderr.write(
            "error: --state is not implemented in this release; "
            "this command always fetches state=open.\n"
        )
        return EXIT_INVALID_INPUT
    if args.since is not None:
        sys.stderr.write(
            "error: --since is not implemented in this release; "
            "this command always performs a full sync.\n"
        )
        return EXIT_INVALID_INPUT

    try:
        config = KajiConfig.discover(start_dir=Path.cwd())
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        result = sync_from_github(
            config=config,
            repo_override=args.repo,
            quiet=args.quiet,
        )
    except SyncError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        sys.stderr.write(f"error: cache write failed: {exc}\n")
        return EXIT_RUNTIME_ERROR

    sys.stdout.write(
        f"Sync completed at {result.last_sync_at} "
        f"({result.issue_count} issues, {result.pages_fetched} pages, "
        f"{result.elapsed_seconds:.1f}s).\n"
    )
    return EXIT_OK


def cmd_sync_status(args: argparse.Namespace) -> int:
    """``kaji sync status`` の dispatcher (issue ``local-p1-8``)。"""
    import json as _json

    from ..errors import SyncError
    from ..sync import format_elapsed_human, read_sync_status

    try:
        config = KajiConfig.discover(start_dir=Path.cwd())
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        status = read_sync_status(config=config)
    except SyncError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_INVALID_INPUT

    elapsed_human: str | None = (
        format_elapsed_human(status.elapsed_seconds) if status.elapsed_seconds is not None else None
    )

    if args.json_mode:
        payload: dict[str, object] = {
            "forge": status.forge,
            "repo": status.repo,
            "last_sync_at": status.last_sync_at,
            "elapsed_seconds": (
                int(status.elapsed_seconds) if status.elapsed_seconds is not None else None
            ),
            "elapsed_human": elapsed_human,
            "issue_count": status.issue_count,
        }
        sys.stdout.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        return EXIT_OK

    forge_disp = status.forge or "(none)"
    repo_disp = status.repo or "(none)"
    last_disp = status.last_sync_at or "(never)"
    if status.elapsed_seconds is None:
        elapsed_disp = "n/a"
    else:
        elapsed_disp = f"{elapsed_human} ({int(status.elapsed_seconds)}s)"
    sys.stdout.write(f"forge        {forge_disp}\n")
    sys.stdout.write(f"repo         {repo_disp}\n")
    sys.stdout.write(f"last_sync    {last_disp}\n")
    sys.stdout.write(f"elapsed      {elapsed_disp}\n")
    cache_glob = "gh-*.json"
    sys.stdout.write(f"cached       {status.issue_count} ({cache_glob} under .kaji/cache/)\n")
    return EXIT_OK
