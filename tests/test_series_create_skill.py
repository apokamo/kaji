"""Workflow description contract relied on by the series-create skill.

Medium: loads the official workflow YAML set from disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.medium

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/series-create/SKILL.md"


def test_official_workflow_descriptions_define_unique_auto_selection() -> None:
    """official workflow の description が series 自動選択契約を満たす。

    ``custom/**`` は利用者所有のため対象外（pytest の契約検証は official のみ）。
    """
    official_dir = SKILL.parents[3] / ".kaji" / "wf" / "official"
    descriptions = {
        path.relative_to(official_dir).as_posix(): str(
            yaml.safe_load(path.read_text(encoding="utf-8"))["description"]
        )
        for path in official_dir.rglob("*.yaml")
    }
    assert descriptions, "official workflow が 1 つも見つからない（glob を確認すること）"
    assert "series 自動選択の標準 workflow" in descriptions["dev.yaml"]
    assert "series 自動選択の標準 workflow" in descriptions["docs.yaml"]
    for name, description in descriptions.items():
        if name not in {"dev.yaml", "docs.yaml"}:
            assert "series 自動選択対象外" in description
