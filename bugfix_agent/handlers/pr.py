"""PR_CREATE state handler for Bugfix Agent v5

This module provides:
- handle_pr_create: Create Pull Request and share PR URL
"""

from ..agent_context import AgentContext
from ..errors import check_tool_result
from ..prompts import load_prompt
from ..state import SessionState, State


def handle_pr_create(ctx: AgentContext, state: SessionState) -> State:
    """PR_CREATE: gh pr create 実行、PR URL 共有

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        次のステート (COMPLETE)
    """
    print("🎉 Creating Pull Request...")

    log_dir = ctx.artifacts_state_dir("pr_create")

    prompt = load_prompt(
        "pr_create",
        issue_url=ctx.issue_url,
        issue_number=ctx.issue_number,
    )

    result, _ = ctx.implementer.run(
        prompt=prompt,
        context=ctx.issue_url,
        log_dir=log_dir,
    )
    check_tool_result(result, "implementer")

    state.completed_states.append("PR_CREATE")
    return State.COMPLETE
