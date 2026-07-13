"""Validated persistent state for sequential series runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ..errors import SeriesValidationError
from ..fsio import atomic_write
from .models import SeriesConfig, series_fingerprint


def _now_iso() -> str:
    """Return an aware UTC timestamp for persisted state."""
    return datetime.now(UTC).isoformat()


class MemberState(BaseModel):
    """Persistent execution state for one series member."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True)

    issue: int
    workflow: str
    status: Literal["pending", "running", "interrupted", "completed", "failed"] = "pending"
    child_pid: int | None = None
    run_id: str | None = None
    exit_code: int | None = None
    gate: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class SeriesState(BaseModel):
    """Persistent state for a complete sequential series."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True)

    series_id: str
    fingerprint: str
    status: Literal["running", "stopped", "completed"] = "running"
    stop_reason: str | None = None
    updated_at: str
    members: list[MemberState]

    @classmethod
    def create(cls, config: SeriesConfig) -> SeriesState:
        """Create initial pending state from a validated series."""
        return cls(
            series_id=config.id,
            fingerprint=series_fingerprint(config),
            updated_at=_now_iso(),
            members=[
                MemberState(issue=member.issue, workflow=member.workflow)
                for member in config.members
            ],
        )

    @classmethod
    def load(cls, path: Path) -> SeriesState:
        """Load and validate a state JSON file.

        Raises:
            SeriesValidationError: File, JSON, or schema validation fails.
        """
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise SeriesValidationError(f"invalid series state {path}: {exc}") from exc

    def save(self, path: Path) -> None:
        """Atomically persist the current state as deterministic JSON."""
        self.updated_at = _now_iso()
        content = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        atomic_write(path, content + "\n")
