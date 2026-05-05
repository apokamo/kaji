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


def _forward_to_gh(group: str, raw_args: list[str]) -> int:
    """`gh <group> ...` に引数を転送する Phase 1 用 wrapper。

    `pr merge` の method flag は露出せず常に `--merge` (= no-ff) 固定で渡す。
    詳細: ``docs/guides/git-commit-flow.md``。

    `argparse.REMAINDER` は先頭の `--` を残したり残さなかったりするため、
    user 入力の意味を変えないよう先頭の単独 `--` のみを除去する。
    """
    if not shutil.which("gh"):
        print(
            "Error: 'gh' CLI not found in PATH. "
            "Install GitHub CLI to use 'kaji issue' / 'kaji pr' (Phase 1).",
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


def _detect_repo() -> str | None:
    """Return current repository in `owner/name` form, or None on failure.

    Calls ``gh repo view --json nameWithOwner -q .nameWithOwner``.
    """
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
) -> int:
    """Forward to ``gh api repos/<repo>/pulls/<N>/comments``."""
    return _forward_pr_api_list(
        pr_id, path_suffix="comments", json_fields=json_fields, jq_expr=jq_expr
    )


def _forward_pr_reviews(
    pr_id: str,
    *,
    json_fields: list[str] | None,
    jq_expr: str | None,
) -> int:
    """Forward to ``gh api repos/<repo>/pulls/<N>/reviews``."""
    return _forward_pr_api_list(
        pr_id, path_suffix="reviews", json_fields=json_fields, jq_expr=jq_expr
    )


def _forward_pr_api_list(
    pr_id: str,
    *,
    path_suffix: str,
    json_fields: list[str] | None,
    jq_expr: str | None,
) -> int:
    if not _is_ascii_decimal(pr_id):
        sys.stderr.write(f"Error: PR_ID must be ASCII decimal, got: {pr_id}\n")
        return EXIT_INVALID_INPUT
    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo()
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


def _forward_pr_reply_to_comment(pr_id: str, *, comment_id: str, body: str) -> int:
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
    repo = _detect_repo()
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


def _dispatch_pr_builtin(sub: str, rest: list[str]) -> int:
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
        return _forward_pr_review_comments(ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr)
    if sub == "reviews":
        return _forward_pr_reviews(ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr)
    return _forward_pr_reply_to_comment(ns.pr_id, comment_id=ns.comment_id, body=ns.body)


def _handle_pr(raw_args: list[str]) -> int:
    """Two-stage dispatch for ``kaji pr``.

    builtin sub (``review-comments`` / ``reviews`` / ``reply-to-comment``) →
    dedicated handler; otherwise fall back to Phase 1 ``gh pr`` passthrough.
    """
    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
        return _dispatch_pr_builtin(args[0], args[1:])
    return _forward_to_gh("pr", raw_args)


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "issue":
        return _forward_to_gh("issue", args.args)
    if args.command == "pr":
        return _handle_pr(args.args)

    parser.print_help()
    return EXIT_ABORT


if __name__ == "__main__":
    sys.exit(main())
