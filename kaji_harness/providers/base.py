"""IssueProvider Protocol.

Phase 3 では Issue 系 protocol のみを定義する。PR 系（GitHub PR を抽象化する
`ReviewRequestProvider` 等）は Phase 4 で追加する（phase3-design.md § scope, L292）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Comment, Issue, IssueContext, Label, PRContext


@runtime_checkable
class IssueProvider(Protocol):
    """Issue CRUD + コンテキスト解決の provider interface。"""

    @property
    def is_readonly(self) -> bool:
        """provider が write 操作を受け付けない場合 True。

        ``provider=local`` 配下の remote_cache 経路（``gh:N``）など、
        部分的に read-only な経路が将来発生する可能性がある。Phase 3 では
        provider 単位でのみ表現する。
        """
        ...

    # --- CRUD ---
    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        """新規 Issue を作成する。"""
        ...

    def view_issue(self, issue_id: str) -> Issue:
        """Issue を取得する。"""
        ...

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        """Issue を更新する。"""
        ...

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        """Issue にコメントを追加する。"""
        ...

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        """Issue を close する。"""
        ...

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Issue]:
        """Issue 一覧を取得する。"""
        ...

    # --- Labels ---
    def list_labels(self) -> list[Label]:
        """ラベル一覧。"""
        ...

    # --- Context ---
    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        """Skill 注入用の `IssueContext` を解決する。

        provider は Issue メタ情報（label / frontmatter / cache）を読み、
        9 変数体系の Issue 系 7 変数を組み立てる。`prompt.py` はこの結果を
        そのまま参照する（design.md L918）。
        """
        ...

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """branch 名から PR を逆引きし `PRContext` を返す。

        GitHubProvider が本実装、LocalProvider は ``return None`` の no-op。

        Returns:
            PRContext: branch に対応する open な PR が一意に存在する場合。
            None: PR が存在しない場合（branch 未 push / PR 未作成 等）。

        Raises:
            Provider 固有のエラー: 複数該当 / CLI / API 失敗時。
        """
        ...


@runtime_checkable
class IncidentSearchCapable(Protocol):
    """incident 検知・集約層（第1層・Issue #304）が要求する全件検索能力。

    v1 は GitHub provider のみが実装する。``isinstance(provider, IncidentSearchCapable)``
    が False の provider では incident 起票・照合は no-op（ローカル occurrence 記録のみ）。
    """

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        """label で絞った Issue を全件 pagination で返す（limit デフォルト依存禁止）。

        注: GitHub REST の issue 一覧はコメント本文を内包しない（``comments_url`` のみ）ため、
        戻り値の ``Issue.comments`` は空。コメントは ``list_issue_comments_all()`` で別途取得する。
        """
        ...

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        """対象 Issue の全コメントを pagination で取得する（100 件超でも全件）。"""
        ...
