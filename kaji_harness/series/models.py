"""Pydantic models and pure decisions for sequential series execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SERIES_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SeriesMember(BaseModel):
    """One ordered issue/workflow pair in a series."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issue: int = Field(gt=0)
    workflow: str = Field(min_length=1)

    @field_validator("issue", mode="before")
    @classmethod
    def reject_boolean_issue(cls, value: object) -> object:
        """Reject booleans even though ``bool`` is an ``int`` subclass."""
        if isinstance(value, bool):
            raise ValueError("issue must be a positive integer")
        return value

    @field_validator("workflow")
    @classmethod
    def validate_workflow_path(cls, value: str) -> str:
        """Require a normalized repo-relative workflow path."""
        if value.strip() != value or value.startswith("/"):
            raise ValueError("workflow must be a non-empty repo-relative path")
        return value


class SeriesConfig(BaseModel):
    """Validated schema for a sequential series YAML file."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    parent_issue: int | None = Field(default=None, gt=0)
    strategy: Literal["sequential"]
    members: list[SeriesMember] = Field(min_length=1)
    on_failure: Literal["stop"]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Require a filesystem-safe stable series identifier."""
        if not _SERIES_ID_PATTERN.fullmatch(value):
            raise ValueError("id must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return value

    @field_validator("parent_issue", mode="before")
    @classmethod
    def reject_boolean_parent(cls, value: object) -> object:
        """Reject booleans as parent Issue identifiers."""
        if isinstance(value, bool):
            raise ValueError("parent_issue must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_unique_issues(self) -> SeriesConfig:
        """Reject duplicate Issue identifiers without changing member order."""
        issue_ids = [member.issue for member in self.members]
        duplicates = sorted({issue for issue in issue_ids if issue_ids.count(issue) > 1})
        if duplicates:
            raise ValueError(f"duplicate member issue(s): {duplicates}")
        return self


@dataclass(frozen=True)
class GateResult:
    """Pure result of evaluating a member subprocess and Issue state."""

    success: bool
    gate: str


def evaluate_member_gate(exit_code: int, state: str, state_reason: str) -> GateResult:
    """Allow only a zero child exit followed by a closed/completed Issue."""
    if exit_code != 0:
        return GateResult(success=False, gate=f"exit:{exit_code}")
    normalized_state = state.lower()
    normalized_reason = state_reason.lower()
    if normalized_state == "closed" and normalized_reason == "completed":
        return GateResult(success=True, gate="closed_completed")
    return GateResult(
        success=False,
        gate=f"mismatch:{normalized_state}/{normalized_reason}",
    )


def series_fingerprint(config: SeriesConfig) -> str:
    """Return a SHA-256 fingerprint of the normalized execution contract."""
    payload = config.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
