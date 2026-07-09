"""Invariants for the canonical workflow set under ``.kaji/wf/``.

The workflow set contains the everyday GitHub/local workflows plus tracked model
variants. Each workflow's ``name:`` must match its filename stem. This test
guards those invariants so a re-added legacy workflow, an accidental deletion, or
a ``name:`` / filename drift is caught cheaply by the loader (which is already
covered by other tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.workflow import load_workflow

WF_DIR = Path(__file__).resolve().parent.parent.parent / ".kaji" / "wf"

EXPECTED_WORKFLOWS = {
    "dev",
    "dev-local",
    "dev-thorough",
    "dev-thorough-codex",
    "dev-thorough-fable",
    "docs",
    "docs-fable",
    "docs-local",
    "docs-thorough-codex",
}


@pytest.mark.small
class TestWorkflowSetInvariants:
    def test_exactly_canonical_workflows(self) -> None:
        """``.kaji/wf/`` holds exactly the canonical workflows."""
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
