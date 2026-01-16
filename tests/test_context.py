"""Tests for AgentContext."""

from pathlib import Path

import pytest

from src.core.context import AgentContext
from src.core.providers import IssueProvider
from src.core.tools.protocol import AIToolProtocol


class MockTool:
    """Mock AI tool implementation."""

    def __init__(self, response: str = "mock response") -> None:
        self._response = response
        self.calls: list[dict] = []

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "session_id": session_id,
                "log_dir": log_dir,
            }
        )
        return self._response, "session-123"


class MockIssueProvider:
    """Mock issue provider implementation."""

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


class TestAgentContextCreation:
    """Test AgentContext instantiation."""

    def test_create_with_all_fields(self) -> None:
        """AgentContext can be created with all required fields."""
        analyzer = MockTool()
        reviewer = MockTool()
        implementer = MockTool()
        provider = MockIssueProvider()

        ctx = AgentContext(
            analyzer=analyzer,
            reviewer=reviewer,
            implementer=implementer,
            issue_provider=provider,
        )

        assert ctx.analyzer is analyzer
        assert ctx.reviewer is reviewer
        assert ctx.implementer is implementer
        assert ctx.issue_provider is provider

    def test_different_tools_per_role(self) -> None:
        """Different tools can be assigned to different roles."""
        analyzer = MockTool(response="analysis")
        reviewer = MockTool(response="review")
        implementer = MockTool(response="implementation")
        provider = MockIssueProvider()

        ctx = AgentContext(
            analyzer=analyzer,
            reviewer=reviewer,
            implementer=implementer,
            issue_provider=provider,
        )

        response_a, _ = ctx.analyzer.run("analyze")
        response_r, _ = ctx.reviewer.run("review")
        response_i, _ = ctx.implementer.run("implement")

        assert response_a == "analysis"
        assert response_r == "review"
        assert response_i == "implementation"

    def test_missing_required_field_raises_error(self) -> None:
        """TypeError raised when required fields are missing."""
        with pytest.raises(TypeError):
            AgentContext()  # type: ignore


class TestAgentContextArtifacts:
    """Test AgentContext artifact management."""

    def test_artifacts_dir_default(self) -> None:
        """artifacts_dir uses default base path."""
        provider = MockIssueProvider(issue_number=123)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
        )

        expected = Path("artifacts") / "123" / ctx.run_timestamp
        assert ctx.artifacts_dir == expected

    def test_artifacts_dir_custom_base(self) -> None:
        """artifacts_dir respects custom base path."""
        provider = MockIssueProvider(issue_number=456)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
            artifacts_base=Path("/custom/path"),
        )

        expected = Path("/custom/path") / "456" / ctx.run_timestamp
        assert ctx.artifacts_dir == expected

    def test_artifacts_state_dir(self) -> None:
        """artifacts_state_dir creates correct path."""
        provider = MockIssueProvider(issue_number=789)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
        )

        design_dir = ctx.artifacts_state_dir("design")
        assert design_dir == ctx.artifacts_dir / "design"

    def test_artifacts_state_dir_lowercases(self) -> None:
        """artifacts_state_dir converts state name to lowercase."""
        provider = MockIssueProvider(issue_number=1)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
        )

        design_dir = ctx.artifacts_state_dir("DESIGN")
        assert design_dir == ctx.artifacts_dir / "design"

        impl_dir = ctx.artifacts_state_dir("ImPlEmEnT")
        assert impl_dir == ctx.artifacts_dir / "implement"

    def test_ensure_artifacts_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_artifacts_dir creates the directory."""
        provider = MockIssueProvider(issue_number=100)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
            artifacts_base=tmp_path / "artifacts",
        )

        result = ctx.ensure_artifacts_dir()

        assert result.exists()
        assert result.is_dir()
        assert result == ctx.artifacts_dir

    def test_ensure_artifacts_dir_with_state(self, tmp_path: Path) -> None:
        """ensure_artifacts_dir creates state subdirectory."""
        provider = MockIssueProvider(issue_number=200)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
            artifacts_base=tmp_path / "artifacts",
        )

        result = ctx.ensure_artifacts_dir("review")

        assert result.exists()
        assert result.is_dir()
        assert result == ctx.artifacts_state_dir("review")

    def test_ensure_artifacts_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_artifacts_dir is idempotent."""
        provider = MockIssueProvider(issue_number=300)
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
            artifacts_base=tmp_path / "artifacts",
        )

        result1 = ctx.ensure_artifacts_dir()
        result2 = ctx.ensure_artifacts_dir()

        assert result1 == result2
        assert result1.exists()


class TestAgentContextTimestamp:
    """Test AgentContext timestamp behavior."""

    def test_run_timestamp_format(self) -> None:
        """run_timestamp follows YYMMDDhhmm format."""
        provider = MockIssueProvider()
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
        )

        # Format: YYMMDDhhmm (10 characters)
        assert len(ctx.run_timestamp) == 10
        assert ctx.run_timestamp.isdigit()

    def test_run_timestamp_custom(self) -> None:
        """run_timestamp can be set explicitly."""
        provider = MockIssueProvider()
        ctx = AgentContext(
            analyzer=MockTool(),
            reviewer=MockTool(),
            implementer=MockTool(),
            issue_provider=provider,
            run_timestamp="2401151230",
        )

        assert ctx.run_timestamp == "2401151230"


class TestAgentContextTypeChecking:
    """Test AgentContext type compatibility."""

    def test_tools_match_protocol(self) -> None:
        """Tool fields accept AIToolProtocol implementations."""
        provider = MockIssueProvider()
        tool: AIToolProtocol = MockTool()

        ctx = AgentContext(
            analyzer=tool,
            reviewer=tool,
            implementer=tool,
            issue_provider=provider,
        )

        assert ctx.analyzer is tool

    def test_provider_matches_protocol(self) -> None:
        """issue_provider accepts IssueProvider implementations."""
        mock_provider: IssueProvider = MockIssueProvider()
        tool = MockTool()

        ctx = AgentContext(
            analyzer=tool,
            reviewer=tool,
            implementer=tool,
            issue_provider=mock_provider,
        )

        assert ctx.issue_provider is mock_provider
