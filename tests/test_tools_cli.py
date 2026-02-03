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


class TestFormatJsonlLineGemini:
    """Tests for format_jsonl_line with Gemini format."""

    def test_gemini_response_type(self) -> None:
        """Extracts text from Gemini response event."""
        line = '{"type":"response","response":{"content":[{"type":"text","text":"Hello from Gemini"}]}}'
        result = format_jsonl_line(line, "gemini")
        assert result == "Hello from Gemini"

    def test_gemini_non_response_returns_none(self) -> None:
        """Non-response type returns None."""
        line = '{"type":"init","session_id":"abc123"}'
        result = format_jsonl_line(line, "gemini")
        assert result is None

    def test_gemini_empty_content_returns_none(self) -> None:
        """Empty content array returns None."""
        line = '{"type":"response","response":{"content":[]}}'
        result = format_jsonl_line(line, "gemini")
        assert result is None


class TestFormatJsonlLineCodex:
    """Tests for format_jsonl_line with Codex format."""

    def test_codex_reasoning_type(self) -> None:
        """Extracts text from Codex reasoning event."""
        line = '{"type":"item.completed","item":{"type":"reasoning","text":"Thinking about the problem"}}'
        result = format_jsonl_line(line, "codex")
        assert result == "Thinking about the problem"

    def test_codex_agent_message_type(self) -> None:
        """Extracts text from Codex agent_message event."""
        line = '{"type":"item.completed","item":{"type":"agent_message","text":"Hello from Codex"}}'
        result = format_jsonl_line(line, "codex")
        assert result == "Hello from Codex"

    def test_codex_non_item_completed_returns_none(self) -> None:
        """Non-item.completed type returns None."""
        line = '{"type":"thread.started","thread_id":"xyz789"}'
        result = format_jsonl_line(line, "codex")
        assert result is None

    def test_codex_command_execution_basic(self) -> None:
        """Extracts command execution output."""
        line = '{"type":"item.completed","item":{"type":"command_execution","command":"ls -la","aggregated_output":"total 100\\nfile1.txt\\nfile2.txt","exit_code":0}}'
        result = format_jsonl_line(line, "codex")
        assert result is not None
        assert "$ ls -la" in result
        assert "total 100" in result
        assert "file1.txt" in result

    def test_codex_command_execution_bash_format(self) -> None:
        """Extracts command from /bin/bash -lc format."""
        line = """{"type":"item.completed","item":{"type":"command_execution","command":"/bin/bash -lc 'cd /tmp && git status'","aggregated_output":"On branch main","exit_code":0}}"""
        result = format_jsonl_line(line, "codex")
        assert result is not None
        assert "$ git status" in result
        assert "On branch main" in result

    def test_codex_command_execution_truncates_long_output(self) -> None:
        """Truncates long command output."""
        long_output = "\\n".join([f"line{i}" for i in range(10)])
        line = f'{{"type":"item.completed","item":{{"type":"command_execution","command":"cat file","aggregated_output":"{long_output}","exit_code":0}}}}'
        result = format_jsonl_line(line, "codex")
        assert result is not None
        assert "$ cat file" in result
        assert "line0" in result
        assert "7 more lines" in result

    def test_codex_command_execution_shows_nonzero_exit(self) -> None:
        """Shows exit code for non-zero exits."""
        line = '{"type":"item.completed","item":{"type":"command_execution","command":"false","aggregated_output":"","exit_code":1}}'
        result = format_jsonl_line(line, "codex")
        assert result is not None
        assert "[exit: 1]" in result

    def test_codex_empty_text_returns_none(self) -> None:
        """Empty text field returns None."""
        line = '{"type":"item.completed","item":{"type":"reasoning","text":""}}'
        result = format_jsonl_line(line, "codex")
        assert result is None


class TestCliConsoleLog:
    """Tests for cli_console.log functionality."""

    def test_saves_cli_console_log_with_tool_name(self, monkeypatch) -> None:
        """cli_console.log is saved when tool_name is specified."""
        import subprocess as real_subprocess

        class MockProcess:
            def __init__(self, *args, **kwargs):
                self.stdout = iter(
                    [
                        '{"type":"response","response":{"content":[{"type":"text","text":"Line 1"}]}}\n',
                        '{"type":"response","response":{"content":[{"type":"text","text":"Line 2"}]}}\n',
                    ]
                )
                self.stderr = iter([])
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        monkeypatch.setattr(real_subprocess, "Popen", MockProcess)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            run_cli_streaming(["echo", "test"], log_dir=log_dir, tool_name="gemini", verbose=False)

            # stdout.log should have raw JSONL
            assert (log_dir / "stdout.log").exists()
            raw_content = (log_dir / "stdout.log").read_text()
            assert '"type":"response"' in raw_content

            # cli_console.log should have formatted content
            assert (log_dir / "cli_console.log").exists()
            console_content = (log_dir / "cli_console.log").read_text()
            assert "Line 1" in console_content
            assert "Line 2" in console_content
            assert '"type"' not in console_content  # No raw JSON

    def test_no_cli_console_log_without_tool_name(self, monkeypatch) -> None:
        """cli_console.log is NOT saved when tool_name is None."""
        import subprocess as real_subprocess

        class MockProcess:
            def __init__(self, *args, **kwargs):
                self.stdout = iter(["plain output\n"])
                self.stderr = iter([])
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        monkeypatch.setattr(real_subprocess, "Popen", MockProcess)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            run_cli_streaming(["echo", "test"], log_dir=log_dir, verbose=False)

            # stdout.log should exist
            assert (log_dir / "stdout.log").exists()
            # cli_console.log should NOT exist
            assert not (log_dir / "cli_console.log").exists()
