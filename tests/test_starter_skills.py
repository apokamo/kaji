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


@pytest.mark.parametrize(
    "relative_path",
    [
        ".claude/skills/update-starter/SKILL.md",
        ".claude/skills/update-starter/references/classification-guide.md",
        ".claude/skills/review-starter-update/SKILL.md",
        ".claude/skills/review-starter-update/references/review-rubric.md",
        "docs/operations/release/starter-sync-runbook.md",
    ],
)
def test_starter_fix_discovery_terms_are_shared(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "starter-fix" in text
    assert "starter_repo:" in text
