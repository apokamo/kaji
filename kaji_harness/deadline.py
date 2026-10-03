"""Attempt deadline for kaji_harness (Issue #421).

An agent step attempt has a single hard deadline. It is computed once, at attempt
start, and handed to both the prompt (so the agent can see its remaining time) and the
runner backends (which enforce it), so the two can never drift apart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class AttemptDeadline:
    """Hard deadline of one agent step attempt.

    Attributes:
        timeout_seconds: Resolved step timeout (step -> workflow -> config).
        started_at: UTC wall clock at attempt start (tz-aware). Used for display only.
        started_monotonic: ``time.monotonic()`` taken at the same moment. The hard
            deadline is judged against this clock.
    """

    timeout_seconds: int
    started_at: datetime
    started_monotonic: float

    @classmethod
    def start(cls, timeout_seconds: int) -> AttemptDeadline:
        """Start an attempt now. This is the only way to create an instance."""
        return cls(
            timeout_seconds=timeout_seconds,
            started_at=datetime.now(UTC),
            started_monotonic=time.monotonic(),
        )

    @property
    def deadline_monotonic(self) -> float:
        """Hard deadline on the ``time.monotonic()`` clock."""
        return self.started_monotonic + self.timeout_seconds

    @property
    def deadline_at(self) -> datetime:
        """Hard deadline as a UTC wall clock time (display value)."""
        return self.started_at + timedelta(seconds=self.timeout_seconds)

    def remaining(self) -> float:
        """Seconds until the hard deadline, never negative."""
        return max(0.0, self.deadline_monotonic - time.monotonic())
