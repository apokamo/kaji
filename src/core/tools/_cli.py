"""CLI execution utilities (internal module)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any


def run_cli_streaming(
    args: list[str],
    timeout: int | None = None,
    verbose: bool = True,
    env: dict[str, str] | None = None,
    log_dir: Path | None = None,
    tool_name: str | None = None,
) -> tuple[str, str, int]:
    """Execute CLI with streaming output.

    Args:
        args: Command and arguments list
        timeout: Timeout in seconds (None for unlimited)
        verbose: Enable real-time output display
        env: Environment variables (None to inherit current)
        log_dir: Directory to save logs
        tool_name: Tool name ("claude", "gemini", "codex") for format_jsonl_line

    Returns:
        Tuple of (stdout, stderr, returncode)

    Raises:
        FileNotFoundError: Command not found
        subprocess.TimeoutExpired: Timeout exceeded
    """
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    # Prepare cli_console.log file handle for real-time writing
    console_file = None
    if log_dir is not None and tool_name:
        log_dir.mkdir(parents=True, exist_ok=True)
        console_file = open(log_dir / "cli_console.log", "w", encoding="utf-8")

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    timeout_occurred = threading.Event()
    timer: threading.Timer | None = None

    def kill_on_timeout() -> None:
        timeout_occurred.set()
        process.kill()

    if timeout is not None:
        timer = threading.Timer(timeout, kill_on_timeout)
        timer.start()

    try:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            if tool_name:
                formatted = format_jsonl_line(line, tool_name)
                if formatted:
                    if console_file is not None:
                        console_file.write(formatted + "\n")
                        console_file.flush()
                    if verbose:
                        print(formatted, flush=True)
            elif verbose:
                print(line, end="", flush=True)

        returncode = process.wait()

        # Join stderr thread after process.wait() to ensure all stderr is collected
        stderr_thread.join(timeout=5.0)

        if timeout_occurred.is_set():
            raise subprocess.TimeoutExpired(args, timeout or 0)
    finally:
        if timer is not None:
            timer.cancel()
        if console_file is not None:
            console_file.close()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    return stdout, stderr, returncode


def format_jsonl_line(line: str, tool_name: str) -> str | None:
    """Extract content from JSONL line.

    Args:
        line: JSONL formatted line
        tool_name: Tool name ("claude", "gemini", "codex")

    Returns:
        Extracted content, or None if not extractable
    """
    try:
        data: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        stripped = line.strip()
        return stripped if stripped else None

    if tool_name == "claude":
        return _format_claude_jsonl(data)
    if tool_name == "gemini":
        return _format_gemini_jsonl(data)
    if tool_name == "codex":
        return _format_codex_jsonl(data)

    return None


def _format_claude_jsonl(data: dict[str, Any]) -> str | None:
    """Format Claude-specific JSONL data."""
    msg_type = data.get("type")

    if msg_type == "result":
        result = data.get("result")
        return result if isinstance(result, str) and result else None

    if msg_type == "assistant":
        message = data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(texts) if texts else None

    return None


def _format_gemini_jsonl(data: dict[str, Any]) -> str | None:
    """Format Gemini-specific JSONL data.

    Format: {"type":"response","response":{"content":[{"type":"text","text":"..."}]}}
    """
    if data.get("type") != "response":
        return None

    content = data.get("response", {}).get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts) if texts else None


def _format_codex_jsonl(data: dict[str, Any]) -> str | None:
    """Format Codex-specific JSONL data.

    Format: {"type":"item.completed","item":{"type":"reasoning|agent_message","text":"..."}}
            {"type":"item.completed","item":{"type":"command_execution","aggregated_output":"..."}}
    """
    if data.get("type") != "item.completed":
        return None

    item = data.get("item", {})
    item_type = item.get("type")

    # reasoning or agent_message: return text
    if item_type in ("reasoning", "agent_message"):
        text = item.get("text", "")
        return text if text else None

    # command_execution: command + output summary
    if item_type == "command_execution":
        command = item.get("command", "")
        output = item.get("aggregated_output", "")
        exit_code = item.get("exit_code")

        # Extract actual command from /bin/bash -lc 'cd ... && cmd' format
        # Only strip bash wrapper when command starts with /bin/bash
        if command.startswith("/bin/bash"):
            if " && " in command:
                command = command.split(" && ", 1)[-1].rstrip("'")
            elif "'" in command:
                command = command.split("'", 1)[-1].rstrip("'")

        # Truncate output to first 3 lines
        max_lines = 3
        lines = output.strip().split("\n") if output else []
        if len(lines) > max_lines:
            truncated = "\n  > ".join(lines[:max_lines])
            result = f"$ {command}\n  > {truncated}\n  > ... ({len(lines) - max_lines} more lines)"
        elif lines:
            result = f"$ {command}\n  > " + "\n  > ".join(lines)
        else:
            result = f"$ {command}"

        # Show non-zero exit code
        if exit_code and exit_code != 0:
            result += f"  [exit: {exit_code}]"

        return result

    return None
