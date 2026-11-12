"""Process liveness records and probing (Issue #540).

``kaji run`` と、child の ``kaji run`` を起動する launcher（``kaji recover
--retry-failed-step`` / ``kaji run-series``）は、自分の pid と ``/proc`` 上の開始時刻を
``ProcessRecord`` として artifact に残す。``probe_liveness`` はその記録から
「まだ生きているか」を pid 再利用まで含めて判定する。判定できない場合は ``None``
（不明）を返し、呼び出し側は安全側（拒否）に倒す。
"""

from __future__ import annotations

import errno
import json
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..fsio import atomic_write

PROCESS_RECORD_SCHEMA_VERSION = 1
PROCESS_FILE = "process.json"
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_PROC_ROOT = Path("/proc")

ProcessRole = Literal["run", "launcher"]
LivenessBasis = Literal[
    "no_record",
    "record_unreadable",
    "other_host",
    "rebooted",
    "pid_absent",
    "zombie",
    "pid_verified",
    "pid_reused",
    "unverifiable",
    "finished_by_log",
]


class ProcessRecord(BaseModel):
    """生存判定に使うプロセスの記録（``process.json`` / launch record 共通）。"""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = PROCESS_RECORD_SCHEMA_VERSION
    role: ProcessRole
    pid: int = Field(gt=0)
    pid_start_ticks: int | None = None
    boot_id: str | None = None
    hostname: str
    started_at: str
    run_id: str | None = None


class ProcessRecordError(ValueError):
    """``ProcessRecord`` の artifact が読めない・不正な場合。"""


@dataclass(frozen=True)
class Liveness:
    """生存判定の結果。``alive`` が ``None`` は「判定不能」で、理由は ``basis``。"""

    alive: bool | None
    basis: LivenessBasis


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _kill0(pid: int) -> None:
    os.kill(pid, 0)


def _read_proc_stat(pid: int) -> str:
    return _read_text(_PROC_ROOT / str(pid) / "stat")


def _proc_available() -> bool:
    return (_PROC_ROOT / "self").exists()


@dataclass(frozen=True)
class HostProbe:
    """生存判定が触る OS 境界。テストで差し替えるための注入点。

    Attributes:
        hostname: 現ホスト名を返す。
        boot_id: 現在の boot id を返す（取得不能なら ``None``）。
        kill0: ``os.kill(pid, 0)`` 相当。失敗時は ``OSError`` を投げる。
        read_stat: ``/proc/<pid>/stat`` の内容を返す。消えていれば ``FileNotFoundError``。
        proc_available: ``/proc`` を参照できるか。
    """

    # 関数を直接 default にすると class 属性経由で bound method 化しうるため factory で束縛する。
    hostname: Callable[[], str] = field(default_factory=lambda: socket.gethostname)
    boot_id: Callable[[], str | None] = field(default_factory=lambda: _read_boot_id)
    kill0: Callable[[int], None] = field(default_factory=lambda: _kill0)
    read_stat: Callable[[int], str] = field(default_factory=lambda: _read_proc_stat)
    proc_available: Callable[[], bool] = field(default_factory=lambda: _proc_available)


def _read_boot_id() -> str | None:
    try:
        value = _BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def parse_proc_stat(text: str) -> tuple[str, int] | None:
    """``/proc/<pid>/stat`` から ``(state, starttime)`` を取り出す。

    ``comm``（第 2 欄）は空白や括弧を含みうるため、最後の ``)`` 以降を分割する。

    Args:
        text: ``/proc/<pid>/stat`` の内容。

    Returns:
        第 3 欄（state）と第 22 欄（starttime, clock ticks）。解析できなければ ``None``。
    """
    end = text.rfind(")")
    if end < 0:
        return None
    rest = text[end + 1 :].split()
    # rest[0] は第 3 欄、第 22 欄は rest[19]
    if len(rest) < 20:
        return None
    try:
        return rest[0], int(rest[19])
    except ValueError:
        return None


def current_process_record(
    role: ProcessRole,
    *,
    run_id: str | None = None,
    probe: HostProbe | None = None,
    pid: int | None = None,
) -> ProcessRecord:
    """自プロセス（または ``pid``）の ``ProcessRecord`` を組み立てる。

    Args:
        role: ``run``（``kaji run`` 自身）または ``launcher``。
        run_id: 既に判明している run_id。
        probe: OS 境界の注入点。
        pid: 記録する pid。省略時は自プロセス。

    Returns:
        開始 ticks / boot id は取得できなければ ``None`` の記録。
    """
    host = probe or HostProbe()
    target = pid if pid is not None else os.getpid()
    ticks: int | None = None
    try:
        parsed = parse_proc_stat(host.read_stat(target))
    except OSError:
        parsed = None
    if parsed is not None:
        ticks = parsed[1]
    return ProcessRecord(
        role=role,
        pid=target,
        pid_start_ticks=ticks,
        boot_id=host.boot_id(),
        hostname=host.hostname(),
        started_at=datetime.now(UTC).isoformat(),
        run_id=run_id,
    )


def write_process_record(path: Path, record: ProcessRecord) -> None:
    """``ProcessRecord`` を atomic に書き出す（親ディレクトリは必要なら作成）。

    Raises:
        OSError: 書き込みに失敗した場合。
    """
    atomic_write(path, json.dumps(record.model_dump(), ensure_ascii=False, indent=2) + "\n")


def read_process_record(path: Path) -> ProcessRecord | None:
    """``ProcessRecord`` を読み戻す。

    Args:
        path: 記録のパス。

    Returns:
        記録。ファイルが無ければ ``None``。

    Raises:
        ProcessRecordError: 読めない・JSON 不正・必須 key 欠落の場合。
    """
    if not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProcessRecordError(f"{path.name}: {exc}") from exc
    try:
        return ProcessRecord.model_validate(raw)
    except ValidationError as exc:
        raise ProcessRecordError(f"{path.name}: {exc.error_count()} validation error(s)") from exc


def probe_liveness(
    record: ProcessRecord | None,
    *,
    unreadable: bool = False,
    probe: HostProbe | None = None,
) -> Liveness:
    """記録から生存を判定する（設計書「生存判定」の表を上から評価）。

    Args:
        record: 生存記録。``None`` は記録なし。
        unreadable: 記録が存在するが読めなかった場合に ``True``。
        probe: OS 境界の注入点。

    Returns:
        判定結果。判定できない場合は ``alive=None``。
    """
    if unreadable:
        return Liveness(None, "record_unreadable")
    if record is None:
        return Liveness(None, "no_record")
    host = probe or HostProbe()
    if record.hostname != host.hostname():
        return Liveness(None, "other_host")
    current_boot = host.boot_id()
    if record.boot_id is not None and current_boot is not None and record.boot_id != current_boot:
        return Liveness(False, "rebooted")
    try:
        host.kill0(record.pid)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return Liveness(False, "pid_absent")
        if exc.errno != errno.EPERM:
            return Liveness(None, "unverifiable")
    if not host.proc_available():
        return Liveness(None, "unverifiable")
    try:
        stat_text = host.read_stat(record.pid)
    except FileNotFoundError:
        return Liveness(False, "pid_absent")
    except OSError as exc:
        if exc.errno == errno.ENOENT or exc.errno == errno.ESRCH:
            return Liveness(False, "pid_absent")
        return Liveness(None, "unverifiable")
    parsed = parse_proc_stat(stat_text)
    if parsed is None:
        return Liveness(None, "unverifiable")
    state, ticks = parsed
    if state in ("Z", "X"):
        return Liveness(False, "zombie")
    if record.pid_start_ticks is None:
        return Liveness(None, "unverifiable")
    if record.pid_start_ticks == ticks:
        return Liveness(True, "pid_verified")
    return Liveness(False, "pid_reused")


def is_finished_by_log(events: list[dict[str, Any]]) -> bool:
    """run.log 上で ``kaji run`` プロセスの以降の動作が終わったと判定できるか。

    ``workflow_end`` があり、かつ COMPLETE、非 resume の ``recovery_decision``、
    ``recovery_attempt_end`` のいずれかを満たす場合に ``True``。

    Args:
        events: run.log の event 列（順序どおり）。
    """
    end = next((e for e in reversed(events) if e.get("event") == "workflow_end"), None)
    if end is None:
        return False
    if end.get("status") == "COMPLETE":
        return True
    if any(e.get("event") == "recovery_attempt_end" for e in events):
        return True
    decisions = [e for e in events if e.get("event") == "recovery_decision"]
    return bool(decisions) and decisions[-1].get("decision") != "resume"


def complete_with_run_log(liveness: Liveness, events: list[dict[str, Any]] | None) -> Liveness:
    """null の生存判定を run.log の終端条件で補完する（``finished_by_log``）。

    ``no_record`` / ``other_host`` / ``unverifiable`` の null だけが対象。
    ``record_unreadable`` や true / false は変えない。

    Args:
        liveness: 記録からの判定結果。
        events: 対象 run の run.log の event 列。読めなかった場合は ``None``。
    """
    if liveness.alive is not None or events is None:
        return liveness
    if liveness.basis not in ("no_record", "other_host", "unverifiable"):
        return liveness
    if is_finished_by_log(events):
        return Liveness(False, "finished_by_log")
    return liveness
