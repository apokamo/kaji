"""INVESTIGATE state handlers for Bugfix Agent v5

This module provides:
- handle_investigate: Execute reproduction, analyze differences, formulate hypothesis
- handle_investigate_review: Review investigation results
"""

from ..agent_context import AgentContext
from ..errors import AgentAbortError, check_tool_result
from ..prompts import load_prompt
from ..state import SessionState, State
from ..verdict import Verdict, create_ai_formatter, handle_abort_verdict, parse_verdict


def handle_investigate(ctx: AgentContext, state: SessionState) -> State:
    """INVESTIGATE: 再現実行、期待値との差分、原因仮説

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        次のステート (INVESTIGATE_REVIEW)
    """
    print("🔍 Investigating the issue...")

    design_session = state.active_conversations["Design_Thread_conversation_id"]
    artifacts_dir = ctx.artifacts_state_dir("investigate")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    prompt = load_prompt(
        "investigate",
        issue_url=ctx.issue_url,
        artifacts_dir=artifacts_dir,
    )

    result, new_session = ctx.analyzer.run(
        prompt=prompt,
        context=ctx.issue_url,
        session_id=design_session,
        log_dir=artifacts_dir,
    )
    check_tool_result(result, "analyzer")

    if not design_session and new_session:
        state.active_conversations["Design_Thread_conversation_id"] = new_session

    state.loop_counters["Investigate_Loop"] += 1
    state.completed_states.append("INVESTIGATE")
    return State.INVESTIGATE_REVIEW


def handle_investigate_review(ctx: AgentContext, state: SessionState) -> State:
    """INVESTIGATE_REVIEW: 調査結果のレビュー

    Issue #194 Protocol: VERDICT形式で判定結果を出力。

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        PASS → DETAIL_DESIGN
        RETRY → INVESTIGATE

    Raises:
        AgentAbortError: ABORTが返された場合
    """
    print("👀 Reviewing INVESTIGATE results...")

    log_dir = ctx.artifacts_state_dir("investigate_review")
    prompt = load_prompt("investigate_review", issue_url=ctx.issue_url)

    decision, _ = ctx.reviewer.run(prompt=prompt, context=ctx.issue_url, log_dir=log_dir)
    check_tool_result(decision, "reviewer")

    # VERDICT形式でパース（Issue #292: ハイブリッドフォールバック対応）
    ai_formatter = create_ai_formatter(ctx.reviewer, context=ctx.issue_url, log_dir=log_dir)
    verdict = parse_verdict(decision, ai_formatter=ai_formatter)

    # ABORTの場合は例外を送出（Issue #292 責務分離）
    handle_abort_verdict(verdict, decision)

    # INVESTIGATE_REVIEWではBACK_DESIGNは使用不可（Issue #194 VERDICT対応表）
    if verdict == Verdict.BACK_DESIGN:
        raise AgentAbortError(
            reason=f"Invalid VERDICT '{verdict.value}' for INVESTIGATE_REVIEW (BACK_DESIGN not allowed)",
            suggestion="INVESTIGATE_REVIEW only accepts PASS, RETRY, or ABORT. Check reviewer prompt.",
        )

    if verdict == Verdict.RETRY:
        print(f">>> 🛑 Judgment: {Verdict.RETRY.value} (Re-investigate)")
        return State.INVESTIGATE

    print(f">>> ✅ Judgment: {Verdict.PASS.value}")
    state.completed_states.append("INVESTIGATE_REVIEW")
    return State.DETAIL_DESIGN
