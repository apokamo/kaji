"""Tests for --from and --step start logic and resume guard.

Covers Workflow.find_step, Workflow.find_start_step, empty workflow
handling, and the resume session guard logic.
"""

from __future__ import annotations

import pytest

from kaji_harness.errors import MissingResumeSessionError
from kaji_harness.models import Step, Workflow

# ============================================================
# Helpers
# ============================================================


def _make_step(
    step_id: str = "implement",
    skill: str = "implement-skill",
    agent: str = "claude",
    resume: str | None = None,
) -> Step:
    """Create a Step with sensible defaults."""
    return Step(
        id=step_id,
        skill=skill,
        agent=agent,
        on={"PASS": "__end__"},
        resume=resume,
    )


def _make_workflow(steps: list[Step] | None = None) -> Workflow:
    """Create a Workflow with given steps."""
    return Workflow(
        name="test-workflow",
        description="A test workflow",
        execution_policy="sequential",
        steps=steps or [],
    )


def _check_resume_guard(step: Step, session_id: str | None) -> None:
    """Apply the resume guard logic as the runner would.

    Raises MissingResumeSessionError when the step requires resume
    but no session_id is available.
    """
    if step.resume and session_id is None:
        raise MissingResumeSessionError(step.id, step.resume)


# ============================================================
# 1. find_step with valid id → returns step
# ============================================================


@pytest.mark.small
class TestFindStepValid:
    """Workflow.find_step returns the matching Step when id exists."""

    def test_find_step_returns_step(self) -> None:
        step_a = _make_step(step_id="design")
        step_b = _make_step(step_id="implement")
        step_c = _make_step(step_id="review")
        wf = _make_workflow(steps=[step_a, step_b, step_c])

        result = wf.find_step("implement")

        assert result is not None
        assert result.id == "implement"


# ============================================================
# 2. find_step with invalid id → returns None
# ============================================================


@pytest.mark.small
class TestFindStepInvalid:
    """Workflow.find_step returns None when id does not exist."""

    def test_find_step_returns_none(self) -> None:
        step_a = _make_step(step_id="design")
        wf = _make_workflow(steps=[step_a])

        result = wf.find_step("nonexistent")

        assert result is None


# ============================================================
# 3. find_start_step → returns first step
# ============================================================


@pytest.mark.small
class TestFindStartStep:
    """Workflow.find_start_step returns the first step in the list."""

    def test_find_start_step_returns_first(self) -> None:
        step_a = _make_step(step_id="design")
        step_b = _make_step(step_id="implement")
        wf = _make_workflow(steps=[step_a, step_b])

        result = wf.find_start_step()

        assert result.id == "design"


# ============================================================
# 4. Workflow with no steps → IndexError on find_start_step
# ============================================================


@pytest.mark.small
class TestFindStartStepEmpty:
    """Workflow.find_start_step raises IndexError when steps list is empty."""

    def test_empty_workflow_raises(self) -> None:
        wf = _make_workflow(steps=[])

        with pytest.raises(IndexError):
            wf.find_start_step()


# ============================================================
# 5. Resume guard: step.resume set, session_id None → error
# ============================================================


@pytest.mark.small
class TestResumeGuardMissingSession:
    """Resume guard raises MissingResumeSessionError when session is None."""

    def test_resume_no_session_raises(self) -> None:
        step = _make_step(step_id="implement", resume="design")

        with pytest.raises(MissingResumeSessionError) as exc_info:
            _check_resume_guard(step, session_id=None)

        assert exc_info.value.step_id == "implement"
        assert exc_info.value.resume_target == "design"


# ============================================================
# 6. Resume guard: step.resume set, session_id exists → no error
# ============================================================


@pytest.mark.small
class TestResumeGuardWithSession:
    """Resume guard passes when session_id is available."""

    def test_resume_with_session_ok(self) -> None:
        step = _make_step(step_id="implement", resume="design")

        # Should not raise
        _check_resume_guard(step, session_id="sess-abc123")


# ============================================================
# 7. Resume guard: step.resume None → no error regardless
# ============================================================


@pytest.mark.small
class TestResumeGuardNoResume:
    """Resume guard passes when step.resume is None, regardless of session_id."""

    def test_no_resume_no_session_ok(self) -> None:
        step = _make_step(step_id="implement", resume=None)

        # Should not raise even with None session_id
        _check_resume_guard(step, session_id=None)

    def test_no_resume_with_session_ok(self) -> None:
        step = _make_step(step_id="implement", resume=None)

        # Should not raise with a session_id either
        _check_resume_guard(step, session_id="sess-xyz")
