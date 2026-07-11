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

from kaji_harness.adapters import ClaudeAdapter, CodexAdapter, GeminiAdapter
from kaji_harness.cli import execute_cli, stream_and_log
from kaji_harness.errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from kaji_harness.models import Step


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
        # anomaly B fix: result event no longer replays as text
        assert "Done" not in result.full_output
        assert result.cost is not None
        assert result.cost.usd == 0.05

        # Verify raw log was written
        raw_log = (log_dir / "stdout.log").read_text()
        assert "system" in raw_log
        assert "assistant" in raw_log

    def test_claude_streaming_renders_tool_use_lines(self, tmp_path: Path) -> None:
        """tool_use blocks appear as [tool] ... lines in full_output and console.log (anomaly A)."""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-tool"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "kaji_harness/adapters.py"},
                            }
                        ]
                    },
                }
            ),
            json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.01}),
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

        assert "[tool] Bash $ ls -la" in result.full_output
        assert "[tool] Read kaji_harness/adapters.py" in result.full_output

        console = (log_dir / "console.log").read_text()
        assert "[tool] Bash $ ls -la" in console
        assert "[tool] Read kaji_harness/adapters.py" in console

    def test_claude_streaming_no_duplicate_final_text(self, tmp_path: Path) -> None:
        """Final assistant text appears only once in full_output (anomaly B)."""
        final_text = "## Final Verdict\nstatus: PASS"
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-dup"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": final_text}]},
                }
            ),
            # result event with same text mirrored — should not be re-emitted
            json.dumps({"type": "result", "result": final_text, "total_cost_usd": 0.02}),
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

        assert result.full_output.count(final_text) == 1
        assert result.cost is not None
        assert result.cost.usd == 0.02

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

    def test_codex_mcp_tool_call_unicode_decoded_across_sinks(self, tmp_path: Path) -> None:
        """Issue #137: mcp_tool_call の二重エンコード text が両 sink で可読化される。

        adapter → stream_and_log → console.log / CLIResult.full_output の配線を固定。
        raw の stdout.log には literal escape が残ることも同時に確認する。
        """
        double_encoded = '{"title": "config/workflow \\u306e\\u6697\\u9ed9"}'
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "thread-137"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "result": {"content": [{"type": "text", "text": double_encoded}]},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
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
        result = stream_and_log(process, adapter, "verify-design", log_dir, verbose=False)
        process.wait()

        # sink 1: CLIResult.full_output
        assert "config/workflow の暗黙" in result.full_output
        for esc in ("\\u306e", "\\u6697", "\\u9ed9"):
            assert esc not in result.full_output

        # sink 2: console.log
        console = (log_dir / "console.log").read_text()
        assert "config/workflow の暗黙" in console
        for esc in ("\\u306e", "\\u6697", "\\u9ed9"):
            assert esc not in console

        # raw sink: stdout.log は二重エンコードされた literal escape を保持する
        raw = (log_dir / "stdout.log").read_text()
        assert "\\u306e" in raw

    def test_codex_mcp_tool_call_outer_lone_surrogate_does_not_crash(self, tmp_path: Path) -> None:
        """Issue #137 review 回帰: 外側 JSONL の孤立サロゲートで stream_and_log が停止しない。

        `{"text": "\\ud83d"}` を JSON 行に含めると `cli.py` の行全体 `json.loads` で値が
        実サロゲート `'\\ud83d'` になる。修正前はこの値が console.log 書き込みで
        `UnicodeEncodeError` を起こして stream_and_log を破壊した。修正後は adapter 出口で
        literal escape へ戻り、(1) crash せず、(2) raw stdout.log は元の literal escape を
        保持し、(3) console.log / full_output も UTF-8 書き出し可能な literal escape になる。
        """
        lone_surrogate = "\ud83d"  # literal `\u` を含まない実サロゲート 1 文字
        assert "\\u" not in lone_surrogate
        # json.dumps(ensure_ascii=True) で行内は literal `\ud83d` として直列化される。
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "thread-137-lone"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "result": {
                            "content": [{"type": "text", "text": f"pre {lone_surrogate} post"}]
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
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
        # 修正前はここで UnicodeEncodeError により例外送出（回帰の核心）。
        result = stream_and_log(process, adapter, "verify-design", log_dir, verbose=False)
        process.wait()

        # sink 1 + 2: literal escape へ正規化され UTF-8 書き出し可能。
        assert "pre \\ud83d post" in result.full_output
        result.full_output.encode("utf-8")
        console = (log_dir / "console.log").read_text()
        assert "pre \\ud83d post" in console
        console.encode("utf-8")

        # raw sink: stdout.log は元 JSONL の literal escape をそのまま保持する。
        raw = (log_dir / "stdout.log").read_text()
        assert "\\ud83d" in raw

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
            "kaji_harness.cli.build_cli_args",
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
                    default_timeout=1800,
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

        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="test prompt",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=1800,
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

        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(StepTimeoutError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="test prompt",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=1800,
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

        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="test prompt",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=1800,
            )

        assert result.session_id == "s-123"
        assert "output text" in result.full_output
        assert result.cost is not None
        assert result.cost.usd == 0.01


@pytest.mark.medium
class TestTerminalEventBreak:
    """terminal event 観測時の break + terminate 挙動（local-p1-22）。"""

    def _write_leak_script(self, path: Path, jsonl_lines: list[str], sleep_after: int = 30) -> Path:
        """terminal event 出力後に stdout fd を保持したまま sleep する fake CLI。"""
        script = path / "leak_cli.sh"
        echos = "\n".join(f"echo '{line}'" for line in jsonl_lines)
        # exec sleep で stdout fd を保持し続ける（fd leak の最小再現）
        script.write_text(f"#!/bin/bash\n{echos}\nexec sleep {sleep_after}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _write_sigterm_trap_script(
        self, path: Path, jsonl_lines: list[str], trap_exit: int, sleep_after: int = 30
    ) -> Path:
        """terminal event 出力後に SIGTERM を trap し正値で exit する fake CLI。

        kaji が後始末で ``process.terminate()`` を撃つと bash が TERM trap を実行し、
        shell 慣例の正の終了コード（``128+15=143`` / ``128+9=137`` 等）を返す。
        Claude Code CLI の SIGTERM ハンドリング（trap して正値 exit）を決定論的に
        再現するための mock。``sleep & wait`` で background job を待つ間に SIGTERM を
        受けると ``wait`` が即時 return し、直後に trap が実行される。
        """
        script = path / f"trap_cli_{trap_exit}.sh"
        echos = "\n".join(f"echo '{line}'" for line in jsonl_lines)
        script.write_text(
            f"#!/bin/bash\ntrap 'exit {trap_exit}' TERM\n{echos}\nsleep {sleep_after} &\nwait\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def test_claude_terminal_event_breaks_before_eof(self, tmp_path: Path) -> None:
        """fd leak 再現: terminal event 観測直後に break + terminate して timeout を待たない。"""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-leak"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            ),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01}
            ),
        ]
        script = self._write_leak_script(tmp_path, jsonl_lines, sleep_after=30)

        step = Step(id="leak", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            import time

            start = time.monotonic()
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=15,
            )
            elapsed = time.monotonic() - start

        # terminate 後の grace 5s 以内に戻る（30s sleep を待たない）
        assert elapsed < 10, f"expected fast return via terminal event, got {elapsed:.1f}s"
        assert result.terminal_seen is True
        assert result.session_id == "sess-leak"
        assert result.cost is not None and result.cost.usd == 0.01

    def test_terminal_event_observed_does_not_raise_timeout(self, tmp_path: Path) -> None:
        """terminal event を見た直後に timer が発火する race でも StepTimeoutError を出さない。"""
        # default_timeout=2: terminal event は echo 直後に観測 → grace wait 中に timer 発火しても
        # terminal_seen=True により StepTimeoutError は抑制される。
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-race"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            ),
        ]
        script = self._write_leak_script(tmp_path, jsonl_lines, sleep_after=10)

        step = Step(id="race", skill="t", agent="claude", timeout=2, on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=1800,
            )
        assert result.terminal_seen is True
        assert result.session_id == "sess-race"

    def test_codex_turn_failed_is_terminal(self, tmp_path: Path) -> None:
        """Codex turn.failed で break、error_messages 集約、CLIExecutionError 発火。"""
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "t-fail"}),
            json.dumps({"type": "turn.failed", "error": {"message": "capacity"}}),
        ]
        # turn.failed 後 sleep して fd 保持 + exit 1
        script = tmp_path / "codex_fail.sh"
        echos = "\n".join(f"echo '{line}'" for line in jsonl_lines)
        script.write_text(f"#!/bin/bash\n{echos}\nsleep 0.2\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        step = Step(id="fail", skill="t", agent="codex", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=10,
                )
        assert "capacity" in str(exc_info.value)

    def test_codex_error_event_does_not_break_early(self, tmp_path: Path) -> None:
        """Codex `error` event 単体では break せず、後続 turn.failed まで読み切る。"""
        jsonl_lines = [
            json.dumps({"type": "thread.started", "thread_id": "t-err"}),
            json.dumps({"type": "error", "message": "transient blip"}),
            json.dumps({"type": "turn.failed", "error": {"message": "final reason"}}),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)

        step = Step(id="err", skill="t", agent="codex", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=10,
                )
        # 両方が error_messages に集約されているはず
        msg = str(exc_info.value)
        assert "transient blip" in msg or "final reason" in msg

    def test_no_terminal_event_falls_back_to_eof(self, tmp_path: Path) -> None:
        """terminal event を出さず exit する CLI は EOF 経路で従来通り完了。"""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-eof"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "no result"}]},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=0)

        step = Step(id="eof", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=10,
            )
        assert result.terminal_seen is False
        assert result.session_id == "sess-eof"
        assert "no result" in result.full_output

    def test_claude_failure_terminal_raises_cli_execution_error(self, tmp_path: Path) -> None:
        """Claude `result` の subtype:error / is_error:true は failure terminal として CLIExecutionError。"""
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-fail"}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "total_cost_usd": 0.01,
                }
            ),
        ]
        # terminal event 後 stdout fd を保持して fd leak を再現（exec sleep）。
        # 我々の terminate 後 returncode は -15 になるが、terminal_failure で失敗判定する。
        script = tmp_path / "claude_fail.sh"
        echos = "\n".join(f"echo '{line}'" for line in jsonl_lines)
        script.write_text(f"#!/bin/bash\n{echos}\nexec sleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        step = Step(id="cfail", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=15,
                )
        assert exc_info.value.step_id == "cfail"

    def test_gemini_failure_terminal_raises_cli_execution_error(self, tmp_path: Path) -> None:
        """Gemini `result` の status:error は failure terminal として CLIExecutionError。"""
        jsonl_lines = [
            json.dumps({"type": "init", "session_id": "g-fail", "model": "auto"}),
            json.dumps(
                {
                    "type": "result",
                    "status": "error",
                    "stats": {"input_tokens": 10, "output_tokens": 0},
                }
            ),
        ]
        script = tmp_path / "gemini_fail.sh"
        echos = "\n".join(f"echo '{line}'" for line in jsonl_lines)
        script.write_text(f"#!/bin/bash\n{echos}\nexec sleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        step = Step(id="gfail", skill="t", agent="gemini", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=15,
                )
        assert exc_info.value.step_id == "gfail"

    def test_claude_success_terminal_with_self_exit_nonzero_returns_result(
        self, tmp_path: Path
    ) -> None:
        """`result` が success なら CLI が自発的に exit 1 しても CLIResult を返す。

        bc34906 で追加された旧 ``test_claude_success_terminal_with_self_exit_nonzero_raises``
        の期待値を反転したテスト。terminal success event を観測した後は returncode を
        失敗根拠にしない（gl:25 設計書 § 既存テストへの影響）。terminal success 後の
        正の returncode は CLI 側 cleanup のノイズであり、失敗扱いすべきではない。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-sx"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            ),
        ]
        # exec sleep せず即 exit 1 → process.returncode = 1（自発 exit、SIGTERM ではない）
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)

        step = Step(id="sx", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=10,
            )
        assert result.terminal_seen is True
        assert result.session_id == "sess-sx"
        # Issue #222 / codex P2: プロセスが自発 exit した場合（kaji terminate 不要）は
        # その returncode が attempt の真の終了として保持される。
        assert result.exit_code == 1
        assert result.signal is None

    def test_claude_success_terminal_with_sigterm_trap_exit_143_passes(
        self, tmp_path: Path
    ) -> None:
        """terminal success 観測後の terminate で SIGTERM trap exit 143 でも CLIResult を返す。

        本 Issue (gl:25) の再現テスト。
        OB: bc34906 の ``self_exit_failure = (143 > 0) = True`` により、成功した
        terminal success ステップが ``CLIExecutionError: ... code 143`` で誤って例外化される。
        EB: terminal success event を真実とし、kaji が後始末で撃った terminate 起因の
        returncode（143）は失敗根拠から除外する。

        bc34906 直後の cli.py に対しては FAIL する（リグレッション検知）。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-143"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01}
            ),
        ]
        script = self._write_sigterm_trap_script(tmp_path, jsonl_lines, trap_exit=143)

        step = Step(id="t143", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=15,
            )
        assert result.terminal_seen is True
        assert result.session_id == "sess-143"
        assert result.cost is not None and result.cost.usd == 0.01
        # Issue #222 / codex P2: terminal success 観測後にプロセスが自発 exit せず
        # kaji が後始末で terminate した場合、その SIGTERM 起因 returncode(143) は
        # attempt の終了ではない（routine cleanup）。result.json の exit_code / signal を
        # 汚さないよう None に縮退させ、成功 attempt を signal 終了に見せない。
        assert result.exit_code is None
        assert result.signal is None

    def test_claude_success_terminal_with_positive_137_returncode_variant_passes(
        self, tmp_path: Path
    ) -> None:
        """terminal success 観測後の terminate で正の 137 returncode バリアントでも CLIResult を返す。

        137 は shell 慣例の ``128+9`` を模した「正の returncode バリアント」としての検証。
        Python の ``Popen.kill()`` が直接 SIGKILL した場合の returncode は ``-9`` だが、
        本テストの主眼は returncode の値に依存せず terminal success event を真実とすること
        （review-design § 改善提案）。bc34906 直後の cli.py に対しては FAIL する。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-137"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            ),
        ]
        script = self._write_sigterm_trap_script(tmp_path, jsonl_lines, trap_exit=137)

        step = Step(id="t137", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=15,
            )
        assert result.terminal_seen is True
        assert result.session_id == "sess-137"
        # harness terminate 起因の returncode は値に依らず attempt 終了に取り込まない。
        assert result.exit_code is None
        assert result.signal is None

    def test_claude_success_terminal_with_default_sigterm_exit_passes(self, tmp_path: Path) -> None:
        """trap なしの CLI は SIGTERM 既定で returncode -15。terminal success なら CLIResult を返す。

        ``exec sleep`` で stdout fd を保持する CLI に terminate を撃つと、trap がないため
        SIGTERM の既定挙動でプロセスが死に returncode は ``-15``（負値）になる。
        bc34906 直後の cli.py でも ``self_exit_failure = (-15 > 0) = False`` のため例外化
        されないが、案 B 採用後も returncode 値に依存せず PASS となることを網羅確認する。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-neg15"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            ),
        ]
        script = self._write_leak_script(tmp_path, jsonl_lines, sleep_after=30)

        step = Step(id="tneg15", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            result = execute_cli(
                step=step,
                prompt="x",
                workdir=tmp_path,
                session_id=None,
                log_dir=tmp_path / "logs",
                execution_policy="auto",
                verbose=False,
                default_timeout=15,
            )
        assert result.terminal_seen is True
        assert result.session_id == "sess-neg15"
        # 負値 returncode(-15) も harness terminate 起因なら attempt 終了に残さない。
        assert result.exit_code is None
        assert result.signal is None

    def test_claude_success_terminal_with_error_event_still_raises(self, tmp_path: Path) -> None:
        """terminal が success でも stream 中に error イベントがあれば CLIExecutionError。

        案 B 採用後も ``error_messages`` 経路の失敗判定は保持される（Issue gl:25 完了条件・
        設計書 § Medium テスト 3）。terminal success event 単独では失敗扱いにならないが、
        stream 中の ``error`` イベント集約は引き続き例外化の根拠になる。

        Issue #196 注: 本 Issue では Claude を変更しない。
        ``ClaudeAdapter.treats_stream_error_as_failure()`` は ``True`` を返すため、
        既存契約は維持される。Claude の stream-level ``error`` event の recoverable /
        fatal 区別に関する一次情報が得られるまで現契約を続ける。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-errev"}),
            json.dumps({"type": "error", "message": "stream-level failure"}),
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=0)

        step = Step(id="errev", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=10,
                )
        assert "stream-level failure" in str(exc_info.value)

    def test_no_terminal_event_nonzero_exit_raises(self, tmp_path: Path) -> None:
        """terminal event を出さず exit 1 する CLI は従来どおり CLIExecutionError（非 terminal 経路不変）。

        案 B は ``terminal_seen`` 分岐のみを変更する。terminal event 未観測の経路
        （``cli.py`` の ``if process.returncode != 0:``）は不変であることを確認する
        （設計書 § Medium テスト 4）。
        """
        jsonl_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-noterm"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "no result event"}]},
                }
            ),
        ]
        script = _create_mock_cli_script(tmp_path, jsonl_lines, exit_code=1)

        step = Step(id="noterm", skill="t", agent="claude", on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(CLIExecutionError) as exc_info:
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=10,
                )
        assert exc_info.value.step_id == "noterm"

    def test_timer_still_guards_when_no_terminal_event(self, tmp_path: Path) -> None:
        """terminal event なし & stdout 閉じない場合は最終ガードの timeout が効く。"""
        script = tmp_path / "stuck.sh"
        # JSON を一切出さず無限 sleep
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        step = Step(id="stuck", skill="t", agent="claude", timeout=2, on={"PASS": "end"})
        with patch("kaji_harness.cli.build_cli_args", return_value=[str(script)]):
            with pytest.raises(StepTimeoutError):
                execute_cli(
                    step=step,
                    prompt="x",
                    workdir=tmp_path,
                    session_id=None,
                    log_dir=tmp_path / "logs",
                    execution_policy="auto",
                    verbose=False,
                    default_timeout=1800,
                )
