"""``kaji run`` + failure triage + provider 整合ガード（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    SecurityError,
    SkillFrontmatterError,
    SkillNotFound,
    WorkflowValidationError,
)
from ..models import Workflow
from ..providers import IssueProvider, actual_provider_type, get_provider
from ..recovery.handler import RecoveryHandler
from ..runner import WorkflowRunner
from ..state import _format_issue_ref
from ..workflow import load_workflow
from .config import _emit_provider_overlay_divergence_warning
from .exit_codes import (
    EXIT_ABORT,
    EXIT_CONFIG_NOT_FOUND,
    EXIT_DEFINITION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
)


def _apply_execution_overrides(config: KajiConfig, args: argparse.Namespace) -> KajiConfig:
    """Apply ``kaji run`` CLI overrides onto ``config.execution`` (precedence 1).

    ``--agent-runner interactive-terminal`` is normalized to the config value
    ``interactive_terminal``. Each option is independent: when an option is
    unspecified (``None``) the resolved config value is kept. The three-state
    ``close_on_verdict`` (``None`` / ``True`` / ``False``) distinguishes "not
    given" from an explicit ``--no-...``.

    Issue #288: ``failure_triage`` / ``auto_recover`` も同じ three-state で上書きする。
    triage が無効なら handler 自体が起動しないため、``auto_recover`` は常に無効へ
    正規化する（CLI flag が無い場合の config 組み合わせにも適用する）。
    """
    execution = config.execution
    changed = False

    runner_override = getattr(args, "agent_runner", None)
    if runner_override is not None:
        execution = dataclasses.replace(execution, agent_runner=runner_override.replace("-", "_"))
        changed = True
    close_override = getattr(args, "close_on_verdict", None)
    if close_override is not None:
        execution = dataclasses.replace(
            execution, interactive_terminal_close_on_verdict=close_override
        )
        changed = True
    triage_override = getattr(args, "failure_triage", None)
    if triage_override is not None:
        execution = dataclasses.replace(execution, failure_triage=triage_override)
        changed = True
    recover_override = getattr(args, "auto_recover", None)
    if recover_override is not None:
        execution = dataclasses.replace(execution, auto_recover=recover_override)
        changed = True

    if not execution.failure_triage and execution.auto_recover:
        execution = dataclasses.replace(execution, auto_recover=False)
        changed = True

    if not changed:
        return config
    return dataclasses.replace(config, execution=execution)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the `run` subcommand."""
    # Issue #235: 起動コンソール progress logging を初期化する（argparse choices で
    # 検証済みの log level を stdlib level int へ変換）。RunLogger の JSONL とは別系統。
    from ..console_log import configure_console_logging

    configure_console_logging(getattr(logging, getattr(args, "log_level", "INFO")))

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

    # Dependency: --reset-cycle requires --from
    if args.reset_cycle and not args.from_step:
        print(
            "Error: --reset-cycle requires --from <step>",
            file=sys.stderr,
        )
        return EXIT_DEFINITION_ERROR

    # Issue #288: chain identity は root を伴わない parent 単独では成立しない。
    if args.recovery_parent and not args.recovery_root:
        print(
            "Error: --recovery-parent requires --recovery-root",
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

    # Issue #224: CLI option override（agent_runner / close_on_verdict）を
    # config.local.toml / config.toml より優先して適用する（precedence 1）。
    config = _apply_execution_overrides(config, args)

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
    # recovery child は ``--workdir`` を cwd として起動されるため、相対パスのままだと
    # invocation cwd と ``--workdir`` が異なる場合に workflow を解決できない。
    workflow_path = workflow_path.resolve()

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
    artifacts_dir = resolve_artifacts_dir(config)
    runner = WorkflowRunner(
        workflow=workflow,
        issue_number=args.issue,
        project_root=project_root,
        artifacts_dir=artifacts_dir,
        config=config,
        from_step=args.from_step,
        single_step=args.single_step,
        before_step=args.before_step,
        reset_cycle=args.reset_cycle,
        verbose=not args.quiet,
        recovery_root=args.recovery_root,
        recovery_parent=args.recovery_parent,
    )
    try:
        state = runner.run()
    except (WorkflowValidationError, SkillNotFound, SecurityError, SkillFrontmatterError) as e:
        print(f"Error: {e}", file=sys.stderr)
        exit_code = EXIT_DEFINITION_ERROR
    except HarnessError as e:
        print(f"Error: {e}", file=sys.stderr)
        exit_code = EXIT_RUNTIME_ERROR
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        exit_code = EXIT_ABORT
    else:
        # Check for ABORT verdict
        if state.last_transition_verdict and state.last_transition_verdict.status == "ABORT":
            print(
                f"Workflow aborted: {state.last_transition_verdict.reason}",
                file=sys.stderr,
            )
            exit_code = EXIT_ABORT
        else:
            # Success summary: canonical_issue_ref を優先（Phase 3-d preflight § 1）。
            # ``[provider]`` 未設定 fallback などで未確定の場合のみ raw 入力で整形する。
            issue_ref = runner.canonical_issue_ref or _format_issue_ref(args.issue)
            print(f"Workflow '{workflow.name}' completed for issue {issue_ref}")
            return EXIT_OK

    # Issue #288: ERROR / ABORT 終端でのみ failure triage を起動する。run_dir 作成前の
    # 失敗（config / workflow validation / IssueContext 解決失敗）は artifact が無く
    # 根拠のない Issue コメントになるため triage 対象外。
    child_exit_code = _run_failure_triage(
        config=config,
        workflow=workflow,
        workflow_path=workflow_path,
        runner=runner,
        artifacts_dir=artifacts_dir,
        workdir=start_dir,
    )
    # child run を起動した場合、親プロセスの exit code は chain の最終結果に一致させる。
    return child_exit_code if child_exit_code is not None else exit_code


def _run_failure_triage(
    *,
    config: KajiConfig,
    workflow: Workflow,
    workflow_path: Path,
    runner: WorkflowRunner,
    artifacts_dir: Path,
    workdir: Path,
) -> int | None:
    """失敗した run に対し failure triage handler を起動する（Issue #288）。

    triage は best-effort であり、handler 側の失敗で元の run の exit code を変えない
    （WARN を stderr に出して ``None`` を返す）。

    Returns:
        child run を起動した場合はその exit code、そうでなければ ``None``。
    """
    if not config.execution.failure_triage:
        return None
    run_dir = runner.last_run_dir
    if run_dir is None or runner.canonical_issue_id is None:
        return None

    provider: IssueProvider | None
    try:
        provider = get_provider(config)
    except ValueError as exc:
        print(f"WARNING: failure triage cannot resolve a provider: {exc}", file=sys.stderr)
        provider = None

    handler = RecoveryHandler(
        workflow=workflow,
        workflow_path=workflow_path,
        issue_id=runner.canonical_issue_id,
        issue_ref=runner.canonical_issue_ref or runner.canonical_issue_id,
        artifacts_dir=artifacts_dir,
        run_dir=run_dir,
        workdir=workdir,
        provider=provider,
        auto_recover=config.execution.auto_recover,
    )
    try:
        result = handler.run()
    except (OSError, HarnessError) as exc:
        print(f"WARNING: failure triage failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return result.child_exit_code


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
