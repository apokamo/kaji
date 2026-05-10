"""GitHubProvider: ``gh`` CLI 経由の Issue CRUD + IssueContext 解決。

Phase 1-2 で `cli_main.py` 上に書かれていた pass-through wrapper を
provider 化する。**Phase 3-ab の段階では `cli_main.py` の dispatcher は
切替えない**ため、本クラスは PR-3c で `get_provider()` 経由で初めて呼ばれる
（phase3-design.md § ロールアウト戦略, L226-244）。

外部挙動は変えず、構造のみ provider に集約する。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ._mappings import labels_to_branch_prefix
from .context import (
    build_branch_name,
    build_design_path,
    build_worktree_dir,
    derive_slug_from_title,
    format_issue_ref,
)
from .models import Comment, Issue, IssueContext, Label, PRContext


class GitHubProviderError(RuntimeError):
    """``gh`` CLI 起動失敗 / 戻り値非ゼロ等。"""


@dataclass
class GitHubProvider:
    """``gh`` CLI を subprocess で叩く provider。

    Attributes:
        repo: ``owner/name`` 形式。``provider.github.repo`` config 由来。
        repo_root: 設計書 path / worktree path 計算に必要。
        default_branch: ``provider.github.default_branch`` config 由来。``main`` 等。
            `IssueContext.default_branch` の source として用いる
            （phase3d-design.md § 2 / § 3）。
    """

    repo: str
    repo_root: Path
    default_branch: str = "main"

    @property
    def is_readonly(self) -> bool:
        return False

    # -------- 内部 ----------

    def _run_gh(self, *args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
        """``gh`` を subprocess で起動。

        Phase 3-ab では ``--repo`` を明示し、cwd 非依存で動作する。
        """
        if shutil.which("gh") is None:
            raise GitHubProviderError(
                "'gh' CLI not found in PATH. Install GitHub CLI to use provider.type='github'."
            )
        cmd = ["gh", *args]
        try:
            return subprocess.run(
                cmd,
                check=False,
                capture_output=capture,
                text=True,
            )
        except OSError as exc:
            raise GitHubProviderError(f"failed to invoke 'gh': {exc}") from exc

    def _gh_json(self, *args: str) -> object:
        """``gh ... --json ...`` を起動し JSON を parse して返す。"""
        proc = self._run_gh(*args)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubProviderError(f"gh returned invalid JSON: {exc}") from exc

    @staticmethod
    def _parse_issue_payload(payload: dict[str, object]) -> Issue:
        """``gh issue view --json ...`` 出力を `Issue` に詰める。"""
        labels_raw = payload.get("labels", []) or []
        labels: list[Label] = []
        if isinstance(labels_raw, list):
            for entry in labels_raw:
                if isinstance(entry, dict):
                    labels.append(
                        Label(
                            name=str(entry.get("name", "")),
                            description=str(entry.get("description", "") or ""),
                            color=str(entry.get("color", "") or ""),
                        )
                    )
                elif isinstance(entry, str):
                    labels.append(Label(name=entry))
        comments_raw = payload.get("comments", []) or []
        comments: list[Comment] = []
        if isinstance(comments_raw, list):
            for entry in comments_raw:
                if isinstance(entry, dict):
                    author_obj = entry.get("author")
                    author = ""
                    if isinstance(author_obj, dict):
                        author = str(author_obj.get("login", "") or "")
                    elif isinstance(author_obj, str):
                        author = author_obj
                    comments.append(
                        Comment(
                            author=author,
                            body=str(entry.get("body", "") or ""),
                            created_at=str(entry.get("createdAt", "") or ""),
                        )
                    )
        number = payload.get("number")
        title = str(payload.get("title", "") or "")
        return Issue(
            id=str(number) if number is not None else "",
            title=title,
            body=str(payload.get("body", "") or ""),
            state=str(payload.get("state", "open") or "open").lower(),
            labels=labels,
            comments=comments,
            slug=derive_slug_from_title(title),
        )

    # -------- CRUD ----------

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        # `slug` は GitHub では title 由来で導出するため引数は受け取るが採用しない
        del slug
        args = ["issue", "create", "--repo", self.repo, "--title", title, "--body", body]
        for label in labels or []:
            args.extend(["--label", label])
        proc = self._run_gh(*args)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh issue create failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        # gh issue create は URL を stdout に出すので末尾の数値を取り出す
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        issue_number = url.rsplit("/", 1)[-1]
        if not issue_number.isdigit():
            raise GitHubProviderError(
                f"gh issue create did not return a recognizable issue URL: {proc.stdout!r}"
            )
        return self.view_issue(issue_number)

    def view_issue(self, issue_id: str) -> Issue:
        payload = self._gh_json(
            "issue",
            "view",
            issue_id,
            "--repo",
            self.repo,
            "--json",
            "number,title,body,state,labels,comments",
        )
        if not isinstance(payload, dict):
            raise GitHubProviderError("gh issue view returned non-object JSON")
        return self._parse_issue_payload(payload)

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        args = ["issue", "edit", issue_id, "--repo", self.repo]
        if title is not None:
            args.extend(["--title", title])
        if body is not None:
            args.extend(["--body", body])
        for label in add_labels or []:
            args.extend(["--add-label", label])
        for label in remove_labels or []:
            args.extend(["--remove-label", label])
        proc = self._run_gh(*args)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh issue edit failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return self.view_issue(issue_id)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        proc = self._run_gh("issue", "comment", issue_id, "--repo", self.repo, "--body", body)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh issue comment failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        # gh issue comment は created_at / author を返さない。Phase 3-ab では
        # 詳細不要として最小情報のみ返す。詳細が要る呼び出しは view_issue 経由。
        return Comment(author="", body=body, created_at="")

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        args = ["issue", "close", issue_id, "--repo", self.repo]
        if reason:
            args.extend(["--reason", reason])
        proc = self._run_gh(*args)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh issue close failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return self.view_issue(issue_id)

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Issue]:
        args = [
            "issue",
            "list",
            "--repo",
            self.repo,
            "--state",
            state,
            "--json",
            "number,title,body,state,labels",
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        payload = self._gh_json(*args)
        if not isinstance(payload, list):
            raise GitHubProviderError("gh issue list returned non-array JSON")
        result: list[Issue] = []
        for entry in payload:
            if isinstance(entry, dict):
                result.append(self._parse_issue_payload(entry))
        return result

    def list_labels(self) -> list[Label]:
        payload = self._gh_json(
            "label",
            "list",
            "--repo",
            self.repo,
            "--json",
            "name,description,color",
            "--limit",
            "200",
        )
        if not isinstance(payload, list):
            raise GitHubProviderError("gh label list returned non-array JSON")
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
        return out

    # -------- Context ----------

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        """label / title から `IssueContext` を組み立てる。

        Phase 3-ab では view_issue を 1 度呼び、label と title の両方を取得する。
        cache 戦略は `prompt.py` 側（kaji run プロセス境界）で適用する想定
        （phase3-design.md § IssueContext の解決タイミング, L314-322）。
        """
        issue = self.view_issue(issue_id)
        label_names = [label.name for label in issue.labels]
        prefix, fallback = labels_to_branch_prefix(label_names)
        slug = issue.slug or derive_slug_from_title(issue.title)
        return IssueContext(
            issue_id=issue.id,
            issue_ref=format_issue_ref(issue.id),
            issue_input=issue.id,
            slug=slug,
            branch_prefix=prefix,
            branch_name=build_branch_name(prefix, issue.id),
            worktree_dir=build_worktree_dir(prefix, issue.id, self.repo_root),
            design_path=build_design_path(issue.id, slug),
            provider_type="github",
            branch_prefix_fallback=fallback,
            default_branch=self.default_branch,
        )

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """no-op 実装。本実装は forge 採用後の別 Issue で扱う。

        本 Issue（``local-pc5090-7``）では `IssueProvider` Protocol 整合の
        ため ``return None`` のみ。GitHub 側で本実装すると
        ``gh pr list --head <branch>`` 等の subprocess hit が増えるが、
        skill 側暫定運用記述（``kaji pr list --search``）は本 Issue では
        削除しない（子 Issue ``local-pc5090-9`` の OUT スコープ）ため、
        no-op で十分。
        """
        del branch_name
        return None
