"""Mock issue provider for testing Bugfix Agent v5.

This module provides:
- MockIssueProvider: Local mock for testing (no GitHub API calls)

For production use, import IssueProvider and GitHubIssueProvider
from bugfix_agent.providers.
"""

from pathlib import Path

from bugfix_agent.providers import IssueProvider


class MockIssueProvider(IssueProvider):
    """Local mock implementation for testing.

    Stores issue body and comments in memory, enabling:
    - Fast test execution (no network calls)
    - Test assertions on agent behavior
    - Debug output to local files (optional)

    Example:
        >>> provider = MockIssueProvider(fixture_path)
        >>> orchestrator.run(config, issue_provider=provider)
        >>> assert provider.comment_count >= 1
        >>> assert "Cause Hypothesis" in provider.last_comment
    """

    def __init__(
        self,
        fixture_path: Path | None = None,
        initial_body: str = "",
        issue_number: int = 99999,
        repo_url: str = "https://github.com/test/repo",
        write_debug_files: bool = True,
    ):
        """Initialize the mock provider.

        Args:
            fixture_path: Path to test fixture directory (optional).
                         If provided, reads issue.md from this directory.
            initial_body: Initial issue body content (used if fixture_path is None).
            issue_number: Mock issue number (default: 99999).
            repo_url: Mock repository URL.
            write_debug_files: Whether to write debug files to fixture_path.
        """
        self._fixture_path = fixture_path
        self._write_debug_files = write_debug_files and fixture_path is not None
        self._number = issue_number
        self._repo_url = repo_url
        self._comments: list[str] = []

        # Load initial body
        if fixture_path is not None:
            issue_file = fixture_path / "issue.md"
            if issue_file.exists():
                self._body = issue_file.read_text()
            else:
                self._body = initial_body
        else:
            self._body = initial_body

        self._initial_body = self._body

    def get_issue_body(self) -> str:
        """Get the current issue body."""
        return self._body

    def add_comment(self, body: str) -> None:
        """Add a comment (stored in memory)."""
        self._comments.append(body)

        # Write debug file
        if self._write_debug_files and self._fixture_path is not None:
            comments_dir = self._fixture_path / "test-output" / "comments"
            comments_dir.mkdir(parents=True, exist_ok=True)
            comment_file = comments_dir / f"{len(self._comments):03d}.md"
            comment_file.write_text(body)

    def update_body(self, body: str) -> None:
        """Update the issue body (stored in memory)."""
        self._body = body

        # Write debug file
        if self._write_debug_files and self._fixture_path is not None:
            output_dir = self._fixture_path / "test-output"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "updated_body.md").write_text(body)

    @property
    def issue_number(self) -> int:
        """Get the mock issue number."""
        return self._number

    @property
    def issue_url(self) -> str:
        """Get the mock issue URL."""
        return f"{self._repo_url}/issues/{self._number}"

    # ========== Test Assertion Helpers ==========

    @property
    def comments(self) -> list[str]:
        """Get all comments (copy for safety).

        Returns:
            List of all comments added to this issue.
        """
        return self._comments.copy()

    @property
    def last_comment(self) -> str | None:
        """Get the last comment added.

        Returns:
            The last comment, or None if no comments exist.
        """
        return self._comments[-1] if self._comments else None

    @property
    def comment_count(self) -> int:
        """Get the number of comments.

        Returns:
            Total number of comments added.
        """
        return len(self._comments)

    def get_comment(self, index: int) -> str | None:
        """Get a comment by index.

        Args:
            index: 0-based index of the comment.

        Returns:
            The comment at the given index, or None if out of range.
        """
        if 0 <= index < len(self._comments):
            return self._comments[index]
        return None

    def has_comment_containing(self, text: str) -> bool:
        """Check if any comment contains the given text.

        Args:
            text: Text to search for in comments.

        Returns:
            True if any comment contains the text.
        """
        return any(text in comment for comment in self._comments)

    def clear(self) -> None:
        """Reset to initial state (for test isolation)."""
        self._comments.clear()
        self._body = self._initial_body
