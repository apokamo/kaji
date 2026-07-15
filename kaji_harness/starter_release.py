"""Deterministic planning for managed starter releases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReleaseAction = Literal[
    "atomic_push",
    "create_release",
    "update_state_table",
    "close_tracking_issue",
]


class StarterTagObservation(BaseModel):
    """Observed starter tag and target commit."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    sha: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    annotated: bool


class ReleasePlanInput(BaseModel):
    """Validated external observations used to select a release path."""

    model_config = ConfigDict(extra="forbid")

    target_kaji_release: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    candidate_sha: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    tags: list[StarterTagObservation]
    releases: list[str]
    state_table_row_exists: bool
    state_table_status: Literal["PENDING", "PASS", "N/A"]
    tracking_issue_state: Literal["open", "closed"]


class ReleasePlan(BaseModel):
    """Machine-readable release decision returned by the CLI."""

    model_config = ConfigDict(extra="forbid")

    route: Literal[1, 2, 3, 4, 5]
    decision: Literal["PUBLISH", "RESUME", "IDEMPOTENT", "ABORT"]
    tag: str | None
    requires_push: bool
    remaining_actions: list[ReleaseAction]
    reason: str


@dataclass(frozen=True)
class ReleaseReviewEvidence:
    """Latest review verdict fields consumed by release pre-flight."""

    status: str
    target: str
    base: str
    candidate: str


@dataclass(frozen=True)
class ReleaseReviewContext:
    """Current repository state compared with review evidence."""

    target: str
    local_head: str
    remote_main: str
    published_candidate: bool


def is_na_candidate(base_sha: str, candidate_sha: str) -> bool:
    """Return whether review evidence represents a no-change candidate."""
    return base_sha == candidate_sha


def is_review_current(
    evidence: ReleaseReviewEvidence,
    context: ReleaseReviewContext,
) -> bool:
    """Return whether the latest PASS is fresh for the current release path.

    Args:
        evidence: Structured fields resolved from the latest review marker.
        context: Current target and starter Git state.

    Returns:
        ``True`` only when every path-specific equality gate holds.
    """
    if evidence.status != "PASS":
        return False
    if evidence.target != context.target or evidence.candidate != context.local_head:
        return False
    expected_remote = evidence.candidate if context.published_candidate else evidence.base
    return context.remote_main == expected_remote


def build_release_plan(observation: ReleasePlanInput) -> ReleasePlan:
    """Build one deterministic plan from validated repository observations.

    Args:
        observation: Tags, releases, bookkeeping state, and candidate SHA.

    Returns:
        A numbered route matching the starter release runbook decision table.
    """
    tag_prefix = f"kaji-{observation.target_kaji_release}"
    target_tags, invalid_tag_reason = _target_tags(observation.tags, tag_prefix)
    if invalid_tag_reason:
        return _abort_plan(invalid_tag_reason)
    contradiction = _observation_contradiction(observation, target_tags, tag_prefix)
    if contradiction:
        return _abort_plan(contradiction)

    if not target_tags:
        return ReleasePlan(
            route=1,
            decision="PUBLISH",
            tag=tag_prefix,
            requires_push=True,
            remaining_actions=[
                "atomic_push",
                "create_release",
                "update_state_table",
                "close_tracking_issue",
            ],
            reason="No tag exists for the target kaji release.",
        )

    latest_revision, latest = max(target_tags, key=lambda item: item[0])
    if latest.sha == observation.candidate_sha:
        remaining = _remaining_bookkeeping(observation, latest.name)
        return ReleasePlan(
            route=2,
            decision="IDEMPOTENT" if not remaining else "RESUME",
            tag=latest.name,
            requires_push=False,
            remaining_actions=remaining,
            reason=(
                "All publication and bookkeeping actions are already complete."
                if not remaining
                else "The latest tag already points to the candidate; resume missing bookkeeping."
            ),
        )

    if any(tag.sha == observation.candidate_sha for _, tag in target_tags[:-1]):
        return ReleasePlan(
            route=4,
            decision="ABORT",
            tag=None,
            requires_push=False,
            remaining_actions=[],
            reason="Candidate SHA matches an older revision and must not be republished.",
        )

    return ReleasePlan(
        route=3,
        decision="PUBLISH",
        tag=f"{tag_prefix}-r{latest_revision + 1}",
        requires_push=True,
        remaining_actions=[
            "atomic_push",
            "create_release",
            "update_state_table",
            "close_tracking_issue",
        ],
        reason="The candidate differs from the latest published revision.",
    )


def _target_tags(
    tags: list[StarterTagObservation],
    tag_prefix: str,
) -> tuple[list[tuple[int, StarterTagObservation]], str]:
    """Parse target-version tag revisions and identify malformed observations."""
    pattern = re.compile(rf"^{re.escape(tag_prefix)}(?:-r(?P<revision>[1-9][0-9]*))?$")
    parsed: list[tuple[int, StarterTagObservation]] = []
    revisions: set[int] = set()
    for tag in tags:
        if not tag.name.startswith(tag_prefix):
            continue
        match = pattern.fullmatch(tag.name)
        if match is None:
            return [], f"Invalid target-version tag name: {tag.name}."
        if not tag.annotated:
            return [], f"Target-version tag is not annotated: {tag.name}."
        revision = int(match.group("revision") or 0)
        if revision in revisions:
            return [], f"Duplicate target-version revision observed: {revision}."
        revisions.add(revision)
        parsed.append((revision, tag))
    parsed.sort(key=lambda item: item[0])
    return parsed, ""


def _observation_contradiction(
    observation: ReleasePlanInput,
    target_tags: list[tuple[int, StarterTagObservation]],
    tag_prefix: str,
) -> str:
    """Return a fail-closed reason for contradictory bookkeeping observations."""
    if not observation.state_table_row_exists:
        return "The kaji Release state table has no row for the managed starter."
    target_tag_names = {tag.name for _, tag in target_tags}
    target_releases = {name for name in observation.releases if name.startswith(tag_prefix)}
    orphan_releases = target_releases - target_tag_names
    if orphan_releases:
        return f"GitHub Release exists without a matching tag: {sorted(orphan_releases)[0]}."
    if observation.state_table_status == "N/A":
        return "N/A bookkeeping must use the dedicated no-change path, not release-plan."
    if observation.tracking_issue_state == "closed" and observation.state_table_status != "PASS":
        return "Tracking Issue is closed before the state table reached PASS."
    if observation.state_table_status == "PASS" and not target_releases:
        return "State table reports PASS but no starter GitHub Release exists."
    return ""


def _remaining_bookkeeping(
    observation: ReleasePlanInput,
    tag_name: str,
) -> list[ReleaseAction]:
    """Return the fixed suffix of publication bookkeeping still required."""
    if tag_name not in observation.releases:
        return ["create_release", "update_state_table", "close_tracking_issue"]
    if observation.state_table_status != "PASS":
        return ["update_state_table", "close_tracking_issue"]
    if observation.tracking_issue_state != "closed":
        return ["close_tracking_issue"]
    return []


def _abort_plan(reason: str) -> ReleasePlan:
    """Build a route-5 fail-closed plan."""
    return ReleasePlan(
        route=5,
        decision="ABORT",
        tag=None,
        requires_push=False,
        remaining_actions=[],
        reason=reason,
    )
