"""CLI entrypoint for kaji_harness.

Provides the `kaji` command with subcommands (e.g., `kaji run`).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .artifacts import resolve_artifacts_dir
from .config import KajiConfig
from .errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    SecurityError,
    SkillNotFound,
    WorkflowValidationError,
)
from .models import Workflow
from .providers import (
    IssueProvider,
    ResolvedId,
    actual_provider_type,
    get_provider,
    normalize_id,
    provider_overlay_divergence_warning,
)
from .providers.github import GitHubProviderError, build_kaji_review_marker
from .providers.local import (
    IssueNotFoundError,
    IssueReadOnlyError,
    LocalProvider,
    LocalProviderError,
)
from .runner import WorkflowRunner
from .skill import validate_skill_exists
from .state import _format_issue_ref
from .workflow import load_workflow, validate_workflow

EXIT_OK = 0
EXIT_ABORT = 1
EXIT_VALIDATION_ERROR = 1
EXIT_DEFINITION_ERROR = 2
EXIT_CONFIG_NOT_FOUND = 2
EXIT_INVALID_INPUT = 2
EXIT_RUNTIME_ERROR = 3


def _get_version() -> str:
    """Return the installed package version, or 'unknown' if not found."""
    try:
        return version("kaji")
    except PackageNotFoundError:
        return "unknown"


def create_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="kaji",
        description="AI-driven development workflow orchestrator",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _register_run(subparsers)
    _register_validate(subparsers)
    _register_issue(subparsers)
    _register_pr(subparsers)
    _register_config(subparsers)
    _register_sync(subparsers)
    from .local_init import register_subcommand as _register_local

    _register_local(subparsers)
    return parser


def _register_sync(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``kaji sync`` 系の subcommand 登録。

    ``from-github``: GitHub repo から open Issue を全件 fetch して
    ``.kaji/cache/gh-<number>.json`` に atomic write する。
    ``status``: 最終 sync 時刻 / cache 件数 / 経過時間を表示する。

    ``--include-closed`` / ``--state`` / ``--since`` は将来予約 flag。
    本 release では受理せず exit 2 で fail-fast する（completion criterion）。
    """
    p = subparsers.add_parser("sync", help="Cache synchronization commands")
    sync_subs = p.add_subparsers(dest="sync_command", required=True)

    fh = sync_subs.add_parser(
        "from-github",
        help="Sync open issues from a GitHub repo into local cache",
    )
    fh.add_argument(
        "--repo",
        default=None,
        type=str,
        help="GitHub repo (owner/name). Defaults to [provider.github].repo.",
    )
    fh.add_argument("--quiet", action="store_true", help="Suppress progress logs.")
    fh.add_argument(
        "--include-closed",
        dest="include_closed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    fh.add_argument("--state", dest="state", default=None, type=str, help=argparse.SUPPRESS)
    fh.add_argument("--since", dest="since", default=None, type=str, help=argparse.SUPPRESS)

    st = sync_subs.add_parser("status", help="Show local cache sync status")
    st.add_argument("--json", dest="json_mode", action="store_true", help="Output JSON.")


def _register_config(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``config`` subcommand group.

    Phase 4 で ``kaji config provider-type`` を read-only で公開する。
    Skill / 自動化スクリプトが overlay (``.kaji/config.local.toml``) を
    考慮した正しい provider type を取得するための入口。
    """
    p = subparsers.add_parser("config", help="Read-only config inspection commands")
    config_subs = p.add_subparsers(dest="config_command", required=True)
    pt = config_subs.add_parser(
        "provider-type",
        help="Print resolved provider.type ('github' or 'local')",
    )
    pt.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Starting directory for config discovery (default: current directory)",
    )


def _register_run(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `run` subcommand."""
    p = subparsers.add_parser("run", help="Run a workflow")
    p.add_argument("workflow", type=Path, help="Path to workflow YAML file")
    p.add_argument(
        "issue",
        type=str,
        help="Issue ID (GitHub number like '153' or local form like 'local-pc1-1')",
    )
    p.add_argument("--from", dest="from_step", help="Resume from a specific step")
    p.add_argument("--step", dest="single_step", help="Run a single step only")
    p.add_argument(
        "--before",
        dest="before_step",
        help="Stop just before dispatching <step> (exclusive barrier).",
    )
    p.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Starting directory for config discovery (default: current directory)",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress agent output streaming")


def _register_issue(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `issue` subcommand.

    Phase 3-e 以降は ``provider.type`` に応じて分岐する。
    ``provider.type='local'`` → LocalProvider 経由の structured CRUD、
    ``provider.type='github'`` → ``gh issue`` passthrough（``--repo`` 自動注入）。
    """
    p = subparsers.add_parser(
        "issue",
        help="Issue operations (provider-aware: github passthrough or local CRUD)",
        add_help=False,
    )
    p.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to 'gh issue'")


def _register_pr(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `pr` subcommand.

    Phase 3-e: すべての引数を `gh pr` に転送する（`provider.type='github'` 時に
    ``--repo`` を自動注入）。`pr merge` は method flag
    (``--merge`` / ``--squash`` / ``--rebase``) を露出せず、内部で常に
    ``--merge`` (= ``--no-ff`` 相当) 固定で gh に渡す
    (`docs/guides/git-commit-flow.md` の merge 規約に従う)。
    Phase 4 で `provider.type='local'` 配下では bare-provider エラー化予定。
    """
    p = subparsers.add_parser(
        "pr",
        help="Pull request operations (Phase 1: gh pr passthrough)",
        add_help=False,
    )
    p.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to 'gh pr'")


def _register_validate(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the `validate` subcommand."""
    p = subparsers.add_parser("validate", help="Validate workflow YAML files")
    p.add_argument("files", nargs="+", type=Path, help="Workflow YAML file(s) to validate")
    p.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root for skill lookup (default: auto-detect from config or pyproject.toml)",
    )


def _resolve_project_root_for_validate(explicit_root: Path | None, yaml_path: Path) -> Path:
    """Resolve project root for validate command.

    Priority:
    1. Explicit --project-root if provided
    2. .kaji/config.toml discovery from YAML file's directory
    3. Walk up from YAML file's directory looking for pyproject.toml
    4. Fall back to YAML file's parent directory
    """
    if explicit_root is not None:
        return explicit_root.resolve()
    # Try .kaji/config.toml
    try:
        config = KajiConfig.discover(start_dir=yaml_path.resolve().parent)
        return config.repo_root
    except ConfigNotFoundError:
        pass
    except ConfigLoadError:
        raise
    # Fallback: pyproject.toml
    current = yaml_path.resolve().parent
    while True:
        if (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return yaml_path.resolve().parent


def cmd_validate(args: argparse.Namespace) -> int:
    """Execute the `validate` subcommand."""
    failed = 0
    total = len(args.files)

    for path in args.files:
        if not path.exists():
            _print_error(path, ["File not found"])
            failed += 1
            continue
        try:
            wf = load_workflow(path)
            validate_workflow(wf)
            project_root = _resolve_project_root_for_validate(args.project_root, path)
            config = KajiConfig.discover(start_dir=project_root)
            skill_dir = config.paths.skill_dir
            for step in wf.steps:
                validate_skill_exists(step.skill, project_root, skill_dir)
            _print_success(path)
        except WorkflowValidationError as e:
            _print_error(path, e.errors)
            failed += 1
        except (SkillNotFound, SecurityError) as e:
            _print_error(path, [str(e)])
            failed += 1
        except (ConfigNotFoundError, ConfigLoadError) as e:
            _print_error(path, [str(e)])
            failed += 1
        except OSError as e:
            _print_error(path, [str(e)])
            failed += 1

    if failed > 0 and total > 1:
        print(
            f"Validation failed: {failed} of {total} files had errors.",
            file=sys.stderr,
        )

    return EXIT_VALIDATION_ERROR if failed > 0 else EXIT_OK


def _print_success(path: Path) -> None:
    """Print success message to stdout."""
    print(f"✓ {path}")


def _print_error(path: Path, errors: list[str]) -> None:
    """Print error messages to stderr."""
    print(f"✗ {path}", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the `run` subcommand."""
    # Mutual exclusion: --from and --step
    if args.from_step and args.single_step:
        print(
            "Error: --from and --step are mutually exclusive",
            file=sys.stderr,
        )
        return EXIT_DEFINITION_ERROR

    # Mutual exclusion: --step and --before
    if args.single_step and args.before_step:
        print(
            "Error: --step and --before are mutually exclusive",
            file=sys.stderr,
        )
        return EXIT_DEFINITION_ERROR

    # Config discovery: --workdir overrides the start directory
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        print(
            f"Error: --workdir '{args.workdir}' is not a valid directory",
            file=sys.stderr,
        )
        return EXIT_DEFINITION_ERROR

    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except ConfigNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_CONFIG_NOT_FOUND
    except ConfigLoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_CONFIG_NOT_FOUND

    # Phase 3-e § 1.5: provider config を runner 起動前に validate し、
    # `[provider]` 不在を `IssueContextResolutionError` 経由 exit 3 に落とさず
    # exit 2 で正規化する。`kaji issue` / `kaji pr` と契約を統一。
    try:
        get_provider(config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    _emit_provider_overlay_divergence_warning(config)

    project_root = config.repo_root

    # Load and validate workflow
    workflow_path = args.workflow
    if not workflow_path.exists():
        print(
            f"Error: Workflow file not found: {workflow_path}",
            file=sys.stderr,
        )
        return EXIT_DEFINITION_ERROR

    try:
        workflow = load_workflow(workflow_path)
    except WorkflowValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR

    # Phase 4: workflow ↔ provider 整合検証。``requires_provider != "any"`` の
    # 場合のみ ``config.provider.type`` と突合し、不整合を ``EXIT_INVALID_INPUT``
    # で fail-fast する。
    rc = _validate_workflow_provider_match(workflow, config)
    if rc != EXIT_OK:
        return rc

    # Run workflow
    try:
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=args.issue,
            project_root=project_root,
            artifacts_dir=resolve_artifacts_dir(config),
            config=config,
            from_step=args.from_step,
            single_step=args.single_step,
            before_step=args.before_step,
            verbose=not args.quiet,
        )
        state = runner.run()
    except (WorkflowValidationError, SkillNotFound, SecurityError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR
    except HarnessError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return EXIT_ABORT

    # Check for ABORT verdict
    if state.last_transition_verdict and state.last_transition_verdict.status == "ABORT":
        print(
            f"Workflow aborted: {state.last_transition_verdict.reason}",
            file=sys.stderr,
        )
        return EXIT_ABORT

    # Success summary: canonical_issue_ref を優先（Phase 3-d preflight § 1）。
    # ``[provider]`` 未設定 fallback などで未確定の場合のみ raw 入力で整形する。
    issue_ref = runner.canonical_issue_ref or _format_issue_ref(args.issue)
    print(f"Workflow '{workflow.name}' completed for issue {issue_ref}")
    return EXIT_OK


def _validate_workflow_provider_match(workflow: Workflow, config: KajiConfig) -> int:
    """``workflow.requires_provider`` と ``config.provider.type`` の突合検証。

    Phase 4 で導入。``requires_provider`` が ``"any"`` 以外で
    ``config.provider.type`` と一致しない場合、``EXIT_INVALID_INPUT`` を返し、
    切替手順を stderr に出力する。

    本 helper は ``get_provider(config)`` が成功した直後に呼ぶことが前提
    （``actual_provider_type(config)`` の narrowing 契約に従う）。
    """
    if workflow.requires_provider == "any":
        return EXIT_OK
    actual = actual_provider_type(config)
    if workflow.requires_provider == actual:
        return EXIT_OK
    print(
        f"Error: workflow '{workflow.name}' requires provider.type="
        f"'{workflow.requires_provider}' but current config has "
        f"provider.type='{actual}'.\n"
        f"  - To run this workflow, switch provider in .kaji/config.local.toml.\n"
        f"  - To use the current provider, choose a workflow with "
        f"requires_provider='{actual}' or 'any'.",
        file=sys.stderr,
    )
    return EXIT_INVALID_INPUT


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
    "'reply-to-comment' (Phase 2).\n"
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


def _compose_json_and_jq(fields: list[str] | None, jq: str | None) -> str | None:
    """Compose ``--json FIELDS`` and ``--jq EXPR`` into a single ``gh api --jq`` expression.

    `gh api` does not accept ``--json`` (only ``--jq``), so kaji turns
    ``--json`` into a jq projection and chains it before the user expression.

    - fields only          -> ``[.[] | {f1: .f1, f2: .f2}]``
    - jq only              -> ``<jq>``
    - both                 -> ``[.[] | {f1: .f1, ...}] | <jq>``
    - neither              -> None (do not pass ``--jq`` to gh)
    """
    if fields is None and jq is None:
        return None
    field_proj = "[.[] | {" + ", ".join(f"{f}: .{f}" for f in fields) + "}]" if fields else None
    if field_proj and jq:
        return f"{field_proj} | {jq}"
    return field_proj or jq


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


def _dispatch_pr_builtin(sub: str, rest: list[str], *, repo_override: str | None = None) -> int:
    """Parse ``rest`` with a sub-specific argparse and dispatch to the handler.

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
    if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
        return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
    return _forward_to_gh("pr", raw_args, repo=repo_override)


def _emit_provider_overlay_divergence_warning(config: KajiConfig) -> None:
    """provider overlay の worktree 間ズレを検出したら stderr に WARN を出す。

    overlay が無い feature worktree から provider 解決が tracked 値へ沈黙で
    フォールバックし、かつ main worktree の overlay と食い違う場合のみ発火する。
    exit code・標準出力には影響しない。
    """
    warning = provider_overlay_divergence_warning(config)
    if warning is not None:
        sys.stderr.write(warning + "\n")


def _load_config_for_dispatch() -> KajiConfig:
    """Config を読み込む（``kaji issue`` / ``kaji pr`` dispatch 用）。

    Phase 3-e: ``ConfigNotFoundError`` も propagate する（fail-fast 化）。
    Phase 3-c までの「config 不在 → legacy gh passthrough」は廃止。
    呼出側 dispatcher で ``ConfigNotFoundError`` / ``ConfigLoadError`` を
    catch して exit 2 を返す契約。
    """
    config = KajiConfig.discover(start_dir=Path.cwd())
    _emit_provider_overlay_divergence_warning(config)
    return config


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

    if isinstance(provider, LocalProvider):
        return _handle_issue_local(provider, raw_args)
    # GitHubProvider 経路: 設定 repo を --repo で強制注入し cwd 推論を防ぐ。
    # `--commit` は LocalProvider 専用フラグ（.kaji/issues/<id>/ への永続化と
    # commit を atomic 化する用途）。skill は provider 型を意識せず付与できる
    # ように設計したので、github mode では silent に剥がして gh に forward する
    # （gh CLI に誤って渡ると unknown flag で fail する）。
    forwarded = [a for a in raw_args if a != "--commit"]
    assert config.provider is not None  # for type checker
    return _forward_to_gh("issue", forwarded, repo=config.provider.github.repo)


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


def _read_body_arg(body: str | None, body_file: str | None) -> str | None:
    """``--body`` / ``--body-file`` を解決する。両方指定 / 不在の扱いは呼出側。

    ``body_file == "-"`` で stdin、それ以外はファイル読み込み。
    """
    if body is not None and body_file is not None:
        raise ValueError("--body and --body-file are mutually exclusive")
    if body is not None:
        return body
    if body_file is None:
        return None
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).read_text(encoding="utf-8")


def _apply_jq(json_text: str, expr: str) -> tuple[str, int]:
    """Python ``jq`` package で ``json_text`` に式を適用する（``gh --jq`` 互換 raw 出力）。

    Phase 3-d preflight: system ``jq`` バイナリ依存を撤去し、PyPI ``jq``
    package を runtime dependency に格上げした（design.md / phase3d-preflight
    § 2）。

    `gh --jq` および `jq -r` と互換な raw 出力ルール:

    - string         → 改行を含めてそのまま出力 + 末尾 newline 1
    - number / bool  → decimal / ``true`` / ``false`` + newline
    - null           → 空行（newline のみ）
    - object / array → compact JSON + newline
    - stream         → 各結果を上記ルールで整形し連結
    - empty stream   → 出力なし、exit 0
    - syntax/runtime → exit 3、stderr に jq 例外メッセージを user-facing 整形

    Skill 群は ``CURRENT_BODY=$(kaji issue view N --json body -q '.body')``
    のように shell 変数代入で raw 値を期待しているため、string は quote 無しで
    出さなければならない。
    """
    import json as _json

    try:
        data = _json.loads(json_text)
    except _json.JSONDecodeError as exc:
        sys.stderr.write(f"Error: invalid JSON passed to jq: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    try:
        import jq as _jq  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — runtime dependency 化後は不到達
        sys.stderr.write(
            "Error: Python 'jq' package is required but not installed. "
            f"Reinstall kaji ('uv sync' / 'pip install kaji'). Detail: {exc}\n"
        )
        return "", EXIT_RUNTIME_ERROR

    try:
        program = _jq.compile(expr)
    except ValueError as exc:
        sys.stderr.write(f"Error: jq compile failed: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    try:
        results = program.input_value(data).all()
    except ValueError as exc:
        sys.stderr.write(f"Error: jq runtime error: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    return _format_jq_results(results), EXIT_OK


def _format_jq_results(results: list[object]) -> str:
    """``jq.compile(...).all()`` の結果配列を ``jq -r`` 互換 raw 出力に整形する。

    各 result を 1 行として扱い末尾 newline を付ける。string は raw、null は
    空行、object/array は compact JSON にする(design.md § jq 互換 / phase3d
    preflight § 2 出力契約)。
    """
    import json as _json

    parts: list[str] = []
    for value in results:
        if value is None:
            parts.append("")
        elif isinstance(value, str):
            parts.append(value)
        elif isinstance(value, bool):
            parts.append("true" if value else "false")
        elif isinstance(value, (int, float)):
            parts.append(_json.dumps(value))
        else:
            parts.append(_json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _issue_to_json_dict(issue: object, *, include_comments: bool = True) -> dict[str, object]:
    """``Issue`` → gh ``issue view --json ...`` 互換の dict に整形。"""
    from .providers.models import Issue as _Issue  # local import to avoid cycle

    assert isinstance(issue, _Issue)
    out: dict[str, object] = {
        "number": issue.id,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state,
        "labels": [
            {"name": label.name, "description": label.description, "color": label.color}
            for label in issue.labels
        ],
    }
    if include_comments:
        out["comments"] = [
            {"author": c.author, "body": c.body, "createdAt": c.created_at} for c in issue.comments
        ]
    return out


def _emit_json(payload: object, *, jq_expr: str | None) -> int:
    """JSON を ``--jq`` 経由で整形して stdout に書く。"""
    import json as _json

    text = _json.dumps(payload, ensure_ascii=False)
    if jq_expr is None:
        sys.stdout.write(text + "\n")
        return EXIT_OK
    out, rc = _apply_jq(text, jq_expr)
    if rc != EXIT_OK:
        return rc
    # jq は末尾 newline を出すため二重出力を避けて write
    sys.stdout.write(out)
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
    import dataclasses

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
    from .sync import SyncError

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
    ns = p.parse_args(rest)
    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=True)
    if isinstance(rid_or_rc, int):
        return rid_or_rc
    rid = rid_or_rc
    body = _read_body_arg(ns.body, ns.body_file)
    if body is None:
        raise ValueError("'kaji issue comment' requires --body or --body-file")
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


def cmd_config_provider_type(args: argparse.Namespace) -> int:
    """Print resolved ``provider.type`` ("github" / "local") to stdout.

    Phase 4 で導入。Skill / 自動化スクリプトが overlay 込みの provider type を
    副作用なく取得するための read-only エントリ。``KajiConfig.discover()``
    と ``get_provider()`` の検証を経由するため、`_handle_pr` / `_handle_issue`
    / `cmd_run` と同じ config resolution path を共有する。

    Exit codes:
        0: 解決成功（stdout に ``"github\\n"`` / ``"local\\n"``）
        2: config 不在 or 不正（stderr に診断メッセージ）
    """
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        print(
            f"Error: --workdir '{args.workdir}' is not a valid directory",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        get_provider(config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    _emit_provider_overlay_divergence_warning(config)
    sys.stdout.write(f"{actual_provider_type(config)}\n")
    return EXIT_OK


def cmd_sync_from_github(args: argparse.Namespace) -> int:
    """``kaji sync from-github`` の dispatcher。

    将来予約 flag（``--include-closed`` / ``--state`` / ``--since``）は exit 2 で
    fail-fast する。
    """
    from .sync import SyncError, sync_from_github

    if args.include_closed:
        sys.stderr.write(
            "error: --include-closed is not implemented in this release; "
            "reopen tracking issue to add it.\n"
        )
        return EXIT_INVALID_INPUT
    if args.state is not None:
        sys.stderr.write(
            "error: --state is not implemented in this release; "
            "this command always fetches state=open.\n"
        )
        return EXIT_INVALID_INPUT
    if args.since is not None:
        sys.stderr.write(
            "error: --since is not implemented in this release; "
            "this command always performs a full sync.\n"
        )
        return EXIT_INVALID_INPUT

    try:
        config = KajiConfig.discover(start_dir=Path.cwd())
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        result = sync_from_github(
            config=config,
            repo_override=args.repo,
            quiet=args.quiet,
        )
    except SyncError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        sys.stderr.write(f"error: cache write failed: {exc}\n")
        return EXIT_RUNTIME_ERROR

    sys.stdout.write(
        f"Sync completed at {result.last_sync_at} "
        f"({result.issue_count} issues, {result.pages_fetched} pages, "
        f"{result.elapsed_seconds:.1f}s).\n"
    )
    return EXIT_OK


def cmd_sync_status(args: argparse.Namespace) -> int:
    """``kaji sync status`` の dispatcher (issue ``local-p1-8``)。"""
    import json as _json

    from .sync import SyncError, format_elapsed_human, read_sync_status

    try:
        config = KajiConfig.discover(start_dir=Path.cwd())
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        status = read_sync_status(config=config)
    except SyncError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_INVALID_INPUT

    elapsed_human: str | None = (
        format_elapsed_human(status.elapsed_seconds) if status.elapsed_seconds is not None else None
    )

    if args.json_mode:
        payload: dict[str, object] = {
            "forge": status.forge,
            "repo": status.repo,
            "last_sync_at": status.last_sync_at,
            "elapsed_seconds": (
                int(status.elapsed_seconds) if status.elapsed_seconds is not None else None
            ),
            "elapsed_human": elapsed_human,
            "issue_count": status.issue_count,
        }
        sys.stdout.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        return EXIT_OK

    forge_disp = status.forge or "(none)"
    repo_disp = status.repo or "(none)"
    last_disp = status.last_sync_at or "(never)"
    if status.elapsed_seconds is None:
        elapsed_disp = "n/a"
    else:
        elapsed_disp = f"{elapsed_human} ({int(status.elapsed_seconds)}s)"
    sys.stdout.write(f"forge        {forge_disp}\n")
    sys.stdout.write(f"repo         {repo_disp}\n")
    sys.stdout.write(f"last_sync    {last_disp}\n")
    sys.stdout.write(f"elapsed      {elapsed_disp}\n")
    cache_glob = "gh-*.json"
    sys.stdout.write(f"cached       {status.issue_count} ({cache_glob} under .kaji/cache/)\n")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "issue":
        return _handle_issue(args.args)
    if args.command == "pr":
        return _handle_pr(args.args)
    if args.command == "config":
        if args.config_command == "provider-type":
            return cmd_config_provider_type(args)
        parser.print_help()
        return EXIT_ABORT
    if args.command == "sync":
        if args.sync_command == "from-github":
            return cmd_sync_from_github(args)
        if args.sync_command == "status":
            return cmd_sync_status(args)
        parser.print_help()
        return EXIT_ABORT
    if args.command == "local":
        from .local_init import cmd_local

        return cmd_local(args)

    parser.print_help()
    return EXIT_ABORT


if __name__ == "__main__":
    sys.exit(main())
