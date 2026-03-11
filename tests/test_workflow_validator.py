"""Tests for workflow validation logic.

Covers validate_workflow and WorkflowValidationError collection.
"""

from __future__ import annotations

import pytest

from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import CycleDefinition, Step, Workflow
from kaji_harness.workflow import validate_workflow

# ============================================================
# Helpers for building test Workflow objects
# ============================================================


def _step(
    id: str,
    skill: str = "default-skill",
    agent: str = "claude",
    *,
    model: str | None = None,
    effort: str | None = None,
    resume: str | None = None,
    on: dict[str, str] | None = None,
) -> Step:
    """Shorthand factory for building Step objects in tests."""
    return Step(
        id=id,
        skill=skill,
        agent=agent,
        model=model,
        effort=effort,
        resume=resume,
        on=on if on is not None else {},
    )


def _workflow(
    steps: list[Step],
    cycles: list[CycleDefinition] | None = None,
    *,
    name: str = "test-wf",
    description: str = "Test workflow",
    execution_policy: str = "auto",
) -> Workflow:
    """Shorthand factory for building Workflow objects in tests."""
    return Workflow(
        name=name,
        description=description,
        execution_policy=execution_policy,
        steps=steps,
        cycles=cycles if cycles is not None else [],
    )


# ============================================================
# Test class: Valid workflows
# ============================================================


class TestValidWorkflows:
    """Validation passes for well-formed workflows."""

    @pytest.mark.small
    def test_valid_workflow_passes(self) -> None:
        """A simple valid workflow raises no error."""
        wf = _workflow(
            steps=[
                _step("analyse", agent="gemini", on={"PASS": "implement", "ABORT": "end"}),
                _step("implement", agent="claude", on={"PASS": "review", "ABORT": "end"}),
                _step("review", agent="codex", on={"PASS": "end", "RETRY": "implement"}),
            ],
        )

        # Should not raise
        validate_workflow(wf)

    @pytest.mark.small
    def test_valid_workflow_with_cycles_passes(self) -> None:
        """A workflow with properly configured cycles raises no error."""
        wf = _workflow(
            steps=[
                _step("design", agent="claude", on={"PASS": "implement", "ABORT": "end"}),
                _step("implement", agent="claude", on={"PASS": "review", "ABORT": "end"}),
                _step(
                    "review",
                    agent="codex",
                    on={"PASS": "end", "RETRY": "implement"},
                ),
            ],
            cycles=[
                CycleDefinition(
                    name="impl-loop",
                    entry="implement",
                    loop=["implement", "review"],
                    max_iterations=3,
                    on_exhaust="ABORT",
                ),
            ],
        )

        validate_workflow(wf)


# ============================================================
# Test class: Resume validation
# ============================================================


class TestResumeValidation:
    """Validation of the resume field on steps."""

    @pytest.mark.small
    def test_resume_references_unknown_step(self) -> None:
        """resume pointing to a non-existent step triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="claude"),
                _step("step_b", agent="claude", resume="nonexistent"),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("nonexistent" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_resume_agent_mismatch(self) -> None:
        """resume targeting a step with a different agent triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="gemini"),
                _step("step_b", agent="claude", resume="step_a"),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        errors_joined = " ".join(exc_info.value.errors)
        assert "agent" in errors_joined.lower() or "mismatch" in errors_joined.lower()


# ============================================================
# Test class: Transition (on) validation
# ============================================================


class TestTransitionValidation:
    """Validation of on-transition targets and verdict values."""

    @pytest.mark.small
    def test_on_transition_to_unknown_step(self) -> None:
        """on transition referencing a non-existent step triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", on={"PASS": "ghost_step"}),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("ghost_step" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_invalid_verdict_value_in_on(self) -> None:
        """Invalid verdict key in on triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", on={"INVALID_VERDICT": "end"}),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("INVALID_VERDICT" in e for e in exc_info.value.errors)


# ============================================================
# Test class: Cycle validation
# ============================================================


class TestCycleValidation:
    """Validation of cycle definitions."""

    @pytest.mark.small
    def test_cycle_entry_step_not_found(self) -> None:
        """Cycle with an entry pointing to a missing step triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", on={"PASS": "end"}),
            ],
            cycles=[
                CycleDefinition(
                    name="bad-cycle",
                    entry="missing_entry",
                    loop=["step_a"],
                    max_iterations=3,
                    on_exhaust="ABORT",
                ),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("missing_entry" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_cycle_loop_step_not_found(self) -> None:
        """Cycle with a loop step that doesn't exist triggers an error."""
        wf = _workflow(
            steps=[
                _step("step_a", on={"PASS": "end"}),
            ],
            cycles=[
                CycleDefinition(
                    name="bad-cycle",
                    entry="step_a",
                    loop=["step_a", "phantom_step"],
                    max_iterations=3,
                    on_exhaust="ABORT",
                ),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("phantom_step" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_cycle_loop_tail_retry_not_to_loop_head(self) -> None:
        """Cycle loop tail's RETRY must route back to loop head; otherwise error."""
        wf = _workflow(
            steps=[
                _step("impl", agent="claude", on={"PASS": "review", "ABORT": "end"}),
                _step(
                    "review",
                    agent="codex",
                    on={"PASS": "end", "RETRY": "end"},  # Should go to impl
                ),
            ],
            cycles=[
                CycleDefinition(
                    name="impl-loop",
                    entry="impl",
                    loop=["impl", "review"],
                    max_iterations=3,
                    on_exhaust="ABORT",
                ),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        errors_joined = " ".join(exc_info.value.errors)
        assert "RETRY" in errors_joined or "loop" in errors_joined.lower()

    @pytest.mark.small
    def test_cycle_has_no_exit(self) -> None:
        """Cycle where PASS never leaves the cycle triggers an error."""
        wf = _workflow(
            steps=[
                _step("impl", agent="claude", on={"PASS": "review"}),
                _step(
                    "review",
                    agent="codex",
                    on={"PASS": "impl", "RETRY": "impl"},  # PASS stays in cycle
                ),
            ],
            cycles=[
                CycleDefinition(
                    name="infinite-loop",
                    entry="impl",
                    loop=["impl", "review"],
                    max_iterations=3,
                    on_exhaust="ABORT",
                ),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        errors_joined = " ".join(exc_info.value.errors)
        assert "exit" in errors_joined.lower() or "PASS" in errors_joined

    @pytest.mark.small
    def test_invalid_on_exhaust_value(self) -> None:
        """Invalid on_exhaust value triggers an error."""
        wf = _workflow(
            steps=[
                _step("impl", agent="claude", on={"PASS": "review"}),
                _step(
                    "review",
                    agent="codex",
                    on={"PASS": "end", "RETRY": "impl"},
                ),
            ],
            cycles=[
                CycleDefinition(
                    name="bad-exhaust",
                    entry="impl",
                    loop=["impl", "review"],
                    max_iterations=3,
                    on_exhaust="nonexistent_step",
                ),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("nonexistent_step" in e or "on_exhaust" in e for e in exc_info.value.errors)


# ============================================================
# Test class: Multiple error collection
# ============================================================


class TestMultipleErrorCollection:
    """Validation collects all errors before raising."""

    @pytest.mark.small
    def test_multiple_errors_collected(self) -> None:
        """Multiple validation failures are collected in a single exception."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="claude", resume="ghost", on={"PASS": "nowhere"}),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        # At least two errors: unknown resume target + unknown on target
        assert len(exc_info.value.errors) >= 2
