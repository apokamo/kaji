"""同梱 agent skill が verdict.yaml を手書きさせないことを確認する（Issue #568）。

agent step の artifact は prompt の ``kaji verdict publish`` で公開する。skill に
「``verdict_path`` へ pure YAML を保存」型の手書き指示が残ると、prompt と矛盾し、
字下げ 1 文字の誤りで判定が失われる経路（#551 / incident #565）が復活する。
exec_script skill（``baseline-precheck`` / ``review-poll``）は script 自身が書くため対象外。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"

# 設計書「同根の他の壊れ箇所」で手書き指示を持っていた skill（publish への移行対象）。
_MIGRATED_SKILLS = (
    "issue-implement",
    "issue-small-change-execute",
    "issue-small-change-review",
    "incident-investigate",
    "review-starter-update",
    "release-starter",
    "update-starter",
)


def _frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        return {}
    end = text.index("\n---", 4)
    loaded = yaml.safe_load(text[4:end])
    return loaded if isinstance(loaded, dict) else {}


def _agent_skill_files() -> list[Path]:
    files = []
    for path in sorted(_SKILLS.glob("*/SKILL.md")):
        if "exec_script" in _frontmatter(path.read_text(encoding="utf-8")):
            continue
        files.append(path)
    return files


@pytest.mark.small
class TestAgentSkillsUseVerdictPublish:
    def test_agent_skills_are_discovered(self) -> None:
        names = {path.parent.name for path in _agent_skill_files()}

        assert set(_MIGRATED_SKILLS) <= names
        assert "baseline-precheck" not in names
        assert "review-poll" not in names

    @pytest.mark.parametrize("path", _agent_skill_files(), ids=lambda p: p.parent.name)
    def test_no_handwritten_pure_yaml_instruction(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")

        assert "pure YAML" not in text, f"{path} still asks the agent to write YAML by hand"

    @pytest.mark.parametrize("name", _MIGRATED_SKILLS)
    def test_migrated_skill_references_publish_command(self, name: str) -> None:
        text = (_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")

        assert "kaji verdict publish" in text
