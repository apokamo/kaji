"""POSIX advisory lock for one series identifier."""

from __future__ import annotations

import errno
import fcntl
from pathlib import Path
from typing import IO

from ..errors import SeriesInputError


class SeriesLock:
    """Hold a non-blocking exclusive flock for the context lifetime."""

    def __init__(self, path: Path):
        self.path = path
        self._file: IO[str] | None = None

    def __enter__(self) -> SeriesLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("w", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise SeriesInputError(
                    f"series {self.path.parent.name!r} is already running"
                ) from exc
            raise
        self._file = lock_file
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
