"""Issue provider abstraction for Bugfix Agent v5.

This module provides:
- IssueProvider: Abstract base class for issue operations
- GitHubIssueProvider: Real GitHub API implementation (production)

For testing, use MockIssueProvider from tests.utils.providers.
"""

import subprocess
import time
from abc import ABC, abstractmethod

from .config import get_config_value


class IssueProvider(ABC):
    """Abstract base class for issue operations.

    This interface abstracts GitHub API access, enabling:
    - Local testing without GitHub API calls
    - Offline development
    - Faster test execution
    """

    @abstractmethod
    def get_issue_body(self) -> str:
        """Get the issue body content."""
        pass

    @abstractmethod
    def add_comment(self, body: str) -> None:
        """Add a comment to the issue."""
        pass

    @abstractmethod
    def update_body(self, body: str) -> None:
        """Update the issue body."""
        pass

    @property
    @abstractmethod
    def issue_number(self) -> int:
        """Get the issue number."""
        pass

    @property
    @abstractmethod
    def issue_url(self) -> str:
        """Get the issue URL."""
        pass


class GitHubIssueProvider(IssueProvider):
    """Real GitHub API implementation using gh CLI.

    Used in production to interact with actual GitHub issues.
    Includes retry logic for resilience.
    """

    def __init__(self, issue_url: str):
        """Initialize with a GitHub issue URL.

        Args:
            issue_url: Full GitHub issue URL
                      (e.g., https://github.com/owner/repo/issues/123)
        """
        self._url = issue_url
        self._number = int(issue_url.rstrip("/").split("/")[-1])

    def get_issue_body(self) -> str:
        """Get the issue body from GitHub."""
        result = subprocess.run(
            ["gh", "issue", "view", str(self._number), "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def add_comment(self, body: str) -> None:
        """Add a comment to the GitHub issue with retry logic."""
        max_retries = get_config_value("github.max_comment_retries", 2)
        retry_delay = get_config_value("github.retry_delay", 1.0)

        for attempt in range(max_retries + 1):
            try:
                subprocess.run(
                    ["gh", "issue", "comment", str(self._number), "--body", body],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                print(f"✅ Posted comment to Issue #{self._number}")
                return
            except subprocess.CalledProcessError as e:
                error_msg = (
                    f"Failed to post comment (attempt {attempt + 1}/{max_retries + 1}): {e.stderr}"
                )
                print(f"❌ {error_msg}")
                if attempt < max_retries:
                    time.sleep(retry_delay)

        print(f"⚠️ All retries exhausted for Issue #{self._number} comment")

    def update_body(self, body: str) -> None:
        """Update the GitHub issue body."""
        subprocess.run(
            ["gh", "issue", "edit", str(self._number), "--body", body],
            check=True,
        )

    @property
    def issue_number(self) -> int:
        """Get the issue number."""
        return self._number

    @property
    def issue_url(self) -> str:
        """Get the issue URL."""
        return self._url
