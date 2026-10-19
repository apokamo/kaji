"""Issue #515: PTY recorder の純粋な分岐（Small）と記録状態ファイル（Medium）。

実 PTY / subprocess を使う検証は ``test_interactive_terminal_recorder_large_local.py``。
"""

from __future__ import annotations

import errno
import os
import signal
from collections.abc import Callable
from pathlib import Path

import pytest

from kaji_harness import interactive_terminal_recorder as recorder


def _raising_read(err: int) -> Callable[[int, int], bytes]:
    def read(_fd: int, _size: int) -> bytes:
        raise OSError(err, os.strerror(err))

    return read


@pytest.mark.small
class TestReadMaster:
    def test_linux_eio_is_eof(self) -> None:
        assert recorder._read_master(3, _raising_read(errno.EIO)) == b""

    def test_bsd_empty_read_is_eof(self) -> None:
        assert recorder._read_master(3, lambda _fd, _size: b"") == b""

    def test_data_is_returned(self) -> None:
        assert recorder._read_master(3, lambda _fd, _size: b"abc") == b"abc"

    def test_other_errors_propagate(self) -> None:
        with pytest.raises(OSError) as excinfo:
            recorder._read_master(3, _raising_read(errno.EBADF))
        assert excinfo.value.errno == errno.EBADF


@pytest.mark.small
class TestExitCode:
    def test_normal_exit_code_is_kept(self) -> None:
        assert recorder._exit_code(7 << 8) == 7
        assert recorder._exit_code(0) == 0

    def test_signal_is_128_plus_signum(self) -> None:
        assert recorder._exit_code(signal.SIGTERM) == 128 + signal.SIGTERM
        assert recorder._exit_code(signal.SIGINT) == 130


@pytest.mark.small
class TestMainArguments:
    @pytest.mark.parametrize(
        "argv",
        [[], ["log"], ["log", "--"], ["log", "agent"], ["log", "agent", "arg"]],
    )
    def test_missing_separator_or_command_exits_2(self, argv: list[str]) -> None:
        assert recorder.main(argv) == 2


class _FakeState:
    def __init__(self) -> None:
        self.incomplete: list[str] = []

    def mark_incomplete(self, name: str) -> None:
        self.incomplete.append(name)


class _ChunkedWriter:
    """Writes at most ``limit`` bytes per call, then fails with ENOSPC after ``fail_after`` bytes."""

    def __init__(self, limit: int, fail_after: int | None = None) -> None:
        self.limit = limit
        self.fail_after = fail_after
        self.data = bytearray()

    def __call__(self, _fd: int, data: bytes) -> int:
        if self.fail_after is not None and len(self.data) >= self.fail_after:
            raise OSError(errno.ENOSPC, "No space left on device")
        chunk = data[: self.limit]
        self.data += chunk
        return len(chunk)


@pytest.mark.small
class TestLogSinkAndMirror:
    def test_partial_writes_are_completed(self) -> None:
        writer = _ChunkedWriter(limit=2)
        sink = recorder._LogSink(5, _FakeState(), writer)
        sink.write(b"abcdefg")
        assert bytes(writer.data) == b"abcdefg"

    def test_enospc_after_partial_write_stops_recording_but_relay_continues(self) -> None:
        log = _ChunkedWriter(limit=3, fail_after=3)
        out = _ChunkedWriter(limit=100)
        state = _FakeState()
        sink = recorder._LogSink(5, state, log)
        mirror = recorder._Mirror(6, out)

        recorder._relay_output(b"abcdef", sink, mirror)
        recorder._relay_output(b"ghi", sink, mirror)

        assert bytes(log.data) == b"abc"
        assert bytes(out.data) == b"abcdefghi"
        assert state.incomplete == ["ENOSPC"]
        assert sink.failed is True

    def test_stdout_failure_keeps_recording(self) -> None:
        log = _ChunkedWriter(limit=100)

        def broken(_fd: int, _data: bytes) -> int:
            raise OSError(errno.EIO, "pane closed")

        sink = recorder._LogSink(5, _FakeState(), log)
        mirror = recorder._Mirror(6, broken)
        recorder._relay_output(b"one", sink, mirror)
        recorder._relay_output(b"two", sink, mirror)

        assert bytes(log.data) == b"onetwo"
        assert mirror.failed is True


@pytest.mark.medium
class TestStateFile:
    def test_created_as_recording_with_fixed_width(self, tmp_path: Path) -> None:
        log = tmp_path / "terminal.log"
        state = recorder._StateFile.create(recorder.state_path(log))
        state.close()
        raw = (tmp_path / "terminal.log.state").read_bytes()
        assert len(raw) == 32
        assert recorder.read_recorder_state(log) == ("recording", None)

    def test_complete_after_eof(self, tmp_path: Path) -> None:
        log = tmp_path / "terminal.log"
        state = recorder._StateFile.create(recorder.state_path(log))
        state.mark_complete()
        state.close()
        assert recorder.read_recorder_state(log) == ("complete", None)

    def test_incomplete_is_not_overwritten_by_complete(self, tmp_path: Path) -> None:
        log = tmp_path / "terminal.log"
        state = recorder._StateFile.create(recorder.state_path(log))
        state.mark_incomplete("ENOSPC")
        state.mark_complete()
        state.close()
        assert recorder.read_recorder_state(log) == ("incomplete", "ENOSPC")
        assert len(recorder.state_path(log).read_bytes()) == 32

    def test_failed_update_unlinks_state_file(self, tmp_path: Path) -> None:
        path = tmp_path / "terminal.log.state"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)

        def failing_pwrite(_fd: int, _data: bytes, _offset: int) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        state = recorder._StateFile(path, fd, pwrite=failing_pwrite)
        state.mark_incomplete("ENOSPC")
        state.close()
        assert not path.exists()
        assert recorder.read_recorder_state(tmp_path / "terminal.log") == ("unknown", None)

    def test_unlink_failure_is_swallowed(self, tmp_path: Path) -> None:
        path = tmp_path / "terminal.log.state"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)

        def failing_pwrite(_fd: int, _data: bytes, _offset: int) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        def failing_unlink(_path: Path) -> None:
            raise OSError(errno.EACCES, "denied")

        state = recorder._StateFile(path, fd, pwrite=failing_pwrite, unlink=failing_unlink)
        state.mark_incomplete("ENOSPC")
        state.close()
        assert path.exists()

    @pytest.mark.parametrize(
        "content",
        [b"", b"garbage\n", b"incomplete\n", b"incomplete EN OSPC\n", b"\xff\xfe\n"],
    )
    def test_malformed_state_is_unknown(self, tmp_path: Path, content: bytes) -> None:
        log = tmp_path / "terminal.log"
        recorder.state_path(log).write_bytes(content)
        assert recorder.read_recorder_state(log) == ("unknown", None)

    def test_missing_state_is_unknown(self, tmp_path: Path) -> None:
        assert recorder.read_recorder_state(tmp_path / "terminal.log") == ("unknown", None)

    def test_unreadable_state_is_unknown(self, tmp_path: Path) -> None:
        log = tmp_path / "terminal.log"
        recorder.state_path(log).mkdir()
        assert recorder.read_recorder_state(log) == ("unknown", None)

    def test_unopenable_log_exits_2_without_starting_child(self, tmp_path: Path) -> None:
        marker = tmp_path / "started"
        code = recorder.run(tmp_path / "missing-dir" / "terminal.log", ["touch", str(marker)])
        assert code == 2
        assert not marker.exists()
