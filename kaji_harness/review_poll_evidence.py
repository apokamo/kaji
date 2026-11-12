"""Structured approval evidence emitted by ``review-poll`` on PASS (Issue #429, #473, #557).

``review-poll`` が PASS を返すとき、承認した PR identity・40 桁の reviewed head SHA・
承認シグナルを schema version 付き JSON として step attempt に保存する。後続 close は
この証跡を現在の PR head と照合する。

証跡は summary comment の ``codex-review-state:v1``（full SHA・``status``・``has_any_findings``）で
承認したことを記録する（Issue #473）。producer は schema v3 だけを書く。v3 は v2 の ``checks`` に
``security_null_checks`` を足した版で、``security_review`` が head の ``completed`` かつ
``has_any_findings: null`` のときだけ、条件付き受け入れの追加確認（未解決の head bot thread と
trusted bot の summary 以外の Issue comment がともに 0 件）を記録する（Issue #557）。
``load_evidence()`` は upgrade 前に作られた v2 も読み、v2 は従来どおり null を拒否する。
summary comment の表で承認した旧経路の schema v1 は Issue #478 で撤去し、拒否する。

producer（``codex_review_poll``）はこの model で書き込み前に不変条件 I1〜I9 を検証し、
consumer（``issue-close`` skill）は同じ不変条件を jq で検証する。両者の契約は
``docs/ARCHITECTURE.md`` の「review-poll の承認証跡」節にある不変条件表。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from kaji_harness.fsio import atomic_write

EVIDENCE_SCHEMA_VERSION_V2: Literal[2] = 2
EVIDENCE_SCHEMA_VERSION_V3: Literal[3] = 3
EVIDENCE_KIND = "kaji.review-poll.approval"
EVIDENCE_FILENAME = "review-poll-evidence.json"

TRUSTED_BOT_ID = 199175422
TRUSTED_BOT_LOGIN_PREFIX = "chatgpt-codex-connector"

_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
_TIMESTAMP_RE = re.compile(_TIMESTAMP_PATTERN)

Timestamp = Annotated[str, StringConstraints(pattern=_TIMESTAMP_PATTERN)]
FullSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]

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


def _check_common_invariants(
    *,
    repository: Repository,
    pull_request: PullRequest,
    reviewed_head: ReviewedHead,
    reaction: ReactionEvidence,
    fetched_at: str,
    decided_at: str,
    head_shas: tuple[str, str, str],
    current_head_bot_reviews: int,
) -> None:
    """I5 の api_path、I7 の head commit 順序、I8 の head、I9 の不変条件を検証する。

    Raises:
        ValueError: いずれかの不変条件に違反した場合。
    """
    expected_api_path = (
        f"repos/{repository.owner}/{repository.name}/issues/{pull_request.number}/reactions"
    )
    if reaction.api_path != expected_api_path:
        raise ValueError(f"approval.reaction.api_path must be {expected_api_path!r}")

    reaction_at = parse_timestamp(reaction.created_at)
    if reaction_at < parse_timestamp(reviewed_head.committed_at):
        raise ValueError("approval.reaction precedes the reviewed head commit")

    for name, value in zip(
        ("head_sha_at_start", "head_sha_before_decision", "head_sha_after_decision"),
        head_shas,
        strict=True,
    ):
        if value != reviewed_head.sha:
            raise ValueError(f"checks.{name} does not match reviewed_head.sha")
    if current_head_bot_reviews != 0:
        raise ValueError("checks.current_head_bot_reviews must be 0")

    fetched = parse_timestamp(fetched_at)
    decided = parse_timestamp(decided_at)
    if not reaction_at <= fetched <= decided:
        raise ValueError("expected reaction.created_at <= fetched_at <= decided_at")


class ReviewStateEntryEvidence(BaseModel):
    """``codex-review-state:v1`` の 1 review（``code_review`` / ``security_review``）の観測値。"""

    model_config = _STRICT_FROZEN

    commit_sha: FullSha | None
    status: NonEmptyStr | None
    has_any_findings: bool | None


class ReviewStateEvidence(BaseModel):
    """承認の SHA 対応付けに使った summary comment の ``codex-review-state:v1``。"""

    model_config = _STRICT_FROZEN

    comment_id: PositiveInt
    url: NonEmptyStr
    comment_updated_at: Timestamp
    state_version: Literal["v1"]
    code_review: ReviewStateEntryEvidence
    security_review: ReviewStateEntryEvidence

    @model_validator(mode="after")
    def _validate(self) -> ReviewStateEvidence:
        if not self.url.endswith(f"#issuecomment-{self.comment_id}"):
            raise ValueError("review_state.url must end with #issuecomment-<comment_id>")
        _checked(self.comment_updated_at)
        return self


class ApprovalV2(BaseModel):
    """承認シグナル一式（schema v2）。"""

    model_config = _STRICT_FROZEN

    bot: Bot
    reaction: ReactionEvidence
    review_state: ReviewStateEvidence


class ChecksV2(BaseModel):
    """確定確認の観測値（schema v2。full SHA で照合するため短縮 SHA の一意性確認を持たない）。"""

    model_config = _STRICT_FROZEN

    head_sha_at_start: FullSha
    head_sha_before_decision: FullSha
    head_sha_after_decision: FullSha
    current_head_bot_reviews: int


def _check_state_invariants(
    *,
    reviewed_head: ReviewedHead,
    reaction: ReactionEvidence,
    state: ReviewStateEvidence,
) -> None:
    """v2 / v3 共通の I6 の code 条件と、I7 の state 更新秒より後の reaction を検証する。

    Raises:
        ValueError: いずれかの不変条件に違反した場合。
    """
    code = state.code_review
    if (
        code.commit_sha != reviewed_head.sha
        or code.status != "completed"
        or code.has_any_findings is not False
    ):
        raise ValueError(
            "review_state.code_review must be completed without findings for reviewed head"
        )
    reaction_at = parse_timestamp(reaction.created_at)
    if reaction_at < next_second_after(parse_timestamp(state.comment_updated_at)):
        raise ValueError(
            "approval.reaction must be created in a later second than the state comment update"
        )


class ReviewEntryLike(Protocol):
    """``codex-review-state:v1`` の 1 review 分（poll 側の観測値と証跡の両方が満たす形）。"""

    @property
    def commit_sha(self) -> str | None: ...

    @property
    def status(self) -> str | None: ...

    @property
    def has_any_findings(self) -> bool | None: ...


def is_security_null_on_head(security: ReviewEntryLike, head_sha: str) -> bool:
    """security_review が head の ``completed`` かつ ``has_any_findings: null`` か（null 経路）。

    producer の判定（``codex_review_poll``）と証跡の I6(v3) が同じ定義を使う。
    """
    return (
        security.commit_sha == head_sha
        and security.status == "completed"
        and security.has_any_findings is None
    )


class ReviewPollEvidenceV2(BaseModel):
    """``review-poll`` PASS 時の承認証跡（schema v2、``codex-review-state:v1`` 経路）。

    Issue #557 以降 producer は v3 を書く。v2 は upgrade 前に作られた attempt の証跡を
    読むためだけに残し、security_review の null 経路は受け入れない。
    """

    model_config = _STRICT_FROZEN

    schema_version: Literal[2]
    kind: Literal["kaji.review-poll.approval"]
    result: Literal["PASS"]
    provider: Literal["github"]
    repository: Repository
    pull_request: PullRequest
    reviewed_head: ReviewedHead
    approval: ApprovalV2
    checks: ChecksV2
    fetched_at: Timestamp
    decided_at: Timestamp

    @model_validator(mode="after")
    def _validate_invariants(self) -> ReviewPollEvidenceV2:
        head = self.reviewed_head.sha
        state = self.approval.review_state
        _check_state_invariants(
            reviewed_head=self.reviewed_head, reaction=self.approval.reaction, state=state
        )
        security = state.security_review
        if not (
            security.status is None
            or (security.commit_sha is not None and security.commit_sha != head)
            or (
                security.status == "completed"
                and security.has_any_findings is False
                and security.commit_sha == head
            )
        ):
            raise ValueError(
                "review_state.security_review for reviewed head must be completed without findings"
            )

        checks = self.checks
        _check_common_invariants(
            repository=self.repository,
            pull_request=self.pull_request,
            reviewed_head=self.reviewed_head,
            reaction=self.approval.reaction,
            fetched_at=self.fetched_at,
            decided_at=self.decided_at,
            head_shas=(
                checks.head_sha_at_start,
                checks.head_sha_before_decision,
                checks.head_sha_after_decision,
            ),
            current_head_bot_reviews=checks.current_head_bot_reviews,
        )
        return self


class SecurityNullChecks(BaseModel):
    """security_review の null 経路で確定時に数えた追加確認の件数（schema v3）。

    Attributes:
        unresolved_head_bot_threads: 未解決で、trusted bot の comment が head に付いている
            （または head でないと証明できない）review thread の数。
        bot_non_summary_issue_comments: trusted bot による summary 以外の PR Issue comment の数。
    """

    model_config = _STRICT_FROZEN

    unresolved_head_bot_threads: NonNegativeInt
    bot_non_summary_issue_comments: NonNegativeInt


class ChecksV3(BaseModel):
    """確定確認の観測値（schema v3。v2 の 4 キーに ``security_null_checks`` を足したもの）。"""

    model_config = _STRICT_FROZEN

    head_sha_at_start: FullSha
    head_sha_before_decision: FullSha
    head_sha_after_decision: FullSha
    current_head_bot_reviews: int
    security_null_checks: SecurityNullChecks | None


class ReviewPollEvidenceV3(BaseModel):
    """``review-poll`` PASS 時の承認証跡（schema v3、Issue #557）。

    I6(v3) の security_review 条件は次のいずれか:

    - ``status`` が null（観測なし）で ``security_null_checks`` が null
    - ``commit_sha`` が head 以外の SHA で、``completed`` かつ ``has_any_findings: null`` ではなく、
      ``security_null_checks`` が null
    - head の ``completed``・``has_any_findings: false`` で ``security_null_checks`` が null
    - head の ``completed``・``has_any_findings: null`` で ``security_null_checks`` の 2 件数がともに 0
    """

    model_config = _STRICT_FROZEN

    schema_version: Literal[3]
    kind: Literal["kaji.review-poll.approval"]
    result: Literal["PASS"]
    provider: Literal["github"]
    repository: Repository
    pull_request: PullRequest
    reviewed_head: ReviewedHead
    approval: ApprovalV2
    checks: ChecksV3
    fetched_at: Timestamp
    decided_at: Timestamp

    @model_validator(mode="after")
    def _validate_invariants(self) -> ReviewPollEvidenceV3:
        head = self.reviewed_head.sha
        state = self.approval.review_state
        _check_state_invariants(
            reviewed_head=self.reviewed_head, reaction=self.approval.reaction, state=state
        )
        security = state.security_review
        null_checks = self.checks.security_null_checks
        if is_security_null_on_head(security, head):
            if null_checks is None or (
                null_checks.unresolved_head_bot_threads != 0
                or null_checks.bot_non_summary_issue_comments != 0
            ):
                raise ValueError(
                    "security_review completed with null findings on reviewed head requires "
                    "checks.security_null_checks with zero unresolved bot threads and zero "
                    "bot non-summary comments"
                )
        else:
            if null_checks is not None:
                raise ValueError(
                    "checks.security_null_checks must be null unless security_review is "
                    "completed with null findings on reviewed head"
                )
            completed_null = security.status == "completed" and security.has_any_findings is None
            if not (
                security.status is None
                or (
                    security.commit_sha is not None
                    and security.commit_sha != head
                    and not completed_null
                )
                or (
                    security.status == "completed"
                    and security.has_any_findings is False
                    and security.commit_sha == head
                )
            ):
                raise ValueError(
                    "review_state.security_review for reviewed head must be completed "
                    "without findings, or completed with null findings and checked"
                )

        checks = self.checks
        _check_common_invariants(
            repository=self.repository,
            pull_request=self.pull_request,
            reviewed_head=self.reviewed_head,
            reaction=self.approval.reaction,
            fetched_at=self.fetched_at,
            decided_at=self.decided_at,
            head_shas=(
                checks.head_sha_at_start,
                checks.head_sha_before_decision,
                checks.head_sha_after_decision,
            ),
            current_head_bot_reviews=checks.current_head_bot_reviews,
        )
        return self


def save_evidence(path: Path, evidence: ReviewPollEvidenceV3) -> None:
    """検証済みの証跡（producer が書くのは schema v3 だけ）を atomic に書き込む。"""
    content = json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, content)


def _declared_schema_version(text: str) -> object:
    """JSON の top-level ``schema_version`` を返す。JSON object でなければ None。"""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed.get("schema_version") if isinstance(parsed, dict) else None


def load_evidence(path: Path) -> ReviewPollEvidenceV2 | ReviewPollEvidenceV3:
    """証跡を読み込み、``schema_version`` に応じて v2 / v3 の不変条件を検証する。

    ``schema_version`` が整数の 3 なら v3、それ以外はすべて v2 の model で検証する
    （v2 の ``Literal[2]`` が 2 以外の版を拒否する）。

    Raises:
        pydantic.ValidationError: JSON が壊れている、``schema_version`` が 2 / 3 以外（撤去した
            1 を含む）、または不変条件に違反した場合。
    """
    text = path.read_text(encoding="utf-8")
    version = _declared_schema_version(text)
    if type(version) is int and version == EVIDENCE_SCHEMA_VERSION_V3:
        return ReviewPollEvidenceV3.model_validate_json(text)
    return ReviewPollEvidenceV2.model_validate_json(text)
