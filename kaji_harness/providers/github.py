"""GitHubProvider: ``gh`` CLI 経由の Issue CRUD + IssueContext 解決。

Phase 1-2 で `cli_main.py` 上に書かれていた pass-through wrapper を
provider 化する。**Phase 3-ab の段階では `cli_main.py` の dispatcher は
切替えない**ため、本クラスは PR-3c で `get_provider()` 経由で初めて呼ばれる
（phase3-design.md § ロールアウト戦略, L226-244）。

外部挙動は変えず、構造のみ provider に集約する。
"""

from __future__ import annotations

import json
import re
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


# kaji marker: review state を comment body 先頭に埋め込む HTML コメント。
# 1 行目に置き、2 行目以降が user body。GitHub UI 上では HTML コメントとして
# 不可視のため、UI の review 体験を壊さない。self-PR では ``gh pr review --approve``
# が GitHub API で 422 拒否されるため、本 marker 付き comment を Issue Comments API
# に投稿することで approve シグナルを表現する（``cli_main._github_pr_review`` 参照）。
_KAJI_REVIEW_MARKER_PREFIX = "<!-- kaji-review: state="
_KAJI_REVIEW_MARKER_SUFFIX = " -->"

_REVIEW_STATES_VALID = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}

# ``build_kaji_review_marker`` の完全逆写像。1 行目だけを厳密照合し、本文中の
# marker 引用や部分一致を誤検出しない（``providers.markers.parse_kaji_verdict_marker``
# と同じ「build と対の public parser」パターン）。private ``_KAJI_REVIEW_MARKER_PREFIX``
# は github.py 内に留め、外部へは本 public parser だけを公開する（ADR 009）。
_KAJI_REVIEW_MARKER_RE = re.compile(r"^<!-- kaji-review: state=(?P<state>[A-Z_]+) -->$")


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


def parse_kaji_review_marker(line: str) -> str | None:
    """review marker 1 行から state を返す。marker でなければ ``None``。

    ``build_kaji_review_marker`` が生成する 1 行を完全一致で解析する
    ``build`` と対の public parser。comment body の 1 行目にだけ適用し、
    本文中の marker 引用や部分一致は ``None`` に落とす（fail-closed）。

    Args:
        line: Issue / PR comment body の 1 行目。

    Returns:
        ``APPROVED`` / ``CHANGES_REQUESTED`` / ``COMMENTED`` のいずれか。
        marker 行でない、または未知 state の場合は ``None``。
    """
    match = _KAJI_REVIEW_MARKER_RE.fullmatch(line)
    if match is None:
        return None
    state = match.group("state")
    if state not in _REVIEW_STATES_VALID:
        return None
    return state


def _comment_url(stdout: str) -> str:
    """``gh issue comment`` stdout から作成コメント URL を取り出す。

    Issue #288: 取得できない場合（空 stdout / URL 形式でない）は空文字を返し、
    コメント投稿自体は失敗させない（``ref`` は best-effort な参照値）。
    """
    first_line = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    return first_line if first_line.startswith(("http://", "https://")) else ""


@dataclass
class GitHubProvider:
    """``gh`` CLI を subprocess で叩く provider。

    Attributes:
        repo: ``owner/name`` 形式。``provider.github.repo`` config 由来。
        repo_root: 設計書 path / worktree path 計算に必要。
        default_branch: ``provider.github.default_branch`` config 由来。``main`` 等。
            `IssueContext.default_branch` の source として用いる
            （phase3d-design.md § 2 / § 3）。
        git_remote: ``provider.github.git_remote`` config 由来。default ``"origin"``。
            `IssueContext.git_remote` の source。skill 内 ``git push`` / ``git fetch``
            等の対象 remote 名。
        worktree_prefix: ``[paths].worktree_prefix`` config 由来。worktree dir 名の
            先頭 segment。空文字（無設定）なら ``build_worktree_dir`` 側で ``"kaji"``
            にフォールバックする（Issue #215）。
    """

    repo: str
    repo_root: Path
    default_branch: str = "main"
    git_remote: str = "origin"
    worktree_prefix: str = ""

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

    def _gh_json_slurp(self, *args: str) -> list[object]:
        """``gh api --paginate --slurp`` の複数 page JSON を 1 段 flatten して返す（Issue #304）。

        ``gh api --paginate`` は page ごとに別々の JSON を出力するため、既存 ``_gh_json``
        （stdout 全体への単一 ``json.loads``）は 2 page 以上で invalid JSON になる。``--slurp``
        は「各 page の JSON を要素として包んだ外側配列」を返すので、それを 1 回 parse して
        1 段 flatten する（list endpoint 専用。page 数 1 でも同経路）。
        """
        proc = self._run_gh("api", "--paginate", "--slurp", *args)
        if proc.returncode != 0:
            raise GitHubProviderError(
                f"gh api failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            pages = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubProviderError(f"gh api returned invalid JSON: {exc}") from exc
        if not isinstance(pages, list):
            raise GitHubProviderError("gh api --slurp did not return an outer array")
        flattened: list[object] = []
        for page in pages:
            if isinstance(page, list):
                flattened.extend(page)
            else:
                flattened.append(page)
        return flattened

    @staticmethod
    def _parse_rest_issue(payload: dict[str, object]) -> Issue:
        """GitHub REST ``GET /repos/{repo}/issues`` の 1 要素を `Issue` に写像する。"""
        labels_raw = payload.get("labels", []) or []
        labels: list[Label] = []
        if isinstance(labels_raw, list):
            for entry in labels_raw:
                if isinstance(entry, dict):
                    labels.append(Label(name=str(entry.get("name", "") or "")))
                elif isinstance(entry, str):
                    labels.append(Label(name=entry))
        number = payload.get("number")
        title = str(payload.get("title", "") or "")
        return Issue(
            id=str(number) if number is not None else "",
            title=title,
            body=str(payload.get("body", "") or ""),
            state=str(payload.get("state", "open") or "open").lower(),
            labels=labels,
            comments=[],
            slug=derive_slug_from_title(title),
        )

    @staticmethod
    def _parse_rest_comment(payload: dict[str, object]) -> Comment:
        """GitHub REST ``GET /repos/{repo}/issues/{n}/comments`` の 1 要素を `Comment` に写像する。"""
        user = payload.get("user")
        author = ""
        if isinstance(user, dict):
            author = str(user.get("login", "") or "")
        return Comment(
            author=author,
            body=str(payload.get("body", "") or ""),
            created_at=str(payload.get("created_at", "") or ""),
        )

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
            state_reason=str(payload.get("stateReason", "") or "").lower(),
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
            "number,title,body,state,stateReason,labels,comments",
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
        # Issue #288: stdout 1 行目の作成コメント URL を `Comment.ref` に載せる
        # （gh CLI の `CommentableRun` が `--quiet` 以外で必ず出力する）。
        return Comment(author="", body=body, created_at="", ref=_comment_url(proc.stdout))

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

    # -------- Incident search (Issue #304) ----------

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        """label で絞った Issue を全件 pagination で返す（``gh api --paginate --slurp``）。

        REST ``GET /repos/{repo}/issues`` は PR も返す（GitHub は全 PR を issue とみなす）ため、
        ``pull_request`` キーを持つ要素は flatten 後に除外する。``gh issue list --limit`` は
        上限依存があるため使わない（全件 pagination の契約 — #303 決定 F）。
        """
        query = f"repos/{self.repo}/issues?state={state}"
        if labels:
            query += "&labels=" + ",".join(labels)
        query += "&per_page=100"
        raw = self._gh_json_slurp(query)
        result: list[Issue] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if "pull_request" in entry:
                continue
            result.append(self._parse_rest_issue(entry))
        return result

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        """対象 Issue の全コメントを pagination で取得する（``gh api --paginate --slurp``）。"""
        query = f"repos/{self.repo}/issues/{issue_id}/comments?per_page=100"
        raw = self._gh_json_slurp(query)
        result: list[Comment] = []
        for entry in raw:
            if isinstance(entry, dict):
                result.append(self._parse_rest_comment(entry))
        return result

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
            worktree_dir=build_worktree_dir(prefix, issue.id, self.repo_root, self.worktree_prefix),
            design_path=build_design_path(issue.id, slug),
            provider_type="github",
            branch_prefix_fallback=fallback,
            default_branch=self.default_branch,
            git_remote=self.git_remote,
        )

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """branch から open PR を 1 件特定し ``PRContext`` を返す。

        ``gh pr list --repo <self.repo> --head <branch> --state open
        --json number,headRefName`` で 1 件特定する。
        0 件は ``None``、複数件は ``GitHubProviderError``。
        ``--repo`` は ``_run_gh`` が自動注入しないため args に明示する
        （既存 CRUD 経路と同じ規約）。
        """
        payload = self._gh_json(
            "pr",
            "list",
            "--repo",
            self.repo,
            "--head",
            branch_name,
            "--state",
            "open",
            "--json",
            "number,headRefName",
        )
        if not isinstance(payload, list):
            raise GitHubProviderError("gh pr list returned non-array JSON")
        numbers: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                raise GitHubProviderError("gh pr list returned non-object element")
            number = entry.get("number")
            if number is None:
                raise GitHubProviderError("gh pr list entry missing 'number' field")
            numbers.append(str(number))
        if not numbers:
            return None
        if len(numbers) > 1:
            raise GitHubProviderError(
                f"multiple open pull requests found for head branch {branch_name!r}: {numbers}"
            )
        return PRContext(pr_id=numbers[0], pr_ref=f"gh:{numbers[0]}")
