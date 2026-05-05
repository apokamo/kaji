"""IssueContext 構築ヘルパ。

provider 共通の純粋関数群。Phase 3 では `branch_name` / `worktree_dir` /
`design_path` の生成規約を本 module に集約する（phase3-design.md § slug
の供給ルール, L348-364）。worktree / branch は既存規約に準拠し、Phase 3
時点で slug は同梱しない（オープン論点として持ち越し）。
"""

from __future__ import annotations

import re
from pathlib import Path

# slug の文字制約（phase3-design.md L356）
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def validate_slug(slug: str) -> None:
    """slug 文法を検証する。違反は ``ValueError``。"""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r}: must match ^[a-z0-9][a-z0-9-]{{0,39}}$ "
            f"(lowercase alphanumeric, hyphen-separated, leading char alnum, "
            f"max 40 chars)"
        )


def derive_slug_from_title(title: str) -> str:
    """GitHub Issue title から slug を導出する。

    手順（phase3-design.md L362）:
    1. lowercase 化
    2. 英数字以外を ``-`` に置換
    3. 連続 hyphen を圧縮
    4. 先頭末尾 hyphen 除去
    5. 40 文字に切り詰め

    空 title 等で結果が空になる場合は ``"untitled"`` を返す。
    """
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    s = s[:40]
    s = s.rstrip("-")
    return s or "untitled"


def build_branch_name(branch_prefix: str, issue_id: str) -> str:
    """``<prefix>/<issue_id>`` 形式の branch 名を返す。

    `.claude/skills/issue-start/SKILL.md:48` の規約に準拠。
    """
    return f"{branch_prefix}/{issue_id}"


def build_worktree_dir(branch_prefix: str, issue_id: str, repo_root: Path) -> str:
    """worktree 絶対パス文字列を返す。

    既存規約: ``<repo_parent>/kaji-<prefix>-<issue_id>``
    （`.claude/skills/issue-start/SKILL.md:33`）。Phase 3 では slug 同梱しない。
    """
    return str(repo_root.parent / f"kaji-{branch_prefix}-{issue_id}")


def build_design_path(issue_id: str, slug: str) -> str:
    """設計書の relative path を返す。

    既存規約: ``draft/design/issue-<issue_id>-<slug>.md``
    （`.claude/skills/issue-design/SKILL.md:133`）。
    """
    return f"draft/design/issue-{issue_id}-{slug}.md"


def format_issue_ref(issue_id: str) -> str:
    """``#153`` / ``local-pc1-3`` 形式の人間可読参照を返す。

    `kaji_harness.state._format_issue_ref` と同じロジック。本 module は
    provider package として state.py に依存させたくないため独立に持つ。
    将来の統合時は本実装を正本にする。
    """
    return f"#{issue_id}" if issue_id.isdigit() else issue_id
