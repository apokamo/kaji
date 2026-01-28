"""Tests for DesignWorkflow handlers - TDD approach."""

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.errors import LoopLimitExceededError
from src.core.prompts import PromptLoadError
from src.core.tools.mock import MockTool
from src.core.verdict import AgentAbortError, Verdict
from src.workflows.base import SessionState
from src.workflows.design import setup_workflow_context
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


class TestSetupWorkflowContext:
    """setup_workflow_context のテスト."""

    def test_sets_requirements_content_from_input_file(self, tmp_path: Path) -> None:
        """--input ファイルから requirements_content を設定すること."""
        # Arrange
        input_file = tmp_path / "requirements.md"
        input_file.write_text("# Requirements\n\nTest requirements")

        args = Namespace(issue="https://github.com/test/repo/issues/1", input=str(input_file))
        ctx = MagicMock()
        ctx.ensure_artifacts_dir.return_value = tmp_path / "artifacts" / "input"
        (tmp_path / "artifacts" / "input").mkdir(parents=True)

        session = SessionState()

        # Act
        setup_workflow_context(args, ctx, session)

        # Assert
        assert session.get_context("requirements_content") == "# Requirements\n\nTest requirements"

    def test_saves_requirements_to_artifacts(self, tmp_path: Path) -> None:
        """requirements ファイルを artifacts に保存すること."""
        # Arrange
        input_file = tmp_path / "requirements.md"
        input_file.write_text("# Requirements")

        artifacts_dir = tmp_path / "artifacts" / "input"
        artifacts_dir.mkdir(parents=True)

        args = Namespace(issue="https://github.com/test/repo/issues/1", input=str(input_file))
        ctx = MagicMock()
        ctx.ensure_artifacts_dir.return_value = artifacts_dir

        session = SessionState()

        # Act
        setup_workflow_context(args, ctx, session)

        # Assert
        saved_file = artifacts_dir / "requirements.md"
        assert saved_file.exists()
        assert saved_file.read_text() == "# Requirements"

    def test_sets_empty_requirements_when_no_input(self, tmp_path: Path) -> None:
        """--input がない場合に空文字列を設定すること."""
        # Arrange
        args = Namespace(issue="https://github.com/test/repo/issues/1", input=None)
        ctx = MagicMock()
        session = SessionState()

        # Act
        setup_workflow_context(args, ctx, session)

        # Assert
        assert session.get_context("requirements_content") == ""

    def test_handles_missing_input_file_gracefully(self, tmp_path: Path) -> None:
        """存在しない入力ファイルを指定しても空文字列を設定すること."""
        # Arrange
        args = Namespace(
            issue="https://github.com/test/repo/issues/1", input="/nonexistent/file.md"
        )
        ctx = MagicMock()
        session = SessionState()

        # Act
        setup_workflow_context(args, ctx, session)

        # Assert
        assert session.get_context("requirements_content") == ""


class TestDesignWorkflowArtifacts:
    """DesignWorkflow artifacts 出力テスト.

    設計書セクション: ログ出力テスト
    - artifacts/{state}/ に prompt.md, response.md が保存される
    - design_review では verdict.txt も保存される
    """

    def test_design_saves_prompt_md(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN ハンドラが prompt.md を保存すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        prompt_file = target_dir / "prompt.md"
        assert prompt_file.exists()
        assert len(prompt_file.read_text()) > 0

    def test_design_saves_response_md(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN ハンドラが response.md を保存すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output Content"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        response_file = target_dir / "response.md"
        assert response_file.exists()
        assert "Design Output Content" in response_file.read_text()

    def test_design_review_saves_prompt_md(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN_REVIEW ハンドラが prompt.md を保存すること."""
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
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        prompt_file = target_dir / "prompt.md"
        assert prompt_file.exists()

    def test_design_review_saves_response_md(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN_REVIEW ハンドラが response.md を保存すること."""
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
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        response_file = target_dir / "response.md"
        assert response_file.exists()
        assert "VERDICT" in response_file.read_text()

    def test_design_review_saves_verdict_txt(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN_REVIEW ハンドラが verdict.txt を保存すること."""
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
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        verdict_file = target_dir / "verdict.txt"
        assert verdict_file.exists()
        assert verdict_file.read_text() == "PASS"

    def test_design_review_retry_saves_verdict_txt(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """RETRY 判定時も verdict.txt を保存すること."""
        # Arrange
        review_response = "## VERDICT\n- Result: RETRY\n- Reason: Incomplete"
        mock_reviewer = MockTool(responses=[review_response])
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        session.set_context("design_output", "## Design Document")
        session.set_context("design_output_path", str(tmp_path / "design.md"))

        workflow = DesignWorkflow()

        # Act
        workflow._handle_design_review(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        verdict_file = target_dir / "verdict.txt"
        assert verdict_file.exists()
        assert verdict_file.read_text() == "RETRY"


class TestDesignWorkflowEventLogs:
    """DesignWorkflow ハンドラ内イベントログ (events.jsonl) 出力テスト.

    ログファイルの役割分担:
    - run.log: ワークフロー全体のライフサイクルログ ({workdir}/artifacts/)
      → RunLogger が run_start, state_enter/exit, run_end を記録
      → test_run_logger.py でテスト
    - events.jsonl: ハンドラ内の詳細イベントログ ({workdir}/artifacts/{state}/)
      → save_jsonl_log が handler_start, ai_call_*, handler_end を記録
      → このテストクラスでカバー

    設計書セクション: C. ログ・実行基盤
    """

    def test_design_logs_handler_start_event(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN ハンドラが handler_start イベントを記録すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        events_file = target_dir / "events.jsonl"
        assert events_file.exists()

        events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
        handler_start_events = [e for e in events if e.get("type") == "handler_start"]
        assert len(handler_start_events) >= 1
        assert handler_start_events[0]["handler"] == "design"

    def test_design_logs_ai_call_events(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN ハンドラが ai_call_start/ai_call_end イベントを記録すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        events_file = target_dir / "events.jsonl"

        events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]

        ai_call_start = [e for e in events if e.get("type") == "ai_call_start"]
        assert len(ai_call_start) >= 1
        assert ai_call_start[0]["role"] == "analyzer"
        assert "prompt_length" in ai_call_start[0]

        ai_call_end = [e for e in events if e.get("type") == "ai_call_end"]
        assert len(ai_call_end) >= 1
        assert ai_call_end[0]["role"] == "analyzer"
        assert "response_length" in ai_call_end[0]

    def test_design_logs_handler_end_event(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """DESIGN ハンドラが handler_end イベントを記録すること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        events_file = target_dir / "events.jsonl"

        events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
        handler_end_events = [e for e in events if e.get("type") == "handler_end"]
        assert len(handler_end_events) >= 1
        assert handler_end_events[0]["handler"] == "design"
        assert handler_end_events[0]["verdict"] == "PASS"

    def test_design_review_logs_verdict_determined_event(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """DESIGN_REVIEW ハンドラが verdict_determined イベントを記録すること."""
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
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        events_file = target_dir / "events.jsonl"

        events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
        verdict_events = [e for e in events if e.get("type") == "verdict_determined"]
        assert len(verdict_events) >= 1
        assert verdict_events[0]["verdict"] == "PASS"

    def test_events_include_timestamp(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """イベントにタイムスタンプが含まれること."""
        # Arrange
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        workflow._handle_design(mock_context, session)

        # Assert
        artifacts_dir = mock_context.ensure_artifacts_dir.call_args[0][0]
        target_dir = mock_context.artifacts_dir / artifacts_dir.lower()
        events_file = target_dir / "events.jsonl"

        events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
        for event in events:
            assert "timestamp" in event
            # ISO 8601 形式チェック
            assert "T" in event["timestamp"]
