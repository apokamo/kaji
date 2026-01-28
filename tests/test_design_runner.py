"""Tests for DesignWorkflow runner."""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.tools.mock import MockTool
from src.workflows.base import SessionState
from src.workflows.design.runner import _run_workflow_loop, run_design_workflow
from src.workflows.design.workflow import DesignWorkflow


@pytest.fixture
def mock_context(tmp_path: Path) -> MagicMock:
    """テスト用の AgentContext モック."""
    ctx = MagicMock()
    ctx.issue_provider.issue_url = "https://github.com/test/repo/issues/1"
    ctx.issue_provider.issue_number = 1
    ctx.issue_provider.get_issue_body.return_value = "## Issue Body\n\nTest content"
    ctx.issue_provider.add_comment = MagicMock()

    # artifacts_dir の設定
    artifacts_base = tmp_path / "artifacts"
    ctx.artifacts_dir = artifacts_base / "1" / "202501221000"

    def create_artifacts_dir(state: str | None = None) -> Path:
        if state:
            target = ctx.artifacts_dir / state.lower()
        else:
            target = ctx.artifacts_dir
        target.mkdir(parents=True, exist_ok=True)
        return target

    ctx.ensure_artifacts_dir = MagicMock(side_effect=create_artifacts_dir)
    return ctx


class TestRunWorkflowLoop:
    """_run_workflow_loop 関数のテスト."""

    def test_completes_successfully_on_pass(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """PASS で正常終了すること."""
        # Arrange: Design -> PASS -> Review -> PASS -> Complete
        mock_analyzer = MockTool(responses=["## Design Output\n\nDesign content"])
        mock_reviewer = MockTool(responses=["## VERDICT\n- Result: PASS\n- Reason: LGTM"])
        mock_context.analyzer = mock_analyzer
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        result = _run_workflow_loop(workflow, mock_context, session)

        # Assert
        assert result == 0
        assert mock_analyzer.call_count == 1
        assert mock_reviewer.call_count == 1
        mock_context.issue_provider.add_comment.assert_called()

    def test_retries_on_retry_verdict(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """RETRY で再ループすること."""
        # Arrange: Design -> Review (RETRY) -> Design -> Review (PASS) -> Complete
        mock_analyzer = MockTool(
            responses=[
                "## Design v1\n\nInitial design",
                "## Design v2\n\nImproved design",
            ]
        )
        mock_reviewer = MockTool(
            responses=[
                "## VERDICT\n- Result: RETRY\n- Reason: Needs improvement",
                "## VERDICT\n- Result: PASS\n- Reason: LGTM",
            ]
        )
        mock_context.analyzer = mock_analyzer
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        result = _run_workflow_loop(workflow, mock_context, session)

        # Assert
        assert result == 0
        assert mock_analyzer.call_count == 2
        assert mock_reviewer.call_count == 2

    def test_reports_error_on_loop_limit(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """ループ上限超過でエラー報告すること."""
        # Arrange: session already at loop limit
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState(max_loop_count=1)
        session.loop_counters["design"] = 1  # Already at limit

        workflow = DesignWorkflow()

        # Act
        result = _run_workflow_loop(workflow, mock_context, session)

        # Assert
        assert result == 1
        # Error should be reported to issue
        mock_context.issue_provider.add_comment.assert_called()
        call_args = mock_context.issue_provider.add_comment.call_args[0][0]
        assert "エラー" in call_args


class TestRunDesignWorkflow:
    """run_design_workflow 関数のテスト."""

    def test_returns_error_on_invalid_issue_url(self) -> None:
        """無効なIssue URLでエラーを返すこと."""
        # Arrange
        args = Namespace(issue="invalid-url", input=None)

        # Act
        result = run_design_workflow(args)

        # Assert
        assert result == 1

    @patch("src.workflows.design.runner.GitHubIssueProvider")
    @patch("src.workflows.design.runner.ClaudeTool")
    @patch("src.workflows.design.runner._run_workflow_loop")
    @patch("src.workflows.design.runner.setup_workflow_context")
    def test_calls_workflow_loop_with_context(
        self,
        mock_setup_context: MagicMock,
        mock_run_loop: MagicMock,
        mock_claude: MagicMock,
        mock_provider: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ワークフローループが正しいコンテキストで呼ばれること."""
        # Arrange
        mock_run_loop.return_value = 0
        mock_provider.return_value.issue_number = 1

        args = Namespace(
            issue="https://github.com/test/repo/issues/1",
            input=None,
        )

        # Act
        result = run_design_workflow(args)

        # Assert
        assert result == 0
        mock_setup_context.assert_called_once()
        mock_run_loop.assert_called_once()
        call_args = mock_run_loop.call_args
        assert isinstance(call_args[0][0], DesignWorkflow)
        assert isinstance(call_args[0][2], SessionState)

    @patch("src.workflows.design.runner.GitHubIssueProvider")
    @patch("src.workflows.design.runner.ClaudeTool")
    @patch("src.workflows.design.runner._run_workflow_loop")
    @patch("src.workflows.design.runner.AgentContext")
    def test_loads_input_file_when_provided(
        self,
        mock_agent_context: MagicMock,
        mock_run_loop: MagicMock,
        mock_claude: MagicMock,
        mock_provider: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--input オプションでファイルを読み込むこと."""
        # Arrange
        mock_run_loop.return_value = 0
        mock_provider.return_value.issue_number = 1

        # Mock AgentContext to use tmp_path for artifacts
        mock_ctx = MagicMock()
        mock_ctx.ensure_artifacts_dir.return_value = tmp_path / "artifacts" / "input"
        mock_agent_context.return_value = mock_ctx

        # Create input file
        input_file = tmp_path / "requirements.md"
        input_file.write_text("## Requirements\n\nTest requirements")

        args = Namespace(
            issue="https://github.com/test/repo/issues/1",
            input=str(input_file),
        )

        # Act
        result = run_design_workflow(args)

        # Assert
        assert result == 0
        mock_run_loop.assert_called_once()
        # Session should have requirements content
        session = mock_run_loop.call_args[0][2]
        assert session.get_context("requirements_content") == "## Requirements\n\nTest requirements"

    @patch("src.workflows.design.runner.GitHubIssueProvider")
    @patch("src.workflows.design.runner.ClaudeTool")
    @patch("src.workflows.design.runner._run_workflow_loop")
    @patch("src.workflows.design.runner.AgentContext")
    def test_workdir_affects_artifacts_base(
        self,
        mock_agent_context: MagicMock,
        mock_run_loop: MagicMock,
        mock_claude: MagicMock,
        mock_provider: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--workdir オプションが artifacts_base に反映されること."""
        # Arrange
        mock_run_loop.return_value = 0
        mock_provider.return_value.issue_number = 1

        workdir = tmp_path / "custom_workdir"
        args = Namespace(
            issue="https://github.com/test/repo/issues/1",
            input=None,
            workdir=str(workdir),
            dry_run=False,
            verbose=False,
        )

        # Act
        result = run_design_workflow(args)

        # Assert
        assert result == 0
        # AgentContext should be initialized with workdir/artifacts as base
        call_kwargs = mock_agent_context.call_args[1]
        assert call_kwargs["artifacts_base"] == workdir / "artifacts"

    @patch("src.workflows.design.runner.GitHubIssueProvider")
    @patch("src.workflows.design.runner.ClaudeTool")
    @patch("src.workflows.design.runner._run_workflow_loop")
    @patch("src.workflows.design.runner.AgentContext")
    def test_dry_run_passed_to_workflow_loop(
        self,
        mock_agent_context: MagicMock,
        mock_run_loop: MagicMock,
        mock_claude: MagicMock,
        mock_provider: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--dry-run オプションが workflow loop に渡されること."""
        # Arrange
        mock_run_loop.return_value = 0
        mock_provider.return_value.issue_number = 1

        args = Namespace(
            issue="https://github.com/test/repo/issues/1",
            input=None,
            workdir=None,
            dry_run=True,
            verbose=False,
        )

        # Act
        result = run_design_workflow(args)

        # Assert
        assert result == 0
        # dry_run should be passed as keyword argument
        call_kwargs = mock_run_loop.call_args[1]
        assert call_kwargs.get("dry_run") is True


class TestDryRunBehavior:
    """--dry-run option behavior tests."""

    def test_dry_run_skips_success_comment(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """--dry-run で成功時の Issue コメントがスキップされること."""
        # Arrange: Design -> PASS -> Review -> PASS -> Complete
        mock_analyzer = MockTool(responses=["## Design Output\n\nDesign content"])
        mock_reviewer = MockTool(responses=["## VERDICT\n- Result: PASS\n- Reason: LGTM"])
        mock_context.analyzer = mock_analyzer
        mock_context.reviewer = mock_reviewer

        session = SessionState()
        workflow = DesignWorkflow()

        # Act
        result = _run_workflow_loop(workflow, mock_context, session, dry_run=True)

        # Assert
        assert result == 0
        # Issue comment should NOT be called in dry-run mode
        mock_context.issue_provider.add_comment.assert_not_called()

    def test_dry_run_skips_error_comment(self, mock_context: MagicMock, tmp_path: Path) -> None:
        """--dry-run でエラー時の Issue コメントがスキップされること."""
        # Arrange: session already at loop limit
        mock_analyzer = MockTool(responses=["## Design Output"])
        mock_context.analyzer = mock_analyzer

        session = SessionState(max_loop_count=1)
        session.loop_counters["design"] = 1  # Already at limit

        workflow = DesignWorkflow()

        # Act
        result = _run_workflow_loop(workflow, mock_context, session, dry_run=True)

        # Assert
        assert result == 1
        # Issue comment should NOT be called in dry-run mode
        mock_context.issue_provider.add_comment.assert_not_called()
