"""Pytest configuration and fixtures for Bugfix Agent v5 tests."""

from pathlib import Path

import pytest

from bugfix_agent.agent_context import AgentContext
from bugfix_agent.providers import IssueProvider
from bugfix_agent.state import SessionState
from bugfix_agent.tools import MockTool
from tests.utils.providers import MockIssueProvider

# ==========================================
# Fixture Paths
# ==========================================

FIXTURES_DIR = Path(__file__).parent.parent / "test-fixtures" / "bugfix-agent-e2e"
L1_SIMPLE_DIR = FIXTURES_DIR / "L1-simple"


# ==========================================
# Issue Provider Fixtures
# ==========================================


@pytest.fixture
def mock_issue_provider() -> MockIssueProvider:
    """Simple MockIssueProvider with no initial body.

    Use this for tests that don't need a specific issue fixture.

    Example:
        def test_handler_posts_comment(mock_issue_provider):
            ctx = create_context_with_provider(mock_issue_provider)
            handle_init(ctx, SessionState())
            assert mock_issue_provider.comment_count >= 1
    """
    return MockIssueProvider(initial_body="# Test Issue\n\nThis is a test issue.")


@pytest.fixture
def l1_001_issue_provider() -> MockIssueProvider:
    """MockIssueProvider initialized with L1-001 type-error fixture.

    Loads issue.md from test-fixtures/bugfix-agent-e2e/L1-simple/001-type-error/
    """
    fixture_path = L1_SIMPLE_DIR / "001-type-error"
    return MockIssueProvider(fixture_path=fixture_path)


# ==========================================
# Context Fixtures
# ==========================================


@pytest.fixture
def mock_context(mock_issue_provider: MockIssueProvider) -> AgentContext:
    """AgentContext with MockTool and MockIssueProvider.

    All AI tool responses need to be provided via MockTool's responses.

    Example:
        def test_handler(mock_context):
            mock_context.reviewer._responses = ["## VERDICT\\n- Result: PASS"]
            handle_init(mock_context, SessionState())
    """
    return AgentContext(
        analyzer=MockTool([]),
        reviewer=MockTool([]),
        implementer=MockTool([]),
        issue_url=mock_issue_provider.issue_url,
        issue_number=mock_issue_provider.issue_number,
        issue_provider=mock_issue_provider,
        run_timestamp="2512151200",
    )


# ==========================================
# Session State Fixtures
# ==========================================


@pytest.fixture
def session_state() -> SessionState:
    """Fresh SessionState for each test."""
    return SessionState()


# ==========================================
# Helper Functions (available as module-level)
# ==========================================


def create_context_with_responses(
    provider: IssueProvider,
    reviewer_responses: list[str] | None = None,
    analyzer_responses: list[str] | None = None,
    implementer_responses: list[str] | None = None,
) -> AgentContext:
    """Create AgentContext with specified AI tool responses.

    Args:
        provider: IssueProvider to use
        reviewer_responses: Responses for Codex (review operations)
        analyzer_responses: Responses for Gemini (analysis operations)
        implementer_responses: Responses for Claude (implementation operations)

    Returns:
        AgentContext configured for testing

    Example:
        provider = MockIssueProvider(initial_body="...")
        ctx = create_context_with_responses(
            provider,
            reviewer_responses=["## VERDICT\\n- Result: PASS"],
        )
    """
    return AgentContext(
        analyzer=MockTool(analyzer_responses or []),
        reviewer=MockTool(reviewer_responses or []),
        implementer=MockTool(implementer_responses or []),
        issue_url=provider.issue_url,
        issue_number=provider.issue_number,
        issue_provider=provider,
        run_timestamp="2512151200",
    )
