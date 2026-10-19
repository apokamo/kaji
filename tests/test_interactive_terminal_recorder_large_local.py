"""Issue #515: PTY recorder を実 PTY 上で subprocess として起動する（``large_local``）。

外側 PTY（Herdr pane 相当）を ``pty.fork`` で作り、その中で recorder を起動して決定論的な
fake TUI agent を記録する。各テストは deadline を持ち、残存プロセスを ``finally`` で回収する。
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import textwrap
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kaji_harness import interactive_terminal_recorder as recorder
from kaji_harness.interactive_terminal_common import (
    _terminal_exit_detail,
    read_terminal_diagnostic,
)

pytestmark = [pytest.mark.large, pytest.mark.large_local]

RECORDER = Path(recorder.__file__).resolve()
DEADLINE_SECONDS = 10.0

FAKE_AGENT = textwrap.dedent(
    """
    import os, signal, sys, time
    from pathlib import Path

    Path(os.environ["FAKE_PID_FILE"]).write_text(str(os.getpid()))
    mode = sys.argv[1]
    out = sys.stdout

    if mode == "tui":
        for i in range(3000):
            out.write(f"BULK-{i:04d}\\n")
        out.write("\\x1b[?1049h")
        for i in range(200):
            out.write(f"ALT-{i:03d}\\n")
        out.write("\\x1b[?1049l")
        out.write("\\x1b[3J")
        out.write("AFTER-CLEAR\\n")
        out.flush()
        sys.exit(7)
    elif mode == "signal":
        os.kill(os.getpid(), signal.SIGTERM)
    elif mode == "echo-input":
        out.write("READY\\n")
        out.flush()
        line = sys.stdin.readline()
        out.write(f"GOT:{line.strip()}\\n")
        out.flush()
    elif mode == "env":
        out.write(f"HERDR_AGENT={os.environ.get('HERDR_AGENT', '<unset>')}\\n")
        out.flush()
    elif mode == "winsize":
        import fcntl, struct, termios
        def report(*_a):
            rows, cols = struct.unpack("hhhh", fcntl.ioctl(1, termios.TIOCGWINSZ, b"\\0" * 8))[:2]
            out.write(f"SIZE:{rows}x{cols}\\n")
            out.flush()
        signal.signal(signal.SIGWINCH, report)
        report()
        time.sleep(5)
    elif mode == "sigint":
        def on_int(*_a):
            out.write("GOT-SIGINT\\n")
            out.flush()
            sys.exit(130)
        signal.signal(signal.SIGINT, on_int)
        out.write("READY\\n")
        out.flush()
        time.sleep(5)
        sys.exit(1)
    elif mode == "hang":
        out.write("BEFORE-CLOSE\\n")
        out.flush()
        time.sleep(30)
    elif mode == "verdict":
        Path(os.environ["FAKE_VERDICT"]).write_text("status: PASS\\n")
        out.write("VERDICT-WRITTEN\\n")
        out.flush()
        sys.stdin.readline()
        out.write("AFTER-VERDICT\\n")
        out.flush()
    elif mode == "provider-error":
        out.write("\\x1b[?1049h")
        out.write("API Error: 529 {\\"type\\":\\"overloaded_error\\"}\\n")
        if len(sys.argv) > 2:
            out.write("Please try again: invalid token\\n")
        out.write("\\x1b[?1049l")
        out.flush()
        sys.exit(1)
    """
)


@dataclass
class _Session:
    """recorder running inside an outer PTY whose master the test holds."""

    pid: int
    master: int
    log: Path
    agent_pid_file: Path
    output: bytearray = field(default_factory=bytearray)
    status: int | None = None

    def read_until(self, needle: bytes, timeout: float = DEADLINE_SECONDS) -> bytes:
        end = time.monotonic() + timeout
        while needle not in self.output:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {needle!r}: {bytes(self.output)!r}")
            ready, _, _ = select.select([self.master], [], [], remaining)
            if ready and not self._read_once():
                break
        return bytes(self.output)

    def _read_once(self) -> bool:
        try:
            data = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return False
            raise
        self.output += data
        return bool(data)

    def wait(self, timeout: float = DEADLINE_SECONDS) -> int:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                self._read_once()
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = status
                return os.waitstatus_to_exitcode(status)
        raise AssertionError(f"recorder did not exit: {bytes(self.output)!r}")

    def agent_pid(self) -> int | None:
        try:
            return int(self.agent_pid_file.read_text())
        except (OSError, ValueError):
            return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def agent_script(tmp_path: Path) -> Path:
    path = tmp_path / "fake_agent.py"
    path.write_text(FAKE_AGENT, encoding="utf-8")
    return path


@pytest.fixture
def spawn(tmp_path: Path, agent_script: Path) -> Iterator[object]:
    sessions: list[_Session] = []

    def _spawn(
        mode: str, *, env: dict[str, str] | None = None, argv: list[str] | None = None
    ) -> _Session:
        log = tmp_path / "terminal.log"
        pid_file = tmp_path / f"agent-{len(sessions)}.pid"
        child_argv = argv or [sys.executable, "-I", str(agent_script), mode]
        child_env = {
            **os.environ,
            "FAKE_PID_FILE": str(pid_file),
            "FAKE_VERDICT": str(tmp_path / "verdict.yaml"),
            **(env or {}),
        }
        pid, master = pty.fork()
        if pid == 0:
            os.execve(
                sys.executable,
                [sys.executable, "-I", str(RECORDER), str(log), "--", *child_argv],
                child_env,
            )
        session = _Session(pid=pid, master=master, log=log, agent_pid_file=pid_file)
        sessions.append(session)
        return session

    yield _spawn

    for session in sessions:
        for pid in (session.pid, session.agent_pid()):
            if pid is not None and _alive(pid):
                os.kill(pid, signal.SIGKILL)
        if session.status is None:
            try:
                os.waitpid(session.pid, 0)
            except ChildProcessError:
                pass
        try:
            os.close(session.master)
        except OSError:
            pass


class TestRecording:
    def test_bulk_alternate_screen_and_clear_are_recorded_byte_exact(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("tui")
        code = session.wait()

        expected = (
            "".join(f"BULK-{i:04d}\r\n" for i in range(3000))
            + "\x1b[?1049h"
            + "".join(f"ALT-{i:03d}\r\n" for i in range(200))
            + "\x1b[?1049l\x1b[3JAFTER-CLEAR\r\n"
        ).encode()
        assert session.log.read_bytes() == expected
        assert code == 7
        assert recorder.read_recorder_state(session.log) == ("complete", None)

    def test_signal_exit_is_128_plus_signum(self, spawn) -> None:  # type: ignore[no-untyped-def]
        assert spawn("signal").wait() == 128 + signal.SIGTERM

    def test_stdin_is_relayed_to_child(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("echo-input")
        session.read_until(b"READY")
        os.write(session.master, b"hello\r")
        assert session.wait() == 0
        assert b"GOT:hello" in session.log.read_bytes()

    def test_herdr_agent_hint_is_not_inherited_by_child(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("env", env={"HERDR_AGENT": "codex"})
        assert session.wait() == 0
        assert b"HERDR_AGENT=<unset>" in session.log.read_bytes()

    def test_child_exec_failure_is_logged_and_exits_127(self, spawn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        session = spawn("unused", argv=[str(tmp_path / "no-such-agent")])
        assert session.wait() == 127
        assert b"no-such-agent" in session.log.read_bytes()

    def test_unopenable_log_exits_2(self, tmp_path: Path, agent_script: Path) -> None:
        import subprocess

        pid_file = tmp_path / "agent.pid"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(RECORDER),
                str(tmp_path / "nope" / "terminal.log"),
                "--",
                sys.executable,
                str(agent_script),
                "env",
            ],
            env={**os.environ, "FAKE_PID_FILE": str(pid_file)},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=DEADLINE_SECONDS,
        )
        assert result.returncode == 2
        assert not pid_file.exists()

    def test_non_tty_stdin_records_and_keeps_exit_code(
        self, tmp_path: Path, agent_script: Path
    ) -> None:
        import subprocess

        log = tmp_path / "terminal.log"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(RECORDER),
                str(log),
                "--",
                sys.executable,
                str(agent_script),
                "tui",
            ],
            env={**os.environ, "FAKE_PID_FILE": str(tmp_path / "agent.pid")},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=DEADLINE_SECONDS,
        )
        assert result.returncode == 7
        assert log.read_bytes().count(b"BULK-") == 3000
        assert recorder.read_recorder_state(log) == ("complete", None)

    def test_sigwinch_forwards_new_size(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("winsize")
        fcntl.ioctl(session.master, termios.TIOCSWINSZ, struct.pack("hhhh", 30, 100, 0, 0))
        session.read_until(b"SIZE:30x100")
        fcntl.ioctl(session.master, termios.TIOCSWINSZ, struct.pack("hhhh", 41, 123, 0, 0))
        os.kill(session.pid, signal.SIGWINCH)
        session.read_until(b"SIZE:41x123")


class TestInterruptAndClose:
    def test_ctrl_c_key_reaches_child_as_sigint(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("sigint")
        session.read_until(b"READY")
        os.write(session.master, b"\x03")
        assert session.wait() == 130
        assert b"GOT-SIGINT" in session.log.read_bytes()

    def test_outer_close_terminates_recorder_and_child(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("hang")
        session.read_until(b"BEFORE-CLOSE")
        agent_pid = session.agent_pid()
        os.close(session.master)
        session.master = os.open(os.devnull, os.O_RDONLY)
        session.wait()
        _assert_gone(agent_pid)
        assert b"BEFORE-CLOSE" in session.log.read_bytes()
        assert recorder.read_recorder_state(session.log) == ("recording", None)

    def test_sigterm_terminates_recorder_and_child(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("hang")
        session.read_until(b"BEFORE-CLOSE")
        agent_pid = session.agent_pid()
        os.kill(session.pid, signal.SIGTERM)
        assert session.wait() == -signal.SIGTERM
        _assert_gone(agent_pid)
        assert b"BEFORE-CLOSE" in session.log.read_bytes()
        assert recorder.read_recorder_state(session.log) == ("recording", None)

    def test_output_after_verdict_is_still_recorded(self, spawn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        session = spawn("verdict")
        session.read_until(b"VERDICT-WRITTEN")
        assert (tmp_path / "verdict.yaml").exists()
        os.write(session.master, b"continue\r")
        assert session.wait() == 0
        assert b"AFTER-VERDICT" in session.log.read_bytes()


def _assert_gone(pid: int | None, timeout: float = DEADLINE_SECONDS) -> None:
    assert pid is not None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"agent pid {pid} still alive")


class TestOuterTerminalRestore:
    def test_tty_attrs_restored_after_normal_exit_and_relay_error(
        self, tmp_path: Path, agent_script: Path
    ) -> None:
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            env_backup = os.environ.get("FAKE_PID_FILE")
            os.environ["FAKE_PID_FILE"] = str(tmp_path / "agent.pid")
            try:
                code = recorder.run(
                    tmp_path / "ok.log",
                    [sys.executable, "-I", str(agent_script), "env"],
                    stdin_fd=slave,
                    stdout_fd=slave,
                )
                assert code == 0
                assert termios.tcgetattr(slave) == before

                def broken_read(_fd: int) -> bytes:
                    raise OSError(errno.EBADF, "injected")

                with pytest.raises(OSError):
                    recorder.run(
                        tmp_path / "err.log",
                        [sys.executable, "-I", str(agent_script), "hang"],
                        stdin_fd=slave,
                        stdout_fd=slave,
                        read_master=broken_read,
                    )
                assert termios.tcgetattr(slave) == before
            finally:
                if env_backup is None:
                    os.environ.pop("FAKE_PID_FILE", None)
                else:
                    os.environ["FAKE_PID_FILE"] = env_backup
        finally:
            os.close(master)
            os.close(slave)


class TestDiagnosticOnRecording:
    def test_provider_error_in_alternate_screen_is_diagnosed(self, spawn) -> None:  # type: ignore[no-untyped-def]
        session = spawn("provider-error")
        assert session.wait() == 1

        diagnostic = read_terminal_diagnostic(session.log)
        assert diagnostic.kind == "provider_error"
        detail = _terminal_exit_detail(
            session.log, prefix="Herdr pane exited before writing verdict.yaml"
        )
        assert "transient provider error detected" in detail
        assert "sensitive marker" not in detail

    def test_sensitive_marker_is_reported_alongside(self, spawn, agent_script: Path) -> None:  # type: ignore[no-untyped-def]
        session = spawn(
            "unused",
            argv=[sys.executable, "-I", str(agent_script), "provider-error", "with-sensitive"],
        )
        assert session.wait() == 1
        detail = _terminal_exit_detail(
            session.log, prefix="Herdr pane exited before writing verdict.yaml"
        )
        assert "transient provider error detected" in detail
        assert "sensitive marker also detected" in detail
