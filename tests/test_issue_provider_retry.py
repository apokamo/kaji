"""Tests for IssueProvider retry logic.

These tests verify that retry logic works correctly:
- RateLimitError triggers retry
- NotFoundError and AuthenticationError do NOT trigger retry
- Retry count and delay are respected
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueRateLimitError,
)
from src.core.providers import GitHubIssueProvider


class TestRetryOnRateLimit:
    """Tests for retry on RateLimitError."""

    @pytest.fixture
    def provider(self) -> GitHubIssueProvider:
        """Create a provider for testing."""
        return GitHubIssueProvider("https://github.com/owner/repo/issues/123")

    def test_add_comment_retries_on_rate_limit(self, provider: GitHubIssueProvider) -> None:
        """add_comment retries on RateLimitError."""
        # First two calls fail with rate limit, third succeeds
        mock_results = [
            MagicMock(returncode=1, stderr="API rate limit exceeded", stdout=""),
            MagicMock(returncode=1, stderr="API rate limit exceeded", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]

        with patch("subprocess.run", side_effect=mock_results) as mock_run:
            with patch("time.sleep") as mock_sleep:
                provider.add_comment("test comment")

        # Should have been called 3 times (2 retries + 1 success)
        assert mock_run.call_count == 3
        # Should have slept twice (between retries)
        assert mock_sleep.call_count == 2

    def test_add_comment_respects_max_retries(self, provider: GitHubIssueProvider) -> None:
        """add_comment respects max retry count."""
        mock_result = MagicMock(returncode=1, stderr="API rate limit exceeded", stdout="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep"):
                with patch(
                    "src.core.providers.get_config_value",
                    side_effect=lambda key, default: 2 if "max" in key else 1.0,
                ):
                    with pytest.raises(IssueRateLimitError):
                        provider.add_comment("test comment")

        # max_retries=2 means 3 total attempts (initial + 2 retries)
        assert mock_run.call_count == 3

    def test_add_comment_uses_configured_delay(self, provider: GitHubIssueProvider) -> None:
        """add_comment uses configured retry delay."""
        mock_result = MagicMock(returncode=1, stderr="API rate limit exceeded", stdout="")

        with patch("subprocess.run", return_value=mock_result):
            with patch("time.sleep") as mock_sleep:
                with patch(
                    "src.core.providers.get_config_value",
                    side_effect=lambda key, default: 2 if "max" in key else 2.5,
                ):
                    with pytest.raises(IssueRateLimitError):
                        provider.add_comment("test comment")

        # Verify sleep was called with configured delay
        mock_sleep.assert_called_with(2.5)


class TestNoRetryOnPermanentErrors:
    """Tests that permanent errors do NOT trigger retry."""

    @pytest.fixture
    def provider(self) -> GitHubIssueProvider:
        """Create a provider for testing."""
        return GitHubIssueProvider("https://github.com/owner/repo/issues/123")

    def test_add_comment_no_retry_on_not_found(self, provider: GitHubIssueProvider) -> None:
        """add_comment does NOT retry on NotFoundError."""
        mock_result = MagicMock(returncode=1, stderr="issue not found", stdout="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(IssueNotFoundError):
                    provider.add_comment("test comment")

        # Should only be called once (no retry)
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    def test_add_comment_no_retry_on_auth_error(self, provider: GitHubIssueProvider) -> None:
        """add_comment does NOT retry on AuthenticationError."""
        mock_result = MagicMock(returncode=1, stderr="authentication required", stdout="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(IssueAuthenticationError):
                    provider.add_comment("test comment")

        # Should only be called once (no retry)
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    def test_update_body_no_retry_on_not_found(self, provider: GitHubIssueProvider) -> None:
        """update_body does NOT retry on NotFoundError."""
        mock_result = MagicMock(returncode=1, stderr="issue not found", stdout="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(IssueNotFoundError):
                    provider.update_body("new body")

        # Should only be called once (no retry)
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()


class TestRetryOnTransientErrors:
    """Tests for retry on transient/unknown errors."""

    @pytest.fixture
    def provider(self) -> GitHubIssueProvider:
        """Create a provider for testing."""
        return GitHubIssueProvider("https://github.com/owner/repo/issues/123")

    def test_add_comment_retries_on_unknown_error(self, provider: GitHubIssueProvider) -> None:
        """add_comment retries on unknown CalledProcessError."""
        mock_results = [
            MagicMock(returncode=1, stderr="network timeout", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]

        with patch("subprocess.run", side_effect=mock_results) as mock_run:
            with patch("time.sleep"):
                provider.add_comment("test comment")

        # Should have been called 2 times (1 retry + 1 success)
        assert mock_run.call_count == 2


class TestRetryConfiguration:
    """Tests for retry configuration from config."""

    @pytest.fixture
    def provider(self) -> GitHubIssueProvider:
        """Create a provider for testing."""
        return GitHubIssueProvider("https://github.com/owner/repo/issues/123")

    def test_uses_config_max_retries(self, provider: GitHubIssueProvider) -> None:
        """Uses max_comment_retries from config."""
        mock_result = MagicMock(returncode=1, stderr="API rate limit exceeded", stdout="")

        def mock_config(key: str, default: float | int) -> float | int:
            if "max_comment_retries" in key:
                return 5  # Custom retry count
            return default

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep"):
                with patch("src.core.providers.get_config_value", mock_config):
                    with pytest.raises(IssueRateLimitError):
                        provider.add_comment("test comment")

        # max_retries=5 means 6 total attempts
        assert mock_run.call_count == 6

    def test_uses_config_retry_delay(self, provider: GitHubIssueProvider) -> None:
        """Uses retry_delay from config."""
        mock_results = [
            MagicMock(returncode=1, stderr="API rate limit exceeded", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]

        def mock_config(key: str, default: float | int) -> float | int:
            if "retry_delay" in key:
                return 3.0  # Custom delay
            return default

        with patch("subprocess.run", side_effect=mock_results):
            with patch("time.sleep") as mock_sleep:
                with patch("src.core.providers.get_config_value", mock_config):
                    provider.add_comment("test comment")

        mock_sleep.assert_called_once_with(3.0)

    def test_default_max_retries_is_2(self, provider: GitHubIssueProvider) -> None:
        """Default max_comment_retries is 2."""
        mock_result = MagicMock(returncode=1, stderr="API rate limit exceeded", stdout="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep"):
                with pytest.raises(IssueRateLimitError):
                    provider.add_comment("test comment")

        # Default max_retries=2 means 3 total attempts
        assert mock_run.call_count == 3

    def test_default_retry_delay_is_1(self, provider: GitHubIssueProvider) -> None:
        """Default retry_delay is 1.0 second."""
        mock_results = [
            MagicMock(returncode=1, stderr="API rate limit exceeded", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]

        with patch("subprocess.run", side_effect=mock_results):
            with patch("time.sleep") as mock_sleep:
                provider.add_comment("test comment")

        mock_sleep.assert_called_once_with(1.0)
