"""``kaji watch`` subcommand (Issue #543 D3).

``start`` / ``status`` / ``once`` / ``record`` / ``events`` の 5 つ。前提の違反は exit 2、
予期しないエラーは exit 3。``stop`` は作らない。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import ConfigLoadError, ConfigNotFoundError, WatchPreconditionError
from ..watch import service
from ..watch.preflight import check_watch_preconditions
from ..watch.store import WatchStore
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR


def cmd_watch(args: argparse.Namespace) -> int:
    """Execute the ``watch`` subcommand.

    Returns:
        0 = 正常、2 = 引数の誤り・config / ``[watch]`` / ラベルがない・Herdr の外での書き込み、
        3 = 予期しないエラー。
    """
    action: str = args.watch_command
    dry_run = bool(getattr(args, "dry_run", False))
    require_herdr = action == "start" or (action == "once" and not dry_run)
    # 読み取りの入口（status・once --dry-run）は Herdr の中なら pane を観測する（外でも動く）
    probe_herdr = action == "status" or (action == "once" and dry_run)
    try:
        config = KajiConfig.discover(start_dir=Path(args.workdir).resolve())
        context = check_watch_preconditions(
            config, require_herdr=require_herdr, probe_herdr=probe_herdr
        )
    except (ConfigNotFoundError, ConfigLoadError, WatchPreconditionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as exc:  # noqa: BLE001 — 予期しないエラーは exit 3 に写す契約（D3）
        print(f"Error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    try:
        herdr = service.default_herdr(context)
        deps = service.make_deps(context, herdr)
        if action == "start":
            assert herdr is not None  # require_herdr=True で検証済み
            print(service.start(context, herdr))
        elif action == "once":
            plan = service.once(deps, dry_run=dry_run, pane_id=context.herdr_pane_id)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        elif action == "status":
            document = service.status(deps)
            if args.json:
                print(json.dumps(document, ensure_ascii=False, indent=2))
            else:
                print(_render_status(document))
        elif action == "events":
            document = service.events(WatchStore(resolve_artifacts_dir(config)))
            if args.json:
                print(json.dumps(document, ensure_ascii=False, indent=2))
            else:
                for event in document["events"]:
                    print(f"{event['key']}  {event['launch_status']}  {event['event_file']}")
                for record in document["records"]:
                    print(f"{record['event_key']}  {record['outcome']}  {record['recorded_at']}")
        else:
            service.record(
                deps,
                Path(args.event),
                args.outcome,
                comment_url=args.comment_url,
                summary=args.summary,
            )
            print(f"recorded {args.outcome}")
    except WatchPreconditionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as exc:  # noqa: BLE001 — 予期しないエラーは exit 3 に写す契約（D3）
        print(f"Error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


def _render_status(document: dict[str, object]) -> str:
    """人向けの表（形式は契約にしない）。"""
    watcher = document["watcher"]
    lines = [f"watcher: {watcher}  notify: {document['notify']}"]
    issues = document["issues"]
    assert isinstance(issues, list)
    for issue in issues:
        lines.append(
            f"#{issue['issue']:<6} {issue['github_state']:<6} "
            f"current={issue['current_label']} target={issue['target_label']} "
            f"hold={issue['hold_reason']} run={issue['run_id']} triage={issue['triage_tab']}"
        )
    return "\n".join(lines)
