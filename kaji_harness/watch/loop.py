"""One poll of ``kaji watch`` (Issue #543).

1 周 = 事実の収集 → 目標の計算 → 検知 → 処理意図の保存（M4）→ ラベルの付け替え →
triage の起動 → ``aborted`` の報告 → 終了判定。純粋関数（``labels`` / ``detect``）の
結果だけを副作用に写す。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..providers import Comment, Issue
from ..runstate.query import collect_status, list_series, locate_step_artifacts
from .constants import (
    MANAGED_LABELS,
    MAX_LAUNCH_ATTEMPTS,
    STATE_ABORTED,
    STATE_DONE,
    STATE_LABEL_PREFIX,
    STATE_PLANNED,
    STATE_RUNNING,
)
from .detect import (
    DetectedEvent,
    DetectTracking,
    Ledger,
    PaneIdleCounter,
    RecordEntry,
    StallTracker,
    detect_events,
)
from .herdr import (
    HerdrClient,
    TabRef,
    find_tab,
    foreground_has,
    open_triage_tabs,
    triage_command,
    triage_tab_label,
)
from .labels import (
    Target,
    TargetBasis,
    WatchIssue,
    compute_target,
    count_active,
    resolve_anchor,
    should_exit,
)
from .models import (
    EventState,
    PaneIdleState,
    ReportState,
    StallState,
    StepArtifactsModel,
    WatchEventFile,
    WatchState,
)
from .notify import build_payload, send_notification
from .store import WatchStore

# 値は JSON の値（#540 schema v1 の文書）
JsonDict = dict[str, Any]

ABORTED_MARKER = "<!-- kaji-watch: aborted key={key} -->"
_console = logging.getLogger("kaji.watch")


class WatchProvider(Protocol):
    """watch が使う GitHub provider の操作（``GitHubProvider`` が満たす）。"""

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        """label で絞った Issue の全件。"""
        ...

    def view_issue(self, issue_id: str) -> Issue:
        """1 Issue。"""
        ...

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        """ラベル等を変更する。"""
        ...

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        """コメントを投稿する。"""
        ...

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        """全コメント。"""
        ...


@dataclass
class WatchDeps:
    """1 周に必要な依存。

    Attributes:
        provider: GitHub provider。
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        repo_root: repo root（通知・タブの cwd）。
        repo: provider の repo（通知に載せる）。
        notify_command: ``[watch].notify_command``。
        herdr: Herdr の操作（``None`` は Herdr の外の ``--dry-run``）。
        now: 現在時刻（テスト用）。
    """

    provider: WatchProvider
    artifacts_dir: Path
    repo_root: Path
    repo: str | None
    notify_command: tuple[str, ...] | None
    herdr: HerdrClient | None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def store(self) -> WatchStore:
        """``_watch/`` の store。"""
        return WatchStore(self.artifacts_dir)


@dataclass
class Facts:
    """1 周分の事実。"""

    issues: list[WatchIssue]
    series: list[JsonDict]
    tabs: list[TabRef] | None
    pane_status: dict[int, str | None]
    generated_at: str


@dataclass
class PollResult:
    """1 周の結果。"""

    exit: bool
    plan: JsonDict


def _state_labels(labels: list[str]) -> tuple[str, ...]:
    return tuple(sorted(name for name in labels if name.startswith(STATE_LABEL_PREFIX)))


def collect_facts(deps: WatchDeps, state: WatchState) -> Facts:
    """GitHub・runstate・Herdr から事実を集める（読むだけ）。"""
    provider = deps.provider
    by_id: dict[int, tuple[str, str, tuple[str, ...]]] = {}
    for label in (STATE_PLANNED, STATE_RUNNING, STATE_ABORTED):
        for found in provider.search_issues_all(labels=[label], state="all"):
            by_id[int(found.id)] = (
                found.state,
                found.state_reason,
                _state_labels([lb.name for lb in found.labels]),
            )
    for found in provider.search_issues_all(labels=[STATE_DONE], state="open"):
        by_id[int(found.id)] = (
            found.state,
            found.state_reason,
            _state_labels([lb.name for lb in found.labels]),
        )
    extra = set(state.pending_labels) | set(state.pending_reports)
    extra |= {
        str(event.issue)
        for event in state.events.values()
        if event.launch_status in ("detected", "launching")
    }
    for raw in sorted(extra, key=int):
        number = int(raw)
        if number in by_id:
            continue
        viewed = provider.view_issue(raw)
        by_id[number] = (
            viewed.state,
            viewed.state_reason,
            _state_labels([lb.name for lb in viewed.labels]),
        )
    numbers = sorted(by_id)
    document = collect_status(deps.artifacts_dir, [str(n) for n in numbers])
    statuses = {int(entry["issue"]): entry for entry in document["issues"]}
    issues = [
        WatchIssue(
            issue=number,
            github_state=by_id[number][0].lower(),
            state_reason=by_id[number][1] or None,
            state_labels=by_id[number][2],
            status=statuses[number],
        )
        for number in numbers
    ]
    tabs: list[TabRef] | None = None
    pane_status: dict[int, str | None] = {}
    if deps.herdr is not None:
        tabs = deps.herdr.list_tabs()
        for issue in issues:
            run = issue.latest_run
            pane = run.get("pane") if run is not None else None
            if run is None or run.get("termination") != "running" or not isinstance(pane, dict):
                continue
            pane_id = pane.get("pane_id")
            if isinstance(pane_id, str):
                pane_status[issue.issue] = deps.herdr.agent_status(
                    pane_id, pane.get("origin_pane"), run.get("run_id")
                )
    return Facts(
        issues=issues,
        series=list_series(deps.artifacts_dir),
        tabs=tabs,
        pane_status=pane_status,
        generated_at=str(document["generated_at"]),
    )


def _in_series(issue: int, series: list[JsonDict]) -> bool:
    return any(
        isinstance(member, dict) and member.get("issue") == issue
        for entry in series
        for member in (entry.get("members") or [])
    )


def update_baselines(facts: Facts, state: WatchState) -> None:
    """``baselines`` / ``reopen_baselines`` を初めて見た周の run_id で埋める。"""
    for issue in facts.issues:
        key = str(issue.issue)
        run = issue.latest_run
        run_id = run.get("run_id") if run is not None else None
        latest = run_id if isinstance(run_id, str) else ""
        if (
            issue.current_label == STATE_PLANNED
            and key not in state.baselines
            and not _in_series(issue.issue, facts.series)
        ):
            state.baselines[key] = latest
        if issue.is_open and issue.current_label == STATE_DONE:
            state.reopen_baselines.setdefault(key, latest)
        else:
            state.reopen_baselines.pop(key, None)


def compute_targets(facts: Facts, state: WatchState) -> dict[int, Target]:
    """全 Issue の目標を計算する。"""
    targets: dict[int, Target] = {}
    for issue in facts.issues:
        key = str(issue.issue)
        anchor = resolve_anchor(issue.issue, facts.series, state.baselines)
        targets[issue.issue] = compute_target(
            issue,
            anchor,
            state.reopen_baselines.get(key),
            pending=key in state.pending_labels,
        )
    return targets


def label_diff(issue: WatchIssue, target: Target) -> tuple[list[str], list[str]]:
    """``(付けるラベル, 外すラベル)`` を返す（書き換えない Issue は空）。"""
    if not target.write:
        return [], []
    add = [target.label] if target.label and target.label not in issue.state_labels else []
    remove = [
        name for name in issue.state_labels if name in MANAGED_LABELS and name != target.label
    ]
    return add, remove


def _ledger(state: WatchState, store: WatchStore) -> Ledger:
    progress_by_key: dict[str, Any] = {}
    for key, event in state.events.items():
        try:
            progress_by_key[key] = store.read_event(Path(event.event_file)).progress
        except (OSError, ValueError):
            progress_by_key[key] = None
    records: list[RecordEntry] = []
    for record in store.read_records():
        issue_text = record.event_key.split(":", 1)[0]
        if not issue_text.isdigit():
            continue
        try:
            recorded_at = datetime.fromisoformat(record.recorded_at)
        except ValueError:
            continue
        records.append(
            RecordEntry(
                event_key=record.event_key,
                issue=int(issue_text),
                outcome=record.outcome,
                recorded_at=recorded_at,
                progress=progress_by_key.get(record.event_key),
            )
        )
    return Ledger(event_keys=frozenset(state.events), records=tuple(records))


def _tracking(state: WatchState) -> DetectTracking:
    return DetectTracking(
        pane_idle={
            int(k): PaneIdleCounter(v.run_id, v.step, v.attempt, v.idle_polls, v.episode)
            for k, v in state.pane_idle.items()
        },
        stall={
            int(k): StallTracker(tuple(v.signature), datetime.fromisoformat(v.since))
            for k, v in state.stall.items()
        },
    )


def _store_tracking(state: WatchState, tracking: DetectTracking) -> None:
    state.pane_idle = {
        str(k): PaneIdleState(
            run_id=v.run_id,
            step=v.step,
            attempt=v.attempt,
            idle_polls=v.idle_polls,
            episode=v.episode,
        )
        for k, v in tracking.pane_idle.items()
    }
    state.stall = {
        str(k): StallState(signature=list(v.signature), since=v.since.isoformat())
        for k, v in tracking.stall.items()
    }


def build_event_file(
    event: DetectedEvent, issue: WatchIssue, deps: WatchDeps, now: datetime, generated_at: str
) -> WatchEventFile:
    """検知時点の事実を event ファイルに固定する。"""
    run = issue.latest_run or {}
    located = locate_step_artifacts(
        deps.artifacts_dir, str(issue.issue), event.run_id, event.step, event.attempt
    )
    return WatchEventFile(
        key=event.key,
        kind=event.kind,
        issue=event.issue,
        detected_at=now.isoformat(),
        run_id=event.run_id,
        workflow=run.get("workflow"),
        step=event.step,
        attempt=event.attempt,
        failed_step=run.get("failed_step"),
        failure=run.get("failure"),
        pane=run.get("pane"),
        artifacts=StepArtifactsModel(
            run_dir=str(located.run_dir) if located.run_dir else None,
            attempt_dir=str(located.attempt_dir) if located.attempt_dir else None,
        ),
        status_document={
            "schema_version": 1,
            "generated_at": generated_at,
            "series": None,
            "issues": [issue.status],
        },
        progress=event.progress,
    )


def _plan(facts: Facts, targets: dict[int, Target], events: list[DetectedEvent]) -> JsonDict:
    issues = []
    for issue in facts.issues:
        target = targets[issue.issue]
        add, remove = label_diff(issue, target)
        run = issue.latest_run
        issues.append(
            {
                "issue": issue.issue,
                "github_state": issue.github_state,
                "current_label": issue.current_label,
                "target_label": target.label if target.write else None,
                "hold_reason": target.hold_reason,
                "run_id": run.get("run_id") if run is not None else None,
                "add": add,
                "remove": remove,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": facts.generated_at,
        "herdr": "unavailable" if facts.tabs is None else "available",
        "issues": issues,
        "events": [
            {"key": event.key, "kind": event.kind, "issue": event.issue, "run_id": event.run_id}
            for event in events
        ],
    }


def run_once(deps: WatchDeps, *, dry_run: bool = False) -> PollResult:
    """1 周分を実行する。``dry_run`` なら計算結果だけを返し、何も書かない。"""
    store = deps.store
    state = store.load_state() if not dry_run else _peek_state(store)
    now = deps.now()
    try:
        facts = collect_facts(deps, state)
    except Exception as exc:  # noqa: BLE001 — 収集の失敗は周を飛ばして監視を続ける契約
        _console.warning("failed to collect facts: %s: %s", type(exc).__name__, exc)
        if dry_run:
            raise
        state.empty_polls = 0
        store.save_state(state)
        return PollResult(exit=False, plan={})

    update_baselines(facts, state)
    targets = compute_targets(facts, state)
    open_tabs = open_triage_tabs(facts.tabs or [])
    result = detect_events(
        facts.issues,
        targets,
        facts.pane_status,
        _tracking(state),
        _ledger(state, store),
        now,
        open_triage=frozenset(open_tabs),
    )
    plan = _plan(facts, targets, result.events)
    if dry_run:
        if facts.tabs is None:
            plan["events"] = [e for e in plan["events"] if e["kind"] != "pane_idle"]
        return PollResult(exit=False, plan=plan)

    issues_by_id = {issue.issue: issue for issue in facts.issues}
    # M4: 処理意図（付け替え・新しい event・報告）をラベル操作より前に 1 回で保存する
    for issue in facts.issues:
        add, remove = label_diff(issue, targets[issue.issue])
        if add or remove:
            state.pending_labels[str(issue.issue)] = targets[issue.issue].label
        elif str(issue.issue) in state.pending_labels:
            state.pending_labels.pop(str(issue.issue))
    for event in result.events:
        path = store.write_event(
            build_event_file(event, issues_by_id[event.issue], deps, now, facts.generated_at)
        )
        state.events[event.key] = EventState(
            key=event.key,
            kind=event.kind,
            issue=event.issue,
            run_id=event.run_id,
            detected_at=now.isoformat(),
            event_file=str(path),
        )
        if event.kind == "aborted":
            state.pending_reports[str(event.issue)] = event.key
    _store_tracking(state, result.tracking)
    store.save_state(state)

    _apply_labels(deps, store, state, issues_by_id, targets)
    if deps.herdr is not None:
        _launch_triage(deps, store, state, facts.tabs or [])
    _report_aborted(deps, store, state, targets)

    active = count_active(facts.issues, targets)
    state.empty_polls = state.empty_polls + 1 if active == 0 else 0
    has_pending = bool(state.pending_labels or state.pending_reports) or any(
        event.launch_status in ("detected", "launching") for event in state.events.values()
    )
    store.save_state(state)
    finished = should_exit(empty_polls=state.empty_polls, has_pending=has_pending)
    if finished:
        send_notification(
            deps.notify_command,
            build_payload("watch_exit", repo=deps.repo, issue=None, summary="kaji watch finished"),
            cwd=deps.repo_root,
        )
    return PollResult(exit=finished, plan=plan)


def _peek_state(store: WatchStore) -> WatchState:
    """状態ファイルを退避せずに読む（``--dry-run`` は何も書かない）。"""
    path = store.root / "state.json"
    if not path.is_file():
        return WatchState()
    try:
        return WatchState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WatchState()


def _apply_labels(
    deps: WatchDeps,
    store: WatchStore,
    state: WatchState,
    issues: dict[int, WatchIssue],
    targets: dict[int, Target],
) -> None:
    """付ける → 外す の 2 段で付け替え、済んだら ``pending_labels`` から消す。"""
    for key in list(state.pending_labels):
        number = int(key)
        issue = issues.get(number)
        target = targets.get(number)
        if issue is None or target is None or not target.write:
            continue
        add, remove = label_diff(issue, target)
        try:
            if add:
                deps.provider.edit_issue(key, add_labels=add)
                store.append_label_log(number, add, [])
            if remove:
                deps.provider.edit_issue(key, remove_labels=remove)
                store.append_label_log(number, [], remove)
        except Exception as exc:  # noqa: BLE001 — 書き込みの失敗は次の周で直す（冪等）
            _console.warning("label update failed for #%s: %s", key, type(exc).__name__)
            continue
        state.pending_labels.pop(key, None)
        store.save_state(state)


def _launch_triage(
    deps: WatchDeps, store: WatchStore, state: WatchState, tabs: list[TabRef]
) -> None:
    """``detected`` / ``launching`` の event の triage タブを起動する（M4 の照合付き）。"""
    herdr = deps.herdr
    if herdr is None:
        return
    for event in list(state.events.values()):
        if event.launch_status not in ("detected", "launching"):
            continue
        label = triage_tab_label(event.issue)
        tab = find_tab(tabs, label)
        if tab is not None and tab.pane_id is not None:
            foreground, commands = herdr.pane_foreground(tab.pane_id)
            if event.launch_status == "launching":
                if foreground == "active" and foreground_has(commands, "claude"):
                    event.launch_status = "launched"
                    event.triage_tab, event.triage_pane = tab.tab_id, tab.pane_id
                    store.save_state(state)
                    continue
                if foreground == "shell_only":
                    herdr.close_tab(tab.tab_id)
                else:
                    continue
            else:
                # 抑止 1: 別の event の triage タブが開いている間は起動しない
                continue
        event.launch_status = "launching"
        event.launch_attempts += 1
        store.save_state(state)
        try:
            tab_id, pane_id = herdr.create_tab(label, deps.repo_root)
            event.triage_tab, event.triage_pane = tab_id, pane_id
            store.save_state(state)
            herdr.run_in_pane(pane_id, triage_command(Path(event.event_file)))
        except Exception as exc:  # noqa: BLE001 — Herdr の失敗は次の周でやり直す
            _console.warning("triage launch failed for %s: %s", event.key, type(exc).__name__)
            event.launch_status = (
                "launch_failed" if event.launch_attempts >= MAX_LAUNCH_ATTEMPTS else "detected"
            )
            store.save_state(state)
            continue
        event.launch_status = "launched"
        store.save_state(state)


def _report_aborted(
    deps: WatchDeps, store: WatchStore, state: WatchState, targets: dict[int, Target]
) -> None:
    """ラベルが ``aborted`` に揃った Issue に 1 回だけ報告する（marker で重複を防ぐ）。"""
    for issue_key, event_key in list(state.pending_reports.items()):
        number = int(issue_key)
        target = targets.get(number)
        if target is None or target.basis is not TargetBasis.FAILED_RUN:
            state.pending_reports.pop(issue_key)
            store.save_state(state)
            continue
        if issue_key in state.pending_labels:
            continue
        marker = ABORTED_MARKER.format(key=event_key)
        event = state.events.get(event_key)
        try:
            url = None
            for comment in deps.provider.list_issue_comments_all(issue_key):
                if comment.body.splitlines()[:1] == [marker]:
                    url = comment.ref or None
                    break
            if url is None:
                created = deps.provider.comment_issue(
                    issue_key, _aborted_body(marker, event, store)
                )
                url = created.ref or None
        except Exception as exc:  # noqa: BLE001 — 結果不明は次の周に marker 確認からやり直す
            _console.warning("aborted report failed for #%s: %s", issue_key, type(exc).__name__)
            continue
        state.reports[event_key] = ReportState(comment_url=url, notified=True)
        state.pending_reports.pop(issue_key)
        store.save_state(state)
        send_notification(
            deps.notify_command,
            build_payload(
                "aborted",
                repo=deps.repo,
                issue=number,
                summary=_summary(event, store),
                comment_url=url,
                pane_id=event.triage_pane if event is not None else None,
                event_key=event_key,
            ),
            cwd=deps.repo_root,
        )


def _event_file(event: EventState | None, store: WatchStore) -> WatchEventFile | None:
    if event is None:
        return None
    try:
        return store.read_event(Path(event.event_file))
    except (OSError, ValueError):
        return None


def _summary(event: EventState | None, store: WatchStore) -> str:
    data = _event_file(event, store)
    if data is None:
        return "run ended"
    run = (data.status_document.get("issues") or [{}])[0].get("latest_run") or {}
    end = run.get("end_status") or run.get("termination")
    return f"run {data.run_id} ended {end} at step {data.failed_step or data.step}"


def _aborted_body(marker: str, event: EventState | None, store: WatchStore) -> str:
    data = _event_file(event, store)
    lines = [marker, "## kaji watch: run が停止しました", ""]
    if data is not None:
        run = (data.status_document.get("issues") or [{}])[0].get("latest_run") or {}
        cause = (data.failure or {}).get("cause")
        lines += [
            f"- run_id: `{data.run_id}`",
            f"- end_status: `{run.get('end_status') or run.get('termination')}`",
            f"- failed_step: `{data.failed_step}`",
            f"- failure.cause: `{cause}`",
            f"- event: `{data.key}`",
        ]
    return "\n".join(lines)
