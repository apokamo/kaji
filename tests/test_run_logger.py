"""Tests for RunLogger JSONL structured logging.

Covers all log methods: workflow_start, step_start, step_end,
cycle_iteration, workflow_end.  Each method must write a single
JSONL line with an ISO 8601 ``ts`` field and the expected payload.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dao_harness.logger import RunLogger
from dao_harness.models import CostInfo, Verdict

# ============================================================
# Helpers
# ============================================================


def _read_events(log_path: Path) -> list[dict]:
    """Read all JSONL events from *log_path*."""
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _assert_iso8601(ts: str) -> None:
    """Assert *ts* is a valid ISO 8601 timestamp."""
    # datetime.fromisoformat handles the common subset we emit.
    datetime.fromisoformat(ts)


# ============================================================
# Tests
# ============================================================


class TestRunLogger:
    """Unit tests for RunLogger JSONL output."""

    @pytest.mark.small
    def test_log_workflow_start(self, tmp_path: Path) -> None:
        """log_workflow_start writes JSONL with event, issue, workflow."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_workflow_start(issue=42, workflow="bugfix")

        events = _read_events(logger.log_path)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "workflow_start"
        assert ev["issue"] == 42
        assert ev["workflow"] == "bugfix"

    @pytest.mark.small
    def test_log_step_start(self, tmp_path: Path) -> None:
        """log_step_start writes JSONL with step_id, agent, model, effort, session_id."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_step_start(
            step_id="design",
            agent="claude",
            model="claude-sonnet-4-20250514",
            effort="high",
            session_id="sess-001",
        )

        events = _read_events(logger.log_path)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "step_start"
        assert ev["step_id"] == "design"
        assert ev["agent"] == "claude"
        assert ev["model"] == "claude-sonnet-4-20250514"
        assert ev["effort"] == "high"
        assert ev["session_id"] == "sess-001"

    @pytest.mark.small
    def test_log_step_end(self, tmp_path: Path) -> None:
        """log_step_end writes JSONL with verdict (as dict), duration_ms, cost."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")
        verdict = Verdict(
            status="PASS",
            reason="All tests green",
            evidence="pytest output",
            suggestion="",
        )
        cost = CostInfo(usd=0.05, input_tokens=1000, output_tokens=500)

        logger.log_step_end(
            step_id="review",
            verdict=verdict,
            duration_ms=12345,
            cost=cost,
        )

        events = _read_events(logger.log_path)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "step_end"
        assert ev["step_id"] == "review"
        assert ev["verdict"]["status"] == "PASS"
        assert ev["verdict"]["reason"] == "All tests green"
        assert ev["duration_ms"] == 12345
        assert ev["cost"]["usd"] == 0.05
        assert ev["cost"]["input_tokens"] == 1000

    @pytest.mark.small
    def test_log_cycle_iteration(self, tmp_path: Path) -> None:
        """log_cycle_iteration writes JSONL with cycle_name, iteration, max_iterations."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_cycle_iteration(
            cycle_name="impl-loop",
            iteration=2,
            max_iter=5,
        )

        events = _read_events(logger.log_path)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "cycle_iteration"
        assert ev["cycle_name"] == "impl-loop"
        assert ev["iteration"] == 2
        assert ev["max_iterations"] == 5

    @pytest.mark.small
    def test_log_workflow_end(self, tmp_path: Path) -> None:
        """log_workflow_end writes JSONL with status, cycle_counts, totals, error."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_workflow_end(
            status="completed",
            cycle_counts={"impl-loop": 3},
            total_duration_ms=60000,
            total_cost=1.23,
            error=None,
        )

        events = _read_events(logger.log_path)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "workflow_end"
        assert ev["status"] == "completed"
        assert ev["cycle_counts"] == {"impl-loop": 3}
        assert ev["total_duration_ms"] == 60000
        assert ev["total_cost"] == 1.23
        assert "error" not in ev  # error omitted when None

    @pytest.mark.small
    def test_multiple_events_appended_as_separate_lines(self, tmp_path: Path) -> None:
        """Multiple log calls produce one JSONL line each (append mode)."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_workflow_start(issue=1, workflow="design")
        logger.log_step_start(
            step_id="s1",
            agent="claude",
            model=None,
            effort=None,
            session_id=None,
        )
        logger.log_workflow_end(
            status="completed",
            cycle_counts={},
            total_duration_ms=100,
            total_cost=None,
        )

        lines = logger.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    @pytest.mark.small
    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        """Every line written by RunLogger is independently valid JSON."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_workflow_start(issue=10, workflow="bugfix")
        logger.log_cycle_iteration(cycle_name="loop", iteration=1, max_iter=3)

        for line in logger.log_path.read_text(encoding="utf-8").strip().splitlines():
            parsed = json.loads(line)  # raises on invalid JSON
            assert isinstance(parsed, dict)

    @pytest.mark.small
    def test_ts_field_is_iso8601(self, tmp_path: Path) -> None:
        """Each event line contains a ``ts`` field in ISO 8601 format."""
        logger = RunLogger(log_path=tmp_path / "run.jsonl")

        logger.log_workflow_start(issue=1, workflow="bugfix")
        logger.log_step_start(
            step_id="s1",
            agent="claude",
            model=None,
            effort=None,
            session_id=None,
        )

        for line in logger.log_path.read_text(encoding="utf-8").strip().splitlines():
            ev = json.loads(line)
            assert "ts" in ev, "Event line must contain a 'ts' field"
            _assert_iso8601(ev["ts"])

    @pytest.mark.small
    def test_parent_directories_created_automatically(self, tmp_path: Path) -> None:
        """RunLogger creates intermediate parent directories for the log file."""
        deep_path = tmp_path / "a" / "b" / "c" / "run.jsonl"
        logger = RunLogger(log_path=deep_path)

        logger.log_workflow_start(issue=1, workflow="design")

        assert deep_path.exists()
        events = _read_events(deep_path)
        assert len(events) == 1
