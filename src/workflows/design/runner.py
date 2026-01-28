"""Design workflow runner.

This module provides the entry point for running the DesignWorkflow
from CLI or other interfaces.
"""

from argparse import Namespace
from pathlib import Path

from src.core.artifacts import save_jsonl_log
from src.core.context import AgentContext
from src.core.errors import LoopLimitExceededError
from src.core.prompts import PromptLoadError
from src.core.providers import GitHubIssueProvider
from src.core.tools.claude import ClaudeTool
from src.core.tools.errors import AIToolError
from src.core.verdict import AgentAbortError, VerdictParseError
from src.workflows.base import SessionState

from .context import setup_workflow_context
from .workflow import DesignWorkflow


def run_design_workflow(args: Namespace) -> int:
    """Run the design workflow.

    Args:
        args: Parsed CLI arguments with 'issue' and optionally 'input',
              'workdir', and 'dry_run'.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    # 1. Create issue provider from --issue argument
    try:
        issue_provider = GitHubIssueProvider(args.issue)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    # 2. Create AI tools (using Claude for all roles)
    # TODO: Permission settings are intentionally permissive for initial development.
    #       After PG verification, adjust to safer defaults (e.g., remove bypassPermissions).
    #       See: https://github.com/apokamo/dev-agent-orchestra/issues/28
    claude_tool = ClaudeTool(
        model="sonnet",
        permission_mode="bypassPermissions",
        skip_permissions=True,
    )

    # 3. Determine artifacts base path from --workdir option
    workdir = getattr(args, "workdir", None)
    if workdir:
        artifacts_base = Path(workdir) / "artifacts"
    else:
        artifacts_base = Path("artifacts")

    # 4. Create AgentContext
    ctx = AgentContext(
        analyzer=claude_tool,
        reviewer=claude_tool,
        implementer=claude_tool,
        issue_provider=issue_provider,
        artifacts_base=artifacts_base,
    )

    # 5. Create SessionState
    session = SessionState()

    # 6. Setup workflow context (load --input file if provided)
    setup_workflow_context(args, ctx, session)

    # 7. Create workflow
    workflow = DesignWorkflow()

    # 8. Get dry_run flag
    dry_run = getattr(args, "dry_run", False)

    # 9. Run workflow loop
    return _run_workflow_loop(workflow, ctx, session, dry_run=dry_run)


def _run_workflow_loop(
    workflow: DesignWorkflow,
    ctx: AgentContext,
    session: SessionState,
    *,
    dry_run: bool = False,
) -> int:
    """Execute the workflow state machine loop.

    Args:
        workflow: The workflow to execute.
        ctx: Agent context with tools and issue provider.
        session: Session state for tracking progress.
        dry_run: If True, skip Issue updates (comments).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    current_state = workflow.initial_state
    artifacts_dir = ctx.ensure_artifacts_dir()

    # Log workflow start
    save_jsonl_log(
        artifacts_dir,
        "workflow_start",
        {
            "workflow": workflow.name,
            "issue_url": ctx.issue_provider.issue_url,
            "issue_number": ctx.issue_provider.issue_number,
            "dry_run": dry_run,
        },
    )

    try:
        while current_state not in workflow.terminal_states:
            # Get handler for current state
            handler = workflow.get_handler(current_state)

            # Log state transition
            save_jsonl_log(
                artifacts_dir,
                "state_enter",
                {"state": current_state.name},
            )

            # Execute handler
            verdict = handler(ctx, session)

            # Get next state from verdict
            next_state = workflow.get_next_state(current_state, verdict)

            # Log state exit
            save_jsonl_log(
                artifacts_dir,
                "state_exit",
                {
                    "state": current_state.name,
                    "verdict": verdict.value,
                    "next_state": next_state.name,
                },
            )

            current_state = next_state

        # Workflow completed successfully
        save_jsonl_log(
            artifacts_dir,
            "workflow_complete",
            {"final_state": current_state.name},
        )

        # Report success to issue (skip if dry_run)
        _report_success(ctx, artifacts_dir, dry_run=dry_run)
        return 0

    except LoopLimitExceededError as e:
        _report_error(ctx, artifacts_dir, "loop_limit_exceeded", str(e), dry_run=dry_run)
        return 1

    except AIToolError as e:
        _report_error(ctx, artifacts_dir, "ai_tool_error", str(e), dry_run=dry_run)
        return 1

    except VerdictParseError as e:
        _report_error(ctx, artifacts_dir, "verdict_parse_error", str(e), dry_run=dry_run)
        return 1

    except AgentAbortError as e:
        _report_error(ctx, artifacts_dir, "agent_abort", str(e), dry_run=dry_run)
        return 1

    except PromptLoadError as e:
        _report_error(ctx, artifacts_dir, "prompt_load_error", str(e), dry_run=dry_run)
        return 1

    except Exception as e:
        _report_error(ctx, artifacts_dir, "unexpected_error", str(e), dry_run=dry_run)
        return 1


def _report_success(
    ctx: AgentContext,
    artifacts_dir: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Report workflow success to the issue.

    Args:
        ctx: Agent context with issue provider.
        artifacts_dir: Path to artifacts directory.
        dry_run: If True, skip Issue comment (log only).
    """
    save_jsonl_log(artifacts_dir, "report_success", {"dry_run": dry_run})

    design_output_path = artifacts_dir / "design" / "response.md"
    message = f"""## DesignWorkflow 完了

設計が完了しました。

**成果物**: `{design_output_path}`
"""

    if dry_run:
        print("[dry-run] Would post success comment to Issue")
        return

    try:
        ctx.issue_provider.add_comment(message)
    except Exception as e:
        print(f"Warning: Failed to add success comment: {e}")


def _report_error(
    ctx: AgentContext,
    artifacts_dir: Path,
    error_type: str,
    message: str,
    *,
    dry_run: bool = False,
) -> None:
    """Report workflow error to the issue.

    Args:
        ctx: Agent context with issue provider.
        artifacts_dir: Path to artifacts directory.
        error_type: Type of error that occurred.
        message: Error message details.
        dry_run: If True, skip Issue comment (log only).
    """
    save_jsonl_log(
        artifacts_dir,
        "workflow_error",
        {"error_type": error_type, "message": message, "dry_run": dry_run},
    )

    error_message = f"""## DesignWorkflow エラー

ワークフローがエラーで終了しました。

**エラー種別**: `{error_type}`
**詳細**: {message}
"""

    if dry_run:
        print(f"[dry-run] Would post error comment to Issue: {error_type}")
        return

    try:
        ctx.issue_provider.add_comment(error_message)
    except Exception as e:
        print(f"Warning: Failed to add error comment: {e}")
