"""Tests for IssueProvider abstraction.

These tests verify:
1. MockIssueProvider stores comments correctly
2. MockIssueProvider assertion helpers work
3. Handler integration with IssueProvider
"""

import pytest

from bugfix_agent.agent_context import AgentContext
from bugfix_agent.errors import AgentAbortError
from bugfix_agent.handlers.init import handle_init
from bugfix_agent.state import SessionState
from bugfix_agent.tools import MockTool
from tests.utils.providers import MockIssueProvider


class TestMockIssueProvider:
    """Tests for MockIssueProvider functionality."""

    def test_initial_body_from_string(self):
        """MockIssueProvider can be initialized with a string body."""
        provider = MockIssueProvider(initial_body="# Test Issue")
        assert provider.get_issue_body() == "# Test Issue"

    def test_add_comment_stores_in_memory(self):
        """add_comment stores comments in memory."""
        provider = MockIssueProvider(initial_body="")
        provider.add_comment("First comment")
        provider.add_comment("Second comment")

        assert provider.comment_count == 2
        assert provider.comments == ["First comment", "Second comment"]

    def test_last_comment(self):
        """last_comment returns the most recent comment."""
        provider = MockIssueProvider(initial_body="")
        assert provider.last_comment is None

        provider.add_comment("First")
        assert provider.last_comment == "First"

        provider.add_comment("Second")
        assert provider.last_comment == "Second"

    def test_get_comment_by_index(self):
        """get_comment returns comment at given index."""
        provider = MockIssueProvider(initial_body="")
        provider.add_comment("Zero")
        provider.add_comment("One")
        provider.add_comment("Two")

        assert provider.get_comment(0) == "Zero"
        assert provider.get_comment(1) == "One"
        assert provider.get_comment(2) == "Two"
        assert provider.get_comment(3) is None
        assert provider.get_comment(-1) is None

    def test_has_comment_containing(self):
        """has_comment_containing searches all comments."""
        provider = MockIssueProvider(initial_body="")
        provider.add_comment("This contains VERDICT")
        provider.add_comment("Another comment")

        assert provider.has_comment_containing("VERDICT") is True
        assert provider.has_comment_containing("Another") is True
        assert provider.has_comment_containing("NotFound") is False

    def test_update_body(self):
        """update_body changes the issue body."""
        provider = MockIssueProvider(initial_body="Original")
        provider.update_body("Updated")
        assert provider.get_issue_body() == "Updated"

    def test_clear_resets_state(self):
        """clear resets comments and body to initial state."""
        provider = MockIssueProvider(initial_body="Original")
        provider.add_comment("Comment")
        provider.update_body("Changed")

        provider.clear()

        assert provider.comment_count == 0
        assert provider.get_issue_body() == "Original"

    def test_issue_url_and_number(self):
        """issue_url and issue_number return expected values."""
        provider = MockIssueProvider(
            initial_body="",
            issue_number=123,
            repo_url="https://github.com/test/repo",
        )
        assert provider.issue_number == 123
        assert provider.issue_url == "https://github.com/test/repo/issues/123"


class TestHandlerWithIssueProvider:
    """Tests for handlers using IssueProvider."""

    def _create_context(
        self,
        provider: MockIssueProvider,
        reviewer_responses: list[str],
    ) -> AgentContext:
        """Create AgentContext with MockIssueProvider."""
        return AgentContext(
            analyzer=MockTool([]),
            reviewer=MockTool(reviewer_responses),
            implementer=MockTool([]),
            issue_url=provider.issue_url,
            issue_number=provider.issue_number,
            issue_provider=provider,
            run_timestamp="2512151200",
        )

    def test_handle_init_pass_posts_comment(self):
        """handle_init posts comment via issue_provider on PASS."""
        provider = MockIssueProvider(initial_body="# Test Issue")
        ctx = self._create_context(
            provider,
            reviewer_responses=["## VERDICT\n- Result: PASS\n- Reason: All requirements met"],
        )
        state = SessionState()

        handle_init(ctx, state)

        assert provider.comment_count == 1
        assert "INIT Check Result" in provider.last_comment
        assert "PASS" in provider.last_comment

    def test_handle_init_abort_posts_comment(self):
        """handle_init posts comment via issue_provider on ABORT."""
        provider = MockIssueProvider(initial_body="# Test Issue")
        ctx = self._create_context(
            provider,
            reviewer_responses=[
                "## VERDICT\n- Result: ABORT\n- Reason: Missing info\n- Suggestion: Add details"
            ],
        )
        state = SessionState()

        with pytest.raises(AgentAbortError):
            handle_init(ctx, state)

        assert provider.comment_count == 1
        assert "INIT Check Result" in provider.last_comment
        assert "ABORT" in provider.last_comment

    def test_handler_does_not_call_github_api(self):
        """Handler uses issue_provider instead of direct GitHub API calls."""
        provider = MockIssueProvider(initial_body="# Test Issue")
        ctx = self._create_context(
            provider,
            reviewer_responses=["## VERDICT\n- Result: PASS\n- Reason: OK"],
        )
        state = SessionState()

        # This should not raise any subprocess errors
        # because we're using MockIssueProvider instead of GitHubIssueProvider
        handle_init(ctx, state)

        # Verify the comment was captured by MockIssueProvider
        assert provider.comment_count == 1
