"""Medium tests: Workflow execution with mock CLI.

Tests the full workflow execution loop using mock CLI processes.
Verifies state transitions, retry cycles, abort handling, --from resume,
--step single execution, MissingResumeSessionError, and run log terminal state.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from dao_harness.errors import MissingResumeSessionError, WorkflowValidationError
from dao_harness.models import CLIResult, CostInfo, CycleDefinition, Step, Workflow
from dao_harness.runner import WorkflowRunner


def _make_verdict_output(status: str, reason: str = "ok", evidence: str = "test") -> str:
    """Create output text containing a verdict block."""
    suggestion = "fix it" if status in ("ABORT", "BACK") else ""
    return f"""Some output text here.

---VERDICT---
status: {status}
reason: "{reason}"
evidence: "{evidence}"
suggestion: "{suggestion}"
---END_VERDICT---
"""


def _make_cli_result(status: str, session_id: str = "sess-001", reason: str = "ok") -> CLIResult:
    """Create a CLIResult with a verdict block in the output."""
    return CLIResult(
        full_output=_make_verdict_output(status, reason=reason),
        session_id=session_id,
        cost=CostInfo(usd=0.01),
        stderr="",
    )


def _simple_workflow() -> Workflow:
    """Create a minimal 2-step workflow: design → review."""
    return Workflow(
        name="test-workflow",
        description="Test",
        execution_policy="auto",
        steps=[
            Step(
                id="design",
                skill="issue-design",
                agent="claude",
                on={"PASS": "review", "ABORT": "end"},
            ),
            Step(
                id="review",
                skill="issue-review",
                agent="codex",
                on={"PASS": "end", "ABORT": "end"},
            ),
        ],
    )


def _cycle_workflow() -> Workflow:
    """Create a workflow with a review cycle (review → fix → verify → ...)."""
    return Workflow(
        name="cycle-test",
        description="Test with cycle",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="issue-implement",
                agent="claude",
                on={"PASS": "review", "ABORT": "end"},
            ),
            Step(
                id="review",
                skill="issue-review",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix", "ABORT": "end"},
            ),
            Step(
                id="fix",
                skill="issue-fix",
                agent="claude",
                resume="implement",
                on={"PASS": "verify", "ABORT": "end"},
            ),
            Step(
                id="verify",
                skill="issue-verify",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix"},
            ),
        ],
        cycles=[
            CycleDefinition(
                name="code-review",
                entry="review",
                loop=["fix", "verify"],
                max_iterations=3,
                on_exhaust="ABORT",
            ),
        ],
    )


@pytest.mark.medium
class TestWorkflowExecution:
    """Full workflow execution with mocked CLI."""

    def test_simple_workflow_pass_through(self, tmp_path: Path) -> None:
        """Two-step workflow with PASS → PASS completes successfully."""
        workflow = _simple_workflow()
        results = [
            _make_cli_result("PASS", session_id="sess-design"),
            _make_cli_result("PASS", session_id="sess-review"),
        ]
        call_count = 0

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            state = runner.run()

        assert state.last_completed_step == "review"
        assert len(state.step_history) == 2
        assert state.step_history[0].step_id == "design"
        assert state.step_history[1].step_id == "review"

    def test_workflow_abort_stops_early(self, tmp_path: Path) -> None:
        """Workflow stops when a step returns ABORT."""
        workflow = _simple_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _make_cli_result("ABORT")

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            state = runner.run()

        assert state.last_completed_step == "design"
        assert len(state.step_history) == 1

    def test_cycle_retry_and_pass(self, tmp_path: Path) -> None:
        """Review cycle: implement → review(RETRY) → fix → verify(PASS)."""
        workflow = _cycle_workflow()
        results = [
            _make_cli_result("PASS", session_id="sess-impl"),  # implement
            _make_cli_result("RETRY", session_id="sess-review"),  # review → RETRY
            _make_cli_result("PASS", session_id="sess-fix"),  # fix
            _make_cli_result("PASS", session_id="sess-verify"),  # verify → PASS (exit)
        ]
        call_count = 0

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            state = runner.run()

        assert state.last_completed_step == "verify"
        assert len(state.step_history) == 4

    def test_from_step_resumes_workflow(self, tmp_path: Path) -> None:
        """--from skips earlier steps and starts from specified step."""
        workflow = _simple_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _make_cli_result("PASS", session_id="sess-review")

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
                from_step="review",
            )
            state = runner.run()

        # Only review was executed (design was skipped)
        assert len(state.step_history) == 1
        assert state.step_history[0].step_id == "review"

    def test_single_step_executes_only_one(self, tmp_path: Path) -> None:
        """--step executes only the specified step, no transitions."""
        workflow = _simple_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _make_cli_result("PASS", session_id="sess-design")

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
                single_step="design",
            )
            state = runner.run()

        assert len(state.step_history) == 1
        assert state.step_history[0].step_id == "design"

    def test_unknown_from_step_raises_error(self, tmp_path: Path) -> None:
        """--from with non-existent step raises WorkflowValidationError."""
        workflow = _simple_workflow()

        with (
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            with pytest.raises(WorkflowValidationError):
                runner = WorkflowRunner(
                    workflow=workflow,
                    issue_number=99,
                    workdir=tmp_path,
                    from_step="nonexistent",
                )
                runner.run()

    def test_unknown_single_step_raises_error(self, tmp_path: Path) -> None:
        """--step with non-existent step raises WorkflowValidationError."""
        workflow = _simple_workflow()

        with (
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            with pytest.raises(WorkflowValidationError):
                runner = WorkflowRunner(
                    workflow=workflow,
                    issue_number=99,
                    workdir=tmp_path,
                    single_step="nonexistent",
                )
                runner.run()

    def test_resume_without_session_id_raises_error(self, tmp_path: Path) -> None:
        """Resume step without prior session_id raises MissingResumeSessionError."""
        workflow = _cycle_workflow()
        # implement PASS → review RETRY → fix (needs resume from implement but no session)
        results = [
            _make_cli_result("PASS", session_id=None),  # implement - no session_id!
            _make_cli_result("RETRY"),  # review
        ]
        call_count = 0

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            with pytest.raises(MissingResumeSessionError):
                runner.run()


@pytest.mark.medium
class TestWorkflowSessionManagement:
    """Session ID tracking across workflow steps."""

    def test_session_ids_saved_per_step(self, tmp_path: Path) -> None:
        """Each step's session_id is saved in state."""
        workflow = _simple_workflow()
        results = [
            _make_cli_result("PASS", session_id="sess-1"),
            _make_cli_result("PASS", session_id="sess-2"),
        ]
        call_count = 0

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            state = runner.run()

        assert state.sessions.get("design") == "sess-1"
        assert state.sessions.get("review") == "sess-2"


@pytest.mark.medium
class TestWorkflowEndLogging:
    """Verify run.log terminal state reflects actual workflow outcome."""

    def test_abort_workflow_logs_abort_status(self, tmp_path: Path) -> None:
        """Workflow ending via ABORT logs status=ABORT in workflow_end."""
        workflow = _simple_workflow()
        logged_calls: list[dict] = []

        def capture_workflow_end(status: str, cycle_counts: dict, **kwargs: object) -> None:
            logged_calls.append({"status": status, **kwargs})

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _make_cli_result("ABORT")

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
            patch(
                "dao_harness.logger.RunLogger.log_workflow_end",
                side_effect=capture_workflow_end,
            ),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            runner.run()

        assert len(logged_calls) == 1
        assert logged_calls[0]["status"] == "ABORT"

    def test_pass_workflow_logs_complete_status(self, tmp_path: Path) -> None:
        """Workflow ending via PASS logs status=COMPLETE in workflow_end."""
        workflow = _simple_workflow()
        logged_calls: list[dict] = []

        def capture_workflow_end(status: str, cycle_counts: dict, **kwargs: object) -> None:
            logged_calls.append({"status": status, **kwargs})

        results = [
            _make_cli_result("PASS", session_id="sess-1"),
            _make_cli_result("PASS", session_id="sess-2"),
        ]
        call_count = 0

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
            patch(
                "dao_harness.logger.RunLogger.log_workflow_end",
                side_effect=capture_workflow_end,
            ),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            runner.run()

        assert len(logged_calls) == 1
        assert logged_calls[0]["status"] == "COMPLETE"

    def test_exception_logs_error_status(self, tmp_path: Path) -> None:
        """Exception during workflow logs status=ERROR with error message."""
        workflow = _simple_workflow()
        logged_calls: list[dict] = []

        def capture_workflow_end(status: str, cycle_counts: dict, **kwargs: object) -> None:
            logged_calls.append({"status": status, **kwargs})

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            raise RuntimeError("test failure")

        with (
            patch("dao_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("dao_harness.runner.validate_skill_exists"),
            patch("dao_harness.state.SessionState._persist"),
            patch(
                "dao_harness.logger.RunLogger.log_workflow_end",
                side_effect=capture_workflow_end,
            ),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                workdir=tmp_path,
            )
            with pytest.raises(RuntimeError):
                runner.run()

        assert len(logged_calls) == 1
        assert logged_calls[0]["status"] == "ERROR"
        assert "RuntimeError" in logged_calls[0]["error"]
