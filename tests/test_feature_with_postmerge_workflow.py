"""Tests for feature-with-postmerge.yaml workflow.

Verifies the post-merge tail (`pr → wait-merge → verify-main-green →
post-merge-review → post-merge-close → end`) is wired correctly.

Issue: #164
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.cli_main import main
from kaji_harness.workflow import load_workflow, validate_workflow

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / "workflows" / "feature-with-postmerge.yaml"


class TestFeatureWithPostmergeWorkflowSmall:
    @pytest.mark.small
    def test_validates(self) -> None:
        wf = load_workflow(WORKFLOW_PATH)
        validate_workflow(wf)

    @pytest.mark.small
    def test_pr_passes_to_wait_merge(self) -> None:
        wf = load_workflow(WORKFLOW_PATH)
        pr = wf.find_step("pr")
        assert pr is not None
        assert pr.on["PASS"] == "wait-merge"

    @pytest.mark.small
    def test_post_merge_chain(self) -> None:
        wf = load_workflow(WORKFLOW_PATH)
        chain = [
            ("wait-merge", "verify-main-green"),
            ("verify-main-green", "post-merge-review"),
            ("post-merge-review", "post-merge-close"),
            ("post-merge-close", "end"),
        ]
        for src, dst in chain:
            step = wf.find_step(src)
            assert step is not None, f"step {src} missing"
            assert step.on["PASS"] == dst, f"{src} PASS != {dst}"

    @pytest.mark.small
    def test_post_merge_review_retry_pauses(self) -> None:
        wf = load_workflow(WORKFLOW_PATH)
        step = wf.find_step("post-merge-review")
        assert step is not None
        assert step.on.get("RETRY") == "end"

    @pytest.mark.small
    def test_verify_main_green_retry_self_loops(self) -> None:
        wf = load_workflow(WORKFLOW_PATH)
        step = wf.find_step("verify-main-green")
        assert step is not None
        assert step.on.get("RETRY") == "verify-main-green"


class TestFeatureWithPostmergeWorkflowMedium:
    @pytest.mark.medium
    def test_kaji_validate_cli(self) -> None:
        exit_code = main(["validate", str(WORKFLOW_PATH)])
        assert exit_code == 0
