"""GitHub のレビュースレッドを特定して解決する（``kaji pr resolve-thread`` のドメイン層、Issue #528）。

REST のインラインレビューコメント ID から、そのコメントが属する ``PullRequestReviewThread`` を
GraphQL の ``reviewThreads`` で特定し、``resolveReviewThread`` mutation で解決する。

- ``gh api graphql`` のサブプロセス呼出しは ``_run_graphql`` の 1 か所に寄せる
- GraphQL 応答は外部入力として Pydantic v2 で検証する
- ドメイン関数は print しない。例外 → 終了コード / stderr の変換は CLI handler
  （``kaji_harness.commands.pr``）だけが行う
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import HarnessError

PHASE_THREADS_LOOKUP = "reviewThreads lookup"
PHASE_COMMENTS_LOOKUP = "thread comments lookup"
PHASE_RESOLVE = "resolveReviewThread"

# ユーザー入力はクエリ文字列に埋め込まず、すべて GraphQL 変数（-f / -F）で渡す。
_REVIEW_THREADS_QUERY = """\
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          viewerCanResolve
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes { fullDatabaseId }
          }
        }
      }
    }
  }
}
"""

_THREAD_COMMENTS_QUERY = """\
query($threadId: ID!, $after: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { fullDatabaseId }
      }
    }
  }
}
"""

_RESOLVE_MUTATION = """\
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""


# ----------------------------------------------------------------------------
# 例外
# ----------------------------------------------------------------------------


class ReviewThreadError(HarnessError):
    """``kaji pr resolve-thread`` のドメインエラーの基底。"""


class ReviewThreadNotFound(ReviewThreadError):
    """PR の全レビュースレッドを辿っても該当コメントが無い。"""

    def __init__(self, repo: str, pr_id: str, comment_id: str) -> None:
        self.repo = repo
        self.pr_id = pr_id
        self.comment_id = comment_id
        super().__init__(
            f"review comment {comment_id} was not found in any review thread "
            f"of PR #{pr_id} ({repo})."
        )


class InvalidRepositoryError(ReviewThreadError):
    """repository が ``owner/name`` 形式でない（GraphQL の owner / name に分割できない）。"""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        super().__init__(f"repository must be in 'owner/name' form, got: {repo!r}.")


class ReviewThreadPermissionDenied(ReviewThreadError):
    """スレッドは未解決だが ``viewerCanResolve`` が false。"""

    def __init__(self, thread_id: str, comment_id: str) -> None:
        self.thread_id = thread_id
        self.comment_id = comment_id
        super().__init__(
            f"cannot resolve review thread {thread_id} for comment {comment_id}: "
            "viewerCanResolve is false (insufficient permission)."
        )


class ReviewThreadResolveFailed(ReviewThreadError):
    """mutation 応答の ``thread.isResolved`` が true でない。"""

    def __init__(self, thread_id: str, comment_id: str) -> None:
        self.thread_id = thread_id
        self.comment_id = comment_id
        super().__init__(
            f"resolveReviewThread did not resolve thread {thread_id} for comment {comment_id}."
        )


class GitHubGraphQLRequestError(ReviewThreadError):
    """``gh api graphql`` が非 0 終了した、または起動できなかった。

    ``gh_stderr`` は CLI handler が自前のメッセージより先に中継する gh の stderr。
    """

    def __init__(self, phase: str, gh_stderr: str) -> None:
        self.phase = phase
        self.gh_stderr = gh_stderr
        super().__init__(f"GitHub GraphQL request failed ({phase}).")


class GitHubGraphQLResponseError(ReviewThreadError):
    """GraphQL 応答が期待する形でない（JSON でない、必須フィールド欠落、null 等）。"""

    def __init__(self, phase: str, summary: str) -> None:
        self.phase = phase
        self.summary = summary
        super().__init__(f"unexpected GitHub GraphQL response ({phase}): {summary}")


# ----------------------------------------------------------------------------
# GraphQL 応答モデル（クエリで要求したフィールドだけを必須として宣言する）
# ----------------------------------------------------------------------------


class _GraphQLModel(BaseModel):
    # GraphQL は要求フィールドのみ返すが、将来のフィールド追加で壊れないよう ignore にする。
    model_config = ConfigDict(extra="ignore", frozen=True)


class _PageInfo(_GraphQLModel):
    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(alias="endCursor")


class _CommentNode(_GraphQLModel):
    full_database_id: str | None = Field(alias="fullDatabaseId")


class _CommentConnection(_GraphQLModel):
    page_info: _PageInfo = Field(alias="pageInfo")
    nodes: list[_CommentNode]


class _ThreadNode(_GraphQLModel):
    id: str
    is_resolved: bool = Field(alias="isResolved")
    viewer_can_resolve: bool = Field(alias="viewerCanResolve")
    comments: _CommentConnection


class _ThreadConnection(_GraphQLModel):
    page_info: _PageInfo = Field(alias="pageInfo")
    nodes: list[_ThreadNode]


class _PullRequestNode(_GraphQLModel):
    review_threads: _ThreadConnection = Field(alias="reviewThreads")


class _RepositoryNode(_GraphQLModel):
    pull_request: _PullRequestNode = Field(alias="pullRequest")


class _ReviewThreadsData(_GraphQLModel):
    repository: _RepositoryNode


class ReviewThreadsPage(_GraphQLModel):
    """``reviewThreads`` の 1 ページ分の応答。"""

    data: _ReviewThreadsData


class _ThreadCommentsNode(_GraphQLModel):
    comments: _CommentConnection


class _ThreadCommentsData(_GraphQLModel):
    node: _ThreadCommentsNode


class ThreadCommentsPage(_GraphQLModel):
    """``node(id:) { ... on PullRequestReviewThread { comments } }`` の 1 ページ分の応答。"""

    data: _ThreadCommentsData


class _ResolvedThread(_GraphQLModel):
    id: str
    is_resolved: bool = Field(alias="isResolved")


class _ResolvePayload(_GraphQLModel):
    thread: _ResolvedThread | None


class _ResolveData(_GraphQLModel):
    resolve_review_thread: _ResolvePayload = Field(alias="resolveReviewThread")


class ResolveReviewThreadPayload(_GraphQLModel):
    """``resolveReviewThread`` mutation の応答。"""

    data: _ResolveData


_ModelT = TypeVar("_ModelT", bound=BaseModel)


# ----------------------------------------------------------------------------
# 結果型
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewThreadRef:
    """特定したレビュースレッド。"""

    thread_id: str
    is_resolved: bool
    viewer_can_resolve: bool


ResolveResult = Literal["resolved", "already_resolved"]


@dataclass(frozen=True)
class ResolveOutcome:
    """``resolve_review_thread_for_comment`` の結果（stdout の JSON 1 行に対応）。"""

    comment_id: str
    thread_id: str
    result: ResolveResult

    def to_json_dict(self) -> dict[str, str]:
        """stdout に出す JSON object を返す。"""
        return {"comment_id": self.comment_id, "thread_id": self.thread_id, "result": self.result}


# ----------------------------------------------------------------------------
# gh api graphql 呼出し
# ----------------------------------------------------------------------------


def _run_graphql(
    query: str,
    *,
    phase: str,
    string_vars: dict[str, str],
    int_vars: dict[str, str] | None = None,
) -> str:
    """``gh api graphql`` を実行し stdout を返す。

    Args:
        query: GraphQL クエリ文字列（ユーザー入力を埋め込まない）。
        phase: エラーメッセージに含める処理段階名。
        string_vars: ``-f``（文字列）で渡す GraphQL 変数。
        int_vars: ``-F``（gh が数値へ型変換する）で渡す GraphQL 変数。

    Returns:
        gh の stdout（JSON 文字列）。

    Raises:
        GitHubGraphQLRequestError: gh を起動できない、または非 0 終了した場合。
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in string_vars.items():
        cmd.extend(["-f", f"{key}={value}"])
    for key, value in (int_vars or {}).items():
        cmd.extend(["-F", f"{key}={value}"])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GitHubGraphQLRequestError(phase, f"Error: failed to invoke 'gh': {exc}\n") from exc
    if result.returncode != 0:
        raise GitHubGraphQLRequestError(phase, result.stderr or "")
    return result.stdout


def _parse(model: type[_ModelT], stdout: str, *, phase: str) -> _ModelT:
    """GraphQL 応答を検証する。不一致は ``GitHubGraphQLResponseError``。"""
    try:
        return model.model_validate_json(stdout)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first["loc"]) or "<root>"
        summary = f"{loc}: {first['msg']}"
        if exc.error_count() > 1:
            summary += f" (+{exc.error_count() - 1} more)"
        raise GitHubGraphQLResponseError(phase, summary) from None


def _next_cursor(page_info: _PageInfo, *, phase: str) -> str:
    """``hasNextPage`` が true のときの ``endCursor`` を返す（欠落は応答不正）。"""
    if not page_info.end_cursor:
        raise GitHubGraphQLResponseError(phase, "pageInfo.endCursor is null while hasNextPage")
    return page_info.end_cursor


def _contains(comments: _CommentConnection, comment_id: str) -> bool:
    return any(node.full_database_id == comment_id for node in comments.nodes)


def _thread_has_comment(thread: _ThreadNode, comment_id: str) -> bool:
    """スレッドの全コメント（101 件目以降は ``node(id:)`` で辿る）に ``comment_id`` があるか。"""
    comments = thread.comments
    while True:
        if _contains(comments, comment_id):
            return True
        if not comments.page_info.has_next_page:
            return False
        after = _next_cursor(comments.page_info, phase=PHASE_COMMENTS_LOOKUP)
        stdout = _run_graphql(
            _THREAD_COMMENTS_QUERY,
            phase=PHASE_COMMENTS_LOOKUP,
            string_vars={"threadId": thread.id, "after": after},
        )
        comments = _parse(
            ThreadCommentsPage, stdout, phase=PHASE_COMMENTS_LOOKUP
        ).data.node.comments


def _split_repo(repo: str) -> tuple[str, str]:
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise InvalidRepositoryError(repo)
    return owner, name


# ----------------------------------------------------------------------------
# 公開 API
# ----------------------------------------------------------------------------


def find_review_thread(repo: str, pr_id: str, comment_id: str) -> ReviewThreadRef:
    """``comment_id`` が属するレビュースレッドを、全ページを辿って特定する。

    Args:
        repo: ``owner/name`` 形式の repository。
        pr_id: PR 番号（ASCII 10 進文字列。呼出側で検証済み）。
        comment_id: インラインレビューコメントの REST ID。根コメントでも返信でもよい。

    Returns:
        特定したスレッド。

    Raises:
        InvalidRepositoryError: ``repo`` が ``owner/name`` 形式でない場合。
        ReviewThreadNotFound: 全スレッドを辿っても該当コメントが無い場合。
        GitHubGraphQLRequestError: gh の呼出しが失敗した場合。
        GitHubGraphQLResponseError: 応答が期待する形でない場合。
    """
    owner, name = _split_repo(repo)
    after: str | None = None
    while True:
        string_vars = {"owner": owner, "name": name}
        if after is not None:
            string_vars["after"] = after
        stdout = _run_graphql(
            _REVIEW_THREADS_QUERY,
            phase=PHASE_THREADS_LOOKUP,
            string_vars=string_vars,
            int_vars={"number": pr_id},
        )
        threads = _parse(
            ReviewThreadsPage, stdout, phase=PHASE_THREADS_LOOKUP
        ).data.repository.pull_request.review_threads
        for thread in threads.nodes:
            if _thread_has_comment(thread, comment_id):
                return ReviewThreadRef(
                    thread_id=thread.id,
                    is_resolved=thread.is_resolved,
                    viewer_can_resolve=thread.viewer_can_resolve,
                )
        if not threads.page_info.has_next_page:
            raise ReviewThreadNotFound(repo, pr_id, comment_id)
        after = _next_cursor(threads.page_info, phase=PHASE_THREADS_LOOKUP)


def resolve_review_thread_for_comment(repo: str, pr_id: str, comment_id: str) -> ResolveOutcome:
    """``comment_id`` が属するレビュースレッドを解決する（解決済みなら何もしない）。

    Args:
        repo: ``owner/name`` 形式の repository。
        pr_id: PR 番号（ASCII 10 進文字列。呼出側で検証済み）。
        comment_id: インラインレビューコメントの REST ID（ASCII 10 進文字列。呼出側で検証済み）。

    Returns:
        ``resolved``（今回解決した）または ``already_resolved``（mutation を呼ばずに成功）。

    Raises:
        InvalidRepositoryError: ``repo`` が ``owner/name`` 形式でない場合。
        ReviewThreadNotFound: 該当コメントがどのスレッドにも無い場合。
        ReviewThreadPermissionDenied: 未解決かつ ``viewerCanResolve`` が false の場合。
        ReviewThreadResolveFailed: mutation 応答でスレッドが解決になっていない場合。
        GitHubGraphQLRequestError: gh の呼出しが失敗した場合。
        GitHubGraphQLResponseError: 応答が期待する形でない場合。
    """
    thread = find_review_thread(repo, pr_id, comment_id)
    if thread.is_resolved:
        return ResolveOutcome(comment_id, thread.thread_id, "already_resolved")
    if not thread.viewer_can_resolve:
        raise ReviewThreadPermissionDenied(thread.thread_id, comment_id)
    stdout = _run_graphql(
        _RESOLVE_MUTATION, phase=PHASE_RESOLVE, string_vars={"threadId": thread.thread_id}
    )
    resolved = _parse(
        ResolveReviewThreadPayload, stdout, phase=PHASE_RESOLVE
    ).data.resolve_review_thread.thread
    if resolved is None or not resolved.is_resolved:
        raise ReviewThreadResolveFailed(thread.thread_id, comment_id)
    return ResolveOutcome(comment_id, thread.thread_id, "resolved")
