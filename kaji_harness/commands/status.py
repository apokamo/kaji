"""``kaji status`` subcommand: read-only run / series state (Issue #540).

ローカルの artifact だけを読み、ファイル・GitHub・Herdr へ書き込まない。provider を
構築せず、git は artifact の場所の解決（``git worktree list --porcelain``）だけに使う。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import ConfigLoadError, ConfigNotFoundError
from ..providers import normalize_id
from ..runstate.facts import collect_issue_facts
from ..runstate.schema import issue_to_json, series_to_json, status_document
from ..series import is_valid_series_id
from .exit_codes import EXIT_CONFIG_NOT_FOUND, EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR


def cmd_status(args: argparse.Namespace) -> int:
    """Execute the ``status`` subcommand.

    Returns:
        0 = 出力完了（run の失敗・artifact の破損と欠落を含む）。2 = 引数の誤り・config なし。
        3 = 予期しないエラー。
    """
    issues: list[str] = list(args.issues or [])
    series_id: str | None = args.series
    if bool(issues) == (series_id is not None):
        print("Error: specify either <issue>... or --series <series_id>", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        issue_ids = [normalize_id(raw) for raw in issues]
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if series_id is not None and not is_valid_series_id(series_id):
        print(
            f"Error: invalid series id {series_id!r}: expected ^[a-z0-9][a-z0-9-]{{0,63}}$",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT

    start_dir = Path(args.workdir).resolve()
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_NOT_FOUND

    try:
        artifacts_dir = resolve_artifacts_dir(config)
        series: dict[str, Any] | None = None
        if series_id is not None:
            series, member_issues = series_to_json(artifacts_dir, series_id)
            issue_ids = [str(issue) for issue in member_issues]
        documents = [
            issue_to_json(collect_issue_facts(artifacts_dir, issue_id)) for issue_id in issue_ids
        ]
        document = status_document(documents, series=series)
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            print(render_status_table(document))
    except Exception as exc:  # noqa: BLE001 — 予期しないエラーは exit 3 に写す契約（D3）
        print(f"Error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


def _cell(value: Any) -> str:
    return "-" if value is None else str(value)


def render_status_table(document: dict[str, Any]) -> str:
    """人向けの表を描画する（形式は契約にしない）。"""
    lines: list[str] = []
    series = document.get("series")
    if isinstance(series, dict):
        lines.append(
            f"series {series['id']}: status={_cell(series['status'])} "
            f"stop_reason={_cell(series['stop_reason'])}"
        )
        for member in series["members"]:
            lines.append(
                f"  member {_cell(member['issue'])}: {_cell(member['status'])} "
                f"gate={_cell(member['gate'])} run_id={_cell(member['run_id'])}"
            )
        for error in series["read_errors"]:
            lines.append(f"  read error: {error['path']}: {error['error']}")
    header = (
        f"{'ISSUE':<8} {'RUN':<16} {'TERMINATION':<11} {'END':<8} {'ALIVE':<6} "
        f"{'STEP':<20} {'FAILED':<16} CAUSE"
    )
    lines.append(header)
    for issue in document["issues"]:
        run = issue["latest_run"]
        if run is None:
            lines.append(
                f"{issue['issue']:<8} {'-':<16} (no runs) alive={_cell(issue['any_run_alive'])}"
            )
        else:
            step = run["last_step"]["id"] if run["last_step"] else None
            cause = run["failure"]["cause"] if run["failure"] else None
            lines.append(
                f"{issue['issue']:<8} {run['run_id']:<16} {_cell(run['termination']):<11} "
                f"{_cell(run['end_status']):<8} {_cell(run['process']['alive']):<6} "
                f"{_cell(step):<20} {_cell(run['failed_step']):<16} {_cell(cause)}"
            )
            if run["worktree_dir"] or run["branch_name"]:
                lines.append(
                    f"{'':<8} worktree={_cell(run['worktree_dir'])} "
                    f"branch={_cell(run['branch_name'])}"
                )
        for error in issue["read_errors"]:
            lines.append(f"{'':<8} read error: {error['path']}: {error['error']}")
    return "\n".join(lines)
