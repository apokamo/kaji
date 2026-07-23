"""``kaji pr`` 全経路 + gh CLI 転送層（集約。#283 R1 決定 D1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from ..errors import ConfigLoadError, ConfigNotFoundError
from ..providers import get_provider
from ..providers.github import build_kaji_review_marker, parse_kaji_review_marker
from ..providers.local import LocalProvider
from .config import _load_config_for_dispatch
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR
from .output import _compose_json_and_jq, _read_body_arg

_FORGE_METHOD_FLAGS = {"--merge", "--squash", "--rebase"}


def _user_specified_repo(args: list[str]) -> bool:
    """argv 内に user 指定の ``--repo`` / ``-R`` 系トークンが含まれるかを返す。

    pflag (gh の flag parser) が受理する以下 5 形式を検出する:

    - 独立トークン long:  ``--repo owner/name``
    - 独立トークン short: ``-R owner/name``
    - インライン long:    ``--repo=owner/name``
    - インライン short=:  ``-R=owner/name``
    - 短縮連結:           ``-Rowner/name``
    """
    for a in args:
        if a in ("--repo", "-R"):
            return True
        if a.startswith("--repo="):
            return True
        if a.startswith("-R") and len(a) > 2:
            return True
    return False


def _forward_to_gh(group: str, raw_args: list[str], *, repo: str | None = None) -> int:
    """`gh <group> ...` に引数を転送する wrapper。

    `pr merge` の method flag は露出せず常に `--merge` (= no-ff) 固定で渡す。
    詳細: ``docs/guides/git-commit-flow.md``。

    `argparse.REMAINDER` は先頭の `--` を残したり残さなかったりするため、
    user 入力の意味を変えないよう先頭の単独 `--` のみを除去する。

    Phase 3-c rev #3（review #3 反映）:

    - ``repo`` が指定されると ``--repo <owner/name>`` を末尾に強制注入する
      （既に user が ``--repo`` を渡している場合は user 値を尊重し触らない）
    - 用途: ``provider.type='github'`` の `[provider.github] repo` を
      尊重し、worktree の git remote / fork による silent な書き先誤りを防ぐ
    """
    if not shutil.which("gh"):
        print(
            "Error: 'gh' CLI not found in PATH. "
            "Install GitHub CLI to use 'kaji issue' / 'kaji pr'.",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_ERROR

    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]

    if group == "pr" and args and args[0] == "merge":
        # method flag を除去し、常に --merge (= no-ff 相当) を強制する
        # ``docs/guides/git-commit-flow.md`` の merge 規約に従う
        head = [args[0]]
        rest = [a for a in args[1:] if a not in _FORGE_METHOD_FLAGS]
        args = head + rest + ["--merge"]

    if repo and not _user_specified_repo(args):
        # gh は --repo を sub の前後どちらでも受理する。末尾追加で副作用最小
        args = [*args, "--repo", repo]

    cmd = ["gh", group, *args]
    try:
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"Error: failed to invoke 'gh': {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return result.returncode


_PR_BUILTIN_SUBCOMMANDS = {"review-comments", "reviews", "reply-to-comment"}

_PR_BARE_PROVIDER_ERROR = (
    "Error: 'kaji pr' is a forge-only command and cannot run under "
    "provider.type='local'.\n"
    "Pull request concept does not exist in local mode (bare provider). "
    "Use git/issue operations directly:\n\n"
    "  - Code review:        /issue-review-code, /issue-fix-code, "
    "/issue-verify-code\n"
    "  - Merge + close:      /issue-close (executes 'git merge --no-ff' + "
    "frontmatter update)\n"
    "  - Branch listing:     git branch --list 'feat/local-*'\n\n"
    "To switch back to GitHub mode (e.g. after the outage), edit\n"
    '.kaji/config.local.toml and set [provider] type = "github" (or remove the\n'
    "overlay so the tracked .kaji/config.toml takes effect).\n"
)


def _is_ascii_decimal(s: str) -> bool:
    """True iff ``s`` is a non-empty ASCII decimal string.

    ``str.isdigit()`` accepts Unicode digit characters (e.g. ``"１２３"``),
    which would silently produce a malformed REST API path. GitHub
    PR / comment IDs are always ASCII decimals, so reject anything else.
    """
    return bool(s) and s.isascii() and s.isdigit()


_GH_MISSING_GUIDANCE = (
    "Error: 'gh' CLI not found in PATH. "
    "Install GitHub CLI to use 'kaji pr review-comments' / 'reviews' / "
    "'reply-to-comment' / 'review-poll' (Phase 2).\n"
)


def _detect_repo(*, override: str | None = None) -> str | None:
    """Return repository in `owner/name` form.

    Phase 3-c rev #3 で ``override`` を追加（review #3 反映）:

    - ``override`` が non-empty → そのまま採用（``[provider.github] repo`` 由来）
    - 不在 → ``gh repo view`` で current repo を auto-detect する legacy 経路
    """
    if override:
        return override
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    repo = result.stdout.strip()
    return repo or None


def _forward_pr_review_comments(
    pr_id: str,
    *,
    json_fields: list[str] | None,
    jq_expr: str | None,
    repo_override: str | None = None,
) -> int:
    """Forward to ``gh api repos/<repo>/pulls/<N>/comments``."""
    return _forward_pr_api_list(
        pr_id,
        path_suffix="comments",
        json_fields=json_fields,
        jq_expr=jq_expr,
        repo_override=repo_override,
    )


def _forward_pr_reviews(
    pr_id: str,
    *,
    json_fields: list[str] | None,
    jq_expr: str | None,
    repo_override: str | None = None,
) -> int:
    """Forward to ``gh api repos/<repo>/pulls/<N>/reviews``."""
    return _forward_pr_api_list(
        pr_id,
        path_suffix="reviews",
        json_fields=json_fields,
        jq_expr=jq_expr,
        repo_override=repo_override,
    )


def _forward_pr_api_list(
    pr_id: str,
    *,
    path_suffix: str,
    json_fields: list[str] | None,
    jq_expr: str | None,
    repo_override: str | None = None,
) -> int:
    if not _is_ascii_decimal(pr_id):
        sys.stderr.write(f"Error: PR_ID must be ASCII decimal, got: {pr_id}\n")
        return EXIT_INVALID_INPUT
    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=repo_override)
    if repo is None:
        sys.stderr.write(
            "Error: failed to detect current repository.\n"
            "Run 'gh repo view --json nameWithOwner' in a checked-out repo first.\n"
        )
        return EXIT_RUNTIME_ERROR
    cmd = ["gh", "api", f"repos/{repo}/pulls/{pr_id}/{path_suffix}"]
    effective_jq = _compose_json_and_jq(json_fields, jq_expr)
    if effective_jq is not None:
        cmd.extend(["--jq", effective_jq])
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        sys.stderr.write(f"Error: failed to invoke 'gh': {exc}\n")
        return EXIT_RUNTIME_ERROR


def _forward_pr_reply_to_comment(
    pr_id: str,
    *,
    comment_id: str,
    body: str,
    repo_override: str | None = None,
) -> int:
    """POST a reply to a PR review comment."""
    if not _is_ascii_decimal(pr_id):
        sys.stderr.write(f"Error: PR_ID must be ASCII decimal, got: {pr_id}\n")
        return EXIT_INVALID_INPUT
    if not _is_ascii_decimal(comment_id):
        sys.stderr.write(f"Error: --to COMMENT_ID must be ASCII decimal, got: {comment_id}\n")
        return EXIT_INVALID_INPUT
    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=repo_override)
    if repo is None:
        sys.stderr.write(
            "Error: failed to detect current repository.\n"
            "Run 'gh repo view --json nameWithOwner' in a checked-out repo first.\n"
        )
        return EXIT_RUNTIME_ERROR
    cmd = [
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{repo}/pulls/{pr_id}/comments/{comment_id}/replies",
        "-f",
        f"body={body}",
    ]
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        sys.stderr.write(f"Error: failed to invoke 'gh': {exc}\n")
        return EXIT_RUNTIME_ERROR


def _run_pr_review_poll(rest: list[str]) -> int:
    """Run the review-poll workflow helper through the installed kaji package."""
    p = argparse.ArgumentParser(prog="kaji pr review-poll", add_help=True)
    p.parse_args(rest)

    from ..scripts import review_poll_entry

    return review_poll_entry.main([])


def _dispatch_pr_builtin(sub: str, rest: list[str], *, repo_override: str | None = None) -> int:
    """Parse ``rest`` with a sub-specific argparse and dispatch to the handler.

    ``gh api`` 直叩き builtin（``review-comments`` / ``reviews`` /
    ``reply-to-comment``）専用。これらは ``repo_override``（config の
    ``[provider.github] repo`` 由来）を尊重し、別 repo 運用を許す。
    review-poll は workflow 専用 step で repo を取らないため、``_handle_pr``
    側の独立分岐で処理し本関数には流さない（``_PR_BUILTIN_SUBCOMMANDS``
    からも除外済み）。

    ``--help`` / ``-h`` prints sub-specific usage. argparse's default exit
    code on invalid args is 2, matching ``EXIT_INVALID_INPUT``.
    """
    p = argparse.ArgumentParser(prog=f"kaji pr {sub}", add_help=True)
    p.add_argument("pr_id", type=str, help="PR number")
    if sub in {"review-comments", "reviews"}:
        p.add_argument(
            "--json",
            dest="json_fields",
            default=None,
            help="Comma-separated field list (composed into gh api --jq projection)",
        )
        p.add_argument(
            "--jq",
            "-q",
            dest="jq_expr",
            default=None,
            help="jq expression applied after --json projection",
        )
    elif sub == "reply-to-comment":
        p.add_argument("--to", dest="comment_id", required=True, type=str, help="Review comment ID")
        p.add_argument("--body", required=True, type=str, help="Reply body")
    ns = p.parse_args(rest)
    raw_json = getattr(ns, "json_fields", None)
    fields: list[str] | None
    if raw_json is None:
        fields = None
    else:
        parts = [f.strip() for f in raw_json.split(",")]
        if not parts or any(not p_ for p_ in parts):
            sys.stderr.write(
                f"Error: --json must be a non-empty comma-separated list of "
                f"fields, got: {raw_json!r}\n"
            )
            return EXIT_INVALID_INPUT
        fields = parts
    if sub == "review-comments":
        return _forward_pr_review_comments(
            ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr, repo_override=repo_override
        )
    if sub == "reviews":
        return _forward_pr_reviews(
            ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr, repo_override=repo_override
        )
    return _forward_pr_reply_to_comment(
        ns.pr_id, comment_id=ns.comment_id, body=ns.body, repo_override=repo_override
    )


def _has_approve_flag(rest: list[str]) -> bool:
    """``rest`` 中に ``--approve`` / ``--approve=...`` / ``-a`` が含まれるかを pre-scan する。

    ``gh pr review`` の `-a` は ``--approve`` の正式 short alias（``gh pr review --help``）
    のため、long form と同じく self-PR fallback dispatcher へ振り分ける。
    ``--`` 以降は positional 扱いし無視する（``gh`` の慣習に合わせる）。
    """
    for tok in rest:
        if tok == "--":
            return False
        if tok == "--approve" or tok.startswith("--approve=") or tok == "-a":
            return True
    return False


def _has_request_changes_flag(rest: list[str]) -> bool:
    """``rest`` 中に ``--request-changes`` / ``--request-changes=...`` / ``-r`` が含まれるかを pre-scan する。

    ``gh pr review`` の ``-r`` は ``--request-changes`` の正式 short alias
    （``gh pr review --help``）。``--`` 以降は positional 扱いし無視する。
    ``_has_approve_flag`` と完全対称の構造。
    """
    for tok in rest:
        if tok == "--":
            return False
        if tok == "--request-changes" or tok.startswith("--request-changes=") or tok == "-r":
            return True
    return False


def _gh_capture_value(args: list[str]) -> str | None:
    """``gh <args>`` を ``capture_output=True`` で叩き、rc=0 なら stdout.strip() を返す。

    rc≠0 の場合は stderr を中継して ``None`` を返す（fail-loud。
    silent fallthrough を回避するため、呼出側で ``EXIT_RUNTIME_ERROR`` に
    昇格する責務を持つ）。
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"Error: failed to invoke 'gh': {exc}\n")
        return None
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip()


def _gh_capture_json(args: list[str]) -> Any | None:
    """``gh <args>`` を叩き、stdout を JSON として parse して返す（fail-closed）。

    ``_gh_capture_value`` に委譲し、rc≠0 は ``None``。stdout が JSON として
    parse 不能な場合も stderr に診断を出して ``None`` を返す。silent
    fallthrough を作らず、呼出側で DENY へ縮約する。

    Args:
        args: ``gh`` へ渡す引数列（先頭に ``gh`` は含めない）。

    Returns:
        parse 済み JSON 値。取得失敗 / parse 失敗時は ``None``。
    """
    raw = _gh_capture_value(args)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write("Error: failed to parse JSON from gh output.\n")
        return None


def _gh_post_issue_comment_silent(*, repo: str, pr_id: str, body: str) -> int:
    """``gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<body>``.

    ``gh api`` は POST response の JSON を stdout に書く既定挙動だが、
    本関数は ``capture_output=True`` で stdout を捨て、rc のみを返す
    （stdout contract は「空 + rc のみ」と定義する）。
    """
    cmd = [
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{repo}/issues/{pr_id}/comments",
        "-f",
        f"body={body}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        sys.stderr.write(f"Error: failed to invoke 'gh': {exc}\n")
        return EXIT_RUNTIME_ERROR
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


def _github_pr_review(rest: list[str], *, repo_override: str | None) -> int:
    """``kaji pr review <pr_id> --approve|--request-changes`` 専用 dispatcher（GitHub mode）。

    self-PR (PR author == authenticated user) では ``gh pr review --approve``
    / ``--request-changes`` が GitHub API ``Can not approve your own pull
    request`` / ``Can not request changes on your own pull request`` で 422
    拒否されるため、 ``<!-- kaji-review: state=APPROVED|CHANGES_REQUESTED -->``
    marker 付き comment を Issue comments API に投稿することで review シグナル
    を表現する。

    非 self-PR では従来通り ``gh pr review --approve|--request-changes`` を委譲する。
    """
    p = argparse.ArgumentParser(prog="kaji pr review", add_help=True)
    p.add_argument("pr_id", type=str, nargs="?", default=None)
    state_group = p.add_mutually_exclusive_group(required=True)
    state_group.add_argument("-a", "--approve", action="store_true")
    state_group.add_argument(
        "-r",
        "--request-changes",
        dest="request_changes",
        action="store_true",
    )
    p.add_argument("-b", "--body", default=None, type=str)
    p.add_argument("-F", "--body-file", dest="body_file", default=None, type=str)
    # `gh pr review` の inherited flag `-R/--repo` を吸収する。user 明示の
    # `--repo owner/name` は config 由来 `repo_override` より優先する。これを
    # unknown 扱いにすると self-PR fallback がスキップされ、`Can not approve
    # your own pull request` が再発するため（codex review 指摘）。
    p.add_argument("-R", "--repo", dest="repo", default=None, type=str)
    ns, unknown = p.parse_known_args(rest)

    # self-PR fallback は ASCII decimal の PR 番号 + 既知 flag のみで成立する。
    # URL/branch target、PR 省略（current branch 解決）、未認識 flag
    # の場合は従来契約を保つため `gh pr review` への passthrough にフォールバック
    # する（self-PR fallback はかけない）。
    if ns.pr_id is None or not _is_ascii_decimal(ns.pr_id) or unknown:
        return _forward_to_gh("pr", ["review", *rest], repo=repo_override)

    # user 明示 `--repo` を config override より優先する。
    effective_repo_override = ns.repo if ns.repo else repo_override
    try:
        body = _read_body_arg(ns.body, ns.body_file)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        # `--body-file` の不在 / 読取不能 (`FileNotFoundError` /
        # `PermissionError` 等) を未処理例外として露出させず、制御された
        # 診断 + `EXIT_INVALID_INPUT` に変換する。`--request-changes` を
        # `_github_pr_review` に routing したことで、従来 `gh pr review`
        # passthrough が返していた制御エラーが traceback に置き換わる回帰を
        # 防ぐ（Issue #199 review feedback）。
        sys.stderr.write(f"Error: cannot read --body-file {ns.body_file!r}: {exc}\n")
        return EXIT_INVALID_INPUT
    if body is None:
        body = ""

    # body 必須契約: --request-changes は self / 非 self 一貫で body 必須
    # （GitHub REST API event=REQUEST_CHANGES の body parameter requirement）。
    # subprocess 呼び出し前に fail-fast することで `gh api user` / `gh pr view`
    # を無駄に叩かない。--approve は GitHub API 側で body optional のため
    # 本 validation を適用せず、Issue #186 の既存契約「--approve + 空 body は
    # marker のみで rc=0」を維持する。
    if ns.request_changes and not body.strip():
        sys.stderr.write(
            "Error: --request-changes requires --body or --body-file with non-empty content.\n"
        )
        return EXIT_INVALID_INPUT

    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=effective_repo_override)
    if repo is None:
        sys.stderr.write(
            "Error: failed to detect current repository.\n"
            "Run 'gh repo view --json nameWithOwner' in a checked-out repo first.\n"
        )
        return EXIT_RUNTIME_ERROR

    pr_author = _gh_capture_value(
        ["pr", "view", ns.pr_id, "--repo", repo, "--json", "author", "--jq", ".author.login"]
    )
    if pr_author is None:
        return EXIT_RUNTIME_ERROR
    me = _gh_capture_value(["api", "user", "--jq", ".login"])
    if me is None:
        return EXIT_RUNTIME_ERROR
    is_self = pr_author == me

    state = "APPROVED" if ns.approve else "CHANGES_REQUESTED"
    marker = build_kaji_review_marker(state)
    marked_body = f"{marker}\n{body}"

    if is_self:
        return _gh_post_issue_comment_silent(repo=repo, pr_id=ns.pr_id, body=marked_body)

    flag = "--approve" if ns.approve else "--request-changes"
    gh_args = ["review", ns.pr_id, flag]
    if body:
        gh_args.extend(["--body", body])
    return _forward_to_gh("pr", gh_args, repo=repo)


# admin-merge-check が elevated merge を許す唯一の mergeStateStatus。CLEAN は
# 通常 merge 可能で admin 不要、DIRTY / BEHIND / DRAFT / HAS_HOOKS / UNKNOWN は
# conflict / 未知要因なので、いずれも DENY する（fail-closed。設計 § MF3）。
_ELIGIBLE_MERGE_STATE = "BLOCKED"

# statusCheckRollup を通過してよい check 状態（成功・中立のみ）。他 check の
# 失敗 / 保留を admin bypass しないため、これ以外を 1 つでも含めば DENY する。
_OK_CHECK_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# git object SHA の 40 桁 hex。曖昧な headRefOid を elevated merge に渡さない。
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# freshness 判定で「レビュー判定」とみなす marker state。COMMENTED は承認判断
# ではないため除外する。
_REVIEW_DECISION_STATES = {"APPROVED", "CHANGES_REQUESTED"}


def _admin_merge_deny(reason: str) -> int:
    """admin-merge-check の DENY 出力を統一する（stdout 空・stderr に理由・非 0）。"""
    sys.stderr.write(f"DENY: {reason}\n")
    return EXIT_RUNTIME_ERROR


# --------------------------------------------------------------------------
# GitHub payload の Pydantic model（AGENTS.md「外部入力は Pydantic で検証する」）。
#
# admin-merge-check は elevated merge の可否という安全判定を外部 GitHub JSON に
# 依存させるため、payload を手作業の dict 判定でなく Pydantic で検証する。日時は
# ``AwareDatetime`` を必須化し、timezone 欠落の naive 値（例 ``"2026-07-24"``）を
# ``ValidationError`` で弾く。これにより freshness 比較（aware vs aware）が
# ``TypeError: can't compare offset-naive and offset-aware datetimes`` を起こさず、
# 呼出側で ``_admin_merge_deny`` へ縮約される fail-closed 契約が保たれる（#368 MF1）。
# ``extra="ignore"`` で gh の追加フィールドは無視する。
# --------------------------------------------------------------------------


class _PrListItem(BaseModel):
    """``gh pr list --json number`` の 1 要素。"""

    model_config = ConfigDict(extra="ignore")

    number: int


class _PrAuthor(BaseModel):
    """PR author（``.author``）。"""

    model_config = ConfigDict(extra="ignore")

    login: str


class _PrCommit(BaseModel):
    """PR commit の 1 件（``.commits[]``）。``committedDate`` は aware 必須。"""

    model_config = ConfigDict(extra="ignore")

    committedDate: AwareDatetime  # noqa: N815 (gh JSON のフィールド名に一致)


class _PrStatusCheck(BaseModel):
    """``.statusCheckRollup[]`` の 1 件。CheckRun は ``conclusion``、StatusContext は ``state``。"""

    model_config = ConfigDict(extra="ignore")

    conclusion: str | None = None
    state: str | None = None


class _PrDetail(BaseModel):
    """``gh pr view --json author,mergeable,mergeStateStatus,headRefOid,commits,statusCheckRollup``。"""

    model_config = ConfigDict(extra="ignore")

    author: _PrAuthor
    mergeable: str
    mergeStateStatus: str  # noqa: N815
    headRefOid: str  # noqa: N815
    commits: list[_PrCommit] = Field(default_factory=list)
    statusCheckRollup: list[_PrStatusCheck] | None = None  # noqa: N815


class _PrIssueComment(BaseModel):
    """``repos/<repo>/issues/<pr>/comments`` の 1 件。``created_at`` は aware 必須。"""

    model_config = ConfigDict(extra="ignore")

    body: str = ""
    created_at: AwareDatetime


class _GhUser(BaseModel):
    """``gh api user``。認証ユーザーの ``login``。"""

    model_config = ConfigDict(extra="ignore")

    login: str


class _GhRepoPermissions(BaseModel):
    """``repos/<repo>`` の ``permissions``。``admin`` 不在は fail-closed で ``False``。"""

    model_config = ConfigDict(extra="ignore")

    admin: bool = False


class _GhRepo(BaseModel):
    """``gh api repos/<repo>``。認証ユーザーの当該 repo 権限を含む。"""

    model_config = ConfigDict(extra="ignore")

    permissions: _GhRepoPermissions = Field(default_factory=_GhRepoPermissions)


_PR_LIST_ADAPTER = TypeAdapter(list[_PrListItem])
_COMMENTS_ADAPTER = TypeAdapter(list[_PrIssueComment])


def _flatten_slurped_comments(payload: Any) -> list[Any] | None:
    """``gh api --paginate --slurp`` の array-of-pages を要素 list に平坦化する。

    ``--slurp`` は各ページを 1 つの外側配列に包む（``[[...], [...]]``）。ページを
    平坦化し、非 slurp の flat list（``[...]``）も許容する。ページが list / dict
    以外なら形状不正として ``None`` を返す（fail-closed）。要素の内容検証は呼出側の
    ``_COMMENTS_ADAPTER`` に委ね、ここでは構造の平坦化のみを行う。

    Args:
        payload: parse 済みの comments JSON。

    Returns:
        平坦化した要素の list。形状不正時は ``None``。
    """
    if not isinstance(payload, list):
        return None
    flat: list[Any] = []
    for page in payload:
        if isinstance(page, list):
            flat.extend(page)
        elif isinstance(page, dict):
            flat.append(page)
        else:
            return None
    return flat


def _check_is_ok(check: _PrStatusCheck) -> bool:
    """1 件の status check が bypass 不要な成功 / 中立状態かを返す（fail-closed）。

    CheckRun は ``conclusion``、StatusContext は ``state`` を持つ。conclusion が
    空（未完了 CheckRun）は保留とみなし ``False`` を返す。
    """
    if check.conclusion:
        return check.conclusion in _OK_CHECK_STATES
    if check.state:
        return check.state in _OK_CHECK_STATES
    return False


def _fresh_approved_marker(commits: list[_PrCommit], comments: list[_PrIssueComment]) -> bool:
    """現在 HEAD に対応する最新の kaji-review marker が ``APPROVED`` かを返す。

    最新 HEAD commit 時刻（``committedDate`` の max）より後に投稿された
    review decision marker のうち最新のものが ``APPROVED`` の場合だけ ``True``。
    最新 marker が ``CHANGES_REQUESTED`` / stale / 不在ならすべて ``False``。
    ``createdAt == HEAD committedDate`` の等号境界は stale（``latest_dt > head_dt``
    が厳密比較のため ``False``）とする。

    引数は Pydantic 検証済み model のため、``committedDate`` / ``created_at`` は
    いずれも aware ``datetime`` で、naive/aware 混在の ``TypeError`` は起こらない。

    Args:
        commits: 検証済み PR commit list。
        comments: 検証済み Issue comment list。

    Returns:
        fresh な ``APPROVED`` marker が存在すれば ``True``。
    """
    if not commits:
        return False
    head_dt = max(c.committedDate for c in commits)

    decisions: list[tuple[str, datetime]] = []
    for comment in comments:
        if not comment.body:
            continue
        lines = comment.body.splitlines()
        state = parse_kaji_review_marker(lines[0]) if lines else None
        if state not in _REVIEW_DECISION_STATES:
            continue
        decisions.append((state, comment.created_at))
    if not decisions:
        return False
    latest_state, latest_dt = max(decisions, key=lambda pair: pair[1])
    return latest_state == "APPROVED" and latest_dt > head_dt


def _pr_admin_merge_check(rest: list[str], *, repo_override: str | None) -> int:
    """self-PR の条件付き admin merge 適格性を read-only で判定する（fail-closed）。

    通常 merge が base branch policy で block された後の recovery 分岐で使う。
    次の 4 条件をすべて満たす場合だけ ALLOW（exit 0）とし、判定した HEAD SHA を
    stdout に 1 行だけ出力する。後続の ``kaji pr merge <branch> --admin
    --match-head-commit <SHA>`` がこの SHA で HEAD を拘束する。

    1. self-PR（PR author == authenticated user）
    2. 現在 HEAD に対応する最新 kaji-review marker が ``APPROVED``（stale でない）
    3. policy-block 適格（``mergeStateStatus==BLOCKED`` かつ ``mergeable==MERGEABLE``
       かつ他 status check 非失敗）
    4. authenticated user が repository admin（bypass 権限）を持つ

    いずれかを満たさない、または判定に必要な情報の取得 / parse に失敗した場合は
    DENY（exit 非 0、stdout 空、stderr に ``DENY: <理由>``）とする。GitHub への
    write は一切行わない。

    Args:
        rest: サブコマンド引数（先頭に feature branch 名を要求する）。
        repo_override: ``[provider.github] repo`` 由来の repo 明示指定。

    Returns:
        ``EXIT_OK`` (ALLOW) / ``EXIT_RUNTIME_ERROR`` (DENY) / ``EXIT_INVALID_INPUT``。
    """
    p = argparse.ArgumentParser(prog="kaji pr admin-merge-check", add_help=True)
    p.add_argument("branch", type=str, help="Feature branch name (PR head)")
    ns = p.parse_args(rest)
    branch = ns.branch

    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=repo_override)
    if repo is None:
        sys.stderr.write(
            "Error: failed to detect current repository.\n"
            "Run 'gh repo view --json nameWithOwner' in a checked-out repo first.\n"
        )
        return EXIT_RUNTIME_ERROR

    # 1. branch → open PR を一意解決（0 件 / 複数件 / 曖昧はすべて DENY）
    pr_list_raw = _gh_capture_json(
        ["pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "number"]
    )
    if pr_list_raw is None:
        return _admin_merge_deny("failed to list open PRs for branch")
    try:
        pr_list = _PR_LIST_ADAPTER.validate_python(pr_list_raw)
    except ValidationError as exc:
        return _admin_merge_deny(f"unexpected open-PR list payload: {exc.error_count()} error(s)")
    if len(pr_list) != 1:
        return _admin_merge_deny(
            f"expected exactly 1 open PR for head {branch!r}, got {len(pr_list)}"
        )
    pr_id = str(pr_list[0].number)

    # 2. PR 詳細（HEAD SHA / author / merge 適格性 / check / commit 時刻）
    data_raw = _gh_capture_json(
        [
            "pr",
            "view",
            pr_id,
            "--repo",
            repo,
            "--json",
            "author,mergeable,mergeStateStatus,headRefOid,commits,statusCheckRollup",
        ]
    )
    if data_raw is None:
        return _admin_merge_deny("failed to fetch PR details")
    try:
        data = _PrDetail.model_validate(data_raw)
    except ValidationError as exc:
        return _admin_merge_deny(f"unexpected PR detail payload: {exc.error_count()} error(s)")

    head_sha = data.headRefOid
    if _SHA_RE.fullmatch(head_sha) is None:
        return _admin_merge_deny("headRefOid missing/invalid")

    # 3. 全 comment を pagination 取得（marker 見落とし防止）
    comments_payload = _gh_capture_json(
        ["api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr_id}/comments"]
    )
    if comments_payload is None:
        return _admin_merge_deny("failed to fetch PR comments")
    flat_comments = _flatten_slurped_comments(comments_payload)
    if flat_comments is None:
        return _admin_merge_deny("unexpected comments payload shape")
    try:
        comments = _COMMENTS_ADAPTER.validate_python(flat_comments)
    except ValidationError as exc:
        return _admin_merge_deny(f"unexpected comment payload: {exc.error_count()} error(s)")

    me_raw = _gh_capture_json(["api", "user"])
    if me_raw is None:
        return _admin_merge_deny("failed to resolve authenticated user")
    try:
        me = _GhUser.model_validate(me_raw).login
    except ValidationError as exc:
        return _admin_merge_deny(f"unexpected user payload: {exc.error_count()} error(s)")
    repo_raw = _gh_capture_json(["api", f"repos/{repo}"])
    if repo_raw is None:
        return _admin_merge_deny("failed to resolve repository admin permission")
    try:
        repo_model = _GhRepo.model_validate(repo_raw)
    except ValidationError as exc:
        return _admin_merge_deny(f"unexpected repository payload: {exc.error_count()} error(s)")

    # 条件 A: self-PR
    author_login = data.author.login
    if author_login != me:
        return _admin_merge_deny(f"not a self-PR (author={author_login!r}, me={me!r})")

    # 条件 B: policy-block 適格性（MF3）
    if data.mergeStateStatus != _ELIGIBLE_MERGE_STATE:
        return _admin_merge_deny(
            f"mergeStateStatus not {_ELIGIBLE_MERGE_STATE}: {data.mergeStateStatus!r}"
        )
    if data.mergeable != "MERGEABLE":
        return _admin_merge_deny(f"not MERGEABLE: {data.mergeable!r}")
    rollup = data.statusCheckRollup or []
    if any(not _check_is_ok(c) for c in rollup):
        return _admin_merge_deny("failing/pending status checks present (not bypassed)")

    # 条件 C: 現在 HEAD に対応する最新 marker が APPROVED
    if not data.commits:
        return _admin_merge_deny("PR has no commits")
    if not _fresh_approved_marker(data.commits, comments):
        return _admin_merge_deny("no fresh APPROVED marker for current HEAD")

    # 条件 D: repository admin（bypass 権限）。取得失敗も含め admin でなければ DENY
    if not repo_model.permissions.admin:
        return _admin_merge_deny("authenticated user lacks repo admin bypass")

    sys.stdout.write(f"{head_sha}\n")
    return EXIT_OK


def _handle_pr(raw_args: list[str]) -> int:
    """Two-stage dispatch for ``kaji pr``.

    builtin sub (``review-comments`` / ``reviews`` / ``reply-to-comment``) →
    dedicated handler; otherwise fall back to ``gh pr`` passthrough.

    Phase 4: ``provider.type='local'`` 配下では bare-provider エラーで
    fail-fast する。``_PR_BUILTIN_SUBCOMMANDS`` （``gh api`` 直叩き）も
    同じガードで止める。GitHub mode の挙動は Phase 3-e と bit-exact に
    維持する。

    Note: ``kaji pr --help`` / ``-h`` は本関数に到達せず、argparse 上位の
    ``unrecognized arguments`` エラーで先に止まる（``_register_pr`` が
    ``add_help=False`` + ``REMAINDER`` で登録されている既存挙動）。
    bare provider 配下でも GitHub mode でも同じ。設計書 § 1 設計判断
    「`kaji pr --help` を bare で見せない」要件は本挙動で満たされる。
    """
    try:
        config = _load_config_for_dispatch()
    except (ConfigLoadError, ConfigNotFoundError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        provider = get_provider(config)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    if isinstance(provider, LocalProvider):
        sys.stderr.write(_PR_BARE_PROVIDER_ERROR)
        return EXIT_INVALID_INPUT

    repo_override: str | None = None
    if config.provider is not None and config.provider.type == "github":
        if not config.provider.github.repo:
            sys.stderr.write(
                "Error: provider.type='github' requires provider.github.repo (e.g. 'owner/name').\n"
            )
            return EXIT_INVALID_INPUT
        repo_override = config.provider.github.repo
    del provider  # PR routing は config 経由で済む

    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]
    if (
        args
        and args[0] == "review"
        and (_has_approve_flag(args[1:]) or _has_request_changes_flag(args[1:]))
    ):
        return _github_pr_review(args[1:], repo_override=repo_override)
    # review-poll は workflow 専用 step（#234 で exec step 化）。repo は
    # review_poll_entry が KAJI_GIT_REMOTE→`git remote get-url` から一意に
    # 解決するため、CLI `-R` / config `repo_override` のいずれも受理しない。
    # 他 builtin と違い repo_override を渡さないのはこのため（gh-api 直叩き
    # builtin ではないので _PR_BUILTIN_SUBCOMMANDS からも除外している）。
    # bare-provider ガードと repo 必須検証は上で通過済みなので挙動は不変。
    if args and args[0] == "review-poll":
        return _run_pr_review_poll(args[1:])
    # admin-merge-check は read-only の条件付き admin merge 適格性判定。
    # config 由来 repo_override を尊重する（issue-close の close recovery で使用）。
    if args and args[0] == "admin-merge-check":
        return _pr_admin_merge_check(args[1:], repo_override=repo_override)
    if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
        return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
    return _forward_to_gh("pr", raw_args, repo=repo_override)
