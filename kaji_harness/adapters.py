"""CLI event adapters for kaji_harness.

Each adapter extracts session_id, text, and cost from CLI-specific JSONL events.
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import CostInfo


class CLIEventAdapter(Protocol):
    """CLI 固有の JSONL イベント構造をデコードする。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None: ...
    def extract_text(self, event: dict[str, Any]) -> str | None: ...
    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None: ...


class ClaudeAdapter:
    """Claude Code CLI の JSONL イベントアダプタ。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("session_id")
        return None

    def extract_text(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "assistant":
            content = event.get("message", {}).get("content", [])
            texts = [c["text"] for c in content if c.get("type") == "text" and "text" in c]
            return "\n".join(texts) if texts else None
        if event.get("type") == "result":
            result = event.get("result")
            return result if isinstance(result, str) and result else None
        return None

    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None:
        if event.get("type") == "result":
            usd = event.get("total_cost_usd")
            if usd is not None:
                return CostInfo(usd=usd)
        return None


class CodexAdapter:
    """Codex CLI の JSONL イベントアダプタ。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "thread.started":
            return event.get("thread_id")
        return None

    def extract_text(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type in ("agent_message", "reasoning"):
                text = item.get("text")
                return text if text else None
            if item_type == "mcp_tool_call":
                # V5/V6 restoration: extract text from mcp_tool_call result.content
                result = item.get("result", {})
                contents = result.get("content", [])
                extracted = [c["text"] for c in contents if c.get("type") == "text" and "text" in c]
                return "\n".join(extracted) if extracted else None
        return None

    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None:
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            if usage:
                return CostInfo(
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                )
        return None


class GeminiAdapter:
    """Gemini CLI の JSONL イベントアダプタ。

    stream-json イベント形式:
    - init: {type: "init", session_id, model}
    - message: {type: "message", role: "user"|"assistant", content: "<text>"}
    - result: {type: "result", status, stats: {input_tokens, output_tokens, ...}}
    """

    def extract_session_id(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "init":
            return event.get("session_id")
        return None

    def extract_text(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "message" and event.get("role") == "assistant":
            content = event.get("content")
            return content if isinstance(content, str) and content else None
        return None

    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None:
        if event.get("type") == "result":
            stats = event.get("stats", {})
            if stats:
                return CostInfo(
                    input_tokens=stats.get("input_tokens"),
                    output_tokens=stats.get("output_tokens"),
                )
        return None


ADAPTERS: dict[str, CLIEventAdapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
}
