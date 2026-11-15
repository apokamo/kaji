"""Pure event detection for ``kaji watch`` (Issue #543).

検知はラベルの遷移ではなく事実と ledger（event と record の記録）から行う（M5）。I/O を
持たない純粋関数だけを置き、周をまたぐ追跡状態（pane の idle 回数・進み具合の不変時間）は
引数で受け取って新しい値を返す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from .constants import (
    LOG_STALL_SECONDS,
    PANE_IDLE_POLLS,
    STATE_ABORTED,
    STATE_RUNNING,
    STILL_WORKING_SNOOZE_SECONDS,
)
from .labels import Target, TargetBasis, WatchIssue
from .models import OutcomeName

EventKind = Literal["pane_idle", "aborted", "log_stall"]
Outcome = OutcomeName
#: 進み具合: (目標ラベル, run_id, step, attempt, episode)。handoff の抑止に使う。
Progress = tuple[str | None, str | None, str | None, int | None, int]


@dataclass(frozen=True)
class DetectedEvent:
    """検知した event（保存前）。

    Attributes:
        key: event key（ledger の一意キー）。
        kind: event の種類。
        issue: Issue 番号。
        run_id: 対象の run。
        step: ``latest_run.last_step.id``。
        attempt: ``latest_run.last_step.attempt``。
        progress: 検知時点の進み具合。
    """

    key: str
    kind: EventKind
    issue: int
    run_id: str | None
    step: str | None
    attempt: int | None
    progress: Progress
    #: ``wait`` の予定時刻を過ぎて出し直した event のとき、その予定時刻（#545）
    waited_until: datetime | None = None


@dataclass(frozen=True)
class RecordEntry:
    """ledger の record（``records.jsonl`` の 1 行と、その event の進み具合）。

    Attributes:
        event_key: 対象の event key。
        issue: Issue 番号。
        outcome: 判断の結果。
        recorded_at: 記録時刻。
        progress: 対象 event の進み具合（event ファイルが読めなければ ``None``）。
        until: ``wait`` の予定時刻（#545。それ以外は ``None``）。
    """

    event_key: str
    issue: int
    outcome: Outcome
    recorded_at: datetime
    progress: Progress | None
    until: datetime | None = None


@dataclass(frozen=True)
class Ledger:
    """既知の event と record。

    Attributes:
        event_keys: 保存済みの event key。
        records: 記録順の record。
    """

    event_keys: frozenset[str] = frozenset()
    records: tuple[RecordEntry, ...] = ()

    def has(self, key: str) -> bool:
        """key が events または records にあるかを返す。"""
        return key in self.event_keys or any(r.event_key == key for r in self.records)

    def last_record(self, key: str) -> RecordEntry | None:
        """key の最新の record を返す。"""
        found = [r for r in self.records if r.event_key == key]
        return found[-1] if found else None

    def handoff_progress(self, issue: int) -> Progress | None:
        """Issue の最新の record が ``handoff`` なら、その event の進み具合を返す。"""
        found = [r for r in self.records if r.issue == issue and r.outcome != "recovered"]
        recovered = [r for r in self.records if r.issue == issue and r.outcome == "recovered"]
        if not found:
            return None
        last = found[-1]
        if last.outcome != "handoff":
            return None
        if recovered and recovered[-1].recorded_at >= last.recorded_at:
            return None
        return last.progress


@dataclass(frozen=True)
class PaneIdleCounter:
    """step の pane の idle 回数（同じ ``(run_id, step, attempt)`` の間だけ数える）。"""

    run_id: str
    step: str
    attempt: int | None
    idle_polls: int = 0
    episode: int = 0
    #: 反映済みの ``recovered`` の ``recorded_at``（#545 MF-5。同じ記録を 2 度反映しない）
    recovered_seen_at: datetime | None = None


@dataclass(frozen=True)
class StallTracker:
    """進み具合の不変の開始時刻。"""

    signature: tuple[str | None, ...]
    since: datetime


@dataclass(frozen=True)
class DetectTracking:
    """周をまたぐ検知の追跡状態。"""

    pane_idle: Mapping[int, PaneIdleCounter] = field(default_factory=dict)
    stall: Mapping[int, StallTracker] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectResult:
    """``detect_events`` の戻り値。"""

    events: list[DetectedEvent]
    tracking: DetectTracking
    suppressed: frozenset[int] = frozenset()
    #: ``wait`` の予定時刻の前の Issue → 予定時刻（#545。``aborted`` の triage の起動を遅らせる）
    waiting: Mapping[int, datetime] = field(default_factory=dict)


def _step_of(issue: WatchIssue) -> tuple[str | None, str | None, int | None]:
    run = issue.latest_run
    if run is None:
        return None, None, None
    run_id = run.get("run_id")
    last_step = run.get("last_step")
    step = last_step.get("id") if isinstance(last_step, dict) else None
    attempt = last_step.get("attempt") if isinstance(last_step, dict) else None
    return (
        run_id if isinstance(run_id, str) else None,
        step if isinstance(step, str) else None,
        attempt if isinstance(attempt, int) else None,
    )


def _next_rerun_key(
    base: str, ledger: Ledger, now: datetime, outcome: Outcome, wait_seconds: int, prefix: str
) -> str | None:
    """再確認の key 列（``base``, ``base<prefix>1``, ...）から、次に出すべき key を返す。"""
    keys = [base]
    while ledger.has(keys[-1]):
        keys.append(f"{base}{prefix}{len(keys)}")
    if len(keys) == 1:
        return base
    previous = ledger.last_record(keys[-2])
    if previous is None or previous.outcome != outcome:
        return None
    if now < previous.recorded_at + timedelta(seconds=wait_seconds):
        return None
    return keys[-1]


def detect_events(
    issues: Sequence[WatchIssue],
    targets: Mapping[int, Target],
    pane_status: Mapping[int, str | None],
    tracking: DetectTracking,
    ledger: Ledger,
    now: datetime,
    *,
    open_triage: frozenset[int] = frozenset(),
) -> DetectResult:
    """未処理の event を検知する。

    Args:
        issues: 対象の Issue の事実。
        targets: ``compute_target`` の結果（Issue → 目標）。
        pane_status: step の pane の ``agent_status``（観測できなければ ``None``）。
        tracking: 前の周の追跡状態。
        ledger: 既知の event と record。
        now: 現在時刻（tz 付き）。
        open_triage: triage タブが開いている Issue。``pane_idle`` / ``log_stall`` の
            event は出さない（調査中の重複を避ける）。``aborted`` は M5 の記録と報告の
            ために検知を続け、triage の起動だけを抑止する（抑止 1 は起動側で適用）。

    Returns:
        新しい event、更新した追跡状態、handoff の抑止中の Issue。
    """
    events: list[DetectedEvent] = []
    suppressed: set[int] = set()
    waiting: dict[int, datetime] = {}
    pane_idle: dict[int, PaneIdleCounter] = {}
    stall: dict[int, StallTracker] = {}

    for issue in issues:
        number = issue.issue
        target = targets.get(number)
        run = issue.latest_run
        run_id, step, attempt = _step_of(issue)
        termination = run.get("termination") if run is not None else None
        running = issue.is_open and termination == "running" and run_id is not None

        # pane の idle 回数（headless の run は pane が null で対象外。D4）
        counter: PaneIdleCounter | None = None
        agent_status = pane_status.get(number)
        has_pane = run is not None and run.get("pane") is not None
        if running and has_pane and step is not None and run_id is not None:
            previous = tracking.pane_idle.get(number)
            if previous is None or (previous.run_id, previous.step, previous.attempt) != (
                run_id,
                step,
                attempt,
            ):
                previous = PaneIdleCounter(run_id=run_id, step=step, attempt=attempt)
            previous = _apply_recovered(previous, number, ledger)
            if agent_status is None:
                counter = previous
            elif agent_status == "working":
                episode = previous.episode + (1 if previous.idle_polls > 0 else 0)
                counter = replace(previous, idle_polls=0, episode=episode)
            else:
                counter = replace(previous, idle_polls=previous.idle_polls + 1)
            pane_idle[number] = counter

        # 進み具合の不変時間
        tracker: StallTracker | None = None
        if running:
            signature: tuple[str | None, ...] = (
                run_id,
                step,
                str(attempt) if attempt is not None else None,
                termination if isinstance(termination, str) else None,
            )
            previous_stall = tracking.stall.get(number)
            if previous_stall is None or previous_stall.signature != signature:
                tracker = StallTracker(signature=signature, since=now)
            else:
                tracker = previous_stall
            stall[number] = tracker

        episode = counter.episode if counter is not None else 0
        progress: Progress = (
            target.label if target is not None and target.write else None,
            run_id,
            step,
            attempt,
            episode,
        )
        handoff = ledger.handoff_progress(number)
        if _handoff_matches(handoff, target, progress, running=running):
            suppressed.add(number)
        tab_open = number in open_triage
        current_run = target.run_id if target is not None and _failed(target) else run_id
        wait_episode = counter.episode if counter is not None else None
        active_wait = _active_wait(number, current_run, wait_episode, ledger, now)
        if active_wait is not None:
            waiting[number] = active_wait

        candidates: list[DetectedEvent] = []
        if (
            target is not None
            and target.basis is TargetBasis.FAILED_RUN
            and target.run_id is not None
        ):
            key = f"{number}:aborted:{target.run_id}"
            if not ledger.has(key):
                candidates.append(
                    DetectedEvent(
                        key=key,
                        kind="aborted",
                        issue=number,
                        run_id=target.run_id,
                        step=step,
                        attempt=attempt,
                        progress=(STATE_ABORTED, target.run_id, step, attempt, 0),
                    )
                )
        if active_wait is None:
            candidates.extend(_waited_events(number, current_run, wait_episode, ledger, now))
        quiet = tab_open or active_wait is not None
        if not quiet and counter is not None and counter.idle_polls >= PANE_IDLE_POLLS:
            base = f"{number}:pane_idle:{run_id}:{step}:{attempt}:{counter.episode}"
            key_or_none = _next_rerun_key(
                base, ledger, now, "still-working", STILL_WORKING_SNOOZE_SECONDS, ":r"
            )
            if key_or_none is not None:
                candidates.append(
                    DetectedEvent(
                        key=key_or_none,
                        kind="pane_idle",
                        issue=number,
                        run_id=run_id,
                        step=step,
                        attempt=attempt,
                        progress=(STATE_RUNNING, run_id, step, attempt, counter.episode),
                    )
                )
        if (
            not quiet
            and tracker is not None
            and now - tracker.since >= timedelta(seconds=LOG_STALL_SECONDS)
        ):
            key_or_none = _next_stall_key(number, run_id, step, attempt, ledger, now)
            if key_or_none is not None:
                candidates.append(
                    DetectedEvent(
                        key=key_or_none,
                        kind="log_stall",
                        issue=number,
                        run_id=run_id,
                        step=step,
                        attempt=attempt,
                        progress=(STATE_RUNNING, run_id, step, attempt, episode),
                    )
                )
        for candidate in candidates:
            if handoff is not None and handoff == _handoff_view(candidate, progress):
                continue
            events.append(candidate)

    return DetectResult(
        events=events,
        tracking=DetectTracking(pane_idle=pane_idle, stall=stall),
        suppressed=frozenset(suppressed),
        waiting=waiting,
    )


def _failed(target: Target | None) -> bool:
    return (
        target is not None and target.basis is TargetBasis.FAILED_RUN and target.run_id is not None
    )


def _key_parts(key: str) -> list[str]:
    """event key を ``:`` で分ける（``<issue>:<kind>:<run_id>:<step>:<attempt>:<episode>...``）。"""
    return key.split(":")


def _root_key(key: str) -> str:
    """再確認・出し直しの接尾辞（``:w<n>``）を外した key。"""
    parts = _key_parts(key)
    while len(parts) > 3 and parts[-1][:1] == "w" and parts[-1][1:].isdigit():
        parts.pop()
    return ":".join(parts)


def _wait_applies(key: str, current_run: str | None, episode: int | None) -> bool:
    """``wait`` の記録の event が今の run（と pane の episode）のものかを返す（kamo2 ``prune_waits``）。"""
    parts = _key_parts(key)
    if len(parts) < 3 or current_run is None or parts[2] != current_run:
        return False
    if episode is not None and parts[1] == "pane_idle" and len(parts) >= 6 and parts[5].isdigit():
        return int(parts[5]) == episode
    return True


def _latest_waits(issue: int, ledger: Ledger) -> dict[str, RecordEntry]:
    """出し直しの系列（``_root_key``）ごとに、最新の ``wait`` の記録を返す（key → 記録）。"""
    latest: dict[str, RecordEntry] = {}
    for record in ledger.records:
        if record.issue == issue and record.outcome == "wait" and record.until is not None:
            latest[_root_key(record.event_key)] = record
    return {record.event_key: record for record in latest.values()}


def _active_wait(
    issue: int, current_run: str | None, episode: int | None, ledger: Ledger, now: datetime
) -> datetime | None:
    """予定時刻の前の ``wait`` があれば、その予定時刻を返す（#545）。"""
    found: datetime | None = None
    for key, record in _latest_waits(issue, ledger).items():
        last = ledger.last_record(key)
        if last is None or last.outcome != "wait" or record.until is None:
            continue
        if record.until > now and _wait_applies(key, current_run, episode):
            found = record.until if found is None else max(found, record.until)
    return found


def _waited_events(
    issue: int, current_run: str | None, episode: int | None, ledger: Ledger, now: datetime
) -> list[DetectedEvent]:
    """予定時刻を過ぎた ``wait`` の event を ``<key>:w<n>`` で出し直す（#545）。"""
    events: list[DetectedEvent] = []
    for key, record in _latest_waits(issue, ledger).items():
        last = ledger.last_record(key)
        if last is None or last.outcome != "wait" or record.until is None:
            continue
        if record.until > now or not _wait_applies(key, current_run, episode):
            continue
        root = _root_key(key)
        count = sum(
            1
            for r in ledger.records
            if r.outcome == "wait" and (r.event_key == root or r.event_key.startswith(f"{root}:w"))
        )
        new_key = f"{root}:w{count}"
        if ledger.has(new_key):
            continue
        parts = _key_parts(root)
        kind = parts[1]
        if kind not in ("pane_idle", "aborted"):
            continue
        progress = record.progress or (None, current_run, None, None, 0)
        events.append(
            DetectedEvent(
                key=new_key,
                kind="pane_idle" if kind == "pane_idle" else "aborted",
                issue=issue,
                run_id=current_run,
                step=progress[2],
                attempt=progress[3],
                progress=progress,
                waited_until=record.until,
            )
        )
    return events


def _apply_recovered(counter: PaneIdleCounter, issue: int, ledger: Ledger) -> PaneIdleCounter:
    """周の間に記録された ``recovered`` を ``working`` の観測と同じに扱う（#545 MF-5）。

    同じ Issue・run・step・attempt の ``pane_idle`` の event に、``recovered_seen_at`` より新しい
    ``recovered`` があれば episode を進めて ``idle_polls`` を 0 に戻す。1 件につき 1 回だけ反映する。
    """
    prefix = f"{issue}:pane_idle:{counter.run_id}:{counter.step}:{counter.attempt}:"
    newest: datetime | None = None
    for record in ledger.records:
        if record.outcome != "recovered" or not record.event_key.startswith(prefix):
            continue
        if (
            counter.recovered_seen_at is not None
            and record.recorded_at <= counter.recovered_seen_at
        ):
            continue
        newest = record.recorded_at if newest is None else max(newest, record.recorded_at)
    if newest is None:
        return counter
    episode = counter.episode + (1 if counter.idle_polls > 0 else 0)
    return replace(counter, idle_polls=0, episode=episode, recovered_seen_at=newest)


def _handoff_matches(
    handoff: Progress | None, target: Target | None, progress: Progress, *, running: bool
) -> bool:
    """今成立しうる候補が handoff で抑止されるか（候補の抑止と同じ比較。``status`` 用）。

    run の実行中は ``pane_idle`` / ``log_stall`` の比較、失敗した run が目標の根拠なら
    ``aborted`` の比較を使う。どちらでもなければ抑止する候補がないので ``False``。
    """
    if handoff is None:
        return False
    if running and handoff == (STATE_RUNNING, progress[1], progress[2], progress[3], progress[4]):
        return True
    if target is not None and target.basis is TargetBasis.FAILED_RUN and target.run_id is not None:
        return handoff == (STATE_ABORTED, target.run_id, progress[2], progress[3], 0)
    return False


def _handoff_view(candidate: DetectedEvent, progress: Progress) -> Progress:
    """handoff の抑止で比べる現在の進み具合（aborted は目標を aborted として扱う）。"""
    if candidate.kind == "aborted":
        return candidate.progress
    return (STATE_RUNNING, progress[1], progress[2], progress[3], progress[4])


def _next_stall_key(
    issue: int,
    run_id: str | None,
    step: str | None,
    attempt: int | None,
    ledger: Ledger,
    now: datetime,
) -> str | None:
    """``log_stall`` の key（``recheck`` を 0 から増やす）を返す。"""
    prefix = f"{issue}:log_stall:{run_id}:{step}:{attempt}:"
    recheck = 0
    while ledger.has(f"{prefix}{recheck}"):
        recheck += 1
    if recheck == 0:
        return f"{prefix}0"
    previous = ledger.last_record(f"{prefix}{recheck - 1}")
    if previous is None or previous.outcome != "not-stalled":
        return None
    if now < previous.recorded_at + timedelta(seconds=LOG_STALL_SECONDS):
        return None
    return f"{prefix}{recheck}"
