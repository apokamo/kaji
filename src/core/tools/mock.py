"""Mock tool for testing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class MockTool:
    """Mock tool for testing.

    Returns pre-configured responses in order. Generates session IDs automatically.
    """

    def __init__(self, responses: list[str]) -> None:
        """Initialize mock tool.

        Args:
            responses: List of responses to return (consumed in order)
        """
        self._responses: Iterator[str] = iter(responses)
        self._session_counter = 0

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Return configured response.

        Args:
            prompt: Ignored
            context: Ignored
            session_id: Preserved if provided, otherwise auto-generated
            log_dir: Ignored

        Returns:
            Tuple of (response, session_id)
        """
        del prompt, context, log_dir  # Interface compatibility
        response = next(self._responses, "MOCK_RESPONSE")
        self._session_counter += 1
        new_session = session_id or f"mock-session-{self._session_counter}"
        return response, new_session
