"""Entry-point operations shared by ``kaji watch`` and the loop entrypoint (Issue #543 D3).

command 層（``commands/watch.py``）と内部 entrypoint（``python -m kaji_harness.watch``）は
ここを呼ぶ。前提の検証は ``preflight.check_watch_preconditions`` に集約する。
"""

from __future__ import annotations

import shlex
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..artifacts import resolve_artifacts_dir
from ..errors import WatchPreconditionError
from .constants import POLL_INTERVAL_SECONDS
from .herdr import WATCH_TAB_LABEL, CliHerdrClient, HerdrClient, find_tab
from .loop import WatchDeps, WatchProvider, _peek_state, run_once
from .models import OutcomeName, WatchRecord
from .notify import build_payload, send_notification
from .preflight import WatchContext
from .store import WatchLockHeld, WatchStore, judge_id

# 値は JSON の値
JsonDict = dict[str, Any]

_OUTCOME_KIND = {"still-working": "pane_idle", "not-stalled": "log_stall"}


def make_deps(context: WatchContext, herdr: HerdrClient | None) -> WatchDeps:
    """前提を満たした文脈から 1 周の依存を作る。"""
    return WatchDeps(
        provider=cast(WatchProvider, context.provider),
        artifacts_dir=resolve_artifacts_dir(context.config),
        repo_root=context.config.repo_root,
        repo=context.repo or None,
        notify_command=context.watch.notify_command,
        herdr=herdr,
    )


def default_herdr(context: WatchContext) -> HerdrClient | None:
    """Herdr の前提を満たしていれば CLI の client を返す。"""
    return CliHerdrClient(context.herdr_bin) if context.herdr_bin else None


def loop_command(repo_root: Path) -> str:
    """監視の loop を起動する 1 行のコマンド。"""
    return shlex.join([sys.executable, "-m", "kaji_harness.watch", "--workdir", str(repo_root)])


def start(context: WatchContext, herdr: HerdrClient) -> str:
    """監視の loop を ``kaji-watch`` タブで起動する（冪等）。

    Returns:
        出力する 1 行（``watch already running (pane <id>)`` または ``watch started (pane <id>)``）。
    """
    store = WatchStore(resolve_artifacts_dir(context.config))
    if store.is_locked():
        return f"watch already running (pane {store.lock_holder_pane() or 'unknown'})"
    tab = find_tab(herdr.list_tabs(), WATCH_TAB_LABEL)
    pane_id: str | None = None
    if tab is not None and tab.pane_id is not None:
        foreground, _ = herdr.pane_foreground(tab.pane_id)
        if foreground == "shell_only":
            pane_id = tab.pane_id
    if pane_id is None:
        _, pane_id = herdr.create_tab(WATCH_TAB_LABEL, context.config.repo_root)
    herdr.run_in_pane(pane_id, loop_command(context.config.repo_root))
    return f"watch started (pane {pane_id})"


def once(deps: WatchDeps, *, dry_run: bool, pane_id: str | None) -> JsonDict:
    """1 周分を実行する（``dry_run`` 以外は lock を取る）。

    Raises:
        WatchPreconditionError: lock を取れない場合（``watch is running``）。
    """
    if dry_run:
        return run_once(deps, dry_run=True).plan
    store = deps.store
    try:
        store.acquire_lock(pane_id)
    except WatchLockHeld as exc:
        raise WatchPreconditionError("lock", "watch is running") from exc
    try:
        return run_once(deps).plan
    finally:
        store.release_lock()


def run_loop(deps: WatchDeps, *, pane_id: str | None, sleep: Any = time.sleep) -> str:
    """監視の loop（終了条件を満たすまで周を繰り返す）。

    Returns:
        出力する 1 行。
    """
    store = deps.store
    try:
        store.acquire_lock(pane_id)
    except WatchLockHeld as exc:
        return str(exc)
    try:
        while True:
            if run_once(deps).exit:
                return "watch finished"
            sleep(POLL_INTERVAL_SECONDS)
    finally:
        store.release_lock()


def status(deps: WatchDeps) -> JsonDict:
    """``kaji watch status --json`` の文書（schema v1）を作る。GitHub と artifacts に書かない。"""
    store = deps.store
    state = _peek_state(store)
    result = run_once(deps, dry_run=True)
    plan = result.plan
    # 今開いている triage タブだけを表示する（Herdr の外では観測できないので null）
    triage = {issue: tab.pane_id or tab.tab_id for issue, tab in result.open_triage.items()}
    issues = [
        {
            "issue": entry["issue"],
            "github_state": entry["github_state"],
            "current_label": entry["current_label"],
            "target_label": entry["target_label"],
            "hold_reason": entry["hold_reason"],
            "run_id": entry["run_id"],
            "triage_tab": triage.get(entry["issue"]),
            "suppressed_until_progress": entry["issue"] in result.suppressed,
        }
        for entry in plan.get("issues", [])
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "watcher": {"running": store.is_locked(), "pane_id": store.lock_holder_pane()},
        "notify": "enabled" if deps.notify_command else "disabled",
        "issues": issues,
        "pending_labels": sorted(int(k) for k in state.pending_labels),
        "pending_reports": sorted(int(k) for k in state.pending_reports),
    }


def events(store: WatchStore) -> JsonDict:
    """``kaji watch events --json`` の文書（schema v1）。"""
    state = _peek_state(store)
    return {
        "schema_version": 1,
        "events": [
            {
                "key": event.key,
                "kind": event.kind,
                "issue": event.issue,
                "run_id": event.run_id,
                "detected_at": event.detected_at,
                "launch_status": event.launch_status,
                "launch_attempts": event.launch_attempts,
                "triage_pane": event.triage_pane,
                "event_file": event.event_file,
            }
            for event in sorted(state.events.values(), key=lambda e: e.detected_at)
        ],
        "records": [record.model_dump() for record in store.read_records()],
    }


def record(
    deps: WatchDeps,
    event_file: Path,
    outcome: OutcomeName,
    *,
    comment_url: str | None,
    summary: str | None,
) -> WatchRecord:
    """判断の結果を ``records.jsonl`` に追記する（``handoff`` は通知も送る）。

    Raises:
        WatchPreconditionError: event ファイルが不正、または outcome と kind が合わない場合。
    """
    store = deps.store
    try:
        event = store.read_event(event_file)
    except (OSError, ValueError) as exc:
        raise WatchPreconditionError("event", f"invalid event file: {exc}") from exc
    required_kind = _OUTCOME_KIND.get(outcome)
    if required_kind is not None and event.kind != required_kind:
        raise WatchPreconditionError(
            "event", f"--outcome {outcome} is only valid for {required_kind} events"
        )
    entry = WatchRecord(
        event_key=event.key,
        outcome=outcome,
        recorded_at=datetime.now(UTC).isoformat(),
        comment_url=comment_url,
        summary=summary,
        judge=judge_id(),
    )
    store.append_record(entry)
    if outcome == "handoff":
        state = _peek_state(store)
        tracked = state.events.get(event.key)
        send_notification(
            deps.notify_command,
            build_payload(
                "handoff",
                repo=deps.repo,
                issue=event.issue,
                summary=summary or f"{event.kind} on #{event.issue}",
                comment_url=comment_url,
                pane_id=tracked.triage_pane if tracked is not None else None,
                event_key=event.key,
            ),
            cwd=deps.repo_root,
        )
    return entry
