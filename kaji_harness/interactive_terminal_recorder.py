"""PTY recorder that writes an agent's raw terminal output to ``terminal.log`` (Issue #515).

The Herdr backend launches ``<python> -I <this file> <terminal_log> -- <argv...>`` inside the
pane. The recorder runs ``argv`` on a fresh PTY, appends every byte the child writes to
``terminal_log`` (alternate screen and ``\\e[3J`` included, same format as tmux ``pipe-pane``),
and mirrors it to its own stdout so the pane still shows the agent.

The module depends on the standard library only: it is started by path with ``-I``, so it
must not import ``kaji_harness`` (enforced by ``tests/test_interactive_terminal_common.py``).

Recording completeness is reported through ``<terminal_log>.state``, a fixed-width
32 byte ASCII file holding ``recording`` / ``complete`` / ``incomplete <ERRNO>``. Failing to
record never stops the relay or changes the child's exit code.
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import sys
import termios
import tty
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

RecorderState = Literal["recording", "complete", "incomplete", "unknown"]

STATE_SUFFIX = ".state"
HERDR_AGENT_ENV = "HERDR_AGENT"

_STATE_WIDTH = 32
_READ_SIZE = 65536
_EXIT_USAGE = 2
_EXIT_EXEC_FAILED = 127
_USAGE = "usage: interactive_terminal_recorder.py <terminal_log> -- <argv...>\n"

Writer = Callable[[int, bytes], int]
PositionalWriter = Callable[[int, bytes, int], int]
MasterReader = Callable[[int], bytes]


def state_path(terminal_log: Path) -> Path:
    """Return the recording state file that belongs to ``terminal_log``."""
    return terminal_log.with_name(terminal_log.name + STATE_SUFFIX)


def read_recorder_state(terminal_log: Path) -> tuple[RecorderState, str | None]:
    """Read the recorder state for ``terminal_log``.

    Returns:
        ``(state, error)``. ``error`` is the errno name only for ``incomplete``. A missing,
        unreadable or malformed state file yields ``("unknown", None)``; this never raises.
    """
    try:
        text = state_path(terminal_log).read_bytes().decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return "unknown", None
    if text == "recording":
        return "recording", None
    if text == "complete":
        return "complete", None
    parts = text.split()
    if len(parts) == 2 and parts[0] == "incomplete" and parts[1].isalnum():
        return "incomplete", parts[1]
    return "unknown", None


def _encode_state(text: str) -> bytes:
    return text.encode("ascii")[: _STATE_WIDTH - 1].ljust(_STATE_WIDTH - 1) + b"\n"


def _errno_name(exc: OSError) -> str:
    if exc.errno is None:
        return "EUNKNOWN"
    return errno.errorcode.get(exc.errno, "EUNKNOWN")


def _write_all(fd: int, data: bytes, write: Writer = os.write) -> None:
    view = memoryview(data)
    while view:
        written = write(fd, view.tobytes())
        view = view[written:]


def _warn(message: str) -> None:
    try:
        os.write(2, f"kaji recorder: {message}\r\n".encode())
    except OSError:
        pass


class _StateFile:
    """Fixed-width recording state, updated in place so it can be written on a full disk."""

    def __init__(
        self,
        path: Path,
        fd: int,
        pwrite: PositionalWriter = os.pwrite,
        unlink: Callable[[Path], None] = Path.unlink,
    ) -> None:
        self._path = path
        self._fd = fd
        self._pwrite = pwrite
        self._unlink = unlink
        self._incomplete = False

    @classmethod
    def create(cls, path: Path) -> _StateFile:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        try:
            _write_all(fd, _encode_state("recording"))
        except OSError:
            os.close(fd)
            raise
        return cls(path, fd)

    def mark_complete(self) -> None:
        if not self._incomplete:
            self._update("complete")

    def mark_incomplete(self, errno_name: str) -> None:
        self._incomplete = True
        self._update(f"incomplete {errno_name}")

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

    def _update(self, text: str) -> None:
        try:
            if self._pwrite(self._fd, _encode_state(text), 0) == _STATE_WIDTH:
                return
        except OSError:
            pass
        # 上書きできない状態ファイルは「既知の欠落なし」と誤読されないよう消す（unlink は空き容量不要）
        try:
            self._unlink(self._path)
        except OSError:
            pass


class _LogSink:
    """Append chunks to ``terminal.log``; stop recording (not relaying) on the first failure."""

    def __init__(self, fd: int, state: _StateFile, write: Writer = os.write) -> None:
        self._fd = fd
        self._state = state
        self._write = write
        self.failed = False

    def write(self, data: bytes) -> None:
        if self.failed:
            return
        try:
            _write_all(self._fd, data, self._write)
        except OSError as exc:
            self.failed = True
            name = _errno_name(exc)
            self._state.mark_incomplete(name)
            _warn(f"terminal.log recording stopped ({name}); the agent keeps running")


class _Mirror:
    """Mirror chunks to the pane; a closed pane stops mirroring but not recording."""

    def __init__(self, fd: int, write: Writer = os.write) -> None:
        self._fd = fd
        self._write = write
        self.failed = False

    def write(self, data: bytes) -> None:
        if self.failed:
            return
        try:
            _write_all(self._fd, data, self._write)
        except OSError:
            self.failed = True


def _relay_output(data: bytes, sink: _LogSink, mirror: _Mirror) -> None:
    # log を先に書き、pane が先に閉じても記録が残るようにする
    sink.write(data)
    mirror.write(data)


def _read_master(fd: int, read: Callable[[int, int], bytes] = os.read) -> bytes:
    """Read from the PTY master, treating Linux ``EIO`` and BSD ``b""`` alike as EOF."""
    try:
        return read(fd, _READ_SIZE)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _exit_code(status: int) -> int:
    """Convert a ``waitpid`` status to a shell-style exit code (signal → ``128 + signum``)."""
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def _child_exec(argv: Sequence[str]) -> None:
    # HERDR_AGENT は recorder の検出用ヒントなので、子（nested kaji の pane 等）へ漏らさない
    os.environ.pop(HERDR_AGENT_ENV, None)
    try:
        os.execvp(argv[0], list(argv))
    except OSError as exc:
        _warn(f"cannot execute {argv[0]!r}: {exc.strerror or exc}")
    os._exit(_EXIT_EXEC_FAILED)


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    try:
        size = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def _read_stdin(fd: int) -> bytes:
    try:
        return os.read(fd, _READ_SIZE)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _forward_input(master: int, pending: bytearray, write: Writer = os.write) -> bool:
    """Write as much of ``pending`` to the nonblocking ``master`` as it accepts now.

    Returns:
        ``False`` when the master rejects input (forwarding should stop), else ``True``.
    """
    try:
        written = write(master, bytes(pending))
    except BlockingIOError:
        return True
    except OSError:
        pending.clear()
        return False
    del pending[:written]
    return True


def _relay(
    master: int,
    stdin_fd: int,
    sink: _LogSink,
    mirror: _Mirror,
    read_master: MasterReader,
) -> None:
    # 入力を blocking で書くと、入力待ちの子と出力待ちの recorder が互いを待って詰まる（大きな paste で発生）。
    # master を nonblocking にして未送信の入力を保持し、書ける分だけ送る間も出力の吸い出しを続ける
    os.set_blocking(master, False)
    pending = bytearray()
    stdin_open = True
    while True:
        # 未送信の入力が溜まっている間は stdin を読まず、外側の pane へ背圧をかける
        rlist = [master, stdin_fd] if stdin_open and len(pending) < _READ_SIZE else [master]
        wlist = [master] if pending else []
        try:
            readable, writable, _ = select.select(rlist, wlist, [])
        except InterruptedError:
            continue
        if master in readable:
            try:
                data = read_master(master)
            except BlockingIOError:
                data = None
            if data is not None:
                if not data:
                    return
                _relay_output(data, sink, mirror)
        if stdin_fd in readable:
            data = _read_stdin(stdin_fd)
            if data:
                pending += data
            else:
                stdin_open = False
        if master in writable and not _forward_input(master, pending):
            stdin_open = False


def run(
    terminal_log: Path,
    argv: Sequence[str],
    *,
    stdin_fd: int = 0,
    stdout_fd: int = 1,
    read_master: MasterReader = _read_master,
) -> int:
    """Run ``argv`` on a new PTY while recording its output to ``terminal_log``.

    Returns:
        The child's exit code (signal termination → ``128 + signum``), or ``2`` when
        ``terminal_log`` or its state file cannot be opened (the child is not started).
    """
    try:
        log_fd = os.open(terminal_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    except OSError as exc:
        _warn(f"cannot open {terminal_log}: {exc.strerror or exc}")
        return _EXIT_USAGE
    try:
        state = _StateFile.create(state_path(terminal_log))
    except OSError as exc:
        os.close(log_fd)
        _warn(f"cannot create {state_path(terminal_log)}: {exc.strerror or exc}")
        return _EXIT_USAGE

    pid, master = pty.fork()
    if pid == 0:
        _child_exec(argv)

    is_tty = os.isatty(stdin_fd)
    saved_attrs = termios.tcgetattr(stdin_fd) if is_tty else None
    previous_winch = None
    finished = False
    try:
        if is_tty:
            _copy_winsize(stdin_fd, master)
            previous_winch = signal.signal(
                signal.SIGWINCH, lambda _signum, _frame: _copy_winsize(stdin_fd, master)
            )
            tty.setraw(stdin_fd)
        _relay(master, stdin_fd, _LogSink(log_fd, state), _Mirror(stdout_fd), read_master)
        state.mark_complete()
        finished = True
    finally:
        if saved_attrs is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, saved_attrs)
        if previous_winch is not None:
            signal.signal(signal.SIGWINCH, previous_winch)
        os.close(master)
        os.close(log_fd)
        state.close()
        if not finished:
            # relay が壊れた子は操作できないので、残さず回収する
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            os.waitpid(pid, 0)
    _, status = os.waitpid(pid, 0)
    return _exit_code(status)


def main(argv: Sequence[str]) -> int:
    """Entry point: ``<terminal_log> -- <argv...>``."""
    if len(argv) < 3 or argv[1] != "--":
        _warn(_USAGE.strip())
        return _EXIT_USAGE
    return run(Path(argv[0]), argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
