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
    def is_terminal_event(self, event: dict[str, Any]) -> bool: ...
    def is_terminal_failure(self, event: dict[str, Any]) -> bool: ...


_TOOL_SUMMARY_LEN = 80
_THINKING_SUMMARY_LEN = 160
_CMD_OUTPUT_HEAD_LINES = 10
_CMD_OUTPUT_TAIL_LINES = 5


def _truncate(value: str, limit: int) -> str:
    """Truncate to `limit` chars, marking truncation with a trailing `…`."""
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _tool_summary(name: str, inp: dict[str, Any]) -> str:
    """Render a 1-line summary for a tool_use input.

    Unknown tools return "" (no input repr) to avoid leaking secrets.
    """
    match name:
        case "Bash":
            cmd = str(inp.get("command", "")).replace("\n", " ")
            return f"$ {_truncate(cmd, _TOOL_SUMMARY_LEN)}"
        case "Read" | "Edit" | "Write":
            return _truncate(str(inp.get("file_path", "")), _TOOL_SUMMARY_LEN)
        case "Grep" | "Glob":
            return _truncate(str(inp.get("pattern", "")), _TOOL_SUMMARY_LEN)
        case "TodoWrite":
            todos = inp.get("todos", [])
            return f"({len(todos)} items)"
        case "Skill":
            return _truncate(str(inp.get("skill", "")), _TOOL_SUMMARY_LEN)
        case "ToolSearch":
            return _truncate(str(inp.get("query", "")), _TOOL_SUMMARY_LEN)
        case _:
            return ""


def _render_claude_block(block: dict[str, Any]) -> str | None:
    btype = block.get("type")
    if btype == "text":
        text = block.get("text")
        return text if isinstance(text, str) and text else None
    if btype == "tool_use":
        name = str(block.get("name", "?"))
        inp = block.get("input") or {}
        if not isinstance(inp, dict):
            inp = {}
        summary = _tool_summary(name, inp)
        return f"[tool] {name} {summary}".rstrip()
    if btype == "thinking":
        thinking = block.get("thinking")
        if isinstance(thinking, str) and thinking:
            return f"[thinking] {_truncate(thinking, _THINKING_SUMMARY_LEN)}"
        return None
    return None


class ClaudeAdapter:
    """Claude Code CLI の JSONL イベントアダプタ。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("session_id")
        return None

    def extract_text(self, event: dict[str, Any]) -> str | None:
        # `result` イベントの text 抽出は廃止（issue local-p1-14: assistant
        # 最終 text と二重出力されていた）。cost のみ extract_cost が処理する。
        if event.get("type") != "assistant":
            return None
        content = event.get("message", {}).get("content", [])
        rendered: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            line = _render_claude_block(block)
            if line:
                rendered.append(line)
        return "\n".join(rendered) if rendered else None

    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None:
        if event.get("type") == "result":
            usd = event.get("total_cost_usd")
            if usd is not None:
                return CostInfo(usd=usd)
        return None

    def is_terminal_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "result"

    def is_terminal_failure(self, event: dict[str, Any]) -> bool:
        # Claude `result` の failure シグナル: is_error:true もしくは subtype:"error"。
        if event.get("type") != "result":
            return False
        if event.get("is_error") is True:
            return True
        return event.get("subtype") == "error"


def _extract_codex_mcp_tool_call_text(item: dict[str, Any]) -> str | None:
    # result may be None when the tool call failed (result: null in JSONL)
    result = item.get("result")
    if not result:
        return None
    contents = result.get("content", [])
    extracted = [c["text"] for c in contents if c.get("type") == "text" and "text" in c]
    return "\n".join(extracted) if extracted else None


def _render_codex_command_execution_started(item: dict[str, Any]) -> str | None:
    cmd = item.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    cmd_one = cmd.replace("\n", " ")
    return f"[exec] $ {_truncate(cmd_one, _TOOL_SUMMARY_LEN)}"


def _truncate_command_output(output: str) -> str:
    lines = output.splitlines()
    total = len(lines)
    if total <= _CMD_OUTPUT_HEAD_LINES + _CMD_OUTPUT_TAIL_LINES:
        return "\n".join(lines)
    omitted = total - _CMD_OUTPUT_HEAD_LINES - _CMD_OUTPUT_TAIL_LINES
    head = lines[:_CMD_OUTPUT_HEAD_LINES]
    tail = lines[-_CMD_OUTPUT_TAIL_LINES:]
    noun = "line" if omitted == 1 else "lines"
    return "\n".join([*head, f"… ({omitted} more {noun})", *tail])


def _render_codex_command_execution_completed(item: dict[str, Any]) -> str | None:
    cmd = item.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    cmd_one = cmd.replace("\n", " ")
    header = f"[exec] $ {_truncate(cmd_one, _TOOL_SUMMARY_LEN)}"

    output = item.get("aggregated_output")
    body = _truncate_command_output(output) if isinstance(output, str) and output else ""

    parts = [header]
    if body:
        parts.append(body)

    exit_code = item.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        parts.append(f"[exit={exit_code}]")

    return "\n".join(parts)


def _render_codex_file_change(item: dict[str, Any]) -> str | None:
    changes = item.get("changes")
    if not isinstance(changes, list) or not changes:
        return None
    rendered: list[str] = []
    for ch in changes:
        if not isinstance(ch, dict):
            continue
        path = ch.get("path")
        if not isinstance(path, str) or not path:
            continue
        kind = ch.get("kind", "?")
        rendered.append(f"[edit] {kind} {path}")
    return "\n".join(rendered) if rendered else None


def _render_codex_web_search(item: dict[str, Any]) -> str | None:
    query = item.get("query")
    if not isinstance(query, str) or not query:
        action = item.get("action")
        if isinstance(action, dict):
            query = action.get("query")
    if not isinstance(query, str) or not query:
        return None
    return f"[search] {_truncate(query, _TOOL_SUMMARY_LEN)}"


class CodexAdapter:
    """Codex CLI の JSONL イベントアダプタ。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "thread.started":
            return event.get("thread_id")
        return None

    def extract_text(self, event: dict[str, Any]) -> str | None:
        etype = event.get("type")
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        itype = item.get("type")

        if etype == "item.completed":
            if itype in ("agent_message", "reasoning"):
                text = item.get("text")
                return text if isinstance(text, str) and text else None
            if itype == "mcp_tool_call":
                return _extract_codex_mcp_tool_call_text(item)
            if itype == "command_execution":
                return _render_codex_command_execution_completed(item)
            if itype == "file_change":
                return _render_codex_file_change(item)
            if itype == "web_search":
                return _render_codex_web_search(item)
            return None

        if etype == "item.started":
            if itype == "command_execution":
                return _render_codex_command_execution_started(item)
            return None

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

    def is_terminal_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in ("turn.completed", "turn.failed")

    def is_terminal_failure(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "turn.failed"


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

    def is_terminal_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "result"

    def is_terminal_failure(self, event: dict[str, Any]) -> bool:
        # Gemini `result` の failure シグナル: status が "success" 以外なら失敗扱い。
        if event.get("type") != "result":
            return False
        status = event.get("status")
        return status is not None and status != "success"


ADAPTERS: dict[str, CLIEventAdapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
}
