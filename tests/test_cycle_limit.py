"""Tests for cycle iteration limit logic.

Validates that SessionState correctly tracks per-cycle iteration counts
and that the on_exhaust mechanism works when the limit is reached.
"""

from __future__ import annotations

import pytest

from dao_harness.models import CycleDefinition, Verdict
from dao_harness.state import SessionState

# ============================================================
# Helpers
# ============================================================


def _make_state() -> SessionState:
    """Create a minimal SessionState with persistence disabled."""
    state = SessionState(
        issue_number=1,
        sessions={},
        step_history=[],
        cycle_counts={},
        last_completed_step=None,
        last_transition_verdict=None,
    )
    state._persist = lambda: None  # type: ignore[assignment]
    return state


# ============================================================
# Tests
# ============================================================


class TestCycleLimit:
    """Unit tests for cycle iteration tracking and limit enforcement."""

    @pytest.mark.small
    def test_cycle_iterations_returns_zero_initially(self) -> None:
        """A fresh state reports 0 iterations for any cycle."""
        state = _make_state()

        assert state.cycle_iterations("impl-loop") == 0

    @pytest.mark.small
    def test_cycle_iterations_returns_one_after_increment(self) -> None:
        """After a single increment the count is 1."""
        state = _make_state()

        state.increment_cycle("impl-loop")

        assert state.cycle_iterations("impl-loop") == 1

    @pytest.mark.small
    def test_cycle_limit_reached_after_max_iterations(self) -> None:
        """After max_iterations increments, the cycle limit is reached."""
        state = _make_state()
        max_iterations = 3

        for _ in range(max_iterations):
            state.increment_cycle("impl-loop")

        assert state.cycle_iterations("impl-loop") >= max_iterations

    @pytest.mark.small
    def test_on_exhaust_verdict_can_be_constructed(self) -> None:
        """Verdict can be constructed with an on_exhaust status like ABORT."""
        cycle = CycleDefinition(
            name="impl-loop",
            entry="implement",
            loop=["implement", "review"],
            max_iterations=3,
            on_exhaust="ABORT",
        )

        verdict = Verdict(
            status=cycle.on_exhaust,
            reason="Cycle exhausted",
            evidence=f"Reached {cycle.max_iterations} iterations",
            suggestion="Review manually",
        )

        assert verdict.status == "ABORT"
        assert cycle.on_exhaust == "ABORT"

    @pytest.mark.small
    def test_cycle_counts_are_independent(self) -> None:
        """Different cycle names maintain independent counters."""
        state = _make_state()

        state.increment_cycle("loop-a")
        state.increment_cycle("loop-a")
        state.increment_cycle("loop-b")

        assert state.cycle_iterations("loop-a") == 2
        assert state.cycle_iterations("loop-b") == 1

    @pytest.mark.small
    def test_increment_cycle_only_affects_specified_cycle(self) -> None:
        """Incrementing one cycle does not change another cycle's count."""
        state = _make_state()

        state.increment_cycle("loop-a")

        assert state.cycle_iterations("loop-a") == 1
        assert state.cycle_iterations("loop-b") == 0
