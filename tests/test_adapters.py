"""Tests for CLI event adapters (Claude, Codex, Gemini).

Each adapter extracts session_id, text, and cost from JSONL events.
"""

import pytest

from dao_harness.adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
from dao_harness.models import CostInfo

# ==========================================
# Claude Adapter
# ==========================================


class TestClaudeAdapter:
    """ClaudeAdapter: Claude Code JSONL event parsing."""

    @pytest.fixture
    def adapter(self) -> ClaudeAdapter:
        return ClaudeAdapter()

    @pytest.mark.small
    def test_extract_session_id_from_init_event(self, adapter: ClaudeAdapter) -> None:
        """Init event with subtype=init returns session_id."""
        event = {"type": "system", "subtype": "init", "session_id": "abc123"}
        assert adapter.extract_session_id(event) == "abc123"

    @pytest.mark.small
    def test_extract_session_id_returns_none_for_non_matching(self, adapter: ClaudeAdapter) -> None:
        """Non-init system event returns None."""
        event = {"type": "system", "subtype": "other"}
        assert adapter.extract_session_id(event) is None

    @pytest.mark.small
    def test_extract_text_from_assistant_message(self, adapter: ClaudeAdapter) -> None:
        """Assistant message with text content returns the text."""
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        assert adapter.extract_text(event) == "hello"

    @pytest.mark.small
    def test_extract_text_returns_none_for_non_matching(self, adapter: ClaudeAdapter) -> None:
        """Non-assistant/non-result event returns None."""
        event = {"type": "system", "subtype": "other"}
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_cost_from_result_event(self, adapter: ClaudeAdapter) -> None:
        """Result event with total_cost_usd returns CostInfo."""
        event = {"type": "result", "result": "done", "total_cost_usd": 0.05}
        cost = adapter.extract_cost(event)
        assert cost is not None
        assert cost == CostInfo(usd=0.05)

    @pytest.mark.small
    def test_extract_cost_returns_none_for_non_matching(self, adapter: ClaudeAdapter) -> None:
        """Non-result event returns None for cost."""
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        assert adapter.extract_cost(event) is None

    @pytest.mark.small
    def test_extract_text_from_result_event(self, adapter: ClaudeAdapter) -> None:
        """Result event returns the result text."""
        event = {"type": "result", "result": "final text", "total_cost_usd": 0.05}
        assert adapter.extract_text(event) == "final text"

    @pytest.mark.small
    def test_extract_cost_from_result_event_with_usd(self, adapter: ClaudeAdapter) -> None:
        """Result event cost includes usd field."""
        event = {"type": "result", "result": "done", "total_cost_usd": 0.12}
        cost = adapter.extract_cost(event)
        assert cost is not None
        assert cost.usd == 0.12


# ==========================================
# Codex Adapter
# ==========================================


class TestCodexAdapter:
    """CodexAdapter: Codex JSONL event parsing."""

    @pytest.fixture
    def adapter(self) -> CodexAdapter:
        return CodexAdapter()

    @pytest.mark.small
    def test_extract_session_id_from_thread_started(self, adapter: CodexAdapter) -> None:
        """thread.started event returns thread_id."""
        event = {"type": "thread.started", "thread_id": "thread-456"}
        assert adapter.extract_session_id(event) == "thread-456"

    @pytest.mark.small
    def test_extract_session_id_returns_none_for_non_matching(self, adapter: CodexAdapter) -> None:
        """Non-thread.started event returns None."""
        event = {"type": "other"}
        assert adapter.extract_session_id(event) is None

    @pytest.mark.small
    def test_extract_text_from_agent_message(self, adapter: CodexAdapter) -> None:
        """item.completed with agent_message type returns text."""
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "hello"},
        }
        assert adapter.extract_text(event) == "hello"

    @pytest.mark.small
    def test_extract_text_returns_none_for_non_matching(self, adapter: CodexAdapter) -> None:
        """Non-item.completed event returns None."""
        event = {"type": "other"}
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_cost_from_turn_completed(self, adapter: CodexAdapter) -> None:
        """turn.completed event with usage returns CostInfo."""
        event = {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        cost = adapter.extract_cost(event)
        assert cost is not None
        assert cost == CostInfo(input_tokens=100, output_tokens=50)

    @pytest.mark.small
    def test_extract_cost_returns_none_for_non_matching(self, adapter: CodexAdapter) -> None:
        """Non-turn.completed event returns None for cost."""
        event = {"type": "other"}
        assert adapter.extract_cost(event) is None

    @pytest.mark.small
    def test_extract_text_from_reasoning_event(self, adapter: CodexAdapter) -> None:
        """item.completed with reasoning type returns text."""
        event = {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "thinking"},
        }
        assert adapter.extract_text(event) == "thinking"


# ==========================================
# Gemini Adapter
# ==========================================


class TestGeminiAdapter:
    """GeminiAdapter: Gemini CLI JSONL event parsing."""

    @pytest.fixture
    def adapter(self) -> GeminiAdapter:
        return GeminiAdapter()

    @pytest.mark.small
    def test_extract_session_id_from_init_event(self, adapter: GeminiAdapter) -> None:
        """Init event returns session_id."""
        event = {"type": "init", "session_id": "gem-789"}
        assert adapter.extract_session_id(event) == "gem-789"

    @pytest.mark.small
    def test_extract_session_id_returns_none_for_non_matching(self, adapter: GeminiAdapter) -> None:
        """Non-init event returns None."""
        event = {"type": "other"}
        assert adapter.extract_session_id(event) is None

    @pytest.mark.small
    def test_extract_text_from_assistant_message(self, adapter: GeminiAdapter) -> None:
        """Assistant message event returns content text."""
        event = {"type": "message", "role": "assistant", "content": "hello"}
        assert adapter.extract_text(event) == "hello"

    @pytest.mark.small
    def test_extract_text_returns_none_for_user_message(self, adapter: GeminiAdapter) -> None:
        """User message event returns None."""
        event = {"type": "message", "role": "user", "content": "question"}
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_text_returns_none_for_non_matching(self, adapter: GeminiAdapter) -> None:
        """Non-message event returns None."""
        event = {"type": "other"}
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_cost_from_result_event(self, adapter: GeminiAdapter) -> None:
        """Result event with stats returns CostInfo with token counts."""
        event = {
            "type": "result",
            "status": "success",
            "stats": {"input_tokens": 1000, "output_tokens": 50},
        }
        cost = adapter.extract_cost(event)
        assert cost is not None
        assert cost.input_tokens == 1000
        assert cost.output_tokens == 50

    @pytest.mark.small
    def test_extract_cost_returns_none_for_non_matching(self, adapter: GeminiAdapter) -> None:
        """Non-result event returns None for cost."""
        event = {"type": "message", "role": "assistant", "content": "hi"}
        assert adapter.extract_cost(event) is None

    @pytest.mark.small
    def test_extract_cost_returns_none_for_init(self, adapter: GeminiAdapter) -> None:
        """Init event returns None for cost."""
        event = {"type": "init", "session_id": "gem-789"}
        assert adapter.extract_cost(event) is None
