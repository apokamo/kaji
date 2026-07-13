"""``kaji recover`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    RecoveryTargetError,
    WorkflowValidationError,
)
from ..providers import get_provider
from ..providers.github import GitHubProviderError
from ..providers.local import LocalProviderError
from ..recovery.handler import RecoveryHandler
from ..recovery.target import resolve_recover_issue_context, select_target_run_dir
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
        issue_context = resolve_recover_issue_context(config, provider, args.issue)
    except (ValueError, HarnessError, GitHubProviderError, LocalProviderError) as e:
        print(f"Error: cannot resolve issue {args.issue!r}: {e}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    artifacts_dir = resolve_artifacts_dir(config)
    runs_dir = artifacts_dir / issue_context.issue_id / "runs"
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
    )
    try:
        handler.run()
    except (OSError, HarnessError) as exc:
        print(f"Error: failure triage failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    return EXIT_OK
