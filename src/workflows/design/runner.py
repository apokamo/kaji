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
        args: Parsed CLI arguments with 'issue' and optionally 'input'.

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
    claude_tool = ClaudeTool(
        model="sonnet",
        permission_mode="bypassPermissions",
        skip_permissions=True,
    )

    # 3. Create AgentContext
    ctx = AgentContext(
        analyzer=claude_tool,
        reviewer=claude_tool,
        implementer=claude_tool,
        issue_provider=issue_provider,
        artifacts_base=Path("artifacts"),
    )

    # 4. Create SessionState
    session = SessionState()

    # 5. Setup workflow context (load --input file if provided)
    setup_workflow_context(args, ctx, session)

    # 6. Create workflow
    workflow = DesignWorkflow()

    # 7. Run workflow loop
    return _run_workflow_loop(workflow, ctx, session)


def _run_workflow_loop(
    workflow: DesignWorkflow,
    ctx: AgentContext,
    session: SessionState,
) -> int:
    """Execute the workflow state machine loop.

    Args:
        workflow: The workflow to execute.
        ctx: Agent context with tools and issue provider.
        session: Session state for tracking progress.

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

        # Report success to issue
        _report_success(ctx, artifacts_dir)
        return 0

    except LoopLimitExceededError as e:
        _report_error(ctx, artifacts_dir, "loop_limit_exceeded", str(e))
        return 1

    except AIToolError as e:
        _report_error(ctx, artifacts_dir, "ai_tool_error", str(e))
        return 1

    except VerdictParseError as e:
        _report_error(ctx, artifacts_dir, "verdict_parse_error", str(e))
        return 1

    except AgentAbortError as e:
        _report_error(ctx, artifacts_dir, "agent_abort", str(e))
        return 1

    except PromptLoadError as e:
        _report_error(ctx, artifacts_dir, "prompt_load_error", str(e))
        return 1

    except Exception as e:
        _report_error(ctx, artifacts_dir, "unexpected_error", str(e))
        return 1


def _report_success(ctx: AgentContext, artifacts_dir: Path) -> None:
    """Report workflow success to the issue."""
    save_jsonl_log(artifacts_dir, "report_success", {})

    design_output_path = artifacts_dir / "design" / "response.md"
    message = f"""## DesignWorkflow 完了

設計が完了しました。

**成果物**: `{design_output_path}`
"""
    try:
        ctx.issue_provider.add_comment(message)
    except Exception as e:
        print(f"Warning: Failed to add success comment: {e}")


def _report_error(
    ctx: AgentContext,
    artifacts_dir: Path,
    error_type: str,
    message: str,
) -> None:
    """Report workflow error to the issue."""
    save_jsonl_log(
        artifacts_dir,
        "workflow_error",
        {"error_type": error_type, "message": message},
    )

    error_message = f"""## DesignWorkflow エラー

ワークフローがエラーで終了しました。

**エラー種別**: `{error_type}`
**詳細**: {message}
"""
    try:
        ctx.issue_provider.add_comment(error_message)
    except Exception as e:
        print(f"Warning: Failed to add error comment: {e}")
