"""Tests for issue #63: skill harness adaptation.

Validates that all skills have been properly adapted for dual-mode
(manual + harness) operation, and that the workflow YAML is valid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dao_harness.skill import validate_skill_exists
from dao_harness.verdict import parse_verdict
from dao_harness.workflow import load_workflow, validate_workflow

# ============================================================
# Constants
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORKFLOW_SKILLS = [
    "issue-design",
    "issue-review-design",
    "issue-fix-design",
    "issue-verify-design",
    "issue-implement",
    "issue-review-code",
    "issue-fix-code",
    "issue-verify-code",
    "issue-doc-check",
    "issue-pr",
    "issue-close",
]

MANUAL_ONLY_SKILLS = [
    "issue-create",
    "issue-start",
]

ALL_SKILLS = WORKFLOW_SKILLS + MANUAL_ONLY_SKILLS

FIX_SKILLS = ["issue-fix-code", "issue-fix-design"]

HARDCODE_PATH = "bugfix_agent/"

WORKFLOW_YAML_PATH = PROJECT_ROOT / "workflows" / "feature-development.yaml"


def _read_skill(skill_name: str) -> str:
    """Read a skill's SKILL.md content."""
    path = PROJECT_ROOT / ".claude" / "skills" / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8")


# ============================================================
# Small Tests: SKILL.md structure validation
# ============================================================


@pytest.mark.small
class TestVerdictSectionExists:
    """All skills must have a verdict output section."""

    @pytest.mark.parametrize("skill_name", ALL_SKILLS)
    def test_skill_has_verdict_section(self, skill_name: str) -> None:
        content = _read_skill(skill_name)
        assert "---VERDICT---" in content, f"{skill_name} missing ---VERDICT--- block"
        assert "---END_VERDICT---" in content, f"{skill_name} missing ---END_VERDICT--- block"


@pytest.mark.small
class TestInputSectionDualMode:
    """Workflow skills must have both context variables and $ARGUMENTS."""

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_workflow_skill_has_context_variables(self, skill_name: str) -> None:
        content = _read_skill(skill_name)
        assert "issue_number" in content, f"{skill_name} missing issue_number context variable"

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_workflow_skill_has_arguments(self, skill_name: str) -> None:
        content = _read_skill(skill_name)
        assert "$ARGUMENTS" in content, f"{skill_name} missing $ARGUMENTS for manual mode"


@pytest.mark.small
class TestManualSkillsPreserved:
    """Manual-only skills should keep their existing $ARGUMENTS format."""

    def test_issue_create_has_title_argument(self) -> None:
        content = _read_skill("issue-create")
        assert "title" in content.lower()
        assert "$ARGUMENTS" in content

    def test_issue_start_has_prefix_argument(self) -> None:
        content = _read_skill("issue-start")
        assert "prefix" in content.lower()
        assert "$ARGUMENTS" in content


@pytest.mark.small
class TestNoHardcodedPaths:
    """No skill should contain hardcoded 'bugfix_agent/' path."""

    @pytest.mark.parametrize("skill_name", ALL_SKILLS)
    def test_no_bugfix_agent_hardcode(self, skill_name: str) -> None:
        content = _read_skill(skill_name)
        assert HARDCODE_PATH not in content, (
            f"{skill_name} still contains hardcoded '{HARDCODE_PATH}'"
        )


@pytest.mark.small
class TestPreviousVerdictFallback:
    """Fix skills must reference previous_verdict with fallback."""

    @pytest.mark.parametrize("skill_name", FIX_SKILLS)
    def test_fix_skill_has_previous_verdict_reference(self, skill_name: str) -> None:
        content = _read_skill(skill_name)
        assert "previous_verdict" in content, f"{skill_name} missing previous_verdict reference"


@pytest.mark.small
class TestWorkflowYamlParseable:
    """Workflow YAML must be parseable by load_workflow."""

    def test_yaml_loads_without_error(self) -> None:
        assert WORKFLOW_YAML_PATH.exists(), f"Workflow YAML not found at {WORKFLOW_YAML_PATH}"
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        assert workflow.name != ""
        assert len(workflow.steps) > 0


# ============================================================
# Medium Tests: Workflow validation and skill resolution
# ============================================================


@pytest.mark.medium
class TestWorkflowValidation:
    """Workflow YAML must pass validate_workflow."""

    def test_workflow_validates(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        # Should not raise
        validate_workflow(workflow)

    def test_workflow_has_design_review_cycle(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        cycle_names = [c.name for c in workflow.cycles]
        assert "design-review" in cycle_names

    def test_workflow_has_code_review_cycle(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        cycle_names = [c.name for c in workflow.cycles]
        assert "code-review" in cycle_names

    def test_design_review_cycle_integrity(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        cycle = workflow.find_cycle_for_step("review-design")
        assert cycle is not None
        assert cycle.entry == "review-design"
        assert "fix-design" in cycle.loop
        assert "verify-design" in cycle.loop
        assert cycle.max_iterations == 3

    def test_code_review_cycle_integrity(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        cycle = workflow.find_cycle_for_step("review-code")
        assert cycle is not None
        assert cycle.entry == "review-code"
        assert "fix-code" in cycle.loop
        assert "verify-code" in cycle.loop
        assert cycle.max_iterations == 3


@pytest.mark.medium
class TestWorkflowSkillsExist:
    """All skills referenced in workflow YAML must exist on filesystem."""

    def test_all_workflow_skills_exist(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        for step in workflow.steps:
            validate_skill_exists(step.skill, step.agent, PROJECT_ROOT)


@pytest.mark.medium
class TestWorkflowResumeConfig:
    """Fix steps must have resume configured for previous_verdict injection."""

    def test_fix_design_has_resume(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        step = workflow.find_step("fix-design")
        assert step is not None
        assert step.resume is not None, "fix-design must have resume configured"

    def test_fix_code_has_resume(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        step = workflow.find_step("fix-code")
        assert step is not None
        assert step.resume is not None, "fix-code must have resume configured"


# ============================================================
# Large Tests: E2E workflow + skill execution
# ============================================================


@pytest.mark.large
class TestSkillVerdictParseE2E:
    """E2E test: read actual SKILL.md files and verify their verdict examples parse correctly.

    This validates the full chain: filesystem → skill content → verdict parser,
    ensuring that the verdict blocks embedded in each skill are structurally valid
    and can be parsed by the harness verdict parser.

    Note: `dao run --step` E2E is not possible because the `dao` CLI entry point
    is not yet implemented (pyproject.toml [project.scripts] is commented out).
    """

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_skill_verdict_example_is_parseable(self, skill_name: str) -> None:
        """Read each skill's SKILL.md and verify its verdict example parses."""
        content = _read_skill(skill_name)

        # Extract the verdict example from the skill content
        # Skills should have a verdict block in their "Verdict 出力" section
        import re

        match = re.search(
            r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
            content,
            re.DOTALL,
        )
        assert match is not None, f"{skill_name} verdict example block not found in SKILL.md"

        # Build the full verdict block as it would appear in CLI output
        verdict_text = f"---VERDICT---\n{match.group(1)}\n---END_VERDICT---"

        # Get valid statuses for this skill from the workflow YAML
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        step = workflow.find_step(skill_name.replace("issue-", ""))
        assert step is not None, f"Step for {skill_name} not found in workflow"

        valid_statuses = set(step.on.keys())

        # Parse the verdict — this exercises the full verdict parser
        verdict = parse_verdict(verdict_text, valid_statuses)
        assert verdict.status in valid_statuses
        assert verdict.reason != ""
        assert verdict.evidence != ""

    def test_full_workflow_load_validate_and_transitions(self) -> None:
        """Load, validate, and verify all step transitions are reachable."""
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        validate_workflow(workflow)

        # Verify all skills exist on filesystem
        for step in workflow.steps:
            validate_skill_exists(step.skill, step.agent, PROJECT_ROOT)

        # Verify first step is design
        assert workflow.steps[0].id == "design"

        # Verify last step transitions to end on PASS
        last_step = workflow.steps[-1]
        assert "end" in last_step.on.values(), (
            f"Last step '{last_step.id}' should transition to 'end'"
        )

        # Verify all on targets are reachable
        step_ids = {s.id for s in workflow.steps} | {"end"}
        for step in workflow.steps:
            for verdict, target in step.on.items():
                assert target in step_ids, (
                    f"Step '{step.id}' on {verdict} targets '{target}' which doesn't exist"
                )
