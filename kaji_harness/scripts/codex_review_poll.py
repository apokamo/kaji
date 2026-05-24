"""Codex auto-review polling helper for the `review-poll` skill.

Polls the GitHub Reactions / Reviews APIs for `chatgpt-codex-connector[bot]`
signals and emits a verdict consumed by the workflow runner.

The skill bash wrapper invokes this module via
`python -m kaji_harness.scripts.codex_review_poll`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal

BOT_ID = 199175422
BOT_LOGIN_PREFIX = "chatgpt-codex-connector"
CODEX_REVIEW_BODY_MARKER = "### 💡 Codex Review"

POLL_INTERVAL_SEC = 10
NO_REACTION_TIMEOUT_SEC = 60
IN_PROGRESS_TIMEOUT_SEC = 1800
EYES_GRACE_SEC = 10
API_FAILURE_LIMIT = 3

State = Literal[
    "init",
    "in_progress",
    "done_pass",
    "done_retry",
    "done_fallback",
    "done_abort",
]


@dataclass(frozen=True)
class PollResult:
    state: State
    reason: str


def _is_bot(user: dict[str, Any], bot_id: int) -> bool:
    """Match by id (primary). login is checked only as a secondary signal."""
    return isinstance(user, dict) and user.get("id") == bot_id


def classify(
    reactions_json: list[dict[str, Any]],
    reviews_json: list[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    bot_id: int = BOT_ID,
    prev_state: Literal["init", "in_progress"] = "init",
) -> PollResult:
    """Single-poll state classifier.

    Order of evaluation:
      1. bot COMMENTED review on current head (`commit_id == head_sha`) -> done_retry
      2. bot `+1` reaction with `created_at >= head_committed_at` -> done_pass
         (stale `+1` whose `created_at < head_committed_at` is ignored)
      3. bot `eyes` reaction -> in_progress
      4. otherwise -> prev_state unchanged

    `head_committed_at` is the ISO8601 UTC committedDate of the current PR
    head commit (lexicographic comparison is sound for Z-suffixed strings).
    """
    for review in reviews_json:
        if not _is_bot(review.get("user") or {}, bot_id):
            continue
        if review.get("state") != "COMMENTED":
            continue
        if review.get("commit_id") != head_sha:
            continue
        body = review.get("body") or ""
        if body.lstrip().startswith(CODEX_REVIEW_BODY_MARKER):
            return PollResult("done_retry", f"bot review on head {head_sha[:7]}")

    for reaction in reactions_json:
        if not _is_bot(reaction.get("user") or {}, bot_id):
            continue
        if reaction.get("content") != "+1":
            continue
        created_at = reaction.get("created_at") or ""
        if created_at >= head_committed_at:
            return PollResult(
                "done_pass",
                f"bot +1 reaction (fresh, {created_at} >= {head_committed_at})",
            )

    for reaction in reactions_json:
        if not _is_bot(reaction.get("user") or {}, bot_id):
            continue
        if reaction.get("content") == "eyes":
            return PollResult("in_progress", "bot eyes reaction")

    return PollResult(prev_state, "no terminal signal")


def _gh_api(path: str) -> list[dict[str, Any]]:
    """Invoke `gh api --paginate <path>` and return parsed JSON list.

    `--paginate` is required so polling sees the bot's latest review/reaction
    even when the PR has many earlier entries (default page size = 30, reviews
    are returned in chronological order). gh concatenates page arrays into
    one JSON array on stdout.

    Raises subprocess.CalledProcessError on non-zero exit, propagated to the
    caller so it can count consecutive failures.
    """
    proc = subprocess.run(
        ["gh", "api", "--paginate", path],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, list):
        raise ValueError(f"expected list from gh api {path}, got {type(parsed).__name__}")
    return parsed


def run_polling(
    pr_number: int,
    owner: str,
    repo: str,
    head_sha: str,
    head_committed_at: str,
    *,
    poll_interval_sec: int = POLL_INTERVAL_SEC,
    no_reaction_timeout_sec: int = NO_REACTION_TIMEOUT_SEC,
    in_progress_timeout_sec: int = IN_PROGRESS_TIMEOUT_SEC,
    eyes_grace_sec: int = EYES_GRACE_SEC,
    api_failure_limit: int = API_FAILURE_LIMIT,
    bot_id: int = BOT_ID,
    now: object = time.monotonic,
    sleep: object = time.sleep,
) -> PollResult:
    """Drive the state machine until a terminal state is reached.

    `now` and `sleep` are injectable for medium tests. `head_committed_at`
    is fetched once by the caller (skill bash) and held constant across polls.
    """
    reactions_path = f"repos/{owner}/{repo}/issues/{pr_number}/reactions"
    reviews_path = f"repos/{owner}/{repo}/pulls/{pr_number}/reviews"

    state: Literal["init", "in_progress"] = "init"
    start = now()  # type: ignore[operator]
    in_progress_start: float | None = None
    eyes_lost_at: float | None = None
    consecutive_failures = 0

    while True:
        try:
            reactions = _gh_api(reactions_path)
            reviews = _gh_api(reviews_path)
            consecutive_failures = 0
        except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
            consecutive_failures += 1
            if consecutive_failures >= api_failure_limit:
                return PollResult(
                    "done_abort",
                    f"gh api failed {consecutive_failures} times in a row: {exc}",
                )
            sleep(poll_interval_sec)  # type: ignore[operator]
            continue

        result = classify(
            reactions,
            reviews,
            head_sha,
            head_committed_at,
            bot_id=bot_id,
            prev_state=state,
        )

        if result.state in ("done_pass", "done_retry"):
            return result

        elapsed = now() - start  # type: ignore[operator]

        if result.state == "in_progress":
            if state == "init":
                in_progress_start = now()  # type: ignore[operator]
            state = "in_progress"
            eyes_lost_at = None
            if (
                in_progress_start is not None
                and (now() - in_progress_start) > in_progress_timeout_sec  # type: ignore[operator]
            ):
                return PollResult(
                    "done_abort",
                    f"IN_PROGRESS_TIMEOUT_SEC ({in_progress_timeout_sec}s) exceeded",
                )
        else:
            if state == "init":
                if elapsed > no_reaction_timeout_sec:
                    return PollResult(
                        "done_fallback",
                        f"NO_REACTION_TIMEOUT_SEC ({no_reaction_timeout_sec}s) exceeded",
                    )
            else:
                if eyes_lost_at is None:
                    eyes_lost_at = now()  # type: ignore[operator]
                    sleep(eyes_grace_sec)  # type: ignore[operator]
                    continue
                if (
                    in_progress_start is not None
                    and (now() - in_progress_start) > in_progress_timeout_sec  # type: ignore[operator]
                ):
                    return PollResult(
                        "done_abort",
                        f"IN_PROGRESS_TIMEOUT_SEC ({in_progress_timeout_sec}s) exceeded",
                    )

        sleep(poll_interval_sec)  # type: ignore[operator]


_VERDICT_MAP: dict[State, tuple[str, str]] = {
    "done_pass": (
        "PASS",
        "bot +1 reaction (fresh, created_at >= head_committed_at) を検出",
    ),
    "done_retry": (
        "RETRY",
        "codex auto-review が現在 head に対し COMMENTED review を投稿",
    ),
    "done_fallback": (
        "BACK_FALLBACK",
        "NO_REACTION_TIMEOUT_SEC 経過しても codex auto-review シグナル無し",
    ),
    "done_abort": ("ABORT", "codex auto-review polling failed"),
}


def emit_verdict(result: PollResult, suggestion: str) -> str:
    status, default_reason = _VERDICT_MAP.get(result.state, ("ABORT", "unexpected state"))
    return (
        "---VERDICT---\n"
        f"status: {status}\n"
        f"reason: |\n  {default_reason}\n"
        f"evidence: |\n  {result.reason}\n"
        f"suggestion: |\n  {suggestion}\n"
        "---END_VERDICT---\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_review_poll")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--head-committed-at",
        required=True,
        help="ISO8601 UTC committedDate of the current PR head commit",
    )
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_SEC)
    parser.add_argument("--no-reaction-timeout", type=int, default=NO_REACTION_TIMEOUT_SEC)
    parser.add_argument("--in-progress-timeout", type=int, default=IN_PROGRESS_TIMEOUT_SEC)
    parser.add_argument("--eyes-grace", type=int, default=EYES_GRACE_SEC)
    args = parser.parse_args(argv)

    result = run_polling(
        pr_number=args.pr,
        owner=args.owner,
        repo=args.repo,
        head_sha=args.head_sha,
        head_committed_at=args.head_committed_at,
        poll_interval_sec=args.poll_interval,
        no_reaction_timeout_sec=args.no_reaction_timeout,
        in_progress_timeout_sec=args.in_progress_timeout,
        eyes_grace_sec=args.eyes_grace,
    )

    suggestion = {
        "done_pass": "PR を close (or merge) するステップへ進む。",
        "done_retry": "/pr-fix を実行して codex auto-review 指摘に対応する。",
        "done_fallback": "fallback の review skill (codex agent /review) を実行する。",
        "done_abort": "gh api / PR 状態を手動確認してから再実行する。",
    }.get(result.state, "skill 出力を確認する。")

    sys.stdout.write(emit_verdict(result, suggestion))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
