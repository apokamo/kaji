"""AI Tool Protocol definition."""

from pathlib import Path
from typing import Protocol


class AIToolProtocol(Protocol):
    """Protocol for AI tool implementations.

    All AI tools (Claude, Codex, Gemini) must implement this interface.
    """

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Execute the AI tool.

        Args:
            prompt: The prompt to send to the AI
            context: Additional context (file contents, issue body, etc.)
            session_id: Optional session ID for conversation continuity
            log_dir: Optional directory to save execution logs

        Returns:
            Tuple of (response_text, new_session_id)
            new_session_id may be None if session management is not supported
        """
        ...
