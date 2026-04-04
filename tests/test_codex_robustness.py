"""Tests for Codex adapter robustness improvements (Issue #122).

Bug 1: mcp_tool_call result=null causes AttributeError crash
Bug 2: Japanese text in mcp_tool_call result is decoded correctly
Bug 3: CLI error events included in CLIExecutionError message
Bug 4: Transient (capacity) errors trigger backoff retry
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.adapters import CodexAdapter
from kaji_harness.cli import _is_transient, execute_cli, stream_and_log
from kaji_harness.errors import CLIExecutionError
from kaji_harness.models import Step

# ==========================================
# Bug 1: null safety in extract_text()
# ==========================================


class TestCodexAdapterNullSafety:
    """Bug 1: CodexAdapter.extract_text() must not crash on result=null."""

    @pytest.fixture
    def adapter(self) -> CodexAdapter:
        return CodexAdapter()

    @pytest.mark.small
    def test_extract_text_with_null_result_returns_none(self, adapter: CodexAdapter) -> None:
        """mcp_tool_call with result=null must return None, not raise AttributeError."""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": None,
                "error": {"message": "resources/read failed"},
                "status": "failed",
            },
        }
        result = adapter.extract_text(event)
        assert result is None

    @pytest.mark.small
    def test_extract_text_with_missing_result_returns_none(self, adapter: CodexAdapter) -> None:
        """mcp_tool_call with no result key must return None."""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "status": "failed",
            },
        }
        result = adapter.extract_text(event)
        assert result is None

    @pytest.mark.small
    def test_extract_text_with_empty_result_returns_none(self, adapter: CodexAdapter) -> None:
        """mcp_tool_call with result={} (no content) must return None."""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {},
            },
        }
        result = adapter.extract_text(event)
        assert result is None

    @pytest.mark.small
    def test_extract_text_with_valid_result_returns_text(self, adapter: CodexAdapter) -> None:
        """mcp_tool_call with valid result still returns text."""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {"content": [{"type": "text", "text": "tool output"}]},
            },
        }
        result = adapter.extract_text(event)
        assert result == "tool output"


# ==========================================
# Bug 2: Unicode decoding in extract_text()
# ==========================================


class TestCodexAdapterUnicode:
    """Bug 2: Japanese text in mcp_tool_call result must be decoded correctly."""

    @pytest.fixture
    def adapter(self) -> CodexAdapter:
        return CodexAdapter()

    @pytest.mark.small
    def test_extract_text_japanese_decoded(self, adapter: CodexAdapter) -> None:
        """Japanese text in mcp_tool_call result content is returned as decoded string."""
        japanese_text = "こんにちは世界"
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "result": {"content": [{"type": "text", "text": japanese_text}]},
            },
        }
        result = adapter.extract_text(event)
        assert result == japanese_text

    @pytest.mark.small
    def test_extract_text_japanese_from_json_decoded(self, adapter: CodexAdapter) -> None:
        """Japanese text from JSON-parsed event (simulating JSONL decode) is correct."""
        japanese_text = "設計レビュー完了"
        raw_json = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "result": {"content": [{"type": "text", "text": japanese_text}]},
                },
            }
        )
        event = json.loads(raw_json)
        result = adapter.extract_text(event)
        assert result == japanese_text


# ==========================================
# Bug 3: error_messages in CLIExecutionError
# ==========================================


class TestIsTransient:
    """Bug 4: _is_transient() correctly identifies transient CLI errors."""

    @pytest.mark.small
    def test_capacity_error_is_transient(self) -> None:
        """'at capacity' matches transient pattern."""
        err = CLIExecutionError("step", 1, "Selected model is at capacity.")
        assert _is_transient(err) is True

    @pytest.mark.small
    def test_rate_limit_is_transient(self) -> None:
        """'rate limit' matches transient pattern."""
        err = CLIExecutionError("step", 1, "rate limit exceeded")
        assert _is_transient(err) is True

    @pytest.mark.small
    def test_overloaded_is_transient(self) -> None:
        """'overloaded' matches transient pattern."""
        err = CLIExecutionError("step", 1, "server overloaded")
        assert _is_transient(err) is True

    @pytest.mark.small
    def test_try_again_is_transient(self) -> None:
        """'try again' matches transient pattern."""
        err = CLIExecutionError("step", 1, "please try again later")
        assert _is_transient(err) is True

    @pytest.mark.small
    def test_permanent_error_not_transient(self) -> None:
        """Permanent errors do not match transient patterns."""
        err = CLIExecutionError("step", 1, "invalid API key")
        assert _is_transient(err) is False

    @pytest.mark.small
    def test_empty_message_not_transient(self) -> None:
        """Empty message does not match transient patterns."""
        err = CLIExecutionError("step", 1, "")
        assert _is_transient(err) is False

    @pytest.mark.small
    def test_case_insensitive_match(self) -> None:
        """Pattern matching is case insensitive."""
        err = CLIExecutionError("step", 1, "AT CAPACITY")
        assert _is_transient(err) is True


# ==========================================
# Medium: stream_and_log Bug 1+2 integration
# ==========================================


def _create_mock_cli_script(path: Path, jsonl_lines: list[str], exit_code: int = 0) -> Path:
    script = path / "mock_cli.sh"
    output = "\n".join(f"echo '{line}'" for line in jsonl_lines)
    script.write_text(f"#!/bin/bash\n{output}\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.medium
class TestStreamAndLogNullResult:
    """Bug 1+2: stream_and_log() must handle mcp_tool_call result=null without crashing."""

    def test_null_result_does_not_crash(self, tmp_path: Path) -> None:
        """JSONL stream with mcp_tool_call result=null completes without AttributeError."""
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "t-001"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "result": None,
                        "error": {"message": "resources/read failed: unknown MCP server"},
                        "status": "failed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Continuing despite tool error"},
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
        result = stream_and_log(process, adapter, "review-design", log_dir, verbose=False)
        process.wait()

        # Must not crash; agent_message text should be captured
        assert result.session_id == "t-001"
        assert "Continuing despite tool error" in result.full_output

    def test_japanese_text_in_console_log(self, tmp_path: Path) -> None:
        """Japanese text in mcp_tool_call result is decoded in console.log."""
        japanese_text = "設計レビュー完了"
        jsonl_lines = [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "result": {"content": [{"type": "text", "text": japanese_text}]},
                    },
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
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        console = (log_dir / "console.log").read_text(encoding="utf-8")
        assert japanese_text in console
        assert japanese_text in result.full_output


@pytest.mark.medium
class TestStreamAndLogErrorMessages:
    """Bug 3: stream_and_log() collects error events into CLIResult.error_messages."""

    def test_error_event_collected(self, tmp_path: Path) -> None:
        """type='error' event message is stored in CLIResult.error_messages."""
        jsonl_lines = [
            json.dumps({"type": "error", "message": "Selected model is at capacity."}),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = CodexAdapter()
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        assert any("at capacity" in m.lower() for m in result.error_messages)

    def test_turn_failed_event_collected(self, tmp_path: Path) -> None:
        """type='turn.failed' event error.message is stored in CLIResult.error_messages."""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "Selected model is at capacity."},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = CodexAdapter()
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        assert any("at capacity" in m.lower() for m in result.error_messages)

    def test_error_messages_in_cli_execution_error(self, tmp_path: Path) -> None:
        """CLIExecutionError message includes stdout error events when stderr is empty."""
        jsonl_lines = [
            json.dumps({"type": "error", "message": "Selected model is at capacity."}),
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "Selected model is at capacity."},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)

        step = Step(id="review-design", skill="test-skill", agent="codex", on={"PASS": "end"})

        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="test",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=1800,
                )
        assert "at capacity" in str(exc_info.value).lower()


@pytest.mark.medium
class TestExecuteCLIRetry:
    """Bug 4: execute_cli() retries on transient errors."""

    def test_retry_succeeds_on_second_attempt(self, tmp_path: Path) -> None:
        """CLI that fails with capacity error on 1st attempt succeeds on 2nd."""
        (tmp_path / "fail").mkdir()
        fail_script = _create_mock_cli_script(
            tmp_path / "fail",
            [
                json.dumps({"type": "error", "message": "Selected model is at capacity."}),
                json.dumps({"type": "turn.failed", "error": {"message": "at capacity"}}),
            ],
            exit_code=1,
        )

        (tmp_path / "success").mkdir()
        success_script = _create_mock_cli_script(
            tmp_path / "success",
            [
                json.dumps({"type": "thread.started", "thread_id": "t-retry"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "PASS after retry"},
                    }
                ),
            ],
            exit_code=0,
        )

        call_count = 0
        original_popen = subprocess.Popen

        def mock_popen(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return original_popen([str(fail_script)], **kwargs)  # type: ignore[return-value]
            return original_popen([str(success_script)], **kwargs)  # type: ignore[return-value]

        step = Step(id="review-design", skill="test-skill", agent="codex", on={"PASS": "end"})

        with patch("kaji_harness.cli.build_cli_args", return_value=["dummy"]):
            with patch("kaji_harness.cli.subprocess.Popen", side_effect=mock_popen):
                with patch("kaji_harness.cli.time.sleep"):  # don't actually sleep
                    result = execute_cli(
                        step=step,
                        prompt="test",
                        workdir=tmp_path,
                        session_id=None,
                        log_dir=tmp_path / "logs",
                        execution_policy="auto",
                        verbose=False,
                        default_timeout=1800,
                    )

        assert call_count == 2
        assert "PASS after retry" in result.full_output

    def test_non_transient_error_not_retried(self, tmp_path: Path) -> None:
        """Permanent errors are raised immediately without retry."""
        fail_script = _create_mock_cli_script(tmp_path, [], exit_code=1)

        step = Step(id="test", skill="test-skill", agent="codex", on={"PASS": "end"})
        call_count = 0
        original_popen = subprocess.Popen

        def mock_popen(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
            nonlocal call_count
            call_count += 1
            return original_popen([str(fail_script)], **kwargs)  # type: ignore[return-value]

        with patch("kaji_harness.cli.build_cli_args", return_value=["dummy"]):
            with patch("kaji_harness.cli.subprocess.Popen", side_effect=mock_popen):
                with pytest.raises(CLIExecutionError):
                    execute_cli(
                        step=step,
                        prompt="test",
                        workdir=tmp_path,
                        session_id=None,
                        log_dir=tmp_path / "logs",
                        execution_policy="auto",
                        verbose=False,
                        default_timeout=1800,
                    )

        assert call_count == 1  # no retry for permanent error

    def test_all_retries_exhausted_raises(self, tmp_path: Path) -> None:
        """All retries exhausted → final CLIExecutionError is raised."""
        fail_script = _create_mock_cli_script(
            tmp_path,
            [
                json.dumps({"type": "error", "message": "at capacity"}),
            ],
            exit_code=1,
        )

        step = Step(id="test", skill="test-skill", agent="codex", on={"PASS": "end"})
        original_popen = subprocess.Popen

        def mock_popen(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
            return original_popen([str(fail_script)], **kwargs)  # type: ignore[return-value]

        with patch("kaji_harness.cli.build_cli_args", return_value=["dummy"]):
            with patch("kaji_harness.cli.subprocess.Popen", side_effect=mock_popen):
                with patch("kaji_harness.cli.time.sleep"):
                    with pytest.raises(CLIExecutionError):
                        execute_cli(
                            step=step,
                            prompt="test",
                            workdir=tmp_path,
                            session_id=None,
                            log_dir=tmp_path / "logs",
                            execution_policy="auto",
                            verbose=False,
                            default_timeout=1800,
                        )
