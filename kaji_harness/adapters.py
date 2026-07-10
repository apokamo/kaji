"""CLI event adapters for kaji_harness.

Each adapter extracts session_id, text, and cost from CLI-specific JSONL events.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .models import CostInfo

# 順序が重要: 連続 2 個のサロゲートペア (high + low) を優先的に 1 マッチとして拾い、
# それに当てはまらない場合のみ単独 \uXXXX として拾う。
# - high surrogate: U+D800..U+DBFF → \uD[89AB][0-9A-F]{2}
# - low surrogate:  U+DC00..U+DFFF → \uD[CDEF][0-9A-F]{2}
_ESCAPE_RE = re.compile(
    r"\\u[dD][89aAbB][0-9a-fA-F]{2}\\u[dD][cdefCDEF][0-9a-fA-F]{2}"  # surrogate pair
    r"|\\u[0-9a-fA-F]{4}"  # BMP 単独
)


def _is_control_cp(cp: int) -> bool:
    """C0 (U+0000–U+001F) / DEL (U+007F) / C1 (U+0080–U+009F) 制御コードポイントか。"""
    return cp <= 0x1F or 0x7F <= cp <= 0x9F


def _escape_controls(s: str) -> str:
    """C0/C1 制御文字を `\\uXXXX` 表記へ戻し、端末操作・ログ行偽装を防ぐ（Issue #137）。

    復号後のスカラ文字列に ESC (U+001B) や改行 (U+000A) 等の制御文字が残ると、
    `stream_and_log` が `console.log` / verbose stdout へ書き出す際に端末制御列や
    verdict 風の偽行として解釈され得る。JSON-object 経路は `json.dumps` が同種の
    制御文字を再エスケープするため、本 helper で他経路の出力挙動を揃える。
    """
    if not any(_is_control_cp(ord(c)) for c in s):
        return s
    return "".join(f"\\u{ord(c):04x}" if _is_control_cp(ord(c)) else c for c in s)


def _escape_lone_surrogates(s: str) -> str:
    """孤立サロゲート(U+D800..U+DFFF)を 16 進エスケープ表記へ戻し UTF-8 書き出し可能にする。

    valid surrogate pair は json.loads 時点で単一コードポイント(>=U+10000)へ結合済みのため、
    復号後の str に残る U+D800..U+DFFF は必ず孤立サロゲートである。これにより
    decode_unicode_escapes の戻り値は常に .encode("utf-8") 可能という不変条件を保つ。
    """
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in s):
        return s
    return "".join(f"\\u{ord(c):04x}" if 0xD800 <= ord(c) <= 0xDFFF else c for c in s)


def decode_unicode_escapes(text: str) -> str:
    """ツール結果テキストに含まれる `\\uXXXX` リテラルを実文字へ展開する（Issue #137）。

    - 全体が JSON 値として parse 可能な場合: ensure_ascii=False で再シリアライズ
    - parse 不可な場合: 正規表現でサロゲートペア優先に個別復号
    - サロゲートペアは正しく結合する（`\\uD83D\\uDE00` → 😀 等）
    - 孤立サロゲートは原表記のまま維持し、戻り値は常に `.encode("utf-8")` 可能
    - C0/C1 制御文字（ESC・改行等）は復号せず `\\uXXXX` 表記を維持する
      （端末操作・verdict/ログ行の偽装を防ぐ）
    - 置換対象が存在しない通常テキストはそのまま返す

    Args:
        text: adapter が抽出した tool result テキスト。外側 JSONL を `json.loads`
            済みのため、literal `\\uXXXX` だけでなく実サロゲート文字を含み得る。

    Returns:
        `\\uXXXX` を展開し孤立サロゲートを escape 表記へ戻した文字列。全 return 経路が
        `_escape_lone_surrogates` を通るため、戻り値は常に `.encode("utf-8")` 可能。
    """
    # 全 return 経路をこの helper で包み、実サロゲート（literal でなく decode 済み）が
    # 残っても最終 sanitize されることを保証する。
    return _escape_lone_surrogates(_decode_unicode_escapes(text))


def _decode_unicode_escapes(text: str) -> str:
    """`decode_unicode_escapes` の復号本体（sanitize は呼び出し側で行う）。"""
    if "\\u" not in text:
        return text
    # 第一段: JSON 値全体として parse できれば re-serialize（構造を保ったまま日本語化）
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        if isinstance(parsed, str):
            return _escape_controls(parsed)
    except json.JSONDecodeError:
        pass

    # 第二段: 部分的に \uXXXX を含む通常テキスト → サロゲートペア優先で個別復号
    def _sub(m: re.Match[str]) -> str:
        token = m.group(0)
        try:
            decoded: str = json.loads(f'"{token}"')
        except json.JSONDecodeError:
            return token
        # 孤立サロゲート（high のみ / low のみ）は UTF-8 で書けないので原文維持
        if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
            return token
        # C0/C1 制御文字（ESC・改行等）は端末操作や verdict/ログ行の偽装に悪用され得るため
        # 復号せず原表記 (\uXXXX) を維持する。JSON-object 経路の json.dumps と挙動を揃える。
        if any(_is_control_cp(ord(c)) for c in decoded):
            return token
        return decoded

    return _ESCAPE_RE.sub(_sub, text)


class CLIEventAdapter(Protocol):
    """CLI 固有の JSONL イベント構造をデコードする。"""

    def extract_session_id(self, event: dict[str, Any]) -> str | None: ...
    def extract_text(self, event: dict[str, Any]) -> str | None: ...
    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None: ...
    def extract_error_message(self, event: dict[str, Any]) -> str | None: ...
    def is_terminal_event(self, event: dict[str, Any]) -> bool: ...
    def is_terminal_failure(self, event: dict[str, Any]) -> bool: ...
    def treats_stream_error_as_failure(self) -> bool:
        """Stream-level `type:"error"` event を terminal-seen 分岐の失敗根拠とするか。

        - True (Claude / Gemini): 既存契約。terminal が success でも
          `error_messages` が non-empty なら `CLIExecutionError` を raise する
        - False (Codex): `error` event は recoverable 通知（Reconnecting 等）を
          含むため失敗根拠としない。`turn.failed` のみで失敗判定する

        `error_messages` の収集自体および `CLIExecutionError` detail のフォールバック
        利用は本フラグに依らず常に行う（観測性維持）。
        """
        ...


_TOOL_SUMMARY_LEN = 80
_THINKING_SUMMARY_LEN = 160


def _non_empty_string(value: Any) -> str | None:
    """Return value when it is a non-empty string."""
    return value if isinstance(value, str) and value else None


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

    def extract_error_message(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "result" and self.is_terminal_failure(event):
            return _non_empty_string(event.get("result"))
        if event.get("type") == "error":
            return _non_empty_string(event.get("message"))
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

    def treats_stream_error_as_failure(self) -> bool:
        return True


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
                # result may be None when the tool call failed (result: null in JSONL)
                result = item.get("result")
                if not result:
                    return None
                contents = result.get("content", [])
                extracted = [
                    decode_unicode_escapes(c["text"])
                    for c in contents
                    if c.get("type") == "text" and "text" in c
                ]
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

    def extract_error_message(self, event: dict[str, Any]) -> str | None:
        if event.get("type") == "error":
            return _non_empty_string(event.get("message"))
        if event.get("type") == "turn.failed":
            error = event.get("error") or {}
            if isinstance(error, dict):
                return _non_empty_string(error.get("message"))
        return None

    def is_terminal_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in ("turn.completed", "turn.failed")

    def is_terminal_failure(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "turn.failed"

    def treats_stream_error_as_failure(self) -> bool:
        # Codex の stream-level `type:"error"` event は recoverable 通知
        # (`Reconnecting...` 等) を含むため失敗根拠としない。fatal は `turn.failed`
        # event で表現される (adapters.py の is_terminal_failure 契約)。Issue #196。
        return False


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

    def extract_error_message(self, event: dict[str, Any]) -> str | None:
        event_type = event.get("type")
        if event_type not in ("error", "result"):
            return None
        if event_type == "result" and not self.is_terminal_failure(event):
            return None

        message = _non_empty_string(event.get("message"))
        if message:
            return message
        error = event.get("error")
        if isinstance(error, dict):
            return _non_empty_string(error.get("message"))
        return _non_empty_string(error)

    def is_terminal_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "result"

    def is_terminal_failure(self, event: dict[str, Any]) -> bool:
        # Gemini `result` の failure シグナル: status が "success" 以外なら失敗扱い。
        if event.get("type") != "result":
            return False
        status = event.get("status")
        return status is not None and status != "success"

    def treats_stream_error_as_failure(self) -> bool:
        return True


ADAPTERS: dict[str, CLIEventAdapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
}
