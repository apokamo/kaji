"""Small tests for ``AttemptDeadline`` (Issue #421)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from kaji_harness.deadline import AttemptDeadline

_NOW = datetime(2026, 10, 3, 4, 27, 28, 250000, tzinfo=UTC)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return _NOW


@pytest.mark.small
class TestAttemptDeadline:
    def test_start_captures_both_clocks_once(self) -> None:
        with (
            patch("kaji_harness.deadline.time.monotonic", return_value=1234.5),
            patch("kaji_harness.deadline.datetime", _FixedDatetime),
        ):
            deadline = AttemptDeadline.start(6000)

        assert deadline.timeout_seconds == 6000
        assert deadline.started_at == _NOW
        assert deadline.started_at.tzinfo is not None
        assert deadline.started_at.utcoffset() == timedelta(0)
        assert deadline.started_monotonic == 1234.5

    def test_deadlines_are_start_plus_timeout(self) -> None:
        deadline = AttemptDeadline(timeout_seconds=6000, started_at=_NOW, started_monotonic=1000.0)

        assert deadline.deadline_monotonic == 7000.0
        assert deadline.deadline_at == _NOW + timedelta(seconds=6000)

    @pytest.mark.parametrize(
        ("now", "expected"),
        [(1000.0, 60.0), (1030.5, 29.5), (1060.0, 0.0), (5000.0, 0.0)],
    )
    def test_remaining_never_negative(self, now: float, expected: float) -> None:
        deadline = AttemptDeadline(timeout_seconds=60, started_at=_NOW, started_monotonic=1000.0)

        with patch("kaji_harness.deadline.time.monotonic", return_value=now):
            assert deadline.remaining() == expected

    def test_is_frozen(self) -> None:
        deadline = AttemptDeadline(timeout_seconds=1, started_at=_NOW, started_monotonic=0.0)

        with pytest.raises(AttributeError):
            deadline.timeout_seconds = 2  # type: ignore[misc]
