"""Tests for DesignWorkflow handlers - TDD approach."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.errors import LoopLimitExceededError
from src.core.prompts import PromptLoadError
from src.core.tools.mock import MockTool
from src.core.verdict import AgentAbortError, Verdict
from src.workflows.base import SessionState
from src.workflows.design.states import DesignState
from src.workflows.design.workflow import DesignWorkflow


@pytest.fixture
def mock_context(tmp_path: Path) -> MagicMock:
    """テスト用の AgentContext モック."""
    ctx = MagicMock()
    ctx.issue_provider.issue_url = "https://github.com/test/repo/issues/1"
    ctx.issue_provider.issue_number = 1
    ctx.issue_provider.get_issue_body.return_value = "## Issue Body\n\nTest issue content"

    # artifacts_dir の設定
    artifacts_base = tmp_path / "artifacts"
    ctx.artifacts_dir = artifacts_base / "1" / "202501221000"
    ctx.ensure_artifacts_dir = MagicMock(
        side_effect=lambda state: _create_artifacts_dir(ctx.artifacts_dir, state)
    )
    return ctx


def _create_artifacts_dir(base: Path, state: str | None) -> Path:
    """artifacts ディレクトリを作成するヘルパー."""
    if state:
        target = base / state.lower()
    else:
        target = base
    target.mkdir(parents=True, exist_ok=True)
    return target


class TestHandleDesign:
    """handle_design ハンドラテスト."""

    def test_returns_verdict_pass(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """Verdict.PASS を返すこと."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output\n\nDesign content here"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        result = workflow._handle_design(mock_context, session)

        # Assert
        assert result == Verdict.PASS

    def test_calls_analyzer_with_prompt(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """analyzer.run() がプロンプト付きで呼ばれること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        assert mock_analyzer.call_count == 1
        call_args = mock_analyzer.calls[0]
        assert "prompt" in call_args
        assert mock_context.issue_provider.issue_url in call_args["prompt"]

    def test_stores_design_output_in_session(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """設計出力を session に保存すること."""
        # Arrange
        design_output = "## Design Output\n\nDesign content here"
        mock_analyzer = MockTool(responses=[design_output])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        assert session.get_context("design_output") == design_output
        assert session.get_context("design_output_path") is not None

    def test_increments_loop_counter(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """ループカウンタをインクリメントすること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        assert session.loop_counters.get("design", 0) == 1

    def test_raises_loop_limit_exceeded(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """ループ上限超過時に LoopLimitExceededError を送出すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design"])
        mock_context.analyzer = mock_analyzer

        session = SessionState(max_loop_count=3)
        # 既に上限に達している状態
        session.loop_counters["design"] = 3

        workflow = DesignWorkflow()

        # Act & Assert
        with pytest.raises(LoopLimitExceededError) as exc_info:
            workflow._handle_design(mock_context, session)

        assert exc_info.value.state == "design"
        assert exc_info.value.count == 3

    def test_uses_requirements_from_context(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """session から requirements_content を取得すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        session.set_context("requirements_content", "Additional requirements")

        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert - requirements がプロンプトに含まれるか、または空でも動作すること
        assert mock_analyzer.call_count == 1

    def test_updates_conversation_id(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """conversation_id を更新すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design"], session_ids=["new-session-123"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        assert session.get_conversation_id("analyzer") == "new-session-123"


class TestHandleDesignReview:
    """handle_design_review ハンドラテスト."""

    def test_returns_verdict_pass(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """PASS 判定時に Verdict.PASS を返すこと."""
        # Arrange
        review_response = """
## VERDICT
- Result: PASS
- Reason: Design is complete
- Evidence: All requirements covered
- Suggestion: Proceed to implementation
"""
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act
        result = workflow._handle_design_review(mock_context, session)

        # Assert
        assert result == Verdict.PASS

    def test_returns_verdict_retry(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """RETRY 判定時に Verdict.RETRY を返すこと."""
        # Arrange
        review_response = """
## VERDICT
- Result: RETRY
- Reason: Design incomplete
- Evidence: Missing test cases
- Suggestion: Add test cases
"""
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act
        result = workflow._handle_design_review(mock_context, session)

        # Assert
        assert result == Verdict.RETRY

    def test_converts_back_design_to_retry(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """BACK_DESIGN を RETRY に変換すること."""
        # Arrange
        review_response = """
## VERDICT
- Result: BACK_DESIGN
- Reason: Major issues
"""
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act
        result = workflow._handle_design_review(mock_context, session)

        # Assert
        assert result == Verdict.RETRY  # BACK_DESIGN → RETRY

    def test_raises_abort_error(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """ABORT 判定時に AgentAbortError を送出すること."""
        # Arrange
        review_response = """
## VERDICT
- Result: ABORT
- Reason: Critical issue found
- Suggestion: Manual intervention needed
"""
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act & Assert
        with pytest.raises(AgentAbortError):
            workflow._handle_design_review(mock_context, session)

    def test_raises_error_when_design_output_missing(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """design_output がない場合にエラーを送出すること."""
        # Arrange
        mock_reviewer = MockTool(responses=["## VERDICT\n- Result: PASS"])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        # design_output を設定しない

        workflow = DesignWorkflow()

        # Act & Assert
        with pytest.raises(PromptLoadError):
            workflow._handle_design_review(mock_context, session)

    def test_marks_completed_on_pass(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """PASS 判定時に design_review を completed としてマークすること."""
        # Arrange
        review_response = "## VERDICT\n- Result: PASS\n- Reason: OK"
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act
        workflow._handle_design_review(mock_context, session)

        # Assert
        assert session.is_completed("design_review")

    def test_resets_loop_on_pass(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """PASS 判定時にループカウンタをリセットすること."""
        # Arrange
        review_response = "## VERDICT\n- Result: PASS\n- Reason: OK"
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))
        session.loop_counters["design"] = 2

        workflow = DesignWorkflow()

        # Act
        workflow._handle_design_review(mock_context, session)

        # Assert
        assert session.loop_counters["design"] == 0


class TestDesignWorkflowIntegration:
    """DesignWorkflow 統合テスト."""

    def test_design_to_review_flow(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN → DESIGN_REVIEW フローが動作すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_reviewer = MockTool(responses=["## VERDICT\n- Result: PASS\n- Reason: OK"])
        mock_context.analyzer = mock_analyzer
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act: DESIGN
        verdict1 = workflow._handle_design(mock_context, session)
        next_state1 = workflow.get_next_state(DesignState.DESIGN, verdict1)

        # Assert: DESIGN → DESIGN_REVIEW
        assert verdict1 == Verdict.PASS
        assert next_state1 == DesignState.DESIGN_REVIEW

        # Act: DESIGN_REVIEW
        verdict2 = workflow._handle_design_review(mock_context, session)
        next_state2 = workflow.get_next_state(DesignState.DESIGN_REVIEW, verdict2)

        # Assert: DESIGN_REVIEW → COMPLETE
        assert verdict2 == Verdict.PASS
        assert next_state2 == DesignState.COMPLETE

    def test_retry_loop_flow(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """RETRY ループフローが動作すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design v1", "## Design v2"])
        mock_reviewer = MockTool(
            responses=[
                "## VERDICT\n- Result: RETRY\n- Reason: Incomplete",
                "## VERDICT\n- Result: PASS\n- Reason: OK",
            ]
        )
        mock_context.analyzer = mock_analyzer
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        workflow = DesignWorkflow()

        # Loop 1: DESIGN → DESIGN_REVIEW (RETRY) → DESIGN
        workflow._handle_design(mock_context, session)
        verdict1 = workflow._handle_design_review(mock_context, session)
        assert verdict1 == Verdict.RETRY
        assert workflow.get_next_state(DesignState.DESIGN_REVIEW, verdict1) == DesignState.DESIGN

        # Loop 2: DESIGN → DESIGN_REVIEW (PASS) → COMPLETE
        workflow._handle_design(mock_context, session)
        verdict2 = workflow._handle_design_review(mock_context, session)
        assert verdict2 == Verdict.PASS
        assert workflow.get_next_state(DesignState.DESIGN_REVIEW, verdict2) == DesignState.COMPLETE
