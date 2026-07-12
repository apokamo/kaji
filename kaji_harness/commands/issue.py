"""``kaji issue`` dispatch + GitHub verdict + local issue CRUD（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
from pathlib import Path

from ..errors import ConfigLoadError, ConfigNotFoundError
from ..providers import IssueProvider, ResolvedId, get_provider, normalize_id
from ..providers.github import GitHubProviderError
from ..providers.local import (
    IssueNotFoundError,
    IssueReadOnlyError,
    LocalProvider,
    LocalProviderError,
)
from ..providers.markers import build_kaji_verdict_marker
from ..state import _format_issue_ref
from .config import _load_config_for_dispatch
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR
from .output import _emit_json, _issue_to_json_dict, _read_body_arg
from .pr import _forward_to_gh


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
        marker = _resolve_verdict_marker(ns.verdict_step, ns.verdict_status)
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


def _resolve_verdict_marker(step: str | None, status: str | None) -> str | None:
    """``--verdict-step`` / ``--verdict-status`` から marker 行を解決する。

    両フラグは同時必須（片方のみは契約違反）。両方 ``None`` の従来呼び出しは
    ``None`` を返し、呼出側は body を一切変更しない。

    Returns:
        marker 文字列（1 行目に前置する）、または verdict フラグ未指定なら ``None``。

    Raises:
        ValueError: 片方のみ指定 / 不正な step・status 語彙（fail-loud）。
            呼出側で ``EXIT_INVALID_INPUT`` にマップする。
    """
    if step is None and status is None:
        return None
    if step is None or status is None:
        raise ValueError("--verdict-step and --verdict-status must be specified together")
    return build_kaji_verdict_marker(step, status)


def _has_verdict_flags(args: list[str]) -> bool:
    """``args`` に verdict marker フラグ（``--verdict-step`` / ``--verdict-status``）が含まれるか。

    github passthrough を構造化経路へ切替えるかの判定に使う。``--flag=value``
    形式も検出する。
    """
    return any(
        a in ("--verdict-step", "--verdict-status")
        or a.startswith("--verdict-step=")
        or a.startswith("--verdict-status=")
        for a in args
    )


def build_worktree_note_body(current_body: str, *, worktree: str, branch: str) -> str:
    """``> [!NOTE]`` メタブロックを ``current_body`` 先頭へ決定的に合成する。

    ``/issue-start`` Step 4 はかつて multi-line bash heredoc でメタブロックと既存
    本文を結合していたため、エージェントの multi-line 忠実度に依存し、Haiku 等で
    blockquote と本文 heading の境界 blank line が脱落していた（Issue #200 OB）。
    本関数は blank line を Python 文字列リテラル ``\\n\\n`` に固定し、モデル非依存に
    レイアウトを保証する。

    Args:
        current_body: 現在の Issue 本文。
        worktree: NOTE に載せる worktree 相対パスの basename（例 ``kaji-fix-200``）。
        branch: ブランチ名（例 ``fix/200``）。

    Returns:
        ``> [!NOTE]`` ブロック + 空行ちょうど 1 行 + 正規化済み本文。``current_body``
        が空（改行のみを含む）の場合は NOTE ブロックのみ（末尾改行 1 つ）。
    """
    note = f"> [!NOTE]\n> **Worktree**: `../{worktree}`\n> **Branch**: `{branch}`"
    # 本文先頭の余分な空行を剥がし、必ず空行 1 行だけを分離子として付与する。
    body = current_body.lstrip("\n")
    if not body:
        return note + "\n"
    return f"{note}\n\n{body}"


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
        issue_dir = provider._resolve_issue_dir(issue_id_value)
        _commit_local_issue_change(
            provider=provider,
            rid=local_rid,
            action="edit",
            paths=[issue_dir / "issue.md"],
        )
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
    from ..sync import SyncError

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


def _commit_local_issue_change(
    *,
    provider: LocalProvider,
    rid: ResolvedId,
    action: str,
    paths: list[Path],
) -> None:
    """Commit only the given ``paths`` atomically, leaving other staged changes untouched.

    Two-step flow:
      1. ``git add <paths>`` — register untracked targets (new comment markdown)
         and update the index entry for tracked targets (modified ``issue.md``).
         This only touches the listed paths; other entries already staged in the
         user's index are not modified.
      2. ``git commit --only -- <paths>`` — build a temporary index from HEAD
         plus the listed paths and commit it. Pre-existing staged changes for
         paths *not* listed are excluded from HEAD and remain staged in the
         user's index after the commit (per ``man git-commit`` § ``--only``).

    Together these guarantee the atomicity requirement: the resulting commit
    contains only ``paths`` even when the user had unrelated files staged.
    """
    rel_paths = [str(p.relative_to(provider.repo_root)) for p in paths]
    issue_ref = _format_issue_ref(rid.value)
    msg = f"chore(local): {action} for {issue_ref}"
    subprocess.run(
        ["git", "add", "--", *rel_paths],
        cwd=provider.repo_root,
        check=True,
    )
    # `LocalProvider.edit_issue` は同一 body 再送でも `issue.md` を再書込するため、
    # `kaji issue edit --commit` が no-op edit で呼ばれた場合は staged diff が空に
    # なる。`git commit --only` をそのまま呼ぶと `nothing to commit` で exit 1 に
    # 落ちるため、対象 path の staged diff を確認して空なら commit を skip する。
    # `git diff --cached --quiet` の exit code: 0=差分なし / 1=差分あり / >1=エラー。
    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *rel_paths],
        cwd=provider.repo_root,
    )
    if diff_check.returncode == 0:
        return
    if diff_check.returncode != 1:
        diff_check.check_returncode()
    subprocess.run(
        ["git", "commit", "--only", "-m", msg, "--", *rel_paths],
        cwd=provider.repo_root,
        check=True,
    )


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
    issue = provider.edit_issue(
        rid.value,
        title=ns.title,
        body=body,
        add_labels=ns.add_label,
        remove_labels=ns.remove_label,
    )
    if ns.commit:
        issue_dir = provider._resolve_issue_dir(issue.id)
        _commit_local_issue_change(
            provider=provider,
            rid=rid,
            action="edit",
            paths=[issue_dir / "issue.md"],
        )
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
    marker = _resolve_verdict_marker(ns.verdict_step, ns.verdict_status)
    if marker is not None:
        body = f"{marker}\n{body}"
    comment = provider.comment_issue(rid.value, body)
    sys.stdout.write(f"{comment.seq}-{comment.machine_id}\n")
    if ns.commit:
        issue_dir = provider._resolve_issue_dir(rid.value)
        comment_path = issue_dir / "comments" / f"{comment.seq}-{comment.machine_id}.md"
        _commit_local_issue_change(
            provider=provider,
            rid=rid,
            action="comment",
            paths=[comment_path],
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
