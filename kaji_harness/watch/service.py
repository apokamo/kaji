"""Entry-point operations shared by ``kaji watch`` and the loop entrypoint (Issue #543 D3).

command 層（``commands/watch.py``）と内部 entrypoint（``python -m kaji_harness.watch``）は
ここを呼ぶ。前提の検証は ``preflight.check_watch_preconditions`` に集約する。
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..artifacts import resolve_artifacts_dir
from ..errors import AutoRecoveryLimitReached, WatchPreconditionError
from .constants import AUTO_RECOVERY_LIMITS, POLL_INTERVAL_SECONDS, RETRY_TAB_PREFIX
from .herdr import WATCH_TAB_LABEL, CliHerdrClient, HerdrClient, find_tab
from .loop import WatchDeps, WatchProvider, _peek_state, run_once
from .models import OutcomeName, WatchEventFile, WatchRecord
from .notify import build_payload, send_notification
from .preflight import WatchContext
from .recovery import (
    AutoRecoveryInputError,
    check_paths,
    check_until,
    is_auto_outcome,
    recovery_rows,
    unit_of,
    validate_event_for,
)
from .store import WatchLockHeld, WatchStore, judge_id
from .workflows import WorkflowResolutionError, resolve_workflow_name

# 値は JSON の値
JsonDict = dict[str, Any]

_OUTCOME_KIND = {"still-working": "pane_idle", "not-stalled": "log_stall"}
#: ``kaji watch events --json`` の schema version（#545 で 2）。
EVENTS_SCHEMA_VERSION = 2
#: ``kaji watch recoveries --json`` の schema version。
RECOVERIES_SCHEMA_VERSION = 1


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
    """``kaji watch events --json`` の文書（schema v2。#545 で outcome に 5 値を追加）。"""
    state = _peek_state(store)
    return {
        "schema_version": EVENTS_SCHEMA_VERSION,
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
    reason: str | None = None,
    until: str | None = None,
    paths: list[str] | None = None,
) -> WatchRecord:
    """判断の結果を ``records.jsonl`` に追記する（``handoff`` は通知も送る）。

    自動復旧の outcome（#545）は検証の後、``records.jsonl`` の ``flock`` の下で上限を数えて追記する。

    Raises:
        WatchPreconditionError: event ファイルが不正、outcome と kind が合わない、引数が不正な場合。
        AutoRecoveryLimitReached: 自動復旧の上限に達した場合（追記しない）。
    """
    store = deps.store
    event = _read_event(store, event_file)
    if is_auto_outcome(outcome):
        if outcome == "retry-sent":
            raise WatchPreconditionError("event", "retry-sent is recorded only by kaji watch retry")
        return _record_auto(deps, event, outcome, reason=reason, until=until, paths=paths)
    if reason is not None or until is not None or paths:
        raise WatchPreconditionError(
            "event", f"--reason / --until / --path are not valid for --outcome {outcome}"
        )
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


def retry(deps: WatchDeps, event_file: Path, reason: str) -> JsonDict:
    """環境が原因の失敗を 1 回だけやり直す（#545 D1 の 5.）。

    workflow の解決と検証の後、上限の確認と ``retry-sent`` の記録を lock の下で 1 回に行い、
    記録できたときだけ新しい Herdr タブで ``kaji recover --retry-failed-step`` を起動して
    通知 ``retry`` を送る。安全ゲートの判定は ``kaji recover`` に任せる。

    Returns:
        ``{"record", "tab_id", "pane_id", "command"}``。

    Raises:
        WatchPreconditionError: Herdr の外・event が不正・workflow を解決できない場合
            （記録しない。上限を消費しない）。
        AutoRecoveryLimitReached: 上限に達した場合（タブを起動しない）。
    """
    if deps.herdr is None:
        raise WatchPreconditionError("herdr", "kaji watch retry must run inside Herdr")
    store = deps.store
    event = _read_event(store, event_file)
    try:
        validate_event_for("retry-sent", event, reason)
    except AutoRecoveryInputError as exc:
        raise WatchPreconditionError("event", str(exc)) from exc
    if not event.workflow or not event.run_id:
        raise WatchPreconditionError("event", "kaji watch retry requires event workflow and run_id")
    try:
        workflow_path = resolve_workflow_name(event.workflow, deps.repo_root)
    except WorkflowResolutionError as exc:
        raise WatchPreconditionError("workflow", str(exc)) from exc
    unit = unit_of(event, "retry-sent")
    _, limit = AUTO_RECOVERY_LIMITS["retry-sent"]
    entry = WatchRecord(
        event_key=event.key,
        outcome="retry-sent",
        recorded_at=deps.now().isoformat(),
        judge=judge_id(),
        reason=reason,
        unit=unit,
        workflow_path=str(workflow_path),
    )
    if not store.append_record_limited(entry, unit=unit, limit=limit):
        raise AutoRecoveryLimitReached("retry-sent", unit, limit)
    command = retry_command(workflow_path, event.issue, event.run_id)
    tab_id, pane_id = deps.herdr.create_tab(f"{RETRY_TAB_PREFIX}{event.issue}", deps.repo_root)
    deps.herdr.run_in_pane(pane_id, command)
    send_notification(
        deps.notify_command,
        build_payload(
            "retry",
            repo=deps.repo,
            issue=event.issue,
            summary=f"retry {event.failed_step or event.step or 'failed step'} on #{event.issue}: "
            f"{reason}",
            pane_id=pane_id,
            event_key=event.key,
        ),
        cwd=deps.repo_root,
    )
    return {"record": entry.model_dump(), "tab_id": tab_id, "pane_id": pane_id, "command": command}


def retry_command(workflow_path: Path, issue: int, run_id: str) -> str:
    """``kaji watch retry`` が新しいタブで流す 1 行のコマンド。"""
    return shlex.join(
        [
            "kaji",
            "recover",
            str(workflow_path),
            str(issue),
            "--retry-failed-step",
            "--run-id",
            run_id,
        ]
    )


def recoveries(store: WatchStore, issue: int | None) -> JsonDict:
    """``kaji watch recoveries --json`` の文書（schema v1）。"""
    return {
        "schema_version": RECOVERIES_SCHEMA_VERSION,
        "recoveries": recovery_rows(store.read_records(), issue),
    }


def list_checkouts(repo_root: Path) -> list[Path]:
    """repo の main checkout と ``git worktree list --porcelain`` の worktree を返す。"""
    checkouts = [repo_root.resolve()]
    try:
        completed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return checkouts
    if completed.returncode != 0:
        return checkouts
    for line in completed.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ").strip()).resolve()
            if path not in checkouts:
                checkouts.append(path)
    return checkouts


def _read_event(store: WatchStore, event_file: Path) -> WatchEventFile:
    try:
        return store.read_event(event_file)
    except (OSError, ValueError) as exc:
        raise WatchPreconditionError("event", f"invalid event file: {exc}") from exc


def _record_auto(
    deps: WatchDeps,
    event: WatchEventFile,
    outcome: OutcomeName,
    *,
    reason: str | None,
    until: str | None,
    paths: list[str] | None,
) -> WatchRecord:
    now = deps.now()
    try:
        validate_event_for(outcome, event, reason)
        normalized_until = check_until(until, now) if outcome == "wait" else None
        if outcome != "wait" and until is not None:
            raise AutoRecoveryInputError("--until is only valid for --outcome wait")
        if outcome == "dialog-answered":
            checked_paths: list[str] | None = check_paths(paths, list_checkouts(deps.repo_root))
        elif paths:
            raise AutoRecoveryInputError("--path is only valid for --outcome dialog-answered")
        else:
            checked_paths = None
    except AutoRecoveryInputError as exc:
        raise WatchPreconditionError("event", str(exc)) from exc
    unit = unit_of(event, outcome)
    _, limit = AUTO_RECOVERY_LIMITS[outcome]
    entry = WatchRecord(
        event_key=event.key,
        outcome=outcome,
        recorded_at=now.isoformat(),
        judge=judge_id(),
        reason=reason,
        until=normalized_until,
        paths=checked_paths,
        unit=unit,
    )
    if not deps.store.append_record_limited(entry, unit=unit, limit=limit):
        raise AutoRecoveryLimitReached(outcome, unit, limit)
    return entry
