"""Persistence for ``kaji watch`` under ``<artifacts>/_watch/`` (Issue #543 M4).

状態ファイルは原子的に置き換える（tmp + ``os.replace``）。壊れていたら ``.broken-<ts>`` に
退避して空から始め、警告する。lock は ``fcntl.flock``（``series/lock.py`` と同じ方式）。
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from pydantic import ValidationError

from .models import WatchEventFile, WatchRecord, WatchState

WATCH_DIR_NAME = "_watch"
STATE_FILE = "state.json"
EVENTS_DIR = "events"
RECORDS_FILE = "records.jsonl"
LABELS_LOG_FILE = "labels.jsonl"
LOCK_FILE = "lock"

_console = logging.getLogger("kaji.watch")


def judge_id() -> str:
    """書き込んだプロセスの識別子 ``host:pid`` を返す。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def event_file_name(key: str) -> str:
    """event key をファイル名にする（``101:aborted:261115113853`` → ``101-aborted-….json``）。"""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in key.replace(":", "-"))
    return f"{safe}.json"


class WatchLockHeld(Exception):
    """lock が別のプロセスに保持されている。

    Attributes:
        holder_pane: 保持者が書いた pane_id（不明なら ``None``）。
    """

    def __init__(self, holder_pane: str | None) -> None:
        self.holder_pane = holder_pane
        super().__init__(f"watch already running (pane {holder_pane or 'unknown'})")


class WatchStore:
    """``_watch/`` の読み書き。

    Attributes:
        root: ``<artifacts>/_watch``。
    """

    def __init__(self, artifacts_dir: Path) -> None:
        self.root = artifacts_dir / WATCH_DIR_NAME
        self._lock_file: IO[str] | None = None

    @property
    def events_dir(self) -> Path:
        """event ファイルの dir。"""
        return self.root / EVENTS_DIR

    # ---- state ----

    def load_state(self) -> WatchState:
        """状態ファイルを読む（無ければ空、壊れていれば退避して空）。"""
        path = self.root / STATE_FILE
        if not path.is_file():
            return WatchState()
        try:
            return WatchState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{STATE_FILE}.broken-{stamp}")
            path.replace(backup)
            _console.warning(
                "watch state is broken (%s); moved to %s and starting empty",
                type(exc).__name__,
                backup,
            )
            return WatchState()

    def save_state(self, state: WatchState) -> None:
        """状態ファイルを原子的に置き換える。"""
        self._write_atomic(self.root / STATE_FILE, state.model_dump_json(indent=2))

    # ---- events / records / labels log ----

    def write_event(self, event: WatchEventFile) -> Path:
        """event ファイルを書き、そのパスを返す（同じ key は上書きしない）。"""
        path = self.events_dir / event_file_name(event.key)
        if not path.exists():
            self._write_atomic(path, event.model_dump_json(indent=2))
        return path

    def read_event(self, path: Path) -> WatchEventFile:
        """event ファイルを読む。

        Raises:
            ValueError: ``_watch/events/`` の外のファイル。
            ValidationError: schema v1 として読めない場合。
            OSError: 読み込みに失敗した場合。
        """
        resolved = path.resolve()
        if not resolved.is_relative_to(self.events_dir.resolve()):
            raise ValueError(f"event file must be under {self.events_dir}: {path}")
        return WatchEventFile.model_validate_json(resolved.read_text(encoding="utf-8"))

    def append_record(self, record: WatchRecord) -> None:
        """``records.jsonl`` に 1 行追記する（``append_record_limited`` と同じ ``flock`` の下）。"""
        self.append_record_limited(record, unit=None, limit=None)

    def append_record_limited(
        self,
        record: WatchRecord,
        *,
        unit: list[str | int | None] | None,
        limit: int | None,
    ) -> bool:
        """``records.jsonl`` の ``flock``（blocking）の下で数えて、上限未満なら 1 行追記する（#545）。

        Args:
            record: 追記する行。
            unit: 上限を数える単位の値の列（``None`` は数えない）。
            limit: 同じ outcome・同じ単位の上限（``None`` は数えない）。

        Returns:
            追記したか（上限に達していれば追記せず ``False``）。
        """
        path = self.root / RECORDS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                if unit is not None and limit is not None:
                    f.seek(0)
                    existing = self._parse_records(f.read(), path)
                    used = sum(
                        1 for r in existing if r.outcome == record.outcome and r.unit == unit
                    )
                    if used >= limit:
                        return False
                f.seek(0, os.SEEK_END)
                f.write(record.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True

    def read_records(self) -> list[WatchRecord]:
        """``records.jsonl`` を読む（壊れた行は警告して飛ばす）。"""
        path = self.root / RECORDS_FILE
        if not path.is_file():
            return []
        return self._parse_records(path.read_text(encoding="utf-8"), path)

    @staticmethod
    def _parse_records(text: str, path: Path) -> list[WatchRecord]:
        records: list[WatchRecord] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(WatchRecord.model_validate_json(line))
            except ValidationError:
                _console.warning("skipping invalid line %d in %s", number, path)
        return records

    def append_label_log(self, issue: int, add: list[str], remove: list[str]) -> None:
        """``labels.jsonl`` にラベル書き込みを 1 行追記する（``judge=host:pid`` 付き）。"""
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "issue": issue,
            "add": add,
            "remove": remove,
            "judge": judge_id(),
        }
        self._append(self.root / LABELS_LOG_FILE, json.dumps(entry, ensure_ascii=False))

    # ---- lock ----

    def acquire_lock(self, pane_id: str | None) -> None:
        """非 blocking の排他 lock を取り、保持者の pane_id を書く。

        Raises:
            WatchLockHeld: 別のプロセスが保持している場合。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / LOCK_FILE
        lock_file = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise WatchLockHeld(self.lock_holder_pane()) from exc
            raise
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(pane_id or "")
        lock_file.flush()
        self._lock_file = lock_file

    def release_lock(self) -> None:
        """lock を解放する。"""
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def is_locked(self) -> bool:
        """別のプロセスが lock を保持しているかを返す（自分は取らない）。"""
        path = self.root / LOCK_FILE
        if not path.is_file():
            return False
        with path.open("a+", encoding="utf-8") as probe:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    return True
                raise
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        return False

    def lock_holder_pane(self) -> str | None:
        """lock ファイルに書かれた保持者の pane_id を返す。"""
        path = self.root / LOCK_FILE
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    # ---- helpers ----

    def _write_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _append(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
