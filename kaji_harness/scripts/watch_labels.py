"""Deterministic ``state:*`` label operations for ``issue-close`` (Issue #543 D1・A8・M1).

``python -m kaji_harness.scripts.watch_labels mark-done|mark-follow-up <issue>``

- ``[watch]`` 未宣言（tracked の config）: 何もせず ``watch labels: skipped (no [watch])`` で exit 0
- 宣言済みで契約の 6 ラベルが不足: 不足名を stderr に出して exit 2
- ``mark-done``: ``state:done`` を付け、ほかの ``state:*`` をすべて外す（exit 0）
- ``mark-follow-up``: ``state:needs-maintainer`` を付ける（exit 0）
- GitHub の書き込み失敗: exit 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kaji_harness.config import KajiConfig
from kaji_harness.errors import ConfigLoadError, ConfigNotFoundError, WatchPreconditionError
from kaji_harness.providers import IssueProvider, normalize_id
from kaji_harness.watch.constants import CONTRACT_LABELS, STATE_DONE, STATE_NEEDS_MAINTAINER
from kaji_harness.watch.preflight import check_watch_preconditions

# ``commands.exit_codes`` と同じ値（scripts は application 層で command 層を import しない）
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_RUNTIME_ERROR = 3


def create_parser() -> argparse.ArgumentParser:
    """CLI parser を作る。"""
    parser = argparse.ArgumentParser(description="state:* label operations for issue-close")
    parser.add_argument("action", choices=("mark-done", "mark-follow-up"))
    parser.add_argument("issue")
    parser.add_argument("--workdir", default=".")
    return parser


def apply_action(provider: IssueProvider, action: str, issue_id: str) -> None:
    """ラベル操作を 1 回の ``edit_issue`` で行う。

    Args:
        provider: GitHub provider。
        action: ``mark-done`` / ``mark-follow-up``。
        issue_id: canonical Issue ID。
    """
    if action == "mark-done":
        provider.edit_issue(
            issue_id,
            add_labels=[STATE_DONE],
            remove_labels=[name for name in CONTRACT_LABELS if name != STATE_DONE],
        )
    else:
        provider.edit_issue(issue_id, add_labels=[STATE_NEEDS_MAINTAINER])


def main(argv: list[str] | None = None, *, provider: IssueProvider | None = None) -> int:
    """ラベル操作を実行し、process の exit code を返す。

    Args:
        argv: 引数（省略時は ``sys.argv``）。
        provider: 注入する provider（テスト用）。

    Returns:
        0 = 完了または skip、2 = 前提違反、3 = GitHub の書き込み失敗。
    """
    args = create_parser().parse_args(argv)
    try:
        issue_id = normalize_id(args.issue)
        config = KajiConfig.discover(start_dir=Path(args.workdir).resolve())
    except (ValueError, ConfigNotFoundError, ConfigLoadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if config.watch is None:
        print("watch labels: skipped (no [watch])")
        return EXIT_OK
    try:
        context = check_watch_preconditions(config, require_herdr=False, provider=provider)
    except WatchPreconditionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as exc:  # noqa: BLE001 — ラベル一覧の取得失敗（gh）は exit 3 に写す
        print(f"Error: failed to list labels: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    try:
        apply_action(context.provider, args.action, issue_id)
    except Exception as exc:  # noqa: BLE001 — GitHub の書き込み失敗は exit 3 の契約
        print(f"Error: label update failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    print(f"watch labels: {args.action} #{issue_id}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
