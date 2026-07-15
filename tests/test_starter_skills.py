"""Static contracts for starter maintenance skills and runbooks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.medium
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("name", "statuses"),
    [
        ("update-starter", ("PASS", "ABORT")),
        ("review-starter-update", ("PASS", "RETRY", "ABORT")),
        ("release-starter", ("PASS", "ABORT")),
    ],
)
def test_skill_frontmatter_and_verdict_vocabulary(name: str, statuses: tuple[str, ...]) -> None:
    skill_path = ROOT / ".claude" / "skills" / name / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == name
    assert metadata["description"]
    for status in statuses:
        assert status in body


def test_skill_guardrails_are_explicit() -> None:
    update = (ROOT / ".claude/skills/update-starter/SKILL.md").read_text(encoding="utf-8")
    review = (ROOT / ".claude/skills/review-starter-update/SKILL.md").read_text(encoding="utf-8")
    release = (ROOT / ".claude/skills/release-starter/SKILL.md").read_text(encoding="utf-8")

    assert all(
        term in update for term in ("3 区分", "lockfile", "review 前", "push", "コピーしない")
    )
    assert all(
        term in review for term in ("別 session", "修正しない", "target", "base", "candidate")
    )
    assert all(
        term in release
        for term in (
            "resolve-verdict",
            "独立 review PASS",
            "人間の明示承認",
            "git push --atomic",
            "force push",
            "kaji-vX.Y.Z-rN",
            "部分成功",
        )
    )
    assert "N/A を release-plan より先に分岐" in release
    assert "release-plan を呼ばない" in release


def test_release_notes_template_has_required_sections() -> None:
    text = (ROOT / ".claude/skills/release-starter/templates/release-notes.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "対応 kaji Release",
        "反映内容",
        "N/A とした変更と理由",
        "BREAKING 対応",
        "検証 evidence",
        "snapshot の利用方法",
    ):
        assert heading in text


def test_agent_skill_entries_resolve_to_canonical_skills() -> None:
    for name in ("update-starter", "review-starter-update", "release-starter"):
        entry = ROOT / ".agents" / "skills" / name
        assert entry.is_symlink()
        assert entry.resolve() == (ROOT / ".claude" / "skills" / name).resolve()


def test_starter_sync_runbook_contract_and_links() -> None:
    runbook = (ROOT / "docs/operations/release/starter-sync-runbook.md").read_text(encoding="utf-8")
    release_runbook = (ROOT / "docs/operations/release/runbook.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    release_skill = (ROOT / ".claude/skills/release/SKILL.md").read_text(encoding="utf-8")

    assert "## Managed starters" in runbook
    assert "kaji-v0.12.1" in runbook
    assert "follow-up Issue" in runbook
    assert "starter-sync-runbook.md" in release_runbook
    assert "starter-sync-runbook.md" in docs_index
    assert "update-starter" in release_skill
