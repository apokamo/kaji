"""Issue provider protocol and implementations."""

import subprocess
import time
from typing import Protocol

from src.core.errors import (
    IssueAuthenticationError,
    IssueNotFoundError,
    IssueProviderError,
    IssueRateLimitError,
)
from src.core.url_utils import parse_issue_url


def get_config_value(key: str, default: int | float) -> int | float:
    """Get configuration value.

    TODO: This is a stub that should be replaced with actual config integration.
    For now, returns the default value.

    Args:
        key: Configuration key path (e.g., "github.max_comment_retries").
        default: Default value if key is not found.

    Returns:
        Configuration value or default.
    """
    # Import here to avoid circular imports
    try:
        from src.bugfix_agent.config import get_config_value as ba_get_config_value

        result = ba_get_config_value(key, default)
        # Ensure type consistency
        if isinstance(result, (int, float)):
            return result
        return default
    except ImportError:
        return default


def classify_gh_error(returncode: int, stderr: str, http_status: int | None = None) -> Exception:
    """Classify gh CLI error into appropriate exception.

    Priority order:
    1. HTTP status code (when available via `gh api --include`)
    2. stderr message patterns
    3. CalledProcessError fallback

    Args:
        returncode: Exit code from gh CLI.
        stderr: Error output from gh CLI.
        http_status: HTTP status code if available (from `gh api --include`).

    Returns:
        Appropriate exception instance (NOT raised, just returned).

    Example:
        >>> error = classify_gh_error(1, "rate limit exceeded", http_status=429)
        >>> isinstance(error, IssueRateLimitError)
        True
    """
    stderr_lower = stderr.lower()

    # Priority 1: HTTP status code (most reliable)
    if http_status is not None:
        if http_status == 429:
            return IssueRateLimitError("API rate limit exceeded")
        if http_status == 403:
            if "rate limit" in stderr_lower:
                return IssueRateLimitError("API rate limit exceeded")
            return IssueAuthenticationError("Forbidden")
        if http_status == 401:
            return IssueAuthenticationError("Unauthorized")
        if http_status == 404:
            return IssueNotFoundError("Issue not found")

    # Priority 2: stderr message patterns
    if "rate limit" in stderr_lower:
        return IssueRateLimitError("API rate limit exceeded")
    if (
        "authentication" in stderr_lower
        or "unauthorized" in stderr_lower
        or "gh auth login" in stderr_lower
    ):
        return IssueAuthenticationError("Authentication failed")
    if "not found" in stderr_lower or "could not resolve" in stderr_lower:
        return IssueNotFoundError("Issue not found")

    # Priority 3: CalledProcessError fallback
    return subprocess.CalledProcessError(returncode, "gh", stderr=stderr)


def _is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable.

    Retryable:
    - IssueRateLimitError: Temporary, can succeed after waiting
    - CalledProcessError: Unknown/transient errors (network, etc.)

    NOT retryable:
    - IssueNotFoundError: Permanent, issue doesn't exist
    - IssueAuthenticationError: Config problem, retry won't help
    - ValueError: Input error

    Args:
        error: Exception to check.

    Returns:
        True if the error should be retried, False otherwise.
    """
    if isinstance(error, IssueRateLimitError):
        return True
    if isinstance(error, subprocess.CalledProcessError):
        return True
    return False


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
            IssueProviderError: On API failure.
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


class GitHubIssueProvider:
    """GitHub issue provider implementation.

    Uses the gh CLI to interact with GitHub issues.
    Requires gh to be installed and authenticated.

    Includes retry logic for transient errors (rate limits, network issues).

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
        # Use shared URL validation
        owner, repo, number = parse_issue_url(issue_url)

        self._owner = owner
        self._repo = repo
        self._issue_number = number
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
            error = classify_gh_error(result.returncode, result.stderr)
            raise error

        return result.stdout

    def add_comment(self, body: str) -> None:
        """Add a comment to the issue with retry logic.

        Retries on transient errors (rate limit, network issues).
        Does NOT retry on permanent errors (not found, auth).

        Args:
            body: Comment content.

        Raises:
            IssueRateLimitError: If API rate limit is exceeded after all retries.
            IssueNotFoundError: If the issue doesn't exist.
            IssueAuthenticationError: If gh is not authenticated.
            IssueProviderError: On other API failures.
        """
        max_retries = int(get_config_value("github.max_comment_retries", 2))
        retry_delay = float(get_config_value("github.retry_delay", 1.0))

        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
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

            if result.returncode == 0:
                return  # Success

            error = classify_gh_error(result.returncode, result.stderr)

            # Don't retry permanent errors
            if not _is_retryable_error(error):
                raise error

            last_error = error

            # Retry with delay if not last attempt
            if attempt < max_retries:
                time.sleep(retry_delay)

        # All retries exhausted
        if last_error is not None:
            raise last_error

    def update_body(self, body: str) -> None:
        """Update the issue body.

        Does NOT include retry logic as update conflicts would be problematic.

        Args:
            body: New issue body content.

        Raises:
            IssueNotFoundError: If the issue doesn't exist.
            IssueAuthenticationError: If gh is not authenticated.
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
            error = classify_gh_error(result.returncode, result.stderr)
            # Convert CalledProcessError to IssueProviderError for consistency
            if isinstance(error, subprocess.CalledProcessError):
                raise IssueProviderError(f"GitHub API error: {result.stderr}")
            raise error

    @property
    def issue_number(self) -> int:
        """Get the issue number."""
        return self._issue_number

    @property
    def issue_url(self) -> str:
        """Get the full issue URL."""
        return self._issue_url
