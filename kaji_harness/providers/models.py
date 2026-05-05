"""Provider-neutral data classes for kaji_harness.

`Issue` / `Comment` / `Label` は GitHub / local 両方の表現を吸収する
正規形。`IssueContext` は Skill が参照する 9 変数体系のうち
Phase 3 で確立する 7 変数（`issue_id` / `issue_ref` / `issue_input` /
`branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）を
Provider 経由で供給するための DTO（design.md L918, phase3-design.md § 1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Label:
    """Issue/PR ラベル。"""

    name: str
    description: str = ""
    color: str = ""


@dataclass(frozen=True)
class Comment:
    """Issue/PR コメント。"""

    author: str
    body: str
    created_at: str  # ISO8601 文字列
    # local mode 固有: コメントファイル名のシーケンス（``0001`` 等）。
    # GitHub provider では空文字列。
    seq: str = ""
    # local mode 固有: コメント投稿元の machine_id。GitHub では空。
    machine_id: str = ""


@dataclass(frozen=True)
class Issue:
    """Provider 非依存の Issue 表現。

    Attributes:
        id: provider 内部 ID。github なら ``"153"``、local なら
            ``"local-pc1-3"``。
        title: Issue タイトル。
        body: Issue 本文（markdown）。
        state: ``"open"`` または ``"closed"``。
        labels: 適用ラベル一覧。
        comments: 投稿順コメント一覧。
        slug: ディレクトリ末尾 / branch / worktree / design path 合成用の
            kebab-case 名。GitHub では title から sanitize 導出する。
    """

    id: str
    title: str
    body: str
    state: str
    labels: list[Label] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    slug: str = ""


@dataclass(frozen=True)
class IssueContext:
    """Skill 注入用の Issue コンテキスト変数（5 + 2）。

    `prompt.py` がこの値を Skill に注入する。`prompt.py` 自体は provider
    固有の label / cache / frontmatter 解決を行わず、provider が組み立てた
    `IssueContext` をそのまま参照する（design.md L918, phase3-design.md § 1）。

    Attributes:
        issue_id: provider 内部 ID（``"153"`` / ``"local-pc1-3"``）。
        issue_ref: 人間可読参照（``"#153"`` / ``"local-pc1-3"``）。
        issue_input: CLI / skill 引数で再投入できる入力形式。
            github なら ``"153"``、local なら ``"local-pc1-3"``。
        slug: Issue ディレクトリ末尾 / design_path / 将来の worktree 命名で使用。
        branch_prefix: ``feat`` / ``fix`` / ``docs`` 等。GitHub label から
            mapping、local では frontmatter から read。
        branch_prefix_fallback: ``type:*`` label 不在で ``chore`` fallback
            された場合 True。CLI 層の warning 表示などに使う。
        branch_name: ``<branch_prefix>/<id>``（``feat/153`` 等）。
        worktree_dir: worktree 絶対パス（``/path/to/kaji-feat-153``）。
            Phase 3 では slug 同梱しない（既存 ``kaji-<prefix>-<id>`` を維持）。
        design_path: 設計書パス（``draft/design/issue-<id>-<slug>.md`` 等）。
        provider_type: ``"github"`` / ``"local"``。
    """

    issue_id: str
    issue_ref: str
    issue_input: str
    slug: str
    branch_prefix: str
    branch_name: str
    worktree_dir: str
    design_path: str
    provider_type: str
    branch_prefix_fallback: bool = False
