"""Invariants for the canonical workflow set under ``.kaji/wf/`` (Issue #247).

Issue #247 fixed the normal-operation workflow set to exactly 5 files
(3 GitHub + 2 local fallback) and required each workflow's ``name:`` to match
its filename stem. This test guards those invariants so a re-added legacy
workflow, an accidental deletion, or a ``name:`` / filename drift is caught
cheaply by the loader (which is already covered by other tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.workflow import load_workflow

WF_DIR = Path(__file__).resolve().parent.parent.parent / ".kaji" / "wf"

EXPECTED_WORKFLOWS = {
    "dev",
    "dev-thorough",
    "docs",
    "dev-local",
    "docs-local",
}


@pytest.mark.small
class TestWorkflowSetInvariants:
    def test_exactly_five_workflows(self) -> None:
        """``.kaji/wf/`` holds exactly the 5 canonical workflows."""
        stems = {p.stem for p in WF_DIR.glob("*.yaml")}
        assert stems == EXPECTED_WORKFLOWS, (
            f"`.kaji/wf/` workflow set drifted: got {sorted(stems)}, "
            f"expected {sorted(EXPECTED_WORKFLOWS)}"
        )

    @pytest.mark.parametrize("name", sorted(EXPECTED_WORKFLOWS))
    def test_name_matches_filename_stem(self, name: str) -> None:
        """Each workflow's ``name:`` equals its filename stem (no drift)."""
        wf = load_workflow(WF_DIR / f"{name}.yaml")
        assert wf.name == name, f"{name}.yaml: name field {wf.name!r} != filename stem {name!r}"
