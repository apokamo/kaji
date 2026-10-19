"""issue-close の follow-up Issue 本文テンプレートの構造を検証する（Issue #520）。

テンプレートファイル全体がそのまま follow-up 本文になるため、説明用の見出し・説明文・
コードフェンスを含めない。用途説明は SKILL.md § 3 側に置く。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close"
_TEMPLATE = _SKILL_DIR / "templates/follow-up-issue.md"
_SKILL = _SKILL_DIR / "SKILL.md"
_PARENT_MARKER = "<!-- kaji-follow-up-parent: [parent_issue_ref] -->"
_PLACEHOLDERS = (
    "[parent_issue_ref]",
    "[parent_issue_title]",
    "[unchecked_items]",
    "[reference_docs]",
)


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


@pytest.mark.small
def test_template_first_line_is_parent_marker() -> None:
    assert _template_text().splitlines()[0] == _PARENT_MARKER


@pytest.mark.small
def test_template_has_no_explanatory_heading_or_fence() -> None:
    text = _template_text()
    assert "# Follow-up Issue 本文テンプレート" not in text
    fence_lines = [line for line in text.splitlines() if line.lstrip().startswith("```")]
    assert fence_lines == []


@pytest.mark.small
@pytest.mark.parametrize("placeholder", _PLACEHOLDERS)
def test_template_keeps_placeholders(placeholder: str) -> None:
    assert placeholder in _template_text()


@pytest.mark.small
def test_skill_section_3_uses_whole_template_as_body() -> None:
    section = _between(
        _SKILL.read_text(encoding="utf-8"),
        "### 3. follow-up Issue の作成",
        "### 4. 親本文へのマーカー追記",
    )
    assert "ファイル全体" in section
    assert "placeholder 以外" in section
