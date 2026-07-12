"""IssueContext 構築ヘルパ。

provider 共通の純粋関数群。Phase 3 では `branch_name` / `worktree_dir` /
`design_path` の生成規約を本 module に集約する（phase3-design.md § slug
の供給ルール, L348-364）。worktree / branch は既存規約に準拠し、Phase 3
時点で slug は同梱しない（オープン論点として持ち越し）。
"""

from __future__ import annotations

import re
from pathlib import Path

from ._mappings import LABEL_TO_PREFIX

# slug の文字制約（phase3-design.md L356）
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Phase 3-d preflight: branch_prefix は kaji の type label / branch 命名規約の
# 一部であり、自由入力にすると path / branch policy を provider 外へ漏らす。
# 既知 prefix 値（_mappings.LABEL_TO_PREFIX の values）に限定する。
_ALLOWED_BRANCH_PREFIXES: frozenset[str] = frozenset(LABEL_TO_PREFIX.values())


def validate_slug(slug: str) -> None:
    """slug 文法を検証する。違反は ``ValueError``。"""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r}: must match ^[a-z0-9][a-z0-9-]{{0,39}}$ "
            f"(lowercase alphanumeric, hyphen-separated, leading char alnum, "
            f"max 40 chars)"
        )


def validate_branch_prefix(prefix: str) -> None:
    """``branch_prefix`` が既知の type prefix のいずれかであることを検証する。

    違反は ``ValueError``。Phase 3-d preflight で導入（phase3d-preflight-design
    § 3 legacy frontmatter / validation 方針）。
    """
    if prefix not in _ALLOWED_BRANCH_PREFIXES:
        allowed = ", ".join(sorted(_ALLOWED_BRANCH_PREFIXES))
        raise ValueError(f"invalid branch_prefix {prefix!r}: must be one of {{{allowed}}}")


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


def build_worktree_dir(
    branch_prefix: str,
    issue_id: str,
    repo_root: Path,
    worktree_prefix: str = "",
) -> str:
    """worktree 絶対パス文字列を返す。

    規約: ``<repo_parent>/<worktree_prefix>-<prefix>-<issue_id>``
    （`.claude/skills/issue-start/SKILL.md:33`）。Phase 3 では slug 同梱しない。

    Args:
        branch_prefix: type label 由来の branch prefix（``feat`` / ``fix`` 等）。
        issue_id: 正規化済み Issue ID。
        repo_root: repo のルート。worktree は ``repo_root.parent`` 直下に作る。
        worktree_prefix: worktree dir 名の先頭 segment。空文字（無設定）の場合は
            後方互換で ``"kaji"`` にフォールバックする。consumer ごとに
            ``[paths].worktree_prefix`` で上書きできる（Issue #215）。
    """
    return str(repo_root.parent / f"{worktree_prefix or 'kaji'}-{branch_prefix}-{issue_id}")


def build_design_path(issue_id: str, slug: str) -> str:
    """設計書の relative path を返す。

    既存規約: ``draft/design/issue-<issue_id>-<slug>.md``
    （`.claude/skills/issue-design/SKILL.md:133`）。
    """
    return f"draft/design/issue-{issue_id}-{slug}.md"


def format_issue_ref(issue_id: str) -> str:
    """``#153`` / ``local-pc1-3`` 形式の人間可読参照を返す。

    数値のみ（GitHub Issue 番号）→ ``#153``。
    それ以外（local-pc1-1 等）→ そのまま返す。

    Issue #285 で ``kaji_harness.state._format_issue_ref`` を統合し、本実装を正本に
    した（重複していた 2 実装のロジックは一致していた）。`state` / `commands` は
    本関数を呼ぶ。
    """
    return f"#{issue_id}" if issue_id.isdigit() else issue_id
