"""Pure functions for post-merge skills.

Issue: #164
post-merge 系スキル（wait-merge / verify-main-green / post-merge-review /
post-merge-close）の判定ロジックを純粋関数として切り出す。
副作用（gh / git / codex 呼び出し）は SKILL.md の Bash 手順側で行い、
本モジュールは出力パースと判定のみを担当する。
"""

from __future__ import annotations

import json
import re
from typing import Literal

PRState = Literal["MERGED", "OPEN", "CLOSED_NOT_MERGED"]
CIStatus = Literal["GREEN", "IN_PROGRESS", "FAILED", "NOT_FOUND"]
ReviewVerdict = Literal["PASS", "RETRY", "ABORT"]


def parse_pr_state(pr_view_json: str) -> PRState:
    """`gh pr view --json state,mergedAt,mergeCommit` の出力から PR 状態を判定する。

    Args:
        pr_view_json: gh pr view の JSON 出力文字列

    Returns:
        - "MERGED": state=MERGED
        - "OPEN": state=OPEN
        - "CLOSED_NOT_MERGED": state=CLOSED かつ mergedAt が null

    Raises:
        ValueError: JSON パース失敗または `state` フィールド欠落
    """
    try:
        data = json.loads(pr_view_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict) or "state" not in data:
        raise ValueError("missing required field 'state'")
    state = data["state"]
    if state == "MERGED":
        return "MERGED"
    if state == "OPEN":
        return "OPEN"
    if state == "CLOSED":
        if data.get("mergedAt") is None:
            return "CLOSED_NOT_MERGED"
        # state=CLOSED で mergedAt がある異常ケースは MERGED 扱い
        return "MERGED"
    raise ValueError(f"unknown PR state: {state}")


def judge_main_ci(run_list_json: str) -> CIStatus:
    """`gh run list --json status,conclusion,...` の出力から CI 状態を判定する。

    複数 run が混在する場合の優先順位:
    1. いずれかが failure / cancelled / timed_out → FAILED（早期 ABORT）
    2. いずれかが in_progress / queued → IN_PROGRESS
    3. 全て success → GREEN
    4. 0 件 → NOT_FOUND
    """
    try:
        runs = json.loads(run_list_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(runs, list):
        raise ValueError("run list must be a JSON array")
    if not runs:
        return "NOT_FOUND"

    has_in_progress = False
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("each run entry must be a mapping")
        conclusion = run.get("conclusion")
        status = run.get("status")
        if conclusion in ("failure", "cancelled", "timed_out", "startup_failure"):
            return "FAILED"
        if status in ("in_progress", "queued", "waiting", "pending"):
            has_in_progress = True

    if has_in_progress:
        return "IN_PROGRESS"
    # 全て completed && conclusion in (success, skipped, neutral)
    if all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs):
        return "GREEN"
    return "FAILED"


def select_review_range(merge_commit: str, parent_count: int) -> tuple[str, str]:
    """post-merge-review の対象コミット範囲 `(base, head)` を確定する。

    Args:
        merge_commit: merge commit の SHA
        parent_count: merge commit の親数（通常 merge は 2、squash/rebase は 1）

    Returns:
        (base, head): `git log <base>..<head>` で使えるペア
    """
    if not merge_commit:
        raise ValueError("merge_commit must not be empty")
    if parent_count >= 2:
        return (f"{merge_commit}^1", merge_commit)
    if parent_count == 1:
        return (f"{merge_commit}~1", merge_commit)
    raise ValueError(f"invalid parent_count: {parent_count}")


_VERDICT_BLOCK_RE = re.compile(
    r"---VERDICT---(.*?)---END_VERDICT---",
    re.DOTALL,
)
_STATUS_LINE_RE = re.compile(r"^\s*status\s*:\s*(\w+)", re.MULTILINE)


def parse_codex_review_output(output: str) -> ReviewVerdict:
    """codex のレビュー出力から verdict status を抽出する。

    Returns:
        - "PASS" / "RETRY": verdict ブロックから抽出した status
        - "ABORT": verdict ブロック欠落 or status 不明 or status が PASS/RETRY 以外
    """
    match = _VERDICT_BLOCK_RE.search(output)
    if not match:
        return "ABORT"
    status_match = _STATUS_LINE_RE.search(match.group(1))
    if not status_match:
        return "ABORT"
    status = status_match.group(1).upper()
    if status == "PASS":
        return "PASS"
    if status == "RETRY":
        return "RETRY"
    return "ABORT"
