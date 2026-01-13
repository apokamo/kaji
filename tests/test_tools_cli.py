"""Tests for CLI utilities."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.core.tools._cli import format_jsonl_line, run_cli_streaming


class TestRunCliStreaming:
    """Tests for run_cli_streaming function."""

    def test_captures_stdout(self) -> None:
        """Standard output is captured."""
        stdout, stderr, returncode = run_cli_streaming(
            [sys.executable, "-c", "print('hello')"],
            verbose=False,
        )
        assert stdout.strip() == "hello"
        assert returncode == 0

    def test_captures_stderr(self) -> None:
        """Standard error output is captured."""
        stdout, stderr, returncode = run_cli_streaming(
            [sys.executable, "-c", "import sys; sys.stderr.write('error\\n')"],
            verbose=False,
        )
        assert stderr.strip() == "error"
        assert returncode == 0

    def test_returns_exit_code(self) -> None:
        """Exit code is returned."""
        stdout, stderr, returncode = run_cli_streaming(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
            verbose=False,
        )
        assert returncode == 42

    def test_raises_on_timeout(self) -> None:
        """Timeout raises TimeoutExpired."""
        with pytest.raises(subprocess.TimeoutExpired):
            run_cli_streaming(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=1,
                verbose=False,
            )

    def test_raises_on_command_not_found(self) -> None:
        """Command not found raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            run_cli_streaming(
                ["nonexistent_command_12345"],
                verbose=False,
            )

    def test_saves_logs_to_directory(self) -> None:
        """Logs are saved when log_dir is specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            run_cli_streaming(
                [sys.executable, "-c", "print('stdout'); import sys; sys.stderr.write('stderr')"],
                verbose=False,
                log_dir=log_dir,
            )
            stdout_log = log_dir / "stdout.log"
            stderr_log = log_dir / "stderr.log"
            assert stdout_log.exists()
            assert stderr_log.exists()
            assert "stdout" in stdout_log.read_text()
            assert "stderr" in stderr_log.read_text()


class TestFormatJsonlLine:
    """Tests for format_jsonl_line function."""

    def test_claude_result_type(self) -> None:
        """Extracts result from Claude result event."""
        line = '{"type":"result","result":"response text","session_id":"uuid"}'
        result = format_jsonl_line(line, "claude")
        assert result == "response text"

    def test_claude_assistant_type(self) -> None:
        """Extracts text from Claude assistant event."""
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
        result = format_jsonl_line(line, "claude")
        assert result == "hello"

    def test_claude_assistant_multiple_text(self) -> None:
        """Extracts multiple text blocks."""
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"line1"},{"type":"text","text":"line2"}]}}'
        result = format_jsonl_line(line, "claude")
        assert result == "line1\nline2"

    def test_claude_system_type_returns_none(self) -> None:
        """System events return None."""
        line = '{"type":"system","subtype":"init","session_id":"uuid"}'
        result = format_jsonl_line(line, "claude")
        assert result is None

    def test_invalid_json_returns_stripped_line(self) -> None:
        """Invalid JSON returns stripped line if non-empty."""
        line = "plain text output\n"
        result = format_jsonl_line(line, "claude")
        assert result == "plain text output"

    def test_empty_line_returns_none(self) -> None:
        """Empty line returns None."""
        result = format_jsonl_line("", "claude")
        assert result is None

    def test_whitespace_only_returns_none(self) -> None:
        """Whitespace-only line returns None."""
        result = format_jsonl_line("   \n", "claude")
        assert result is None

    def test_unknown_tool_returns_none(self) -> None:
        """Unknown tool returns None."""
        line = '{"type":"result","result":"text"}'
        result = format_jsonl_line(line, "unknown_tool")
        assert result is None

    def test_empty_result_returns_none(self) -> None:
        """Empty result field returns None."""
        line = '{"type":"result","result":""}'
        result = format_jsonl_line(line, "claude")
        assert result is None

    def test_assistant_without_content_returns_none(self) -> None:
        """Assistant without text content returns None."""
        line = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"test"}]}}'
        result = format_jsonl_line(line, "claude")
        assert result is None
