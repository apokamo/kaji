"""Tests for skill harness adaptation.

Validates that:
- All skills have proper SKILL.md structure (verdict blocks, dual-mode input)
- Workflow engine correctly parses and validates fixture YAML
- inject_verdict field is correctly parsed from YAML

Engine logic tests use fixture YAML (tests/fixtures/test_workflow.yaml),
NOT the production workflow. Production YAML validation is handled by
`kaji validate`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.verdict import parse_verdict
from kaji_harness.workflow import load_workflow, validate_workflow

# ============================================================
# Constants
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FIXTURE_WORKFLOW_PATH = Path(__file__).resolve().parent / "fixtures" / "test_workflow.yaml"

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


# ============================================================
# Medium Tests: Workflow engine logic (using fixture YAML)
# ============================================================


@pytest.mark.medium
class TestFixtureWorkflowParseable:
    """Fixture workflow YAML must be parseable by load_workflow."""

    def test_yaml_loads_without_error(self) -> None:
        assert FIXTURE_WORKFLOW_PATH.exists(), f"Fixture YAML not found at {FIXTURE_WORKFLOW_PATH}"
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        assert workflow.name != ""
        assert len(workflow.steps) > 0


@pytest.mark.medium
class TestFixtureWorkflowValidation:
    """Fixture workflow YAML must pass validate_workflow."""

    def test_workflow_validates(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        validate_workflow(workflow)

    def test_workflow_has_review_cycle(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        cycle_names = [c.name for c in workflow.cycles]
        assert "review-cycle" in cycle_names

    def test_review_cycle_integrity(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        cycle = workflow.find_cycle_for_step("review")
        assert cycle is not None
        assert cycle.entry == "review"
        assert "fix" in cycle.loop
        assert "verify" in cycle.loop
        assert cycle.max_iterations == 3


@pytest.mark.medium
class TestFixtureWorkflowInjectVerdict:
    """inject_verdict field must be correctly parsed from fixture YAML."""

    def test_fix_step_has_inject_verdict_true(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        step = workflow.find_step("fix")
        assert step is not None
        assert step.inject_verdict is True, "fix step must have inject_verdict: true"

    def test_design_step_has_inject_verdict_false_by_default(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        step = workflow.find_step("design")
        assert step is not None
        assert step.inject_verdict is False, "design step should default inject_verdict to False"


@pytest.mark.medium
class TestFixtureWorkflowTransitions:
    """Validate fixture workflow step transitions are reachable."""

    def test_all_transitions_reachable(self) -> None:
        workflow = load_workflow(FIXTURE_WORKFLOW_PATH)
        validate_workflow(workflow)

        step_ids = {s.id for s in workflow.steps} | {"end"}
        for step in workflow.steps:
            for verdict, target in step.on.items():
                assert target in step_ids, (
                    f"Step '{step.id}' on {verdict} targets '{target}' which doesn't exist"
                )


@pytest.mark.medium
class TestSkillVerdictParseable:
    """Read actual SKILL.md files and verify their verdict examples parse correctly.

    Uses a minimal step definition for valid_statuses instead of production YAML.
    """

    # Mapping from skill name to valid statuses (derived from standard verdict values)
    _SKILL_STATUSES: dict[str, set[str]] = {
        "issue-design": {"PASS", "ABORT"},
        "issue-review-design": {"PASS", "RETRY", "ABORT"},
        "issue-fix-design": {"PASS", "ABORT"},
        "issue-verify-design": {"PASS", "RETRY", "ABORT"},
        "issue-implement": {"PASS", "RETRY", "BACK", "ABORT"},
        "issue-review-code": {"PASS", "RETRY", "BACK", "ABORT"},
        "issue-fix-code": {"PASS", "ABORT"},
        "issue-verify-code": {"PASS", "RETRY", "ABORT"},
        "issue-doc-check": {"PASS"},
        "issue-pr": {"PASS", "RETRY", "ABORT"},
        "issue-close": {"PASS", "RETRY", "ABORT"},
    }

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
        valid_statuses = self._SKILL_STATUSES[skill_name]

        verdict = parse_verdict(verdict_text, valid_statuses)
        assert verdict.status in valid_statuses
        assert verdict.reason != ""
        assert verdict.evidence != ""


# ============================================================
# Large Tests: E2E workflow + skill execution
# ============================================================


@pytest.mark.large
class TestSingleStepE2E:
    """E2E test: `kaji run --step <step-id>` single-step execution + verdict parse.

    Skipped: physically impossible to implement.
    Single-step execution requires WorkflowRunner -> execute_cli() ->
    subprocess.Popen(["claude", ...]), which needs a live AI agent process + API key.
    This cannot be configured in CI.
    """

    @pytest.mark.skip(reason="Agent subprocess requires live API key — physically impossible in CI")
    def test_single_step_verdict_parse(self) -> None:
        """Run a single workflow step via `kaji run --step` and verify verdict is parsed."""
