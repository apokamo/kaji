"""Tests for prompt building.

Covers build_prompt: skill reference, issue number injection,
step_id inclusion, valid statuses, verdict format, cycle variable
injection, and previous_verdict handling.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dao_harness.models import CycleDefinition, Step, Verdict, Workflow
from dao_harness.prompt import build_prompt
from dao_harness.state import SessionState

# ============================================================
# Helpers
# ============================================================


def _make_step(
    step_id: str = "implement",
    skill: str = "implement-skill",
    agent: str = "claude",
    on: dict[str, str] | None = None,
    resume: str | None = None,
) -> Step:
    """Create a Step with sensible defaults."""
    return Step(
        id=step_id,
        skill=skill,
        agent=agent,
        on=on or {"PASS": "review", "RETRY": "implement"},
        resume=resume,
    )


def _make_workflow(
    steps: list[Step] | None = None,
    cycles: list[CycleDefinition] | None = None,
) -> Workflow:
    """Create a Workflow with sensible defaults."""
    return Workflow(
        name="test-workflow",
        description="A test workflow",
        execution_policy="sequential",
        steps=steps or [_make_step()],
        cycles=cycles or [],
    )


def _make_state(
    issue: int = 42,
    last_transition_verdict: Verdict | None = None,
) -> SessionState:
    """Create a SessionState without filesystem side effects."""
    with patch.object(SessionState, "_persist"):
        state = SessionState(
            issue_number=issue,
            sessions={},
            step_history=[],
            cycle_counts={},
            last_completed_step=None,
            last_transition_verdict=last_transition_verdict,
        )
    return state


# ============================================================
# 1. Basic prompt contains skill name reference
# ============================================================


@pytest.mark.small
class TestPromptContainsSkillName:
    """build_prompt output references the skill name."""

    def test_prompt_contains_skill(self) -> None:
        step = _make_step(skill="design-skill")
        workflow = _make_workflow(steps=[step])
        state = _make_state()

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "design-skill" in prompt


# ============================================================
# 2. Basic prompt contains issue number
# ============================================================


@pytest.mark.small
class TestPromptContainsIssueNumber:
    """build_prompt output contains the issue number."""

    def test_prompt_contains_issue(self) -> None:
        step = _make_step()
        workflow = _make_workflow(steps=[step])
        state = _make_state(issue=123)

        prompt = build_prompt(step, issue=123, state=state, workflow=workflow)

        assert "123" in prompt


# ============================================================
# 3. Basic prompt contains step_id
# ============================================================


@pytest.mark.small
class TestPromptContainsStepId:
    """build_prompt output contains the step id."""

    def test_prompt_contains_step_id(self) -> None:
        step = _make_step(step_id="review-step")
        workflow = _make_workflow(steps=[step])
        state = _make_state()

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "review-step" in prompt


# ============================================================
# 4. Basic prompt contains valid statuses from step.on keys
# ============================================================


@pytest.mark.small
class TestPromptContainsValidStatuses:
    """build_prompt output includes the valid status values from step.on."""

    def test_prompt_contains_statuses(self) -> None:
        step = _make_step(on={"PASS": "__end__", "RETRY": "implement", "BACK": "design"})
        workflow = _make_workflow(steps=[step])
        state = _make_state()

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "PASS" in prompt
        assert "RETRY" in prompt
        assert "BACK" in prompt


# ============================================================
# 5. Prompt contains verdict output format instructions
# ============================================================


@pytest.mark.small
class TestPromptContainsVerdictFormat:
    """build_prompt output includes verdict format markers."""

    def test_prompt_contains_verdict_format(self) -> None:
        step = _make_step()
        workflow = _make_workflow(steps=[step])
        state = _make_state()

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "---VERDICT---" in prompt
        assert "---END_VERDICT---" in prompt


# ============================================================
# 6. Cycle variables injected when step is in a cycle
# ============================================================


@pytest.mark.small
class TestCycleVariablesInjected:
    """build_prompt includes cycle_count and max_iterations when step is in a cycle."""

    def test_cycle_variables_present(self) -> None:
        step = _make_step(step_id="implement")
        cycle = CycleDefinition(
            name="impl-loop",
            entry="implement",
            loop=["implement", "review"],
            max_iterations=3,
            on_exhaust="__end__",
        )
        workflow = _make_workflow(steps=[step], cycles=[cycle])
        state = _make_state()
        state.cycle_counts["impl-loop"] = 1

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        # Should contain the current iteration count and max
        assert "1" in prompt
        assert "3" in prompt


# ============================================================
# 7. Cycle variables NOT injected when step is not in a cycle
# ============================================================


@pytest.mark.small
class TestNoCycleVariablesWhenNotInCycle:
    """build_prompt does not include cycle info when the step is not in any cycle."""

    def test_no_cycle_variables(self) -> None:
        step = _make_step(step_id="standalone")
        workflow = _make_workflow(steps=[step], cycles=[])
        state = _make_state()

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "max_iterations" not in prompt.lower()
        assert "cycle_count" not in prompt.lower()


# ============================================================
# 8. previous_verdict injected when step has resume AND state has verdict
# ============================================================


@pytest.mark.small
class TestPreviousVerdictInjected:
    """build_prompt includes previous verdict when step.resume is set and state has a verdict."""

    def test_previous_verdict_present(self) -> None:
        prev_verdict = Verdict(
            status="RETRY",
            reason="Tests failed",
            evidence="pytest: 3 failed",
            suggestion="Fix import errors",
        )
        step = _make_step(resume="design")
        workflow = _make_workflow(steps=[step])
        state = _make_state(last_transition_verdict=prev_verdict)

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        assert "RETRY" in prompt
        assert "Tests failed" in prompt


# ============================================================
# 9. previous_verdict NOT injected when step has no resume
# ============================================================


@pytest.mark.small
class TestNoPreviousVerdictWithoutResume:
    """build_prompt does not include previous verdict when step.resume is None."""

    def test_no_previous_verdict_without_resume(self) -> None:
        prev_verdict = Verdict(
            status="RETRY",
            reason="Tests failed",
            evidence="pytest: 3 failed",
            suggestion="Fix import errors",
        )
        step = _make_step(resume=None)
        workflow = _make_workflow(steps=[step])
        state = _make_state(last_transition_verdict=prev_verdict)

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        # The previous verdict's specific reason should NOT appear
        assert "Tests failed" not in prompt


# ============================================================
# 10. previous_verdict NOT injected when state has no verdict
# ============================================================


@pytest.mark.small
class TestNoPreviousVerdictWhenStateEmpty:
    """build_prompt does not include previous verdict when state has no verdict."""

    def test_no_previous_verdict_when_none(self) -> None:
        step = _make_step(resume="design")
        workflow = _make_workflow(steps=[step])
        state = _make_state(last_transition_verdict=None)

        prompt = build_prompt(step, issue=42, state=state, workflow=workflow)

        # Should not contain previous verdict markers
        # (The prompt should still be valid, just without previous verdict section)
        assert "previous_verdict" not in prompt.lower() or "none" in prompt.lower()
