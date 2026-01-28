"""INIT state handler for Bugfix Agent v5

This module provides:
- handle_init: Check Issue requirements and validate VERDICT
"""

from ..agent_context import AgentContext
from ..errors import AgentAbortError, check_tool_result
from ..prompts import load_prompt
from ..state import SessionState, State
from ..verdict import Verdict, create_ai_formatter, handle_abort_verdict, parse_verdict


def handle_init(ctx: AgentContext, state: SessionState) -> State:
    """INIT: Issue 本文の必須情報存在確認

    Issue #194 Protocol: VERDICT形式で判定結果を出力。
    ABORTの場合はAgentAbortErrorがraiseされる。

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        PASS → INVESTIGATE

    Raises:
        AgentAbortError: ABORTが返された場合（情報不足）
    """
    print("📋 Checking Issue requirements...")

    log_dir = ctx.artifacts_state_dir("init")
    prompt = load_prompt("init", issue_url=ctx.issue_url)

    result, _ = ctx.reviewer.run(prompt=prompt, context=ctx.issue_url, log_dir=log_dir)
    check_tool_result(result, "reviewer")

    # VERDICT形式でパース（Issue #292: ハイブリッドフォールバック対応）
    ai_formatter = create_ai_formatter(ctx.reviewer, context=ctx.issue_url, log_dir=log_dir)
    verdict = parse_verdict(result, ai_formatter=ai_formatter)

    # ABORTの場合はコメントを投稿してから例外を送出
    if verdict == Verdict.ABORT:
        try:
            handle_abort_verdict(verdict, result)
        except AgentAbortError as e:
            comment_body = (
                f"## INIT Check Result\n\n{result}\n\n"
                "---\n"
                f"**INIT ABORT**: {e.reason}\n\n"
                f"**Suggestion**: {e.suggestion}"
            )
            ctx.issue_provider.add_comment(comment_body)
            raise

    # INITではPASSのみ許可（Issue #194 VERDICT対応表: RETRY/BACK_DESIGNは使用不可）
    if verdict != Verdict.PASS:
        raise AgentAbortError(
            reason=f"Invalid VERDICT '{verdict.value}' for INIT state (only PASS allowed)",
            suggestion="INIT state only accepts PASS or ABORT. Check reviewer prompt.",
        )

    print(f">>> ✅ Judgment: {Verdict.PASS.value}")
    comment_body = f"## INIT Check Result\n\n{result}"
    ctx.issue_provider.add_comment(comment_body)

    state.completed_states.append("INIT")
    return State.INVESTIGATE
