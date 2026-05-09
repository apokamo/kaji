"""GitLabProvider: ``glab`` CLI + ``glab api`` 経由の Issue CRUD + IssueContext 解決。

EPIC ``local-pc5090-4`` 確定事項 #1（CLI subprocess 必須）/ #3（``gitlab.com`` 固定）に基づく。
``GitHubProvider`` の ``gh`` CLI passthrough と対称構造を取り、構造的差異は
GitLab REST API の field 名（``iid`` / ``description`` / ``state='opened'`` / labels が
string array）に閉じる。

mutating 系（create/edit/comment/close）は ``glab issue <sub>`` を直接起動し、
read 系（view/list/list_labels）は ``glab api projects/<URL-encoded-repo>/...``
で REST JSON を取得する（``glab issue list`` は ``--output-format
details/ids/urls`` のみで構造化 JSON を出さないため）。

host 固定: 本 provider は ``provider.gitlab.repo`` のみで対象 forge を一意に決める
契約のため、すべての ``glab`` 起動に ``--hostname gitlab.com`` を default 注入する。
``glab`` の global flag ``--hostname`` は current git directory / login config による
host 解決を強制 override するため、これにより ``glab api`` / mutating 系の双方で
self-hosted host への誤送信を防ぐ（self-hosted 非対応は確定事項 #3）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ._mappings import labels_to_branch_prefix
from .context import (
    build_branch_name,
    build_design_path,
    build_worktree_dir,
    derive_slug_from_title,
)
from .models import Comment, Issue, IssueContext, Label


class GitLabProviderError(RuntimeError):
    """``glab`` CLI 起動失敗 / 戻り値非ゼロ / JSON parse 失敗等。"""


# 確定事項 #3: self-hosted 非対応 / ``gitlab.com`` 内部固定。``glab`` の host 解決
# （current git directory / login config）への暗黙依存を切り、``provider.gitlab.repo``
# のみで forge が一意に決まることを保証する。
_GITLAB_HOSTNAME = "gitlab.com"


@dataclass
class GitLabProvider:
    """``glab`` CLI を subprocess で叩く provider。

    Attributes:
        repo: ``group/project`` 形式（GitLab namespace path）。
            ``provider.gitlab.repo`` config 由来。``glab --repo`` に渡すほか、
            ``glab api projects/:id`` の URL encode 元としても使う。
        repo_root: 設計書 path / worktree path 計算用（GitHubProvider 同形）。
        default_branch: ``provider.gitlab.default_branch`` config 由来。
            ``IssueContext.default_branch`` の source。
    """

    repo: str
    repo_root: Path
    default_branch: str = "main"

    @property
    def is_readonly(self) -> bool:
        return False

    # -------- 内部 ----------

    def _run_glab(self, *args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
        """``glab`` を subprocess で起動する。

        ``glab`` の global flag ``--hostname gitlab.com`` を全 invocation に default 注入
        することで、current git directory / login config に基づく host 解決を強制
        override する（``glab api --help`` / ``glab issue --help`` 参照）。これにより
        self-hosted GitLab 環境や複数 host を持つ workstation でも、``provider.gitlab.repo``
        が指す ``gitlab.com`` 上の project だけが対象になることを保証する。

        ``--repo <repo>`` は呼出側で ``args`` に明示する責務。``glab api`` は
        ``--repo`` を受け付けないため endpoint URL 側に encoded repo を埋める。
        """
        if shutil.which("glab") is None:
            raise GitLabProviderError(
                "'glab' CLI not found in PATH. Install glab to use provider.type='gitlab'."
            )
        cmd = ["glab", "--hostname", _GITLAB_HOSTNAME, *args]
        try:
            return subprocess.run(
                cmd,
                check=False,
                capture_output=capture,
                text=True,
            )
        except OSError as exc:
            raise GitLabProviderError(f"failed to invoke 'glab': {exc}") from exc

    def _glab_api_get(self, endpoint: str) -> object:
        """``glab api <endpoint>`` を起動し JSON を parse して返す。

        Args:
            endpoint: ``projects/<encoded-repo>/issues/<iid>`` 等の REST path。
                呼出側で URL encode 済みであること（``_encoded_repo`` 経由）。
        """
        proc = self._run_glab("api", endpoint)
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab api failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GitLabProviderError(f"glab returned invalid JSON: {exc}") from exc

    def _encoded_repo(self) -> str:
        """``self.repo`` を URL encode して ``glab api`` の ``:id`` に渡す形に変換する。

        ``group/project`` → ``group%2Fproject``。``safe=""`` を渡すことで ``/``
        も encode する（GitLab REST の ``:id`` placeholder は URL-encoded path
        を受理する）。
        """
        return quote(self.repo, safe="")

    @staticmethod
    def _parse_issue_payload(payload: dict[str, object]) -> Issue:
        """GitLab REST API の issue JSON を `Issue` に詰める。

        GitLab 固有の field 名差異を吸収する:

        - ``iid`` → ``Issue.id`` （project-local IID。global ``id`` は採らない）
        - ``description`` → ``Issue.body``
        - ``state='opened'`` → ``"open"`` に正規化
        - ``labels: string[]`` → ``[Label(name=...)]``
        """
        labels_raw = payload.get("labels", []) or []
        labels: list[Label] = []
        if isinstance(labels_raw, list):
            for entry in labels_raw:
                if isinstance(entry, str):
                    labels.append(Label(name=entry))
                elif isinstance(entry, dict):
                    labels.append(
                        Label(
                            name=str(entry.get("name", "")),
                            description=str(entry.get("description", "") or ""),
                            color=str(entry.get("color", "") or ""),
                        )
                    )
        iid = payload.get("iid")
        title = str(payload.get("title", "") or "")
        raw_state = str(payload.get("state", "open") or "open").lower()
        normalized_state = "open" if raw_state == "opened" else raw_state
        return Issue(
            id=str(iid) if iid is not None else "",
            title=title,
            body=str(payload.get("description", "") or ""),
            state=normalized_state,
            labels=labels,
            comments=[],
            slug=derive_slug_from_title(title),
        )

    @staticmethod
    def _parse_comments_payload(payload: list[object]) -> list[Comment]:
        """GitLab notes API の JSON を `Comment` list に詰める。

        ``system: true`` の note（state change 等の system note）は除外する。
        """
        comments: list[Comment] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            if entry.get("system") is True:
                continue
            author_obj = entry.get("author")
            author = ""
            if isinstance(author_obj, dict):
                author = str(author_obj.get("username", "") or "")
            elif isinstance(author_obj, str):
                author = author_obj
            comments.append(
                Comment(
                    author=author,
                    body=str(entry.get("body", "") or ""),
                    created_at=str(entry.get("created_at", "") or ""),
                )
            )
        return comments

    # -------- CRUD ----------

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        del slug  # GitLab では title から slug を導出するため引数は採用しない
        args = [
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--description",
            body,
            "--yes",
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])
        proc = self._run_glab(*args)
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab issue create failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        # ``glab issue create`` は最終行に
        # ``https://gitlab.com/<group>/<project>/-/issues/<iid>`` を出すので末尾の
        # 数値を取り出す。
        url_line = ""
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("http"):
                url_line = line
                break
        iid = url_line.rsplit("/", 1)[-1] if url_line else ""
        if not iid.isdigit():
            raise GitLabProviderError(
                f"glab issue create did not return a recognizable issue URL: {proc.stdout!r}"
            )
        return self.view_issue(iid)

    def view_issue(self, issue_id: str) -> Issue:
        """Issue 本体 + notes (= comments) を取得する。

        notes は ``?per_page=100`` の 1 ページ目のみ取得する（pagination 未対応、
        list_labels と同方針）。100 件に達した場合は warning を残し、後続 pagination
        対応を検出可能にする。完全な pagination は実需要が出るまで保留（design §
        list_labels の pagination 方針 / Should Fix #1 反映）。
        """
        encoded = self._encoded_repo()
        payload = self._glab_api_get(f"projects/{encoded}/issues/{issue_id}")
        if not isinstance(payload, dict):
            raise GitLabProviderError("glab api returned non-object JSON for issue view")
        issue = self._parse_issue_payload(payload)
        notes_payload = self._glab_api_get(
            f"projects/{encoded}/issues/{issue_id}/notes?per_page=100"
        )
        if not isinstance(notes_payload, list):
            raise GitLabProviderError("glab api returned non-array JSON for notes")
        comments = self._parse_comments_payload(notes_payload)
        if len(notes_payload) == 100:
            import logging

            logging.getLogger(__name__).warning(
                "GitLabProvider.view_issue: notes returned 100 entries (per_page cap), "
                "pagination is not implemented; comments beyond the first page may be missing."
            )
        return Issue(
            id=issue.id,
            title=issue.title,
            body=issue.body,
            state=issue.state,
            labels=issue.labels,
            comments=comments,
            slug=issue.slug,
        )

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        args = ["issue", "update", issue_id, "--repo", self.repo]
        if title is not None:
            args.extend(["--title", title])
        if body is not None:
            args.extend(["--description", body])
        if add_labels:
            args.extend(["--label", ",".join(add_labels)])
        if remove_labels:
            args.extend(["--unlabel", ",".join(remove_labels)])
        proc = self._run_glab(*args)
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab issue update failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return self.view_issue(issue_id)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        proc = self._run_glab(
            "issue",
            "note",
            issue_id,
            "--repo",
            self.repo,
            "--message",
            body,
        )
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab issue note failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        # ``glab issue note`` は created_at / author を返さないため、最小情報のみ返す
        # （GitHubProvider と同方針）。詳細が要る呼び出しは view_issue 経由。
        return Comment(author="", body=body, created_at="")

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        del reason  # GitLab issue close は理由を受け取らない
        proc = self._run_glab("issue", "close", issue_id, "--repo", self.repo)
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab issue close failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return self.view_issue(issue_id)

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Issue]:
        """Issue 一覧を 1 リクエスト分（最大 ``per_page=100``）取得する。

        ``limit`` は GitLab REST の ``per_page`` に転写し、上限 100 で頭打ちにする
        （GitLab pagination doc 参照）。pagination ループは未実装。100 件に達した
        場合は warning を残す（list_labels / view_issue notes と同方針）。
        """
        encoded = self._encoded_repo()
        # GitLab REST: state は ``opened`` / ``closed`` / ``all``
        gl_state = "opened" if state == "open" else state
        per_page = min(limit, 100) if limit is not None else 100
        params = [f"state={gl_state}", f"per_page={per_page}"]
        if labels:
            params.append(f"labels={quote(','.join(labels), safe='')}")
        endpoint = f"projects/{encoded}/issues?{'&'.join(params)}"
        payload = self._glab_api_get(endpoint)
        if not isinstance(payload, list):
            raise GitLabProviderError("glab api returned non-array JSON for issue list")
        result: list[Issue] = []
        for entry in payload:
            if isinstance(entry, dict):
                result.append(self._parse_issue_payload(entry))
        if len(result) == 100:
            import logging

            logging.getLogger(__name__).warning(
                "GitLabProvider.list_issues: returned 100 issues (per_page cap), "
                "pagination is not implemented; issues beyond the first page may be missing."
            )
        return result

    def list_labels(self) -> list[Label]:
        encoded = self._encoded_repo()
        payload = self._glab_api_get(f"projects/{encoded}/labels?per_page=100")
        if not isinstance(payload, list):
            raise GitLabProviderError("glab api returned non-array JSON for labels")
        out: list[Label] = []
        for entry in payload:
            if isinstance(entry, dict):
                out.append(
                    Label(
                        name=str(entry.get("name", "")),
                        description=str(entry.get("description", "") or ""),
                        color=str(entry.get("color", "") or ""),
                    )
                )
        # GitLab REST の per_page 上限 100 に達した場合は pagination 未対応のため
        # 後続呼び出しが silent に欠落しないよう警告を残す。完全な pagination は
        # 子 Issue で実需要が出た段階で追加する（design § list_labels の pagination 方針）。
        if len(out) == 100:
            import logging

            logging.getLogger(__name__).warning(
                "GitLabProvider.list_labels: returned 100 labels, "
                "pagination is not implemented; labels beyond the first page may be missing."
            )
        return out

    # -------- PR (MR) helpers ----------
    #
    # 本セクションは Issue local-pc5090-6 で追加。``kaji pr`` の GitLab dispatcher が
    # 呼ぶ薄い helper 群と、純粋な GitLab→GitHub shape 変換層 ``_GitLabPrShape`` を
    # 提供する。``pr_*`` の単純な CLI passthrough は cli_main 側で ``_run_glab`` を
    # 直接呼ぶ薄い経路を採るため、provider 側に集約するのは:
    #
    # 1. Branch 名 → MR IID の解決（``resolve_mr_iid_from_branch``）
    # 2. discussions API + 互換 shape 変換（``list_pr_review_comments``）
    # 3. notes API + approvals API + marker による reviews 合成（``list_pr_reviews``）
    # 4. discussion thread への reply（``reply_to_pr_comment``）
    #
    # の 4 つに限る。Tier B の create/view/list/merge/comment/review は dispatcher
    # 側で sub 名 / flag rewrite + ``_run_glab`` 起動で完結する。

    def resolve_mr_iid_from_branch(self, branch: str) -> str:
        """``glab mr list --source-branch <branch>`` で branch から MR IID を引く。

        ``issue-close`` Step 3 が ``kaji pr merge [branch_name]`` を呼ぶ経路で、
        branch 名 → IID の正引きが必要になる（GitHub の ``gh pr merge <branch>``
        は branch を直接受理するが、glab は IID のみ受理）。

        Returns:
            project-local IID（``"42"``）。見つからない / 複数該当の場合は
            ``GitLabProviderError``。
        """
        encoded = self._encoded_repo()
        # state=opened に絞り込まないと過去 close MR とぶつかる可能性がある。
        from urllib.parse import quote as _quote

        endpoint = (
            f"projects/{encoded}/merge_requests"
            f"?source_branch={_quote(branch, safe='')}&state=opened"
        )
        payload = self._glab_api_get(endpoint)
        if not isinstance(payload, list):
            raise GitLabProviderError(
                f"glab api returned non-array JSON for MR lookup by branch {branch!r}"
            )
        iids = [
            str(entry["iid"]) for entry in payload if isinstance(entry, dict) and "iid" in entry
        ]
        if not iids:
            raise GitLabProviderError(f"no open merge request found for source branch {branch!r}")
        if len(iids) > 1:
            raise GitLabProviderError(
                f"multiple open merge requests found for source branch {branch!r}: {iids}"
            )
        return iids[0]

    def list_pr_review_comments(self, mr_iid: str) -> list[dict[str, object]]:
        """``glab api .../merge_requests/<iid>/discussions`` を GitHub 互換 subset に整形。"""
        encoded = self._encoded_repo()
        payload = self._glab_api_get(f"projects/{encoded}/merge_requests/{mr_iid}/discussions")
        if not isinstance(payload, list):
            raise GitLabProviderError("glab api returned non-array JSON for MR discussions")
        return _GitLabPrShape.to_github_review_comments(payload)

    def list_pr_reviews(self, mr_iid: str) -> list[dict[str, object]]:
        """notes + approvals を join し、GitHub ``pulls/<N>/reviews`` 互換 list を返す。

        詳細仕様は設計書 § ``reviews`` contract の合成方法 を参照。
        """
        encoded = self._encoded_repo()
        notes_payload = self._glab_api_get(
            f"projects/{encoded}/merge_requests/{mr_iid}/notes?per_page=100"
        )
        approvals_payload = self._glab_api_get(
            f"projects/{encoded}/merge_requests/{mr_iid}/approvals"
        )
        if not isinstance(notes_payload, list):
            raise GitLabProviderError("glab api returned non-array JSON for MR notes")
        if not isinstance(approvals_payload, dict):
            raise GitLabProviderError("glab api returned non-object JSON for MR approvals")
        return _GitLabPrShape.to_github_reviews(notes_payload, approvals_payload)

    def reply_to_pr_comment(self, mr_iid: str, *, discussion_id: str, body: str) -> None:
        """discussion thread に reply note を POST する。

        ``glab api`` の POST 引数 ``-f body=<text>`` を使う。
        """
        encoded = self._encoded_repo()
        endpoint = f"projects/{encoded}/merge_requests/{mr_iid}/discussions/{discussion_id}/notes"
        proc = self._run_glab("api", "--method", "POST", endpoint, "-f", f"body={body}")
        if proc.returncode != 0:
            raise GitLabProviderError(
                f"glab api POST {endpoint} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )

    def get_mr_approval_state(self, mr_iid: str) -> dict[str, object]:
        """approvals API の生 payload（``approved_by`` / ``approvals_left`` 等）を返す。

        ``--request-changes`` 時の revoke 要否判定（自分が approve 済か）に使う。
        """
        encoded = self._encoded_repo()
        payload = self._glab_api_get(f"projects/{encoded}/merge_requests/{mr_iid}/approvals")
        if not isinstance(payload, dict):
            raise GitLabProviderError("glab api returned non-object JSON for MR approvals")
        return payload

    # -------- Context ----------

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        """label / title から `IssueContext` を組み立てる（GitHubProvider と同形）。"""
        issue = self.view_issue(issue_id)
        label_names = [label.name for label in issue.labels]
        prefix, fallback = labels_to_branch_prefix(label_names)
        slug = issue.slug or derive_slug_from_title(issue.title)
        return IssueContext(
            issue_id=issue.id,
            issue_ref=f"gl:{issue.id}",
            issue_input=issue.id,
            slug=slug,
            branch_prefix=prefix,
            branch_name=build_branch_name(prefix, issue.id),
            worktree_dir=build_worktree_dir(prefix, issue.id, self.repo_root),
            design_path=build_design_path(issue.id, slug),
            provider_type="gitlab",
            branch_prefix_fallback=fallback,
            default_branch=self.default_branch,
        )


# ----------------------------------------------------------------------------
# _GitLabPrShape — pure GitLab → GitHub shape 変換層
# ----------------------------------------------------------------------------
#
# Issue local-pc5090-6 で導入。``glab mr view --output json`` 等の payload を
# ``gh pr view --json`` 互換 dict に変換する純粋関数群。subprocess を呼ばず
# JSON 構造のみを扱うため Small テスト容易。
#
# 設計書 § ``reviews`` contract の合成方法 / § テスト戦略 § Small に対応。

# kaji marker: review state を note body 先頭に埋め込む HTML コメント。
# 1 行目に置き、2 行目以降が user body。GitLab UI 上では HTML コメントとして
# 不可視のため、UI の review 体験を壊さない。
_KAJI_REVIEW_MARKER_PREFIX = "<!-- kaji-review: state="
_KAJI_REVIEW_MARKER_SUFFIX = " -->"

_REVIEW_STATES_VALID = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}


def build_kaji_review_marker(state: str) -> str:
    """``state`` から marker 文字列（先頭行のみ、改行なし）を組み立てる。

    Args:
        state: ``APPROVED`` / ``CHANGES_REQUESTED`` / ``COMMENTED`` のいずれか。

    Raises:
        ValueError: 不明な state。
    """
    if state not in _REVIEW_STATES_VALID:
        raise ValueError(f"invalid review state {state!r}: expected one of {_REVIEW_STATES_VALID}")
    return f"{_KAJI_REVIEW_MARKER_PREFIX}{state}{_KAJI_REVIEW_MARKER_SUFFIX}"


def _parse_kaji_review_marker(body: str) -> tuple[str, str] | None:
    """note body 先頭行が kaji marker なら ``(state, body_without_marker)`` を返す。

    marker 形式不正 / state が ``_REVIEW_STATES_VALID`` 外 → ``None``。
    """
    if not body.startswith(_KAJI_REVIEW_MARKER_PREFIX):
        return None
    head, _, tail = body.partition("\n")
    if not head.endswith(_KAJI_REVIEW_MARKER_SUFFIX):
        return None
    state = head[len(_KAJI_REVIEW_MARKER_PREFIX) : -len(_KAJI_REVIEW_MARKER_SUFFIX)]
    if state not in _REVIEW_STATES_VALID:
        return None
    return state, tail


class _GitLabPrShape:
    """GitLab REST API JSON → GitHub ``gh pr`` 互換 dict 変換。

    すべて ``@staticmethod``。インスタンス化しない。
    """

    @staticmethod
    def to_github(payload: dict[str, object]) -> dict[str, object]:
        """``glab mr view --output json`` の dict を GitHub ``pr view --json`` 互換に。

        変換ルール:

        - ``iid`` → ``number``（int として保持）
        - ``state`` ``opened`` → ``OPEN``、``closed`` → ``CLOSED``、``merged`` → ``MERGED``
          （GitHub の state 命名は upper-case 列挙値）
        - ``description`` → ``body``
        - ``title`` → ``title`` (そのまま)
        - ``source_branch`` → ``headRefName``
        - ``target_branch`` → ``baseRefName``
        - ``web_url`` → ``url``
        - ``author.username`` → ``author.login``（dict の中で互換 key）
        - ``labels: list[str]`` → ``labels: [{"name": str}]``
        """
        iid = payload.get("iid")
        number: object
        if iid is None:
            number = None
        elif isinstance(iid, int):
            number = iid
        else:
            try:
                number = int(str(iid))
            except (TypeError, ValueError):
                number = iid
        gl_state = str(payload.get("state", "") or "").lower()
        gh_state_map = {
            "opened": "OPEN",
            "closed": "CLOSED",
            "merged": "MERGED",
            "locked": "LOCKED",
        }
        state = gh_state_map.get(gl_state, gl_state.upper())
        author_obj = payload.get("author")
        author: dict[str, object] = {}
        if isinstance(author_obj, dict):
            author = {"login": str(author_obj.get("username", "") or "")}
        labels_raw = payload.get("labels", []) or []
        labels: list[dict[str, object]] = []
        if isinstance(labels_raw, list):
            for entry in labels_raw:
                if isinstance(entry, str):
                    labels.append({"name": entry})
                elif isinstance(entry, dict):
                    labels.append({"name": str(entry.get("name", "") or "")})
        return {
            "number": number,
            "title": str(payload.get("title", "") or ""),
            "body": str(payload.get("description", "") or ""),
            "state": state,
            "headRefName": str(payload.get("source_branch", "") or ""),
            "baseRefName": str(payload.get("target_branch", "") or ""),
            "url": str(payload.get("web_url", "") or ""),
            "author": author,
            "labels": labels,
        }

    @staticmethod
    def to_github_list(payload: list[object]) -> list[dict[str, object]]:
        """``glab mr list -F json`` の list を GitHub ``pulls`` array shape に。"""
        out: list[dict[str, object]] = []
        for entry in payload:
            if isinstance(entry, dict):
                out.append(_GitLabPrShape.to_github(entry))
        return out

    @staticmethod
    def to_github_review_comments(
        discussions_payload: list[object],
    ) -> list[dict[str, object]]:
        """``GET .../discussions`` を ``pulls/<N>/comments`` 互換 subset に。

        各 discussion の ``notes[0]`` を 1 entry とする（GitHub の review-comment は
        thread 起点を 1 entry として扱う流儀に揃える）。

        出力 entry shape:
        - ``id``: ``"<discussion_id>:<note_id>"`` 形式の opaque string
        - ``path``: file path（diff 上のコメントの場合）
        - ``line``: 行番号（diff 上のコメントの場合）
        - ``body``: note body（GitLab は marker の概念なしのため raw を流す）
        - ``user``: ``{"login": <username>}``
        """
        out: list[dict[str, object]] = []
        for entry in discussions_payload:
            if not isinstance(entry, dict):
                continue
            discussion_id = str(entry.get("id", "") or "")
            notes = entry.get("notes", []) or []
            if not isinstance(notes, list) or not notes:
                continue
            head_note = notes[0]
            if not isinstance(head_note, dict):
                continue
            if head_note.get("system") is True:
                # state change 等の system note を含む discussion はスキップ
                continue
            note_id = str(head_note.get("id", "") or "")
            author_obj = head_note.get("author")
            login = ""
            if isinstance(author_obj, dict):
                login = str(author_obj.get("username", "") or "")
            position = head_note.get("position")
            path = ""
            line: object = None
            if isinstance(position, dict):
                path = str(position.get("new_path", "") or position.get("old_path", "") or "")
                # GitLab の position.new_line が None の場合は old_line を採る。
                # GitHub では line は int / null。
                raw_line = position.get("new_line", position.get("old_line"))
                if raw_line is not None:
                    try:
                        line = int(raw_line)
                    except (TypeError, ValueError):
                        line = None
            out.append(
                {
                    "id": f"{discussion_id}:{note_id}",
                    "path": path,
                    "line": line,
                    "body": str(head_note.get("body", "") or ""),
                    "user": {"login": login},
                }
            )
        return out

    @staticmethod
    def to_github_reviews(
        notes_payload: list[object],
        approvals_payload: dict[str, object],
    ) -> list[dict[str, object]]:
        """notes + approvals を join し ``pulls/<N>/reviews`` 互換 list を返す。

        詳細仕様は設計書 § ``reviews`` contract の合成方法 を参照:

        1. body 先頭に kaji marker を持つ note → ``state`` / ``body``（marker 剥がし後）
           を復元
        2. approvals API の ``approved_by[]`` のうち marker note を持たない approver
           は ``state="APPROVED"`` / ``body=""`` で補完
        3. ``submitted_at`` 昇順でソート
        4. system note / marker 形式不正 note は無視（fail-fast しない）
        """
        reviews: list[dict[str, object]] = []
        users_with_marker_note: set[str] = set()
        for entry in notes_payload:
            if not isinstance(entry, dict):
                continue
            if entry.get("system") is True:
                continue
            body = str(entry.get("body", "") or "")
            parsed = _parse_kaji_review_marker(body)
            if parsed is None:
                continue
            state, body_without_marker = parsed
            author_obj = entry.get("author")
            login = ""
            if isinstance(author_obj, dict):
                login = str(author_obj.get("username", "") or "")
            created_at = str(entry.get("created_at", "") or "")
            reviews.append(
                {
                    "user": {"login": login},
                    "state": state,
                    "body": body_without_marker,
                    "submitted_at": created_at,
                }
            )
            if login:
                users_with_marker_note.add(login)

        approved_by_raw = approvals_payload.get("approved_by", []) or []
        if isinstance(approved_by_raw, list):
            for approver_entry in approved_by_raw:
                if not isinstance(approver_entry, dict):
                    continue
                user_obj = approver_entry.get("user")
                if not isinstance(user_obj, dict):
                    continue
                login = str(user_obj.get("username", "") or "")
                if not login or login in users_with_marker_note:
                    continue
                # GitLab approvals API は approver ごとの timestamp を返さないため、
                # ``submitted_at`` は空文字で補完する（GitHub 側は ISO8601 文字列を
                # 期待するが空文字でも ``gh api`` の jq 経路は壊れない）。
                reviews.append(
                    {
                        "user": {"login": login},
                        "state": "APPROVED",
                        "body": "",
                        "submitted_at": "",
                    }
                )

        # submitted_at 昇順。空文字列は最後に置く（marker note 由来のものを優先表示）。
        def _sort_key(r: dict[str, object]) -> tuple[int, str]:
            ts = str(r.get("submitted_at", "") or "")
            return (1 if not ts else 0, ts)

        reviews.sort(key=_sort_key)
        return reviews
