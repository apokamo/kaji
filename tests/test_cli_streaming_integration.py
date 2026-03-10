"""Medium tests: CLI streaming integration.

Mock CLI process that outputs JSONL, verifying stream_and_log() behavior:
- Immediate flush to raw log
- Adapter decode
- Console log output
- Timeout handling (threading.Event + SIGTERM → SIGKILL)
- CLINotFoundError on missing CLI
"""

import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dao_harness.adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
from dao_harness.cli import execute_cli, stream_and_log
from dao_harness.errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from dao_harness.models import Step


def _create_mock_cli_script(path: Path, jsonl_lines: list[str], exit_code: int = 0) -> Path:
    """Create a mock CLI script that outputs JSONL lines."""
    script = path / "mock_cli.sh"
    output = "\n".join(f"echo '{line}'" for line in jsonl_lines)
    script.write_text(f"#!/bin/bash\n{output}\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.medium
class TestStreamAndLog:
    """Mock CLI → stream_and_log() integration tests."""

    def test_claude_streaming_extracts_session_and_text(self, tmp_path: Path) -> None:
        """Claude JSONL stream produces correct CLIResult."""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-001"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Hello world"}]},
                }
            ),
            json.dumps({"type": "result", "result": "Done", "total_cost_usd": 0.05}),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        result = stream_and_log(process, adapter, "design", log_dir, verbose=False)
        process.wait()

        assert result.session_id == "sess-001"
        assert "Hello world" in result.full_output
        assert "Done" in result.full_output
        assert result.cost is not None
        assert result.cost.usd == 0.05

        # Verify raw log was written
        raw_log = (log_dir / "stdout.log").read_text()
        assert "system" in raw_log
        assert "assistant" in raw_log

    def test_codex_streaming_extracts_thread_id(self, tmp_path: Path) -> None:
        """Codex JSONL stream extracts thread_id as session_id."""
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "thread-abc"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Working on it"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = CodexAdapter()
        result = stream_and_log(process, adapter, "review", log_dir, verbose=False)
        process.wait()

        assert result.session_id == "thread-abc"
        assert "Working on it" in result.full_output
        assert result.cost is not None
        assert result.cost.input_tokens == 100

    def test_gemini_streaming(self, tmp_path: Path) -> None:
        """Gemini JSONL stream extracts session_id, text, and cost from stats."""
        jsonl_lines = [
            json.dumps({"type": "init", "session_id": "gem-xyz", "model": "auto"}),
            json.dumps({"type": "message", "role": "assistant", "content": "Gemini says hi"}),
            json.dumps(
                {
                    "type": "result",
                    "status": "success",
                    "stats": {"input_tokens": 500, "output_tokens": 20},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = GeminiAdapter()
        result = stream_and_log(process, adapter, "implement", log_dir, verbose=False)
        process.wait()

        assert result.session_id == "gem-xyz"
        assert "Gemini says hi" in result.full_output
        assert result.cost is not None
        assert result.cost.input_tokens == 500

    def test_console_log_written(self, tmp_path: Path) -> None:
        """Console log contains decoded text (not raw JSONL)."""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "decoded text"}]},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        console = (log_dir / "console.log").read_text()
        assert "decoded text" in console

    def test_stderr_captured(self, tmp_path: Path) -> None:
        """stderr from CLI process is captured in result and log."""
        script = tmp_path / "mock_cli.sh"
        script.write_text("#!/bin/bash\necho 'some error' >&2\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        assert "some error" in result.stderr

    def test_invalid_json_lines_skipped(self, tmp_path: Path) -> None:
        """Non-JSON lines in output are skipped without error."""
        jsonl_lines = [
            "not a json line",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "valid"}]},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        assert "valid" in result.full_output


@pytest.mark.medium
class TestExecuteCLI:
    """execute_cli() integration tests with mock CLI scripts."""

    def test_cli_not_found_raises_error(self, tmp_path: Path) -> None:
        """Non-existent CLI command raises CLINotFoundError."""
        step = Step(
            id="test",
            skill="test-skill",
            agent="claude",
            on={"PASS": "end"},
        )
        # Use a guaranteed-nonexistent command
        with patch(
            "dao_harness.cli.build_cli_args",
            return_value=["__nonexistent_cli_cmd_42__", "-p", "test"],
        ):
            with pytest.raises(CLINotFoundError):
                execute_cli(
                    step=step,
                    prompt="test prompt",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                )

    def test_nonzero_exit_raises_cli_execution_error(self, tmp_path: Path) -> None:
        """CLI exiting with non-zero code raises CLIExecutionError."""
        script = _create_mock_cli_script(tmp_path, [], exit_code=1)

        step = Step(
            id="test",
            skill="test-skill",
            agent="claude",
            on={"PASS": "end"},
        )

        with patch("dao_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="test prompt",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                )
            assert exc_info.value.step_id == "test"
            assert exc_info.value.returncode == 1

    def test_timeout_raises_step_timeout_error(self, tmp_path: Path) -> None:
        """CLI that exceeds timeout is killed and StepTimeoutError is raised."""
        script = tmp_path / "slow_cli.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        step = Step(
            id="slow-step",
            skill="test-skill",
            agent="claude",
            timeout=1,  # 1 second timeout
            on={"PASS": "end"},
        )

        with patch("dao_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(StepTimeoutError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="test prompt",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                )
            assert exc_info.value.step_id == "slow-step"
            assert exc_info.value.timeout == 1


@pytest.mark.medium
class TestExecuteCLISuccessFlow:
    """Successful execute_cli flow with mock CLI."""

    def test_successful_execution_returns_cli_result(self, tmp_path: Path) -> None:
        """Successful CLI execution returns CLIResult with parsed data."""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s-123"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "output text"}]},
                }
            ),
            json.dumps({"type": "result", "result": "final", "total_cost_usd": 0.01}),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines)

        step = Step(
            id="test-step",
            skill="test-skill",
            agent="claude",
            on={"PASS": "end"},
        )

        with patch("dao_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="test prompt",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
            )

        assert result.session_id == "s-123"
        assert "output text" in result.full_output
        assert result.cost is not None
        assert result.cost.usd == 0.01
