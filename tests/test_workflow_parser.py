"""Tests for YAML workflow parsing.

Covers load_workflow_from_str, load_workflow, and Workflow helper methods.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dao_harness.errors import WorkflowValidationError
from dao_harness.workflow import load_workflow, load_workflow_from_str, validate_workflow

# ============================================================
# Shared YAML fixtures (embedded strings)
# ============================================================

MINIMAL_WORKFLOW_YAML = dedent("""\
    name: minimal-wf
    description: A minimal two-step workflow
    steps:
      - id: step_a
        skill: analyse
        agent: claude
      - id: step_b
        skill: review
        agent: codex
        on:
          PASS: end
          RETRY: step_a
""")

FULL_WORKFLOW_YAML = dedent("""\
    name: full-wf
    description: Full workflow with cycles
    execution_policy: auto
    steps:
      - id: design
        skill: design
        agent: claude
        model: gemini-2.5-pro
        effort: high
        max_budget_usd: 5.0
        max_turns: 10
        timeout: 600
        on:
          PASS: implement
          ABORT: end
      - id: implement
        skill: implement
        agent: claude
        model: claude-sonnet-4-20250514
        resume: design
        on:
          PASS: review
          ABORT: end
      - id: review
        skill: review
        agent: codex
        on:
          PASS: end
          RETRY: implement
    cycles:
      impl-loop:
        entry: implement
        loop:
          - implement
          - review
        max_iterations: 3
        on_exhaust: ABORT
""")


# ============================================================
# Test class: Parsing
# ============================================================


class TestWorkflowParsing:
    """Tests for load_workflow_from_str basic parsing."""

    @pytest.mark.small
    def test_parse_minimal_workflow(self) -> None:
        """Parse valid minimal workflow with 2 steps and no cycles."""
        wf = load_workflow_from_str(MINIMAL_WORKFLOW_YAML)

        assert wf.name == "minimal-wf"
        assert wf.description == "A minimal two-step workflow"
        assert len(wf.steps) == 2
        assert wf.steps[0].id == "step_a"
        assert wf.steps[1].id == "step_b"

    @pytest.mark.small
    def test_parse_full_workflow_with_cycles(self) -> None:
        """Parse workflow containing steps and cycle definitions."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        assert wf.name == "full-wf"
        assert len(wf.steps) == 3
        assert len(wf.cycles) == 1
        assert wf.cycles[0].name == "impl-loop"
        assert wf.cycles[0].entry == "implement"
        assert wf.cycles[0].loop == ["implement", "review"]
        assert wf.cycles[0].max_iterations == 3
        assert wf.cycles[0].on_exhaust == "ABORT"

    @pytest.mark.small
    def test_all_step_fields_parsed(self) -> None:
        """All optional step fields (model, effort, max_budget_usd, etc.) are parsed."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        design = wf.find_step("design")
        assert design is not None
        assert design.model == "gemini-2.5-pro"
        assert design.effort == "high"
        assert design.max_budget_usd == 5.0
        assert design.max_turns == 10
        assert design.timeout == 600

        impl = wf.find_step("implement")
        assert impl is not None
        assert impl.resume == "design"
        assert impl.on == {"PASS": "review", "ABORT": "end"}

    @pytest.mark.small
    def test_execution_policy_defaults_to_auto(self) -> None:
        """execution_policy defaults to 'auto' when omitted from YAML."""
        wf = load_workflow_from_str(MINIMAL_WORKFLOW_YAML)

        assert wf.execution_policy == "auto"

    @pytest.mark.small
    def test_optional_step_fields_default_to_none(self) -> None:
        """Optional step fields default to None when not specified."""
        wf = load_workflow_from_str(MINIMAL_WORKFLOW_YAML)

        step_a = wf.find_step("step_a")
        assert step_a is not None
        assert step_a.model is None
        assert step_a.effort is None
        assert step_a.max_budget_usd is None
        assert step_a.max_turns is None
        assert step_a.timeout is None
        assert step_a.resume is None

    @pytest.mark.small
    def test_empty_cycles_when_no_cycles_section(self) -> None:
        """cycles list is empty when YAML has no cycles section."""
        wf = load_workflow_from_str(MINIMAL_WORKFLOW_YAML)

        assert wf.cycles == []

    @pytest.mark.small
    def test_yaml_1_1_on_key_parsed_correctly(self) -> None:
        """YAML 1.1 interprets bare 'on' as True; parser handles this fallback."""
        yaml_str = dedent("""\
            name: test
            steps:
              - id: step1
                skill: s
                agent: claude
                on:
                  PASS: end
        """)
        wf = load_workflow_from_str(yaml_str)

        step = wf.find_step("step1")
        assert step is not None
        assert step.on == {"PASS": "end"}


# ============================================================
# Test class: Workflow helper methods
# ============================================================


class TestWorkflowHelpers:
    """Tests for Workflow.find_step, find_start_step, find_cycle_for_step."""

    @pytest.mark.small
    def test_find_step_returns_correct_step(self) -> None:
        """find_step returns the Step with the matching id."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        step = wf.find_step("review")
        assert step is not None
        assert step.id == "review"
        assert step.agent == "codex"

    @pytest.mark.small
    def test_find_step_returns_none_for_unknown(self) -> None:
        """find_step returns None when step id does not exist."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        assert wf.find_step("nonexistent") is None

    @pytest.mark.small
    def test_find_start_step_returns_first_step(self) -> None:
        """find_start_step returns the first step in the list."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        start = wf.find_start_step()
        assert start.id == "design"

    @pytest.mark.small
    def test_find_cycle_for_step_entry(self) -> None:
        """find_cycle_for_step returns cycle when step is the entry."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        cycle = wf.find_cycle_for_step("implement")
        assert cycle is not None
        assert cycle.name == "impl-loop"

    @pytest.mark.small
    def test_find_cycle_for_step_in_loop(self) -> None:
        """find_cycle_for_step returns cycle when step is in the loop list."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        cycle = wf.find_cycle_for_step("review")
        assert cycle is not None
        assert cycle.name == "impl-loop"

    @pytest.mark.small
    def test_find_cycle_for_step_returns_none_for_unrelated(self) -> None:
        """find_cycle_for_step returns None for a step not in any cycle."""
        wf = load_workflow_from_str(FULL_WORKFLOW_YAML)

        assert wf.find_cycle_for_step("design") is None


# ============================================================
# Test class: File-based loading
# ============================================================


class TestFileBasedLoading:
    """Tests for load_workflow (file path-based)."""

    @pytest.mark.small
    def test_load_workflow_from_file(self, tmp_path: Path) -> None:
        """load_workflow reads and parses a YAML file from disk."""
        wf_file = tmp_path / "workflow.yaml"
        wf_file.write_text(MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        wf = load_workflow(wf_file)

        assert wf.name == "minimal-wf"
        assert len(wf.steps) == 2


# ============================================================
# Test class: Error handling
# ============================================================


class TestParsingErrors:
    """Tests for error handling in load_workflow_from_str."""

    @pytest.mark.small
    def test_invalid_yaml_syntax_raises_validation_error(self) -> None:
        """Malformed YAML syntax raises WorkflowValidationError."""
        bad_yaml = "name: test\nsteps:\n  - id: [unclosed"

        with pytest.raises(WorkflowValidationError, match="YAML parse error"):
            load_workflow_from_str(bad_yaml)

    @pytest.mark.small
    def test_non_mapping_root_raises_validation_error(self) -> None:
        """Non-mapping root (e.g. a list) raises WorkflowValidationError."""
        with pytest.raises(WorkflowValidationError, match="must be a YAML mapping"):
            load_workflow_from_str("- item1\n- item2")

    @pytest.mark.small
    def test_steps_null_raises_validation_error(self) -> None:
        """steps: null raises WorkflowValidationError."""
        yaml_str = "name: test\nsteps: null"

        with pytest.raises(WorkflowValidationError, match="'steps' must be a list, got null"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_steps_not_a_list_raises_validation_error(self) -> None:
        """steps as a scalar raises WorkflowValidationError."""
        yaml_str = "name: test\nsteps: not-a-list"

        with pytest.raises(WorkflowValidationError, match="'steps' must be a list"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_step_not_mapping_raises_validation_error(self) -> None:
        """Step item that is a plain string raises WorkflowValidationError."""
        yaml_str = "name: test\nsteps:\n  - just-a-string"

        with pytest.raises(WorkflowValidationError, match="Step at index 0 must be a mapping"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_step_missing_id_raises_validation_error(self) -> None:
        """Step missing 'id' raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            steps:
              - skill: s
                agent: claude
        """)

        with pytest.raises(WorkflowValidationError, match="missing required key.*id"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_step_missing_skill_raises_validation_error(self) -> None:
        """Step missing 'skill' raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            steps:
              - id: step1
                agent: claude
        """)

        with pytest.raises(WorkflowValidationError, match="missing required key.*skill"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_step_missing_agent_raises_validation_error(self) -> None:
        """Step missing 'agent' raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            steps:
              - id: step1
                skill: s
        """)

        with pytest.raises(WorkflowValidationError, match="missing required key.*agent"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_step_missing_multiple_keys_reports_all(self) -> None:
        """Step missing multiple required keys reports all missing keys."""
        yaml_str = dedent("""\
            name: test
            steps:
              - skill: s
        """)

        with pytest.raises(WorkflowValidationError, match="id.*agent"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_invalid_execution_policy_raises_validation_error(self) -> None:
        """Typo in execution_policy raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            execution_policy: yolo
            steps:
              - id: step1
                skill: s
                agent: claude
        """)

        with pytest.raises(WorkflowValidationError, match="execution_policy must be one of"):
            load_workflow_from_str(yaml_str)

    @pytest.mark.small
    def test_valid_execution_policies_accepted(self) -> None:
        """All valid execution_policy values are accepted."""
        for policy in ("auto", "sandbox", "interactive"):
            yaml_str = dedent(f"""\
                name: test
                execution_policy: {policy}
                steps:
                  - id: step1
                    skill: s
                    agent: claude
            """)
            wf = load_workflow_from_str(yaml_str)
            assert wf.execution_policy == policy

    @pytest.mark.small
    def test_cycle_missing_required_keys_raises_validation_error(self) -> None:
        """Cycle missing required keys raises WorkflowValidationError."""
        yaml_str = dedent("""\
            name: test
            steps:
              - id: step1
                skill: s
                agent: claude
            cycles:
              my-cycle:
                entry: step1
        """)

        with pytest.raises(WorkflowValidationError, match="Cycle 'my-cycle' missing required"):
            load_workflow_from_str(yaml_str)


class TestValidationErrors:
    """Tests for validate_workflow catching structural issues."""

    @pytest.mark.small
    def test_empty_steps_raises_validation_error(self) -> None:
        """Workflow with steps: [] raises WorkflowValidationError on validation."""
        yaml_str = "name: test\nsteps: []"
        wf = load_workflow_from_str(yaml_str)

        with pytest.raises(WorkflowValidationError, match="at least one step"):
            validate_workflow(wf)

    @pytest.mark.small
    def test_empty_cycle_loop_raises_validation_error(self) -> None:
        """Cycle with loop: [] raises WorkflowValidationError on validation."""
        yaml_str = dedent("""\
            name: test
            steps:
              - id: step1
                skill: s
                agent: claude
                on:
                  PASS: end
            cycles:
              my-cycle:
                entry: step1
                loop: []
                max_iterations: 3
                on_exhaust: ABORT
        """)
        wf = load_workflow_from_str(yaml_str)

        with pytest.raises(WorkflowValidationError, match="loop must not be empty"):
            validate_workflow(wf)
