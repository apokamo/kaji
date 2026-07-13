"""Public API for sequential series execution."""

from __future__ import annotations

from .generator import generate_series_yaml
from .loader import load_series
from .lock import SeriesLock
from .models import (
    GateResult,
    SeriesConfig,
    SeriesMember,
    evaluate_member_gate,
    series_fingerprint,
)
from .runner import SeriesRunner
from .state import MemberState, SeriesState

__all__ = [
    "GateResult",
    "SeriesConfig",
    "SeriesLock",
    "SeriesMember",
    "SeriesRunner",
    "SeriesState",
    "MemberState",
    "evaluate_member_gate",
    "generate_series_yaml",
    "load_series",
    "series_fingerprint",
]
