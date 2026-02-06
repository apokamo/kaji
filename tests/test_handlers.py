"""Tests for handler functions.

Issue #312: IMPLEMENT_REVIEW session_id handling tests.
"""

from unittest.mock import MagicMock

import pytest

from bugfix_agent.agent_context import AgentContext
from bugfix_agent.handlers.implement import handle_implement_review
from bugfix_agent.state import SessionState
from tests.utils.providers import MockIssueProvider


class TestImplementReviewSessionHandling:
    """Issue #312: IMPLEMENT_REVIEW が session_id を使わないことを検証。

    3原則: 読まない・渡さない・保存しない
    """

    @pytest.fixture
    def mock_reviewer(self) -> MagicMock:
        """MagicMock reviewer for spy verification."""
        mock = MagicMock()
        # decision を strict にして fallback が走らない前提
        mock.run.return_value = (
            "## VERDICT\n- Result: PASS\n- Reason: All checks passed\n- Evidence: OK",
            "codex-thread-123",
        )
        return mock

    @pytest.fixture
    def context_with_mock_reviewer(self, mock_reviewer: MagicMock) -> AgentContext:
        """AgentContext with MagicMock reviewer for spy verification."""
        provider = MockIssueProvider(initial_body="# Test Issue")
        return AgentContext(
            analyzer=MagicMock(),
            reviewer=mock_reviewer,
            implementer=MagicMock(),
            issue_url=provider.issue_url,
            issue_number=provider.issue_number,
            issue_provider=provider,
            run_timestamp="2512181200",
        )

    def test_implement_review_calls_reviewer_with_session_id_none(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """IMPLEMENT_REVIEW が session_id=None で reviewer を呼ぶことを検証。

        spy検証: 最初の呼び出しで session_id=None であること。
        """
        state = SessionState()
        # 既存の Claude セッションが存在する状況を再現
        state.active_conversations["Implement_Loop_conversation_id"] = "existing-claude-session"

        handle_implement_review(context_with_mock_reviewer, state)

        # spy検証: 最初の呼び出しで session_id=None であること（fallback時も壊れない）
        assert mock_reviewer.run.call_count >= 1
        first_call_kwargs = mock_reviewer.run.call_args_list[0].kwargs
        assert first_call_kwargs.get("session_id") is None

    def test_implement_review_does_not_update_implement_loop_conversation_id(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """IMPLEMENT_REVIEW が Implement_Loop_conversation_id を更新しないことを検証。

        不変条件: 既存の Claude セッションIDが変更されない。
        """
        state = SessionState()
        original_session = "existing-claude-session"
        state.active_conversations["Implement_Loop_conversation_id"] = original_session

        handle_implement_review(context_with_mock_reviewer, state)

        # 不変条件: Implement_Loop_conversation_id が変更されていないこと
        assert state.active_conversations["Implement_Loop_conversation_id"] == original_session

    def test_implement_review_does_not_save_codex_session_to_implement_loop(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """IMPLEMENT_REVIEW が Codex の session_id を Implement_Loop に保存しないことを検証。

        逆方向クロスツール事故の防止: Codex の thread_id が保存されない。
        """
        state = SessionState()
        # Implement_Loop_conversation_id が None の状況（初回実行）
        state.active_conversations["Implement_Loop_conversation_id"] = None

        handle_implement_review(context_with_mock_reviewer, state)

        # 保存しない: Codex の session_id (codex-thread-123) が保存されていないこと
        assert state.active_conversations["Implement_Loop_conversation_id"] is None

    def test_implement_review_returns_correct_state_on_pass(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """PASS 時に PR_CREATE に遷移することを検証。"""
        from bugfix_agent.state import State

        state = SessionState()

        result = handle_implement_review(context_with_mock_reviewer, state)

        assert result == State.PR_CREATE

    def test_implement_review_returns_implement_on_retry(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """RETRY 時に IMPLEMENT に遷移することを検証。"""
        from bugfix_agent.state import State

        mock_reviewer.run.return_value = (
            "## VERDICT\n- Result: RETRY\n- Reason: Fix needed\n- Suggestion: Fix X",
            "codex-thread-456",
        )
        state = SessionState()

        result = handle_implement_review(context_with_mock_reviewer, state)

        assert result == State.IMPLEMENT

    def test_implement_review_returns_detail_design_on_back_design(
        self, context_with_mock_reviewer: AgentContext, mock_reviewer: MagicMock
    ):
        """BACK_DESIGN 時に DETAIL_DESIGN に遷移することを検証。"""
        from bugfix_agent.state import State

        mock_reviewer.run.return_value = (
            "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Design issue\n- Suggestion: Redesign Y",
            "codex-thread-789",
        )
        state = SessionState()

        result = handle_implement_review(context_with_mock_reviewer, state)

        assert result == State.DETAIL_DESIGN
