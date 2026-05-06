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

from .config import KajiConfig
from .errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    SecurityError,
    SkillNotFound,
    WorkflowValidationError,
)
from .providers import ResolvedId, get_provider, normalize_id
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
    return parser


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
    """Register the `issue` subcommand (gh issue passthrough wrapper).

    Phase 1: provider 抽象が未導入のため、すべての引数を `gh issue` に転送する。
    Phase 3 で LocalProvider 導入時に dispatch ロジックへ差し替える。
    """
    p = subparsers.add_parser(
        "issue",
        help="Issue operations (Phase 1: gh issue passthrough)",
        add_help=False,
    )
    p.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to 'gh issue'")


def _register_pr(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `pr` subcommand (gh pr passthrough wrapper).

    Phase 1: すべての引数を `gh pr` に転送する。例外として `pr merge` は
    method flag (``--merge`` / ``--squash`` / ``--rebase``) を露出せず、
    内部で常に ``--merge`` (= ``--no-ff`` 相当) 固定で gh に渡す
    (`docs/guides/git-commit-flow.md` の merge 規約に従う)。
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

    # Run workflow
    try:
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=args.issue,
            project_root=project_root,
            artifacts_dir=config.artifacts_dir,
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

    # Success summary
    print(f"Workflow '{workflow.name}' completed for issue {_format_issue_ref(args.issue)}")
    return EXIT_OK


_FORGE_METHOD_FLAGS = {"--merge", "--squash", "--rebase"}


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

    if repo and "--repo" not in args and "-R" not in args:
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


def _handle_pr(raw_args: list[str]) -> int:
    """Two-stage dispatch for ``kaji pr``.

    builtin sub (``review-comments`` / ``reviews`` / ``reply-to-comment``) →
    dedicated handler; otherwise fall back to ``gh pr`` passthrough.

    Phase 3-c rev #3（review #3 反映）:

    - 壊れた config → exit 2（fail-fast、握りつぶさない）
    - ``[provider.github] repo`` が設定されている場合は ``--repo`` で強制注入
      （builtin / passthrough 両方）。``provider.type='local'`` 配下は
      Phase 4 で bare-provider エラー化するため、本 PR では legacy 経路で
      通す（``repo_override`` は ``None``）
    """
    try:
        config = _load_config_for_dispatch_or_none()
    except ConfigLoadError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    repo_override: str | None = None
    if config is not None and config.provider is not None and config.provider.type == "github":
        if not config.provider.github.repo:
            sys.stderr.write(
                "Error: provider.type='github' requires provider.github.repo (e.g. 'owner/name').\n"
            )
            return EXIT_INVALID_INPUT
        repo_override = config.provider.github.repo
    elif config is not None and config.provider is None:
        # `[provider]` 未設定 → WARN（副作用）+ legacy 経路
        get_provider(config)

    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
        return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
    return _forward_to_gh("pr", raw_args, repo=repo_override)


def _load_config_for_dispatch_or_none() -> KajiConfig | None:
    """Config を読み込む（``kaji issue`` / ``kaji pr`` dispatch 用）。

    Phase 3-c の方針（review #3 反映）:

    - ``ConfigNotFoundError`` のみ ``None`` で legacy 経路に fallback
      （リポジトリ外で `kaji issue view 1` が直接 `gh` に転送される従来挙動）
    - ``ConfigLoadError``（壊れた TOML / 未知の type 等）は **raise**
      → 呼出側が user-facing error として exit 2 を返す
    """
    try:
        return KajiConfig.discover(start_dir=Path.cwd())
    except ConfigNotFoundError:
        return None


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
        config = _load_config_for_dispatch_or_none()
    except ConfigLoadError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    if config is not None and config.provider is not None:
        try:
            provider = get_provider(config)
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return EXIT_INVALID_INPUT
        if isinstance(provider, LocalProvider):
            return _handle_issue_local(provider, raw_args)
        # GitHubProvider 経路: 設定 repo を --repo で強制注入し cwd 推論を防ぐ
        return _forward_to_gh("issue", raw_args, repo=config.provider.github.repo)
    # `[provider]` 未設定 → WARN（副作用）+ legacy 経路
    if config is not None:
        get_provider(config)
    return _forward_to_gh("issue", raw_args)


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
    """``jq`` を subprocess 起動して ``json_text`` に式を適用する。

    ``gh --jq`` 互換挙動として **raw 出力**（`-r`）を採用する。

    `gh` は内部で gojq を使い、結果が string 値の場合のみ quote 無しで
    raw 出力する（array / object はそのまま JSON）。これは `jq -r` と
    同じ動作。Skill 群は ``CURRENT_BODY=$(kaji issue view N --json body
    -q '.body')`` のように shell 変数代入で raw 値を期待しているため、
    quote 付き出力では下流が壊れる（rev 2 で発覚した本指摘）。

    Phase 3-c では Python 製 jq 互換実装を持たず、system ``jq`` に委譲する。
    ``jq`` 不在時は exit 3（runtime error）。
    """
    if shutil.which("jq") is None:
        sys.stderr.write(
            "Error: 'jq' is required for --jq/-q under provider.type='local' "
            "but was not found in PATH. Install jq (e.g. 'apt install jq', "
            "'brew install jq') or invoke without --jq.\n"
        )
        return "", EXIT_RUNTIME_ERROR
    try:
        proc = subprocess.run(
            # -r: raw output for string results (gh --jq compatible)
            ["jq", "-r", expr],
            input=json_text,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"Error: failed to invoke 'jq': {exc}\n")
        return "", EXIT_RUNTIME_ERROR
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return "", EXIT_RUNTIME_ERROR
    return proc.stdout, EXIT_OK


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


_LOCAL_ISSUE_SUBS = {"view", "create", "edit", "comment", "close", "list"}


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
        # sub == "list"
        return _local_issue_list(provider, rest)
    except IssueReadOnlyError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except IssueNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_RUNTIME_ERROR
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
        required=True,
        type=str,
        help="kebab-case slug (required for local provider)",
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
    return EXIT_OK


def _local_issue_comment(provider: LocalProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue comment", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--body", default=None, type=str)
    p.add_argument("--body-file", dest="body_file", default=None, type=str)
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

    parser.print_help()
    return EXIT_ABORT


if __name__ == "__main__":
    sys.exit(main())
