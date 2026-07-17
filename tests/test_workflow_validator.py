"""Tests for workflow validation logic.

Covers validate_workflow and WorkflowValidationError collection.
"""

from __future__ import annotations

from typing import cast

import pytest

from kaji_harness.adapters import ADAPTERS
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import CycleDefinition, Step, Workflow
from kaji_harness.workflow import VALID_AGENTS, validate_workflow

# ============================================================
# Helpers for building test Workflow objects
# ============================================================


def _step(
    id: str,
    skill: str = "default-skill",
    agent: str | None = "claude",
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


class TestAgentValidation:
    """Validation of the agent enum."""

    @pytest.mark.small
    @pytest.mark.parametrize("agent", ["claude", "codex", "gemini"])
    def test_registered_agent_passes(self, agent: str) -> None:
        """Every registered runtime agent is accepted."""
        validate_workflow(_workflow([_step("run", agent=agent, on={"PASS": "end"})]))

    @pytest.mark.small
    def test_omitted_agent_is_not_an_enum_error(self) -> None:
        """An omitted agent remains available to exec_script preflight validation."""
        validate_workflow(_workflow([_step("run", agent=None, on={"PASS": "end"})]))

    @pytest.mark.small
    def test_model_name_is_not_enum_validated(self) -> None:
        """Rapidly changing model names remain passthrough values."""
        workflow = _workflow(
            [_step("run", agent="codex", model="future-model-name", on={"PASS": "end"})]
        )

        validate_workflow(workflow)

    @pytest.mark.small
    @pytest.mark.parametrize("agent", ["cladue", "Claude", "unknown"])
    def test_unknown_agent_is_rejected(self, agent: str) -> None:
        """Unknown and incorrectly cased agents are rejected with the enum contract."""
        workflow = _workflow([_step("run", agent=agent, on={"PASS": "end"})])

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert exc_info.value.errors == [
            f"Step 'run' has unknown agent '{agent}' (allowed: ['claude', 'codex', 'gemini'])"
        ]

    @pytest.mark.small
    def test_agent_enum_matches_runtime_adapters(self) -> None:
        """Static validation and runtime dispatch expose the same agent set."""
        assert VALID_AGENTS == frozenset(ADAPTERS)


class TestPassTransitionValidation:
    """Validation of the mandatory PASS transition."""

    @pytest.mark.small
    @pytest.mark.parametrize("agent", ["claude", None], ids=["agent", "exec-script"])
    def test_skill_step_without_pass_is_rejected(self, agent: str | None) -> None:
        """Agent-backed and exec_script skill steps require a successful transition."""
        workflow = _workflow([_step("run", agent=agent, on={"ABORT": "end"})])

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert "Step 'run' 'on' must define a 'PASS' transition" in exc_info.value.errors

    @pytest.mark.small
    def test_exec_step_without_pass_is_rejected(self) -> None:
        """A direct exec step must define its successful transition."""
        workflow = _workflow(
            [Step(id="run", exec=["true"], on={"ABORT": "end"})],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert "Step 'run' 'on' must define a 'PASS' transition" in exc_info.value.errors

    @pytest.mark.small
    @pytest.mark.parametrize("invalid_on", [{}, []], ids=["empty", "non-mapping"])
    def test_invalid_on_does_not_duplicate_pass_error(self, invalid_on: object) -> None:
        """An invalid on mapping reports only the existing mapping error."""
        workflow = _workflow(
            [
                Step(
                    id="run",
                    skill="default-skill",
                    agent="claude",
                    on=cast(dict[str, str], invalid_on),
                )
            ]
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert exc_info.value.errors == ["Step 'run' 'on' must be a non-empty mapping"]


class TestReachabilityValidation:
    """Validation of reachability from the canonical first step."""

    @pytest.mark.small
    def test_branching_graph_and_single_step_are_valid(self) -> None:
        """All declared steps may be reached through any verdict edge."""
        branching = _workflow(
            [
                _step("root", on={"PASS": "left", "RETRY": "right"}),
                _step("left", on={"PASS": "end"}),
                _step("right", on={"PASS": "end"}),
            ]
        )

        validate_workflow(branching)
        validate_workflow(_workflow([_step("only", on={"PASS": "end"})]))

    @pytest.mark.small
    def test_unreachable_steps_are_reported_in_declaration_order(self) -> None:
        """Disconnected steps are errors rooted at the first declared step."""
        workflow = _workflow(
            [
                _step("root", on={"PASS": "end"}),
                _step("orphan-a", on={"PASS": "orphan-b"}),
                _step("orphan-b", on={"PASS": "end"}),
            ]
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert exc_info.value.errors == [
            "Step 'orphan-a' is not reachable from the first step 'root'",
            "Step 'orphan-b' is not reachable from the first step 'root'",
        ]

    @pytest.mark.small
    def test_cycle_and_resume_references_do_not_make_steps_reachable(self) -> None:
        """Cycle metadata and resume references are not graph transition edges."""
        workflow = _workflow(
            [
                _step("root", on={"PASS": "end"}),
                _step("orphan", resume="root", on={"PASS": "end"}),
            ],
            cycles=[
                CycleDefinition(
                    name="orphan-cycle",
                    entry="orphan",
                    loop=["orphan"],
                    max_iterations=1,
                    on_exhaust="ABORT",
                )
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert "Step 'orphan' is not reachable from the first step 'root'" in exc_info.value.errors

    @pytest.mark.small
    def test_unknown_transition_and_dead_step_errors_are_aggregated(self) -> None:
        """Unknown targets are ignored by traversal but retained as validation errors."""
        workflow = _workflow(
            [
                _step("root", on={"PASS": "missing"}),
                _step("orphan", on={"PASS": "end"}),
            ]
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert "Step 'root' transitions to unknown step 'missing' on PASS" in exc_info.value.errors
        assert "Step 'orphan' is not reachable from the first step 'root'" in exc_info.value.errors


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

    @pytest.mark.small
    def test_back_prefix_verdicts_accepted(self) -> None:
        """BACK_DESIGN / BACK_IMPLEMENT prefixed verdicts are accepted."""
        wf = _workflow(
            steps=[
                _step("design", agent="claude", on={"PASS": "implement", "ABORT": "end"}),
                _step("implement", agent="claude", on={"PASS": "final", "ABORT": "end"}),
                _step(
                    "final",
                    agent="claude",
                    on={
                        "PASS": "end",
                        "BACK_DESIGN": "design",
                        "BACK_IMPLEMENT": "implement",
                        "ABORT": "end",
                    },
                ),
            ],
        )

        validate_workflow(wf)

    @pytest.mark.small
    def test_back_prefix_empty_suffix_rejected(self) -> None:
        """BACK_ alone (no suffix) is rejected."""
        wf = _workflow(
            steps=[
                _step("step_a", on={"PASS": "end", "BACK_": "end"}),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("BACK_" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_back_prefix_lowercase_suffix_rejected(self) -> None:
        """BACK_design (lowercase suffix) is rejected to keep validator aligned with the
        relaxed verdict parser's uppercase normalization."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="claude", on={"PASS": "step_b", "ABORT": "end"}),
                _step("step_b", agent="claude", on={"PASS": "end", "BACK_design": "step_a"}),
            ],
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(wf)

        assert any("BACK_design" in e for e in exc_info.value.errors)

    @pytest.mark.small
    def test_back_prefix_unknown_suffix_accepted(self) -> None:
        """BACK_FOO (unknown root-cause) is formally accepted (prefix-based design)."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="claude", on={"PASS": "step_b", "ABORT": "end"}),
                _step("step_b", agent="claude", on={"PASS": "end", "BACK_FOO": "step_a"}),
            ],
        )

        validate_workflow(wf)

    @pytest.mark.small
    def test_base_verdicts_still_accepted(self) -> None:
        """Existing PASS/RETRY/BACK/ABORT continues to be accepted (backward compat)."""
        wf = _workflow(
            steps=[
                _step("step_a", agent="claude", on={"PASS": "step_b", "ABORT": "end"}),
                _step(
                    "step_b",
                    agent="claude",
                    on={"PASS": "end", "RETRY": "step_b", "BACK": "step_a", "ABORT": "end"},
                ),
            ],
        )

        validate_workflow(wf)


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

    @pytest.mark.small
    def test_cycle_on_exhaust_accepts_back_prefix(self) -> None:
        """on_exhaust accepts BACK_* prefixed verdicts (shared judgment with step.on)."""
        wf = _workflow(
            steps=[
                _step("impl", agent="claude", on={"PASS": "review", "ABORT": "end"}),
                _step(
                    "review",
                    agent="codex",
                    on={"PASS": "end", "RETRY": "impl"},
                ),
            ],
            cycles=[
                CycleDefinition(
                    name="impl-loop",
                    entry="impl",
                    loop=["impl", "review"],
                    max_iterations=3,
                    on_exhaust="BACK_DESIGN",
                ),
            ],
        )

        validate_workflow(wf)


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

    @pytest.mark.small
    def test_new_rule_errors_are_collected_in_one_exception(self) -> None:
        """Agent, PASS, and reachability violations are reported together."""
        workflow = _workflow(
            [
                _step("root", agent="cladue", on={"ABORT": "end"}),
                _step("orphan", on={"PASS": "end"}),
            ]
        )

        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_workflow(workflow)

        assert (
            "Step 'root' has unknown agent 'cladue' "
            "(allowed: ['claude', 'codex', 'gemini'])" in exc_info.value.errors
        )
        assert "Step 'root' 'on' must define a 'PASS' transition" in exc_info.value.errors
        assert "Step 'orphan' is not reachable from the first step 'root'" in exc_info.value.errors
