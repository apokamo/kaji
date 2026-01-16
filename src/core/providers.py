"""Issue provider protocol and implementations."""

import re
import subprocess
from typing import Protocol

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueProviderError,
    IssueRateLimitError,
)


class IssueProvider(Protocol):
    """Protocol for issue system operations.

    Abstracts GitHub/GitLab/etc issue systems.
    Uses structural subtyping - no explicit inheritance required.

    Example:
        >>> class MockProvider:
        ...     def get_issue_body(self) -> str:
        ...         return "body"
        ...     def add_comment(self, body: str) -> None:
        ...         pass
        ...     def update_body(self, body: str) -> None:
        ...         pass
        ...     @property
        ...     def issue_number(self) -> int:
        ...         return 1
        ...     @property
        ...     def issue_url(self) -> str:
        ...         return "url"
        >>> provider: IssueProvider = MockProvider()  # Type checks OK
    """

    def get_issue_body(self) -> str:
        """Get the issue body content.

        Returns:
            The full issue body as a string.

        Raises:
            IssueProviderError: On API failure.
        """
        ...

    def add_comment(self, body: str) -> None:
        """Add a comment to the issue.

        Args:
            body: Comment content.

        Raises:
            IssueProviderError: On API failure (after retries if configured).
        """
        ...

    def update_body(self, body: str) -> None:
        """Update the issue body.

        Args:
            body: New issue body content.

        Raises:
            IssueProviderError: On API failure.
        """
        ...

    @property
    def issue_number(self) -> int:
        """Get the issue number.

        Returns:
            Integer issue number.
        """
        ...

    @property
    def issue_url(self) -> str:
        """Get the full issue URL.

        Returns:
            Full URL to the issue.
        """
        ...


# Pattern for GitHub issue URLs
_GITHUB_ISSUE_URL_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$")


class GitHubIssueProvider:
    """GitHub issue provider implementation.

    Uses the gh CLI to interact with GitHub issues.
    Requires gh to be installed and authenticated.

    Example:
        >>> provider = GitHubIssueProvider(
        ...     "https://github.com/owner/repo/issues/123"
        ... )
        >>> body = provider.get_issue_body()
        >>> provider.add_comment("Implementation complete")
    """

    def __init__(self, issue_url: str) -> None:
        """Initialize with a GitHub issue URL.

        Args:
            issue_url: Full GitHub issue URL.
                Format: https://github.com/{owner}/{repo}/issues/{number}

        Raises:
            ValueError: If URL format is invalid.
        """
        match = _GITHUB_ISSUE_URL_PATTERN.match(issue_url)
        if not match:
            raise ValueError(
                f"Invalid GitHub issue URL: {issue_url}. "
                "Expected format: https://github.com/{owner}/{repo}/issues/{number}"
            )

        self._owner = match.group(1)
        self._repo = match.group(2)
        self._issue_number = int(match.group(3))
        self._issue_url = issue_url.rstrip("/")

    def get_issue_body(self) -> str:
        """Get the issue body content.

        Returns:
            The issue body as a string.

        Raises:
            IssueNotFoundError: If the issue doesn't exist.
            IssueAuthenticationError: If gh is not authenticated.
            IssueProviderError: On other API failures.
        """
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(self._issue_number),
                "--repo",
                f"{self._owner}/{self._repo}",
                "--json",
                "body",
                "-q",
                ".body",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            self._handle_error(result.stderr)

        return result.stdout

    def add_comment(self, body: str) -> None:
        """Add a comment to the issue.

        Args:
            body: Comment content.

        Raises:
            IssueRateLimitError: If API rate limit is exceeded.
            IssueProviderError: On other API failures.
        """
        result = subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                str(self._issue_number),
                "--repo",
                f"{self._owner}/{self._repo}",
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            self._handle_error(result.stderr)

    def update_body(self, body: str) -> None:
        """Update the issue body.

        Args:
            body: New issue body content.

        Raises:
            IssueProviderError: On API failure.
        """
        result = subprocess.run(
            [
                "gh",
                "issue",
                "edit",
                str(self._issue_number),
                "--repo",
                f"{self._owner}/{self._repo}",
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            self._handle_error(result.stderr)

    @property
    def issue_number(self) -> int:
        """Get the issue number."""
        return self._issue_number

    @property
    def issue_url(self) -> str:
        """Get the full issue URL."""
        return self._issue_url

    def _handle_error(self, stderr: str) -> None:
        """Handle gh CLI errors by raising appropriate exceptions.

        Args:
            stderr: Error output from gh CLI.

        Raises:
            IssueNotFoundError: If issue not found.
            IssueAuthenticationError: If not authenticated.
            IssueRateLimitError: If rate limited.
            IssueProviderError: For other errors.
        """
        stderr_lower = stderr.lower()

        if "could not resolve to an issue" in stderr_lower:
            raise IssueNotFoundError(f"Issue #{self._issue_number} not found")

        if "gh auth login" in stderr_lower or "authentication" in stderr_lower:
            raise IssueAuthenticationError("GitHub CLI not authenticated")

        if "rate limit" in stderr_lower:
            raise IssueRateLimitError("GitHub API rate limit exceeded")

        raise IssueProviderError(f"GitHub API error: {stderr}")
