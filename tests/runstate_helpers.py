"""Shared fixture builders for Issue #540 runstate / status / retry tests."""

from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import Any

from kaji_harness.runstate.process import PROCESS_FILE, HostProbe, ProcessRecord

HOST = "host-a"
BOOT = "boot-1"


def stat_line(pid: int, ticks: int, state: str = "S") -> str:
    """``/proc/<pid>/stat`` 形式の 1 行（第 22 欄 = ticks）。comm は空白と括弧を含む。"""
    fields = [state] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
    return f"{pid} (kaji (run) x) " + " ".join(fields)


def fake_probe(
    alive: dict[int, int] | None = None,
    *,
    zombies: frozenset[int] = frozenset(),
    host: str = HOST,
    boot: str | None = BOOT,
    proc: bool = True,
    eperm: frozenset[int] = frozenset(),
    vanish_on_stat: frozenset[int] = frozenset(),
    zombie_state: str = "Z",
) -> HostProbe:
    """pid → ticks の表で OS 境界を模擬する ``HostProbe``。"""
    table = dict(alive or {})

    def kill0(pid: int) -> None:
        if pid in eperm:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        if pid not in table and pid not in zombies and pid not in vanish_on_stat:
            raise ProcessLookupError(errno.ESRCH, "No such process")

    def read_stat(pid: int) -> str:
        if pid in vanish_on_stat or (pid not in table and pid not in zombies):
            raise FileNotFoundError(errno.ENOENT, "gone")
        if pid in zombies:
            return stat_line(pid, table.get(pid, 1), zombie_state)
        return stat_line(pid, table[pid])

    return HostProbe(
        hostname=lambda: host,
        boot_id=lambda: boot,
        kill0=kill0,
        read_stat=read_stat,
        proc_available=lambda: proc,
    )


def record(
    pid: int,
    ticks: int | None = 100,
    *,
    role: str = "run",
    run_id: str | None = None,
    host: str = HOST,
    boot: str | None = BOOT,
) -> ProcessRecord:
    return ProcessRecord.model_validate(
        {
            "role": role,
            "pid": pid,
            "pid_start_ticks": ticks,
            "boot_id": boot,
            "hostname": host,
            "started_at": "2026-11-12T00:00:00+00:00",
            "run_id": run_id,
        }
    )


def ev(event: str, **fields: Any) -> dict[str, Any]:
    return {"ts": "2026-11-12T00:00:00+00:00", "event": event, **fields}


def start(workflow: str = "dev") -> dict[str, Any]:
    return ev("workflow_start", issue="540", workflow=workflow, schema_version=1)


def step_start(step: str, attempt: int = 1, **fields: Any) -> dict[str, Any]:
    return ev(
        "step_start",
        step_id=step,
        attempt=attempt,
        agent=fields.pop("agent", "claude"),
        session_id=fields.pop("session_id", None),
        dispatch="agent",
        **fields,
    )


def step_end(step: str, attempt: int = 1, status: str = "PASS") -> dict[str, Any]:
    return ev(
        "step_end", step_id=step, attempt=attempt, verdict={"status": status}, dispatch="agent"
    )


def end(status: str, error: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"status": status, "cycle_counts": {}, "total_duration_ms": 1}
    if error is not None:
        fields["error"] = error
    return ev("workflow_end", **fields)


def write_run(
    artifacts: Path,
    issue: str,
    run_id: str,
    events: list[dict[str, Any]] | None,
    *,
    process: ProcessRecord | None = None,
    raw_log: str | None = None,
    results: dict[tuple[str, int], dict[str, Any]] | None = None,
    panes: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> Path:
    """run dir を作り、run.log / process.json / result.json / pane-metadata.json を置く。"""
    run_dir = artifacts / issue / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if raw_log is not None:
        (run_dir / "run.log").write_text(raw_log, encoding="utf-8")
    elif events is not None:
        (run_dir / "run.log").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )
    if process is not None:
        (run_dir / PROCESS_FILE).write_text(process.model_dump_json(), encoding="utf-8")
    for (step, attempt), data in (results or {}).items():
        path = run_dir / "steps" / step / f"attempt-{attempt:03d}" / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    for (step, attempt), data in (panes or {}).items():
        path = run_dir / "steps" / step / f"attempt-{attempt:03d}" / "pane-metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return run_dir


def write_state(
    artifacts: Path,
    issue: str,
    *,
    worktree: str | None = "/wt/feat-540",
    branch: str | None = "feat/540",
    raw: str | None = None,
) -> None:
    path = artifacts / issue / "session-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return
    path.write_text(
        json.dumps(
            {
                "issue_number": issue,
                "step_history": [],
                "last_completed_step": None,
                "worktree_dir": worktree,
                "branch_name": branch,
                "sessions": {},
                "cycle_counts": {},
            }
        ),
        encoding="utf-8",
    )


def write_config(root: Path) -> Path:
    kaji = root / ".kaji"
    kaji.mkdir(parents=True, exist_ok=True)
    path = kaji / "config.toml"
    path.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    return path
