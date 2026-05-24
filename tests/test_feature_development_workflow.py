"""Tests for feature-development.yaml workflow structure.

Verifies:
- Workflow stops at PR creation (no auto close step). Issue: #93
- final-check splits BACK into BACK_DESIGN / BACK_IMPLEMENT for root-cause
  routing. Issue: #158

Both the legacy `workflows/feature-development.yaml` and the builtin
`.kaji/wf/feature-development.yaml` are validated in parallel via
pytest.mark.parametrize to guarantee structural parity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.cli_main import main
from kaji_harness.workflow import load_workflow, validate_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURE_WORKFLOW_PATHS = [
    REPO_ROOT / "workflows" / "feature-development.yaml",
    REPO_ROOT / ".kaji" / "wf" / "feature-development.yaml",
]
WORKFLOW_IDS = ["legacy", "builtin"]


# ============================================================
# Small tests — YAML structure verification
# ============================================================


class TestFeatureDevelopmentWorkflowSmall:
    """Small: verify workflow YAML structure (parametrized over legacy/builtin)."""

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_pr_step_pass_transitions_to_end(self, path: Path) -> None:
        """pr step PASS should transition to 'end', not 'close'."""
        wf = load_workflow(path)
        pr_step = wf.find_step("pr")
        assert pr_step is not None
        assert pr_step.on["PASS"] == "end"

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_close_step_does_not_exist(self, path: Path) -> None:
        """close step should not exist in the workflow."""
        wf = load_workflow(path)
        assert wf.find_step("close") is None

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_description_says_pr_creation(self, path: Path) -> None:
        """description should reference PR creation, not PR close."""
        wf = load_workflow(path)
        assert "PR 作成まで" in wf.description
        assert "クローズ" not in wf.description

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_workflow_passes_validation(self, path: Path) -> None:
        """Workflow must pass validate_workflow."""
        wf = load_workflow(path)
        validate_workflow(wf)

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_final_check_back_design_maps_to_design(self, path: Path) -> None:
        """final-check BACK_DESIGN routes directly to design (root-cause split)."""
        wf = load_workflow(path)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert fc.on["BACK_DESIGN"] == "design"

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_final_check_back_implement_maps_to_implement(self, path: Path) -> None:
        """final-check BACK_IMPLEMENT routes directly to implement (root-cause split)."""
        wf = load_workflow(path)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert fc.on["BACK_IMPLEMENT"] == "implement"

    @pytest.mark.small
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_final_check_has_no_plain_back(self, path: Path) -> None:
        """final-check must not carry the legacy plain `BACK` key."""
        wf = load_workflow(path)
        fc = wf.find_step("final-check")
        assert fc is not None
        assert "BACK" not in fc.on


# ============================================================
# Medium tests — CLI validation integration
# ============================================================


class TestFeatureDevelopmentWorkflowMedium:
    """Medium: validate via CLI with real file I/O."""

    @pytest.mark.medium
    @pytest.mark.parametrize("path", FEATURE_WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_kaji_validate_feature_development(self, path: Path) -> None:
        """kaji validate should pass for both workflow files."""
        exit_code = main(["validate", str(path)])
        assert exit_code == 0


# ============================================================
# Large tests — /kaji-run-verify による実機検証
# ============================================================
# Large 検証は pytest ではなく /kaji-run-verify による手動実行で実施する。
# 検証コマンド: /kaji-run-verify .kaji/wf/feature-development.yaml <issue>
# 検証結果は Issue コメントとして記録される。
# 設計書: draft/design/issue-93-workflow-stop-at-pr.md / issue-158-final-check-back-split.md
