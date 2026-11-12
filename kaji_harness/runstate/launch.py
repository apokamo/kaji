"""Issue-scoped launch records and launch lock (Issue #540).

launch record は「run dir がまだ無い区間も含めて、この Issue に生きた kaji プロセスが
いる」ことを示すローカル artifact。``kaji run`` は provider による Issue 解決より前に、
launcher（retry / series）は child を起動する前に書き、正常終了時に削除する。

``LaunchLock`` は retry / series resume の planner が「ゲート判定 → launcher record
書き込み」を排他する Issue 単位の ``flock``。
"""

from __future__ import annotations

import errno
import fcntl
import os
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .process import ProcessRecord, ProcessRecordError, read_process_record, write_process_record

LAUNCHES_DIR = "launches"
LAUNCH_LOCK_FILE = ".lock"


def launches_dir(artifacts_dir: Path, issue_id: str) -> Path:
    """Issue の launch record ディレクトリを返す。"""
    return artifacts_dir / issue_id / LAUNCHES_DIR


def launch_record_path(artifacts_dir: Path, issue_id: str, record: ProcessRecord) -> Path:
    """記録の保存先 ``<role>-<pid>-<ticks or 0>.json`` を返す。"""
    name = f"{record.role}-{record.pid}-{record.pid_start_ticks or 0}.json"
    return launches_dir(artifacts_dir, issue_id) / name


def write_launch_record(artifacts_dir: Path, issue_id: str, record: ProcessRecord) -> Path:
    """launch record を atomic に書き、そのパスを返す。

    Raises:
        OSError: 書き込みに失敗した場合（呼び出し側は先へ進まない）。
    """
    path = launch_record_path(artifacts_dir, issue_id, record)
    write_process_record(path, record)
    return path


def set_launch_run_id(path: Path, run_id: str) -> None:
    """既存の launch record に ``run_id`` を設定して atomic に書き直す。

    Raises:
        OSError: 書き込みに失敗した場合。
        ProcessRecordError: 記録が無い・読めない場合。
    """
    record = read_process_record(path)
    if record is None:
        raise ProcessRecordError(f"{path.name}: launch record missing")
    write_process_record(path, record.model_copy(update={"run_id": run_id}))


def remove_launch_record(path: Path) -> None:
    """launch record を削除する（無ければ何もしない）。

    Raises:
        OSError: 削除に失敗した場合（呼び出し側は warning で続行する）。
    """
    try:
        path.unlink()
    except FileNotFoundError:
        return


@dataclass(frozen=True)
class LaunchEntry:
    """列挙した launch record 1 件。読めなければ ``record`` は ``None`` で ``error`` に理由。"""

    path: Path
    record: ProcessRecord | None
    error: str | None = None


def list_launch_records(artifacts_dir: Path, issue_id: str) -> list[LaunchEntry]:
    """Issue の launch record をファイル名順に列挙する（lock file と tmp は除く）。"""
    directory = launches_dir(artifacts_dir, issue_id)
    if not directory.is_dir():
        return []
    entries: list[LaunchEntry] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = read_process_record(path)
        except ProcessRecordError as exc:
            entries.append(LaunchEntry(path, None, str(exc)))
            continue
        if record is None:
            # 列挙と読み取りの間に削除された（正常終了した）記録
            continue
        entries.append(LaunchEntry(path, record))
    return entries


class LaunchLock:
    """Issue 単位の非ブロッキング排他 lock（``fcntl.flock``）。

    ``acquire()`` は取得できれば ``True``、他が保持中なら ``False`` を返す。
    lock file を開けない等の失敗は ``OSError`` として呼び出し側へ伝える。
    プロセスが終了すると fd が閉じて lock は解放される。
    """

    def __init__(self, artifacts_dir: Path, issue_id: str) -> None:
        self.path = launches_dir(artifacts_dir, issue_id) / LAUNCH_LOCK_FILE
        self._fd: int | None = None

    def acquire(self) -> bool:
        """lock を取る。

        Returns:
            取得できれば ``True``、他プロセスが保持中なら ``False``。

        Raises:
            OSError: lock file を作成・オープンできない場合。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                return False
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        """lock を解放する（保持していなければ何もしない）。"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> LaunchLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
