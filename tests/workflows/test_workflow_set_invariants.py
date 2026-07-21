"""Invariants for the official workflow set under ``.kaji/wf/official/``.

The official set is what kaji ships, updates, and regression-tests. Each
workflow's ``name:`` must match its filename stem. This test guards those
invariants so a re-added legacy workflow, an accidental deletion, a ``local/``
hierarchy drift, or a custom workflow leaking into ``official/`` is caught
cheaply by the loader (which is already covered by other tests).

Workflows under ``.kaji/wf/custom/`` are repository-owned and intentionally
excluded: the glob is rooted at ``official/`` rather than filtered, so adding a
custom category can never pull it into the official regression set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.workflow import load_workflow

OFFICIAL_DIR = Path(__file__).resolve().parent.parent.parent / ".kaji" / "wf" / "official"

EXPECTED_OFFICIAL = {
    "dev.yaml",
    "docs.yaml",
    "incident.yaml",
    "local/dev-local.yaml",
    "local/docs-local.yaml",
}


@pytest.mark.medium
class TestOfficialWorkflowSetInvariants:
    def test_exactly_official_workflows(self) -> None:
        """``.kaji/wf/official/`` holds exactly the official workflows."""
        found = {p.relative_to(OFFICIAL_DIR).as_posix() for p in OFFICIAL_DIR.rglob("*.yaml")}
        assert found == EXPECTED_OFFICIAL, (
            f"`.kaji/wf/official/` workflow set drifted: got {sorted(found)}, "
            f"expected {sorted(EXPECTED_OFFICIAL)}"
        )

    @pytest.mark.parametrize("relative_path", sorted(EXPECTED_OFFICIAL))
    def test_name_matches_filename_stem(self, relative_path: str) -> None:
        """Each workflow's ``name:`` equals its filename stem (no drift)."""
        path = OFFICIAL_DIR / relative_path
        stem = path.stem
        wf = load_workflow(path)
        assert wf.name == stem, f"{relative_path}: name field {wf.name!r} != filename stem {stem!r}"
