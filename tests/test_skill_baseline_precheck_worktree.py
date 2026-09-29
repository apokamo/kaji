"""Skill / docs の baseline_precheck 実行例が ``--worktree`` を渡すことの静的検証。

agent step には ``KAJI_WORKTREE_DIR`` が注入されないため、``--compare`` /
``--evaluate`` を ``--worktree`` なしで実行すると ``ValueError`` になる（#430）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills"
BASELINE_DOC = REPO_ROOT / "docs" / "dev" / "baseline-check.md"

COMMAND_PATTERN = re.compile(r"python -m kaji_harness\.scripts\.baseline_precheck[^`\n]*")
MODE_PATTERN = re.compile(r"--(compare|evaluate)\b")


def _join_continuations(text: str) -> list[str]:
    """行継続（末尾 ``\\``）で分かれた行を 1 行に結合する。"""
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def _commands_missing_worktree(text: str) -> list[str]:
    """``--compare`` / ``--evaluate`` を使い ``--worktree`` を含まないコマンドを返す。"""
    missing: list[str] = []
    for line in _join_continuations(text):
        for match in COMMAND_PATTERN.finditer(line):
            command = match.group(0)
            if MODE_PATTERN.search(command) and "--worktree" not in command:
                missing.append(command.strip())
    return missing


def _target_files() -> list[Path]:
    return [*sorted(SKILL_DIR.rglob("*.md")), BASELINE_DOC]


@pytest.mark.small
@pytest.mark.parametrize(
    ("text", "expected_missing"),
    [
        ("python -m kaji_harness.scripts.baseline_precheck --compare\n", 1),
        ("`python -m kaji_harness.scripts.baseline_precheck --compare` を使う\n", 1),
        ("python -m kaji_harness.scripts.baseline_precheck \\\n  --evaluate --scope a\n", 1),
        (
            "python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --compare\n",
            0,
        ),
        (
            "python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] \\\n"
            "  --evaluate --scope a --scope b\n",
            0,
        ),
        ("exec_script: kaji_harness.scripts.baseline_precheck\n", 0),
        ("python -m kaji_harness.scripts.baseline_precheck\n", 0),
    ],
)
def test_commands_missing_worktree_detection(text: str, expected_missing: int) -> None:
    """検出ロジック: 行継続を結合し、比較・評価モードで --worktree 欠落のみを検出する。"""
    assert len(_commands_missing_worktree(text)) == expected_missing


@pytest.mark.medium
def test_baseline_precheck_commands_pass_worktree() -> None:
    """Skill / baseline-check.md の --compare / --evaluate 実行例がすべて --worktree を含む。"""
    assert BASELINE_DOC.is_file()
    violations: list[str] = []
    for path in _target_files():
        rel = path.relative_to(REPO_ROOT)
        for command in _commands_missing_worktree(path.read_text(encoding="utf-8")):
            violations.append(f"{rel}: {command}")
    assert not violations, "baseline_precheck commands without --worktree:\n  " + "\n  ".join(
        violations
    )
