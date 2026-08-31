"""Deterministic planning for managed starter-sync tracking issues (Issue #423).

A managed starter's unfinished sync work is tracked in a single open GitHub
Issue per unfinished period (not one Issue per kaji Release). This module
parses/renders the Issue body (v1 schema) and provides the two pure decision
functions consumed by the ``kaji starter tracking-plan`` / ``kaji starter
task-plan`` CLI subcommands: ``build_tracking_plan`` decides whether a
release-time observation should create a new tracking issue, append to the
existing one, or stop for human disambiguation; ``build_task_plan`` decides
how a sync-time observation should continue, start, complete, or close the
current batch of covered releases.

Both decision functions never raise: a malformed body or a contradictory
observation is reported as a ``decision: ABORT`` plan (the ``build_release_plan``
convention in ``starter_release.py``), so the CLI layer can always emit a
JSON plan with exit 0.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import TrackingBodyError

TaskStatus = Literal["open", "syncing", "done"]
TrackingDecision = Literal["CREATE", "APPEND", "IDEMPOTENT", "ABORT"]
TaskDecision = Literal["SYNC", "COMPLETED", "CLOSABLE", "ABORT"]

_SCHEMA_MARKER = "<!-- kaji-starter-sync: v1 -->"
_SYNC_TASKS_HEADING = "## Sync tasks"
_TABLE_HEADER = ("target_kaji_release", "status", "batch", "result")

_STARTER_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_BATCH_ID_RE = re.compile(r"^b([1-9][0-9]*)$")
_RESULT_TAG_RE = re.compile(r"^kaji-v[0-9]+\.[0-9]+\.[0-9]+(-r[1-9][0-9]*)?$")
_VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")


# --- structured body representation -----------------------------------------


@dataclass(frozen=True)
class TrackingTaskRow:
    """One row of a tracking issue's ``## Sync tasks`` table.

    Attributes:
        target_kaji_release: The kaji Release this task follows (``vX.Y.Z``).
        status: Row lifecycle: ``open`` (not started) / ``syncing`` (part of
            the active batch) / ``done`` (published or coalesced into a
            sibling row's result).
        batch: The batch id (``bN``) grouping this row with the sibling
            releases processed together, or ``-`` for ``open`` rows.
        result: The starter tag published for this row, ``N/A`` when
            coalesced into a sibling release's publication, or ``-`` before
            the batch completes.
    """

    target_kaji_release: str
    status: TaskStatus
    batch: str
    result: str


@dataclass(frozen=True)
class TrackingIssueBody:
    """Structured state of a starter-sync tracking issue body (v1 schema).

    Attributes:
        starter_repo: The managed starter's ``owner/repo``.
        starter_path: Optional local checkout path used by starter-sync skills.
        tasks: ``## Sync tasks`` table rows, in source order.
    """

    starter_repo: str
    starter_path: str | None
    tasks: tuple[TrackingTaskRow, ...]


def parse_release_version(value: str) -> tuple[int, int, int]:
    """Parse a ``vX.Y.Z`` kaji release string into a sortable tuple.

    Args:
        value: A release string expected to match ``vX.Y.Z``.

    Returns:
        ``(major, minor, patch)`` as integers, ordering ``v0.9.0`` before
        ``v0.10.0`` (unlike a plain string comparison).

    Raises:
        ValueError: ``value`` does not match ``vX.Y.Z``.
    """
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid kaji release version: {value!r}.")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _extract_field(lines: Sequence[str], name: str) -> str | None:
    """Return the value of the first ``name: value`` line, or ``None``."""
    prefix = f"{name}:"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None


def _parse_row(line: str) -> list[str]:
    """Split one markdown table row into stripped cell values."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        raise TrackingBodyError(f"Malformed Sync tasks table row: {line!r}.")
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _parse_task_rows(lines: Sequence[str]) -> list[TrackingTaskRow]:
    """Parse and validate the ``## Sync tasks`` table from body lines."""
    heading_index = next(
        (i for i, line in enumerate(lines) if line.strip() == _SYNC_TASKS_HEADING), None
    )
    if heading_index is None:
        raise TrackingBodyError(
            f"Tracking issue body is missing the '{_SYNC_TASKS_HEADING}' section."
        )

    remaining = [line for line in lines[heading_index + 1 :] if line.strip()]
    table_lines = list(itertools.takewhile(lambda line: line.strip().startswith("|"), remaining))
    if len(table_lines) < 2:
        raise TrackingBodyError("Tracking issue body is missing the Sync tasks table.")

    header = _parse_row(table_lines[0])
    if tuple(header) != _TABLE_HEADER:
        raise TrackingBodyError(f"Unexpected Sync tasks table header: {header}.")
    separator = _parse_row(table_lines[1])
    if len(separator) != len(_TABLE_HEADER) or not all(
        re.fullmatch(r"-+", cell) for cell in separator
    ):
        raise TrackingBodyError(f"Malformed Sync tasks table separator row: {table_lines[1]!r}.")

    data_rows = table_lines[2:]
    if not data_rows:
        raise TrackingBodyError("Sync tasks table must have at least one row.")

    seen_targets: set[str] = set()
    tasks: list[TrackingTaskRow] = []
    for raw_row in data_rows:
        cells = _parse_row(raw_row)
        if len(cells) != len(_TABLE_HEADER):
            raise TrackingBodyError(f"Sync tasks row does not have 4 columns: {raw_row!r}.")
        target, raw_status, batch, result = cells

        try:
            parse_release_version(target)
        except ValueError as exc:
            raise TrackingBodyError(f"Invalid target_kaji_release format: {target!r}.") from exc
        if target in seen_targets:
            raise TrackingBodyError(f"Duplicate target_kaji_release in Sync tasks table: {target}.")
        seen_targets.add(target)

        if raw_status not in ("open", "syncing", "done"):
            raise TrackingBodyError(f"Unknown status for {target}: {raw_status!r}.")
        status = cast(TaskStatus, raw_status)

        if status == "open":
            if batch != "-":
                raise TrackingBodyError(
                    f"'open' row for {target} must have batch '-', got {batch!r}."
                )
        elif not _BATCH_ID_RE.fullmatch(batch):
            raise TrackingBodyError(
                f"'{status}' row for {target} must have a 'bN' batch id, got {batch!r}."
            )

        if status in ("open", "syncing"):
            if result != "-":
                raise TrackingBodyError(
                    f"'{status}' row for {target} must have result '-', got {result!r}."
                )
        elif result == "-":
            raise TrackingBodyError(f"'done' row for {target} must not have result '-'.")
        elif result != "N/A" and not _RESULT_TAG_RE.fullmatch(result):
            raise TrackingBodyError(f"Invalid result for {target}: {result!r}.")

        tasks.append(
            TrackingTaskRow(target_kaji_release=target, status=status, batch=batch, result=result)
        )
    return tasks


def parse_tracking_issue_body(text: str) -> TrackingIssueBody:
    """Parse a tracking issue body into structured starter-sync state.

    Args:
        text: Raw GitHub Issue body text.

    Returns:
        Structured ``starter_repo`` / ``starter_path`` / task rows.

    Raises:
        TrackingBodyError: The body violates the v1 schema (missing schema
            marker, malformed fields, or an inconsistent Sync tasks table).
            A missing marker also rejects legacy single-target bodies
            (BREAKING; no compatibility read path per ADR 008).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _SCHEMA_MARKER:
        raise TrackingBodyError(
            f"Tracking issue body is missing the '{_SCHEMA_MARKER}' schema marker "
            "as its first line (legacy single-target bodies are not supported; "
            "rewrite using the v1 schema)."
        )

    starter_repo = _extract_field(lines, "starter_repo")
    if starter_repo is None:
        raise TrackingBodyError("Tracking issue body is missing the required 'starter_repo' field.")
    if not _STARTER_REPO_RE.fullmatch(starter_repo):
        raise TrackingBodyError(f"Invalid starter_repo format: {starter_repo!r}.")
    starter_path = _extract_field(lines, "starter_path")

    tasks = tuple(_parse_task_rows(lines))
    return TrackingIssueBody(starter_repo=starter_repo, starter_path=starter_path, tasks=tasks)


def render_tracking_issue_body(body: TrackingIssueBody) -> str:
    """Render a tracking issue body deterministically from structured state.

    Args:
        body: Structured ``starter_repo`` / ``starter_path`` / task rows.

    Returns:
        Full markdown body text with tasks sorted by ascending kaji release
        version, matching the v1 schema ``parse_tracking_issue_body`` accepts.
    """
    lines = [_SCHEMA_MARKER, f"starter_repo: {body.starter_repo}"]
    if body.starter_path is not None:
        lines.append(f"starter_path: {body.starter_path}")
    lines += [
        "",
        _SYNC_TASKS_HEADING,
        "",
        f"| {' | '.join(_TABLE_HEADER)} |",
        "|---|---|---|---|",
    ]
    for task in sorted(body.tasks, key=lambda t: parse_release_version(t.target_kaji_release)):
        lines.append(
            f"| {task.target_kaji_release} | {task.status} | {task.batch} | {task.result} |"
        )
    return "\n".join(lines) + "\n"


def _next_batch_id(tasks: Sequence[TrackingTaskRow]) -> str:
    """Return the next unused batch id (never reuses a historical batch id)."""
    used = [int(m.group(1)) for t in tasks if (m := _BATCH_ID_RE.fullmatch(t.batch))]
    return f"b{(max(used) + 1) if used else 1}"


# --- tracking-plan (release time) -------------------------------------------


class TrackingIssueObservation(BaseModel):
    """One open tracking Issue as observed by the caller."""

    model_config = ConfigDict(extra="forbid")

    issue_id: int
    body: str


class TrackingPlanInput(BaseModel):
    """Validated release-time observation used to select a tracking-plan route."""

    model_config = ConfigDict(extra="forbid")

    starter_repo: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    new_target: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    open_tracking_issues: list[TrackingIssueObservation]
    selected_issue_id: int | None = None


class TrackingPlan(BaseModel):
    """Machine-readable tracking-issue decision returned by the CLI."""

    model_config = ConfigDict(extra="forbid")

    route: Literal[1, 2, 3, 4, 5]
    decision: TrackingDecision
    issue_id: int | None
    next_body: str | None
    required_labels: list[str]
    reason: str


def _tracking_abort(reason: str) -> TrackingPlan:
    """Build a route-5 fail-closed tracking plan."""
    return TrackingPlan(
        route=5,
        decision="ABORT",
        issue_id=None,
        next_body=None,
        required_labels=[],
        reason=reason,
    )


def build_tracking_plan(observation: TrackingPlanInput) -> TrackingPlan:
    """Build one deterministic tracking-issue plan from a release-time observation.

    Args:
        observation: The starter, the new kaji Release, every currently open
            ``starter-sync``-labeled Issue (regardless of starter), and an
            optional human-selected Issue id.

    Returns:
        A numbered route (1-5) matching the starter-sync tracking runbook
        decision table. Never raises: unparseable candidates or contradictory
        selections are reported as ``decision: ABORT``.
    """
    candidates: list[TrackingIssueObservation] = []
    for issue in observation.open_tracking_issues:
        # ``starter-sync`` is the sole discovery key (may span multiple starters), so a
        # labeled Issue whose body cannot be parsed (missing schema marker, missing/malformed
        # ``starter_repo``, malformed table) is never silently skipped as "a different
        # starter": its identity is unknown, and ignoring it risks creating a duplicate
        # tracking Issue for this starter. Fail loud instead (ADR 008; Issue #423 review).
        try:
            body = parse_tracking_issue_body(issue.body)
        except TrackingBodyError as exc:
            return _tracking_abort(f"Tracking Issue #{issue.issue_id} body failed to parse: {exc}")
        if body.starter_repo != observation.starter_repo:
            continue  # belongs to a different starter
        candidates.append(issue)

    if observation.selected_issue_id is not None:
        selected = next(
            (c for c in candidates if c.issue_id == observation.selected_issue_id), None
        )
        if selected is None:
            return _tracking_abort(
                f"selected_issue_id {observation.selected_issue_id} is not among the open "
                f"tracking issues for {observation.starter_repo}: "
                f"{sorted(c.issue_id for c in candidates)}."
            )
        target_issue = selected
    elif not candidates:
        empty_body = TrackingIssueBody(
            starter_repo=observation.starter_repo,
            starter_path=None,
            tasks=(
                TrackingTaskRow(
                    target_kaji_release=observation.new_target, status="open", batch="-", result="-"
                ),
            ),
        )
        return TrackingPlan(
            route=1,
            decision="CREATE",
            issue_id=None,
            next_body=render_tracking_issue_body(empty_body),
            required_labels=["starter-sync"],
            reason=f"No open tracking issue exists for {observation.starter_repo}.",
        )
    elif len(candidates) == 1:
        target_issue = candidates[0]
    else:
        return TrackingPlan(
            route=4,
            decision="ABORT",
            issue_id=None,
            next_body=None,
            required_labels=[],
            reason=(
                f"Multiple open tracking issues found for {observation.starter_repo}: "
                f"{sorted(c.issue_id for c in candidates)}. Specify selected_issue_id explicitly."
            ),
        )

    body = parse_tracking_issue_body(target_issue.body)
    if body.starter_repo != observation.starter_repo:
        return _tracking_abort(
            f"Tracking Issue #{target_issue.issue_id} starter_repo {body.starter_repo!r} "
            f"does not match observed {observation.starter_repo!r}."
        )

    if any(task.target_kaji_release == observation.new_target for task in body.tasks):
        return TrackingPlan(
            route=3,
            decision="IDEMPOTENT",
            issue_id=target_issue.issue_id,
            next_body=None,
            required_labels=[],
            reason=(
                f"{observation.new_target} is already listed in tracking issue "
                f"#{target_issue.issue_id}."
            ),
        )

    appended = TrackingIssueBody(
        starter_repo=body.starter_repo,
        starter_path=body.starter_path,
        tasks=(
            *body.tasks,
            TrackingTaskRow(
                target_kaji_release=observation.new_target, status="open", batch="-", result="-"
            ),
        ),
    )
    return TrackingPlan(
        route=2,
        decision="APPEND",
        issue_id=target_issue.issue_id,
        next_body=render_tracking_issue_body(appended),
        required_labels=[],
        reason=f"Appended {observation.new_target} to open tracking issue #{target_issue.issue_id}.",
    )


# --- task-plan (sync time) ---------------------------------------------------


class TaskCompletion(BaseModel):
    """Publish-success bookkeeping applied to a completed batch."""

    model_config = ConfigDict(extra="forbid")

    batch: str = Field(pattern=r"^b[1-9][0-9]*$")
    completed_targets: list[str]
    published_tag: str | None = Field(
        default=None, pattern=r"^kaji-v[0-9]+\.[0-9]+\.[0-9]+(-r[1-9][0-9]*)?$"
    )

    @field_validator("completed_targets")
    @classmethod
    def _validate_completed_targets(cls, value: list[str]) -> list[str]:
        """Enforce the design's "ascending vX.Y.Z set" contract (no duplicates, no gaps in order)."""
        if not value:
            raise ValueError("completed_targets must not be empty.")
        try:
            parsed = [parse_release_version(target) for target in value]
        except ValueError as exc:
            raise ValueError(f"completed_targets contains an invalid vX.Y.Z entry: {exc}") from exc
        if len(set(value)) != len(value):
            raise ValueError(f"completed_targets must not contain duplicates, got {value!r}.")
        if parsed != sorted(parsed):
            raise ValueError(f"completed_targets must be in ascending order, got {value!r}.")
        return value

    @model_validator(mode="after")
    def _validate_published_tag_matches_active_target(self) -> TaskCompletion:
        """A starter tag names the kaji Release it was published for (runbook: initial tag
        is ``kaji-vX.Y.Z``, revisions append ``-rN``); it must correspond to the batch's
        active target (the max of ``completed_targets``), never an unrelated version.
        """
        if self.published_tag is None:
            return self
        active_target = self.completed_targets[-1]  # already validated ascending
        expected_prefix = f"kaji-{active_target}"
        if self.published_tag != expected_prefix and not self.published_tag.startswith(
            f"{expected_prefix}-r"
        ):
            raise ValueError(
                f"published_tag {self.published_tag!r} does not correspond to active target "
                f"{active_target!r} (expected {expected_prefix!r} or a {expected_prefix!r}-rN "
                "revision)."
            )
        return self


class TaskPlanInput(BaseModel):
    """Validated sync-time observation used to select a task-plan route."""

    model_config = ConfigDict(extra="forbid")

    issue_id: int
    issue_state: Literal["open", "closed"]
    body: str
    completion: TaskCompletion | None = None


class StateTableUpdate(BaseModel):
    """One kaji Release state-table row update generated after publication."""

    model_config = ConfigDict(extra="forbid")

    kaji_release: str
    status: Literal["PASS", "N/A"]
    starter_release: str | None
    reason: str | None
    tracking_issue: int


class TaskPlan(BaseModel):
    """Machine-readable sync decision returned by the CLI."""

    model_config = ConfigDict(extra="forbid")

    route: Literal[1, 2, 3, 4, 5, 6, 7]
    decision: TaskDecision
    batch: str | None
    active_target: str | None
    covered_targets: list[str]
    coalesced: bool
    remaining_targets: list[str]
    close_allowed: bool
    state_table_updates: list[StateTableUpdate]
    next_body: str | None
    reason: str


def _task_abort(route: Literal[6, 7], reason: str) -> TaskPlan:
    """Build a fail-closed task plan for route 6 (set mismatch) or 7 (contradiction)."""
    return TaskPlan(
        route=route,
        decision="ABORT",
        batch=None,
        active_target=None,
        covered_targets=[],
        coalesced=False,
        remaining_targets=[],
        close_allowed=False,
        state_table_updates=[],
        next_body=None,
        reason=reason,
    )


def _compute_batch_results(targets: list[str], published_tag: str | None) -> dict[str, str]:
    """Derive the ``result`` value each target should carry once a batch completes."""
    if published_tag is None:
        return {target: "N/A" for target in targets}
    active = max(targets, key=parse_release_version)
    return {target: (published_tag if target == active else "N/A") for target in targets}


def _state_table_updates(
    batch_id: str, result_by_target: dict[str, str], issue_id: int
) -> list[StateTableUpdate]:
    """Build state-table row updates from a batch's final (post-completion) results."""
    tag_results = [result for result in result_by_target.values() if result != "N/A"]
    published_tag = tag_results[0] if tag_results else None
    updates: list[StateTableUpdate] = []
    for target in sorted(result_by_target, key=parse_release_version):
        result = result_by_target[target]
        if result != "N/A":
            updates.append(
                StateTableUpdate(
                    kaji_release=target,
                    status="PASS",
                    starter_release=result,
                    reason=None,
                    tracking_issue=issue_id,
                )
            )
        else:
            reason = (
                f"{published_tag} の snapshot に統合。個別 snapshot なし"
                if published_tag is not None
                else "変更なし。starter Release を作らない"
            )
            updates.append(
                StateTableUpdate(
                    kaji_release=target,
                    status="N/A",
                    starter_release=None,
                    reason=reason,
                    tracking_issue=issue_id,
                )
            )
    return updates


def _replace_batch_rows_with_done(
    body: TrackingIssueBody, batch_id: str, result_by_target: dict[str, str]
) -> TrackingIssueBody:
    """Return a body with the given batch's syncing rows transitioned to done."""
    updated_tasks = tuple(
        TrackingTaskRow(
            target_kaji_release=task.target_kaji_release,
            status="done",
            batch=task.batch,
            result=result_by_target[task.target_kaji_release],
        )
        if task.status == "syncing" and task.batch == batch_id
        else task
        for task in body.tasks
    )
    return TrackingIssueBody(
        starter_repo=body.starter_repo, starter_path=body.starter_path, tasks=updated_tasks
    )


def build_task_plan(observation: TaskPlanInput) -> TaskPlan:
    """Build one deterministic sync-task plan from a sync-time observation.

    Args:
        observation: The tracking issue id/state/body, and (only for
            publish-success bookkeeping) the completion to apply.

    Returns:
        A numbered route (1-7) matching the starter-sync task-plan decision
        table. Never raises: an unparseable body or a contradictory
        completion is reported as ``decision: ABORT``.
    """
    try:
        body = parse_tracking_issue_body(observation.body)
    except TrackingBodyError as exc:
        return _task_abort(7, f"Tracking issue #{observation.issue_id} body failed to parse: {exc}")

    syncing_batches = sorted({task.batch for task in body.tasks if task.status == "syncing"})
    if len(syncing_batches) > 1:
        return _task_abort(
            7,
            f"Tracking issue #{observation.issue_id} has multiple syncing batches: "
            f"{syncing_batches}.",
        )

    open_targets = sorted(
        (task.target_kaji_release for task in body.tasks if task.status == "open"),
        key=parse_release_version,
    )
    if observation.issue_state == "closed" and (open_targets or syncing_batches):
        return _task_abort(
            7,
            f"Tracking issue #{observation.issue_id} is closed but has unresolved "
            f"open={open_targets} syncing_batch={syncing_batches or None}.",
        )

    # A batch id is one sync attempt; every row sharing it must be in the same lifecycle
    # phase (all 'syncing' or all 'done'). A batch split across two statuses cannot happen
    # under the normal SYNC -> COMPLETED transition (both are rewritten together), so a
    # mixed observation is a contradiction and must not be allowed to continue as if the
    # batch were still syncing or already closable.
    statuses_by_batch: dict[str, set[TaskStatus]] = {}
    for task in body.tasks:
        if task.batch != "-":
            statuses_by_batch.setdefault(task.batch, set()).add(task.status)
    for batch_id, statuses in statuses_by_batch.items():
        if len(statuses) > 1:
            return _task_abort(
                7,
                f"Tracking issue #{observation.issue_id} batch {batch_id} mixes statuses "
                f"{sorted(statuses)}; a batch must be entirely 'syncing' or entirely 'done'.",
            )

    done_batches: dict[str, list[TrackingTaskRow]] = {}
    for task in body.tasks:
        if task.status == "done":
            done_batches.setdefault(task.batch, []).append(task)
    for batch_id, rows in done_batches.items():
        tag_rows = [row for row in rows if row.result != "N/A"]
        if len(tag_rows) > 1:
            return _task_abort(
                7,
                f"Tracking issue #{observation.issue_id} done batch {batch_id} has "
                f"{len(tag_rows)} starter tag results; expected at most 1.",
            )
        if tag_rows:
            expected_active = max(
                (row.target_kaji_release for row in rows), key=parse_release_version
            )
            if tag_rows[0].target_kaji_release != expected_active:
                return _task_abort(
                    7,
                    f"Tracking issue #{observation.issue_id} done batch {batch_id} carries "
                    f"its starter tag on {tag_rows[0].target_kaji_release}, but the batch's "
                    f"max target is {expected_active}.",
                )

    if observation.completion is None:
        return _build_sync_or_closable_plan(body, syncing_batches, open_targets)
    return _build_completion_plan(
        body, observation.completion, observation.issue_id, open_targets, syncing_batches
    )


def _build_sync_or_closable_plan(
    body: TrackingIssueBody, syncing_batches: list[str], open_targets: list[str]
) -> TaskPlan:
    """Route 1-3: no completion supplied yet (progress phase)."""
    if syncing_batches:
        batch_id = syncing_batches[0]
        covered = sorted(
            (task.target_kaji_release for task in body.tasks if task.batch == batch_id),
            key=parse_release_version,
        )
        return TaskPlan(
            route=1,
            decision="SYNC",
            batch=batch_id,
            active_target=covered[-1],
            covered_targets=covered,
            coalesced=len(covered) > 1,
            remaining_targets=open_targets,
            close_allowed=False,
            state_table_updates=[],
            next_body=None,
            reason=f"Batch {batch_id} is already syncing; continuing without changing the body.",
        )

    if open_targets:
        next_batch = _next_batch_id(body.tasks)
        updated_tasks = tuple(
            TrackingTaskRow(
                target_kaji_release=task.target_kaji_release,
                status="syncing",
                batch=next_batch,
                result="-",
            )
            if task.status == "open"
            else task
            for task in body.tasks
        )
        next_body = render_tracking_issue_body(
            TrackingIssueBody(
                starter_repo=body.starter_repo, starter_path=body.starter_path, tasks=updated_tasks
            )
        )
        return TaskPlan(
            route=2,
            decision="SYNC",
            batch=next_batch,
            active_target=open_targets[-1],
            covered_targets=open_targets,
            coalesced=len(open_targets) > 1,
            remaining_targets=[],
            close_allowed=False,
            state_table_updates=[],
            next_body=next_body,
            reason=f"Started batch {next_batch} covering {open_targets}.",
        )

    return TaskPlan(
        route=3,
        decision="CLOSABLE",
        batch=None,
        active_target=None,
        covered_targets=[],
        coalesced=False,
        remaining_targets=[],
        close_allowed=True,
        state_table_updates=[],
        next_body=None,
        reason="All tasks are done; the tracking issue may be closed.",
    )


def _build_completion_plan(
    body: TrackingIssueBody,
    completion: TaskCompletion,
    issue_id: int,
    open_targets: list[str],
    syncing_batches: list[str],
) -> TaskPlan:
    """Route 4-6: a completion was supplied (publish-success bookkeeping)."""
    completed_set = set(completion.completed_targets)

    matching_syncing = [
        task for task in body.tasks if task.status == "syncing" and task.batch == completion.batch
    ]
    if matching_syncing:
        batch_targets = {task.target_kaji_release for task in matching_syncing}
        if batch_targets != completed_set:
            return _task_abort(
                6,
                f"completion.completed_targets {sorted(completed_set)} does not match "
                f"syncing batch {completion.batch} targets {sorted(batch_targets)}.",
            )
        result_by_target = _compute_batch_results(
            completion.completed_targets, completion.published_tag
        )
        next_body_state = _replace_batch_rows_with_done(body, completion.batch, result_by_target)
        covered = sorted(completion.completed_targets, key=parse_release_version)
        remaining = open_targets
        return TaskPlan(
            route=4,
            decision="COMPLETED",
            batch=completion.batch,
            active_target=covered[-1],
            covered_targets=covered,
            coalesced=len(covered) > 1,
            remaining_targets=remaining,
            close_allowed=not remaining
            and all(task.status != "syncing" for task in next_body_state.tasks),
            state_table_updates=_state_table_updates(completion.batch, result_by_target, issue_id),
            next_body=render_tracking_issue_body(next_body_state),
            reason=f"Completed batch {completion.batch} covering {covered}.",
        )

    done_rows = None
    for batch_id, rows in _group_done_batches(body).items():
        if batch_id == completion.batch:
            done_rows = rows
            break
    if done_rows is None:
        return _task_abort(
            6,
            f"completion.batch {completion.batch!r} matches neither the current syncing "
            f"batch ({syncing_batches or None}) nor a done batch in tracking issue #{issue_id}.",
        )

    done_targets = {row.target_kaji_release for row in done_rows}
    if done_targets != completed_set:
        return _task_abort(
            6,
            f"completion.completed_targets {sorted(completed_set)} does not match done "
            f"batch {completion.batch} targets {sorted(done_targets)}.",
        )

    actual_results = {row.target_kaji_release: row.result for row in done_rows}
    expected_results = _compute_batch_results(
        completion.completed_targets, completion.published_tag
    )
    if actual_results != expected_results:
        return _task_abort(
            6,
            f"done batch {completion.batch} results {actual_results} do not match the "
            f"recomputed values {expected_results} for published_tag="
            f"{completion.published_tag!r}.",
        )

    covered = sorted(completed_set, key=parse_release_version)
    return TaskPlan(
        route=5,
        decision="COMPLETED",
        batch=completion.batch,
        active_target=covered[-1],
        covered_targets=covered,
        coalesced=len(covered) > 1,
        remaining_targets=open_targets,
        close_allowed=not open_targets and not syncing_batches,
        state_table_updates=_state_table_updates(completion.batch, actual_results, issue_id),
        next_body=None,
        reason=f"Batch {completion.batch} was already completed; replaying bookkeeping idempotently.",
    )


def _group_done_batches(body: TrackingIssueBody) -> dict[str, list[TrackingTaskRow]]:
    """Group ``done`` rows by batch id."""
    grouped: dict[str, list[TrackingTaskRow]] = {}
    for task in body.tasks:
        if task.status == "done":
            grouped.setdefault(task.batch, []).append(task)
    return grouped
