"""Tests for CLI event adapters (Claude, Codex, Gemini).

Each adapter extracts session_id, text, and cost from JSONL events.
"""

import pytest

from kaji_harness.adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
from kaji_harness.models import CostInfo

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
    def test_extract_text_from_result_event_returns_none(self, adapter: ClaudeAdapter) -> None:
        """Result event no longer returns text (anomaly B fix)."""
        event = {"type": "result", "result": "final text", "total_cost_usd": 0.05}
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_cost_from_result_event_with_usd(self, adapter: ClaudeAdapter) -> None:
        """Result event cost includes usd field."""
        event = {"type": "result", "result": "done", "total_cost_usd": 0.12}
        cost = adapter.extract_cost(event)
        assert cost is not None
        assert cost.usd == 0.12

    @pytest.mark.small
    def test_extract_text_from_tool_use_bash(self, adapter: ClaudeAdapter) -> None:
        """tool_use Bash renders summary with command head."""
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]
            },
        }
        assert adapter.extract_text(event) == "[tool] Bash $ ls -la"

    @pytest.mark.small
    def test_extract_text_from_tool_use_bash_replaces_newlines(
        self, adapter: ClaudeAdapter
    ) -> None:
        """Bash command newlines are replaced with spaces."""
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "echo a\necho b"}}
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Bash $ echo a echo b"

    @pytest.mark.small
    def test_extract_text_from_tool_use_read(self, adapter: ClaudeAdapter) -> None:
        """tool_use Read renders summary with file_path."""
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "kaji_harness/adapters.py"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Read kaji_harness/adapters.py"

    @pytest.mark.small
    def test_extract_text_from_tool_use_edit(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "kaji_harness/adapters.py"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Edit kaji_harness/adapters.py"

    @pytest.mark.small
    def test_extract_text_from_tool_use_write(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "draft/design/issue-XX.md"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Write draft/design/issue-XX.md"

    @pytest.mark.small
    def test_extract_text_from_tool_use_grep(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "extract_text"}}
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Grep extract_text"

    @pytest.mark.small
    def test_extract_text_from_tool_use_glob(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Glob",
                        "input": {"pattern": "kaji_harness/**/*.py"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Glob kaji_harness/**/*.py"

    @pytest.mark.small
    def test_extract_text_from_tool_use_todowrite(self, adapter: ClaudeAdapter) -> None:
        """TodoWrite renders count of todos."""
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "TodoWrite",
                        "input": {"todos": [{}, {}, {}, {}]},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] TodoWrite (4 items)"

    @pytest.mark.small
    def test_extract_text_from_tool_use_skill(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "issue-design"}}
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] Skill issue-design"

    @pytest.mark.small
    def test_extract_text_from_tool_use_toolsearch(self, adapter: ClaudeAdapter) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "ToolSearch",
                        "input": {"query": "select:TodoWrite"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] ToolSearch select:TodoWrite"

    @pytest.mark.small
    def test_extract_text_from_tool_use_unknown(self, adapter: ClaudeAdapter) -> None:
        """Unknown tools render only the tool name (no input repr for safety)."""
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "WebFetch",
                        "input": {"url": "https://example.com", "api_key": "secret"},
                    }
                ]
            },
        }
        assert adapter.extract_text(event) == "[tool] WebFetch"

    @pytest.mark.small
    def test_tool_summary_truncated_at_80_chars(self, adapter: ClaudeAdapter) -> None:
        """tool_use summary values are truncated to 80 chars with `…` suffix."""
        long_path = "a" * 200
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": long_path}}]
            },
        }
        out = adapter.extract_text(event)
        assert out is not None
        # 80 chars taken + "…" suffix marking truncation (per design)
        assert out == f"[tool] Read {'a' * 80}…"

    @pytest.mark.small
    def test_tool_summary_no_ellipsis_when_within_limit(self, adapter: ClaudeAdapter) -> None:
        """tool_use summary at exactly the limit has no `…` suffix."""
        boundary_path = "a" * 80
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": boundary_path}}
                ]
            },
        }
        assert adapter.extract_text(event) == f"[tool] Read {'a' * 80}"

    @pytest.mark.small
    def test_extract_text_from_thinking_redacted_returns_none(self, adapter: ClaudeAdapter) -> None:
        """Empty `thinking` (Extended Thinking redacted) is suppressed."""
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "", "signature": "abc"}]},
        }
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_text_from_thinking_with_content(self, adapter: ClaudeAdapter) -> None:
        """Non-empty thinking renders with [thinking] prefix."""
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "I should check the file."}]},
        }
        assert adapter.extract_text(event) == "[thinking] I should check the file."

    @pytest.mark.small
    def test_extract_text_from_thinking_truncated_at_160_chars(
        self, adapter: ClaudeAdapter
    ) -> None:
        """thinking content is truncated to 160 characters with `…` suffix."""
        long_thought = "x" * 300
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": long_thought}]},
        }
        out = adapter.extract_text(event)
        assert out == f"[thinking] {'x' * 160}…"

    @pytest.mark.small
    def test_extract_text_mixed_text_and_tool_use(self, adapter: ClaudeAdapter) -> None:
        """Mixed text + tool_use blocks are joined with newline.

        Note: 1 assistant message can hold multiple parallel tool_use blocks
        (Anthropic tool use parallel calls). Since stream_and_log adds the
        timestamp/step prefix once per extract_text return value, the rendered
        multi-line string carries a single prefix — accepted as-is for now.
        """
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "foo.py"}},
                ]
            },
        }
        assert adapter.extract_text(event) == "Let me check.\n[tool] Read foo.py"

    @pytest.mark.small
    def test_extract_text_assistant_with_only_unknown_blocks_returns_none(
        self, adapter: ClaudeAdapter
    ) -> None:
        """Assistant message with only unrenderable blocks returns None."""
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "s"},
                    {"type": "unknown_future_block", "data": "x"},
                ]
            },
        }
        assert adapter.extract_text(event) is None


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


class TestIsTerminalEvent:
    """is_terminal_event: session 終端マーカー判定（local-p1-22）。"""

    @pytest.mark.small
    def test_claude_result_event_is_terminal(self) -> None:
        adapter = ClaudeAdapter()
        assert adapter.is_terminal_event(
            {"type": "result", "subtype": "success", "is_error": False}
        )

    @pytest.mark.small
    def test_claude_error_result_is_terminal(self) -> None:
        adapter = ClaudeAdapter()
        # success/failure 共に terminal（成功失敗は returncode で判定する責務分離）
        assert adapter.is_terminal_event({"type": "result", "subtype": "error", "is_error": True})

    @pytest.mark.small
    def test_claude_assistant_is_not_terminal(self) -> None:
        adapter = ClaudeAdapter()
        assert not adapter.is_terminal_event({"type": "assistant", "message": {"content": []}})
        assert not adapter.is_terminal_event({"type": "system", "subtype": "init"})
        assert not adapter.is_terminal_event({"type": "user"})

    @pytest.mark.small
    def test_claude_empty_event_is_not_terminal(self) -> None:
        adapter = ClaudeAdapter()
        assert not adapter.is_terminal_event({})

    @pytest.mark.small
    def test_codex_turn_completed_is_terminal(self) -> None:
        adapter = CodexAdapter()
        assert adapter.is_terminal_event({"type": "turn.completed", "usage": {}})

    @pytest.mark.small
    def test_codex_turn_failed_is_terminal(self) -> None:
        adapter = CodexAdapter()
        assert adapter.is_terminal_event({"type": "turn.failed", "error": {"message": "x"}})

    @pytest.mark.small
    def test_codex_error_event_is_not_terminal(self) -> None:
        # error は intermediate（後続の turn.failed まで読まないと error_messages が薄くなる）
        adapter = CodexAdapter()
        assert not adapter.is_terminal_event({"type": "error", "message": "boom"})

    @pytest.mark.small
    def test_codex_other_events_are_not_terminal(self) -> None:
        adapter = CodexAdapter()
        for ev in (
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {}},
            {"type": "item.started"},
        ):
            assert not adapter.is_terminal_event(ev)

    @pytest.mark.small
    def test_gemini_result_is_terminal(self) -> None:
        adapter = GeminiAdapter()
        assert adapter.is_terminal_event({"type": "result", "status": "success", "stats": {}})
        assert adapter.is_terminal_event({"type": "result", "status": "error", "stats": {}})

    @pytest.mark.small
    def test_gemini_init_and_message_are_not_terminal(self) -> None:
        adapter = GeminiAdapter()
        assert not adapter.is_terminal_event({"type": "init", "session_id": "g-1"})
        assert not adapter.is_terminal_event(
            {"type": "message", "role": "assistant", "content": "hi"}
        )


class TestIsTerminalFailure:
    """is_terminal_failure: terminal event 内の failure シグナル判定（local-p1-22 fix）。"""

    @pytest.mark.small
    def test_claude_subtype_error_is_failure(self) -> None:
        adapter = ClaudeAdapter()
        assert adapter.is_terminal_failure({"type": "result", "subtype": "error"})

    @pytest.mark.small
    def test_claude_is_error_true_is_failure(self) -> None:
        adapter = ClaudeAdapter()
        assert adapter.is_terminal_failure(
            {"type": "result", "subtype": "success", "is_error": True}
        )

    @pytest.mark.small
    def test_claude_success_is_not_failure(self) -> None:
        adapter = ClaudeAdapter()
        assert not adapter.is_terminal_failure(
            {"type": "result", "subtype": "success", "is_error": False}
        )

    @pytest.mark.small
    def test_claude_non_terminal_is_not_failure(self) -> None:
        adapter = ClaudeAdapter()
        assert not adapter.is_terminal_failure({"type": "assistant"})
        assert not adapter.is_terminal_failure({})

    @pytest.mark.small
    def test_codex_turn_failed_is_failure(self) -> None:
        adapter = CodexAdapter()
        assert adapter.is_terminal_failure({"type": "turn.failed", "error": {"message": "x"}})

    @pytest.mark.small
    def test_codex_turn_completed_is_not_failure(self) -> None:
        adapter = CodexAdapter()
        assert not adapter.is_terminal_failure({"type": "turn.completed", "usage": {}})

    @pytest.mark.small
    def test_codex_error_is_not_failure(self) -> None:
        # error は intermediate（is_terminal_event でも False）
        adapter = CodexAdapter()
        assert not adapter.is_terminal_failure({"type": "error", "message": "x"})

    @pytest.mark.small
    def test_gemini_status_error_is_failure(self) -> None:
        adapter = GeminiAdapter()
        assert adapter.is_terminal_failure({"type": "result", "status": "error", "stats": {}})

    @pytest.mark.small
    def test_gemini_status_success_is_not_failure(self) -> None:
        adapter = GeminiAdapter()
        assert not adapter.is_terminal_failure({"type": "result", "status": "success", "stats": {}})

    @pytest.mark.small
    def test_gemini_non_terminal_is_not_failure(self) -> None:
        adapter = GeminiAdapter()
        assert not adapter.is_terminal_failure({"type": "init"})
        assert not adapter.is_terminal_failure({"type": "message"})


class TestTreatsStreamErrorAsFailure:
    """Issue #196: adapter ごとの stream-level error event の致死性契約。"""

    @pytest.mark.small
    def test_claude_treats_stream_error_as_failure(self) -> None:
        assert ClaudeAdapter().treats_stream_error_as_failure() is True

    @pytest.mark.small
    def test_codex_does_not_treat_stream_error_as_failure(self) -> None:
        # Codex の stream-level `type:"error"` event は recoverable 通知を含むため
        # 失敗根拠としない (Issue #196)。fatal は `turn.failed` で表現される。
        assert CodexAdapter().treats_stream_error_as_failure() is False

    @pytest.mark.small
    def test_gemini_treats_stream_error_as_failure(self) -> None:
        assert GeminiAdapter().treats_stream_error_as_failure() is True
