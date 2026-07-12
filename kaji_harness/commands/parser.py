"""parser 構築・subcommand 登録（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


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
    _register_recover(subparsers)
    _register_validate(subparsers)
    _register_issue(subparsers)
    _register_pr(subparsers)
    _register_config(subparsers)
    _register_sync(subparsers)
    from ..local_init import register_subcommand as _register_local

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
    # Issue #305: incident-* skill 群が main worktree 基準の artifact root を
    # 副作用なく取得するための read-only エントリ。`kaji run` と同じ
    # `resolve_artifacts_dir()` を経由するため、feature worktree から呼んでも
    # main worktree に集約された run/state artifact の絶対パスを返す。
    ad = config_subs.add_parser(
        "artifacts-dir",
        help="Print resolved artifacts dir (main-worktree-based absolute path)",
    )
    ad.add_argument(
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
        "--reset-cycle",
        dest="reset_cycle",
        action="store_true",
        help="Reset the iteration count of the cycle that --from's step belongs "
        "to before running (requires --from).",
    )
    p.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Starting directory for config discovery (default: current directory)",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress agent output streaming")
    # Issue #235: 起動コンソール progress（stdlib logging）の閾値。--quiet とは独立で、
    # agent/exec の stdout streaming 抑制（--quiet）と harness progress 表示を分離する。
    p.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Console progress log level (default: INFO).",
    )
    # Issue #224: per-run overrides for the [execution] runner backend. These take
    # precedence over both config.local.toml and config.toml (precedence 1).
    p.add_argument(
        "--agent-runner",
        dest="agent_runner",
        choices=["headless", "interactive-terminal"],
        default=None,
        help="Override the agent runner backend for this run.",
    )
    close_group = p.add_mutually_exclusive_group()
    close_group.add_argument(
        "--interactive-terminal-close-on-verdict",
        dest="close_on_verdict",
        action="store_true",
        default=None,
        help="Close the interactive terminal after the verdict is detected.",
    )
    close_group.add_argument(
        "--no-interactive-terminal-close-on-verdict",
        dest="close_on_verdict",
        action="store_false",
        default=None,
        help="Keep the interactive terminal open after the verdict is detected.",
    )
    _add_recovery_arguments(p)


def _add_recovery_arguments(p: argparse.ArgumentParser) -> None:
    """Issue #288: failure triage / auto recovery の per-run override と chain flag。"""
    triage_group = p.add_mutually_exclusive_group()
    triage_group.add_argument(
        "--failure-triage",
        dest="failure_triage",
        action="store_true",
        default=None,
        help="Classify the failure and record a triage report when the run fails.",
    )
    triage_group.add_argument(
        "--no-failure-triage",
        dest="failure_triage",
        action="store_false",
        default=None,
        help="Disable failure triage for this run.",
    )
    recover_group = p.add_mutually_exclusive_group()
    recover_group.add_argument(
        "--auto-recover",
        dest="auto_recover",
        action="store_true",
        default=None,
        help="Automatically resume once (per recovery chain) after a recoverable failure.",
    )
    recover_group.add_argument(
        "--no-auto-recover",
        dest="auto_recover",
        action="store_false",
        default=None,
        help="Disable the automatic child run for this run.",
    )
    # 以下 2 つは handler が child run 起動時に付与する内部伝播用（手動指定も可）。
    p.add_argument(
        "--recovery-root",
        dest="recovery_root",
        default=None,
        help="Root run_id of the recovery chain this run belongs to.",
    )
    p.add_argument(
        "--recovery-parent",
        dest="recovery_parent",
        default=None,
        help="Direct parent run_id (requires --recovery-root).",
    )


def _register_recover(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``recover`` subcommand (Issue #288).

    失敗 run の artifact に対して failure triage handler を手動起動する入口。
    調査・再調査・opt-in 再開に使う。
    """
    p = subparsers.add_parser("recover", help="Run failure triage against a failed run's artifacts")
    p.add_argument("workflow", type=Path, help="Workflow YAML used by the target run")
    p.add_argument("issue", type=str, help="Issue ID (GitHub number or local form)")
    p.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="Target run_id (default: the newest run for the issue).",
    )
    p.add_argument(
        "--auto-recover",
        dest="auto_recover",
        action="store_true",
        default=False,
        help="Allow the handler to start a child run when the decision is 'resume'.",
    )
    p.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Starting directory for config discovery (default: current directory)",
    )


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
