"""Tests for URL validation utilities.

These tests verify that URL validation functions work correctly
and are shared between CLI and IssueProvider.
"""

import pytest

from src.core.url_utils import (
    GITHUB_ISSUE_URL_PATTERN,
    is_valid_issue_url,
    parse_issue_url,
)


class TestIsValidIssueUrl:
    """Tests for is_valid_issue_url function."""

    def test_valid_url_without_trailing_slash(self) -> None:
        """Valid GitHub issue URL is accepted."""
        url = "https://github.com/owner/repo/issues/123"
        assert is_valid_issue_url(url) is True

    def test_valid_url_with_trailing_slash(self) -> None:
        """Valid GitHub issue URL with trailing slash is accepted."""
        url = "https://github.com/owner/repo/issues/123/"
        assert is_valid_issue_url(url) is True

    def test_valid_url_complex_owner_repo(self) -> None:
        """URLs with complex owner/repo names are accepted."""
        url = "https://github.com/org-name/repo.name/issues/999"
        assert is_valid_issue_url(url) is True

    def test_invalid_pr_url(self) -> None:
        """PR URL is rejected."""
        url = "https://github.com/owner/repo/pull/123"
        assert is_valid_issue_url(url) is False

    def test_invalid_http_protocol(self) -> None:
        """HTTP (non-HTTPS) URL is rejected."""
        url = "http://github.com/owner/repo/issues/123"
        assert is_valid_issue_url(url) is False

    def test_invalid_no_protocol(self) -> None:
        """URL without protocol is rejected."""
        url = "github.com/owner/repo/issues/123"
        assert is_valid_issue_url(url) is False

    def test_invalid_different_domain(self) -> None:
        """Non-GitHub URL is rejected."""
        url = "https://gitlab.com/owner/repo/issues/123"
        assert is_valid_issue_url(url) is False

    def test_invalid_missing_issue_number(self) -> None:
        """URL without issue number is rejected."""
        url = "https://github.com/owner/repo/issues/"
        assert is_valid_issue_url(url) is False

    def test_invalid_non_numeric_issue(self) -> None:
        """URL with non-numeric issue ID is rejected."""
        url = "https://github.com/owner/repo/issues/abc"
        assert is_valid_issue_url(url) is False

    def test_invalid_empty_string(self) -> None:
        """Empty string is rejected."""
        assert is_valid_issue_url("") is False

    def test_invalid_random_string(self) -> None:
        """Random string is rejected."""
        assert is_valid_issue_url("not a url") is False


class TestParseIssueUrl:
    """Tests for parse_issue_url function."""

    def test_parse_valid_url(self) -> None:
        """Parses owner, repo, and issue number from valid URL."""
        url = "https://github.com/owner/repo/issues/123"
        owner, repo, number = parse_issue_url(url)
        assert owner == "owner"
        assert repo == "repo"
        assert number == 123

    def test_parse_valid_url_with_trailing_slash(self) -> None:
        """Parses URL with trailing slash."""
        url = "https://github.com/owner/repo/issues/456/"
        owner, repo, number = parse_issue_url(url)
        assert owner == "owner"
        assert repo == "repo"
        assert number == 456

    def test_parse_complex_names(self) -> None:
        """Parses URLs with complex owner/repo names."""
        url = "https://github.com/org-name/my.repo/issues/789"
        owner, repo, number = parse_issue_url(url)
        assert owner == "org-name"
        assert repo == "my.repo"
        assert number == 789

    def test_parse_invalid_url_raises_value_error(self) -> None:
        """Invalid URL raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_issue_url("https://github.com/owner/repo/pull/123")
        assert "Invalid GitHub issue URL" in str(exc_info.value)

    def test_parse_invalid_url_includes_expected_format(self) -> None:
        """Error message includes expected format."""
        with pytest.raises(ValueError) as exc_info:
            parse_issue_url("invalid-url")
        assert "Expected format" in str(exc_info.value)
        # Check for owner/repo/number in message (format may vary)
        msg = str(exc_info.value)
        assert "owner" in msg or "{owner}" in msg
        assert "repo" in msg or "{repo}" in msg
        assert "number" in msg or "{number}" in msg or "issues" in msg


class TestPatternExport:
    """Tests for pattern export."""

    def test_pattern_is_compiled_regex(self) -> None:
        """Pattern is a compiled regular expression."""
        import re

        assert isinstance(GITHUB_ISSUE_URL_PATTERN, re.Pattern)

    def test_pattern_matches_valid_url(self) -> None:
        """Pattern matches valid GitHub issue URL."""
        url = "https://github.com/owner/repo/issues/123"
        match = GITHUB_ISSUE_URL_PATTERN.match(url)
        assert match is not None
        assert match.group(1) == "owner"
        assert match.group(2) == "repo"
        assert match.group(3) == "123"
