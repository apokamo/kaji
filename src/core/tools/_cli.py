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
        tool_name: Tool name ("claude") for format_jsonl_line

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
            if verbose and tool_name:
                formatted = format_jsonl_line(line, tool_name)
                if formatted:
                    print(formatted, flush=True)
            elif verbose:
                print(line, end="", flush=True)

        stderr_thread.join(timeout=5.0)
        returncode = process.wait()

        if timeout_occurred.is_set():
            raise subprocess.TimeoutExpired(args, timeout or 0)
    finally:
        if timer is not None:
            timer.cancel()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(stdout)
        (log_dir / "stderr.log").write_text(stderr)

    return stdout, stderr, returncode


def format_jsonl_line(line: str, tool_name: str) -> str | None:
    """Extract content from JSONL line.

    Args:
        line: JSONL formatted line
        tool_name: Tool name ("claude")

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
