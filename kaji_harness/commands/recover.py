"""``kaji recover`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    RecoveryTargetError,
    WorkflowValidationError,
)
from ..interactive_terminal_herdr import inspect_agent_pane
from ..models import Workflow
from ..preflight import preflight_workflow
from ..providers import get_provider
from ..providers.github import GitHubProviderError
from ..recovery.classify import classify_failure
from ..recovery.gates import (
    AgentPaneState,
    LauncherRunIdWatcher,
    RetryPlan,
    RetryPlanner,
    RetryRecorder,
    build_child_argv,
    default_child_popen,
    find_retry_child_run_id,
    prepare_retry,
    release_launcher_record,
)
from ..recovery.handler import RecoveryHandler, plan_recovery
from ..recovery.models import RecoveryDecision
from ..recovery.snapshot import FailureSnapshot
from ..recovery.target import resolve_recover_issue_context, select_target_run_dir
from ..runstate.facts import PaneRef
from ..workflow import load_workflow
from .exit_codes import (
    EXIT_CONFIG_NOT_FOUND,
    EXIT_DEFINITION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_RETRY_REFUSED,
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
    try:
        preflight = preflight_workflow(
            workflow,
            project_root=config.repo_root,
            skill_dir=config.paths.skill_dir,
        )
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR
    if preflight.errors:
        print(f"Error: {WorkflowValidationError(preflight.errors)}", file=sys.stderr)
        return EXIT_DEFINITION_ERROR

    # cmd_run と同じ workflow ↔ provider 整合検証。これが無いと triage / wait を経て
    # 起動した child が provider 不一致で必ず失敗する。
    rc = _validate_workflow_provider_match(workflow, config)
    if rc != EXIT_OK:
        return rc

    try:
        issue_context = resolve_recover_issue_context(config, provider, args.issue)
    except (ValueError, HarnessError, GitHubProviderError) as e:
        print(f"Error: cannot resolve issue {args.issue!r}: {e}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    artifacts_dir = resolve_artifacts_dir(config)
    runs_dir = artifacts_dir / issue_context.issue_id / "runs"
    if getattr(args, "retry_failed_step", False):
        if args.run_id is not None and not (runs_dir / args.run_id).is_dir():
            print(f"Error: run dir not found: {runs_dir / args.run_id}", file=sys.stderr)
            return EXIT_INVALID_INPUT
        return retry_failed_step(
            workflow=workflow,
            workflow_path=workflow_path,
            issue_id=issue_context.issue_id,
            artifacts_dir=artifacts_dir,
            workdir=start_dir,
            run_id=args.run_id,
        )
    try:
        run_dir = select_target_run_dir(runs_dir, args.run_id)
    except RecoveryTargetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
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
        incident=config.incident,
    )
    try:
        handler.run()
    except (OSError, HarnessError) as exc:
        print(f"Error: failure triage failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


def inspect_pane(pane: PaneRef) -> AgentPaneState:
    """失敗 step の Herdr pane を読み取り専用で照会する（ゲート 15 / 16）。"""
    return inspect_agent_pane(pane.pane_id, origin_pane=pane.origin_pane, run_id=pane.run_id)


def print_refusal(plan: RetryPlan) -> None:
    """拒否ゲートを機械可読な形で stderr に出す。"""
    print(
        f"Error: retry refused by safety gate(s): {', '.join(plan.gate_names())}",
        file=sys.stderr,
    )
    for gate in plan.gates:
        print(gate.stderr_line(), file=sys.stderr)


def retry_failed_step(
    *,
    workflow: Workflow,
    workflow_path: Path,
    issue_id: str,
    artifacts_dir: Path,
    workdir: Path,
    run_id: str | None,
) -> int:
    """Issue #540: 安全ゲートを通った run を失敗 step から 1 回やり直す（D4〜D7）。

    Returns:
        拒否 = 4（何も起動していない）、起動前の書き込み失敗・起動失敗 = 3、
        起動した場合は child の exit code。
    """
    planner = RetryPlanner(
        workflow=workflow,
        artifacts_dir=artifacts_dir,
        issue_id=issue_id,
        pane_inspector=inspect_pane,
    )
    try:
        prepared = prepare_retry(planner, run_id)
    except HarnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    plan = prepared.plan

    def base_decision(snapshot: FailureSnapshot) -> RecoveryDecision:
        return plan_recovery(
            snapshot=snapshot,
            classification=classify_failure(snapshot),
            workflow=workflow,
            workflow_path=workflow_path,
            issue_id=issue_id,
            auto_recover=False,
            now=datetime.now(UTC),
        )

    recorder = RetryRecorder(
        plan=plan,
        trigger="retry_failed_step",
        artifacts_dir=artifacts_dir,
        base_decision=base_decision,
    )
    if plan.refused:
        recorder.record_refused()
        print_refusal(plan)
        return EXIT_RETRY_REFUSED
    assert plan.run_id is not None and plan.resume_from is not None
    assert plan.root_run_id is not None and prepared.launcher_record is not None
    try:
        recorder.record_launched()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _warn(release_launcher_record(prepared.launcher_record))
        recorder.record_launch_failed()
        print(f"Error: cannot record the retry before launch: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    argv = build_child_argv(
        workflow_path=workflow_path,
        issue_id=issue_id,
        resume_from=plan.resume_from,
        root_run_id=plan.root_run_id,
        parent_run_id=plan.run_id,
        workdir=workdir,
        trigger="retry_failed_step",
    )
    try:
        process = default_child_popen(argv, workdir)
    except OSError as exc:
        recorder.record_launch_failed()
        _warn(release_launcher_record(prepared.launcher_record))
        print(f"Error: cannot start the retry child run: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    try:
        with LauncherRunIdWatcher(artifacts_dir, issue_id, prepared.launcher_record, process.pid):
            exit_code = process.wait()
        child_run_id = find_retry_child_run_id(artifacts_dir, issue_id, plan.run_id)
        try:
            recorder.record_child_end(child_run_id=child_run_id, exit_code=exit_code)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _warn(f"cannot record the retry result: {exc}")
    finally:
        _warn(release_launcher_record(prepared.launcher_record))
    return exit_code


def _warn(message: str | None) -> None:
    if message:
        print(f"WARNING: {message}", file=sys.stderr)
