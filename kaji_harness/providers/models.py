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
    # local mode 固有: コメントファイル名の uniqueness 部分。
    # Issue local-pc5090-21 以降は compact ISO 8601 timestamp
    # (``YYYYMMDDTHHMMSSZ``、例: ``"20260510T142536Z"``)。
    # GitHub provider では空文字列。
    # 注: filename の uniqueness 用であり、comment ordering の正本は
    # frontmatter ``created_at``（同秒衝突 retry で +1s 加算され乖離しうる）。
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
        provider_type: ``"github"`` / ``"local"`` / ``"gitlab"``。
        default_branch: provider の default branch。``main`` 等。
            ``provider=local`` では ``provider.local.default_branch``、
            ``provider=github`` では ``provider.github.default_branch`` が source。
            Skill prompt の ``[default_branch]`` placeholder で参照される
            （phase3d-design.md § 2）。
        git_remote: skill 内の ``git push`` / ``git fetch`` 等が対象とする
            git remote 名。default ``"origin"``。``provider.<type>.git_remote``
            config が source。Skill prompt の ``[git_remote]`` placeholder で
            参照される（gl:6 で導入、hybrid setup 対応）。
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
    default_branch: str = "main"
    git_remote: str = "origin"


@dataclass(frozen=True)
class PRContext:
    """Skill 注入用の PR コンテキスト変数。

    `IssueContext` と分離している理由は、PR が workflow 実行中に新規作成
    されるため `IssueContext` 解決時点（`kaji run` 起動直後）で確定でき
    ない点にある。`prompt.py` は `provider.resolve_pr_context(branch_name)`
    の戻り値が ``None`` でない場合に限り `pr_id` / `pr_ref` を variables に
    追加する。

    Attributes:
        pr_id: provider 内部 ID。github なら ``"42"``、gitlab なら
            project-local ``merge_request_iid`` の文字列（``"42"``）。
        pr_ref: 人間可読参照。github なら ``"#42"``、gitlab なら
            ``"gl:42"``（`kaji-pr-mr-bridge.md` § 設計原則 1 に準拠）。
    """

    pr_id: str
    pr_ref: str
