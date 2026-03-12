"""Tests for feature-development.yaml workflow structure.

Verifies the workflow stops at PR creation (no auto close step).
Issue: #93
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness.cli_main import main
from kaji_harness.workflow import load_workflow, validate_workflow

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / "workflows" / "feature-development.yaml"


# ============================================================
# Small tests — YAML structure verification
# ============================================================


class TestFeatureDevelopmentWorkflowSmall:
    """Small: verify workflow YAML structure after #93 changes."""

    @pytest.mark.small
    def test_pr_step_pass_transitions_to_end(self) -> None:
        """pr step PASS should transition to 'end', not 'close'."""
        wf = load_workflow(WORKFLOW_PATH)
        pr_step = wf.find_step("pr")
        assert pr_step is not None
        assert pr_step.on["PASS"] == "end"

    @pytest.mark.small
    def test_close_step_does_not_exist(self) -> None:
        """close step should not exist in the workflow."""
        wf = load_workflow(WORKFLOW_PATH)
        assert wf.find_step("close") is None

    @pytest.mark.small
    def test_description_says_pr_creation(self) -> None:
        """description should reference PR creation, not PR close."""
        wf = load_workflow(WORKFLOW_PATH)
        assert "PR 作成まで" in wf.description
        assert "クローズ" not in wf.description

    @pytest.mark.small
    def test_workflow_passes_validation(self) -> None:
        """Changed workflow must pass validate_workflow."""
        wf = load_workflow(WORKFLOW_PATH)
        validate_workflow(wf)


# ============================================================
# Medium tests — CLI validation integration
# ============================================================


class TestFeatureDevelopmentWorkflowMedium:
    """Medium: validate via CLI with real file I/O."""

    @pytest.mark.medium
    def test_kaji_validate_feature_development(self) -> None:
        """kaji validate should pass for the modified workflow."""
        exit_code = main(["validate", str(WORKFLOW_PATH)])
        assert exit_code == 0


# ============================================================
# Large tests — subprocess execution
# ============================================================


class TestFeatureDevelopmentWorkflowLarge:
    """Large: real subprocess execution of kaji validate."""

    @pytest.mark.large
    def test_kaji_validate_subprocess(self) -> None:
        """kaji validate via subprocess should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "validate", str(WORKFLOW_PATH)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "✓" in result.stdout
