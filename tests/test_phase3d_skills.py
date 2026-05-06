"""Phase 3-d: Skill markdown placeholder 静的検証。

phase3d-design.md § 1 の forbidden list（hyphen 形式 + 山括弧形式）を
全 Skill に対して grep で検証する。回帰検知用。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills"

FORBIDDEN_PATTERNS = [
    # hyphen 形式（Phase 3-d で全廃）
    r"\[worktree-absolute-path\]",
    r"\[branch-name\]",
    r"\[design-path\]",
    r"\[issue-input\]",
    # 山括弧形式（preflight 後 0 件、回帰検知用）
    r"<worktree-absolute-path>",
    r"<branch-name>",
    r"<design-path>",
    r"<issue-input>",
]


def _all_skill_md() -> list[Path]:
    return sorted(p for p in SKILL_DIR.rglob("*.md"))


@pytest.mark.medium
def test_no_legacy_placeholders_remain_in_skill_md() -> None:
    """全 Skill markdown から hyphen / 山括弧形式の placeholder が消えている。"""
    violations: list[str] = []
    for path in _all_skill_md():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}: matches {pattern!r}")
    assert not violations, "Legacy placeholders found:\n  " + "\n  ".join(violations)


@pytest.mark.medium
def test_new_placeholders_used_somewhere() -> None:
    """新 placeholder が少なくとも 1 ファイルで使われていることを確認する。

    回帰防止: 一括置換ミスでファイルが空になっても気付けるよう、
    underscore 形式が現存することを確認する。
    """
    # 旧 hyphen 形式から実置換された placeholder のみ存在確認する。
    # `[design_path]` / `[issue_input]` は元の Skill 群に出現していなかったため
    # 検証対象外（forbidden list には残し、新規混入時は別パターンで catch する）。
    expected = [r"\[worktree_dir\]", r"\[branch_name\]"]
    for pattern in expected:
        found = False
        for path in _all_skill_md():
            if re.search(pattern, path.read_text(encoding="utf-8")):
                found = True
                break
        assert found, f"no skill markdown uses pattern {pattern!r}"


@pytest.mark.medium
def test_issue_close_skill_contains_local_six_steps() -> None:
    """`issue-close` SKILL.md が design.md L972-996 の 6-step を含む。

    phase3d-design.md § 7 で要求される keyword:

    - ``provider_type`` への分岐
    - ``[default_branch]`` の使用
    - 各 Step の特徴的キーワード
    - Step 4 で ``--reason completed`` を明示
    """
    path = SKILL_DIR / "issue-close" / "SKILL.md"
    text = path.read_text(encoding="utf-8")

    expected_keywords = [
        "[provider_type]",
        "[default_branch]",
        "Preflight check",
        # Phase 3-d レビュー反映: base worktree 側で merge / commit / cleanup を
        # 行う運用に変更したため "Base branch" → "Base worktree" を期待する。
        "Base worktree",
        "merge --no-ff",
        "kaji issue close [issue_id] --reason completed",
        "git worktree remove",
        "git push origin [default_branch]",
    ]
    for kw in expected_keywords:
        assert kw in text, f"issue-close SKILL.md missing keyword {kw!r}"
