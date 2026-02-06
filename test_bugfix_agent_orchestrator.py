"""
Bugfix Agent v5 Orchestrator - Unified Test Suite

テストカテゴリ:
- Tool Wrapper Tests (Phase 0): MockTool, GeminiTool, CodexTool, ClaudeTool
- State/Context Tests: AgentContext, SessionState, Factory functions
- CLI Parsing Tests (Phase 2): parse_args, ExecutionMode, ExecutionConfig
- State Handler Tests (Phase 3): 全10ステートのハンドラ単体テスト
- Edge Case Tests: ループカウンター、セッション管理、状態遷移
- Error Handling Tests: ToolError, check_tool_result
- Logging Tests: RunLogger
- Integration Tests: run() 実行モード
- Smoke Tests (CI skip): 実CLI呼び出し
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import allure
import pytest

# Ensure this directory is on sys.path to import target module
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bugfix_agent_orchestrator as mod

# Direct imports for Phase 2/3 tests
from bugfix_agent_orchestrator import (
    # Issue #292 Constants
    AI_FORMATTER_MAX_INPUT_CHARS,
    FORMATTER_PROMPT,
    RELAXED_PATTERNS,
    AgentAbortError,
    ExecutionConfig,
    ExecutionMode,
    InvalidVerdictValueError,  # Issue #292: 不正値は即 raise
    RunLogger,
    SessionState,
    State,
    # Error handling
    ToolError,
    # Issue #194 Protocol: Verdict classes
    Verdict,
    VerdictParseError,
    _extract_verdict_field,
    check_tool_result,
    create_ai_formatter,  # Issue #292: Step 3 用
    handle_abort_verdict,  # Issue #292 責務分離
    handle_detail_design,
    handle_detail_design_review,
    handle_implement,
    handle_implement_review,
    # State handlers (QA/QA_REVIEW removed - merged into IMPLEMENT_REVIEW)
    handle_init,
    handle_investigate,
    handle_investigate_review,
    handle_pr_create,
    infer_result_label,
    parse_args,
    parse_verdict,
    run,
)

# Test utilities
from tests.utils.context import create_test_context

# ==========================================
# Helper Functions
# ==========================================


def _fake_completed_process(stdout: str, returncode: int = 0):
    """Create a fake subprocess.CompletedProcess"""
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def _fake_streaming_result(stdout: str, stderr: str = "", returncode: int = 0):
    """Create a fake run_cli_streaming result (stdout, stderr, returncode)"""
    return (stdout, stderr, returncode)


# ==========================================
# MockTool Tests
# ==========================================


@allure.title("MockTool: returns predefined responses in order")
@allure.description("Verifies MockTool returns responses in sequence and generates session IDs.")
def test_mock_tool_responses():
    tool = mod.MockTool(["first", "second", "third"])

    resp1, sid1 = tool.run("prompt1")
    assert resp1 == "first"
    assert sid1 == "mock-session-1"

    resp2, sid2 = tool.run("prompt2", session_id=sid1)
    assert resp2 == "second"
    assert sid2 == sid1  # Session ID preserved when provided

    resp3, sid3 = tool.run("prompt3")
    assert resp3 == "third"
    assert sid3 == "mock-session-3"


@allure.title("MockTool: exhausted responses return default")
@allure.description("Verifies MockTool returns default when responses are exhausted.")
def test_mock_tool_exhausted():
    tool = mod.MockTool(["only"])
    _, _ = tool.run("use it")
    resp, sid = tool.run("exhausted")
    assert resp == "MOCK_RESPONSE"
    assert sid == "mock-session-2"


@allure.title("MockTool: empty response list")
@allure.description("Verifies MockTool handles empty response list.")
def test_mock_tool_empty_responses():
    tool = mod.MockTool([])
    resp, sid = tool.run("prompt")
    assert resp == "MOCK_RESPONSE"
    assert sid == "mock-session-1"


@allure.title("MockTool: context parameter ignored")
@allure.description("Verifies MockTool ignores context parameter.")
def test_mock_tool_ignores_context():
    tool = mod.MockTool(["response"])
    resp1, _ = tool.run("prompt", context="some context")
    resp2, _ = tool.run("prompt", context=["file1", "file2"])
    assert resp1 == "response"
    assert resp2 == "MOCK_RESPONSE"


# ==========================================
# GeminiTool Tests
# ==========================================


@allure.title("build_context: builds context from string")
@allure.description("Verifies build_context handles string context correctly.")
def test_gemini_tool_string_context():
    context = mod.build_context("test context")
    assert context == "test context"


@allure.title("build_context: builds context from empty string")
@allure.description("Verifies build_context handles empty string context.")
def test_gemini_tool_empty_string_context():
    context = mod.build_context("")
    assert context == ""


@allure.title("build_context: builds context from file list")
@allure.description("Verifies build_context reads files from list[str] context.")
def test_gemini_tool_file_context(tmp_path):
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")

    context = mod.build_context([str(file1), str(file2)], allowed_root=tmp_path)
    assert "content1" in context
    assert "content2" in context
    assert str(file1) in context
    assert str(file2) in context


@allure.title("build_context: handles non-existent files in context")
@allure.description("Verifies build_context skips non-existent files.")
def test_gemini_tool_nonexistent_files(tmp_path):
    existing = tmp_path / "exists.txt"
    existing.write_text("exists")
    missing = tmp_path / "missing.txt"  # This file doesn't exist

    context = mod.build_context([str(existing), str(missing)], allowed_root=tmp_path)
    assert "exists" in context
    # Check that missing file wasn't read (no header for it)
    assert f"--- {missing} ---" not in context


@allure.title("GeminiTool: successful run with new session")
@allure.description("Verifies GeminiTool creates new session and extracts response.")
def test_gemini_tool_run_success_new_session(monkeypatch):
    stdout = '\n'.join([
        '{"type": "init", "session_id": "gemini-123"}',
        '{"role": "assistant", "content": "analyzed result"}',
    ])

    def fake_streaming(args, **kwargs):
        assert "gemini" in args
        assert "-o" in args and "stream-json" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.GeminiTool(model="test-model")
    response, session_id = tool.run("analyze this", context="some context")

    assert response == "analyzed result"
    assert session_id == "gemini-123"


@allure.title("GeminiTool: successful run with session resume")
@allure.description("Verifies GeminiTool resumes existing session.")
def test_gemini_tool_run_resume_session(monkeypatch):
    stdout = '{"role": "assistant", "content": "continued"}'

    def fake_streaming(args, **kwargs):
        assert "-r" in args
        assert "gemini-123" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.GeminiTool()
    response, session_id = tool.run("continue", session_id="gemini-123")

    assert response == "continued"
    assert session_id == "gemini-123"


@allure.title("GeminiTool: handles subprocess error")
@allure.description("Verifies GeminiTool returns ERROR on subprocess failure.")
def test_gemini_tool_run_error(monkeypatch):
    def fake_streaming(args, **kwargs):
        return _fake_streaming_result("", stderr="error", returncode=1)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.GeminiTool()
    response, session_id = tool.run("fail")

    assert response == "ERROR"
    assert session_id is None


@allure.title("GeminiTool: handles malformed JSON")
@allure.description("Verifies GeminiTool handles malformed JSON gracefully.")
def test_gemini_tool_run_malformed_json(monkeypatch):
    stdout = '{invalid json}\n{"role": "assistant", "content": "ok"}'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.GeminiTool()
    response, session_id = tool.run("test")

    assert response == "ok"


@allure.title("GeminiTool: uses custom model")
@allure.description("Verifies GeminiTool passes custom model to CLI.")
def test_gemini_tool_custom_model(monkeypatch):
    captured_args = []

    def fake_streaming(args, **kwargs):
        captured_args.extend(args)
        return _fake_streaming_result('{"role": "assistant", "content": "ok"}')

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.GeminiTool(model="custom-model")
    tool.run("test")

    assert "-m" in captured_args
    assert "custom-model" in captured_args


@allure.title("GeminiTool: auto model skips -m flag")
@allure.description("Verifies GeminiTool with auto model doesn't pass -m flag.")
def test_gemini_tool_auto_model(monkeypatch):
    captured_args = []

    def fake_streaming(args, **kwargs):
        captured_args.extend(args)
        return _fake_streaming_result('{"role": "assistant", "content": "ok"}')

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.GeminiTool(model="auto")
    tool.run("test")

    assert "-m" not in captured_args


@allure.title("GeminiTool: handles JSON decode error in session extraction")
@allure.description("Verifies GeminiTool handles malformed session_id JSON.")
def test_gemini_tool_json_error_session(monkeypatch):
    # Malformed JSON in init line
    stdout = '{"type": "init", INVALID}\n{"role": "assistant", "content": "ok"}'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.GeminiTool()
    response, session_id = tool.run("test")

    assert response == "ok"
    # Session ID should remain None due to JSON error
    assert session_id is None


@allure.title("GeminiTool: handles JSON decode error in assistant message")
@allure.description("Verifies GeminiTool handles malformed assistant JSON.")
def test_gemini_tool_json_error_assistant(monkeypatch):
    # Valid init but malformed assistant message
    stdout = '{"type": "init", "session_id": "sid"}\n{"role": "assistant", INVALID}\n{"role": "assistant", "content": "final"}'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.GeminiTool()
    response, session_id = tool.run("test")

    # Should get the last valid message
    assert response == "final"
    assert session_id == "sid"


# ==========================================
# CodexTool Tests
# ==========================================


@allure.title("build_context: builds context from string (CodexTool)")
@allure.description("Verifies build_context handles string context correctly (duplicates GeminiTool test).")
def test_codex_tool_string_context():
    # Note: This tests the shared build_context() function
    context = mod.build_context("review this")
    assert context == "review this"


@allure.title("build_context: builds context from file list (CodexTool)")
@allure.description("Verifies build_context reads files from list[str] context (duplicates GeminiTool test).")
def test_codex_tool_file_context(tmp_path):
    file1 = tmp_path / "code.py"
    file1.write_text("def foo(): pass")

    # Note: This tests the shared build_context() function
    context = mod.build_context([str(file1)], allowed_root=tmp_path)
    assert "def foo(): pass" in context


@allure.title("CodexTool: successful run with new session")
@allure.description("Verifies CodexTool creates new session and extracts response.")
def test_codex_tool_run_success_new_session(monkeypatch):
    stdout = '\n'.join([
        '{"type":"thread.started","thread_id":"codex-456"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"PASS"}}',
    ])

    def fake_streaming(args, **kwargs):
        assert "codex" in args
        assert "exec" in args
        assert "-m" in args
        assert "gpt-5.1-codex" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.CodexTool()
    response, session_id = tool.run("review", context="code content")

    assert response == "PASS"
    assert session_id == "codex-456"


@allure.title("CodexTool: successful run with session resume")
@allure.description("Verifies CodexTool resumes existing session.")
def test_codex_tool_run_resume_session(monkeypatch):
    stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"BLOCKED"}}'

    def fake_streaming(args, **kwargs):
        assert "resume" in args
        assert "codex-456" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.CodexTool()
    response, session_id = tool.run("review again", session_id="codex-456")

    assert response == "BLOCKED"
    assert session_id == "codex-456"


@allure.title("CodexTool: handles subprocess error")
@allure.description("Verifies CodexTool returns ERROR on subprocess failure.")
def test_codex_tool_run_error(monkeypatch):
    def fake_streaming(args, **kwargs):
        return _fake_streaming_result("", stderr="error", returncode=1)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.CodexTool()
    response, session_id = tool.run("fail")

    assert response == "ERROR"
    assert session_id is None


@allure.title("CodexTool: fallback to raw stdout when no JSON")
@allure.description("Verifies CodexTool falls back to raw stdout when JSON parsing fails.")
def test_codex_tool_fallback_stdout(monkeypatch):
    stdout = "plain text output"

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.CodexTool()
    response, session_id = tool.run("test")

    assert response == "plain text output"
    assert session_id is None


@allure.title("CodexTool: context truncated to 4000 chars")
@allure.description("Verifies CodexTool truncates context to 4000 characters (config default).")
def test_codex_tool_context_truncation(monkeypatch):
    captured_prompt = []

    def fake_streaming(args, **kwargs):
        captured_prompt.append(args[-1])
        return _fake_streaming_result('{"type":"item.completed","item":{"text":"ok"}}')

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.CodexTool()
    long_context = "x" * 5000  # 5000 chars input
    tool.run("review", context=long_context)

    # Context should be truncated to 4000 chars (config default)
    # The captured prompt includes "review" + "\n\nTarget Content to Review:\n" + truncated context
    prompt_overhead = len("review\n\nTarget Content to Review:\n")
    assert len(captured_prompt[0]) < prompt_overhead + 4000 + 100  # Small buffer for overhead


@allure.title("CodexTool: custom workdir and sandbox")
@allure.description("Verifies CodexTool uses custom workdir and sandbox settings.")
def test_codex_tool_custom_settings(monkeypatch):
    captured_args = []

    def fake_streaming(args, **kwargs):
        captured_args.extend(args)
        return _fake_streaming_result('{"type":"item.completed","item":{"text":"ok"}}')

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.CodexTool(model="custom", workdir="/custom/dir", sandbox="read-only")
    tool.run("test")

    assert "custom" in captured_args
    assert "/custom/dir" in captured_args
    assert "read-only" in captured_args


@allure.title("CodexTool: handles JSON decode error gracefully")
@allure.description("Verifies CodexTool collects malformed JSON lines during parsing (for VERDICT fallback).")
def test_codex_tool_json_error_thread(monkeypatch):
    # Valid JSON with one malformed line - non-JSON lines are collected (not skipped)
    # This is intentional design for mcp_tool_call mode where VERDICT is plain text
    stdout = '{"type":"thread.started","thread_id":"tid123"}\nINVALID JSON LINE\n{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.CodexTool()
    response, session_id = tool.run("test")

    # Non-JSON lines are collected and joined with valid messages
    # See: E2E_TEST_FINDINGS.md section 3.1, 4.1
    assert response == "INVALID JSON LINE\n\nok"
    assert session_id == "tid123"


@allure.title("CodexTool: handles continue after JSON decode error")
@allure.description("Verifies CodexTool collects malformed JSON lines and continues parsing.")
def test_codex_tool_json_error_item(monkeypatch):
    # Multiple lines with one malformed - non-JSON lines are collected (not skipped)
    # This is intentional design for mcp_tool_call mode where VERDICT is plain text
    stdout = '{"type":"thread.started","thread_id":"tid"}\n{INVALID}\n{"type":"item.completed","item":{"type":"agent_message","text":"final"}}'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.CodexTool()
    response, session_id = tool.run("test")

    # Non-JSON lines are collected and joined with valid messages
    # See: E2E_TEST_FINDINGS.md section 3.1, 4.1
    assert response == "{INVALID}\n\nfinal"
    assert session_id == "tid"


# ==========================================
# ClaudeTool Tests
# ==========================================


@allure.title("build_context: builds context from string (ClaudeTool)")
@allure.description("Verifies build_context handles string context correctly (duplicates other tests).")
def test_claude_tool_string_context():
    # Note: This tests the shared build_context() function
    context = mod.build_context("implement this")
    assert context == "implement this"


@allure.title("build_context: builds context from file list (ClaudeTool)")
@allure.description("Verifies build_context reads files from list[str] context (duplicates other tests).")
def test_claude_tool_file_context(tmp_path):
    file1 = tmp_path / "spec.md"
    file1.write_text("# Specification")

    # Note: This tests the shared build_context() function
    context = mod.build_context([str(file1)], allowed_root=tmp_path)
    assert "# Specification" in context


@allure.title("ClaudeTool: successful run with stream-json output")
@allure.description("Verifies ClaudeTool parses stream-json output correctly.")
def test_claude_tool_run_success_json(monkeypatch):
    # stream-json 形式: result は文字列、session_id はトップレベル
    stdout = json.dumps({
        "type": "result",
        "result": "implemented successfully",
        "session_id": "claude-789"
    })

    def fake_streaming(args, **kwargs):
        assert "claude" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--verbose" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool()
    response, session_id = tool.run("implement feature")

    assert response == "implemented successfully"
    assert session_id == "claude-789"


@allure.title("ClaudeTool: successful run with session resume")
@allure.description("Verifies ClaudeTool resumes existing session.")
def test_claude_tool_run_resume_session(monkeypatch):
    # stream-json 形式: result は文字列、session_id はトップレベル
    stdout = json.dumps({
        "type": "result",
        "result": "continued",
        "session_id": "claude-789"
    })

    def fake_streaming(args, **kwargs):
        assert "-r" in args
        assert "claude-789" in args
        return _fake_streaming_result(stdout)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool()
    response, session_id = tool.run("continue", session_id="claude-789")

    assert response == "continued"
    assert session_id == "claude-789"


@allure.title("ClaudeTool: handles subprocess error")
@allure.description("Verifies ClaudeTool returns ERROR on subprocess failure.")
def test_claude_tool_run_error(monkeypatch):
    def fake_streaming(args, **kwargs):
        return _fake_streaming_result("some output", stderr="error", returncode=1)

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool()
    response, session_id = tool.run("fail")

    assert response == "ERROR"
    assert session_id is None


@allure.title("ClaudeTool: fallback to raw stdout when JSON parse fails")
@allure.description("Verifies ClaudeTool falls back to raw stdout on JSON error.")
def test_claude_tool_fallback_stdout(monkeypatch):
    stdout = "not valid json"

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(stdout))

    tool = mod.ClaudeTool()
    response, session_id = tool.run("test")

    assert response == "not valid json"
    assert session_id is None


@allure.title("ClaudeTool: uses custom model and permission mode")
@allure.description("Verifies ClaudeTool passes custom settings to CLI.")
def test_claude_tool_custom_settings(monkeypatch):
    captured_args = []

    def fake_streaming(args, **kwargs):
        captured_args.extend(args)
        return _fake_streaming_result(json.dumps({"result": {"text": "ok"}}))

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool(model="sonnet", permission_mode="allow-all")
    tool.run("test")

    assert "--model" in captured_args
    assert "sonnet" in captured_args
    assert "--permission-mode" in captured_args
    assert "allow-all" in captured_args


@allure.title("ClaudeTool: default permission mode skips flag")
@allure.description("Verifies ClaudeTool with default permission mode doesn't add flag.")
def test_claude_tool_default_permission(monkeypatch):
    captured_args = []

    def fake_streaming(args, **kwargs):
        captured_args.extend(args)
        return _fake_streaming_result(json.dumps({"result": {"text": "ok"}}))

    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool(permission_mode="default")
    tool.run("test")

    assert "--permission-mode" not in captured_args


@allure.title("ClaudeTool: creates debug and cache directories")
@allure.description("Verifies ClaudeTool creates necessary directories.")
def test_claude_tool_creates_directories(monkeypatch, tmp_path):
    env_copy = {
        "CLAUDE_DEBUG_DIR": str(tmp_path / "debug"),
        "CLAUDE_CACHE_DIR": str(tmp_path / "cache"),
    }

    def fake_streaming(args, **kwargs):
        assert (tmp_path / "debug").exists()
        assert (tmp_path / "cache").exists()
        return _fake_streaming_result(json.dumps({"result": {"text": "ok"}}))

    monkeypatch.setattr(mod.os, "environ", env_copy)
    monkeypatch.setattr(mod, "run_cli_streaming", fake_streaming)

    tool = mod.ClaudeTool()
    tool.run("test")


@allure.title("ClaudeTool: handles dirty input with warning prefix")
@allure.description("Verifies ClaudeTool extracts JSON from output with warning prefix.")
def test_claude_tool_dirty_input_warning_prefix(monkeypatch):
    """ダーティ入力: 警告 + JSON のケース"""
    dirty_stdout = 'Warning: Something happened\n' + json.dumps({
        "result": {
            "text": "extracted response",
            "session_id": "session-from-dirty"
        }
    })

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(dirty_stdout))

    tool = mod.ClaudeTool()
    response, session_id = tool.run("test")

    assert response == "extracted response"
    assert session_id == "session-from-dirty"


@allure.title("ClaudeTool: handles multiline warning before JSON")
@allure.description("Verifies ClaudeTool extracts JSON even with multiple warning lines.")
def test_claude_tool_dirty_input_multiline_warning(monkeypatch):
    """ダーティ入力: 複数行警告 + JSON のケース"""
    dirty_stdout = 'Warning line 1\nWarning line 2\nSome debug info\n' + json.dumps({
        "result": {
            "text": "response after warnings",
            "session_id": "multi-warn-session"
        }
    })

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(dirty_stdout))

    tool = mod.ClaudeTool()
    response, session_id = tool.run("test")

    assert response == "response after warnings"
    assert session_id == "multi-warn-session"


@allure.title("ClaudeTool: handles JSON with trailing garbage")
@allure.description("Verifies ClaudeTool extracts JSON even with trailing non-JSON content.")
def test_claude_tool_dirty_input_trailing_garbage(monkeypatch):
    """ダーティ入力: JSON + 後続ゴミのケース"""
    dirty_stdout = json.dumps({
        "result": {
            "text": "valid response",
            "session_id": "trailing-session"
        }
    }) + '\nSome trailing output'

    monkeypatch.setattr(mod, "run_cli_streaming", lambda *a, **k: _fake_streaming_result(dirty_stdout))

    tool = mod.ClaudeTool()
    response, session_id = tool.run("test")

    assert response == "valid response"
    assert session_id == "trailing-session"


# ==========================================
# CLI Streaming Log Tests
# ==========================================


@allure.title("run_cli_streaming: saves logs when log_dir specified")
@allure.description("Verifies stdout.log and stderr.log are saved when log_dir is provided.")
def testrun_cli_streaming_saves_logs(tmp_path, monkeypatch):
    """run_cli_streaming: log_dir が指定された場合にログを保存"""

    # Mock subprocess.Popen
    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(["stdout line 1\n", "stdout line 2\n"])
            self.stderr = iter(["stderr line\n"])
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("subprocess.Popen", MockProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)  # verbose=False

    log_dir = tmp_path / "test_logs"
    stdout, stderr, returncode = mod.run_cli_streaming(
        ["echo", "test"], log_dir=log_dir
    )

    assert stdout == "stdout line 1\nstdout line 2\n"
    assert stderr == "stderr line\n"
    assert returncode == 0
    assert (log_dir / "stdout.log").exists()
    assert (log_dir / "stderr.log").exists()
    assert (log_dir / "stdout.log").read_text() == "stdout line 1\nstdout line 2\n"
    assert (log_dir / "stderr.log").read_text() == "stderr line\n"


@allure.title("run_cli_streaming: no logs when log_dir is None")
@allure.description("Verifies no log files are created when log_dir is None.")
def testrun_cli_streaming_no_logs_without_log_dir(tmp_path, monkeypatch):
    """run_cli_streaming: log_dir が None の場合はログを保存しない"""

    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(["output\n"])
            self.stderr = iter([])
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("subprocess.Popen", MockProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)

    stdout, stderr, returncode = mod.run_cli_streaming(["echo", "test"])

    assert stdout == "output\n"
    assert returncode == 0
    # No log files should be created in tmp_path
    assert not (tmp_path / "stdout.log").exists()


@allure.title("Tool.run: passes log_dir to run_cli_streaming")
@allure.description("Verifies GeminiTool passes log_dir parameter correctly.")
def test_gemini_tool_passes_log_dir(tmp_path, monkeypatch):
    """GeminiTool: log_dir パラメータを run_cli_streaming に渡す"""
    captured_log_dir = []

    def mock_streaming(args, timeout=None, log_dir=None, **kwargs):
        captured_log_dir.append(log_dir)
        stdout = '{"type": "init", "session_id": "test-session"}\n{"role": "assistant", "content": "response"}'
        return stdout, "", 0

    monkeypatch.setattr(mod, "run_cli_streaming", mock_streaming)

    tool = mod.GeminiTool()
    log_dir = tmp_path / "gemini_logs"
    tool.run("test prompt", log_dir=log_dir)

    assert len(captured_log_dir) == 1
    assert captured_log_dir[0] == log_dir


# ==========================================
# Console Formatting Tests
# ==========================================


@allure.title("format_jsonl_line: Gemini format extraction")
@allure.description("Verifies content extraction from Gemini JSONL format.")
def testformat_jsonl_line_gemini():
    """format_jsonl_line: Gemini 形式からコンテンツを抽出"""
    # Gemini response format
    line = '{"type":"response","response":{"content":[{"type":"text","text":"Hello from Gemini"}]}}'
    result = mod.format_jsonl_line(line, "gemini")
    assert result == "Hello from Gemini"

    # Non-response type should return None
    line = '{"type":"init","session_id":"abc123"}'
    result = mod.format_jsonl_line(line, "gemini")
    assert result is None


@allure.title("format_jsonl_line: Codex format extraction")
@allure.description("Verifies content extraction from Codex JSONL format.")
def testformat_jsonl_line_codex():
    """format_jsonl_line: Codex 形式からコンテンツを抽出"""
    # Codex reasoning format
    line = '{"type":"item.completed","item":{"type":"reasoning","text":"Thinking about the problem"}}'
    result = mod.format_jsonl_line(line, "codex")
    assert result == "Thinking about the problem"

    # Codex agent_message format
    line = '{"type":"item.completed","item":{"type":"agent_message","text":"Hello from Codex"}}'
    result = mod.format_jsonl_line(line, "codex")
    assert result == "Hello from Codex"

    # Non-item.completed type should return None
    line = '{"type":"thread.started","thread_id":"xyz789"}'
    result = mod.format_jsonl_line(line, "codex")
    assert result is None

    # command_execution: コマンド + 出力の先頭行を表示
    line = '{"type":"item.completed","item":{"type":"command_execution","command":"ls -la","aggregated_output":"total 100\\nfile1.txt\\nfile2.txt","exit_code":0}}'
    result = mod.format_jsonl_line(line, "codex")
    assert "$ ls -la" in result
    assert "total 100" in result
    assert "file1.txt" in result

    # command_execution: /bin/bash -lc 形式のコマンドを整形
    line = """{"type":"item.completed","item":{"type":"command_execution","command":"/bin/bash -lc 'cd /tmp && git status'","aggregated_output":"On branch main","exit_code":0}}"""
    result = mod.format_jsonl_line(line, "codex")
    assert "$ git status" in result
    assert "On branch main" in result

    # command_execution: 出力が多い場合は truncate
    long_output = "\\n".join([f"line{i}" for i in range(10)])
    line = f'{{"type":"item.completed","item":{{"type":"command_execution","command":"cat file","aggregated_output":"{long_output}","exit_code":0}}}}'
    result = mod.format_jsonl_line(line, "codex")
    assert "$ cat file" in result
    assert "line0" in result
    assert "7 more lines" in result  # 10 - 3 = 7

    # command_execution: exit_code が非0の場合は表示
    line = '{"type":"item.completed","item":{"type":"command_execution","command":"false","aggregated_output":"","exit_code":1}}'
    result = mod.format_jsonl_line(line, "codex")
    assert "[exit: 1]" in result


@allure.title("format_jsonl_line: Claude stream-json format extraction")
@allure.description("Verifies content extraction from Claude stream-json format.")
def testformat_jsonl_line_claude():
    """format_jsonl_line: Claude stream-json 形式からコンテンツを抽出"""
    # Claude stream-json result format (type: result)
    line = '{"type":"result","result":"Hello from Claude","session_id":"abc"}'
    result = mod.format_jsonl_line(line, "claude")
    assert result == "Hello from Claude"

    # Claude stream-json assistant format (type: assistant with content array)
    line = '{"type":"assistant","message":{"content":[{"type":"text","text":"Processing..."}]}}'
    result = mod.format_jsonl_line(line, "claude")
    assert result == "Processing..."

    # Claude stream-json system format (should be skipped)
    line = '{"type":"system","subtype":"init","session_id":"abc"}'
    result = mod.format_jsonl_line(line, "claude")
    assert result is None


@allure.title("format_jsonl_line: non-JSON line")
@allure.description("Verifies non-JSON lines are returned as-is if not empty.")
def testformat_jsonl_line_non_json():
    """format_jsonl_line: JSON でない行はそのまま返す"""
    line = "Plain text message\n"
    result = mod.format_jsonl_line(line, "gemini")
    assert result == "Plain text message"

    # Empty line should return None
    line = "   \n"
    result = mod.format_jsonl_line(line, "gemini")
    assert result is None


@allure.title("run_cli_streaming: saves cli_console.log with tool_name")
@allure.description("Verifies cli_console.log is saved when tool_name is specified.")
def testrun_cli_streaming_saves_console_log(tmp_path, monkeypatch):
    """run_cli_streaming: tool_name 指定時に cli_console.log を保存"""

    class MockProcess:
        def __init__(self, *args, **kwargs):
            # Gemini format JSONL
            self.stdout = iter([
                '{"type":"response","response":{"content":[{"type":"text","text":"Line 1"}]}}\n',
                '{"type":"response","response":{"content":[{"type":"text","text":"Line 2"}]}}\n',
            ])
            self.stderr = iter([])
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("subprocess.Popen", MockProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)  # verbose=False

    log_dir = tmp_path / "console_logs"
    stdout, stderr, returncode = mod.run_cli_streaming(
        ["echo", "test"], log_dir=log_dir, tool_name="gemini"
    )

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


@allure.title("run_cli_streaming: no cli_console.log without tool_name")
@allure.description("Verifies cli_console.log is not saved when tool_name is None.")
def testrun_cli_streaming_no_console_log_without_tool_name(tmp_path, monkeypatch):
    """run_cli_streaming: tool_name なしの場合は cli_console.log を保存しない"""

    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(["plain output\n"])
            self.stderr = iter([])
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("subprocess.Popen", MockProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)

    log_dir = tmp_path / "no_console_logs"
    mod.run_cli_streaming(["echo", "test"], log_dir=log_dir)

    # stdout.log should exist
    assert (log_dir / "stdout.log").exists()
    # cli_console.log should NOT exist
    assert not (log_dir / "cli_console.log").exists()


# ==========================================
# Config Tests
# ==========================================


@allure.title("get_config_value: returns default when key missing")
@allure.description("Verifies get_config_value returns default for missing keys.")
def test_get_config_value_missing_key():
    """存在しないキーの場合はデフォルト値を返す"""
    # キャッシュをクリア
    mod.load_config.cache_clear()
    result = mod.get_config_value("nonexistent.key", "default_value")
    assert result == "default_value"


@allure.title("get_workdir: returns auto-detected path")
@allure.description("Verifies get_workdir returns auto-detected path when not configured.")
def test_get_workdir_auto_detect(monkeypatch):
    """workdir が設定されていない場合は自動検出"""
    monkeypatch.delenv("BUGFIX_AGENT_WORKDIR", raising=False)
    mod.load_config.cache_clear()

    workdir = mod.get_workdir()
    # 自動検出されたパスは Path オブジェクト
    assert isinstance(workdir, Path)


# ==========================================
# ReviewResult Tests
# ==========================================


@allure.title("ReviewResult.contains: detects PASS in text")
@allure.description("Verifies ReviewResult.contains correctly detects keywords.")
def test_review_result_contains():
    """ReviewResult.contains が正しく判定する"""
    assert mod.ReviewResult.contains("PASS: All good", mod.ReviewResult.PASS) is True
    assert mod.ReviewResult.contains("BLOCKED: Missing info", mod.ReviewResult.BLOCKED) is True
    assert mod.ReviewResult.contains("FIX_REQUIRED: Bug found", mod.ReviewResult.FIX_REQUIRED) is True
    assert mod.ReviewResult.contains("DESIGN_FIX: Need redesign", mod.ReviewResult.DESIGN_FIX) is True

    # 含まれていない場合
    assert mod.ReviewResult.contains("PASS: All good", mod.ReviewResult.BLOCKED) is False


# ==========================================
# LoopLimitExceeded Tests
# ==========================================


@allure.title("run: raises LoopLimitExceeded on circuit breaker")
@allure.description("Verifies run() raises LoopLimitExceeded when loop limit exceeded.")
def test_run_raises_loop_limit_exceeded():
    """Circuit Breaker でループ制限超過時に例外を投げる"""
    config = ExecutionConfig(
        mode=ExecutionMode.FULL,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    # INVESTIGATE_REVIEW が常に RETRY を返す → 無限ループ
    verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
    verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: 不足\n- Suggestion: 再調査"
    ctx = create_test_context(
        analyzer_responses=["investigate"] * 10,
        reviewer_responses=[verdict_pass] + [verdict_retry] * 10,  # INIT は通過、その後 RETRY
        implementer_responses=[],
        issue_url=config.issue_url,
        run_timestamp="2511291400",
    )

    # max_loop_count をモックで 3 に設定
    with patch.object(mod, "get_config_value", side_effect=lambda k, d: 3 if k == "agent.max_loop_count" else d):
        with pytest.raises(mod.LoopLimitExceeded) as exc_info:
            run(config, ctx=ctx)
        assert "Investigate_Loop" in str(exc_info.value)
        assert "3" in str(exc_info.value)


# ==========================================
# SessionState Tests
# ==========================================


@allure.title("SessionState: default initialization")
@allure.description("Verifies SessionState dataclass default values.")
def test_session_state_defaults():
    state = mod.SessionState()
    assert state.current_state == mod.State.INIT
    assert state.completed_states == []
    assert state.loop_counters["Investigate_Loop"] == 0
    assert state.loop_counters["Detail_Design_Loop"] == 0
    assert state.loop_counters["Implement_Loop"] == 0
    # NOTE: QA_Loop removed - merged into IMPLEMENT_REVIEW (Issue #194)
    assert state.active_conversations["Design_Thread_conversation_id"] is None
    assert state.active_conversations["Implement_Loop_conversation_id"] is None


@allure.title("SessionState: mutable defaults are independent")
@allure.description("Verifies SessionState instances have independent mutable defaults.")
def test_session_state_independent_defaults():
    state1 = mod.SessionState()
    state2 = mod.SessionState()

    state1.completed_states.append("TEST")
    state1.loop_counters["Investigate_Loop"] = 5

    assert state2.completed_states == []
    assert state2.loop_counters["Investigate_Loop"] == 0


# ==========================================
# AgentContext Tests
# ==========================================


@allure.title("AgentContext: artifacts_dir property")
@allure.description("Verifies AgentContext correctly builds artifacts directory path.")
def test_agent_context_artifacts_dir():
    ctx = create_test_context([], [], [], run_timestamp="2511281430")
    artifacts_dir = ctx.artifacts_dir
    assert "999" in str(artifacts_dir)
    assert "2511281430" in str(artifacts_dir)
    assert "test-artifacts/bugfix-agent" in str(artifacts_dir)


@allure.title("AgentContext: artifacts_state_dir method")
@allure.description("Verifies AgentContext correctly builds state-specific directory path.")
def test_agent_context_artifacts_state_dir():
    ctx = create_test_context([], [], [], run_timestamp="2511281430")

    investigate_dir = ctx.artifacts_state_dir("INVESTIGATE")
    assert "investigate" in str(investigate_dir).lower()
    assert "999" in str(investigate_dir)

    qa_dir = ctx.artifacts_state_dir("QA")
    assert "qa" in str(qa_dir).lower()


# ==========================================
# Factory Functions Tests
# ==========================================


@allure.title("create_default_context: extracts issue number from URL")
@allure.description("Verifies factory function correctly parses issue URL.")
def test_create_default_context_issue_parsing():
    ctx = mod.create_default_context("https://github.com/apokamo/kamo2/issues/182")
    assert ctx.issue_number == 182
    assert ctx.issue_url == "https://github.com/apokamo/kamo2/issues/182"
    assert isinstance(ctx.analyzer, mod.ClaudeTool)  # Changed: Gemini → Claude
    assert isinstance(ctx.reviewer, mod.CodexTool)
    assert isinstance(ctx.implementer, mod.ClaudeTool)
    assert ctx.run_timestamp != ""
    assert len(ctx.run_timestamp) == 10  # YYMMDDhhmm format


@allure.title("create_default_context: handles trailing slash in URL")
@allure.description("Verifies factory handles URLs with trailing slashes.")
def test_create_default_context_trailing_slash():
    ctx = mod.create_default_context("https://github.com/apokamo/kamo2/issues/182/")
    assert ctx.issue_number == 182


@allure.title("create_default_context: tool override")
@allure.description("Verifies create_default_context applies tool_override.")
def test_create_default_context_tool_override():
    """tool_override: 全ロールで同一ツール使用"""
    ctx = mod.create_default_context(
        "https://github.com/apokamo/kamo2/issues/182",
        tool_override="codex"
    )
    # 全ロールが同じ CodexTool インスタンス
    assert isinstance(ctx.analyzer, mod.CodexTool)
    assert isinstance(ctx.reviewer, mod.CodexTool)
    assert isinstance(ctx.implementer, mod.CodexTool)
    assert ctx.analyzer is ctx.reviewer  # same instance


@allure.title("create_default_context: tool and model override")
@allure.description("Verifies create_default_context applies tool_override and model_override.")
def test_create_default_context_tool_model_override():
    """tool_override + model_override"""
    ctx = mod.create_default_context(
        "https://github.com/apokamo/kamo2/issues/182",
        tool_override="gemini",
        model_override="gemini-2.5-flash"
    )
    # Note: tool_override="gemini" なので GeminiTool が期待される
    assert isinstance(ctx.analyzer, mod.GeminiTool)
    assert ctx.analyzer.model == "gemini-2.5-flash"


@allure.title("_create_tool: creates correct tool instances")
@allure.description("Verifies _create_tool factory function.")
def test_create_tool_factory():
    """_create_tool: 各ツールタイプを正しく生成"""
    codex = mod._create_tool("codex")
    assert isinstance(codex, mod.CodexTool)

    gemini = mod._create_tool("gemini")
    assert isinstance(gemini, mod.GeminiTool)

    claude = mod._create_tool("claude")
    assert isinstance(claude, mod.ClaudeTool)


@allure.title("_create_tool: with model override")
@allure.description("Verifies _create_tool applies model override.")
def test_create_tool_with_model():
    """_create_tool: モデル指定"""
    codex = mod._create_tool("codex", "o4-mini")
    assert codex.model == "o4-mini"

    gemini = mod._create_tool("gemini", "gemini-2.5-flash")
    assert gemini.model == "gemini-2.5-flash"


@allure.title("_create_tool: unknown tool raises ValueError")
@allure.description("Verifies _create_tool raises ValueError for unknown tool.")
def test_create_tool_unknown():
    """_create_tool: 不明なツールでエラー"""
    with pytest.raises(ValueError, match="Unknown tool"):
        mod._create_tool("unknown")


@allure.title("create_test_context: injects MockTools")
@allure.description("Verifies test factory function creates MockTools correctly.")
def test_create_test_context():
    ctx = create_test_context(
        analyzer_responses=["analyze1", "analyze2"],
        reviewer_responses=["PASS", "BLOCKED"],
        implementer_responses=["implemented"],
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        run_timestamp="2511281430",
    )

    assert ctx.issue_number == 999
    assert ctx.run_timestamp == "2511281430"
    assert isinstance(ctx.analyzer, mod.MockTool)
    assert isinstance(ctx.reviewer, mod.MockTool)
    assert isinstance(ctx.implementer, mod.MockTool)

    # Test mock responses work
    resp1, _ = ctx.analyzer.run("test")
    assert resp1 == "analyze1"
    resp2, _ = ctx.reviewer.run("test")
    assert resp2 == "PASS"


@allure.title("create_test_context: default values")
@allure.description("Verifies test factory uses default values correctly.")
def test_create_test_context_defaults():
    ctx = create_test_context([], [], [])
    assert ctx.issue_number == 999  # default
    assert ctx.run_timestamp == "2511281430"  # default


# ==========================================
# State Enum Tests
# ==========================================


@allure.title("State: enum completeness")
@allure.description("Verifies all expected states are defined.")
def test_state_enum_completeness():
    # Issue #194: 9 states (QA/QA_REVIEW merged into IMPLEMENT_REVIEW)
    expected_states = [
        "INIT",
        "INVESTIGATE",
        "INVESTIGATE_REVIEW",
        "DETAIL_DESIGN",
        "DETAIL_DESIGN_REVIEW",
        "IMPLEMENT",
        "IMPLEMENT_REVIEW",
        "PR_CREATE",
        "COMPLETE",
    ]
    for state_name in expected_states:
        assert hasattr(mod.State, state_name)
    # Verify removed states don't exist
    assert not hasattr(mod.State, "QA")
    assert not hasattr(mod.State, "QA_REVIEW")


@allure.title("State: enum values are unique")
@allure.description("Verifies all state enum values are unique.")
def test_state_enum_unique():
    values = [state.value for state in mod.State]
    assert len(values) == len(set(values))


# ==========================================
# CLI Parsing Tests (Phase 2)
# ==========================================


@allure.title("parse_args: FULL mode (default)")
@allure.description("Verifies parse_args returns FULL mode when no --state or --from is specified.")
def test_parse_args_default_full_mode():
    """デフォルト（FULL モード）のパース確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()

    assert config.mode == ExecutionMode.FULL
    assert config.target_state is None
    assert config.issue_url == "https://github.com/apokamo/kamo2/issues/182"
    assert config.issue_number == 182


@allure.title("parse_args: SINGLE mode")
@allure.description("Verifies parse_args returns SINGLE mode with --state option.")
def test_parse_args_single_mode():
    """SINGLE モード（--state）のパース確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INVESTIGATE"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()

    assert config.mode == ExecutionMode.SINGLE
    assert config.target_state == State.INVESTIGATE
    assert config.issue_number == 182


@allure.title("parse_args: FROM_END mode")
@allure.description("Verifies parse_args returns FROM_END mode with --from option.")
def test_parse_args_from_end_mode():
    """FROM_END モード（--from）のパース確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--from", "IMPLEMENT"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()

    assert config.mode == ExecutionMode.FROM_END
    assert config.target_state == State.IMPLEMENT
    assert config.issue_number == 182


@allure.title("parse_args: short options")
@allure.description("Verifies parse_args handles -i, -s, -f short options.")
def test_parse_args_short_options():
    """短縮オプション（-i, -s, -f）のパース確認"""
    # Issue #194: QA removed, use IMPLEMENT instead
    test_cases = [
        (["-i", "https://github.com/apokamo/kamo2/issues/999"], ExecutionMode.FULL, None),
        (["-i", "https://github.com/apokamo/kamo2/issues/999", "-s", "IMPLEMENT"], ExecutionMode.SINGLE, State.IMPLEMENT),
        (["-i", "https://github.com/apokamo/kamo2/issues/999", "-f", "PR_CREATE"], ExecutionMode.FROM_END, State.PR_CREATE),
    ]

    for args, expected_mode, expected_state in test_cases:
        test_args = ["bugfix_agent_orchestrator.py"] + args

        with patch("sys.argv", test_args):
            config = parse_args()

        assert config.mode == expected_mode
        assert config.target_state == expected_state


@allure.title("parse_args: --list-states")
@allure.description("Verifies parse_args handles --list-states option.")
def test_parse_args_list_states():
    """--list-states オプションのパース確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--list-states"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()

    assert config.issue_url == "__LIST_STATES__"


@allure.title("parse_args: mutual exclusion")
@allure.description("Verifies parse_args rejects --state and --from together.")
def test_parse_args_mutual_exclusion():
    """--state と --from の排他制約確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INVESTIGATE",
        "--from", "IMPLEMENT"
    ]

    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        parse_args()


@allure.title("parse_args: --issue required")
@allure.description("Verifies parse_args requires --issue option.")
def test_parse_args_missing_issue():
    """--issue 必須制約確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--state", "INVESTIGATE"
    ]

    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        parse_args()


@allure.title("parse_args: --tool option")
@allure.description("Verifies parse_args handles --tool option.")
def test_parse_args_tool_option():
    """--tool オプション: ツール指定"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INIT",
        "--tool", "codex"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()
        assert config.tool_override == "codex"
        assert config.model_override is None


@allure.title("parse_args: --tool-model option")
@allure.description("Verifies parse_args handles --tool-model option.")
def test_parse_args_tool_model_option():
    """--tool-model オプション: ツール:モデル指定"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INIT",
        "--tool-model", "codex:o4-mini"
    ]

    with patch("sys.argv", test_args):
        config = parse_args()
        assert config.tool_override == "codex"
        assert config.model_override == "o4-mini"


@allure.title("parse_args: --tool and --tool-model mutual exclusion")
@allure.description("Verifies --tool and --tool-model are mutually exclusive.")
def test_parse_args_tool_mutual_exclusion():
    """--tool と --tool-model の排他制約確認"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INIT",
        "--tool", "codex",
        "--tool-model", "gemini:gemini-2.5-flash"
    ]

    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        parse_args()


@allure.title("parse_args: --tool-model invalid format")
@allure.description("Verifies --tool-model rejects invalid format.")
def test_parse_args_tool_model_invalid_format():
    """--tool-model: 不正フォーマットでエラー"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INIT",
        "--tool-model", "codex-no-colon"  # missing colon
    ]

    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        parse_args()


@allure.title("parse_args: --tool-model invalid tool")
@allure.description("Verifies --tool-model rejects invalid tool name.")
def test_parse_args_tool_model_invalid_tool():
    """--tool-model: 不正ツール名でエラー"""
    test_args = [
        "bugfix_agent_orchestrator.py",
        "--issue", "https://github.com/apokamo/kamo2/issues/182",
        "--state", "INIT",
        "--tool-model", "unknown:model"
    ]

    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        parse_args()


# ==========================================
# State Handler Tests (Phase 3)
# ==========================================


@allure.title("handle_init: success")
@allure.description("Verifies handle_init reviews Issue and transitions to INVESTIGATE.")
def test_handle_init_success():
    """INIT: reviewer が Issue を確認してレビュー結果を返す"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[
            "## VERDICT\n- Result: PASS\n- Reason: All required fields present\n- Evidence: Checked all sections"
        ],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_init(ctx, state)

    assert next_state == State.INVESTIGATE
    assert "INIT" in state.completed_states


@allure.title("handle_investigate: new session")
@allure.description("Verifies handle_investigate creates new session and investigates.")
def test_handle_investigate_new_session():
    """INVESTIGATE: analyzer が新規セッションで調査を実行"""
    ctx = create_test_context(
        analyzer_responses=["Investigation complete: found root cause in mapper.py"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_investigate(ctx, state)

    assert next_state == State.INVESTIGATE_REVIEW
    assert "INVESTIGATE" in state.completed_states
    assert state.loop_counters["Investigate_Loop"] == 1
    assert state.active_conversations["Design_Thread_conversation_id"] is not None


@allure.title("handle_investigate: resume session")
@allure.description("Verifies handle_investigate resumes existing session.")
def test_handle_investigate_resume_session():
    """INVESTIGATE: 既存セッションを継続"""
    ctx = create_test_context(
        analyzer_responses=["Additional investigation results"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()
    state.active_conversations["Design_Thread_conversation_id"] = "existing-session-123"
    state.loop_counters["Investigate_Loop"] = 1

    next_state = handle_investigate(ctx, state)

    assert next_state == State.INVESTIGATE_REVIEW
    assert state.loop_counters["Investigate_Loop"] == 2
    assert state.active_conversations["Design_Thread_conversation_id"] == "existing-session-123"


@allure.title("handle_investigate_review: PASS (Issue #194 VERDICT)")
@allure.description("Verifies handle_investigate_review transitions to DETAIL_DESIGN on PASS.")
def test_handle_investigate_review_pass():
    """INVESTIGATE_REVIEW: PASS 判定で次ステートへ"""
    verdict_response = """
## VERDICT
- Result: PASS
- Reason: All required items present
- Evidence: Reproduction steps, expected diff, hypothesis all documented
- Suggestion: Proceed to design
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_investigate_review(ctx, state)

    assert next_state == State.DETAIL_DESIGN
    assert "INVESTIGATE_REVIEW" in state.completed_states


@allure.title("handle_investigate_review: RETRY (Issue #194 VERDICT)")
@allure.description("Verifies handle_investigate_review returns to INVESTIGATE on RETRY.")
def test_handle_investigate_review_blocked():
    """INVESTIGATE_REVIEW: RETRY 判定で INVESTIGATE へ戻る"""
    verdict_response = """
## VERDICT
- Result: RETRY
- Reason: Missing reproduction steps
- Evidence: No concrete steps to reproduce the bug
- Suggestion: Add step-by-step reproduction instructions
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_investigate_review(ctx, state)

    assert next_state == State.INVESTIGATE
    assert "INVESTIGATE_REVIEW" not in state.completed_states


@allure.title("handle_detail_design: new session")
@allure.description("Verifies handle_detail_design creates detailed design.")
def test_handle_detail_design_new_session():
    """DETAIL_DESIGN: analyzer が詳細設計を作成"""
    ctx = create_test_context(
        analyzer_responses=["Detailed design: update mapper.py, add validation"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_detail_design(ctx, state)

    assert next_state == State.DETAIL_DESIGN_REVIEW
    assert "DETAIL_DESIGN" in state.completed_states
    assert state.loop_counters["Detail_Design_Loop"] == 1


@allure.title("handle_detail_design: resume session")
@allure.description("Verifies handle_detail_design resumes Design_Thread session.")
def test_handle_detail_design_resume_session():
    """DETAIL_DESIGN: Design_Thread セッションを継続"""
    ctx = create_test_context(
        analyzer_responses=["Revised design based on feedback"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()
    state.active_conversations["Design_Thread_conversation_id"] = "design-123"
    state.loop_counters["Detail_Design_Loop"] = 1

    _next_state = handle_detail_design(ctx, state)

    assert state.loop_counters["Detail_Design_Loop"] == 2
    assert state.active_conversations["Design_Thread_conversation_id"] == "design-123"


@allure.title("handle_detail_design_review: PASS (Issue #194 VERDICT)")
@allure.description("Verifies handle_detail_design_review transitions to IMPLEMENT on PASS.")
def test_handle_detail_design_review_pass():
    """DETAIL_DESIGN_REVIEW: PASS 判定で IMPLEMENT へ"""
    verdict_response = """
## VERDICT
- Result: PASS
- Reason: Design is complete and correct
- Evidence: All required sections present with sufficient detail
- Suggestion: Proceed to implementation
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_detail_design_review(ctx, state)

    assert next_state == State.IMPLEMENT
    assert "DETAIL_DESIGN_REVIEW" in state.completed_states


@allure.title("handle_detail_design_review: RETRY (Issue #194 VERDICT)")
@allure.description("Verifies handle_detail_design_review returns to DETAIL_DESIGN on RETRY.")
def test_handle_detail_design_review_blocked():
    """DETAIL_DESIGN_REVIEW: RETRY 判定で DETAIL_DESIGN へ戻る"""
    verdict_response = """
## VERDICT
- Result: RETRY
- Reason: Test cases missing
- Evidence: No test case section found in design document
- Suggestion: Add test cases for all modified functions
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_detail_design_review(ctx, state)

    assert next_state == State.DETAIL_DESIGN
    assert "DETAIL_DESIGN_REVIEW" not in state.completed_states


@allure.title("handle_implement: new session")
@allure.description("Verifies handle_implement creates new implementation session.")
def test_handle_implement_new_session():
    """IMPLEMENT: implementer が実装を実行"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[],
        implementer_responses=["Implementation complete: branch bugfix/issue-182 created"],
    )
    state = SessionState()

    next_state = handle_implement(ctx, state)

    assert next_state == State.IMPLEMENT_REVIEW
    assert "IMPLEMENT" in state.completed_states
    assert state.loop_counters["Implement_Loop"] == 1
    assert state.active_conversations["Implement_Loop_conversation_id"] is not None


@allure.title("handle_implement: resume session")
@allure.description("Verifies handle_implement resumes Implement_Loop session.")
def test_handle_implement_resume_session():
    """IMPLEMENT: Implement_Loop セッションを継続"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[],
        implementer_responses=["Fixed implementation issues"],
    )
    state = SessionState()
    state.active_conversations["Implement_Loop_conversation_id"] = "impl-456"
    state.loop_counters["Implement_Loop"] = 1

    _next_state = handle_implement(ctx, state)

    assert state.loop_counters["Implement_Loop"] == 2
    assert state.active_conversations["Implement_Loop_conversation_id"] == "impl-456"


@allure.title("handle_implement_review: PASS (Issue #194 VERDICT)")
@allure.description("Verifies handle_implement_review transitions to PR_CREATE on PASS (QA integrated).")
def test_handle_implement_review_pass():
    """IMPLEMENT_REVIEW: PASS 判定で PR_CREATE へ（QA統合）"""
    verdict_response = """
## VERDICT
- Result: PASS
- Reason: Implementation and QA checks complete
- Evidence: All tests pass, code review OK
- Suggestion: Ready for PR
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_implement_review(ctx, state)

    assert next_state == State.PR_CREATE
    assert "IMPLEMENT_REVIEW" in state.completed_states


@allure.title("handle_implement_review: RETRY (Issue #194 VERDICT)")
@allure.description("Verifies handle_implement_review returns to IMPLEMENT on RETRY.")
def test_handle_implement_review_fix_required():
    """IMPLEMENT_REVIEW: RETRY 判定で IMPLEMENT へ戻る"""
    verdict_response = """
## VERDICT
- Result: RETRY
- Reason: Logic error in validation
- Evidence: Test failure in test_validate_input
- Suggestion: Fix validation logic in validate_input()
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_implement_review(ctx, state)

    assert next_state == State.IMPLEMENT
    assert "IMPLEMENT_REVIEW" not in state.completed_states


@allure.title("handle_implement_review: BACK_DESIGN (Issue #194 VERDICT)")
@allure.description("Verifies handle_implement_review returns to DETAIL_DESIGN on BACK_DESIGN.")
def test_handle_implement_review_design_fix():
    """IMPLEMENT_REVIEW: BACK_DESIGN 判定で DETAIL_DESIGN へ戻る"""
    verdict_response = """
## VERDICT
- Result: BACK_DESIGN
- Reason: Approach needs reconsideration
- Evidence: Design does not handle edge cases
- Suggestion: Redesign error handling approach
"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_response],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_implement_review(ctx, state)

    assert next_state == State.DETAIL_DESIGN
    assert "IMPLEMENT_REVIEW" not in state.completed_states


# NOTE: QA/QA_REVIEW tests removed - merged into IMPLEMENT_REVIEW (Issue #194)


@allure.title("handle_pr_create: success")
@allure.description("Verifies handle_pr_create creates PR and transitions to COMPLETE.")
def test_handle_pr_create_success():
    """PR_CREATE: implementer が PR を作成して COMPLETE へ"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[],
        implementer_responses=["PR created: https://github.com/apokamo/kamo2/pull/123"],
    )
    state = SessionState()

    next_state = handle_pr_create(ctx, state)

    assert next_state == State.COMPLETE
    assert "PR_CREATE" in state.completed_states


# ==========================================
# Edge Case Tests
# ==========================================


@allure.title("Loop counter: increments correctly")
@allure.description("Verifies loop counters increment on each handler call.")
def test_loop_counter_increments_correctly():
    """ループカウンターが正しくインクリメントされる"""
    ctx = create_test_context(
        analyzer_responses=["result1", "result2", "result3"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    for i in range(3):
        handle_investigate(ctx, state)
        assert state.loop_counters["Investigate_Loop"] == i + 1


@allure.title("Session ID: preserved across loops")
@allure.description("Verifies session ID is preserved when handler is called multiple times.")
def test_session_id_preserved_across_loops():
    """セッション ID がループ間で保持される"""
    ctx = create_test_context(
        analyzer_responses=["result1", "result2"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    handle_investigate(ctx, state)
    session_1 = state.active_conversations["Design_Thread_conversation_id"]
    assert session_1 is not None

    handle_investigate(ctx, state)
    session_2 = state.active_conversations["Design_Thread_conversation_id"]
    assert session_2 == session_1


@allure.title("Completed states: accumulate")
@allure.description("Verifies completed_states accumulates as handlers run.")
def test_completed_states_accumulate():
    """completed_states が累積される"""
    verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
    ctx = create_test_context(
        analyzer_responses=["analyze"],
        reviewer_responses=[verdict_pass, verdict_pass],  # INIT, INVESTIGATE_REVIEW
        implementer_responses=[],
    )
    state = SessionState()

    handle_init(ctx, state)
    assert len(state.completed_states) == 1
    assert "INIT" in state.completed_states

    handle_investigate(ctx, state)
    assert len(state.completed_states) == 2
    assert "INVESTIGATE" in state.completed_states

    handle_investigate_review(ctx, state)
    assert len(state.completed_states) == 3
    assert "INVESTIGATE_REVIEW" in state.completed_states


@allure.title("RETRY verdict: not added to completed")
@allure.description("Verifies RETRY verdict states are not added to completed_states.")
def test_retry_verdict_does_not_add_to_completed():
    """RETRY 判定の場合、completed_states に追加されない"""
    verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Missing info\n- Suggestion: 再調査"
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_retry],
        implementer_responses=[],
    )
    state = SessionState()

    handle_investigate_review(ctx, state)
    assert "INVESTIGATE_REVIEW" not in state.completed_states


@allure.title("Artifacts directory: created by handler")
@allure.description("Verifies handlers create artifacts directory.")
def test_artifacts_directory_creation():
    """各ステートが証跡ディレクトリを作成する"""
    ctx = create_test_context(
        analyzer_responses=["result"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    with patch("bugfix_agent_orchestrator.Path.mkdir") as mock_mkdir:
        handle_investigate(ctx, state)
        assert mock_mkdir.called


@allure.title("Multiple RETRY: loops work correctly")
@allure.description("Verifies multiple RETRY loops function correctly.")
def test_multiple_retry_loops():
    """RETRY が複数回発生してもループが機能する"""
    verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: 設計が不十分\n- Suggestion: 再設計"
    verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
    ctx = create_test_context(
        analyzer_responses=["design1", "design2", "design3"],
        reviewer_responses=[verdict_retry, verdict_pass, verdict_pass],
        implementer_responses=["impl1"],
    )
    state = SessionState()

    handle_detail_design(ctx, state)
    assert state.loop_counters["Detail_Design_Loop"] == 1

    result = handle_detail_design_review(ctx, state)
    assert result == State.DETAIL_DESIGN

    handle_detail_design(ctx, state)
    assert state.loop_counters["Detail_Design_Loop"] == 2


# ==========================================
# Result Label Inference Tests
# ==========================================


@allure.title("infer_result_label: non-review states return PASS")
@allure.description("Non-review states always return PASS regardless of transition.")
def test_infer_result_label_non_review_always_pass():
    """非レビューステートは常に PASS を返す"""
    assert infer_result_label(State.INIT, State.INVESTIGATE) == "PASS"
    assert infer_result_label(State.INVESTIGATE, State.INVESTIGATE_REVIEW) == "PASS"
    assert infer_result_label(State.DETAIL_DESIGN, State.DETAIL_DESIGN_REVIEW) == "PASS"
    assert infer_result_label(State.IMPLEMENT, State.IMPLEMENT_REVIEW) == "PASS"
    assert infer_result_label(State.PR_CREATE, State.COMPLETE) == "PASS"


@allure.title("infer_result_label: INVESTIGATE_REVIEW transitions")
@allure.description("Verifies INVESTIGATE_REVIEW transition labels.")
def test_infer_result_label_investigate_review():
    """INVESTIGATE_REVIEW の遷移ラベル"""
    assert infer_result_label(State.INVESTIGATE_REVIEW, State.DETAIL_DESIGN) == "PASS"
    assert infer_result_label(State.INVESTIGATE_REVIEW, State.INVESTIGATE) == "RETRY"


@allure.title("infer_result_label: DETAIL_DESIGN_REVIEW transitions")
@allure.description("Verifies DETAIL_DESIGN_REVIEW transition labels.")
def test_infer_result_label_detail_design_review():
    """DETAIL_DESIGN_REVIEW の遷移ラベル"""
    assert infer_result_label(State.DETAIL_DESIGN_REVIEW, State.IMPLEMENT) == "PASS"
    assert infer_result_label(State.DETAIL_DESIGN_REVIEW, State.DETAIL_DESIGN) == "RETRY"


@allure.title("infer_result_label: IMPLEMENT_REVIEW transitions")
@allure.description("Verifies IMPLEMENT_REVIEW transition labels.")
def test_infer_result_label_implement_review():
    """IMPLEMENT_REVIEW の遷移ラベル (QA統合後)"""
    # PASS → PR_CREATE (QA削除後の直接遷移)
    assert infer_result_label(State.IMPLEMENT_REVIEW, State.PR_CREATE) == "PASS"
    # RETRY → IMPLEMENT (軽微な修正)
    assert infer_result_label(State.IMPLEMENT_REVIEW, State.IMPLEMENT) == "RETRY"
    # BACK_DESIGN → DETAIL_DESIGN (設計からやり直し)
    assert infer_result_label(State.IMPLEMENT_REVIEW, State.DETAIL_DESIGN) == "BACK_DESIGN"


@allure.title("infer_result_label: unknown transition returns UNKNOWN")
@allure.description("Verifies unknown transitions return UNKNOWN.")
def test_infer_result_label_unknown_transition():
    """未定義の遷移は UNKNOWN を返す"""
    # レビューステートから予期しない遷移
    assert infer_result_label(State.INVESTIGATE_REVIEW, State.COMPLETE) == "UNKNOWN"
    assert infer_result_label(State.IMPLEMENT_REVIEW, State.INIT) == "UNKNOWN"


# ==========================================
# Error Handling Tests
# ==========================================


@allure.title("check_tool_result: passes normal result")
@allure.description("Verifies check_tool_result returns normal results unchanged.")
def test_check_tool_result_passes_normal_result():
    """check_tool_result: 正常な結果をそのまま返す"""
    result = check_tool_result("PASS: All good.", "reviewer")
    assert result == "PASS: All good."


@allure.title("check_tool_result: raises on ERROR")
@allure.description("Verifies check_tool_result raises ToolError on ERROR.")
def test_check_tool_result_raises_on_error():
    """check_tool_result: ERROR で ToolError を投げる"""
    with pytest.raises(ToolError) as exc_info:
        check_tool_result("ERROR", "analyzer")
    assert "analyzer returned ERROR" in str(exc_info.value)


@allure.title("ToolError: stops handler")
@allure.description("Verifies handler stops when receiving ERROR.")
def test_tool_error_stops_handler():
    """ハンドラが ERROR を受けると ToolError で停止"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=["ERROR"],
        implementer_responses=[],
    )
    state = SessionState()

    with pytest.raises(ToolError) as exc_info:
        handle_init(ctx, state)
    assert "reviewer returned ERROR" in str(exc_info.value)
    assert "INIT" not in state.completed_states


@allure.title("ToolError: in INVESTIGATE")
@allure.description("Verifies INVESTIGATE stops on ERROR.")
def test_tool_error_in_investigate():
    """INVESTIGATE で ERROR が発生すると停止"""
    ctx = create_test_context(
        analyzer_responses=["ERROR"],
        reviewer_responses=[],
        implementer_responses=[],
    )
    state = SessionState()

    with pytest.raises(ToolError) as exc_info:
        handle_investigate(ctx, state)
    assert "analyzer returned ERROR" in str(exc_info.value)


@allure.title("ToolError: in IMPLEMENT")
@allure.description("Verifies IMPLEMENT stops on ERROR.")
def test_tool_error_in_implement():
    """IMPLEMENT で ERROR が発生すると停止"""
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[],
        implementer_responses=["ERROR"],
    )
    state = SessionState()

    with pytest.raises(ToolError) as exc_info:
        handle_implement(ctx, state)
    assert "implementer returned ERROR" in str(exc_info.value)


@allure.title("run: stops on ToolError")
@allure.description("Verifies run() stops and logs on ToolError.")
def test_run_stops_on_tool_error():
    """run() が ToolError で停止してログを出力"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INIT,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=["ERROR"],
        implementer_responses=[],
        issue_url=config.issue_url,
        run_timestamp="2511291200",
    )

    with pytest.raises(ToolError) as exc_info:
        run(config, ctx=ctx)
    assert "reviewer returned ERROR" in str(exc_info.value)


# ==========================================
# RunLogger Tests
# ==========================================


@allure.title("RunLogger: creates directory")
@allure.description("Verifies RunLogger creates parent directories.")
def test_run_logger_creates_directory(tmp_path):
    """RunLogger: 親ディレクトリを自動作成"""
    log_path = tmp_path / "subdir" / "run.log"
    RunLogger(log_path)

    assert log_path.parent.exists()


@allure.title("RunLogger: writes JSONL")
@allure.description("Verifies RunLogger writes valid JSONL format.")
def test_run_logger_writes_jsonl(tmp_path):
    """RunLogger: JSONL 形式でログを出力"""
    log_path = tmp_path / "run.log"
    logger = RunLogger(log_path)

    logger.log_run_start("https://github.com/test/repo/issues/1", "2511291200")
    logger.log_state_enter("INIT")
    logger.log_state_exit("INIT", "PASS", "INVESTIGATE")
    logger.log_run_end("COMPLETE", {"Investigate_Loop": 1})

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 4

    for line in lines:
        entry = json.loads(line)
        assert "ts" in entry
        assert "event" in entry


@allure.title("RunLogger: error event")
@allure.description("Verifies RunLogger includes error info in ERROR events.")
def test_run_logger_error_event(tmp_path):
    """RunLogger: ERROR イベントにエラー情報を含める"""
    log_path = tmp_path / "run.log"
    logger = RunLogger(log_path)

    logger.log_run_end("ERROR", {"Investigate_Loop": 1}, error="analyzer returned ERROR")

    line = log_path.read_text().strip()
    entry = json.loads(line)
    assert entry["event"] == "run_end"
    assert entry["status"] == "ERROR"
    assert entry["error"] == "analyzer returned ERROR"


@allure.title("run: creates log file")
@allure.description("Verifies run() creates run.log file.")
def test_run_creates_log_file():
    """run() が run.log を作成する"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.PR_CREATE,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[],
        implementer_responses=["PR created"],
        issue_url=config.issue_url,
        run_timestamp="2511291300",
    )

    run(config, ctx=ctx)

    log_path = ctx.artifacts_dir / "run.log"
    assert log_path.exists()

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) >= 3

    first_entry = json.loads(lines[0])
    assert first_entry["event"] == "run_start"
    assert first_entry["issue_url"] == config.issue_url


@allure.title("run: logs correct result label for RETRY")
@allure.description("Verifies run() logs RETRY when review state returns previous work state.")
def test_run_logs_retry_result_label():
    """run() が RETRY を正しくログに記録する"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INVESTIGATE_REVIEW,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Need more investigation\n- Suggestion: 再調査"
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_retry],
        implementer_responses=[],
        issue_url=config.issue_url,
        run_timestamp="2511291300",
    )

    # INVESTIGATE_REVIEW で RETRY を返すと INVESTIGATE に戻る
    run(config, ctx=ctx)

    log_path = ctx.artifacts_dir / "run.log"
    lines = log_path.read_text().strip().split("\n")

    # state_exit イベントを探す（最後のマッチを使用：ログはappendされるため）
    state_exit_entry = None
    for line in lines:
        entry = json.loads(line)
        if entry["event"] == "state_exit" and entry["state"] == "INVESTIGATE_REVIEW":
            state_exit_entry = entry  # 最後のマッチを使用

    assert state_exit_entry is not None
    assert state_exit_entry["result"] == "RETRY"
    assert state_exit_entry["next"] == "INVESTIGATE"


@allure.title("run: logs correct result label for IMPLEMENT_REVIEW RETRY")
@allure.description("Verifies run() logs RETRY when IMPLEMENT_REVIEW returns IMPLEMENT.")
def test_run_logs_implement_review_retry_result_label():
    """run() が IMPLEMENT_REVIEW の RETRY を正しくログに記録する"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.IMPLEMENT_REVIEW,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Tests failing\n- Suggestion: 修正"
    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[verdict_retry],
        implementer_responses=[],
        issue_url=config.issue_url,
        run_timestamp="2511291300",
    )

    run(config, ctx=ctx)

    log_path = ctx.artifacts_dir / "run.log"
    lines = log_path.read_text().strip().split("\n")

    # state_exit イベントを探す（最後のマッチを使用：ログはappendされるため）
    state_exit_entry = None
    for line in lines:
        entry = json.loads(line)
        if entry["event"] == "state_exit" and entry["state"] == "IMPLEMENT_REVIEW":
            state_exit_entry = entry  # 最後のマッチを使用

    assert state_exit_entry is not None
    assert state_exit_entry["result"] == "RETRY"
    assert state_exit_entry["next"] == "IMPLEMENT"


@allure.title("run: logs ERROR state_exit on ToolError")
@allure.description("Verifies run() logs state_exit with ERROR when ToolError occurs.")
def test_run_logs_error_state_exit_on_tool_error():
    """run() が ToolError 発生時に state_exit(ERROR) をログに記録する"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INVESTIGATE,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    ctx = create_test_context(
        analyzer_responses=["ERROR"],  # ToolError を発生させる
        reviewer_responses=[],
        implementer_responses=[],
        issue_url=config.issue_url,
        run_timestamp="2511291300",
    )

    with pytest.raises(ToolError):
        run(config, ctx=ctx)

    log_path = ctx.artifacts_dir / "run.log"
    lines = log_path.read_text().strip().split("\n")

    # state_exit イベントを探す（ERROR になるはず）
    state_exit_entry = None
    for line in lines:
        entry = json.loads(line)
        if entry["event"] == "state_exit" and entry["state"] == "INVESTIGATE":
            state_exit_entry = entry
            break

    assert state_exit_entry is not None
    assert state_exit_entry["result"] == "ERROR"
    assert state_exit_entry["next"] == "INVESTIGATE"  # 同じステートに留まる


# ==========================================
# Integration Tests (run execution modes)
# ==========================================


@allure.title("run: SINGLE mode stops after one state")
@allure.description("Verifies SINGLE mode executes only target state.")
def test_run_single_mode_stops_after_one():
    """SINGLE モードが1回で停止するか確認"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INIT,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
    ctx = create_test_context(
        analyzer_responses=["analyzer response"],
        reviewer_responses=[verdict_pass],  # INIT用のVERDICT応答
        implementer_responses=["implementer response"],
        issue_url=config.issue_url,
        run_timestamp="2511281430",
    )

    run(config, ctx=ctx)
    # If it completes without error, test passes


@allure.title("run: FROM_END mode continues to COMPLETE")
@allure.description("Verifies FROM_END mode runs from target to COMPLETE.")
def test_run_from_end_mode_continues():
    """FROM_END モードが指定ステートから継続するか確認"""
    config = ExecutionConfig(
        mode=ExecutionMode.FROM_END,
        target_state=State.PR_CREATE,
        issue_url="https://github.com/apokamo/kamo2/issues/999",
        issue_number=999,
    )

    ctx = create_test_context(
        analyzer_responses=["analyzer response"],
        reviewer_responses=["reviewer response"],
        implementer_responses=["implementer response"],
        issue_url=config.issue_url,
        run_timestamp="2511281430",
    )

    run(config, ctx=ctx)
    # If it completes without error, test passes


# ==========================================
# Smoke Tests (CI skip)
# ==========================================


@pytest.mark.skip(reason="Requires actual CLI tools (gemini, codex, claude)")
@allure.title("Smoke: GeminiTool real CLI")
@allure.description("Smoke test for GeminiTool with actual CLI.")
def test_smoke_gemini_tool_real_cli():
    """Smoke test: GeminiTool が実 CLI を呼び出せる"""
    tool = mod.GeminiTool(model="auto")
    response, session_id = tool.run("What is 2+2?")

    assert response != ""
    assert response != "ERROR"


@pytest.mark.skip(reason="Requires actual CLI tools (gemini, codex, claude)")
@allure.title("Smoke: CodexTool real CLI")
@allure.description("Smoke test for CodexTool with actual CLI.")
def test_smoke_codex_tool_real_cli():
    """Smoke test: CodexTool が実 CLI を呼び出せる"""
    tool = mod.CodexTool()
    response, session_id = tool.run("Review this code: def add(a, b): return a + b")

    assert response != ""
    assert response != "ERROR"


@pytest.mark.skip(reason="Requires actual CLI tools (gemini, codex, claude)")
@allure.title("Smoke: ClaudeTool real CLI")
@allure.description("Smoke test for ClaudeTool with actual CLI.")
def test_smoke_claude_tool_real_cli():
    """Smoke test: ClaudeTool が実 CLI を呼び出せる"""
    tool = mod.ClaudeTool()
    response, session_id = tool.run("Echo 'Hello from Claude'")

    assert response != ""
    assert response != "ERROR"


@pytest.mark.skip(reason="Requires actual CLI tools and GitHub issue")
@allure.title("Smoke: full orchestrator run")
@allure.description("Smoke test for full orchestrator execution.")
def test_smoke_full_orchestrator_run():
    """Smoke test: オーケストレーター全体実行（SINGLE モード）"""
    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INIT,
        issue_url="https://github.com/apokamo/kamo2/issues/182",
        issue_number=182,
    )

    run(config)


# ==========================================
# Issue #186 Tests: INIT NG, web_fetch, Timeout
# ==========================================


@allure.title("handle_init: NG judgment raises ToolError")
@allure.description("Verifies handle_init raises ToolError when reviewer returns NG.")
def test_handle_init_ng_raises_agent_abort_error():
    """INIT: ABORT 判定で AgentAbortError を発生"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[
            "## VERDICT\n- Result: ABORT\n- Reason: 期待される挙動が不足\n- Suggestion: Issue本文に期待される挙動を追記してください"
        ],
        implementer_responses=[],
    )
    state = SessionState()

    with pytest.raises(AgentAbortError) as exc_info:
        handle_init(ctx, state)

    assert "期待される挙動が不足" in str(exc_info.value)
    assert "INIT" not in state.completed_states  # INIT は完了していない
    # issue_provider.add_comment() が呼ばれたことを確認
    assert len(ctx.issue_provider._comments) == 1


@allure.title("handle_init: PASS verdict transitions to INVESTIGATE")
@allure.description("Verifies handle_init transitions to INVESTIGATE when reviewer returns PASS.")
def test_handle_init_pass_transitions_to_investigate():
    """INIT: PASS 判定で INVESTIGATE へ遷移"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[
            "## VERDICT\n- Result: PASS\n- Reason: 全ての必須項目が記載されている\n- Evidence: Issue本文を確認"
        ],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_init(ctx, state)

    assert next_state == State.INVESTIGATE
    assert "INIT" in state.completed_states


@allure.title("handle_init: ABORT verdict variations")
@allure.description("Verifies handle_init detects various ABORT patterns.")
@pytest.mark.parametrize(
    "abort_response",
    [
        "## VERDICT\n- Result: ABORT\n- Reason: 必須項目が不足\n- Suggestion: 修正してください",
        "## VERDICT\n- Result: abort\n- Reason: 情報不足",  # 小文字でも動作
        "Some analysis...\n## VERDICT\n- Result: ABORT\n- Reason: エラー発生",  # 途中にVERDICT
    ],
)
def test_handle_init_abort_verdict_variations(abort_response):
    """INIT: 様々な ABORT パターンを検出"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[abort_response],
        implementer_responses=[],
    )
    state = SessionState()

    with pytest.raises(AgentAbortError):
        handle_init(ctx, state)


@allure.title("handle_init: PASS even when ABORT appears in context")
@allure.description(
    "Verifies handle_init does not falsely detect ABORT when it appears outside VERDICT section."
)
@pytest.mark.parametrize(
    "pass_response_with_abort_context",
    [
        "## Analysis\nABORT条件は満たしていません。\n\n## VERDICT\n- Result: PASS\n- Reason: 全項目OK",
        "ABORT判定の基準を確認...\n## VERDICT\n- Result: PASS\n- Reason: 問題なし",
    ],
)
def test_handle_init_pass_with_abort_word_in_context(pass_response_with_abort_context):
    """INIT: 文脈上ABORTという単語が含まれていてもPASS判定"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    ctx = create_test_context(
        analyzer_responses=[],
        reviewer_responses=[pass_response_with_abort_context],
        implementer_responses=[],
    )
    state = SessionState()

    next_state = handle_init(ctx, state)

    assert next_state == State.INVESTIGATE
    assert "INIT" in state.completed_states


@allure.title("GeminiTool: --allowed-tools includes web_fetch")
@allure.description("Verifies GeminiTool includes web_fetch in --allowed-tools.")
def test_gemini_tool_allowed_tools_includes_web_fetch(monkeypatch):
    """GeminiTool: --allowed-tools に web_fetch が含まれる"""
    captured_args = []

    def mock_streaming(args, **kwargs):
        captured_args.extend(args)
        stdout = '{"type": "init", "session_id": "test"}\n{"role": "assistant", "content": "ok"}'
        return stdout, "", 0

    monkeypatch.setattr(mod, "run_cli_streaming", mock_streaming)

    tool = mod.GeminiTool()
    tool.run(prompt="test")

    # --allowed-tools の引数を確認
    allowed_tools_idx = captured_args.index("--allowed-tools")
    allowed_tools_value = captured_args[allowed_tools_idx + 1]

    assert "web_fetch" in allowed_tools_value
    assert "run_shell_command" in allowed_tools_value


@allure.title("run_cli_streaming: timeout kills hanging process")
@allure.description("Verifies run_cli_streaming kills process on timeout.")
def testrun_cli_streaming_timeout_kills_process(monkeypatch):
    """run_cli_streaming: タイムアウトでプロセスを強制終了"""
    import subprocess
    import threading
    import time

    kill_called = threading.Event()
    process_started = threading.Event()

    class HangingProcess:
        def __init__(self, *args, **kwargs):
            self.returncode = -9
            self._stdout_iter = self._hang_forever()
            self.stdout = self._stdout_iter
            self.stderr = iter([])

        def _hang_forever(self):
            process_started.set()
            # 永遠にブロックするイテレータ
            while not kill_called.is_set():
                time.sleep(0.05)
            # kill が呼ばれたら終了
            return
            yield  # make it a generator  # noqa: RET503

        def kill(self):
            kill_called.set()

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", HangingProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)

    start = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        mod.run_cli_streaming(["hang"], timeout=1)
    elapsed = time.time() - start

    assert kill_called.is_set()  # kill() が呼ばれた
    assert elapsed < 3  # タイムアウト時間内に終了


@allure.title("run_cli_streaming: no timeout completes normally")
@allure.description("Verifies run_cli_streaming completes normally without timeout.")
def testrun_cli_streaming_no_timeout_completes_normally(monkeypatch):
    """run_cli_streaming: タイムアウトなしで正常完了"""
    import subprocess

    class NormalProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(["line1\n", "line2\n"])
            self.stderr = iter([])
            self.returncode = 0

        def kill(self):
            pytest.fail("kill() should not be called")

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", NormalProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)

    stdout, stderr, returncode = mod.run_cli_streaming(["test"], timeout=60)

    assert stdout == "line1\nline2\n"
    assert returncode == 0


@allure.title("run_cli_streaming: timer cancelled on success")
@allure.description("Verifies timeout timer is cancelled when process completes normally.")
def testrun_cli_streaming_timer_cancelled_on_success(monkeypatch):
    """run_cli_streaming: 正常完了時にタイマーがキャンセルされる"""
    import subprocess
    import threading

    timer_cancelled = threading.Event()
    OriginalTimer = threading.Timer

    class MockTimer(OriginalTimer):
        def cancel(self):
            timer_cancelled.set()
            super().cancel()

    monkeypatch.setattr(threading, "Timer", MockTimer)

    class NormalProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(["done\n"])
            self.stderr = iter([])
            self.returncode = 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", NormalProcess)
    monkeypatch.setattr(mod, "get_config_value", lambda *a, **k: False)

    mod.run_cli_streaming(["test"], timeout=60)

    assert timer_cancelled.is_set()  # タイマーがキャンセルされた


@allure.title("run: stops on INIT ABORT")
@allure.description("Verifies orchestrator stops when INIT returns ABORT.")
def test_run_stops_on_init_abort(monkeypatch, tmp_path):
    """run: INIT ABORT で停止"""
    # create_test_context は MockIssueProvider を使用するため
    # post_issue_comment のモック不要（GitHub API呼び出しなし）

    config = ExecutionConfig(
        mode=ExecutionMode.SINGLE,
        target_state=State.INIT,
        issue_url="https://github.com/test/repo/issues/1",
        issue_number=1,
    )

    # create_default_context をモックして create_test_context を使用
    def mock_create_default_context(issue_url, **kwargs):
        return create_test_context(
            analyzer_responses=[],
            reviewer_responses=[
                "## VERDICT\n- Result: ABORT\n- Reason: 期待される挙動が不足\n- Suggestion: Issue本文に追記"
            ],
            implementer_responses=[],
            issue_url=issue_url,
        )

    monkeypatch.setattr(mod, "create_default_context", mock_create_default_context)

    with pytest.raises(AgentAbortError) as exc_info:
        run(config)

    assert "期待される挙動が不足" in str(exc_info.value)


# ==========================================
# State Machine Comprehensive Tests (Issue #194)
# ==========================================


@allure.title("VERDICT parsing: extracts all fields correctly")
@allure.description("Verifies parse_verdict and _extract_verdict_field work correctly.")
class TestVerdictParsing:
    """VERDICT形式パースの詳細テスト"""

    def test_parse_verdict_pass_with_all_fields(self):
        """PASS: 全フィールドが正しく抽出される"""
        text = """## VERDICT
- Result: PASS
- Reason: All requirements met
- Evidence: Checked all sections
- Suggestion: Proceed to next state"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.PASS

        reason = _extract_verdict_field(text, "Reason")
        assert reason == "All requirements met"

        evidence = _extract_verdict_field(text, "Evidence")
        assert evidence == "Checked all sections"

    def test_parse_verdict_retry_case_insensitive(self):
        """RETRY: 大文字小文字を区別しない"""
        for result_str in ["RETRY", "retry", "Retry", "rEtRy"]:
            text = f"## VERDICT\n- Result: {result_str}\n- Reason: Test"
            verdict = parse_verdict(text)
            assert verdict == Verdict.RETRY

    def test_parse_verdict_back_design(self):
        """BACK_DESIGN: アンダースコア付きのVERDICTを正しくパース"""
        text = """## VERDICT
- Result: BACK_DESIGN
- Reason: Architecture needs rethinking
- Suggestion: Reconsider data model"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.BACK_DESIGN

    def test_parse_verdict_abort_returns_verdict_abort(self):
        """ABORT: parse_verdict は Verdict.ABORT を返す（例外は送出しない）

        Issue #292 責務分離: パーサーは Verdict enum を返すのみ。
        AgentAbortError は handle_abort_verdict() で送出される。
        """
        text = """## VERDICT
- Result: ABORT
- Reason: Missing critical information
- Suggestion: Add expected behavior to issue"""
        # parse_verdict は例外を送出せず Verdict.ABORT を返す
        verdict = parse_verdict(text)
        assert verdict == Verdict.ABORT

    def test_handle_abort_verdict_raises_agent_abort_error(self):
        """ABORT: handle_abort_verdict で AgentAbortError が送出される

        Issue #292 責務分離: ABORT時の例外送出はオーケストレーター責務。
        """
        text = """## VERDICT
- Result: ABORT
- Reason: Missing critical information
- Suggestion: Add expected behavior to issue"""
        verdict = parse_verdict(text)
        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(verdict, text)
        assert "Missing critical information" in str(exc_info.value)
        assert exc_info.value.reason == "Missing critical information"
        assert exc_info.value.suggestion == "Add expected behavior to issue"

    def test_handle_abort_verdict_without_suggestion(self):
        """ABORT: Suggestionなしでも動作（Issue #292 責務分離）"""
        text = "## VERDICT\n- Result: ABORT\n- Reason: Fatal error"
        verdict = parse_verdict(text)
        with pytest.raises(AgentAbortError) as exc_info:
            handle_abort_verdict(verdict, text)
        assert exc_info.value.suggestion == ""

    def test_handle_abort_verdict_non_abort_returns_verdict(self):
        """PASS/RETRY/BACK_DESIGN: handle_abort_verdict は verdict をそのまま返す"""
        for verdict_str in ["PASS", "RETRY", "BACK_DESIGN"]:
            text = f"## VERDICT\n- Result: {verdict_str}\n- Reason: Test"
            verdict = parse_verdict(text)
            result = handle_abort_verdict(verdict, text)
            assert result == verdict

    def test_parse_verdict_no_result_raises_error(self):
        """VERDICTなし: VerdictParseErrorがraiseされる（Step 1-2失敗）

        Issue #292 ハイブリッドパーサー: Step 1-2 で見つからない場合、
        ai_formatter なしではエラーメッセージが変更される。
        """
        text = "Some random text without VERDICT"
        with pytest.raises(VerdictParseError) as exc_info:
            parse_verdict(text)
        # Hybrid parser Step 1-2 fallthrough message
        assert "All parse attempts failed" in str(exc_info.value)

    def test_parse_verdict_invalid_value_raises_error(self):
        """無効なVERDICT値: InvalidVerdictValueErrorがraiseされる

        Issue #292 レビュー対応: 不正な値はフォールバック対象外。
        プロンプト違反/実装バグを示すため、即座に raise される。
        """
        text = "## VERDICT\n- Result: INVALID"
        with pytest.raises(InvalidVerdictValueError) as exc_info:
            parse_verdict(text)
        # InvalidVerdictValueError is raised immediately (no fallback)
        assert "Invalid VERDICT value: INVALID" in str(exc_info.value)

    def test_parse_verdict_with_leading_content(self):
        """VERDICT前にコンテンツがあっても正しくパース"""
        text = """### Analysis
Some analysis content here...

### Findings
- Found issue A
- Found issue B

## VERDICT
- Result: PASS
- Reason: All good"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.PASS

    def test_extract_verdict_field_multiline(self):
        """複数行のフィールド値を抽出"""
        text = """## VERDICT
- Result: PASS
- Reason: Multiple reasons here
- Evidence: First evidence"""
        reason = _extract_verdict_field(text, "Reason")
        assert reason == "Multiple reasons here"


@allure.title("Hybrid Fallback Parser: Issue #292 implementation tests")
@allure.description("Verifies 3-step hybrid fallback parser with strict, relaxed, and AI formatter retry.")
class TestHybridFallbackParser:
    """ハイブリッドフォールバックパーサーのテスト（Issue #292）

    3ステップのフォールバック戦略をテスト:
    - Step 1: 厳密パース "Result: <STATUS>"
    - Step 2: 緩和パース (複数パターン)
    - Step 3: AI Formatter Retry
    """

    # === Step 2: Relaxed Parse Tests ===

    def test_step2_list_format_status(self):
        """Step 2: リスト形式 "- Status: PASS" でパース成功"""
        text = """## Review Result
- Status: PASS
- Summary: All checks passed
- Details: Test coverage 100%"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.PASS

    def test_step2_bold_format_status(self):
        """Step 2: Bold形式 "**Status**: RETRY" でパース成功"""
        text = """## Review Result
**Status**: RETRY
**Summary**: Minor issues found
**Details**: Fix formatting"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.RETRY

    def test_step2_japanese_status(self):
        """Step 2: 日本語形式 "ステータス: BACK_DESIGN" でパース成功"""
        text = """## レビュー結果
ステータス: BACK_DESIGN
理由: アーキテクチャの見直しが必要
提案: データモデルを再検討"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.BACK_DESIGN

    def test_step2_assignment_format_status(self):
        """Step 2: 代入形式 "Status = ABORT" でパース成功"""
        text = """Review completed.
Status = ABORT
Reason = Missing environment variables"""
        verdict = parse_verdict(text)
        assert verdict == Verdict.ABORT

    def test_step2_plain_status_colon(self):
        """Step 2: プレーン形式 "Status: PASS" でパース成功"""
        text = """Analysis complete.
Status: PASS
No issues found."""
        verdict = parse_verdict(text)
        assert verdict == Verdict.PASS

    def test_step2_patterns_only_match_valid_values(self):
        """Step 2: 無効な値 "PENDING" はマッチしない（Issue #292 パターン制限）"""
        text = """## Review Result
- Status: PENDING
- Summary: Waiting for review"""
        # Should raise VerdictParseError because PENDING is not a valid Verdict
        with pytest.raises(VerdictParseError):
            parse_verdict(text)

    # === Step 3: AI Formatter Tests ===

    def test_step3_ai_formatter_success(self):
        """Step 3: AI Formatter でリカバリー成功"""
        # Malformed input that Step 1/2 cannot parse
        malformed_text = "The review is complete and the verdict is that we should proceed (PASS)"

        # Mock AI formatter that returns proper format
        # Note: Step 3 uses strict parser internally, so "Result:" format is required
        def mock_formatter(text: str) -> str:
            return "## VERDICT\n- Result: PASS\n- Reason: Proceed"

        verdict = parse_verdict(malformed_text, ai_formatter=mock_formatter)
        assert verdict == Verdict.PASS

    def test_step3_ai_formatter_multiple_retries(self):
        """Step 3: AI Formatter 複数回リトライ後に成功"""
        malformed_text = "verdict unclear, needs analysis"
        attempts = []

        def flaky_formatter(text: str) -> str:
            attempts.append(1)
            if len(attempts) < 2:
                return "Still unclear..."  # First attempt fails
            return "## VERDICT\n- Result: RETRY\n- Reason: Needs more work"

        verdict = parse_verdict(malformed_text, ai_formatter=flaky_formatter, max_retries=3)
        assert verdict == Verdict.RETRY
        assert len(attempts) == 2  # Should succeed on second attempt

    def test_step3_ai_formatter_all_retries_fail(self):
        """Step 3: AI Formatter 全リトライ失敗時は VerdictParseError"""
        malformed_text = "completely unstructured output"

        def failing_formatter(text: str) -> str:
            return "Still cannot parse this..."

        with pytest.raises(VerdictParseError) as exc_info:
            parse_verdict(malformed_text, ai_formatter=failing_formatter, max_retries=2)

        assert "2 AI formatter attempts failed" in str(exc_info.value)

    def test_step3_no_ai_formatter_raises_error(self):
        """Step 3: AI Formatter なしで Step 1-2 失敗時はエラー"""
        malformed_text = "completely unstructured output without any verdict"

        with pytest.raises(VerdictParseError) as exc_info:
            parse_verdict(malformed_text)  # No ai_formatter

        assert "Provide ai_formatter for Step 3 retry" in str(exc_info.value)

    # === Constants Tests ===

    def test_constants_exported(self):
        """Issue #292: 定数がエクスポートされている"""
        assert AI_FORMATTER_MAX_INPUT_CHARS == 8000
        assert isinstance(RELAXED_PATTERNS, list)
        assert len(RELAXED_PATTERNS) >= 6
        # FORMATTER_PROMPT uses "Result:" format for strict parser compatibility
        assert "Result:" in FORMATTER_PROMPT

    def test_relaxed_patterns_all_valid_values_only(self):
        """Issue #292: 全パターンが有効な Verdict 値のみをマッチする"""
        import re
        for pattern in RELAXED_PATTERNS:
            # Each pattern should contain the explicit value list
            assert "PASS|RETRY|BACK_DESIGN|ABORT" in pattern or \
                   re.search(r"\(PASS\|RETRY\|BACK_DESIGN\|ABORT\)", pattern)

    # === Validation Tests (Issue #292 レビュー対応) ===

    def test_max_retries_validation(self):
        """Issue #292: max_retries < 1 は ValueError"""
        malformed_text = "no verdict here"

        def mock_formatter(text: str) -> str:
            return "## VERDICT\n- Result: PASS"

        with pytest.raises(ValueError) as exc_info:
            parse_verdict(malformed_text, ai_formatter=mock_formatter, max_retries=0)

        assert "max_retries must be >= 1" in str(exc_info.value)

    def test_invalid_value_in_step3_raises_immediately(self):
        """Issue #292: Step 3 で不正値が返された場合も即 raise"""
        malformed_text = "no verdict here"

        def bad_formatter(text: str) -> str:
            return "## VERDICT\n- Result: WRONG_VALUE"

        with pytest.raises(InvalidVerdictValueError) as exc_info:
            parse_verdict(malformed_text, ai_formatter=bad_formatter)

        assert "Invalid VERDICT value: WRONG_VALUE" in str(exc_info.value)

    def test_create_ai_formatter_basic(self):
        """Issue #292: create_ai_formatter が正しく動作する"""
        # Mock tool that returns formatted output
        class MockReviewerTool:
            def run(self, prompt: str, context: str, **kwargs):
                return "## VERDICT\n- Result: PASS\n- Reason: Mock", None

        formatter = create_ai_formatter(MockReviewerTool())
        result = formatter("some input")
        assert "Result: PASS" in result


@allure.title("State transition matrix: covers all valid paths")
@allure.description("Verifies all valid state transitions work correctly.")
class TestStateTransitionMatrix:
    """ステート遷移マトリクスの網羅テスト"""

    @pytest.mark.parametrize(
        "current,next_state,expected_label",
        [
            # INVESTIGATE_REVIEW transitions
            (State.INVESTIGATE_REVIEW, State.DETAIL_DESIGN, "PASS"),
            (State.INVESTIGATE_REVIEW, State.INVESTIGATE, "RETRY"),
            # DETAIL_DESIGN_REVIEW transitions
            (State.DETAIL_DESIGN_REVIEW, State.IMPLEMENT, "PASS"),
            (State.DETAIL_DESIGN_REVIEW, State.DETAIL_DESIGN, "RETRY"),
            # IMPLEMENT_REVIEW transitions (QA統合)
            (State.IMPLEMENT_REVIEW, State.PR_CREATE, "PASS"),
            (State.IMPLEMENT_REVIEW, State.IMPLEMENT, "RETRY"),
            (State.IMPLEMENT_REVIEW, State.DETAIL_DESIGN, "BACK_DESIGN"),
        ],
    )
    def test_review_state_transitions(self, current, next_state, expected_label):
        """レビューステートの遷移ラベルが正しい"""
        label = infer_result_label(current, next_state)
        assert label == expected_label

    @pytest.mark.parametrize(
        "current,next_state",
        [
            (State.INIT, State.INVESTIGATE),
            (State.INVESTIGATE, State.INVESTIGATE_REVIEW),
            (State.DETAIL_DESIGN, State.DETAIL_DESIGN_REVIEW),
            (State.IMPLEMENT, State.IMPLEMENT_REVIEW),
            (State.PR_CREATE, State.COMPLETE),
        ],
    )
    def test_work_state_transitions_always_pass(self, current, next_state):
        """作業ステートは常にPASSを返す"""
        label = infer_result_label(current, next_state)
        assert label == "PASS"

    @pytest.mark.parametrize(
        "current,next_state",
        [
            (State.INVESTIGATE_REVIEW, State.COMPLETE),
            (State.INVESTIGATE_REVIEW, State.PR_CREATE),
            (State.DETAIL_DESIGN_REVIEW, State.COMPLETE),
            (State.IMPLEMENT_REVIEW, State.INVESTIGATE),
        ],
    )
    def test_invalid_transitions_return_unknown(self, current, next_state):
        """無効な遷移はUNKNOWNを返す"""
        label = infer_result_label(current, next_state)
        assert label == "UNKNOWN"


@allure.title("Invalid VERDICT validation: ABORT on disallowed VERDICTs")
@allure.description("Verifies that handlers raise AgentAbortError for disallowed VERDICTs per Issue #194.")
class TestInvalidVerdictAbort:
    """無効なVERDICTでABORTするテスト（Issue #194 VERDICT対応表準拠）"""

    def test_init_aborts_on_retry(self):
        """INITでRETRYが返されるとAgentAbortError"""
        verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Need retry\n- Suggestion: Redo"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_retry],
            implementer_responses=[],
        )
        state = SessionState()

        with pytest.raises(AgentAbortError) as exc_info:
            handle_init(ctx, state)

        assert "Invalid VERDICT 'RETRY' for INIT state" in exc_info.value.reason
        assert "only PASS allowed" in exc_info.value.reason

    def test_init_aborts_on_back_design(self):
        """INITでBACK_DESIGNが返されるとAgentAbortError"""
        verdict_back = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Needs redesign"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_back],
            implementer_responses=[],
        )
        state = SessionState()

        with pytest.raises(AgentAbortError) as exc_info:
            handle_init(ctx, state)

        assert "Invalid VERDICT 'BACK_DESIGN' for INIT state" in exc_info.value.reason

    def test_investigate_review_aborts_on_back_design(self):
        """INVESTIGATE_REVIEWでBACK_DESIGNが返されるとAgentAbortError"""
        verdict_back = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Needs redesign"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_back],
            implementer_responses=[],
        )
        state = SessionState()

        with pytest.raises(AgentAbortError) as exc_info:
            handle_investigate_review(ctx, state)

        assert "Invalid VERDICT 'BACK_DESIGN' for INVESTIGATE_REVIEW" in exc_info.value.reason
        assert "BACK_DESIGN not allowed" in exc_info.value.reason

    def test_detail_design_review_aborts_on_back_design(self):
        """DETAIL_DESIGN_REVIEWでBACK_DESIGNが返されるとAgentAbortError"""
        verdict_back = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Needs redesign"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_back],
            implementer_responses=[],
        )
        state = SessionState()
        state.active_conversations["Design_Thread_conversation_id"] = "session-123"

        with pytest.raises(AgentAbortError) as exc_info:
            handle_detail_design_review(ctx, state)

        assert "Invalid VERDICT 'BACK_DESIGN' for DETAIL_DESIGN_REVIEW" in exc_info.value.reason
        assert "BACK_DESIGN not allowed" in exc_info.value.reason

    def test_implement_review_allows_back_design(self):
        """IMPLEMENT_REVIEWではBACK_DESIGNが許可される（ABORTしない）"""
        verdict_back = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Design issue found"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_back],
            implementer_responses=[],
        )
        state = SessionState()
        state.active_conversations["Implement_Loop_conversation_id"] = "session-456"

        # Should not raise, should return DETAIL_DESIGN
        result = handle_implement_review(ctx, state)
        assert result == State.DETAIL_DESIGN


@allure.title("Loop counter behavior: increments and resets correctly")
@allure.description("Verifies loop counters work as expected during retries.")
class TestLoopCounterBehavior:
    """ループカウンターの動作テスト"""

    def test_investigate_loop_increments_on_retry(self):
        """INVESTIGATE_REVIEWでRETRY時にカウンターがインクリメント"""
        verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Need more\n- Suggestion: Redo"
        verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
        ctx = create_test_context(
            analyzer_responses=["result1", "result2", "result3"],
            reviewer_responses=[verdict_retry, verdict_retry, verdict_pass],
            implementer_responses=[],
        )
        state = SessionState()

        # 1st cycle: INVESTIGATE -> INVESTIGATE_REVIEW (RETRY)
        handle_investigate(ctx, state)
        assert state.loop_counters["Investigate_Loop"] == 1
        result = handle_investigate_review(ctx, state)
        assert result == State.INVESTIGATE

        # 2nd cycle: INVESTIGATE -> INVESTIGATE_REVIEW (RETRY)
        handle_investigate(ctx, state)
        assert state.loop_counters["Investigate_Loop"] == 2
        result = handle_investigate_review(ctx, state)
        assert result == State.INVESTIGATE

        # 3rd cycle: INVESTIGATE -> INVESTIGATE_REVIEW (PASS)
        handle_investigate(ctx, state)
        assert state.loop_counters["Investigate_Loop"] == 3
        result = handle_investigate_review(ctx, state)
        assert result == State.DETAIL_DESIGN

    def test_implement_loop_increments_on_retry(self):
        """IMPLEMENT_REVIEWでRETRY時にカウンターがインクリメント"""
        verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Fix tests\n- Suggestion: Redo"
        verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_retry, verdict_pass],
            implementer_responses=["impl1", "impl2"],
        )
        state = SessionState()

        # 1st cycle: IMPLEMENT -> IMPLEMENT_REVIEW (RETRY)
        handle_implement(ctx, state)
        assert state.loop_counters["Implement_Loop"] == 1
        result = handle_implement_review(ctx, state)
        assert result == State.IMPLEMENT

        # 2nd cycle: IMPLEMENT -> IMPLEMENT_REVIEW (PASS)
        handle_implement(ctx, state)
        assert state.loop_counters["Implement_Loop"] == 2
        result = handle_implement_review(ctx, state)
        assert result == State.PR_CREATE


@allure.title("BACK_DESIGN path: returns to design from implementation")
@allure.description("Verifies BACK_DESIGN transition works correctly.")
class TestBackDesignPath:
    """BACK_DESIGN遷移パスのテスト"""

    def test_implement_review_back_design_returns_to_detail_design(self):
        """IMPLEMENT_REVIEWでBACK_DESIGNを返すとDETAIL_DESIGNに戻る"""
        verdict_back = (
            "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Architecture issue\n"
            "- Suggestion: Reconsider approach"
        )
        ctx = create_test_context(
            analyzer_responses=[],
            reviewer_responses=[verdict_back],
            implementer_responses=[],
        )
        state = SessionState()

        result = handle_implement_review(ctx, state)
        assert result == State.DETAIL_DESIGN
        assert "IMPLEMENT_REVIEW" not in state.completed_states  # 完了していない

    def test_back_design_loop_full_cycle(self):
        """BACK_DESIGN後の完全なサイクル"""
        verdict_back = "## VERDICT\n- Result: BACK_DESIGN\n- Reason: Need redesign"
        verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: OK"
        ctx = create_test_context(
            analyzer_responses=["design1", "design2"],
            reviewer_responses=[verdict_back, verdict_pass, verdict_pass],
            implementer_responses=["impl1", "impl2"],
        )
        state = SessionState()

        # First implementation: BACK_DESIGN
        handle_implement(ctx, state)
        result = handle_implement_review(ctx, state)
        assert result == State.DETAIL_DESIGN

        # Redesign cycle
        handle_detail_design(ctx, state)
        result = handle_detail_design_review(ctx, state)
        assert result == State.IMPLEMENT

        # Second implementation: PASS
        handle_implement(ctx, state)
        result = handle_implement_review(ctx, state)
        assert result == State.PR_CREATE


@allure.title("Full workflow: end-to-end scenarios")
@allure.description("Tests complete workflows from INIT to COMPLETE.")
class TestFullWorkflow:
    """エンドツーエンドのワークフローテスト"""

    def test_happy_path_all_pass(self):
        """ハッピーパス: 全てPASSで一発完了"""
        # create_test_context は MockIssueProvider を使用するため
        # post_issue_comment のモック不要（GitHub API呼び出しなし）

        verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: All good"
        ctx = create_test_context(
            analyzer_responses=["investigate", "design"],
            reviewer_responses=[verdict_pass, verdict_pass, verdict_pass, verdict_pass],
            implementer_responses=["implement", "pr"],
            run_timestamp="test_happy_path",
        )
        config = ExecutionConfig(
            mode=ExecutionMode.FULL,
            issue_url="https://github.com/test/repo/issues/1",
            issue_number=1,
        )

        # ワークフローが正常完了することを確認（例外なし）
        run(config, ctx=ctx)

        # ログファイルで全ステートが実行されたことを確認
        log_path = ctx.artifacts_dir / "run.log"
        log_content = log_path.read_text()
        expected_states = [
            "INIT", "INVESTIGATE", "INVESTIGATE_REVIEW",
            "DETAIL_DESIGN", "DETAIL_DESIGN_REVIEW",
            "IMPLEMENT", "IMPLEMENT_REVIEW", "PR_CREATE",
        ]
        for state in expected_states:
            assert f'"state": "{state}"' in log_content

    def test_workflow_with_one_retry_per_stage(self):
        """各ステージで1回RETRYするワークフロー"""
        # create_test_context は MockIssueProvider を使用するため
        # post_issue_comment のモック不要（GitHub API呼び出しなし）

        verdict_retry = "## VERDICT\n- Result: RETRY\n- Reason: Not good enough"
        verdict_pass = "## VERDICT\n- Result: PASS\n- Reason: Good now"

        # Pattern: RETRY then PASS for each review stage
        ctx = create_test_context(
            analyzer_responses=["inv1", "inv2", "design1", "design2"],
            reviewer_responses=[
                verdict_pass,      # INIT: PASS
                verdict_retry,     # INVESTIGATE_REVIEW: RETRY
                verdict_pass,      # INVESTIGATE_REVIEW: PASS
                verdict_retry,     # DETAIL_DESIGN_REVIEW: RETRY
                verdict_pass,      # DETAIL_DESIGN_REVIEW: PASS
                verdict_retry,     # IMPLEMENT_REVIEW: RETRY
                verdict_pass,      # IMPLEMENT_REVIEW: PASS
            ],
            implementer_responses=["impl1", "impl2", "pr"],
            run_timestamp="test_retry_path",
        )
        config = ExecutionConfig(
            mode=ExecutionMode.FULL,
            issue_url="https://github.com/test/repo/issues/1",
            issue_number=1,
        )

        # ワークフローが正常完了することを確認
        run(config, ctx=ctx)

        # ログファイルでRETRYが記録されていることを確認
        log_path = ctx.artifacts_dir / "run.log"
        log_content = log_path.read_text()

        # 各レビューステートでRETRYが発生
        retry_count = log_content.count('"result": "RETRY"')
        assert retry_count >= 3, f"Expected at least 3 RETRYs, got {retry_count}"


@allure.title("State enum: complete coverage")
@allure.description("Verifies all states are accounted for.")
class TestStateEnumCoverage:
    """State Enumの網羅性テスト"""

    def test_all_states_have_handlers(self):
        """全ステート（COMPLETE以外）にハンドラーがある"""
        from bugfix_agent_orchestrator import STATE_HANDLERS

        for state in State:
            if state != State.COMPLETE:
                assert state in STATE_HANDLERS, f"Missing handler for {state}"

    def test_state_count_is_nine(self):
        """ステート数が9（QA/QA_REVIEW削除後）"""
        assert len(State) == 9

    def test_state_names(self):
        """ステート名が正しい"""
        expected_names = {
            "INIT",
            "INVESTIGATE",
            "INVESTIGATE_REVIEW",
            "DETAIL_DESIGN",
            "DETAIL_DESIGN_REVIEW",
            "IMPLEMENT",
            "IMPLEMENT_REVIEW",
            "PR_CREATE",
            "COMPLETE",
        }
        actual_names = {s.name for s in State}
        assert actual_names == expected_names
