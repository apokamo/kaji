"""Tests for session state management.

Covers SessionState: load_or_create, save/get session IDs,
cycle iteration tracking, and step recording.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dao_harness.models import Verdict
from dao_harness.state import SessionState, StepRecord

# ============================================================
# Helper: create a fresh SessionState without filesystem side effects
# ============================================================


def _make_state(issue: int = 42) -> SessionState:
    """Create a new SessionState with _persist mocked out."""
    with patch.object(SessionState, "_persist"):
        state = SessionState(
            issue_number=issue,
            sessions={},
            step_history=[],
            cycle_counts={},
            last_completed_step=None,
            last_transition_verdict=None,
        )
    return state


# ============================================================
# 1. load_or_create with non-existent path → new empty state
# ============================================================


@pytest.mark.small
class TestLoadOrCreateNew:
    """load_or_create returns a fresh empty state when no file exists."""

    def test_load_or_create_returns_new_state(self, tmp_path: Path) -> None:
        with patch("dao_harness.state.STATE_DIR", tmp_path / "artifacts"):
            state = SessionState.load_or_create(issue=99)

        assert state.issue_number == 99
        assert state.sessions == {}
        assert state.step_history == []
        assert state.cycle_counts == {}
        assert state.last_completed_step is None
        assert state.last_transition_verdict is None


# ============================================================
# 2. save_session_id stores session correctly
# ============================================================


@pytest.mark.small
class TestSaveSessionId:
    """save_session_id stores a mapping from step_id to session_id."""

    def test_save_session_id_stores_value(self) -> None:
        state = _make_state()

        with patch.object(state, "_persist"):
            state.save_session_id("design", "sess-abc")

        assert state.sessions["design"] == "sess-abc"


# ============================================================
# 3. get_session_id returns stored session
# ============================================================


@pytest.mark.small
class TestGetSessionIdFound:
    """get_session_id returns the session_id for a known resume target."""

    def test_get_session_id_returns_stored(self) -> None:
        state = _make_state()
        state.sessions["design"] = "sess-xyz"

        result = state.get_session_id("design")

        assert result == "sess-xyz"


# ============================================================
# 4. get_session_id with None target → None
# ============================================================


@pytest.mark.small
class TestGetSessionIdNoneTarget:
    """get_session_id with None target returns None immediately."""

    def test_get_session_id_none_target(self) -> None:
        state = _make_state()

        result = state.get_session_id(None)

        assert result is None


# ============================================================
# 5. get_session_id unknown step → None
# ============================================================


@pytest.mark.small
class TestGetSessionIdUnknown:
    """get_session_id returns None for an unknown step_id."""

    def test_get_session_id_unknown_step(self) -> None:
        state = _make_state()

        result = state.get_session_id("nonexistent")

        assert result is None


# ============================================================
# 6. cycle_iterations returns 0 for unknown cycle
# ============================================================


@pytest.mark.small
class TestCycleIterationsUnknown:
    """cycle_iterations returns 0 when the cycle has never been incremented."""

    def test_cycle_iterations_unknown_cycle(self) -> None:
        state = _make_state()

        assert state.cycle_iterations("impl-loop") == 0


# ============================================================
# 7. increment_cycle from 0 to 1
# ============================================================


@pytest.mark.small
class TestIncrementCycleFromZero:
    """increment_cycle sets count to 1 for a fresh cycle."""

    def test_increment_cycle_zero_to_one(self) -> None:
        state = _make_state()

        with patch.object(state, "_persist"):
            state.increment_cycle("impl-loop")

        assert state.cycle_iterations("impl-loop") == 1


# ============================================================
# 8. increment_cycle from 1 to 2
# ============================================================


@pytest.mark.small
class TestIncrementCycleFromOne:
    """increment_cycle increments from 1 to 2."""

    def test_increment_cycle_one_to_two(self) -> None:
        state = _make_state()
        state.cycle_counts["impl-loop"] = 1

        with patch.object(state, "_persist"):
            state.increment_cycle("impl-loop")

        assert state.cycle_iterations("impl-loop") == 2


# ============================================================
# 9. record_step adds to step_history
# ============================================================


@pytest.mark.small
class TestRecordStepAddsHistory:
    """record_step appends a StepRecord to step_history."""

    def test_record_step_appends(self) -> None:
        state = _make_state()
        verdict = Verdict(
            status="PASS",
            reason="All tests passed",
            evidence="pytest: 10 passed",
            suggestion="",
        )

        with patch.object(state, "_persist"):
            state.record_step("implement", verdict)

        assert len(state.step_history) == 1
        record = state.step_history[0]
        assert isinstance(record, StepRecord)
        assert record.step_id == "implement"
        assert record.verdict_status == "PASS"
        assert record.verdict_reason == "All tests passed"
        assert record.verdict_evidence == "pytest: 10 passed"
        assert record.verdict_suggestion == ""


# ============================================================
# 10. record_step sets last_completed_step
# ============================================================


@pytest.mark.small
class TestRecordStepSetsLastCompleted:
    """record_step updates last_completed_step to the recorded step_id."""

    def test_record_step_sets_last_completed(self) -> None:
        state = _make_state()
        verdict = Verdict(
            status="PASS",
            reason="OK",
            evidence="evidence",
            suggestion="",
        )

        with patch.object(state, "_persist"):
            state.record_step("review", verdict)

        assert state.last_completed_step == "review"


# ============================================================
# 11. record_step sets last_transition_verdict
# ============================================================


@pytest.mark.small
class TestRecordStepSetsLastVerdict:
    """record_step updates last_transition_verdict to the given Verdict."""

    def test_record_step_sets_last_verdict(self) -> None:
        state = _make_state()
        verdict = Verdict(
            status="RETRY",
            reason="Tests failed",
            evidence="pytest: 3 failed",
            suggestion="Fix import errors",
        )

        with patch.object(state, "_persist"):
            state.record_step("implement", verdict)

        assert state.last_transition_verdict is not None
        assert state.last_transition_verdict.status == "RETRY"
        assert state.last_transition_verdict.reason == "Tests failed"
        assert state.last_transition_verdict.suggestion == "Fix import errors"
