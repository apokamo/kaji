"""Entry module for the `review-poll` skill (exec_script dispatch).

env から argv 変換と PR / head 解決を担当し、polling 本体である
``codex_review_poll.main()`` に委譲する薄い shim。``codex_review_poll`` の
polling ロジック / argparse 契約には一切手を入れない（Issue #204 スコープ境界）。

ABORT verdict は **stdout に emit して return 0**。``gh`` CLI 不在のような
catastrophic 失敗は raise させ harness 側で ``ScriptExecutionError`` として
fail-loud に扱わせる（exit code と verdict の優先順位は設計書参照）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

from . import codex_review_poll

_REMOTE_URL_RE = re.compile(
    # git@host:owner/repo(.git)?  or  https://host/owner/repo(.git)?
    r"^(?:git@[^:]+:|https?://[^/]+/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def _abort(reason: str, evidence: str) -> int:
    sys.stdout.write(
        "---VERDICT---\n"
        "status: ABORT\n"
        f"reason: |\n  {reason}\n"
        f"evidence: |\n  {evidence}\n"
        "suggestion: |\n  review-poll skill の env / PR 状態を手動確認してから再実行する。\n"
        "---END_VERDICT---\n"
    )
    return 0


def parse_remote_url(url: str) -> tuple[str, str]:
    """git remote URL から (owner, repo) を抽出。失敗時は ``ValueError``。"""
    m = _REMOTE_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"unsupported remote url format: {url!r}")
    return m.group("owner"), m.group("repo")


def _gh_json(args: list[str], cwd: str | None = None) -> Any:
    """``gh`` / ``kaji`` CLI を実行し JSON を返す。失敗は raise（catastrophic）。

    `--jq` で object/array を抽出する用途専用。``--jq`` のスカラー（string）抽出は
    クォートなし生文字列を出力するため json.loads に渡せない。スカラーは
    ``_gh_raw`` を使うこと。
    """
    result = subprocess.run(args, check=True, capture_output=True, text=True, cwd=cwd)
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _gh_raw(args: list[str], cwd: str | None = None) -> str:
    """CLI を実行し ``--jq`` スカラーの生 stdout を返す（json.loads しない）。

    ``gh`` / ``kaji pr view --jq '.headRefOid'`` 等は string scalar を
    クォートなし生文字列で出力する。失敗は raise（catastrophic）。
    """
    result = subprocess.run(args, check=True, capture_output=True, text=True, cwd=cwd)
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    """env から PR / head 情報を解決して codex_review_poll.main へ委譲。

    Args:
        argv: 未使用。harness は env のみ渡す。テスト互換のため受け取る。
    """
    del argv  # 互換用、env-driven なので未使用

    provider_type = os.environ.get("KAJI_PROVIDER_TYPE", "")
    if provider_type != "github":
        return _abort(
            "review-poll requires provider.type='github' (codex auto-review is GitHub-only).",
            f"provider_type was {provider_type!r}",
        )

    issue_id = os.environ.get("KAJI_ISSUE_ID", "")
    if not issue_id:
        return _abort(
            "KAJI_ISSUE_ID env is required to resolve PR.",
            "KAJI_ISSUE_ID was empty",
        )

    worktree_dir = os.environ.get("KAJI_WORKTREE_DIR", "")
    git_remote = os.environ.get("KAJI_GIT_REMOTE", "origin")

    # owner / repo を git remote から解決
    try:
        remote_url = subprocess.run(
            ["git", "remote", "get-url", git_remote],
            check=True,
            capture_output=True,
            text=True,
            cwd=worktree_dir or None,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        return _abort(
            "remote url parse failure: git remote get-url failed.",
            f"git remote get-url {git_remote!r} in {worktree_dir!r}: "
            f"returncode={exc.returncode} stderr={exc.stderr.strip()!r}",
        )

    try:
        owner, repo = parse_remote_url(remote_url)
    except ValueError as exc:
        return _abort(
            "remote url parse failure: unsupported url format.",
            str(exc),
        )

    # PR id を解決（harness が KAJI_PR_ID を渡していれば優先）
    pr_id_env = os.environ.get("KAJI_PR_ID", "").strip()
    pr_id: int | None = None
    if pr_id_env:
        try:
            pr_id = int(pr_id_env)
        except ValueError:
            return _abort(
                "PR not resolved: KAJI_PR_ID is not an integer.",
                f"KAJI_PR_ID={pr_id_env!r}",
            )

    if pr_id is None:
        pr_list = _gh_json(
            [
                "kaji",
                "pr",
                "list",
                "--search",
                issue_id,
                "--json",
                "number,headRefName,headRefOid",
                "--jq",
                ".[0]",
            ],
            cwd=worktree_dir or None,
        )
        if not pr_list or not isinstance(pr_list, dict):
            return _abort(
                "PR not resolved: kaji pr list returned no match.",
                f"search key issue_id={issue_id!r}",
            )
        pr_id_raw = pr_list.get("number")
        if pr_id_raw is None:
            return _abort(
                "PR not resolved: pr.number missing.",
                f"pr_list={pr_list!r}",
            )
        pr_id = int(pr_id_raw)
        head_sha = (pr_list.get("headRefOid") or "").strip()
    else:
        head_sha = ""

    # head_sha 補完（--jq .headRefOid は生 SHA 文字列。json.loads 不可）
    if not head_sha:
        head_sha = _gh_raw(
            [
                "kaji",
                "pr",
                "view",
                str(pr_id),
                "--json",
                "headRefOid",
                "--jq",
                ".headRefOid",
            ],
            cwd=worktree_dir or None,
        )

    if not head_sha:
        return _abort(
            "head_sha unavailable: PR headRefOid is empty.",
            f"pr_id={pr_id}",
        )

    # head committed_at を取得（--jq スカラーは生日付文字列。json.loads 不可）
    head_committed_at = _gh_raw(
        [
            "kaji",
            "pr",
            "view",
            str(pr_id),
            "--json",
            "commits",
            "--jq",
            ".commits[-1].committedDate",
        ],
        cwd=worktree_dir or None,
    )
    if not head_committed_at:
        return _abort(
            "head committed_at unavailable: pr commits[-1].committedDate missing.",
            f"pr_id={pr_id}",
        )

    poll_argv = [
        "--pr",
        str(pr_id),
        "--owner",
        owner,
        "--repo",
        repo,
        "--head-sha",
        head_sha,
        "--head-committed-at",
        head_committed_at,
    ]
    return codex_review_poll.main(poll_argv)


if __name__ == "__main__":
    raise SystemExit(main())
