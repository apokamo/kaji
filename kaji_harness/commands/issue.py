"""``kaji issue`` dispatch + GitHub verdict + local issue CRUD（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    VerdictMarkerMalformedError,
    VerdictMarkerMetaMissingError,
    VerdictMarkerNotFoundError,
)
from ..providers import Comment, IssueProvider, ResolvedId, get_provider, normalize_id
from ..providers.context import build_worktree_note_body
from ..providers.github import GitHubProviderError
from ..providers.local import (
    IssueNotFoundError,
    IssueReadOnlyError,
    LocalProvider,
    LocalProviderError,
)
from ..providers.markers import (
    KajiVerdictMarker,
    build_kaji_verdict_marker,
    parse_kaji_verdict_marker,
    resolve_verdict_marker,
)
from .config import _load_config_for_dispatch
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR
from .output import _emit_json, _issue_to_json_dict, _read_body_arg
from .pr import _forward_to_gh

EXIT_VERDICT_NOT_FOUND = 4
EXIT_VERDICT_MALFORMED = 5
EXIT_VERDICT_META_MISSING = 6


@dataclass(frozen=True)
class ResolvedVerdictMarker:
    """Latest valid verdict marker plus its provider timestamp.

    Attributes:
        step: Producing workflow step.
        status: Verdict status.
        meta: Validated marker metadata.
        created_at: Provider-neutral ISO 8601 comment timestamp.
    """

    step: str
    status: str
    meta: dict[str, str]
    created_at: str


def resolve_latest_verdict(
    comments: list[Comment],
    *,
    step: str,
    required_meta: tuple[str, ...] = (),
) -> ResolvedVerdictMarker:
    """Resolve the latest marker for one step and validate required metadata.

    ``Comment`` lists are provider-normalized in posting order, so reverse scan
    deterministically makes every later verdict supersede earlier verdicts.

    Args:
        comments: Provider-neutral Issue comments in posting order.
        step: Producing step to select.
        required_meta: Metadata keys that must exist on the latest marker.

    Returns:
        The latest parsed marker and its comment timestamp.

    Raises:
        VerdictMarkerNotFoundError: No marker exists for the requested step.
        VerdictMarkerMalformedError: The latest matching marker is malformed.
        VerdictMarkerMetaMissingError: A required metadata key is absent.
    """
    build_kaji_verdict_marker(step, "PASS")  # fail-loud step grammar validation
    marker_prefix = f"<!-- kaji-verdict: step={step} "
    for comment in reversed(comments):
        first_line = comment.body.splitlines()[0] if comment.body.splitlines() else ""
        if not first_line.startswith(marker_prefix):
            continue
        marker = parse_kaji_verdict_marker(first_line)
        if marker is None or marker.step != step:
            raise VerdictMarkerMalformedError(
                f"latest verdict marker for step {step!r} is malformed"
            )
        missing = [key for key in required_meta if key not in marker.meta]
        if missing:
            raise VerdictMarkerMetaMissingError(
                f"latest verdict marker for step {step!r} is missing metadata: {', '.join(missing)}"
            )
        return _resolved_marker(marker, comment.created_at)
    raise VerdictMarkerNotFoundError(f"no verdict marker found for step {step!r}")


def _resolved_marker(marker: KajiVerdictMarker, created_at: str) -> ResolvedVerdictMarker:
    """Combine a parsed marker with its comment timestamp."""
    return ResolvedVerdictMarker(
        step=marker.step,
        status=marker.status,
        meta=marker.meta,
        created_at=created_at,
    )


def _handle_issue(raw_args: list[str]) -> int:
    """``kaji issue`` の dispatcher。

    Phase 3-c:

    - ``provider.type == "local"`` → ``LocalProvider`` 経由の structured CRUD
    - ``provider.type == "github"`` → ``gh issue`` passthrough。ただし
      ``[provider.github] repo`` を ``--repo`` で強制注入する（review #3 反映）
    - ``[provider]`` 未設定 → WARN + Phase 1 互換 passthrough（``--repo`` 無し）

    fail-fast 経路（review #3 反映）:

    - 壊れた config → exit 2
    - ``provider`` 設定値の不整合（``machine_id`` 不在 / ``repo`` 不在等） → exit 2
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

    # ``context`` subcommand は provider 共通で provider.resolve_issue_context()
    # を呼ぶ helper（issue local-p1-17）。``gh issue context`` は存在しない
    # ため、GitHub passthrough 前に捕捉する。
    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] == "context":
        return _handle_issue_context(provider, args[1:])
    # ``prepend-note`` も ``gh issue`` に存在しない provider 共通 helper のため、
    # GitHub passthrough 前に捕捉する（Issue #200）。
    if args and args[0] == "prepend-note":
        return _handle_issue_prepend_note(provider, args[1:])
    if args and args[0] == "resolve-verdict":
        return _handle_issue_resolve_verdict(provider, args[1:])

    if isinstance(provider, LocalProvider):
        return _handle_issue_local(provider, raw_args)
    # verdict marker 付与要求（`--verdict-step` / `--verdict-status`）は gh へ
    # passthrough せず構造化経路へ振り分ける。gh issue comment は verdict
    # フラグを知らないため passthrough は unknown flag で fail するし、marker
    # 合成を CLI 層で決定的に行う必要がある（ADR 008 決定 3）。
    if args and args[0] == "comment" and _has_verdict_flags(args):
        return _github_issue_comment_with_verdict(provider, args[1:])
    # GitHubProvider 経路: 設定 repo を --repo で強制注入し cwd 推論を防ぐ。
    # `--commit` は LocalProvider 専用フラグ（.kaji/issues/<id>/ への永続化と
    # commit を atomic 化する用途）。skill は provider 型を意識せず付与できる
    # ように設計したので、github mode では silent に剥がして gh に forward する
    # （gh CLI に誤って渡ると unknown flag で fail する）。
    forwarded = [a for a in raw_args if a != "--commit"]
    assert config.provider is not None  # for type checker
    return _forward_to_gh("issue", forwarded, repo=config.provider.github.repo)


def _github_issue_comment_with_verdict(provider: object, rest: list[str]) -> int:
    """``kaji issue comment <id> ... --verdict-step S --verdict-status ST`` (github)。

    verdict marker を要求する comment は gh passthrough せず、marker を 1 行目
    に前置した body を ``GitHubProvider.comment_issue`` で投稿する（repo は
    provider が ``--repo`` で注入する）。``--commit`` は local 専用のため github
    では silent に無視する（passthrough 経路と同じ扱い）。

    片方のみのフラグ / 不正語彙 / body 不在は ``EXIT_INVALID_INPUT``（fail-loud）。
    """
    from ..providers.github import GitHubProvider

    assert isinstance(provider, GitHubProvider)  # github 経路のみ到達
    p = argparse.ArgumentParser(prog="kaji issue comment", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
    # `--commit` は github では no-op（local 専用）だが、skill が provider 型を
    # 意識せず付与できるよう受理して無視する。
    p.add_argument("--commit", action="store_true")
    p.add_argument("--verdict-step", dest="verdict_step", default=None, type=str)
    p.add_argument("--verdict-status", dest="verdict_status", default=None, type=str)
    p.add_argument("--verdict-meta", dest="verdict_meta", action="append", default=[])
    ns = p.parse_args(rest)
    try:
        body = _read_body_arg(ns.body, ns.body_file)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        sys.stderr.write(f"Error: cannot read --body-file {ns.body_file!r}: {exc}\n")
        return EXIT_INVALID_INPUT
    if body is None:
        sys.stderr.write("Error: 'kaji issue comment' requires --body or --body-file\n")
        return EXIT_INVALID_INPUT
    try:
        marker = resolve_verdict_marker(ns.verdict_step, ns.verdict_status, ns.verdict_meta)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    # 本経路は `_has_verdict_flags` 検出後にのみ到達するため marker は非 None。
    assert marker is not None
    marked_body = f"{marker}\n{body}"
    try:
        provider.comment_issue(ns.issue_id, marked_body)
    except GitHubProviderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


# ---------- LocalProvider dispatch ----------


def _resolve_local_id(provider: LocalProvider, raw: str, *, write: bool) -> ResolvedId | int:
    """``normalize_id`` 経由で input id を `ResolvedId` に解決する。

    Phase 3-c の契約（review #1 反映）:

    - ``"153"``       → ``local-<machine_id>-153``
    - ``"pc1-3"``     → ``local-pc1-3``
    - ``"local-..."`` → そのまま
    - ``"gh:N"``      → remote_cache（read-only。write 系で受理 → exit 2）

    解決失敗 / write 拒否は ``EXIT_INVALID_INPUT`` を返す。
    """
    try:
        rid = normalize_id(raw, provider_name="local", machine_id=provider.machine_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    if rid.kind == "remote_cache" and write:
        sys.stderr.write(
            f"Error: cannot modify {raw!r} under provider.type='local'. "
            f"Cached GitHub issues (gh:N) are read-only.\n"
        )
        return EXIT_INVALID_INPUT
    return rid


def _has_verdict_flags(args: list[str]) -> bool:
    """``args`` に verdict marker フラグ（``--verdict-step`` / ``--verdict-status``）が含まれるか。

    github passthrough を構造化経路へ切替えるかの判定に使う。``--flag=value``
    形式も検出する。
    """
    return any(
        a in ("--verdict-step", "--verdict-status", "--verdict-meta")
        or a.startswith("--verdict-step=")
        or a.startswith("--verdict-status=")
        or a.startswith("--verdict-meta=")
        for a in args
    )


def _handle_issue_resolve_verdict(provider: IssueProvider, rest: list[str]) -> int:
    """Resolve the latest structured verdict marker for one Issue step.

    Args:
        provider: Active provider implementation.
        rest: Arguments after ``kaji issue resolve-verdict``.

    Returns:
        Zero on success; distinct non-zero codes for not found, malformed, and
        required-metadata-missing outcomes.
    """
    parser = argparse.ArgumentParser(prog="kaji issue resolve-verdict", add_help=True)
    parser.add_argument("issue_id", type=str)
    parser.add_argument("--step", required=True, type=str)
    parser.add_argument("--require-meta", action="append", default=[])
    namespace = parser.parse_args(rest)

    if isinstance(provider, LocalProvider):
        resolved_id = _resolve_local_id(provider, namespace.issue_id, write=False)
        if isinstance(resolved_id, int):
            return resolved_id
        issue_id = resolved_id.value
    else:
        try:
            issue_id = normalize_id(
                namespace.issue_id,
                provider_name="github",
                machine_id=None,
            ).value
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return EXIT_INVALID_INPUT

    try:
        comments = provider.list_issue_comments_all(issue_id)
        resolved = resolve_latest_verdict(
            comments,
            step=namespace.step,
            required_meta=tuple(namespace.require_meta),
        )
    except VerdictMarkerNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_VERDICT_NOT_FOUND
    except VerdictMarkerMalformedError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_VERDICT_MALFORMED
    except VerdictMarkerMetaMissingError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_VERDICT_META_MISSING
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except (GitHubProviderError, IssueNotFoundError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    return _emit_json(dataclasses.asdict(resolved), jq_expr=None)


def _handle_issue_prepend_note(provider: IssueProvider, rest: list[str]) -> int:
    """``kaji issue prepend-note <id> --worktree W --branch B [--commit]``。

    provider 共通: ``view_issue`` で現在本文を取得し、``build_worktree_note_body``
    で NOTE ブロック + blank line + 本文を決定的に合成して ``edit_issue`` で更新する。
    エージェントが multi-line 本文を組み立てる必要を排し、blank line 脱落
    （Issue #200）をモデル非依存に防ぐ。

    ``gh issue prepend-note`` は存在しないため、``context`` と同様に ``_handle_issue``
    の provider 分岐より前で捕捉される。``--commit`` は local provider で ``issue.md``
    を atomic commit する用途。github では silent に無視する（既存 ``edit`` と同契約）。
    """
    p = argparse.ArgumentParser(prog="kaji issue prepend-note", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--worktree", required=True, type=str)
    p.add_argument("--branch", required=True, type=str)
    p.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Commit the resulting .kaji/issues/<id>/issue.md atomically "
            "(local provider only; silently ignored for github)."
        ),
    )
    ns = p.parse_args(rest)

    # provider 別 ID 正規化（_handle_issue_context と同じ規則）
    local_rid: ResolvedId | None = None
    if isinstance(provider, LocalProvider):
        rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=True)
        if isinstance(rid_or_rc, int):
            return rid_or_rc
        local_rid = rid_or_rc
        issue_id_value = local_rid.value
    else:
        try:
            rid = normalize_id(ns.issue_id, provider_name="github", machine_id=None)
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return EXIT_INVALID_INPUT
        issue_id_value = rid.value

    try:
        current = provider.view_issue(issue_id_value)
        new_body = build_worktree_note_body(current.body, worktree=ns.worktree, branch=ns.branch)
        provider.edit_issue(issue_id_value, body=new_body)
    except IssueNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except GitHubProviderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except (LocalProviderError, ValueError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    # local provider の --commit のみ issue.md を atomic commit。github は silent 無視。
    if ns.commit and isinstance(provider, LocalProvider) and local_rid is not None:
        provider.commit_issue_change(local_rid, "edit", [Path("issue.md")])
    return EXIT_OK


def _handle_issue_context(provider: IssueProvider, rest: list[str]) -> int:
    """``kaji issue context <id>`` の実装（local / github 共通）。

    薄いラッパー: ``provider.resolve_issue_context()`` の戻り値を JSON
    シリアライズして stdout に書く。``--json FIELDS`` でキー絞り込み、
    ``-q EXPR`` で jq 式適用。未知 ``--json`` キーは ``null`` を返す
    （``_local_issue_view`` の ``full.get(k)`` 挙動に揃える）。

    issue local-p1-17 で導入。skill (`/issue-start`) が context 正本と
    同期するために参照する。
    """
    p = argparse.ArgumentParser(prog="kaji issue context", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--json", dest="json_fields", default=None, type=str)
    p.add_argument("--jq", "-q", dest="jq_expr", default=None, type=str)
    ns = p.parse_args(rest)

    # provider 別 ID 正規化（_resolve_local_id を local 経路で再利用）
    if isinstance(provider, LocalProvider):
        rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=False)
        if isinstance(rid_or_rc, int):
            return rid_or_rc
        issue_id_value = rid_or_rc.value
    else:
        # GitHub: 数値 / ``gh:N`` を受理し github の数値 ID に正規化
        try:
            rid = normalize_id(ns.issue_id, provider_name="github", machine_id=None)
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return EXIT_INVALID_INPUT
        issue_id_value = rid.value

    try:
        ctx = provider.resolve_issue_context(issue_id_value)
    except IssueNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except GitHubProviderError as exc:
        # GitHub 経路の CLI 不在 / 非 0 終了 / 不正 JSON 等を
        # user-facing なエラー出力 + EXIT_RUNTIME_ERROR に正規化する。
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except (LocalProviderError, ValueError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    payload: dict[str, object] = dataclasses.asdict(ctx)
    if ns.json_fields:
        fields = [f.strip() for f in ns.json_fields.split(",") if f.strip()]
        if fields:
            payload = {k: payload.get(k) for k in fields}

    return _emit_json(payload, jq_expr=ns.jq_expr)


_LOCAL_ISSUE_SUBS = {"view", "create", "edit", "comment", "close", "list", "context"}


def _handle_issue_local(provider: LocalProvider, raw_args: list[str]) -> int:
    """``kaji issue`` の LocalProvider 経由 CRUD dispatcher。

    対応 sub: ``view`` / ``create`` / ``edit`` / ``comment`` / ``close`` /
    ``list``。Skill が現在使用中のフラグはすべて受理する（review #2 反映）:

    - ``--json FIELDS`` / ``--jq EXPR`` / ``-q EXPR``
    - ``--comments``（plain view）
    - ``--body`` / ``--body-file PATH`` (``-`` で stdin)
    """
    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        sys.stderr.write(
            "Error: 'kaji issue' requires a subcommand under provider.type='local'. "
            f"Supported: {', '.join(sorted(_LOCAL_ISSUE_SUBS))}.\n"
        )
        return EXIT_INVALID_INPUT
    sub, rest = args[0], args[1:]
    if sub not in _LOCAL_ISSUE_SUBS:
        sys.stderr.write(
            f"Error: 'kaji issue {sub}' is not supported under provider.type='local' "
            f"(Phase 3-c). Supported: {', '.join(sorted(_LOCAL_ISSUE_SUBS))}.\n"
        )
        return EXIT_INVALID_INPUT
    from ..errors import SyncError

    try:
        if sub == "view":
            return _local_issue_view(provider, rest)
        if sub == "create":
            return _local_issue_create(provider, rest)
        if sub == "edit":
            return _local_issue_edit(provider, rest)
        if sub == "comment":
            return _local_issue_comment(provider, rest)
        if sub == "close":
            return _local_issue_close(provider, rest)
        if sub == "context":
            # 通常 top-level `_handle_issue` が context を先回り捕捉するが、
            # `_handle_issue_local` が直接呼ばれた場合の保険として委譲する。
            return _handle_issue_context(provider, rest)
        # sub == "list"
        return _local_issue_list(provider, rest)
    except IssueReadOnlyError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except IssueNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except SyncError as exc:
        # Issue #191: list_issues / view_cached_issue が legacy forge cache を
        # 検出した場合の fail-fast 経路。sync コマンド系と同じ contract
        # (EXIT_INVALID_INPUT) に揃える。
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except (LocalProviderError, ValueError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        sys.stderr.write(f"Error: I/O failure: {exc}\n")
        return EXIT_RUNTIME_ERROR


def _local_issue_view(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue view", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--json", dest="json_fields", default=None, type=str)
    p.add_argument("--jq", "-q", dest="jq_expr", default=None, type=str)
    p.add_argument("--comments", action="store_true")
    ns = p.parse_args(rest)

    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=False)
    if isinstance(rid_or_rc, int):
        return rid_or_rc
    rid = rid_or_rc

    if rid.kind == "remote_cache":
        issue = provider.view_cached_issue(rid.value)
    else:
        issue = provider.view_issue(rid.value)

    json_mode = ns.json_fields is not None or ns.jq_expr is not None
    if json_mode:
        full = _issue_to_json_dict(issue)
        if ns.json_fields:
            fields = [f.strip() for f in ns.json_fields.split(",") if f.strip()]
            payload: object = {k: full.get(k) for k in fields} if fields else full
        else:
            payload = full
        return _emit_json(payload, jq_expr=ns.jq_expr)

    sys.stdout.write(f"# {issue.title}\n\n{issue.body}\n")
    if ns.comments and issue.comments:
        for c in issue.comments:
            header = f"[{c.author or 'unknown'} @ {c.created_at or 'n/a'}]"
            sys.stdout.write(f"\n---\n{header}\n{c.body}\n")
    return EXIT_OK


def _local_issue_create(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue create", add_help=True)
    p.add_argument("--title", required=True, type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
    p.add_argument("--label", action="append", default=[], type=str)
    p.add_argument(
        "--slug",
        default=None,
        type=str,
        help="kebab-case slug (optional; derived from title when omitted)",
    )
    ns = p.parse_args(rest)
    body = _read_body_arg(ns.body, ns.body_file)
    if body is None:
        raise ValueError("'kaji issue create' requires --body or --body-file")
    issue = provider.create_issue(title=ns.title, body=body, labels=ns.label, slug=ns.slug)
    sys.stdout.write(f"{issue.id}\n")
    return EXIT_OK


def _local_issue_edit(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue edit", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--title", default=None, type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
    p.add_argument("--add-label", dest="add_label", action="append", default=[], type=str)
    p.add_argument("--remove-label", dest="remove_label", action="append", default=[], type=str)
    p.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Commit the resulting .kaji/issues/<id>/issue.md atomically after "
            "persistence (uses `git commit --only` so other staged changes are "
            "not included in the new commit)."
        ),
    )
    ns = p.parse_args(rest)
    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=True)
    if isinstance(rid_or_rc, int):
        return rid_or_rc
    rid = rid_or_rc
    body = _read_body_arg(ns.body, ns.body_file)
    provider.edit_issue(
        rid.value,
        title=ns.title,
        body=body,
        add_labels=ns.add_label,
        remove_labels=ns.remove_label,
    )
    if ns.commit:
        provider.commit_issue_change(rid, "edit", [Path("issue.md")])
    return EXIT_OK


def _local_issue_comment(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue comment", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
    p.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Commit the resulting .kaji/issues/<id>/comments/<ts>-<machine>.md "
            "atomically after persistence (uses `git commit --only` so other "
            "staged changes are not included in the new commit)."
        ),
    )
    p.add_argument("--verdict-step", dest="verdict_step", default=None, type=str)
    p.add_argument("--verdict-status", dest="verdict_status", default=None, type=str)
    p.add_argument("--verdict-meta", dest="verdict_meta", action="append", default=[])
    ns = p.parse_args(rest)
    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=True)
    if isinstance(rid_or_rc, int):
        return rid_or_rc
    rid = rid_or_rc
    body = _read_body_arg(ns.body, ns.body_file)
    if body is None:
        raise ValueError("'kaji issue comment' requires --body or --body-file")
    # verdict marker（ADR 008 決定 3: cross-skill 契約を CLI 層に置く）。
    # 片方のみ / 不正語彙は ValueError → _handle_issue_local が exit 2 にマップ。
    marker = resolve_verdict_marker(ns.verdict_step, ns.verdict_status, ns.verdict_meta)
    if marker is not None:
        body = f"{marker}\n{body}"
    comment = provider.comment_issue(rid.value, body)
    sys.stdout.write(f"{comment.seq}-{comment.machine_id}\n")
    if ns.commit:
        provider.commit_issue_change(
            rid,
            "comment",
            [Path("comments") / f"{comment.seq}-{comment.machine_id}.md"],
        )
    return EXIT_OK


def _local_issue_close(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue close", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--reason", default=None, type=str)
    ns = p.parse_args(rest)
    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=True)
    if isinstance(rid_or_rc, int):
        return rid_or_rc
    rid = rid_or_rc
    provider.close_issue(rid.value, reason=ns.reason)
    return EXIT_OK


def _local_issue_list(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue list", add_help=True)
    p.add_argument("--state", default="open", type=str, choices=["open", "closed", "all"])
    p.add_argument("--label", action="append", default=[], type=str)
    p.add_argument("--limit", default=None, type=int)
    p.add_argument("--json", dest="json_fields", default=None, type=str)
    p.add_argument("--jq", "-q", dest="jq_expr", default=None, type=str)
    ns = p.parse_args(rest)
    issues = provider.list_issues(state=ns.state, labels=ns.label or None, limit=ns.limit)
    json_mode = ns.json_fields is not None or ns.jq_expr is not None
    if json_mode:
        items: list[dict[str, object]] = [
            _issue_to_json_dict(i, include_comments=False) for i in issues
        ]
        if ns.json_fields:
            fields = [f.strip() for f in ns.json_fields.split(",") if f.strip()]
            if fields:
                items = [{k: it.get(k) for k in fields} for it in items]
        return _emit_json(items, jq_expr=ns.jq_expr)
    for issue in issues:
        sys.stdout.write(f"{issue.id}\t{issue.state}\t{issue.title}\n")
    return EXIT_OK
