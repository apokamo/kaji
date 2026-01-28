"""Tests for GitHubIssueProvider."""

from unittest.mock import MagicMock, patch

import pytest

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueProviderError,
    IssueRateLimitError,
)
from src.core.providers import GitHubIssueProvider, IssueProvider


class TestGitHubIssueProviderInit:
    """Test GitHubIssueProvider initialization."""

    def test_valid_url(self) -> None:
        """Accepts valid GitHub issue URL."""
        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/123")
        assert provider.issue_number == 123
        assert provider.issue_url == "https://github.com/owner/repo/issues/123"

    def test_valid_url_trailing_slash(self) -> None:
        """Accepts URL with trailing slash."""
        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/456/")
        assert provider.issue_number == 456

    def test_extracts_owner_and_repo(self) -> None:
        """Extracts owner and repo from URL."""
        provider = GitHubIssueProvider("https://github.com/acme-corp/my-project/issues/789")
        assert provider._owner == "acme-corp"
        assert provider._repo == "my-project"

    def test_invalid_url_not_github(self) -> None:
        """Rejects non-GitHub URLs."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueProvider("https://gitlab.com/owner/repo/issues/1")

    def test_invalid_url_not_issues(self) -> None:
        """Rejects URLs that aren't issue pages."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueProvider("https://github.com/owner/repo/pull/1")

    def test_invalid_url_no_number(self) -> None:
        """Rejects URLs without issue number."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueProvider("https://github.com/owner/repo/issues/")

    def test_invalid_url_non_numeric(self) -> None:
        """Rejects URLs with non-numeric issue identifier."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueProvider("https://github.com/owner/repo/issues/abc")

    def test_invalid_url_negative_number(self) -> None:
        """Rejects URLs with negative issue number."""
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueProvider("https://github.com/owner/repo/issues/-1")

    def test_implements_protocol(self) -> None:
        """GitHubIssueProvider satisfies IssueProvider protocol."""
        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")
        # Structural subtyping check
        _: IssueProvider = provider


class TestGitHubIssueProviderGetIssueBody:
    """Test GitHubIssueProvider.get_issue_body.

    Note: get_issue_body uses `gh api --include` for HTTP status-based
    error classification. The response format is:
        HTTP/2.0 200 OK
        Header: Value
        ...
        (blank line)
        {"body": "..."}
    """

    @patch("src.core.providers.subprocess.run")
    def test_returns_issue_body(self, mock_run: MagicMock) -> None:
        """Returns issue body from gh API."""
        # Simulate gh api --include response format
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "HTTP/2.0 200 OK\nContent-Type: application/json\n\n"
            '{"body": "## Summary\\nThis is the issue body"}'
        )
        mock_run.return_value.stderr = ""

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/123")
        body = provider.get_issue_body()

        assert body == "## Summary\nThis is the issue body"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "gh" in call_args[0][0]
        assert "api" in call_args[0][0]
        assert "--include" in call_args[0][0]

    @patch("src.core.providers.subprocess.run")
    def test_not_found_error(self, mock_run: MagicMock) -> None:
        """Raises IssueNotFoundError when issue doesn't exist (HTTP 404)."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            'HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n{"message": "Not Found"}'
        )
        mock_run.return_value.stderr = ""

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/999")

        with pytest.raises(IssueNotFoundError):
            provider.get_issue_body()

    @patch("src.core.providers.subprocess.run")
    def test_auth_error(self, mock_run: MagicMock) -> None:
        """Raises IssueAuthenticationError when not authenticated (HTTP 401)."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            "HTTP/2.0 401 Unauthorized\nContent-Type: application/json\n\n"
            '{"message": "Requires authentication"}'
        )
        mock_run.return_value.stderr = ""

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")

        with pytest.raises(IssueAuthenticationError):
            provider.get_issue_body()

    @patch("src.core.providers.subprocess.run")
    def test_rate_limit_error_http_429(self, mock_run: MagicMock) -> None:
        """Raises IssueRateLimitError on HTTP 429."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            "HTTP/2.0 429 Too Many Requests\nContent-Type: application/json\n\n"
            '{"message": "API rate limit exceeded"}'
        )
        mock_run.return_value.stderr = ""

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")

        with pytest.raises(IssueRateLimitError):
            provider.get_issue_body()

    @patch("src.core.providers.subprocess.run")
    def test_rate_limit_error_http_403_with_body(self, mock_run: MagicMock) -> None:
        """Raises IssueRateLimitError on HTTP 403 with rate limit in body."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            "HTTP/2.0 403 Forbidden\nContent-Type: application/json\n\n"
            '{"message": "API rate limit exceeded for user"}'
        )
        mock_run.return_value.stderr = ""

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")

        with pytest.raises(IssueRateLimitError):
            provider.get_issue_body()


class TestGitHubIssueProviderAddComment:
    """Test GitHubIssueProvider.add_comment."""

    @patch("src.core.providers.subprocess.run")
    def test_adds_comment_successfully(self, mock_run: MagicMock) -> None:
        """Adds comment via gh CLI."""
        mock_run.return_value.returncode = 0

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/123")
        provider.add_comment("Test comment body")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "gh" in call_args[0][0]
        assert "issue" in call_args[0][0]
        assert "comment" in call_args[0][0]

    @patch("src.core.providers.subprocess.run")
    def test_rate_limit_error(self, mock_run: MagicMock) -> None:
        """Raises IssueRateLimitError on rate limit."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "API rate limit exceeded"

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")

        with pytest.raises(IssueRateLimitError):
            provider.add_comment("test")


class TestGitHubIssueProviderUpdateBody:
    """Test GitHubIssueProvider.update_body."""

    @patch("src.core.providers.subprocess.run")
    def test_updates_body_successfully(self, mock_run: MagicMock) -> None:
        """Updates issue body via gh CLI."""
        mock_run.return_value.returncode = 0

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/123")
        provider.update_body("New body content")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "gh" in call_args[0][0]
        assert "issue" in call_args[0][0]
        assert "edit" in call_args[0][0]

    @patch("src.core.providers.subprocess.run")
    def test_generic_error(self, mock_run: MagicMock) -> None:
        """Raises IssueProviderError on unknown error."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Unknown error occurred"

        provider = GitHubIssueProvider("https://github.com/owner/repo/issues/1")

        with pytest.raises(IssueProviderError):
            provider.update_body("test")
