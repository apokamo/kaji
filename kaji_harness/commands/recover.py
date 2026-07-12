"""``kaji recover`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    WorkflowValidationError,
)
from ..providers import IssueContext, IssueProvider, get_provider, normalize_id
from ..providers.github import GitHubProviderError
from ..providers.local import LocalProviderError
from ..recovery.handler import RecoveryHandler
from ..recovery.snapshot import read_run_log_events
from ..workflow import load_workflow
from .exit_codes import (
    EXIT_CONFIG_NOT_FOUND,
    EXIT_DEFINITION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
)
from .run import _validate_workflow_provider_match


def cmd_recover(args: argparse.Namespace) -> int:
    """Execute the `recover` subcommand (Issue #288).

    失敗 run の artifact に対して handler を手動起動する。triage が完了すれば decision に
    かかわらず ``EXIT_OK``。対象 run 不在 / 進行中 run は ``EXIT_INVALID_INPUT``、
    handler 内部エラーは ``EXIT_RUNTIME_ERROR``。
    """
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        print(f"Error: --workdir '{args.workdir}' is not a valid directory", file=sys.stderr)
        return EXIT_DEFINITION_ERROR
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except (ConfigNotFoundError, ConfigLoadError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_CONFIG_NOT_FOUND

    try:
        provider = get_provider(config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if not args.workflow.exists():
        print(f"Error: Workflow file not found: {args.workflow}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR
    # child run と同じく ``--workdir`` を cwd として起動するため絶対化する（cmd_run と同様）。
    workflow_path = args.workflow.resolve()
    try:
        workflow = load_workflow(workflow_path)
    except WorkflowValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR

    # cmd_run と同じ workflow ↔ provider 整合検証。これが無いと triage / wait を経て
    # 起動した child が provider 不一致で必ず失敗する。
    rc = _validate_workflow_provider_match(workflow, config)
    if rc != EXIT_OK:
        return rc

    try:
        issue_context = _resolve_recover_issue_context(config, provider, args.issue)
    except (ValueError, HarnessError, GitHubProviderError, LocalProviderError) as e:
        print(f"Error: cannot resolve issue {args.issue!r}: {e}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    artifacts_dir = resolve_artifacts_dir(config)
    runs_dir = artifacts_dir / issue_context.issue_id / "runs"
    run_dir = _resolve_target_run_dir(runs_dir, args.run_id)
    if run_dir is None:
        return EXIT_INVALID_INPUT

    handler = RecoveryHandler(
        workflow=workflow,
        workflow_path=workflow_path,
        issue_id=issue_context.issue_id,
        issue_ref=issue_context.issue_ref,
        artifacts_dir=artifacts_dir,
        run_dir=run_dir,
        workdir=start_dir,
        provider=provider,
        auto_recover=args.auto_recover,
    )
    try:
        handler.run()
    except (OSError, HarnessError) as exc:
        print(f"Error: failure triage failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


def _resolve_recover_issue_context(
    config: KajiConfig, provider: IssueProvider, issue_input: str
) -> IssueContext:
    """``kaji recover`` 用に canonical Issue ID / ref を解決する。"""
    assert config.provider is not None  # get_provider 成功後
    provider_type = config.provider.type
    machine_id = config.provider.local.machine_id if provider_type == "local" else None
    rid = normalize_id(issue_input, provider_name=provider_type, machine_id=machine_id)
    return provider.resolve_issue_context(rid.value)


def _resolve_target_run_dir(runs_dir: Path, run_id: str | None) -> Path | None:
    """triage 対象の run dir を解決する。不正な場合は stderr に理由を出して ``None``。

    実行中 run（``workflow_end`` event が無い）への誤介入は拒否する。
    """
    if not runs_dir.is_dir():
        print(f"Error: no runs found under {runs_dir}", file=sys.stderr)
        return None
    if run_id is not None:
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            print(f"Error: run dir not found: {run_dir}", file=sys.stderr)
            return None
    else:
        candidates = sorted(p for p in runs_dir.iterdir() if p.is_dir())
        if not candidates:
            print(f"Error: no runs found under {runs_dir}", file=sys.stderr)
            return None
        run_dir = candidates[-1]

    run_log = run_dir / "run.log"
    if not run_log.is_file():
        print(f"Error: run.log not found: {run_log}", file=sys.stderr)
        return None
    try:
        events = read_run_log_events(run_log)
    except OSError as exc:
        print(f"Error: cannot read {run_log}: {exc}", file=sys.stderr)
        return None
    end = [e for e in events if e.get("event") == "workflow_end"]
    if not end:
        print(
            f"Error: run {run_dir.name} is still in progress (no workflow_end event); "
            "refusing to run failure triage against it",
            file=sys.stderr,
        )
        return None
    if end[-1].get("status") not in ("ERROR", "ABORT"):
        print(
            f"Error: run {run_dir.name} ended with status {end[-1].get('status')!r}; "
            "failure triage only applies to ERROR / ABORT runs",
            file=sys.stderr,
        )
        return None
    return run_dir
