"""IMPLEMENT state handlers for Bugfix Agent v5

This module provides:
- handle_implement: Create branch, implement fix, run tests
- handle_implement_review: Review implementation results (QA integrated)
"""

from ..agent_context import AgentContext
from ..errors import check_tool_result
from ..prompts import load_prompt
from ..state import SessionState, State
from ..verdict import Verdict, create_ai_formatter, handle_abort_verdict, parse_verdict


def handle_implement(ctx: AgentContext, state: SessionState) -> State:
    """IMPLEMENT: ブランチ作成、実装、テスト実行

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        次のステート (IMPLEMENT_REVIEW)
    """
    print("🔨 Implementing the fix...")

    impl_session = state.active_conversations["Implement_Loop_conversation_id"]
    artifacts_dir = ctx.artifacts_state_dir("implement")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    prompt = load_prompt(
        "implement",
        issue_url=ctx.issue_url,
        issue_number=ctx.issue_number,
        artifacts_dir=artifacts_dir,
    )

    result, new_session = ctx.implementer.run(
        prompt=prompt,
        context=ctx.issue_url,
        session_id=impl_session,
        log_dir=artifacts_dir,
    )
    check_tool_result(result, "implementer")

    if not impl_session and new_session:
        state.active_conversations["Implement_Loop_conversation_id"] = new_session

    state.loop_counters["Implement_Loop"] += 1
    state.completed_states.append("IMPLEMENT")
    return State.IMPLEMENT_REVIEW


def handle_implement_review(ctx: AgentContext, state: SessionState) -> State:
    """IMPLEMENT_REVIEW: 実装結果のレビュー（QA統合版）

    Issue #194 Protocol: QA/QA_REVIEWを統合した新しいレビューステート。
    実装レビューに加え、QA観点での検証も同時に行う。

    Args:
        ctx: Agent コンテキスト
        state: セッション状態

    Returns:
        PASS → PR_CREATE（QA観点も含めて問題なし）
        RETRY → IMPLEMENT (実装修正)
        BACK_DESIGN → DETAIL_DESIGN (設計見直し)

    Raises:
        AgentAbortError: ABORTが返された場合
    """
    print("👀 Reviewing IMPLEMENT results (QA integrated)...")

    log_dir = ctx.artifacts_state_dir("implement_review")
    # Issue #312: 3原則（読まない・渡さない・保存しない）
    # REVIEW ステートは成果物ベースでレビューするため、session_id は不要
    # Implement_Loop_conversation_id へのアクセスは一切行わない

    prompt = load_prompt("implement_review", issue_url=ctx.issue_url)

    decision, _ = ctx.reviewer.run(
        prompt=prompt,
        context=ctx.issue_url,
        session_id=None,
        log_dir=log_dir,
    )
    check_tool_result(decision, "reviewer")

    # VERDICT形式でパース（Issue #292: ハイブリッドフォールバック対応）
    ai_formatter = create_ai_formatter(ctx.reviewer, context=ctx.issue_url, log_dir=log_dir)
    verdict = parse_verdict(decision, ai_formatter=ai_formatter)

    # ABORTの場合は例外を送出（Issue #292 責務分離）
    handle_abort_verdict(verdict, decision)

    if verdict == Verdict.RETRY:
        print(f">>> 🛑 Judgment: {Verdict.RETRY.value} (Re-implement)")
        return State.IMPLEMENT
    elif verdict == Verdict.BACK_DESIGN:
        print(f">>> 🛑 Judgment: {Verdict.BACK_DESIGN.value} (Back to design)")
        return State.DETAIL_DESIGN
    else:
        print(f">>> ✅ Judgment: {Verdict.PASS.value}")
        state.completed_states.append("IMPLEMENT_REVIEW")
        return State.PR_CREATE
