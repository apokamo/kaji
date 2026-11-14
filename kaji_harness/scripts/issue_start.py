"""Entry module for the ``issue-start`` skill (exec_script dispatch / ``kaji issue start``).

workflow の ``start`` step は harness が ``python -m kaji_harness.scripts.issue_start`` として
起動し（skill frontmatter ``exec_script``）、手動実行は ``kaji issue start <issue_id>`` が同じ
``main`` に合流する（Issue #569）。処理本体は ``kaji_harness.issue_start``。

verdict は ``KAJI_VERDICT_PATH``（workflow のみ）へ pure YAML を原子的に書き、同じ内容を
stdout の ``---VERDICT---`` block に出す。想定内の失敗は ABORT verdict + ``return 0``
（exec_script の非ゼロ終了は ERROR となり ``on: ABORT`` 遷移が成立しないため）。想定外の
例外は捕捉せず traceback + 非ゼロ終了とする（exec_script の fail-loud 契約）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kaji_harness.config import KajiConfig
from kaji_harness.errors import ConfigLoadError, ConfigNotFoundError
from kaji_harness.fsio import atomic_write
from kaji_harness.issue_start import (
    StartOutcome,
    build_abort_verdict,
    format_verdict_block,
    run_issue_start,
)
from kaji_harness.models import Verdict
from kaji_harness.providers import IssueProvider, get_provider, normalize_id
from kaji_harness.verdict import dump_verdict_yaml


def _parse_args(argv: list[str] | None) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        prog="kaji issue start",
        description=(
            "Create (or verify and reuse) the Issue worktree from the fetched remote default "
            "branch, then record the Issue NOTE and the start report."
        ),
    )
    parser.add_argument(
        "issue_id",
        nargs="?",
        default=None,
        help="Issue ID (e.g. 569 / gh:569). Defaults to KAJI_ISSUE_ID.",
    )
    return parser, parser.parse_args(argv)


def _emit_verdict(verdict: Verdict, verdict_path: Path | None) -> None:
    """artifact（workflow のみ）を書いてから、互換の stdout block を出す。"""
    if verdict_path is not None:
        atomic_write(verdict_path, dump_verdict_yaml(verdict))
    sys.stdout.write(format_verdict_block(verdict))


def _discover_provider(cwd: Path, issue_id: str) -> IssueProvider | StartOutcome:
    """main checkout の config（overlay を含む）から provider を構築する。"""
    try:
        return get_provider(KajiConfig.discover(cwd))
    except (ConfigNotFoundError, ConfigLoadError, ValueError) as exc:
        verdict = build_abort_verdict(
            issue_id,
            reason="kaji config から provider を構築できない",
            evidence=f"{type(exc).__name__}: {exc}",
            completed=(),
            suggestion=".kaji/config.toml（と config.local.toml）の [provider] 設定を確認する。",
        )
        return StartOutcome(verdict=verdict)


def main(argv: list[str] | None = None, *, provider: IssueProvider | None = None) -> int:
    """引数または ``KAJI_ISSUE_ID`` の Issue について start を 1 プロセスで実行する。

    Args:
        argv: ``[issue_id]``。exec_script 経路では空で、``KAJI_ISSUE_ID`` を使う。
            2 個以上の位置引数（旧第 2 引数 prefix）は usage error。
        provider: ``kaji issue start`` が dispatch 用 config で構築した provider。
            ``None`` なら cwd から config を探索して構築する。

    Returns:
        verdict を出力できた場合は PASS / ABORT とも 0。

    Raises:
        SystemExit: usage error（exit 2）。provider / git は呼ばない。
    """
    parser, args = _parse_args(argv)
    raw_issue_id = args.issue_id or os.environ.get("KAJI_ISSUE_ID", "")
    if not raw_issue_id:
        parser.error("issue_id is required (positional argument or KAJI_ISSUE_ID)")
    try:
        issue_id = normalize_id(raw_issue_id)
    except ValueError as exc:
        parser.error(str(exc))

    verdict_path_raw = os.environ.get("KAJI_VERDICT_PATH", "")
    verdict_path = Path(verdict_path_raw) if verdict_path_raw else None
    cwd = Path.cwd()
    resolved = provider if provider is not None else _discover_provider(cwd, issue_id)
    if isinstance(resolved, StartOutcome):
        outcome = resolved
    else:
        outcome = run_issue_start(issue_id, resolved, cwd=cwd, env=os.environ)
    _emit_verdict(outcome.verdict, verdict_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
