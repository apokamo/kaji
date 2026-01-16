"""Tests for IssueProvider protocol and errors."""

import pytest

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueProviderError,
    IssueRateLimitError,
)
from src.core.providers import IssueProvider


class TestIssueProviderErrors:
    """Test IssueProvider error hierarchy."""

    def test_base_error_is_exception(self) -> None:
        """IssueProviderError is an Exception."""
        error = IssueProviderError("base error")
        assert isinstance(error, Exception)
        assert str(error) == "base error"

    def test_not_found_inherits_from_base(self) -> None:
        """IssueNotFoundError inherits from IssueProviderError."""
        error = IssueNotFoundError("issue #123 not found")
        assert isinstance(error, IssueProviderError)
        assert isinstance(error, Exception)

    def test_authentication_inherits_from_base(self) -> None:
        """IssueAuthenticationError inherits from IssueProviderError."""
        error = IssueAuthenticationError("gh not authenticated")
        assert isinstance(error, IssueProviderError)

    def test_rate_limit_inherits_from_base(self) -> None:
        """IssueRateLimitError inherits from IssueProviderError."""
        error = IssueRateLimitError("rate limit exceeded")
        assert isinstance(error, IssueProviderError)

    def test_errors_can_be_caught_by_base(self) -> None:
        """All specific errors can be caught by base type."""
        errors = [
            IssueNotFoundError("not found"),
            IssueAuthenticationError("auth error"),
            IssueRateLimitError("rate limit"),
        ]
        for error in errors:
            with pytest.raises(IssueProviderError):
                raise error


class MockIssueProvider:
    """Mock implementation of IssueProvider for testing."""

    def __init__(
        self,
        issue_number: int = 42,
        issue_url: str = "https://github.com/owner/repo/issues/42",
        body: str = "test body",
    ) -> None:
        self._issue_number = issue_number
        self._issue_url = issue_url
        self._body = body
        self.comments: list[str] = []

    def get_issue_body(self) -> str:
        return self._body

    def add_comment(self, body: str) -> None:
        self.comments.append(body)

    def update_body(self, body: str) -> None:
        self._body = body

    @property
    def issue_number(self) -> int:
        return self._issue_number

    @property
    def issue_url(self) -> str:
        return self._issue_url


class TestIssueProviderProtocol:
    """Test IssueProvider protocol compliance."""

    def test_mock_implements_protocol(self) -> None:
        """MockIssueProvider satisfies IssueProvider protocol."""
        mock = MockIssueProvider()
        # Structural subtyping check
        provider: IssueProvider = mock
        assert provider is mock

    def test_get_issue_body(self) -> None:
        """get_issue_body returns issue body."""
        mock = MockIssueProvider(body="## Summary\nTest issue")
        assert mock.get_issue_body() == "## Summary\nTest issue"

    def test_add_comment(self) -> None:
        """add_comment appends to comments list."""
        mock = MockIssueProvider()
        mock.add_comment("First comment")
        mock.add_comment("Second comment")
        assert mock.comments == ["First comment", "Second comment"]

    def test_update_body(self) -> None:
        """update_body changes the issue body."""
        mock = MockIssueProvider(body="original")
        mock.update_body("updated")
        assert mock.get_issue_body() == "updated"

    def test_issue_number_property(self) -> None:
        """issue_number returns the issue number."""
        mock = MockIssueProvider(issue_number=123)
        assert mock.issue_number == 123

    def test_issue_url_property(self) -> None:
        """issue_url returns the full issue URL."""
        url = "https://github.com/acme/project/issues/999"
        mock = MockIssueProvider(issue_url=url)
        assert mock.issue_url == url

    def test_protocol_type_checking(self) -> None:
        """Protocol can be used for type annotation."""

        def process_issue(provider: IssueProvider) -> str:
            return provider.get_issue_body()

        mock = MockIssueProvider(body="test")
        result = process_issue(mock)
        assert result == "test"
