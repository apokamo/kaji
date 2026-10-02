"""Structured approval evidence emitted by ``review-poll`` on PASS (Issue #429).

``review-poll`` が PASS を返すとき、承認した PR identity・40 桁の reviewed head SHA・
承認シグナルを schema version 付き JSON として step attempt に保存する。後続 close は
この証跡を現在の PR head と照合する。

producer（``codex_review_poll``）はこの model で書き込み前に不変条件 I1〜I9 を検証し、
consumer（``issue-close`` skill）は同じ不変条件を jq で検証する。両者の契約は
``docs/ARCHITECTURE.md`` の「review-poll の承認証跡」節にある不変条件表。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kaji_harness.fsio import atomic_write

EVIDENCE_SCHEMA_VERSION: Literal[1] = 1
EVIDENCE_KIND = "kaji.review-poll.approval"
EVIDENCE_FILENAME = "review-poll-evidence.json"

TRUSTED_BOT_ID = 199175422
TRUSTED_BOT_LOGIN_PREFIX = "chatgpt-codex-connector"

_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
_TIMESTAMP_RE = re.compile(_TIMESTAMP_PATTERN)

Timestamp = Annotated[str, StringConstraints(pattern=_TIMESTAMP_PATTERN)]
FullSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
ShortSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]

_STRICT_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True)


def parse_timestamp(value: str) -> datetime:
    """GitHub の ISO 8601 UTC（``Z`` 終端、小数秒は任意）を aware datetime へ変換する。

    Raises:
        ValueError: 形式が ``Z`` 終端の ISO 8601 UTC でない、または暦として不正な場合。
    """
    if not _TIMESTAMP_RE.match(value):
        raise ValueError(f"unsupported timestamp format: {value!r}")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    """aware datetime を秒精度の UTC ``Z`` 形式へ変換する。"""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_second_after(value: datetime) -> datetime:
    """``floor(value) + 1s``。秒精度の値がこれ以上なら ``value`` より後だと証明できる。"""
    return value.replace(microsecond=0) + timedelta(seconds=1)


def _checked(value: str) -> str:
    parse_timestamp(value)
    return value


class Repository(BaseModel):
    """承認対象の repository identity。"""

    model_config = _STRICT_FROZEN

    owner: NonEmptyStr
    name: NonEmptyStr


class PullRequest(BaseModel):
    """承認対象の PR identity。"""

    model_config = _STRICT_FROZEN

    number: PositiveInt
    url: NonEmptyStr

    @model_validator(mode="after")
    def _validate_url(self) -> PullRequest:
        if not (
            self.url.startswith("https://github.com/") and self.url.endswith(f"/pull/{self.number}")
        ):
            raise ValueError("pull_request.url must be a github.com URL ending in /pull/<number>")
        return self


class ReviewedHead(BaseModel):
    """承認された PR head。"""

    model_config = _STRICT_FROZEN

    sha: FullSha
    committed_at: Timestamp

    @model_validator(mode="after")
    def _validate_timestamp(self) -> ReviewedHead:
        _checked(self.committed_at)
        return self


class Bot(BaseModel):
    """承認元 bot identity。"""

    model_config = _STRICT_FROZEN

    id: int
    login: NonEmptyStr

    @model_validator(mode="after")
    def _validate_trusted(self) -> Bot:
        if self.id != TRUSTED_BOT_ID:
            raise ValueError(f"approval.bot.id must be {TRUSTED_BOT_ID}")
        if not self.login.startswith(TRUSTED_BOT_LOGIN_PREFIX):
            raise ValueError(f"approval.bot.login must start with {TRUSTED_BOT_LOGIN_PREFIX!r}")
        return self


class ReactionEvidence(BaseModel):
    """承認に使った ``+1`` reaction。reaction は SHA も URL も持たないため ID と API path を残す。"""

    model_config = _STRICT_FROZEN

    id: PositiveInt
    content: Literal["+1"]
    created_at: Timestamp
    api_path: NonEmptyStr

    @model_validator(mode="after")
    def _validate_timestamp(self) -> ReactionEvidence:
        _checked(self.created_at)
        return self


class ReviewSummaryEvidence(BaseModel):
    """承認の SHA 対応付けに使った bot の review summary comment。"""

    model_config = _STRICT_FROZEN

    comment_id: PositiveInt
    url: NonEmptyStr
    commit_short_sha: ShortSha
    status: Literal["Completed"]
    completed_at: Timestamp
    comment_updated_at: Timestamp

    @model_validator(mode="after")
    def _validate(self) -> ReviewSummaryEvidence:
        if not self.url.endswith(f"#issuecomment-{self.comment_id}"):
            raise ValueError("review_summary.url must end with #issuecomment-<comment_id>")
        completed = parse_timestamp(self.completed_at)
        updated = parse_timestamp(self.comment_updated_at)
        if updated < completed.replace(microsecond=0):
            raise ValueError("review_summary.comment_updated_at precedes completed_at")
        return self


class Approval(BaseModel):
    """承認シグナル一式。"""

    model_config = _STRICT_FROZEN

    bot: Bot
    reaction: ReactionEvidence
    review_summary: ReviewSummaryEvidence


class Checks(BaseModel):
    """確定確認の観測値。"""

    model_config = _STRICT_FROZEN

    head_sha_at_start: FullSha
    head_sha_before_decision: FullSha
    head_sha_after_decision: FullSha
    short_sha_matching_pr_commits: int
    current_head_bot_reviews: int


class ReviewPollEvidence(BaseModel):
    """``review-poll`` PASS 時の承認証跡（schema v1）。"""

    model_config = _STRICT_FROZEN

    schema_version: Literal[1]
    kind: Literal["kaji.review-poll.approval"]
    result: Literal["PASS"]
    provider: Literal["github"]
    repository: Repository
    pull_request: PullRequest
    reviewed_head: ReviewedHead
    approval: Approval
    checks: Checks
    fetched_at: Timestamp
    decided_at: Timestamp

    @model_validator(mode="after")
    def _validate_invariants(self) -> ReviewPollEvidence:
        head = self.reviewed_head.sha
        reaction = self.approval.reaction
        summary = self.approval.review_summary

        expected_api_path = (
            f"repos/{self.repository.owner}/{self.repository.name}"
            f"/issues/{self.pull_request.number}/reactions"
        )
        if reaction.api_path != expected_api_path:
            raise ValueError(f"approval.reaction.api_path must be {expected_api_path!r}")

        if not head.startswith(summary.commit_short_sha):
            raise ValueError("review_summary.commit_short_sha is not a prefix of reviewed head")

        reaction_at = parse_timestamp(reaction.created_at)
        completed_at = parse_timestamp(summary.completed_at)
        if reaction_at < next_second_after(completed_at):
            raise ValueError("approval.reaction must be created in a later second than completion")
        if reaction_at < parse_timestamp(self.reviewed_head.committed_at):
            raise ValueError("approval.reaction precedes the reviewed head commit")

        checks = self.checks
        for name, value in (
            ("head_sha_at_start", checks.head_sha_at_start),
            ("head_sha_before_decision", checks.head_sha_before_decision),
            ("head_sha_after_decision", checks.head_sha_after_decision),
        ):
            if value != head:
                raise ValueError(f"checks.{name} does not match reviewed_head.sha")
        if checks.short_sha_matching_pr_commits != 1:
            raise ValueError("checks.short_sha_matching_pr_commits must be 1")
        if checks.current_head_bot_reviews != 0:
            raise ValueError("checks.current_head_bot_reviews must be 0")

        fetched = parse_timestamp(self.fetched_at)
        decided = parse_timestamp(self.decided_at)
        if not reaction_at <= fetched <= decided:
            raise ValueError("expected reaction.created_at <= fetched_at <= decided_at")
        return self


def save_evidence(path: Path, evidence: ReviewPollEvidence) -> None:
    """検証済みの証跡を atomic に書き込む。"""
    content = json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, content)


def load_evidence(path: Path) -> ReviewPollEvidence:
    """証跡を読み込み、不変条件を検証する。"""
    return ReviewPollEvidence.model_validate_json(path.read_text(encoding="utf-8"))
