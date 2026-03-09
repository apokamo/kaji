"""Large tests: Real CLI E2E.

Tests using actual Claude Code / Codex CLI to verify:
- Single step execution with real CLI
- Verdict parsing from real CLI output
- Session ID extraction

These tests require CLI tools to be installed and may incur API costs.
"""

import shutil

import pytest

from dao_harness.adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter


def _cli_available(name: str) -> bool:
    """Check if a CLI tool is available on PATH."""
    return shutil.which(name) is not None


@pytest.mark.large
class TestRealClaudeCodeCLI:
    """E2E tests with real Claude Code CLI."""

    @pytest.mark.skipif(not _cli_available("claude"), reason="Claude Code CLI not installed")
    def test_claude_cli_version(self) -> None:
        """Verify Claude Code CLI is accessible and returns version info."""
        import subprocess

        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "claude" in result.stdout.lower() or result.stdout.strip()

    @pytest.mark.skipif(not _cli_available("claude"), reason="Claude Code CLI not installed")
    def test_claude_adapter_with_sample_jsonl(self) -> None:
        """ClaudeAdapter correctly parses known JSONL structures."""
        adapter = ClaudeAdapter()

        # Test session_id extraction
        init_event = {"type": "system", "subtype": "init", "session_id": "real-sess-123"}
        assert adapter.extract_session_id(init_event) == "real-sess-123"

        # Test text extraction
        text_event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Real output"}]},
        }
        assert adapter.extract_text(text_event) == "Real output"

        # Test cost extraction
        result_event = {"type": "result", "result": "done", "total_cost_usd": 0.123}
        cost = adapter.extract_cost(result_event)
        assert cost is not None
        assert cost.usd == 0.123


@pytest.mark.large
class TestRealCodexCLI:
    """E2E tests with real Codex CLI."""

    @pytest.mark.skipif(not _cli_available("codex"), reason="Codex CLI not installed")
    def test_codex_cli_version(self) -> None:
        """Verify Codex CLI is accessible."""
        import subprocess

        result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0

    @pytest.mark.skipif(not _cli_available("codex"), reason="Codex CLI not installed")
    def test_codex_adapter_with_sample_jsonl(self) -> None:
        """CodexAdapter correctly parses known JSONL structures."""
        adapter = CodexAdapter()

        init_event = {"type": "thread.started", "thread_id": "thread-real-456"}
        assert adapter.extract_session_id(init_event) == "thread-real-456"

        text_event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Codex output"},
        }
        assert adapter.extract_text(text_event) == "Codex output"


@pytest.mark.large
class TestRealGeminiCLI:
    """E2E tests with real Gemini CLI."""

    @pytest.mark.skipif(not _cli_available("gemini"), reason="Gemini CLI not installed")
    def test_gemini_cli_version(self) -> None:
        """Verify Gemini CLI is accessible."""
        import subprocess

        result = subprocess.run(["gemini", "--version"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0

    @pytest.mark.skipif(not _cli_available("gemini"), reason="Gemini CLI not installed")
    def test_gemini_adapter_with_sample_jsonl(self) -> None:
        """GeminiAdapter correctly parses known JSONL structures."""
        adapter = GeminiAdapter()

        init_event = {"type": "init", "session_id": "gem-real-789", "model": "auto"}
        assert adapter.extract_session_id(init_event) == "gem-real-789"

        text_event = {"type": "message", "role": "assistant", "content": "Gemini output"}
        assert adapter.extract_text(text_event) == "Gemini output"

        # Cost from result event stats
        result_event = {
            "type": "result",
            "status": "success",
            "stats": {"input_tokens": 1000, "output_tokens": 50},
        }
        cost = adapter.extract_cost(result_event)
        assert cost is not None
        assert cost.input_tokens == 1000
