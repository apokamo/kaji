"""Provider 固有 mapping の正本。

`branch_prefix` mapping の正本は元々 `.claude/skills/issue-create/SKILL.md`
にあったが、Phase 3 で本 module を Python 上の正本に昇格させる
（phase3-design.md § branch_prefix mapping の正本化, L324-346）。

dict の挿入順を優先順位として扱う（同一 Issue に複数 type label が付いた
場合の tie-break）。Python 3.7+ で挿入順が保証されているため決定的。
"""

from __future__ import annotations

from typing import Final

# GitHub label name → branch_prefix の写像。順序が優先順位。
LABEL_TO_PREFIX: Final[dict[str, str]] = {
    "type:feature": "feat",
    "type:bug": "fix",
    "type:refactor": "refactor",
    "type:docs": "docs",
    "type:test": "test",
    "type:chore": "chore",
    "type:perf": "perf",
    "type:security": "security",
}

# label 不在時の fallback。phase3-design.md L346。
DEFAULT_BRANCH_PREFIX: Final[str] = "chore"


def labels_to_branch_prefix(label_names: list[str]) -> tuple[str, bool]:
    """ラベル一覧から branch_prefix を決定する。

    Returns:
        (branch_prefix, fallback) — `fallback` は ``type:*`` label 不在で
        ``chore`` に倒した場合 True。
    """
    label_set = set(label_names)
    for label, prefix in LABEL_TO_PREFIX.items():
        if label in label_set:
            return prefix, False
    return DEFAULT_BRANCH_PREFIX, True
