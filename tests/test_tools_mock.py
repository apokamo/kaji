"""Tests for MockTool."""

from pathlib import Path

from src.core.tools.mock import MockTool
from src.core.tools.protocol import AIToolProtocol


class TestMockTool:
    """Tests for MockTool."""

    def test_implements_protocol(self) -> None:
        """MockTool implements AIToolProtocol."""
        tool = MockTool(["response"])
        # Type check: MockTool should be compatible with AIToolProtocol
        _: AIToolProtocol = tool

    def test_returns_responses_in_order(self) -> None:
        """Responses are returned in order."""
        tool = MockTool(["first", "second", "third"])
        response1, _ = tool.run("prompt1")
        response2, _ = tool.run("prompt2")
        response3, _ = tool.run("prompt3")
        assert response1 == "first"
        assert response2 == "second"
        assert response3 == "third"

    def test_returns_default_when_exhausted(self) -> None:
        """Returns default value when responses exhausted."""
        tool = MockTool(["only"])
        tool.run("first")
        response, _ = tool.run("second")
        assert response == "MOCK_RESPONSE"

    def test_generates_session_id(self) -> None:
        """Session ID is auto-generated."""
        tool = MockTool(["response"])
        _, session_id = tool.run("prompt")
        assert session_id is not None
        assert session_id.startswith("mock-session-")

    def test_preserves_existing_session_id(self) -> None:
        """Existing session ID is preserved."""
        tool = MockTool(["response"])
        _, session_id = tool.run("prompt", session_id="existing-session")
        assert session_id == "existing-session"

    def test_increments_session_counter(self) -> None:
        """Session counter increments on each call."""
        tool = MockTool(["r1", "r2", "r3"])
        _, session1 = tool.run("p1")
        _, session2 = tool.run("p2")
        _, session3 = tool.run("p3")
        assert session1 == "mock-session-1"
        assert session2 == "mock-session-2"
        assert session3 == "mock-session-3"

    def test_accepts_context_string(self) -> None:
        """Accepts context as string."""
        tool = MockTool(["response"])
        response, _ = tool.run("prompt", context="some context")
        assert response == "response"

    def test_accepts_context_list(self) -> None:
        """Accepts context as list."""
        tool = MockTool(["response"])
        response, _ = tool.run("prompt", context=["ctx1", "ctx2"])
        assert response == "response"

    def test_accepts_log_dir(self) -> None:
        """Accepts log_dir parameter (ignored)."""
        tool = MockTool(["response"])
        response, _ = tool.run("prompt", log_dir=Path("/tmp"))
        assert response == "response"
