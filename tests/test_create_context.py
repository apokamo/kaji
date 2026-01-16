"""Tests for create_context factory function."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.context import AgentContext, create_context


class TestCreateContextValidUrl:
    """Test create_context URL validation."""

    def test_valid_github_url(self) -> None:
        """Accepts valid GitHub issue URL."""
        with patch("src.core.context.GitHubIssueProvider"):
            ctx = create_context("https://github.com/owner/repo/issues/123")
            assert isinstance(ctx, AgentContext)

    def test_invalid_url_raises_error(self) -> None:
        """Raises ValueError for invalid URLs."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            create_context("https://gitlab.com/owner/repo/issues/1")

    def test_malformed_url_raises_error(self) -> None:
        """Raises ValueError for malformed URLs."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            create_context("not-a-url")


class TestCreateContextDefaultTools:
    """Test create_context default tool configuration."""

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_uses_claude_by_default(self, mock_claude: MagicMock, mock_provider: MagicMock) -> None:
        """Default configuration uses ClaudeTool."""
        ctx = create_context("https://github.com/owner/repo/issues/1")

        # ClaudeTool should be called for analyzer, reviewer, implementer
        assert mock_claude.call_count >= 1
        assert ctx.analyzer is not None
        assert ctx.reviewer is not None
        assert ctx.implementer is not None

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_default_model_is_sonnet(
        self, mock_claude: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Default model for ClaudeTool is sonnet."""
        create_context("https://github.com/owner/repo/issues/1")

        # Check that ClaudeTool was called with model="sonnet"
        calls = mock_claude.call_args_list
        assert any(
            call.kwargs.get("model") == "sonnet" or (call.args and call.args[0] == "sonnet")
            for call in calls
        )


class TestCreateContextToolOverride:
    """Test create_context tool override."""

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_tool_override_claude(self, mock_claude: MagicMock, mock_provider: MagicMock) -> None:
        """tool_override='claude' uses ClaudeTool for all roles."""
        ctx = create_context(
            "https://github.com/owner/repo/issues/1",
            tool_override="claude",
        )

        assert mock_claude.call_count >= 1
        assert ctx.analyzer is not None

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_model_override_with_tool(
        self, mock_claude: MagicMock, mock_provider: MagicMock
    ) -> None:
        """model_override changes the model used."""
        create_context(
            "https://github.com/owner/repo/issues/1",
            tool_override="claude",
            model_override="opus",
        )

        # Check that ClaudeTool was called with model="opus"
        calls = mock_claude.call_args_list
        assert any(
            call.kwargs.get("model") == "opus" or (call.args and "opus" in str(call.args))
            for call in calls
        )

    @patch("src.core.context.GitHubIssueProvider")
    def test_unknown_tool_raises_error(self, mock_provider: MagicMock) -> None:
        """Unknown tool_override raises ValueError."""
        with pytest.raises(ValueError, match="Unknown tool"):
            create_context(
                "https://github.com/owner/repo/issues/1",
                tool_override="unknown_tool",
            )


class TestCreateContextArtifacts:
    """Test create_context artifact configuration."""

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_default_artifacts_base(self, mock_claude: MagicMock, mock_provider: MagicMock) -> None:
        """Default artifacts_base is Path('artifacts')."""
        ctx = create_context("https://github.com/owner/repo/issues/1")
        assert ctx.artifacts_base == Path("artifacts")

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_custom_artifacts_base(self, mock_claude: MagicMock, mock_provider: MagicMock) -> None:
        """Custom artifacts_base is respected."""
        ctx = create_context(
            "https://github.com/owner/repo/issues/1",
            artifacts_base=Path("/custom/artifacts"),
        )
        assert ctx.artifacts_base == Path("/custom/artifacts")


class TestCreateContextIssueProvider:
    """Test create_context issue provider setup."""

    @patch("src.core.context.GitHubIssueProvider")
    @patch("src.core.context.ClaudeTool")
    def test_creates_github_provider(
        self, mock_claude: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Creates GitHubIssueProvider with correct URL."""
        url = "https://github.com/acme/project/issues/42"
        create_context(url)

        mock_provider.assert_called_once_with(url)
