"""Mock tool for testing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any


class MockTool:
    """Mock tool for testing.

    Returns pre-configured responses in order. Tracks all calls for verification.

    Attributes:
        call_count: Number of times run() was called.
        calls: List of call arguments (each as dict with prompt, context, etc.).
    """

    def __init__(
        self,
        responses: list[str],
        session_ids: list[str] | None = None,
    ) -> None:
        """Initialize mock tool.

        Args:
            responses: List of responses to return (consumed in order).
            session_ids: Optional list of session IDs to return.
                        If None, auto-generates session IDs.
        """
        self._responses: Iterator[str] = iter(responses)
        self._session_ids: Iterator[str] | None = iter(session_ids) if session_ids else None
        self._session_counter = 0
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Return configured response and track call.

        Args:
            prompt: Prompt text (recorded for verification).
            context: Context (recorded for verification).
            session_id: Preserved if provided, otherwise from session_ids or auto-generated.
            log_dir: Log directory (recorded for verification).

        Returns:
            Tuple of (response, session_id).
        """
        # Track this call
        self.call_count += 1
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "session_id": session_id,
                "log_dir": log_dir,
            }
        )

        # Get response
        response = next(self._responses, "MOCK_RESPONSE")

        # Get session ID
        if self._session_ids is not None:
            new_session = next(self._session_ids, None)
        else:
            self._session_counter += 1
            new_session = session_id or f"mock-session-{self._session_counter}"

        return response, new_session
