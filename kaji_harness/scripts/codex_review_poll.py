"""Codex auto-review polling helper for the `review-poll` step.

Polls the GitHub Reactions / Reviews / Comments APIs for `chatgpt-codex-connector[bot]`
signals and emits a verdict consumed by the workflow runner.

The primary signal is the machine-readable `<!-- codex-review-state:v1 {...} -->` in the
bot's review summary comment (full SHA / `status` / `has_any_findings`, Issue #473). A
summary without that state falls back to the deprecated legacy markup path (review body
heading / summary table). PASS always requires a SHA-bearing bot signal *and* a `+1`
reaction created after it; a `+1` alone carries no commit SHA and is never an approval
(Issue #429). On PASS the approving PR / full head SHA / signal IDs are saved as
`review_poll_evidence` JSON before the verdict is emitted, so a later close step can
verify them against the current PR head.

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

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from kaji_harness.review_poll_evidence import (
    EVIDENCE_KIND,
    EVIDENCE_SCHEMA_VERSION_V1,
    EVIDENCE_SCHEMA_VERSION_V2,
    ReviewPollEvidence,
    ReviewPollEvidenceV2,
    format_timestamp,
    next_second_after,
    parse_timestamp,
    save_evidence,
)

BOT_ID = 199175422
BOT_LOGIN_PREFIX = "chatgpt-codex-connector"
CODEX_REVIEW_BODY_MARKER = "### 💡 Codex Review"
SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
REVIEW_STATE_MARKER = "<!-- codex-review-state:"
REVIEW_STATE_VERSION = "v1"
_REVIEW_STATE_RE = re.compile(
    r"<!-- codex-review-state:(?P<version>\S+) (?P<payload>.*?) -->", re.DOTALL
)
HTML_COMMENT_OPEN = "<!--"
HTML_COMMENT_CLOSE = "-->"
LEGACY_DEPRECATION_WARNING = (
    "kaji: deprecation: review-poll used the legacy codex review markup "
    "(no codex-review-state:v1 in the summary comment); "
    "this path will be removed in the next release"
)
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SUMMARY_COMMIT_CELL_RE = re.compile(r"`([0-9a-f]{7,40})`")
_SUMMARY_DATETIME_RE = re.compile(r'datetime="([^"]+)"')
_SUMMARY_COMPLETED_RE = re.compile(r"\bCompleted\b")
_MARKUP_RE = re.compile(r"<[^>]*>|\*")

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
class ReviewSummary:
    """bot の review summary comment から取り出した `Code Review` 行。"""

    comment_id: int
    url: str
    commit_short_sha: str
    status: str
    completed_at: str | None
    updated_at: str


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
        if self.status == "completed" and (
            self.commit_sha is None or self.has_any_findings is None
        ):
            raise ValueError("completed review state requires commit_sha and has_any_findings")
        return self

    @property
    def completed(self) -> bool:
        return self.status == "completed"


class _ReviewStatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    code_review: ReviewStateEntry
    security_review: ReviewStateEntry


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
    """PASS 候補の承認シグナル（`+1` reaction と、SHA を持つ summary comment）。

    `summary` は旧経路（表）、`review_state` は state 経路の根拠で、どちらか一方だけを持つ。
    """

    reaction_id: int
    reaction_created_at: str
    bot_login: str
    summary: ReviewSummary | None = None
    review_state: ReviewState | None = None


@dataclass(frozen=True)
class PollResult:
    """1 poll の判定結果。`signal` は terminal を決めた経路（state / 旧経路）。"""

    state: State
    reason: str
    approval: Approval | None = None
    signal: Literal["state", "legacy"] | None = None


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


def _default_warn(line: str) -> None:
    """deprecation 警告を stderr へ書く既定 sink。書き込み失敗は観測用の副作用として無視する。"""
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except OSError:
        return


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


def _parse_summary_row(body: str, comment: dict[str, Any]) -> ReviewSummary | None:
    """summary 本文の表から `Code Review` 行をちょうど 1 行取り出す。解釈できなければ None。"""
    rows: list[list[str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 4 and "Code Review" in cells[0]:
            rows.append(cells)
    if len(rows) != 1:
        return None
    _, status_cell, commit_cell = rows[0][:3]
    commit = _SUMMARY_COMMIT_CELL_RE.fullmatch(commit_cell)
    if commit is None:
        return None
    datetime_match = _SUMMARY_DATETIME_RE.search(status_cell)
    return ReviewSummary(
        comment_id=comment["id"],
        url=comment["html_url"],
        commit_short_sha=commit.group(1),
        status=(
            "Completed"
            if _SUMMARY_COMPLETED_RE.search(status_cell)
            else _MARKUP_RE.sub("", status_cell).strip()
        ),
        completed_at=datetime_match.group(1) if datetime_match else None,
        updated_at=comment["updated_at"],
    )


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
        and isinstance(comment.get("body"), str)
        and comment["body"].lstrip().startswith(SUMMARY_MARKER)
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


def parse_review_summary(comments: Iterable[dict[str, Any]], bot_id: int) -> ReviewSummary | None:
    """trusted bot の review summary comment の表を解析する（純粋関数、旧経路）。

    marker で始まる bot comment のうち `updated_at` が最大のものを採る。最大値が同じ
    comment が複数あれば曖昧として None。`Code Review` 行が 0 / 複数、commit セルが
    短縮 SHA（7〜40 桁の小文字 hex を backtick で囲んだもの）でない場合も None。
    """
    newest = _latest_summary_comments(comments, bot_id)
    if len(newest) != 1:
        return None
    return _parse_summary_row(newest[0]["body"], newest[0])


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

    summary comment の選別は `parse_review_summary` と同じ規則。trusted bot 以外の comment の
    state は読まない。

    Returns:
        state がなければ None（旧経路へ）、解析・検証できれば `ReviewState`、
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


def _skip_leading_html_comments(body: str) -> str | None:
    """先頭の空白と HTML コメント（0 個以上）を読み飛ばした残りを返す。閉じないコメントは None。"""
    text = body.lstrip()
    while text.startswith(HTML_COMMENT_OPEN):
        end = text.find(HTML_COMMENT_CLOSE, len(HTML_COMMENT_OPEN))
        if end < 0:
            return None
        text = text[end + len(HTML_COMMENT_CLOSE) :].lstrip()
    return text


def _is_codex_review_body(body: str) -> bool:
    rest = _skip_leading_html_comments(body)
    return rest is not None and rest.startswith(CODEX_REVIEW_BODY_MARKER)


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


def _find_approval(
    reactions_json: list[dict[str, Any]],
    comments: Iterable[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    bot_id: int,
) -> Approval | None:
    """summary Completed(head) と、その完了より後の秒に付いた trusted bot の `+1` を探す。

    reaction は秒精度、summary の完了時刻はマイクロ秒精度なので、reaction の秒が完了秒より
    厳密に後（`>= floor(completed_at) + 1s`）の場合だけ「Completed の後」と証明できる。
    同じ秒の reaction は順序が不明なので承認にしない。
    """
    summary = parse_review_summary(comments, bot_id)
    if summary is None or summary.status != "Completed" or summary.completed_at is None:
        return None
    if not head_sha.startswith(summary.commit_short_sha):
        return None
    try:
        threshold = next_second_after(parse_timestamp(summary.completed_at))
        head_committed = parse_timestamp(head_committed_at)
    except ValueError:
        return None
    found = _find_fresh_plus_one(
        reactions_json, not_before=threshold, head_committed=head_committed, bot_id=bot_id
    )
    if found is None:
        return None
    reaction_id, created_at, bot_login = found
    return Approval(
        reaction_id=reaction_id,
        reaction_created_at=created_at,
        bot_login=bot_login,
        summary=summary,
    )


def _classify_state(
    state: ReviewState,
    reactions_json: list[dict[str, Any]],
    head_sha: str,
    head_committed_at: str,
    *,
    head_review_exists: bool,
    bot_id: int,
) -> PollResult | None:
    """`codex-review-state:v1` で RETRY / PASS を判定する。terminal でなければ None。

    full SHA が head と一致する `completed` の state だけを head の完了として扱う。
    PASS には、summary comment の `updated_at` より後の秒（`>= floor(updated_at) + 1s`）かつ
    head commit 以降の trusted bot `+1` が要る（state は comment 本文の一部なので、遅くとも
    `updated_at` には書かれている）。
    """
    code, security = state.code_review, state.security_review
    code_for_head = code.commit_sha == head_sha
    security_applies = security.status is not None and security.commit_sha in (head_sha, None)
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
    if not (code_for_head and code.completed) or code.has_any_findings:
        return None
    if security_applies and not (security.completed and security.has_any_findings is False):
        return None
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
      2. state path (state present): code / security review completed with findings on
         the current head -> done_retry; code review completed without findings (and the
         head's security review, if any, likewise) + later bot `+1` + no bot review on the
         head -> done_pass
      3. legacy path (state absent, deprecated): bot COMMENTED review on current head whose
         body starts with the Codex Review heading after leading HTML comments ->
         done_retry; any other bot review on current head blocks PASS; summary table
         Completed for current head + later bot `+1` -> done_pass
         (a `+1` alone, or one that is stale / from another head, never passes)
      4. bot `eyes` reaction -> in_progress
      5. otherwise -> prev_state unchanged

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
    ambiguous_review = False
    if review_state is not None:
        decided = _classify_state(
            review_state,
            reactions_json,
            head_sha,
            head_committed_at,
            head_review_exists=bool(head_reviews),
            bot_id=bot_id,
        )
        if decided is not None:
            return decided
    else:
        for review in head_reviews:
            if review.get("state") == "COMMENTED" and _is_codex_review_body(
                review.get("body") or ""
            ):
                return PollResult(
                    "done_retry", f"bot review on head {head_sha[:7]}", signal="legacy"
                )
        ambiguous_review = bool(head_reviews)
        if not ambiguous_review:
            approval = _find_approval(reactions_json, comments, head_sha, head_committed_at, bot_id)
            if approval is not None:
                return PollResult(
                    "done_pass",
                    f"bot +1 reaction {approval.reaction_id} after summary Completed "
                    f"for head {head_sha[:7]}",
                    approval=approval,
                    signal="legacy",
                )

    for reaction in reactions_json:
        if not _is_bot(reaction.get("user") or {}, bot_id):
            continue
        if reaction.get("content") == "eyes":
            return PollResult("in_progress", "bot eyes reaction")

    if ambiguous_review:
        return PollResult(prev_state, f"ambiguous bot review on head {head_sha[:7]}")
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


def _finalize_pass(
    result: PollResult,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    head_committed_at: str,
    current_head_bot_reviews: int,
    evidence_path: Path | None,
    utcnow: Callable[[], datetime],
    bot_id: int,
) -> PollResult:
    """PASS 候補を確定する: head 再確認 →（旧経路のみ）短縮 SHA の一意性 → 証跡の保存。

    state 経路は full SHA の完全一致で承認しているので PR commits を引かず、schema v2 の
    証跡を書く。旧経路は短縮 SHA を PR commits と突き合わせ、schema v1 の証跡を書く。
    証跡を atomic write してから `done_pass` を返すので、PASS verdict が出た時点で証跡は
    必ず存在する。どこかで失敗したら PASS ではなく ABORT にする。取得失敗は呼び出し側の
    連続失敗カウンタへ伝播させる（例外のまま送出）。
    """
    approval = result.approval
    if approval is None or (approval.summary is None) == (approval.review_state is None):
        return _abort("evidence unavailable: approval details missing from PASS candidate")
    if evidence_path is None:
        return _abort("evidence unavailable: --evidence-path was not provided")

    pulls_path = f"repos/{owner}/{repo}/pulls/{pr_number}"
    before = _gh_api_object(pulls_path)
    fetched_at = format_timestamp(utcnow())
    stop = _check_pull(before, head_sha, "before decision")
    if stop is not None:
        return stop

    summary = approval.summary
    matches: list[str] = []
    if summary is not None:
        short_sha = summary.commit_short_sha
        commits = _gh_api(f"{pulls_path}/commits")
        matches = [
            commit["sha"]
            for commit in commits
            if isinstance(commit.get("sha"), str) and commit["sha"].startswith(short_sha)
        ]
        if len(matches) != 1 or matches[0] != head_sha:
            return _abort(
                f"SHA mapping ambiguous: {len(matches)} PR commits match short sha {short_sha} "
                f"(head {head_sha})"
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
    checks: dict[str, Any] = {
        "head_sha_at_start": head_sha,
        "head_sha_before_decision": _pull_head(before),
        "head_sha_after_decision": _pull_head(after),
        "current_head_bot_reviews": current_head_bot_reviews,
    }
    payload: dict[str, Any] = {
        "kind": EVIDENCE_KIND,
        "result": "PASS",
        "provider": "github",
        "repository": {"owner": owner, "name": repo},
        "pull_request": {"number": pr_number, "url": str(before.get("html_url") or "")},
        "reviewed_head": {"sha": head_sha, "committed_at": head_committed_at},
        "fetched_at": fetched_at,
        "decided_at": decided_at,
    }
    review_state = approval.review_state
    try:
        evidence: ReviewPollEvidence | ReviewPollEvidenceV2
        if review_state is not None:
            summary_url = review_state.url
            evidence = ReviewPollEvidenceV2.model_validate(
                {
                    **payload,
                    "schema_version": EVIDENCE_SCHEMA_VERSION_V2,
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
                    "checks": checks,
                }
            )
        else:
            assert summary is not None
            summary_url = summary.url
            evidence = ReviewPollEvidence.model_validate(
                {
                    **payload,
                    "schema_version": EVIDENCE_SCHEMA_VERSION_V1,
                    "approval": {
                        "bot": {"id": bot_id, "login": approval.bot_login},
                        "reaction": reaction,
                        "review_summary": {
                            "comment_id": summary.comment_id,
                            "url": summary.url,
                            "commit_short_sha": summary.commit_short_sha,
                            "status": summary.status,
                            "completed_at": summary.completed_at,
                            "comment_updated_at": summary.updated_at,
                        },
                    },
                    "checks": {**checks, "short_sha_matching_pr_commits": len(matches)},
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
                f"summary_comment={summary_url}",
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
    emit_warning: Callable[[str], None] = _default_warn,
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

    When the deprecated legacy markup path decides the terminal result, a deprecation
    warning is written once to `emit_warning` (default: stderr), never to stdout. A
    `done_fallback` from an unusable `codex-review-state` returns immediately without
    waiting for `NO_REACTION_TIMEOUT_SEC`.
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
                    current_head_bot_reviews=_count_bot_reviews_on_head(reviews, head_sha, bot_id),
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
            if result.signal == "legacy":
                emit_warning(LEGACY_DEPRECATION_WARNING)
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
        "trusted bot approval bound to PR head SHA (codex-review-state / review summary + 👍)",
    ),
    "done_retry": (
        "RETRY",
        "codex auto-review が現在 head に指摘ありと判定（codex-review-state または COMMENTED review）",
    ),
    "done_fallback": (
        "BACK_FALLBACK",
        "codex auto-review シグナルを判定できない"
        "（NO_REACTION_TIMEOUT_SEC 経過、または codex-review-state を解析できない）",
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
