"""Static contracts for starter maintenance skills."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium
ROOT = Path(__file__).resolve().parents[1]


def test_agent_skill_entries_resolve_to_canonical_skills() -> None:
    for name in ("update-starter", "review-starter-update", "release-starter"):
        entry = ROOT / ".agents" / "skills" / name
        assert entry.is_symlink()
        assert entry.resolve() == (ROOT / ".claude" / "skills" / name).resolve()
