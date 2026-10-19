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
from kaji_harness.recovery import FailureEvent, FailureSnapshot, compute_signature
from kaji_harness.recovery.models import FailureClassification
from kaji_harness.recovery.report import sanitize_evidence
from kaji_harness.script_exec import _stderr_summary, execute_exec, execute_script


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


# ============================================================
# Issue #516: ScriptExecutionError のメッセージに stderr 末尾の原因行を残す
# ============================================================


def _pytest_stderr(
    cause: str,
    *,
    root: str = "/home/a/kaji-fix-489",
    ts: str = "2026-10-18T03:55:46Z",
    run_id: str = "261018124412",
    duration: str = "312.45",
) -> str:
    """baseline_precheck のように pytest 出力の後に原因行で終わる stderr を作る。"""
    lines = [
        "=" * 29 + " test session starts " + "=" * 30,
        f"platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- {root}/.venv/bin/python",
        "cachedir: .pytest_cache",
        f"rootdir: {root}",
        f"started at {ts} run {run_id}",
        f"=== 3488 passed, 7 skipped in {duration}s ===",
        "Traceback (most recent call last):",
        f'  File "{root}/kaji_harness/fsio.py", line 24, in atomic_write',
        "    path.parent.mkdir(parents=True, exist_ok=True)",
        cause,
    ]
    return "\n".join(lines) + "\n"


def _erofs(root: str) -> str:
    return f"OSError: [Errno 30] Read-only file system: '{root}/.kaji-artifacts'"


def _error(stderr: str) -> ScriptExecutionError:
    """送出箇所（``_run_argv``）と同じ組み立てで例外を作る。"""
    return ScriptExecutionError(
        "baseline",
        "kaji_harness.scripts.baseline_precheck",
        1,
        stderr,
        summary=_stderr_summary(stderr),
    )


def _attempt_error(stderr: str) -> str:
    """runner が ``result.json.error`` に書く形（``runner.py``）。"""
    exc = _error(stderr)
    return f"{type(exc).__name__}: {exc}"


def _hash(stderr: str) -> str:
    snapshot = FailureSnapshot(
        run_id="261018124412",
        run_dir=Path("/nonexistent/runs/261018124412"),
        attempt_error=_attempt_error(stderr),
        failure_event=FailureEvent(
            kind="dispatch_exception", exception_type="ScriptExecutionError"
        ),
    )
    classification = FailureClassification(
        cause="dispatch_failure", synthetic=True, source="runner", recoverability_hint="no"
    )
    return compute_signature(snapshot, classification).fingerprint_hash


def _long_path(length: int) -> str:
    return "/home/" + "x" * (length - len("/home/"))


@pytest.mark.small
class TestStderrSummaryMessage:
    """再現手順 2: メッセージに stderr 末尾の原因行が残る。"""

    def test_message_contains_cause_line_not_banner(self) -> None:
        stderr = _pytest_stderr("OSError: [Errno 30] Read-only file system: '/x'")
        assert len(stderr) > 200

        message = str(_error(stderr))

        assert "Read-only file system" in message
        assert "test session starts" not in message

    def test_incident_evidence_keeps_cause_line(self) -> None:
        stderr = _pytest_stderr(_erofs("/home/aki/dev/kaji/kaji-fix-489"))

        evidence = sanitize_evidence(f"steps/baseline/result.json error={_attempt_error(stderr)}")

        assert "Read-only file system" in evidence


@pytest.mark.small
class TestStderrSummaryFingerprint:
    """再現手順 3・完了条件 3: 原因が違えば分離し、同じ原因は集約する。"""

    def test_different_cause_lines_have_different_hash(self) -> None:
        oserror = _pytest_stderr("OSError: [Errno 30] Read-only file system: '/x'")
        runtime = _pytest_stderr("RuntimeError: plugin failed to load")

        assert _hash(oserror) != _hash(runtime)

    def test_same_cause_with_occurrence_values_has_same_hash(self) -> None:
        first = _pytest_stderr(
            _erofs("/home/a/kaji-fix-489"),
            root="/home/a/kaji-fix-489",
            ts="2026-10-18T03:55:46Z",
            run_id="261018124412",
            duration="312.45",
        )
        second = _pytest_stderr(
            _erofs("/home/b/kaji-fix-1234"),
            root="/home/b/kaji-fix-1234",
            ts="2027-01-01T00:00:00Z",
            run_id="270101000000",
            duration="45.01",
        )

        assert _stderr_summary(first) == _stderr_summary(second)
        assert _hash(first) == _hash(second)

    def test_long_variable_paths_do_not_split_hash(self) -> None:
        stderrs = []
        for length in (20, 333, 3006, 10000):
            path = _long_path(length)
            cause = f"OSError: [Errno 30] Read-only file system: '{path}' -> '{path}/a' ({path}/b)"
            stderrs.append(_pytest_stderr(cause))
        # 原因行長は 2000 文字の前後をまたぐ。
        assert len(stderrs[0].splitlines()[-1]) < 2000 < len(stderrs[-1].splitlines()[-1])

        assert len({_stderr_summary(s) for s in stderrs}) == 1
        assert len({_hash(s) for s in stderrs}) == 1

    def test_timestamp_and_run_id_in_cause_line_have_same_hash(self) -> None:
        first = _pytest_stderr("RuntimeError: lock held since 2026-10-18T03:55:46Z by 261018124412")
        second = _pytest_stderr(
            "RuntimeError: lock held since 2027-01-01T00:00:00Z by 270101000000"
        )

        assert _stderr_summary(first) == _stderr_summary(second)
        assert _hash(first) == _hash(second)

    def test_long_cause_lines_with_different_exceptions_have_different_hash(self) -> None:
        body = "detail " * 400
        oserror = _pytest_stderr(f"OSError: {body}")
        runtime = _pytest_stderr(f"RuntimeError: {body}")
        assert len(oserror.splitlines()[-1]) > 2000

        assert _hash(oserror) != _hash(runtime)


@pytest.mark.small
class TestStderrSummaryNormalizationContext:
    """design review cycle 3 の反例: 正規化の文脈・回数に依存せず同じ原因は同じ hash。"""

    @pytest.mark.parametrize("lengths", [(20, 3006)])
    def test_cause_followed_by_frame_line(self, lengths: tuple[int, int]) -> None:
        stderrs = [
            f'OSError: boom\n  File "{_long_path(n)}", line 42, in save_artifact\n' for n in lengths
        ]

        assert [_stderr_summary(s) for s in stderrs] == ["OSError: boom", "OSError: boom"]
        assert _hash(stderrs[0]) == _hash(stderrs[1])

    @pytest.mark.parametrize("lengths", [(20, 3006)])
    def test_frame_line_only(self, lengths: tuple[int, int]) -> None:
        stderrs = [f'  File "{_long_path(n)}", line 42, in save_artifact\n' for n in lengths]

        assert [_stderr_summary(s) for s in stderrs] == ["(no cause line)", "(no cause line)"]
        assert _hash(stderrs[0]) == _hash(stderrs[1])

    @pytest.mark.parametrize("lengths", [(20, 3006)])
    def test_ansi_colored_key(self, lengths: tuple[int, int]) -> None:
        stderrs = [
            _pytest_stderr(
                f"RuntimeError: \x1b[31mtoken\x1b[0m=sample-credential path='{_long_path(n)}'"
            )
            for n in lengths
        ]

        assert _stderr_summary(stderrs[0]) == _stderr_summary(stderrs[1])
        assert _hash(stderrs[0]) == _hash(stderrs[1])


@pytest.mark.small
class TestStderrSummaryBoundaries:
    """抜粋規則の境界。"""

    def test_trailing_blank_lines_are_skipped(self) -> None:
        assert _stderr_summary("noise\nOSError: x\n\n   \n\t\n") == "OSError: x"

    def test_crlf_newlines(self) -> None:
        assert _stderr_summary("first line\r\nOSError: x\r\n") == "OSError: x"

    def test_carriage_return_progress_uses_last_segment(self) -> None:
        assert _stderr_summary("progress 10%\rprogress 50%\rOSError: x") == "OSError: x"

    @pytest.mark.parametrize("stderr", ["", "   ", "\n\n", " \t\r\n"])
    def test_empty_or_whitespace_is_no_stderr(self, stderr: str) -> None:
        assert _stderr_summary(stderr) == "(no stderr)"

    def test_all_lines_normalized_to_empty_is_no_cause_line(self) -> None:
        stderr = '  File "/a/b.py", line 1, in f\n  File "/a/c.py", line 2, in g\n\x1b[0m\n'

        assert _stderr_summary(stderr) == "(no cause line)"

    def test_short_cause_line_is_normalized(self) -> None:
        assert _stderr_summary("OSError: x '/a/b'") == "OSError: x '<PATH>'"

    def test_huge_cause_line_is_bounded_single_line(self) -> None:
        summary = _stderr_summary("noise\nOSError: " + "y" * 100_000 + "\n")

        assert summary.startswith("OSError: ")
        assert len(summary) <= 2000
        assert "\n" not in summary
        assert "\r" not in summary
