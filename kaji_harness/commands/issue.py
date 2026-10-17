"""``kaji issue`` dispatch + GitHub verdict（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..artifact_verdict import (
    RunContext,
    parse_run_id,
    resolve_artifact_verdict,
    run_context_from_env,
    run_context_from_verdict_path,
)
from ..artifacts import resolve_artifacts_dir
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    VerdictArtifactNotFoundError,
    VerdictArtifactUnusableError,
    VerdictMarkerMalformedError,
    VerdictMarkerMetaMissingError,
    VerdictMarkerNotFoundError,
)
from ..providers import Comment, IssueProvider, get_provider, normalize_id
from ..providers.context import build_worktree_note_body
from ..providers.github import GitHubProviderError
from ..providers.markers import (
    KajiVerdictMarker,
    build_kaji_verdict_marker,
    parse_kaji_verdict_marker,
    resolve_verdict_marker,
)
from .config import _load_config_for_dispatch
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR
from .output import _emit_json, _read_body_arg
from .pr import _forward_to_gh

EXIT_VERDICT_NOT_FOUND = 4
EXIT_VERDICT_MALFORMED = 5
EXIT_VERDICT_META_MISSING = 6
EXIT_VERDICT_ARTIFACT_UNUSABLE = 7


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

    - ``provider.type == "github"`` → ``gh issue`` passthrough。ただし
      ``[provider.github] repo`` を ``--repo`` で強制注入する（review #3 反映）
    - ``[provider]`` 未設定 → WARN + Phase 1 互換 passthrough（``--repo`` 無し）

    fail-fast 経路（review #3 反映）:

    - 壊れた config → exit 2
    - ``provider`` 設定値の不整合（``repo`` 不在等） → exit 2
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
    # を呼ぶ helper。``gh issue context`` は存在しない
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
        return _handle_issue_resolve_verdict(
            provider,
            args[1:],
            artifacts_dir_resolver=lambda: resolve_artifacts_dir(config),
        )

    # verdict marker 付与要求（`--verdict-step` / `--verdict-status`）は gh へ
    # passthrough せず構造化経路へ振り分ける。gh issue comment は verdict
    # フラグを知らないため passthrough は unknown flag で fail するし、marker
    # 合成を CLI 層で決定的に行う必要がある（ADR 008 決定 3）。
    if args and args[0] == "comment" and _has_verdict_flags(args):
        return _github_issue_comment_with_verdict(provider, args[1:])
    # GitHubProvider 経路: 設定 repo を --repo で強制注入し cwd 推論を防ぐ。
    # `--commit` は no-op（既存 skill 呼び出しとの互換のため受理）。gh CLI に
    # 誤って渡ると unknown flag で fail するため、剥がしてから gh に forward する。
    forwarded = [a for a in raw_args if a != "--commit"]
    assert config.provider is not None  # for type checker
    return _forward_to_gh("issue", forwarded, repo=config.provider.github.repo)


def _github_issue_comment_with_verdict(provider: object, rest: list[str]) -> int:
    """``kaji issue comment <id> ... --verdict-step S --verdict-status ST`` (github)。

    verdict marker を要求する comment は gh passthrough せず、marker を 1 行目
    に前置した body を ``GitHubProvider.comment_issue`` で投稿する（repo は
    provider が ``--repo`` で注入する）。``--commit`` は no-op（既存 skill 呼び出し
    との互換のため受理）として無視する（passthrough 経路と同じ扱い）。

    片方のみのフラグ / 不正語彙 / body 不在は ``EXIT_INVALID_INPUT``（fail-loud）。
    """
    from ..providers.github import GitHubProvider

    assert isinstance(provider, GitHubProvider)  # github 経路のみ到達
    p = argparse.ArgumentParser(prog="kaji issue comment", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
    # `--commit` は no-op（既存 skill 呼び出しとの互換のため受理）。
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


def _handle_issue_resolve_verdict(
    provider: IssueProvider,
    rest: list[str],
    *,
    artifacts_dir_resolver: Callable[[], Path] | None = None,
) -> int:
    """Resolve the latest structured verdict for one Issue step.

    Issue comment markers always take priority. Only when no marker exists for the
    step and a run context is available (``--run``, ``--current-verdict-path`` or the
    ``KAJI_VERDICT_PATH`` environment variable) is the local ``verdict.yaml`` artifact
    of that run consulted.

    Args:
        provider: Active provider implementation.
        rest: Arguments after ``kaji issue resolve-verdict``.
        artifacts_dir_resolver: Lazily resolves the artifacts directory for ``--run``
            (avoids running git unless the artifact fallback needs it).

    Returns:
        Zero on success; distinct non-zero codes for not found, malformed,
        required-metadata-missing, and unusable-artifact outcomes.
    """
    parser = argparse.ArgumentParser(prog="kaji issue resolve-verdict", add_help=True)
    parser.add_argument("issue_id", type=str)
    parser.add_argument("--step", required=True, type=str)
    parser.add_argument("--require-meta", action="append", default=[])
    run_group = parser.add_mutually_exclusive_group()
    run_group.add_argument("--run", dest="run_id", default=None, type=str)
    run_group.add_argument("--current-verdict-path", dest="current_verdict_path", default=None)
    namespace = parser.parse_args(rest)

    try:
        issue_id = normalize_id(namespace.issue_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        explicit_context = _explicit_run_context(namespace, issue_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        comments = provider.list_issue_comments_all(issue_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except GitHubProviderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR

    try:
        resolved = resolve_latest_verdict(
            comments,
            step=namespace.step,
            required_meta=tuple(namespace.require_meta),
        )
    except VerdictMarkerNotFoundError as exc:
        context = explicit_context or run_context_from_env(os.environ, issue_id)
        if context is None:
            sys.stderr.write(f"Error: {exc}\n")
            return EXIT_VERDICT_NOT_FOUND
        return _resolve_from_artifact(
            context,
            issue_id=issue_id,
            step=namespace.step,
            require_meta=bool(namespace.require_meta),
            artifacts_dir_resolver=artifacts_dir_resolver,
        )
    except VerdictMarkerMalformedError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_VERDICT_MALFORMED
    except VerdictMarkerMetaMissingError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_VERDICT_META_MISSING
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    return _emit_json(dataclasses.asdict(resolved), jq_expr=None)


def _explicit_run_context(namespace: argparse.Namespace, issue_id: str) -> RunContext | None:
    """Build the run context from ``--run`` / ``--current-verdict-path`` (fail-fast).

    Raises:
        ValueError: The option value is malformed or belongs to another Issue.
    """
    if namespace.run_id is not None:
        return RunContext(run_id=parse_run_id(namespace.run_id))
    if namespace.current_verdict_path is not None:
        return run_context_from_verdict_path(namespace.current_verdict_path, issue_id=issue_id)
    return None


def _resolve_from_artifact(
    context: RunContext,
    *,
    issue_id: str,
    step: str,
    require_meta: bool,
    artifacts_dir_resolver: Callable[[], Path] | None,
) -> int:
    """Resolve a verdict from ``verdict.yaml`` artifacts after a marker miss."""
    run_dir = context.run_dir
    if run_dir is None:
        if artifacts_dir_resolver is None:
            sys.stderr.write("Error: --run requires a resolvable artifacts directory\n")
            return EXIT_INVALID_INPUT
        run_dir = artifacts_dir_resolver() / issue_id / "runs" / context.run_id
    try:
        artifact = resolve_artifact_verdict(run_dir, step=step)
    except VerdictArtifactNotFoundError as exc:
        sys.stderr.write(f"Error: no verdict marker found for step {step!r}; {exc}\n")
        return EXIT_VERDICT_NOT_FOUND
    except VerdictArtifactUnusableError as exc:
        sys.stderr.write(f"Error: artifact verdict for step {step!r} is unusable: {exc}\n")
        return EXIT_VERDICT_ARTIFACT_UNUSABLE
    if require_meta:
        # Artifacts carry no marker metadata; never guess it.
        sys.stderr.write(
            f"Error: artifact verdict for step {step!r} cannot satisfy --require-meta\n"
        )
        return EXIT_VERDICT_META_MISSING
    return _emit_json(artifact.as_json(), jq_expr=None)


def _handle_issue_prepend_note(provider: IssueProvider, rest: list[str]) -> int:
    """``kaji issue prepend-note <id> --worktree W --branch B [--commit]``。

    provider 共通: ``view_issue`` で現在本文を取得し、``build_worktree_note_body``
    で NOTE ブロック + blank line + 本文を決定的に合成して ``edit_issue`` で更新する。
    エージェントが multi-line 本文を組み立てる必要を排し、blank line 脱落
    （Issue #200）をモデル非依存に防ぐ。

    ``gh issue prepend-note`` は存在しないため、``context`` と同様に ``_handle_issue``
    の provider 分岐より前で捕捉される。``--commit`` は no-op（既存 skill 呼び出し
    との互換のため受理）として無視する（既存 ``edit`` と同契約）。
    """
    p = argparse.ArgumentParser(prog="kaji issue prepend-note", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--worktree", required=True, type=str)
    p.add_argument("--branch", required=True, type=str)
    p.add_argument(
        "--commit",
        action="store_true",
        help="No-op; accepted for compatibility with existing skill invocations.",
    )
    ns = p.parse_args(rest)

    try:
        issue_id_value = normalize_id(ns.issue_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        current = provider.view_issue(issue_id_value)
        new_body = build_worktree_note_body(current.body, worktree=ns.worktree, branch=ns.branch)
        provider.edit_issue(issue_id_value, body=new_body)
    except GitHubProviderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    return EXIT_OK


def _handle_issue_context(provider: IssueProvider, rest: list[str]) -> int:
    """``kaji issue context <id>`` の実装（provider 共通）。

    薄いラッパー: ``provider.resolve_issue_context()`` の戻り値を JSON
    シリアライズして stdout に書く。``--json FIELDS`` でキー絞り込み、
    ``-q EXPR`` で jq 式適用。未知 ``--json`` キーは ``null`` を返す
    （``payload.get(k)``）。

    skill (`/issue-start`) が context 正本と
    同期するために参照する。
    """
    p = argparse.ArgumentParser(prog="kaji issue context", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--json", dest="json_fields", default=None, type=str)
    p.add_argument("--jq", "-q", dest="jq_expr", default=None, type=str)
    ns = p.parse_args(rest)

    # 数値 / ``gh:N`` を受理し github の数値 ID に正規化
    try:
        issue_id_value = normalize_id(ns.issue_id)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        ctx = provider.resolve_issue_context(issue_id_value)
    except GitHubProviderError as exc:
        # GitHub 経路の CLI 不在 / 非 0 終了 / 不正 JSON 等を
        # user-facing なエラー出力 + EXIT_RUNTIME_ERROR に正規化する。
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    payload: dict[str, object] = dataclasses.asdict(ctx)
    if ns.json_fields:
        fields = [f.strip() for f in ns.json_fields.split(",") if f.strip()]
        if fields:
            payload = {k: payload.get(k) for k in fields}

    return _emit_json(payload, jq_expr=ns.jq_expr)
