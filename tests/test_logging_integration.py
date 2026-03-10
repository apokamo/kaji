"""Medium tests: Logging integration.

Verifies run.log (workflow layer) and step logs are written correctly
with proper structure and immediate flush.
"""

import json
from pathlib import Path

import pytest

from dao_harness.logger import RunLogger
from dao_harness.models import CostInfo, Verdict


@pytest.mark.medium
class TestRunLogIntegration:
    """Full lifecycle logging test."""

    def test_full_workflow_logging(self, tmp_path: Path) -> None:
        """Complete workflow log contains all expected events in order."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path=log_path)

        # Simulate a workflow
        logger.log_workflow_start(issue=42, workflow="feature-dev")
        logger.log_step_start(
            step_id="design",
            agent="claude",
            model="sonnet",
            effort="high",
            session_id="sess-001",
        )
        logger.log_step_end(
            step_id="design",
            verdict=Verdict(status="PASS", reason="ok", evidence="tests", suggestion=""),
            duration_ms=5000,
            cost=CostInfo(usd=0.05),
        )
        logger.log_cycle_iteration(cycle_name="code-review", iteration=1, max_iter=3)
        logger.log_workflow_end(
            status="COMPLETE",
            cycle_counts={"code-review": 1},
            total_duration_ms=10000,
            total_cost=0.05,
        )

        # Read and verify
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 5

        events = [json.loads(line) for line in lines]
        assert events[0]["event"] == "workflow_start"
        assert events[0]["issue"] == 42
        assert events[1]["event"] == "step_start"
        assert events[1]["step_id"] == "design"
        assert events[2]["event"] == "step_end"
        assert events[2]["verdict"]["status"] == "PASS"
        assert events[2]["duration_ms"] == 5000
        assert events[3]["event"] == "cycle_iteration"
        assert events[4]["event"] == "workflow_end"

    def test_log_with_error(self, tmp_path: Path) -> None:
        """Workflow end with error is logged."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path=log_path)

        logger.log_workflow_end(
            status="ERROR",
            cycle_counts={},
            total_duration_ms=1000,
            total_cost=None,
            error="VerdictNotFound: no verdict block",
        )

        line = json.loads(log_path.read_text().strip())
        assert line["event"] == "workflow_end"
        assert line["status"] == "ERROR"
        assert "VerdictNotFound" in line["error"]

    def test_logs_are_appendable(self, tmp_path: Path) -> None:
        """Multiple log writes append to the same file."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path=log_path)

        logger.log_workflow_start(issue=1, workflow="test")
        logger.log_workflow_start(issue=2, workflow="test2")

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_nested_directory_creation(self, tmp_path: Path) -> None:
        """Logger creates parent directories if they don't exist."""
        log_path = tmp_path / "deep" / "nested" / "run.log"
        logger = RunLogger(log_path=log_path)

        logger.log_workflow_start(issue=1, workflow="test")

        assert log_path.exists()
