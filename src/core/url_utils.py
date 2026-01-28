"""URL validation utilities for GitHub Issue URLs.

This module provides shared URL validation functions used by both
CLI and IssueProvider to ensure consistent URL acceptance criteria.

Example:
    >>> from src.core.url_utils import is_valid_issue_url, parse_issue_url
    >>> is_valid_issue_url("https://github.com/owner/repo/issues/123")
    True
    >>> owner, repo, number = parse_issue_url("https://github.com/owner/repo/issues/123")
    >>> print(f"{owner}/{repo}#{number}")
    owner/repo#123
"""

import re

# Shared pattern for GitHub Issue URLs (single source of truth)
GITHUB_ISSUE_URL_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$")


def is_valid_issue_url(url: str) -> bool:
    """Check if URL is a valid GitHub Issue URL.

    This function is used by both IssueProvider and CLI to ensure
    consistent URL acceptance criteria.

    Args:
        url: URL string to validate.

    Returns:
        True if the URL is a valid GitHub Issue URL, False otherwise.

    Example:
        >>> is_valid_issue_url("https://github.com/owner/repo/issues/123")
        True
        >>> is_valid_issue_url("https://github.com/owner/repo/pull/123")
        False
    """
    return GITHUB_ISSUE_URL_PATTERN.match(url) is not None


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse GitHub Issue URL into owner, repo, and issue number.

    Args:
        url: GitHub Issue URL to parse.

    Returns:
        Tuple of (owner, repo, issue_number).

    Raises:
        ValueError: If URL format is invalid.

    Example:
        >>> owner, repo, number = parse_issue_url("https://github.com/owner/repo/issues/123")
        >>> print(owner, repo, number)
        owner repo 123
    """
    match = GITHUB_ISSUE_URL_PATTERN.match(url)
    if not match:
        raise ValueError(
            f"Invalid GitHub issue URL: {url}. "
            f"Expected format: https://github.com/{{owner}}/{{repo}}/issues/{{number}}"
        )
    return match.group(1), match.group(2), int(match.group(3))
