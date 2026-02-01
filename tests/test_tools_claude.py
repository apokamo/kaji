"""Tests for ClaudeTool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.tools.claude import ClaudeTool
from src.core.tools.errors import (
    AIToolExecutionError,
    AIToolNotFoundError,
    AIToolTimeoutError,
)
from src.core.tools.protocol import AIToolProtocol


class TestClaudeToolInit:
    """Tests for ClaudeTool initialization."""

    def test_default_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default initialization values (without config.toml)."""
        # Move to tmp_path so no config.toml is found
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        tool = ClaudeTool()
        assert tool.model == "sonnet"
        assert tool.timeout == 600
        assert tool.permission_mode == "default"
        assert tool.skip_permissions is False
        assert tool.verbose is True

    def test_custom_values(self) -> None:
        """Custom initialization values."""
        tool = ClaudeTool(
            model="opus",
            timeout=300,
            permission_mode="bypassPermissions",
            skip_permissions=True,
            verbose=False,
        )
        assert tool.model == "opus"
        assert tool.timeout == 300
        assert tool.permission_mode == "bypassPermissions"
        assert tool.skip_permissions is True
        assert tool.verbose is False

    def test_implements_protocol(self) -> None:
        """ClaudeTool implements AIToolProtocol."""
        tool = ClaudeTool()
        _: AIToolProtocol = tool


class TestClaudeToolBuildCommand:
    """Tests for ClaudeTool command building."""

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_builds_basic_command(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Basic CLI command is built correctly."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        # Move to tmp_path so no config.toml is found
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        tool = ClaudeTool(verbose=False)
        tool.run("test prompt")

        args = mock_run.call_args[0][0]
        assert "claude" in args
        assert "-p" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--verbose" in args
        assert "--model" in args
        assert "sonnet" in args

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_includes_session_resume_flag(self, mock_run: MagicMock) -> None:
        """Session ID includes -r flag."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        tool = ClaudeTool(verbose=False)
        tool.run("prompt", session_id="existing-session")

        args = mock_run.call_args[0][0]
        assert "-r" in args
        assert "existing-session" in args

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_includes_permission_mode(self, mock_run: MagicMock) -> None:
        """Permission mode is included when not default."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        tool = ClaudeTool(permission_mode="bypassPermissions", verbose=False)
        tool.run("prompt")

        args = mock_run.call_args[0][0]
        assert "--permission-mode" in args
        assert "bypassPermissions" in args

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_includes_skip_permissions(self, mock_run: MagicMock) -> None:
        """Skip permissions flag is included when set."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        tool = ClaudeTool(skip_permissions=True, verbose=False)
        tool.run("prompt")

        args = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in args

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_includes_context_in_prompt(self, mock_run: MagicMock) -> None:
        """Context is included in prompt."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        tool = ClaudeTool(verbose=False)
        tool.run("test prompt", context="additional context")

        args = mock_run.call_args[0][0]
        # Prompt is the last argument
        prompt_arg = args[-1]
        assert "test prompt" in prompt_arg
        assert "Context:" in prompt_arg
        assert "additional context" in prompt_arg

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_includes_context_list(self, mock_run: MagicMock) -> None:
        """Context list is joined with newlines."""
        mock_run.return_value = (
            '{"type":"result","result":"response","session_id":"uuid"}',
            "",
            0,
        )
        tool = ClaudeTool(verbose=False)
        tool.run("prompt", context=["line1", "line2"])

        args = mock_run.call_args[0][0]
        prompt_arg = args[-1]
        assert "line1\nline2" in prompt_arg


class TestClaudeToolParsing:
    """Tests for ClaudeTool output parsing."""

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_parses_stream_json_output(self, mock_run: MagicMock) -> None:
        """Stream-json output is parsed correctly."""
        mock_run.return_value = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"thinking"}]}}\n'
            '{"type":"result","result":"final response","session_id":"new-uuid"}\n',
            "",
            0,
        )
        tool = ClaudeTool(verbose=False)
        response, session_id = tool.run("prompt")

        assert response == "final response"
        assert session_id == "new-uuid"

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_returns_raw_output_on_parse_failure(self, mock_run: MagicMock) -> None:
        """Returns raw output when JSON parsing fails."""
        mock_run.return_value = ("plain text response", "", 0)
        tool = ClaudeTool(verbose=False)
        response, session_id = tool.run("prompt", session_id="original")

        assert response == "plain text response"
        assert session_id == "original"

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_preserves_session_id_when_not_in_response(self, mock_run: MagicMock) -> None:
        """Original session ID preserved when not in response."""
        mock_run.return_value = ('{"type":"result","result":"response"}', "", 0)
        tool = ClaudeTool(verbose=False)
        _, session_id = tool.run("prompt", session_id="original-session")

        assert session_id == "original-session"


class TestClaudeToolErrors:
    """Tests for ClaudeTool error handling."""

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_handles_cli_not_found(self, mock_run: MagicMock) -> None:
        """CLI not found raises AIToolNotFoundError."""
        mock_run.side_effect = FileNotFoundError()
        tool = ClaudeTool(verbose=False)

        with pytest.raises(AIToolNotFoundError):
            tool.run("prompt")

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_handles_timeout(self, mock_run: MagicMock) -> None:
        """Timeout raises AIToolTimeoutError."""
        mock_run.side_effect = subprocess.TimeoutExpired(["claude"], 600)
        tool = ClaudeTool(timeout=600, verbose=False)

        with pytest.raises(AIToolTimeoutError) as exc_info:
            tool.run("prompt")

        assert "600s" in str(exc_info.value)

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_handles_non_zero_exit(self, mock_run: MagicMock) -> None:
        """Non-zero exit raises AIToolExecutionError."""
        mock_run.return_value = ("", "error message", 1)
        tool = ClaudeTool(verbose=False)

        with pytest.raises(AIToolExecutionError) as exc_info:
            tool.run("prompt")

        assert exc_info.value.returncode == 1
        assert exc_info.value.stderr == "error message"


class TestClaudeToolIntegration:
    """Integration tests for ClaudeTool."""

    @patch("src.core.tools.claude.run_cli_streaming")
    def test_full_workflow(self, mock_run: MagicMock) -> None:
        """Full workflow test."""
        mock_run.return_value = (
            '{"type":"result","result":"AI response","session_id":"session-123"}',
            "",
            0,
        )
        tool = ClaudeTool(model="haiku", timeout=300, verbose=False)
        response, session_id = tool.run("Hello", context="Some context")

        assert response == "AI response"
        assert session_id == "session-123"

        # Verify command structure
        args = mock_run.call_args[0][0]
        kwargs = mock_run.call_args[1]
        assert "--model" in args
        assert "haiku" in args
        assert kwargs["timeout"] == 300
        assert kwargs["verbose"] is False
        assert kwargs["tool_name"] == "claude"
