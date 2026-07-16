"""Deterministic pytest baseline models and comparison logic."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kaji_harness.fsio import atomic_write

BASELINE_SCHEMA_VERSION: Literal[1] = 1
MASS_FAILURE_THRESHOLD = 10

BaselineStatus = Literal["clean", "known_failures", "blocked", "invalid"]
FailureKind = Literal["FAILED", "ERROR"]


class BaselineFailure(BaseModel):
    """One lossless pytest failure identity and human-readable summary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodeid: str = Field(min_length=1)
    kind: FailureKind
    error_type: str = Field(min_length=1)
    message_head: str = ""

    @property
    def key(self) -> str:
        """Return the stable regression comparison key."""
        return f"{self.nodeid} | {self.kind} | {self.error_type}"


class BaselineSummary(BaseModel):
    """Machine-readable pytest summary counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class BaselineArtifact(BaseModel):
    """Validated source-of-truth artifact for one pre-implementation baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    issue_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    measured_commit: str = Field(min_length=1)
    measured_at: datetime
    pytest_exit_code: int
    summary: BaselineSummary
    status: BaselineStatus
    stop_reason: str | None
    failures: list[BaselineFailure]

    @model_validator(mode="after")
    def validate_classification(self) -> BaselineArtifact:
        """Reject artifacts whose status contradicts their structured report."""
        classified_status, classified_reason = classify_baseline(
            self.pytest_exit_code,
            self.summary,
            self.failures,
        )
        if self.status == "invalid":
            if not self.stop_reason:
                raise ValueError("invalid baseline artifact requires stop_reason")
            return self
        if self.status != classified_status or self.stop_reason != classified_reason:
            raise ValueError("baseline status does not match exit code and failure report")
        return self


class FailureComparison(BaseModel):
    """Set comparison between baseline and current pytest failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["ok", "regression"]
    regressions: list[str]
    matched_baseline: list[str]
    resolved: list[str]


class ScopeEvaluation(BaseModel):
    """Overlap result for agent-selected implementation scope paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["ok", "stop"]
    stop: bool
    overlapping: list[str]
    baseline_status: BaselineStatus
    measured_commit: str


class PluginReport(BaseModel):
    """Validated JSON emitted by the internal pytest plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: BaselineSummary
    failures: list[BaselineFailure]


def classify_baseline(
    pytest_exit_code: int,
    summary: BaselineSummary,
    failures: list[BaselineFailure],
) -> tuple[BaselineStatus, str | None]:
    """Classify a validated pytest report with fail-closed exit handling."""
    if pytest_exit_code not in (0, 1):
        return "invalid", f"unexpected_exit_code:{pytest_exit_code}"

    failed_count = sum(failure.kind == "FAILED" for failure in failures)
    error_count = sum(failure.kind == "ERROR" for failure in failures)
    report_is_consistent = (
        summary.failed == failed_count
        and summary.errors == error_count
        and len(failures) == summary.failed + summary.errors
    )
    if not report_is_consistent:
        return "invalid", "inconsistent_report"

    failure_count = len(failures)
    if pytest_exit_code == 0:
        if failure_count != 0:
            return "invalid", "inconsistent_report"
        return "clean", None
    if failure_count == 0:
        return "invalid", "inconsistent_report"
    if failure_count > MASS_FAILURE_THRESHOLD:
        return "blocked", "mass_failures"
    return "known_failures", None


def compare_failures(
    baseline: list[BaselineFailure],
    current: list[BaselineFailure],
) -> FailureComparison:
    """Compare failures by the stable ``(nodeid, kind, error_type)`` key."""
    baseline_keys = {failure.key for failure in baseline}
    current_keys = {failure.key for failure in current}
    regressions = sorted(current_keys - baseline_keys)
    return FailureComparison(
        verdict="regression" if regressions else "ok",
        regressions=regressions,
        matched_baseline=sorted(current_keys & baseline_keys),
        resolved=sorted(baseline_keys - current_keys),
    )


def evaluate_scope(artifact: BaselineArtifact, scopes: list[str]) -> ScopeEvaluation:
    """Evaluate exact-file and directory-prefix overlap with baseline failures."""
    normalized_scopes = [PurePosixPath(scope).as_posix().rstrip("/") for scope in scopes]
    overlapping: list[str] = []
    for failure in artifact.failures:
        failure_path = PurePosixPath(failure.nodeid.split("::", 1)[0]).as_posix()
        if any(
            failure_path == scope or failure_path.startswith(scope + "/")
            for scope in normalized_scopes
        ):
            overlapping.append(failure.nodeid)
    overlapping = sorted(set(overlapping))
    return ScopeEvaluation(
        verdict="stop" if overlapping else "ok",
        stop=bool(overlapping),
        overlapping=overlapping,
        baseline_status=artifact.status,
        measured_commit=artifact.measured_commit,
    )


def save_artifact(path: Path, artifact: BaselineArtifact) -> None:
    """Atomically write one validated baseline artifact."""
    content = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, content)


def load_artifact(path: Path) -> BaselineArtifact:
    """Load and validate one baseline artifact."""
    return BaselineArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def load_plugin_report(path: Path) -> PluginReport:
    """Load and validate a lossless report emitted by the pytest plugin."""
    return PluginReport.model_validate_json(path.read_text(encoding="utf-8"))
