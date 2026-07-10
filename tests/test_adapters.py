"""Tests for CLI event adapters (Claude, Codex, Gemini).

Each adapter extracts session_id, text, and cost from JSONL events.
"""

import pytest

from kaji_harness.adapters import (
    ClaudeAdapter,
    CodexAdapter,
    GeminiAdapter,
    decode_unicode_escapes,
)
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

    @pytest.mark.small
    def test_extract_text_from_user_tool_result_returns_none(self, adapter: ClaudeAdapter) -> None:
        """Issue #137: Claude は tool_result を表示経路に流さない（対象外の固定）。

        将来 tool_result 抽出を追加した場合は本テストが落ち、decode_unicode_escapes
        適用の必要性に気付ける。
        """
        event = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": [{"type": "text", "text": "\\u306e"}]}
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

    @pytest.mark.small
    def test_extract_text_from_mcp_tool_call_decodes_unicode_escapes(
        self, adapter: CodexAdapter
    ) -> None:
        """mcp_tool_call の result.content[].text 内の literal \\uXXXX を可読化する。

        Issue #137 の再現テスト: 二重 JSON エンコードで残った `\\u306e` 等が
        console.log / full_output に流れる前にデコードされる（修正前は FAIL）。
        """
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"title": "config/workflow \\u306e\\u6697\\u9ed9"}',
                        }
                    ]
                },
            },
        }
        out = adapter.extract_text(event)
        assert out is not None
        assert "の暗黙" in out
        assert "\\u306e" not in out
        assert "\\u6697" not in out
        assert "\\u9ed9" not in out

    @pytest.mark.small
    def test_extract_text_from_mcp_tool_call_none_result_returns_none(
        self, adapter: CodexAdapter
    ) -> None:
        """result:null（tool call 失敗）は None を返す（既存挙動の維持）。"""
        event = {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "result": None},
        }
        assert adapter.extract_text(event) is None

    @pytest.mark.small
    def test_extract_text_from_mcp_tool_call_plain_text_unchanged(
        self, adapter: CodexAdapter
    ) -> None:
        """エスケープを含まない tool result はそのまま返す。"""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {"content": [{"type": "text", "text": "plain output"}]},
            },
        }
        assert adapter.extract_text(event) == "plain output"


# ==========================================
# decode_unicode_escapes helper
# ==========================================


class TestDecodeUnicodeEscapes:
    """decode_unicode_escapes: tool result text の \\uXXXX 展開（Issue #137）。"""

    @pytest.mark.small
    def test_full_json_object_decoded_and_reserialized(self) -> None:
        """JSON 全体として parse 可能なら ensure_ascii=False で再整形する。"""
        out = decode_unicode_escapes('{"title": "config/workflow \\u306e\\u6697\\u9ed9"}')
        assert "の暗黙" in out
        assert "\\u306e" not in out

    @pytest.mark.small
    def test_partial_escape_in_plain_text(self) -> None:
        """JSON parse 不能な部分エスケープは個別に復号する。"""
        assert decode_unicode_escapes("prefix \\u3042 suffix") == "prefix あ suffix"

    @pytest.mark.small
    def test_plain_text_without_escape_returned_as_is(self) -> None:
        """エスケープを含まない通常テキストはそのまま返す。"""
        assert decode_unicode_escapes("hello world") == "hello world"

    @pytest.mark.small
    def test_surrogate_pair_decoded_to_supplementary_char(self) -> None:
        """連続サロゲートペアは補助平面 1 文字へ復号する。"""
        assert decode_unicode_escapes("emoji \\uD83D\\uDE00 end") == "emoji 😀 end"

    @pytest.mark.small
    def test_lone_high_surrogate_kept_literal(self) -> None:
        """孤立 high surrogate は原表記のまま維持する（UTF-8 書き出し不能を回避）。"""
        out = decode_unicode_escapes("lone \\uD83D end")
        assert out == "lone \\uD83D end"

    @pytest.mark.small
    def test_lone_low_surrogate_kept_literal(self) -> None:
        """孤立 low surrogate も原表記のまま維持する。"""
        out = decode_unicode_escapes("lone \\uDE00 end")
        assert out == "lone \\uDE00 end"

    @pytest.mark.small
    def test_return_value_always_utf8_encodable(self) -> None:
        """戻り値は常に UTF-8 で書き出し可能（stream_and_log 経路の不変条件）。"""
        for text in (
            "lone \\uD83D end",
            "lone \\uDE00 end",
            '{"title": "lone \\uD83D"}',
            "prefix \\uD83D\\uDE00 mid \\u3042 lone \\uD800 tail",
        ):
            decode_unicode_escapes(text).encode("utf-8")  # raises if surrogate leaks

    @pytest.mark.small
    def test_broken_escape_sequence_kept(self) -> None:
        """不正なエスケープ（\\uZZZZ）はクラッシュせずそのまま残す。"""
        assert decode_unicode_escapes("broken \\uZZZZ end") == "broken \\uZZZZ end"

    @pytest.mark.small
    def test_mixed_pair_bmp_lone_and_plain(self) -> None:
        """ペア + 単独 BMP + 孤立 + 通常テキストの混在ケース。"""
        out = decode_unicode_escapes("prefix \\uD83D\\uDE00 mid \\u3042 lone \\uD800 tail")
        assert "😀" in out
        assert "あ" in out
        assert "\\uD800" in out  # 孤立サロゲートはリテラル維持
        assert "prefix " in out
        assert " tail" in out
        out.encode("utf-8")

    @pytest.mark.small
    def test_nested_json_object_lone_high_surrogate(self) -> None:
        """object の nested value に high-only surrogate（第一段 dict 分岐の固定）。"""
        out = decode_unicode_escapes('{"title": "lone \\uD83D"}')
        encoded = out.encode("utf-8")  # UTF-8 書き出し可能
        assert b"\\ud83d" in encoded.lower()  # 孤立サロゲートはリテラル escape で保持

    @pytest.mark.small
    def test_nested_json_list_lone_low_surrogate(self) -> None:
        """list の nested value に low-only surrogate（第一段 list 分岐の固定）。"""
        out = decode_unicode_escapes('["ok \\uDE00"]')
        encoded = out.encode("utf-8")
        assert b"\\ude00" in encoded.lower()

    @pytest.mark.small
    def test_nested_json_object_valid_bmp_and_lone_mixed(self) -> None:
        """valid BMP + high-only 混在の object: BMP は復号、孤立はリテラル維持。"""
        out = decode_unicode_escapes('{"a": "\\u3042", "b": "\\uD800"}')
        assert "あ" in out
        encoded = out.encode("utf-8")
        assert b"\\ud800" in encoded.lower()

    @pytest.mark.small
    def test_json_string_scalar_decoded(self) -> None:
        """全体が JSON 文字列スカラのケースも復号する。"""
        assert decode_unicode_escapes('"\\u3042\\u3044"') == "あい"

    @pytest.mark.small
    def test_raw_lone_high_surrogate_input_escaped(self) -> None:
        """外側 JSONL decode 済みの実サロゲート文字（literal `\\u` を含まない）も escape する。

        `cli.py` は行全体を `json.loads` するため、`{"text":"\\ud83d"}` の値は helper へ
        実サロゲート `'\\ud83d'` として渡る。early return 経路もこれを sanitize し、戻り値が
        UTF-8 書き出し可能であることを固定する（review #137 の未処理経路回帰）。
        """
        raw = "\ud83d"  # literal `\u` を含まない実サロゲート 1 文字
        assert "\\u" not in raw
        out = decode_unicode_escapes(raw)
        assert out == "\\ud83d"
        out.encode("utf-8")  # raises if surrogate leaks

    @pytest.mark.small
    def test_raw_lone_low_surrogate_mixed_with_plain_escaped(self) -> None:
        """通常文字に紛れた実 low surrogate も escape され UTF-8 書き出し可能。"""
        out = decode_unicode_escapes("pre \udc00 post")
        assert out == "pre \\udc00 post"
        out.encode("utf-8")


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
    def test_extract_text_from_tool_result_event_returns_none(self, adapter: GeminiAdapter) -> None:
        """Issue #137: Gemini も tool result 類似イベントを表示経路に流さない。"""
        event = {
            "type": "tool_result",
            "content": [{"type": "text", "text": "\\u306e"}],
        }
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
