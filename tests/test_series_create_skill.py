"""Structure and safety tests for the series-create skill."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.small

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/series-create/SKILL.md"


def test_series_create_frontmatter_and_required_sections() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "series-create"
    assert "sequential" in metadata["description"]
    for heading in ("## Input", "## Output", "## Stop Conditions", "## Non-goals"):
        assert heading in body


def test_series_create_delegates_deterministic_operations() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "kaji_harness.scripts.series_generate" in text
    assert "kaji validate-series" in text
    assert "kaji run-series" in text
    assert "--dry-run" in text
    assert "Do not write YAML manually" in text


def test_series_create_requires_read_only_issue_lookup_and_explicit_update() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "kaji issue view" in text
    assert "Never retry with `--update` implicitly" in text
    assert "kaji issue edit" not in text
    assert "kaji issue close" not in text
    assert "kaji run-series` without `--dry-run`" in text


def test_builtin_workflow_descriptions_define_unique_auto_selection() -> None:
    workflow_dir = SKILL.parents[3] / ".kaji" / "wf"
    descriptions = {
        path.name: str(yaml.safe_load(path.read_text(encoding="utf-8"))["description"])
        for path in workflow_dir.glob("*.yaml")
    }
    assert "series 自動選択の標準 workflow" in descriptions["dev.yaml"]
    assert "series 自動選択の標準 workflow" in descriptions["docs.yaml"]
    for name, description in descriptions.items():
        if name not in {"dev.yaml", "docs.yaml"}:
            assert "series 自動選択対象外" in description
