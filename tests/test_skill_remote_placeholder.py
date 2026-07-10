"""gl:6: skill `i-pr` / `issue-close` の `origin` hardcode 置換 regression test。

設計書 § テスト戦略 § bug 固有: 再現テスト に対応。
SKILL 全文（コマンド行 / コメント / 説明文 / echo文）で `\\borigin\\b`
単語マッチが 0 件、`[git_remote]` placeholder が所定回数以上出現することを
assert する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_I_PR_SKILL = _REPO_ROOT / ".claude" / "skills" / "i-pr" / "SKILL.md"
_ISSUE_CLOSE_SKILL = _REPO_ROOT / ".claude" / "skills" / "issue-close" / "SKILL.md"

_ORIGIN_WORD_RE = re.compile(r"\borigin\b")


@pytest.mark.small
def test_i_pr_skill_has_no_origin_word() -> None:
    """`i-pr/SKILL.md` 全文に `\\borigin\\b` 単語が残っていない。"""
    text = _I_PR_SKILL.read_text(encoding="utf-8")
    matches = _ORIGIN_WORD_RE.findall(text)
    assert matches == [], (
        f"unexpected `origin` word(s) in {_I_PR_SKILL}: {len(matches)} occurrence(s). "
        f"Use [git_remote] placeholder instead."
    )


@pytest.mark.small
def test_issue_close_skill_has_no_origin_word() -> None:
    """`issue-close/SKILL.md` 全文に `\\borigin\\b` 単語が残っていない。"""
    text = _ISSUE_CLOSE_SKILL.read_text(encoding="utf-8")
    matches = _ORIGIN_WORD_RE.findall(text)
    assert matches == [], (
        f"unexpected `origin` word(s) in {_ISSUE_CLOSE_SKILL}: {len(matches)} occurrence(s). "
        f"Use [git_remote] placeholder instead."
    )


@pytest.mark.small
def test_i_pr_skill_uses_git_remote_placeholder() -> None:
    """`i-pr/SKILL.md` が `[git_remote]` placeholder を 1 回以上使う。"""
    text = _I_PR_SKILL.read_text(encoding="utf-8")
    count = text.count("[git_remote]")
    assert count >= 1, f"expected `[git_remote]` placeholder in {_I_PR_SKILL}, got {count}"


@pytest.mark.small
def test_issue_close_skill_uses_git_remote_placeholder() -> None:
    """`issue-close/SKILL.md` が `[git_remote]` placeholder を 10 回以上使う。"""
    text = _ISSUE_CLOSE_SKILL.read_text(encoding="utf-8")
    count = text.count("[git_remote]")
    assert count >= 10, (
        f"expected at least 10 `[git_remote]` placeholders in {_ISSUE_CLOSE_SKILL}, got {count}"
    )


@pytest.mark.small
def test_issue_close_skill_uses_remote_default_branch_combo() -> None:
    """`issue-close/SKILL.md` が `[git_remote]/[default_branch]` 組合せを 3 回以上使う。"""
    text = _ISSUE_CLOSE_SKILL.read_text(encoding="utf-8")
    count = text.count("[git_remote]/[default_branch]")
    assert count >= 3, (
        f"expected at least 3 `[git_remote]/[default_branch]` occurrences in "
        f"{_ISSUE_CLOSE_SKILL}, got {count}"
    )
