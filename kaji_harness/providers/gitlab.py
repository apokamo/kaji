"""GitLabProvider: ``glab`` CLI + ``glab api`` 経由の Issue CRUD + IssueContext 解決。

EPIC ``local-pc5090-4`` 確定事項 #1（CLI subprocess 必須）/ #3（``gitlab.com`` 固定）に基づく。
``GitHubProvider`` の ``gh`` CLI passthrough と対称構造を取り、構造的差異は
GitLab REST API の field 名（``iid`` / ``description`` / ``state='opened'`` / labels が
string array）に閉じる。

mutating 系（create/edit/comment/close）は ``glab issue <sub>`` を直接起動し、
read 系（view/list/list_labels）は ``glab api projects/<URL-encoded-repo>/...``
で REST JSON を取得する（``glab issue list`` は ``--output-format
details/ids/urls`` のみで構造化 JSON を出さないため）。
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

        ``--repo <repo>`` は呼出側で ``args`` に明示する責務。``glab api`` は
        ``--repo`` を受け付けないため endpoint URL 側に encoded repo を埋める。
        """
        if shutil.which("glab") is None:
            raise GitLabProviderError(
                "'glab' CLI not found in PATH. Install glab to use provider.type='gitlab'."
            )
        cmd = ["glab", *args]
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
