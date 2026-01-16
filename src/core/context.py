"""Agent context for state handlers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.core.providers import GitHubIssueProvider, IssueProvider
from src.core.tools.claude import ClaudeTool
from src.core.tools.protocol import AIToolProtocol

# Supported tools for create_context
_SUPPORTED_TOOLS = {"claude"}
_DEFAULT_MODEL = "sonnet"


@dataclass
class AgentContext:
    """Context passed to state handlers.

    Provides unified access to AI tools, issue provider, and artifact management.
    Enables dependency injection for different tool configurations.

    Attributes:
        analyzer: AI tool for analysis and documentation tasks.
        reviewer: AI tool for review and decision-making tasks.
        implementer: AI tool for implementation tasks.
        issue_provider: Provider for issue system operations.
        artifacts_base: Base path for storing artifacts.
        run_timestamp: Timestamp for this execution run (YYMMDDhhmm format).

    Example:
        >>> from src.core.context import AgentContext
        >>> ctx = AgentContext(
        ...     analyzer=claude_tool,
        ...     reviewer=codex_tool,
        ...     implementer=claude_tool,
        ...     issue_provider=github_provider,
        ... )
        >>> response, session_id = ctx.analyzer.run(prompt, context)
    """

    analyzer: AIToolProtocol
    reviewer: AIToolProtocol
    implementer: AIToolProtocol
    issue_provider: IssueProvider
    artifacts_base: Path = field(default_factory=lambda: Path("artifacts"))
    run_timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%y%m%d%H%M"))

    @property
    def artifacts_dir(self) -> Path:
        """Get the artifacts directory for this execution.

        Returns:
            Path: {artifacts_base}/{issue_number}/{run_timestamp}
        """
        return self.artifacts_base / str(self.issue_provider.issue_number) / self.run_timestamp

    def artifacts_state_dir(self, state: str) -> Path:
        """Get the artifacts directory for a specific state.

        Args:
            state: State name (e.g., "design", "implement").

        Returns:
            Path: {artifacts_dir}/{state} (lowercase)
        """
        return self.artifacts_dir / state.lower()

    def ensure_artifacts_dir(self, state: str | None = None) -> Path:
        """Create and return the artifacts directory.

        Args:
            state: Optional state name. If None, creates the base artifacts dir.

        Returns:
            Path: The created directory path.
        """
        target = self.artifacts_state_dir(state) if state else self.artifacts_dir
        target.mkdir(parents=True, exist_ok=True)
        return target


def create_context(
    issue_url: str,
    tool_override: str | None = None,
    model_override: str | None = None,
    artifacts_base: Path | None = None,
) -> AgentContext:
    """Create a production AgentContext.

    Factory function that creates an AgentContext with production tools
    configured for the specified issue.

    Args:
        issue_url: GitHub Issue URL.
            Format: https://github.com/{owner}/{repo}/issues/{number}
        tool_override: Tool name to use for all roles ("claude").
            If None, uses default configuration.
        model_override: Model name when using tool_override.
        artifacts_base: Base path for artifacts. Defaults to Path("artifacts").

    Returns:
        AgentContext configured with production tools.

    Raises:
        ValueError: If issue_url format is invalid.
        ValueError: If tool_override is unknown.

    Example:
        >>> ctx = create_context("https://github.com/org/repo/issues/42")
        >>> response, session_id = ctx.analyzer.run(prompt, context)

    Default tool configuration:
        - analyzer: ClaudeTool(model="sonnet")
        - reviewer: ClaudeTool(model="sonnet")
        - implementer: ClaudeTool(model="sonnet")
    """
    # Validate and create issue provider
    provider = GitHubIssueProvider(issue_url)

    # Determine tool configuration
    model = model_override or _DEFAULT_MODEL
    tool_name = tool_override or "claude"

    if tool_name not in _SUPPORTED_TOOLS:
        raise ValueError(f"Unknown tool: {tool_name}. Supported: {', '.join(_SUPPORTED_TOOLS)}")

    # Create tools (currently only Claude is supported)
    analyzer = ClaudeTool(model=model)
    reviewer = ClaudeTool(model=model)
    implementer = ClaudeTool(model=model)

    return AgentContext(
        analyzer=analyzer,
        reviewer=reviewer,
        implementer=implementer,
        issue_provider=provider,
        artifacts_base=artifacts_base or Path("artifacts"),
    )
