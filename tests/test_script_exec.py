"""Tests for execute_script subprocess dispatch (Issue #204)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.console_log import configure_console_logging
from kaji_harness.errors import ScriptExecutionError, StepTimeoutError
from kaji_harness.models import Step
from kaji_harness.script_exec import execute_exec, execute_script


def _make_step() -> Step:
    return Step(id="s1", skill="dummy", agent=None, on={"PASS": "end"})


def _mock_popen(
    *,
    stdout_lines: list[str],
    returncode: int = 0,
    stderr: str = "",
) -> MagicMock:
    proc = MagicMock()
    proc.stdout = iter(stdout_lines)
    # script_exec は stderr を並行 drain するため、iterable として返す。
    stderr_lines = [stderr] if stderr else []
    proc.stderr = iter(stderr_lines)
    proc.wait.return_value = None
    proc.returncode = returncode
    return proc


@pytest.mark.small
class TestExecuteScriptDispatch:
    def test_argv_uses_sys_executable_and_dash_m(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _mock_popen(stdout_lines=["---VERDICT---\n", "status: PASS\n"])

        with patch("kaji_harness.script_exec.subprocess.Popen", side_effect=fake_popen):
            execute_script(
                step=_make_step(),
                module="some.module.path",
                env={"KAJI_ISSUE_ID": "204"},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )

        args = captured["args"]
        assert args[1:] == ["-m", "some.module.path"]
        assert args[0].endswith("python") or "python" in args[0]
        # shell=False は明示またはデフォルト
        assert captured["kwargs"].get("shell", False) is False

    def test_env_merges_into_os_environ(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
            captured["env"] = kwargs.get("env")
            return _mock_popen(stdout_lines=[])

        with patch("kaji_harness.script_exec.subprocess.Popen", side_effect=fake_popen):
            execute_script(
                step=_make_step(),
                module="m",
                env={"KAJI_ISSUE_ID": "204", "KAJI_STEP_ID": "review-poll"},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )

        env = captured["env"]
        assert env["KAJI_ISSUE_ID"] == "204"
        assert env["KAJI_STEP_ID"] == "review-poll"
        # os.environ も merge されていること（PATH 等の一般的 env が残る）
        assert "PATH" in env

    def test_stale_optional_reserved_env_is_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # parent shell に残った KAJI_PR_ID は、現在 step の env に含まれない限り
        # subprocess へ伝播してはならない（skill-authoring.md の env 契約）。
        monkeypatch.setenv("KAJI_PR_ID", "999")
        monkeypatch.setenv("KAJI_PR_REF", "gh:999")

        captured: dict[str, Any] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
            captured["env"] = kwargs.get("env")
            return _mock_popen(stdout_lines=[])

        with patch("kaji_harness.script_exec.subprocess.Popen", side_effect=fake_popen):
            execute_script(
                step=_make_step(),
                module="m",
                env={"KAJI_ISSUE_ID": "204"},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )

        env = captured["env"]
        assert "KAJI_PR_ID" not in env
        assert "KAJI_PR_REF" not in env

    def test_optional_reserved_env_passes_when_provided(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 現在 step の env に明示注入された場合は parent shell の値ではなく
        # step 値が subprocess へ届くこと。
        monkeypatch.setenv("KAJI_PR_ID", "999")

        captured: dict[str, Any] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
            captured["env"] = kwargs.get("env")
            return _mock_popen(stdout_lines=[])

        with patch("kaji_harness.script_exec.subprocess.Popen", side_effect=fake_popen):
            execute_script(
                step=_make_step(),
                module="m",
                env={"KAJI_PR_ID": "207", "KAJI_PR_REF": "gh:207"},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )

        env = captured["env"]
        assert env["KAJI_PR_ID"] == "207"
        assert env["KAJI_PR_REF"] == "gh:207"

    def test_returncode_zero_with_verdict_returns_cli_result(self, tmp_path: Path) -> None:
        lines = [
            "---VERDICT---\n",
            "status: PASS\n",
            "reason: |\n",
            "  ok\n",
            "---END_VERDICT---\n",
        ]
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=lines, returncode=0),
        ):
            result = execute_script(
                step=_make_step(),
                module="m",
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )
        assert "---VERDICT---" in result.full_output
        assert result.session_id is None
        assert result.cost is None

    def test_returncode_zero_empty_stdout(self, tmp_path: Path) -> None:
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=[], returncode=0),
        ):
            result = execute_script(
                step=_make_step(),
                module="m",
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )
        assert result.full_output == ""

    def test_nonzero_returncode_raises_even_with_verdict(self, tmp_path: Path) -> None:
        lines = [
            "---VERDICT---\n",
            "status: PASS\n",
            "---END_VERDICT---\n",
        ]
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=lines, returncode=2, stderr="dependency error"),
        ):
            with pytest.raises(ScriptExecutionError) as exc_info:
                execute_script(
                    step=_make_step(),
                    module="m",
                    env={},
                    workdir=tmp_path,
                    log_dir=tmp_path / "log",
                    timeout=60,
                    verbose=False,
                )
        assert exc_info.value.returncode == 2
        assert "dependency error" in str(exc_info.value)

    def test_timeout_raises_step_timeout(self, tmp_path: Path) -> None:
        # Popen returncode は本物のプロセスを使うと安定しないので、
        # _kill が timed_out.set() を呼んだ後で wait() が返り、
        # 最終 returncode が -15 / 0 のどちらでも StepTimeoutError を期待する。
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        proc.wait.return_value = None
        proc.returncode = -15
        proc.terminate = MagicMock()

        def slow_iter() -> Any:
            # timer が発火する余地を作るため、最低限の遅延を入れる
            import time

            time.sleep(0.3)
            return

        proc.stdout = []  # iterable で即終了

        with (
            patch("kaji_harness.script_exec.subprocess.Popen", return_value=proc),
            patch("kaji_harness.script_exec.threading.Timer") as timer_cls,
        ):
            # Timer を即座に発火させる: start() で _kill コールバックを呼ぶ
            timer_instance = MagicMock()
            captured: dict[str, Any] = {}

            def fake_timer(interval: float, fn: Any, *args: Any, **kwargs: Any) -> Any:
                captured["fn"] = fn
                return timer_instance

            timer_cls.side_effect = fake_timer

            def fake_start() -> None:
                # immediate kill: set the flag like _kill would
                captured["fn"]()

            timer_instance.start.side_effect = fake_start

            with pytest.raises(StepTimeoutError):
                execute_script(
                    step=_make_step(),
                    module="m",
                    env={},
                    workdir=tmp_path,
                    log_dir=tmp_path / "log",
                    timeout=1,
                    verbose=False,
                )

    def test_writes_log_files(self, tmp_path: Path) -> None:
        lines = ["hello\n", "world\n"]
        log_dir = tmp_path / "log"
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=lines, returncode=0, stderr="some stderr"),
        ):
            execute_script(
                step=_make_step(),
                module="m",
                env={},
                workdir=tmp_path,
                log_dir=log_dir,
                timeout=60,
                verbose=False,
            )
        assert (log_dir / "stdout.log").read_text() == "hello\nworld\n"
        assert (log_dir / "console.log").read_text() == "hello\nworld\n"
        assert (log_dir / "stderr.log").read_text() == "some stderr"


@pytest.mark.small
class TestStepAgentOptional:
    def test_step_constructs_with_agent_none(self) -> None:
        step = Step(id="s", skill="k", agent=None, on={"PASS": "end"})
        assert step.agent is None

    def test_step_default_agent_is_none(self) -> None:
        step = Step(id="s", skill="k", on={"PASS": "end"})
        assert step.agent is None


@pytest.mark.small
class TestExecStartProgress:
    """Issue #235: `_run_argv` が起動コンソールへ exec start progress を出す。"""

    def test_exec_start_logged_with_full_argv(
        self,
        clean_kaji_console_root: None,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        configure_console_logging(logging.INFO)
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=["---VERDICT---\n", "status: PASS\n"]),
        ):
            execute_exec(
                step=_make_step(),
                argv=["echo", "hello", "world"],
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )
        out = capsys.readouterr().out
        assert "exec start: echo hello world" in out
        assert "[kaji]" in out

    def test_exec_start_truncates_long_argv(
        self,
        clean_kaji_console_root: None,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        configure_console_logging(logging.INFO)
        long_arg = "x" * 500
        with patch(
            "kaji_harness.script_exec.subprocess.Popen",
            return_value=_mock_popen(stdout_lines=["---VERDICT---\n", "status: PASS\n"]),
        ):
            execute_exec(
                step=_make_step(),
                argv=["run", long_arg],
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=60,
                verbose=False,
            )
        exec_line = next(ln for ln in capsys.readouterr().out.splitlines() if "exec start:" in ln)
        assert exec_line.endswith("…")
