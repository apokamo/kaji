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
    # Phase 4 で追加: pr-* 系の hyphen 形式 placeholder（pr-fix / pr-verify /
    # i-pr で全廃）
    r"\[pr-number\]",
    r"<pr-number>",
]

# Claude Code は引数付き手動起動時に本文の `$<数字>` を引数で置換する（Issue #524）。
# awk の `$1` / `$0` 等が壊れるため、`$` の直後に数字が続く並びを禁止する。
POSITIONAL_ARG_PATTERN = re.compile(r"\$[0-9]")


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
def test_no_positional_arg_tokens_in_skill_md() -> None:
    """全 Skill markdown に Claude Code が引数置換する `$<数字>` が残っていない。"""
    violations: list[str] = []
    for path in _all_skill_md():
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if POSITIONAL_ARG_PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    assert not violations, "`$<digit>` tokens found:\n  " + "\n  ".join(violations)


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
