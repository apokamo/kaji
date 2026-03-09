"""Tests for YAML workflow parsing.

Covers load_workflow_from_str, load_workflow, and Workflow helper methods.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dao_harness.workflow import load_workflow, load_workflow_from_str

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
    def test_invalid_yaml_raises_error(self) -> None:
        """Malformed YAML raises an appropriate error."""
        bad_yaml = "name: test\nsteps:\n  - id: [unclosed"

        with pytest.raises((ValueError, TypeError, KeyError, Exception)):  # noqa: B017
            load_workflow_from_str(bad_yaml)
