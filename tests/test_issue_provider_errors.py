"""Tests for IssueProvider error classification.

These tests verify that gh CLI errors are correctly classified
using HTTP status codes (preferred) or stderr messages (fallback).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueRateLimitError,
)
from src.core.providers import GitHubIssueProvider, classify_gh_error


class TestClassifyGhError:
    """Tests for classify_gh_error function."""

    # HTTP Status Code Tests (Priority 1)

    def test_http_429_returns_rate_limit_error(self) -> None:
        """HTTP 429 status code returns RateLimitError."""
        error = classify_gh_error(returncode=1, stderr="", http_status=429)
        assert isinstance(error, IssueRateLimitError)
        assert "rate limit" in str(error).lower()

    def test_http_403_with_rate_limit_returns_rate_limit_error(self) -> None:
        """HTTP 403 with 'rate limit' in body returns RateLimitError."""
        error = classify_gh_error(returncode=1, stderr="rate limit exceeded", http_status=403)
        assert isinstance(error, IssueRateLimitError)

    def test_http_403_without_rate_limit_returns_auth_error(self) -> None:
        """HTTP 403 without 'rate limit' returns AuthenticationError."""
        error = classify_gh_error(returncode=1, stderr="forbidden", http_status=403)
        assert isinstance(error, IssueAuthenticationError)
        assert "forbidden" in str(error).lower()

    def test_http_401_returns_auth_error(self) -> None:
        """HTTP 401 status code returns AuthenticationError."""
        error = classify_gh_error(returncode=1, stderr="", http_status=401)
        assert isinstance(error, IssueAuthenticationError)
        assert "unauthorized" in str(error).lower()

    def test_http_404_returns_not_found_error(self) -> None:
        """HTTP 404 status code returns NotFoundError."""
        error = classify_gh_error(returncode=1, stderr="", http_status=404)
        assert isinstance(error, IssueNotFoundError)
        assert "not found" in str(error).lower()

    # Stderr Fallback Tests (Priority 2)

    def test_stderr_rate_limit_returns_rate_limit_error(self) -> None:
        """Stderr with 'rate limit' returns RateLimitError."""
        error = classify_gh_error(returncode=1, stderr="API rate limit exceeded", http_status=None)
        assert isinstance(error, IssueRateLimitError)

    def test_stderr_authentication_returns_auth_error(self) -> None:
        """Stderr with 'authentication' returns AuthenticationError."""
        error = classify_gh_error(returncode=1, stderr="authentication required", http_status=None)
        assert isinstance(error, IssueAuthenticationError)

    def test_stderr_unauthorized_returns_auth_error(self) -> None:
        """Stderr with 'unauthorized' returns AuthenticationError."""
        error = classify_gh_error(returncode=1, stderr="unauthorized access", http_status=None)
        assert isinstance(error, IssueAuthenticationError)

    def test_stderr_not_found_returns_not_found_error(self) -> None:
        """Stderr with 'not found' returns NotFoundError."""
        error = classify_gh_error(returncode=1, stderr="issue not found", http_status=None)
        assert isinstance(error, IssueNotFoundError)

    def test_stderr_could_not_resolve_returns_not_found_error(self) -> None:
        """Stderr with 'could not resolve' returns NotFoundError."""
        error = classify_gh_error(
            returncode=1, stderr="Could not resolve to an Issue", http_status=None
        )
        assert isinstance(error, IssueNotFoundError)

    def test_stderr_case_insensitive(self) -> None:
        """Stderr matching is case insensitive."""
        error = classify_gh_error(returncode=1, stderr="RATE LIMIT exceeded", http_status=None)
        assert isinstance(error, IssueRateLimitError)

    # Fallback to CalledProcessError (Priority 3)

    def test_unknown_error_returns_called_process_error(self) -> None:
        """Unknown error returns CalledProcessError."""
        error = classify_gh_error(returncode=1, stderr="some unknown error", http_status=None)
        assert isinstance(error, subprocess.CalledProcessError)
        assert error.returncode == 1

    def test_http_status_priority_over_stderr(self) -> None:
        """HTTP status takes priority over stderr content."""
        # HTTP 404 should win over "rate limit" in stderr
        error = classify_gh_error(returncode=1, stderr="rate limit exceeded", http_status=404)
        assert isinstance(error, IssueNotFoundError)

    # Edge Cases

    def test_empty_stderr_with_no_status(self) -> None:
        """Empty stderr with no HTTP status returns CalledProcessError."""
        error = classify_gh_error(returncode=1, stderr="", http_status=None)
        assert isinstance(error, subprocess.CalledProcessError)

    def test_gh_auth_login_message(self) -> None:
        """Stderr with 'gh auth login' returns AuthenticationError."""
        error = classify_gh_error(
            returncode=1,
            stderr="To authenticate, please run `gh auth login`",
            http_status=None,
        )
        assert isinstance(error, IssueAuthenticationError)


class TestGitHubIssueProviderErrorClassification:
    """Tests for GitHubIssueProvider error classification integration."""

    @pytest.fixture
    def provider(self) -> GitHubIssueProvider:
        """Create a provider for testing."""
        return GitHubIssueProvider("https://github.com/owner/repo/issues/123")

    def test_get_issue_body_not_found(self, provider: GitHubIssueProvider) -> None:
        """get_issue_body raises NotFoundError on 404."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Could not resolve to an Issue"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IssueNotFoundError):
                provider.get_issue_body()

    def test_get_issue_body_auth_error(self, provider: GitHubIssueProvider) -> None:
        """get_issue_body raises AuthenticationError on auth failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "To authenticate, please run `gh auth login`"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IssueAuthenticationError):
                provider.get_issue_body()

    def test_get_issue_body_rate_limit(self, provider: GitHubIssueProvider) -> None:
        """get_issue_body raises RateLimitError on rate limit."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "API rate limit exceeded"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IssueRateLimitError):
                provider.get_issue_body()
