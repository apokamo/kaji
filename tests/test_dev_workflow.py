"""Tests for .kaji/wf/official/dev.yaml workflow structure.

Verifies:
- final-check splits BACK into BACK_DESIGN / BACK_IMPLEMENT for root-cause
  routing. Issue: #158

The legacy feature-development workflows were consolidated into the 5-file
operation (dev / dev-thorough / docs / dev-local / docs-local) in Issue #247.
The structural guarantee that remains meaningful — the #158 final-check
BACK_DESIGN / BACK_IMPLEMENT split — is asserted here against dev.yaml.

The historical "stop at PR creation, no close step" assertions (#93) are
dropped: dev.yaml now runs through review-poll to close, and stopping before
PR review is expressed by
`kaji run .kaji/wf/official/dev.yaml <id> --before review-poll` instead of a
dedicated workflow YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.commands.main import main
from kaji_harness.workflow import load_workflow, validate_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV_WORKFLOW_PATH = REPO_ROOT / ".kaji" / "wf" / "official" / "dev.yaml"


# ============================================================
# Medium tests — YAML structure verification (reads the real dev.yaml)
# ============================================================


class TestDevWorkflowStructure:
    """Medium: verify official dev.yaml workflow structure."""

    @pytest.mark.medium
    def test_workflow_passes_validation(self) -> None:
        """Workflow must pass validate_workflow."""
        wf = load_workflow(DEV_WORKFLOW_PATH)
        validate_workflow(wf)

    @pytest.mark.medium
    def test_final_check_back_design_maps_to_design(self) -> None:
        """final-check BACK_DESIGN routes directly to design (root-cause split)."""
        wf = load_workflow(DEV_WORKFLOW_PATH)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert fc.on["BACK_DESIGN"] == "design"

    @pytest.mark.medium
    def test_final_check_back_implement_maps_to_implement(self) -> None:
        """final-check BACK_IMPLEMENT routes directly to implement (root-cause split)."""
        wf = load_workflow(DEV_WORKFLOW_PATH)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert fc.on["BACK_IMPLEMENT"] == "implement"

    @pytest.mark.medium
    def test_final_check_has_no_plain_back(self) -> None:
        """final-check must not carry the legacy plain `BACK` key."""
        wf = load_workflow(DEV_WORKFLOW_PATH)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert "BACK" not in fc.on


# ============================================================
# Medium tests — CLI validation integration
# ============================================================


class TestDevWorkflowMedium:
    """Medium: validate via CLI with real file I/O."""

    @pytest.mark.medium
    def test_kaji_validate_dev(self) -> None:
        """kaji validate should pass for dev.yaml."""
        exit_code = main(["validate", str(DEV_WORKFLOW_PATH)])
        assert exit_code == 0
