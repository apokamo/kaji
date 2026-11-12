"""Codex auto-review polling helper for the `review-poll` step.

Polls the GitHub Reactions / Reviews / Comments APIs for `chatgpt-codex-connector[bot]`
signals and emits a verdict consumed by the workflow runner.

The primary signal is the machine-readable `<!-- codex-review-state:v1 {...} -->` in the
bot's review summary comment (full SHA / `status` / `has_any_findings`, Issue #473). A
summary without that state is not a terminal signal: the legacy markup path (review body
heading / summary table) was removed in Issue #478. PASS always requires the state's full
SHA *and* a `+1` reaction created after it; a `+1` alone carries no commit SHA and is never
an approval (Issue #429). On PASS the approving PR / full head SHA / signal IDs are saved as
`review_poll_evidence` JSON (schema v3) before the verdict is emitted, so a later close step
can verify them against the current PR head.

Since 2026-11 the bot reports `security_review` as `completed` with `has_any_findings: null`.
The meaning of that null is not documented upstream, so it is accepted only conditionally
(maintainer decision, Issue #557): on top of the regular PASS conditions, no trusted bot
Issue comment other than the summary may exist, and at finalization no unresolved review
thread with a trusted bot comment on the head may exist (GraphQL `reviewThreads`). Any other
null stays fail-closed. This relies on undocumented bot behaviour and must be revisited once
upstream defines or changes the meaning of the null.

Since #234 the workflow runner (`script_exec.py`) launches review-poll as an
exec step (`exec: [kaji, pr, review-poll]`), which dispatches to
`kaji_harness.scripts.review_poll_entry`; this module is invoked by that entry
as the polling core (not via a skill bash wrapper).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeGuard

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from kaji_harness.review_poll_evidence import (
    EVIDENCE_KIND,
    EVIDENCE_SCHEMA_VERSION_V3,
    ReviewPollEvidenceV3,
    format_timestamp,
    is_security_null_on_head,
    next_second_after,
    parse_timestamp,
    save_evidence,
)

BOT_ID = 199175422
BOT_LOGIN_PREFIX = "chatgpt-codex-connector"
SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
REVIEW_STATE_MARKER = "<!-- codex-review-state:"
REVIEW_STATE_VERSION = "v1"
_REVIEW_STATE_RE = re.compile(
    r"<!-- codex-review-state:(?P<version>\S+) (?P<payload>.*?) -->", re.DOTALL
)
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")

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


_StateSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
_StateStatus = Annotated[str, StringConstraints(min_length=1)]


class ReviewStateEntry(BaseModel):
    """`codex-review-state:v1` の 1 review 分。判定に使う key だけを検証し、未知の key は無視する。"""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    commit_sha: _StateSha | None
    has_any_findings: bool | None
    status: _StateStatus | None

    @model_validator(mode="after")
    def _validate_completed(self) -> ReviewStateEntry:
        # has_any_findings の必須性は review ごとに違うので `_ReviewStatePayload` で検査する
        if self.status == "completed" and self.commit_sha is None:
            raise ValueError("completed review state requires commit_sha")
        return self

    @property
    def completed(self) -> bool:
        return self.status == "completed"


class _ReviewStatePayload(BaseModel):
    """`codex-review-state:v1` の payload。

    code_review は `completed` なら `has_any_findings` が bool でなければならない。
    security_review の `completed` / `has_any_findings: null` は parse では拒否せず、
    `_classify_state` が条件付きで扱う（Issue #557）。
    """

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    code_review: ReviewStateEntry
    security_review: ReviewStateEntry

    @model_validator(mode="after")
    def _validate_code_findings(self) -> _ReviewStatePayload:
        if self.code_review.completed and self.code_review.has_any_findings is None:
            raise ValueError("completed code_review requires has_any_findings")
        return self


@dataclass(frozen=True)
class ReviewState:
    """trusted bot の summary comment から取り出した `codex-review-state:v1`。"""

    comment_id: int
    url: str
    updated_at: str
    code_review: ReviewStateEntry
    security_review: ReviewStateEntry


@dataclass(frozen=True)
class InvalidReviewState:
    """state marker はあるが判定に使えない（版が未知・複数・JSON 破損・検証失敗・選別不能）。"""

    reason: str


@dataclass(frozen=True)
class Approval:
    """PASS 候補の承認シグナル（`+1` reaction と、full SHA を持つ `codex-review-state`）。"""

    reaction_id: int
    reaction_created_at: str
    bot_login: str
    review_state: ReviewState


@dataclass(frozen=True)
class PollResult:
    """1 poll の判定結果。`signal` は terminal を `codex-review-state` で決めたときに `"state"`。"""

    state: State
    reason: str
    approval: Approval | None = None
    signal: Literal["state"] | None = None


def format_heartbeat(
    *,
    elapsed_sec: float,
    pr_number: int,
    head_sha: str,
    state: str,
    remaining_sec: float,
) -> str:
    """polling 進捗 heartbeat の 1 行を組み立てる純粋関数（Issue #235）。

    起動コンソール運用者が「待機中 / 停止中 / エラー」を切り分けられるよう、
    経過秒・PR 番号・head 短縮・観測中 state・timeout 残を 1 行に含める。

    verdict marker 非汚染: ``---VERDICT---`` / ``---END_VERDICT---`` および
    ``---`` 始まりの marker 類似文字列を **含めない** 素のテキストを返す
    （``verdict.py`` の抽出正規表現を壊さない）。
    """
    return (
        f"polling PR #{pr_number} head={head_sha[:7]} "
        f"state={state} elapsed={int(elapsed_sec)}s remaining={max(0, int(remaining_sec))}s"
    )


def _default_emit(line: str) -> None:
    """heartbeat を stdout へ flush 出力する既定 emitter。

    subprocess の stdout は非 tty で block-buffer されるため、各行を
    ``flush()`` で即時送出しないと ``script_exec`` の pipe で終了まで溜まる。
    ``BrokenPipeError`` / ``OSError``（reader 側が先に閉じた等）は捕捉して
    黙って return する。heartbeat は観測のみの best-effort 副作用であり、
    pipe 切断を polling 失敗へ昇格させない。
    """
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        return


def _is_bot(user: dict[str, Any], bot_id: int) -> bool:
    """Match by id (primary). login is checked only as a secondary signal."""
    return isinstance(user, dict) and user.get("id") == bot_id


def _is_positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _latest_summary_comments(
    comments: Iterable[dict[str, Any]], bot_id: int
) -> list[dict[str, Any]]:
    """summary marker で始まる trusted bot comment のうち `updated_at` が最大のものを返す。

    Returns:
        最新の comment 群。通常 1 件。該当なしは空、`updated_at` が同じものが複数あればその全件。
    """
    candidates = [
        comment
        for comment in comments
        if _is_bot(comment.get("user") or {}, bot_id)
        and _is_summary_body(comment.get("body"))
        and _is_positive_int(comment.get("id"))
        and isinstance(comment.get("html_url"), str)
        and comment["html_url"]
        and isinstance(comment.get("updated_at"), str)
        and comment["updated_at"]
    ]
    if not candidates:
        return []
    latest = max(comment["updated_at"] for comment in candidates)
    return [comment for comment in candidates if comment["updated_at"] == latest]


def _is_summary_body(body: object) -> bool:
    return isinstance(body, str) and body.lstrip().startswith(SUMMARY_MARKER)


def _count_bot_non_summary_comments(comments: Iterable[dict[str, Any]], bot_id: int) -> int:
    """trusted bot による summary 以外の Issue comment を数える（Issue #557 重要判断 (b)）。

    エラー文面に機械可読な仕様がないので文面では判定しない。`body` が文字列でないものも
    summary ではないとして数える（fail-closed）。古い summary（最新でない summary）は数えない。
    """
    return sum(
        1
        for comment in comments
        if _is_bot(comment.get("user") or {}, bot_id) and not _is_summary_body(comment.get("body"))
    )


def _parse_state_body(comment: dict[str, Any]) -> ReviewState | InvalidReviewState | None:
    body: str = comment["body"]
    count = body.count(REVIEW_STATE_MARKER)
    if count == 0:
        return None
    if count > 1:
        return InvalidReviewState(f"{count} codex-review-state markers in comment {comment['id']}")
    match = _REVIEW_STATE_RE.search(body)
    if match is None:
        return InvalidReviewState(f"malformed codex-review-state marker in comment {comment['id']}")
    version = match.group("version")
    if version != REVIEW_STATE_VERSION:
        return InvalidReviewState(f"unsupported codex-review-state version {version!r}")
    try:
        payload = _ReviewStatePayload.model_validate_json(match.group("payload"))
    except ValidationError as exc:
        return InvalidReviewState(
            f"codex-review-state in comment {comment['id']} failed validation: "
            f"{exc.error_count()} error(s)"
        )
    return ReviewState(
        comment_id=comment["id"],
        url=comment["html_url"],
        updated_at=comment["updated_at"],
        code_review=payload.code_review,
        security_review=payload.security_review,
    )


def parse_review_state(
    comments: Iterable[dict[str, Any]], bot_id: int
) -> ReviewState | InvalidReviewState | None:
    """trusted bot の summary comment から `codex-review-state:v1` を取り出す（純粋関数）。

    summary marker で始まる trusted bot comment のうち `updated_at` が最大のものを採る。
    trusted bot 以外の comment の state は読まない。

    Returns:
        state がなければ None（terminal シグナルなし）、解析・検証できれば `ReviewState`、
        state があるのに判定に使えなければ `InvalidReviewState`。
    """
    newest = _latest_summary_comments(comments, bot_id)
    if not newest:
        return None
    if len(newest) > 1:
        if any(REVIEW_STATE_MARKER in comment["body"] for comment in newest):
            return InvalidReviewState(
                f"{len(newest)} summary comments share the latest updated_at; "
                "cannot select the codex-review-state"
            )
        return None
    return _parse_state_body(newest[0])


def _find_fresh_plus_one(
    reactions_json: list[dict[str, Any]],
    *,
    not_before: datetime,
    head_committed: datetime,
    bot_id: int,
) -> tuple[int, str, str] | None:
    """`not_before` と head commit 以降に付いた trusted bot の `+1` を探す。

    Returns:
        `(reaction_id, created_at, bot_login)`。該当なしは None。
    """
    for reaction in reactions_json:
        user = reaction.get("user") or {}
        if not _is_bot(user, bot_id) or reaction.get("content") != "+1":
            continue
        reaction_id = reaction.get("id")
        created_at = reaction.get("created_at")
        if not _is_positive_int(reaction_id) or not isinstance(created_at, str):
            continue
        try:
            created = parse_timestamp(created_at)
        except ValueError:
            continue
        if created >= head_committed and created >= not_before:
            return reaction_id, created_at, str(user.get("login") or "")
    return None


def _classify_state(
    state: ReviewState,
    reactions_json: list[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    *,
    head_review_exists: bool,
    bot_non_summary_comments: int,
    bot_id: int,
) -> PollResult | None:
    """`codex-review-state:v1` で RETRY / PASS / FALLBACK を判定する。terminal でなければ None。

    full SHA が head と一致する `completed` の state だけを head の完了として扱う。
    PASS には、summary comment の `updated_at` より後の秒（`>= floor(updated_at) + 1s`）かつ
    head commit 以降の trusted bot `+1` が要る（state は comment 本文の一部なので、遅くとも
    `updated_at` には書かれている）。

    評価順（Issue #557。RETRY を最優先にする）:
      1. code が head で completed / 指摘あり -> done_retry
      2. security が head 向けで completed / 指摘あり -> done_retry
      3. security が completed / null で head 以外 -> done_fallback（即時）
      4. code が head で completed / 指摘なしでない -> None
      5. security が head 向けで未完了 -> None
      6. security が head の completed / null（null 経路）で、trusted bot の summary 以外の
         Issue comment が 1 件以上 -> done_fallback（即時。👍 を待たない）
      7. head に bot review がある -> None
      8. fresh な bot `+1` がない -> None
      9. PASS 候補。null 経路の追加確認（未解決の head bot thread）は `_finalize_pass` が行う
    """
    code, security = state.code_review, state.security_review
    code_for_head = code.commit_sha == head_sha
    security_applies = security.status is not None and security.commit_sha in (head_sha, None)
    security_null = security.completed and security.has_any_findings is None
    short = head_sha[:7]

    if code_for_head and code.completed and code.has_any_findings:
        return PollResult(
            "done_retry",
            f"codex-review-state: code review found issues on head {short}",
            signal="state",
        )
    if security_applies and security.completed and security.has_any_findings:
        return PollResult(
            "done_retry",
            f"codex-review-state: security review found issues on head {short}",
            signal="state",
        )
    if security_null and security.commit_sha != head_sha:
        return PollResult(
            "done_fallback",
            "codex-review-state: security review completed without a findings result "
            f"for {str(security.commit_sha)[:7]}, not head {short}",
            signal="state",
        )
    if not (code_for_head and code.completed) or code.has_any_findings:
        return None
    if security_applies and not security.completed:
        return None
    if security_null and bot_non_summary_comments:
        return PollResult(
            "done_fallback",
            "codex-review-state: security review completed without a findings result on head "
            f"{short}, and the trusted bot posted {bot_non_summary_comments} non-summary "
            "comment(s)",
            signal="state",
        )
    if head_review_exists:
        return None
    try:
        threshold = next_second_after(parse_timestamp(state.updated_at))
        head_committed = parse_timestamp(head_committed_at)
    except ValueError:
        return None
    found = _find_fresh_plus_one(
        reactions_json, not_before=threshold, head_committed=head_committed, bot_id=bot_id
    )
    if found is None:
        return None
    reaction_id, created_at, bot_login = found
    return PollResult(
        "done_pass",
        f"bot +1 reaction {reaction_id} after codex-review-state completed for head {short}",
        approval=Approval(
            reaction_id=reaction_id,
            reaction_created_at=created_at,
            bot_login=bot_login,
            review_state=state,
        ),
        signal="state",
    )


def classify(
    reactions_json: list[dict[str, Any]],
    reviews_json: list[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    *,
    comments: Iterable[dict[str, Any]] = (),
    bot_id: int = BOT_ID,
    prev_state: Literal["init", "in_progress"] = "init",
) -> PollResult:
    """Single-poll state classifier.

    Order of evaluation:
      1. summary comment has a `codex-review-state` that cannot be used -> done_fallback
      2. state present: code / security review completed with findings on the current
         head -> done_retry; code review completed without findings (and the head's
         security review, if any, likewise) + later bot `+1` + no bot review on the head
         -> done_pass (a `+1` alone, or one that is stale / from another head, never passes).
         A security review `completed` with `has_any_findings: null` is accepted only on the
         head and only without trusted bot non-summary comments; otherwise -> done_fallback
         (Issue #557, see `_classify_state`)
      3. bot `eyes` reaction -> in_progress
      4. otherwise -> prev_state unchanged

    A summary without `codex-review-state` (or no summary at all) is never terminal: review
    bodies and the summary table are not read (Issue #478).

    `head_committed_at` is the ISO8601 UTC committedDate of the current PR head commit.
    """
    comments = list(comments)
    review_state = parse_review_state(comments, bot_id)
    if isinstance(review_state, InvalidReviewState):
        return PollResult(
            "done_fallback",
            f"codex-review-state is present but unusable: {review_state.reason}",
            signal="state",
        )

    head_reviews = [
        review
        for review in reviews_json
        if _is_bot(review.get("user") or {}, bot_id) and review.get("commit_id") == head_sha
    ]
    if review_state is not None:
        decided = _classify_state(
            review_state,
            reactions_json,
            head_sha,
            head_committed_at,
            head_review_exists=bool(head_reviews),
            bot_non_summary_comments=_count_bot_non_summary_comments(comments, bot_id),
            bot_id=bot_id,
        )
        if decided is not None:
            return decided

    for reaction in reactions_json:
        if not _is_bot(reaction.get("user") or {}, bot_id):
            continue
        if reaction.get("content") == "eyes":
            return PollResult("in_progress", "bot eyes reaction")

    if review_state is not None:
        return PollResult(prev_state, f"codex-review-state not terminal for head {head_sha[:7]}")
    return PollResult(prev_state, "no terminal signal")


def _gh_api(path: str) -> list[dict[str, Any]]:
    """Invoke `gh api --paginate --slurp <path>` and return the flattened JSON list.

    `--paginate` is required so polling sees the bot's latest review/reaction/comment
    even when the PR has many earlier entries (default page size = 30). gh prints each
    page as a separate JSON value, so `--slurp` is needed to wrap them in one outer
    array that can be parsed once; the pages are then flattened here.

    Raises subprocess.CalledProcessError on non-zero exit and ValueError on an
    unexpected shape, propagated to the caller so it can count consecutive failures.
    """
    proc = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path],
        capture_output=True,
        text=True,
        check=True,
    )
    pages = json.loads(proc.stdout)
    if not isinstance(pages, list):
        raise ValueError(f"expected page list from gh api {path}, got {type(pages).__name__}")
    items: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, list):
            raise ValueError(f"expected list page from gh api {path}, got {type(page).__name__}")
        items.extend(page)
    return items


def _gh_api_object(path: str) -> dict[str, Any]:
    """Invoke `gh api <path>` for a single-object endpoint (no pagination)."""
    proc = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected object from gh api {path}, got {type(parsed).__name__}")
    return parsed


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              author { __typename ... on Bot { databaseId } }
              commit { oid }
              originalCommit { oid }
            }
          }
        }
      }
    }
  }
}
"""


def _gh_graphql(query: str, variables: dict[str, str | int | None]) -> dict[str, Any]:
    """Invoke `gh api graphql` and return the response's `data` object.

    String variables are passed with `-f` and integers with `-F` (typed). A None variable is
    omitted so GraphQL sees it as null. Raises subprocess.CalledProcessError on non-zero exit
    (gh exits 1 when the response carries `errors`) and ValueError when the response has
    `errors` or no `data` object, propagated to the caller's consecutive failure counter.
    """
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        if value is None:
            continue
        flag = "-F" if isinstance(value, int) and not isinstance(value, bool) else "-f"
        args += [flag, f"{name}={value}"]
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected object from gh api graphql, got {type(parsed).__name__}")
    if parsed.get("errors"):
        raise ValueError(f"gh api graphql returned errors: {parsed['errors']!r}")
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise ValueError("gh api graphql returned no data object")
    return data


def _object_at(value: object, *keys: str) -> dict[str, Any]:
    """`value[keys[0]][keys[1]]...` を辿り、各段が object であることを確かめて返す。

    Raises:
        ValueError: 途中の段が object でない（null・欠落を含む）場合。
    """
    node = value
    for depth in range(len(keys) + 1):
        if not isinstance(node, dict):
            path = ".".join(keys[:depth]) or "<root>"
            raise ValueError(f"expected object at {path}, got {type(node).__name__}")
        if depth < len(keys):
            node = node.get(keys[depth])
    assert isinstance(node, dict)
    return node


def _fetch_review_threads(owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
    """GraphQL `reviewThreads` を全ページ取得し、thread node の list を返す。

    `gh --paginate` は nested な `pageInfo` を持つ query で cursor を取り違えうるので使わず、
    ここで `endCursor` を回す。

    Raises:
        ValueError: 応答の形が想定外、または `hasNextPage` なのに `endCursor` が無い・進まない場合。
        subprocess.CalledProcessError: `gh` が失敗した場合。
    """
    threads: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = _gh_graphql(
            REVIEW_THREADS_QUERY,
            {"owner": owner, "name": repo, "number": pr_number, "cursor": cursor},
        )
        connection = _object_at(data, "repository", "pullRequest", "reviewThreads")
        page_info = _object_at(connection, "pageInfo")
        nodes = connection.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("reviewThreads.nodes is not a list")
        threads.extend(nodes)
        has_next = page_info.get("hasNextPage")
        if not isinstance(has_next, bool):
            raise ValueError("reviewThreads.pageInfo.hasNextPage is not a bool")
        if not has_next:
            return threads
        end_cursor = page_info.get("endCursor")
        if not isinstance(end_cursor, str) or not end_cursor or end_cursor == cursor:
            raise ValueError("reviewThreads.pageInfo.endCursor does not advance")
        cursor = end_cursor


class _GqlCommit(BaseModel):
    """GraphQL `Commit`（`oid` だけを読む）。`oid` は 40 桁小文字 hex でなければならない。"""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    oid: _StateSha


class _GqlAuthor(BaseModel):
    """GraphQL `Actor`。`Bot` のときだけ `databaseId`（int）を必須にする。"""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    typename: Annotated[str, StringConstraints(min_length=1)] = Field(alias="__typename")
    database_id: int | None = Field(default=None, alias="databaseId")

    @model_validator(mode="after")
    def _validate_bot_id(self) -> _GqlAuthor:
        if self.typename == "Bot" and self.database_id is None:
            raise ValueError("Bot author requires an integer databaseId")
        return self


class _GqlThreadComment(BaseModel):
    """`reviewThread.comments.nodes[]`。query で要求した key は null 可でも欠落させない。"""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    author: _GqlAuthor | None
    commit: _GqlCommit | None
    original_commit: _GqlCommit | None = Field(alias="originalCommit")


class _GqlPageInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    has_next_page: bool = Field(alias="hasNextPage")


class _GqlThreadComments(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    page_info: _GqlPageInfo = Field(alias="pageInfo")
    nodes: list[_GqlThreadComment]


class _GqlReviewThread(BaseModel):
    """GraphQL `PullRequestReviewThread` の判定に使う部分（Issue #557 review MF2）。

    外部入力なので strict に検証し、形・型・SHA 形式の不正を「指摘なし」へ変換させない。
    """

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    is_resolved: bool = Field(alias="isResolved")
    comments: _GqlThreadComments


_REVIEW_THREADS_ADAPTER: TypeAdapter[list[_GqlReviewThread]] = TypeAdapter(list[_GqlReviewThread])


def _is_trusted_bot_author(author: _GqlAuthor | None, bot_id: int) -> bool:
    return author is not None and author.typename == "Bot" and author.database_id == bot_id


def _count_unresolved_head_bot_threads(
    threads: Iterable[object], head_sha: str, bot_id: int
) -> int:
    """未解決で head に当たる trusted bot の指摘 thread を数える（Issue #557 重要判断 (a)）。

    `isResolved == false` の thread のうち、次のいずれかを数える。

    - trusted bot（`Bot.databaseId == bot_id`）の comment で、`commit.oid` と
      `originalCommit.oid` が「ともに非 null かつ head 以外」と言えないものがある
      （head に付いている、または付いていないと証明できない）
    - thread 内の comment を全件見られない（`comments.pageInfo.hasNextPage`、fail-closed）

    `isOutdated` 単独では除外しない（GitHub は outdated でない comment の `commit` を head に
    付け替えるので、古い commit で付いた指摘でも head に当たっていれば数える）。

    全 thread を `_GqlReviewThread` で strict に検証してから数える。`Bot` の `databaseId` が
    int でない、`oid` が 40 桁 hex でない等の不正は 0 件扱いにせず例外にする。

    Raises:
        ValueError: thread / comment の形・型・値が想定外の場合（`ValidationError` を含む）。
    """
    validated = _REVIEW_THREADS_ADAPTER.validate_python(list(threads))
    count = 0
    for thread in validated:
        if thread.is_resolved:
            continue
        if thread.comments.page_info.has_next_page:
            count += 1
            continue
        for comment in thread.comments.nodes:
            if not _is_trusted_bot_author(comment.author, bot_id):
                continue
            commit, original = comment.commit, comment.original_commit
            if commit is None or original is None or head_sha in (commit.oid, original.oid):
                count += 1
                break
    return count


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _abort(reason: str) -> PollResult:
    return PollResult("done_abort", reason)


def _pull_head(pull: dict[str, Any]) -> str:
    head = pull.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError("pull request object has no head.sha")
    return sha


def _check_pull(pull: dict[str, Any], head_sha: str, when: str) -> PollResult | None:
    """PR head が固定 head のまま、かつ open であることを確認する。違えば ABORT。"""
    current = _pull_head(pull)
    if current != head_sha:
        return _abort(f"head changed {when}: expected {head_sha}, got {current}")
    if pull.get("state") != "open":
        return _abort(f"pull request is not open (state={pull.get('state')!r}) {when}")
    return None


def _state_entry_evidence(entry: ReviewStateEntry) -> dict[str, Any]:
    return {
        "commit_sha": entry.commit_sha,
        "status": entry.status,
        "has_any_findings": entry.has_any_findings,
    }


def _recheck_review_state(
    review_state: ReviewState,
    comments: list[dict[str, Any]],
    reactions_json: list[dict[str, Any]],
    reviews_json: list[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    *,
    prev_state: Literal["init", "in_progress"],
    bot_id: int,
) -> PollResult | None:
    """確定時に再取得した comments の summary が PASS 候補の state のままか確かめる。

    Returns:
        一致すれば None（確定を続けてよい）。一致しなければ PASS 以外の `PollResult`:
        再取得した comments での `classify` が RETRY / FALLBACK ならそれ、それ以外は
        非 terminal（`prev_state` か `in_progress`）。
    """
    if parse_review_state(comments, bot_id) == review_state:
        return None
    recheck = classify(
        reactions_json,
        reviews_json,
        head_sha,
        head_committed_at,
        comments=comments,
        bot_id=bot_id,
        prev_state=prev_state,
    )
    if recheck.state in ("done_retry", "done_fallback"):
        return PollResult(
            recheck.state,
            f"codex-review-state changed before finalization: {recheck.reason}",
            signal=recheck.signal,
        )
    if recheck.state == "in_progress":
        return recheck
    return PollResult(
        prev_state,
        f"codex-review-state changed before finalization for head {head_sha[:7]}; re-evaluating",
    )


def _finalize_pass(
    result: PollResult,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    head_committed_at: str,
    reactions_json: list[dict[str, Any]],
    reviews_json: list[dict[str, Any]],
    prev_state: Literal["init", "in_progress"],
    evidence_path: Path | None,
    utcnow: Callable[[], datetime],
    bot_id: int,
) -> PollResult:
    """PASS 候補を確定する: head 再確認（判定の直前・直後）→ 証跡（schema v3）の保存。

    state の full SHA の完全一致で承認しているので PR commits は引かない。証跡を atomic write
    してから `done_pass` を返すので、PASS verdict が出た時点で証跡は必ず存在する。どこかで
    失敗したら PASS ではなく ABORT にする。取得失敗は呼び出し側の連続失敗カウンタへ伝播させる
    （例外のまま送出）。

    security_review が head の `completed` / `has_any_findings: null`（null 経路、Issue #557）の
    ときだけ、判定直前と直後の head 確認の間で Issue comment を再取得し、GraphQL
    `reviewThreads` を全ページ取得する。trusted bot の summary 以外の comment、または未解決の
    head bot thread が 1 件でもあれば `done_fallback`（証跡は書かない）、ともに 0 件ならその件数を
    証跡の `checks.security_null_checks` に残す。

    再取得した comments の最新 summary の state が PASS 候補の state（comment ID・URL・
    `updated_at`・code / security の各値）と一致しなければ、古い候補では確定しない
    （Issue #557 review MF1）。再取得した comments で `classify` をやり直し、RETRY / FALLBACK なら
    それを返し、それ以外（state の更新・欠落・別候補）は非 terminal（`prev_state`）として次の poll で
    再評価させる。
    """
    approval = result.approval
    if approval is None:
        return _abort("evidence unavailable: approval details missing from PASS candidate")
    if evidence_path is None:
        return _abort("evidence unavailable: --evidence-path was not provided")

    pulls_path = f"repos/{owner}/{repo}/pulls/{pr_number}"
    before = _gh_api_object(pulls_path)
    fetched_at = format_timestamp(utcnow())
    stop = _check_pull(before, head_sha, "before decision")
    if stop is not None:
        return stop

    review_state = approval.review_state
    security_null_checks: dict[str, int] | None = None
    if is_security_null_on_head(review_state.security_review, head_sha):
        comments = _gh_api(f"repos/{owner}/{repo}/issues/{pr_number}/comments")
        changed = _recheck_review_state(
            review_state,
            comments,
            reactions_json,
            reviews_json,
            head_sha,
            head_committed_at,
            prev_state=prev_state,
            bot_id=bot_id,
        )
        if changed is not None:
            return changed
        security_null_checks = {
            "unresolved_head_bot_threads": _count_unresolved_head_bot_threads(
                _fetch_review_threads(owner, repo, pr_number), head_sha, bot_id
            ),
            "bot_non_summary_issue_comments": _count_bot_non_summary_comments(comments, bot_id),
        }
        if any(security_null_checks.values()):
            counts = ", ".join(f"{key}={value}" for key, value in security_null_checks.items())
            return PollResult(
                "done_fallback",
                "codex-review-state: security review completed without a findings result on "
                f"head {head_sha[:7]} and the conditions to accept it are not met: {counts}",
                signal="state",
            )

    after = _gh_api_object(pulls_path)
    stop = _check_pull(after, head_sha, "after decision")
    if stop is not None:
        return stop
    decided_at = format_timestamp(utcnow())

    reaction = {
        "id": approval.reaction_id,
        "content": "+1",
        "created_at": approval.reaction_created_at,
        "api_path": f"repos/{owner}/{repo}/issues/{pr_number}/reactions",
    }
    try:
        evidence = ReviewPollEvidenceV3.model_validate(
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION_V3,
                "kind": EVIDENCE_KIND,
                "result": "PASS",
                "provider": "github",
                "repository": {"owner": owner, "name": repo},
                "pull_request": {"number": pr_number, "url": str(before.get("html_url") or "")},
                "reviewed_head": {"sha": head_sha, "committed_at": head_committed_at},
                "approval": {
                    "bot": {"id": bot_id, "login": approval.bot_login},
                    "reaction": reaction,
                    "review_state": {
                        "comment_id": review_state.comment_id,
                        "url": review_state.url,
                        "comment_updated_at": review_state.updated_at,
                        "state_version": REVIEW_STATE_VERSION,
                        "code_review": _state_entry_evidence(review_state.code_review),
                        "security_review": _state_entry_evidence(review_state.security_review),
                    },
                },
                "checks": {
                    "head_sha_at_start": head_sha,
                    "head_sha_before_decision": _pull_head(before),
                    "head_sha_after_decision": _pull_head(after),
                    "current_head_bot_reviews": _count_bot_reviews_on_head(
                        reviews_json, head_sha, bot_id
                    ),
                    "security_null_checks": security_null_checks,
                },
                "fetched_at": fetched_at,
                "decided_at": decided_at,
            }
        )
        save_evidence(evidence_path, evidence)
    except ValidationError as exc:
        return _abort(f"evidence unavailable: approval evidence failed validation: {exc}")
    except OSError as exc:
        return _abort(f"evidence unavailable: could not write {evidence_path}: {exc}")

    return PollResult(
        "done_pass",
        "\n".join(
            [
                f"pr={owner}/{repo}#{pr_number}",
                f"head_sha={head_sha}",
                f"reaction_id={approval.reaction_id}",
                f"summary_comment={review_state.url}",
                f"evidence_path={evidence_path.resolve()}",
            ]
        ),
        approval=approval,
        signal=result.signal,
    )


def _count_bot_reviews_on_head(
    reviews_json: list[dict[str, Any]], head_sha: str, bot_id: int
) -> int:
    return sum(
        1
        for review in reviews_json
        if _is_bot(review.get("user") or {}, bot_id) and review.get("commit_id") == head_sha
    )


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
    evidence_path: Path | None = None,
    utcnow: Callable[[], datetime] = _utcnow,
    now: object = time.monotonic,
    sleep: object = time.sleep,
    emit_progress: object = _default_emit,
) -> PollResult:
    """Drive the state machine until a terminal state is reached.

    `now` and `sleep` are injectable for medium tests. `head_sha` and
    `head_committed_at` are resolved once by the caller and held constant across polls;
    every poll re-reads the PR and stops with ABORT as soon as its head differs (the
    evaluation target is never silently swapped).

    A PASS candidate is confirmed by `_finalize_pass`, which saves the approval evidence
    to `evidence_path` first. Without `evidence_path` a PASS candidate becomes ABORT.

    `emit_progress` is an injectable heartbeat sink (default: stdout flush
    print). It is called once per non-terminal poll just before sleeping —
    including the API-failure retry wait and the eyes-lost grace wait — so the
    startup console can distinguish "waiting / stalled / erroring" on every
    sleep path. It is fully isolated: any exception it raises is swallowed so
    the verdict state machine stays unchanged (Issue #235, heartbeat is
    observation only).

    A `done_fallback` from an unusable `codex-review-state` (or from a security review null
    that cannot be accepted, Issue #557) returns immediately without waiting for
    `NO_REACTION_TIMEOUT_SEC`. A summary without the state is not terminal, so
    it ends through the existing timeouts (`done_fallback` from init, `done_abort` after
    `eyes`).
    """
    pulls_path = f"repos/{owner}/{repo}/pulls/{pr_number}"
    reactions_path = f"repos/{owner}/{repo}/issues/{pr_number}/reactions"
    reviews_path = f"{pulls_path}/reviews"
    comments_path = f"repos/{owner}/{repo}/issues/{pr_number}/comments"

    state: Literal["init", "in_progress"] = "init"
    start = now()  # type: ignore[operator]
    in_progress_start: float | None = None
    eyes_lost_at: float | None = None
    consecutive_failures = 0

    def emit_heartbeat(state_label: str) -> None:
        """非 terminal poll の sleep 直前に heartbeat を 1 回 emit する（Issue #235）。

        elapsed / remaining は現在の state machine 状態から都度計算する。emitter が
        任意の例外を投げても polling 判定（state machine）には一切影響させない
        （観測のみの副作用）。
        """
        elapsed = now() - start  # type: ignore[operator]
        if state == "in_progress" and in_progress_start is not None:
            remaining = in_progress_timeout_sec - (now() - in_progress_start)  # type: ignore[operator]
        else:
            remaining = no_reaction_timeout_sec - elapsed
        try:
            emit_progress(  # type: ignore[operator]
                format_heartbeat(
                    elapsed_sec=elapsed,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    state=state_label,
                    remaining_sec=remaining,
                )
            )
        except Exception:
            pass

    while True:
        try:
            stop = _check_pull(_gh_api_object(pulls_path), head_sha, "during polling")
            if stop is not None:
                return stop
            reactions = _gh_api(reactions_path)
            reviews = _gh_api(reviews_path)
            comments = _gh_api(comments_path)
            result = classify(
                reactions,
                reviews,
                head_sha,
                head_committed_at,
                comments=comments,
                bot_id=bot_id,
                prev_state=state,
            )
            if result.state == "done_pass":
                result = _finalize_pass(
                    result,
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    head_committed_at=head_committed_at,
                    reactions_json=reactions,
                    reviews_json=reviews,
                    prev_state=state,
                    evidence_path=evidence_path,
                    utcnow=utcnow,
                    bot_id=bot_id,
                )
            consecutive_failures = 0
        except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
            consecutive_failures += 1
            if consecutive_failures >= api_failure_limit:
                return PollResult(
                    "done_abort",
                    f"gh api failed {consecutive_failures} times in a row: {exc}",
                )
            # transient error の待機中であることを起動コンソールへ可視化する。
            emit_heartbeat(f"api_retry:{consecutive_failures}/{api_failure_limit}")
            sleep(poll_interval_sec)  # type: ignore[operator]
            continue

        if result.state in ("done_pass", "done_retry", "done_fallback", "done_abort"):
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
                    # eyes 消失の grace 待機中であることを起動コンソールへ可視化する。
                    emit_heartbeat("in_progress/eyes_lost_grace")
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

        # Issue #235: non-terminal poll の末尾で heartbeat を 1 回 emit する。
        emit_heartbeat(state)

        sleep(poll_interval_sec)  # type: ignore[operator]


_VERDICT_MAP: dict[State, tuple[str, str]] = {
    "done_pass": (
        "PASS",
        "trusted bot approval bound to PR head SHA (codex-review-state + 👍)",
    ),
    "done_retry": (
        "RETRY",
        "codex auto-review が現在 head に指摘ありと判定（codex-review-state）",
    ),
    "done_fallback": (
        "BACK_FALLBACK",
        "codex auto-review シグナルを判定できない"
        "（NO_REACTION_TIMEOUT_SEC 経過、codex-review-state を解析できない、"
        "または security_review の null を受け入れる条件を満たさない）",
    ),
    "done_abort": ("ABORT", "codex auto-review polling failed"),
}


def emit_verdict(result: PollResult, suggestion: str) -> str:
    status, default_reason = _VERDICT_MAP.get(result.state, ("ABORT", "unexpected state"))
    evidence = "\n".join(f"  {line}" for line in result.reason.splitlines() or [""])
    return (
        "---VERDICT---\n"
        f"status: {status}\n"
        f"reason: |\n  {default_reason}\n"
        f"evidence: |\n{evidence}\n"
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
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=None,
        help="Where to save the approval evidence JSON; PASS is impossible without it",
    )
    args = parser.parse_args(argv)

    if not FULL_SHA_RE.fullmatch(args.head_sha):
        sys.stdout.write(
            emit_verdict(
                PollResult(
                    "done_abort",
                    f"invalid head sha: expected 40 lowercase hex characters, got {args.head_sha!r}",
                ),
                "PR head の完全な SHA を解決できているか確認してから再実行する。",
            )
        )
        return 0

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
        evidence_path=args.evidence_path,
    )

    suggestion = {
        "done_pass": "PR を close (or merge) するステップへ進む。close は承認証跡を現在の head と照合する。",
        "done_retry": "/pr-fix を実行して codex auto-review 指摘に対応する。",
        "done_fallback": "fallback の review skill (codex agent /review) を実行する。",
        "done_abort": "gh api / PR 状態を手動確認してから再実行する。",
    }.get(result.state, "skill 出力を確認する。")

    sys.stdout.write(emit_verdict(result, suggestion))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
