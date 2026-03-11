"""Tests for issue #63: skill harness adaptation.

Validates that all skills have been properly adapted for dual-mode
(manual + harness) operation, and that the workflow YAML is valid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.skill import validate_skill_exists
from kaji_harness.verdict import parse_verdict
from kaji_harness.workflow import load_workflow, validate_workflow

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

    def test_fix_code_has_no_resume(self) -> None:
        """fix-code uses a separate agent (codex for review), so resume is removed
        to avoid context bloat from long implement sessions."""
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        step = workflow.find_step("fix-code")
        assert step is not None
        assert step.resume is None, "fix-code must not have resume (separate review agent)"


@pytest.mark.medium
class TestSkillVerdictParseable:
    """Read actual SKILL.md files and verify their verdict examples parse correctly.

    Validates the chain: filesystem → skill content → verdict parser.
    This is Medium (file I/O + internal service integration).
    """

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_skill_verdict_example_is_parseable(self, skill_name: str) -> None:
        """Read each skill's SKILL.md and verify its verdict example parses."""
        import re

        content = _read_skill(skill_name)

        match = re.search(
            r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
            content,
            re.DOTALL,
        )
        assert match is not None, f"{skill_name} verdict example block not found in SKILL.md"

        verdict_text = f"---VERDICT---\n{match.group(1)}\n---END_VERDICT---"

        workflow = load_workflow(WORKFLOW_YAML_PATH)
        step = workflow.find_step(skill_name.replace("issue-", ""))
        assert step is not None, f"Step for {skill_name} not found in workflow"

        valid_statuses = set(step.on.keys())

        verdict = parse_verdict(verdict_text, valid_statuses)
        assert verdict.status in valid_statuses
        assert verdict.reason != ""
        assert verdict.evidence != ""


@pytest.mark.medium
class TestWorkflowTransitions:
    """Validate all workflow step transitions are reachable."""

    def test_all_transitions_reachable(self) -> None:
        workflow = load_workflow(WORKFLOW_YAML_PATH)
        validate_workflow(workflow)

        for step in workflow.steps:
            validate_skill_exists(step.skill, step.agent, PROJECT_ROOT)

        assert workflow.steps[0].id == "design"

        last_step = workflow.steps[-1]
        assert "end" in last_step.on.values(), (
            f"Last step '{last_step.id}' should transition to 'end'"
        )

        step_ids = {s.id for s in workflow.steps} | {"end"}
        for step in workflow.steps:
            for verdict, target in step.on.items():
                assert target in step_ids, (
                    f"Step '{step.id}' on {verdict} targets '{target}' which doesn't exist"
                )


# ============================================================
# Large Tests: E2E workflow + skill execution
# ============================================================


@pytest.mark.large
class TestSingleStepE2E:
    """E2E test: `kaji run --step <step-id>` single-step execution + verdict parse.

    Skipped: physically impossible to implement.
    1. `kaji` CLI entry point is not implemented (pyproject.toml [project.scripts] commented out)
    2. Single-step execution requires WorkflowRunner → execute_cli() →
       subprocess.Popen(["claude", ...]), which needs a live AI agent process + API key.
       This cannot be configured in CI.
    """

    @pytest.mark.skip(
        reason="kaji CLI entry point not implemented and agent subprocess requires live API key"
    )
    def test_single_step_verdict_parse(self) -> None:
        """Run a single workflow step via `kaji run --step` and verify verdict is parsed."""
