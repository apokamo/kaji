"""Pure validation and bookkeeping for automatic recovery (Issue #545).

``/watch-triage`` が承認なしで行う 5 種類の自動復旧（D1）の検証・単位の計算・一覧の組み立て・
``kaji watch retry`` の子 run の判定を置く。I/O を持たない（checkout の一覧や時刻は引数で受け取る）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .constants import AUTO_RECOVERY_LIMITS, WAIT_HORIZON_SECONDS
from .models import WatchEventFile, WatchRecord

#: 単位の値（event の ``issue`` / ``run_id`` / ``step`` / ``attempt`` / ``anchor``）
UnitValue = str | int | None
RetryChild = Literal["running", "completed", "failed", "not_started", "unrelated"]

#: outcome → 使える event の ``kind``
AUTO_OUTCOME_KINDS: dict[str, frozenset[str]] = {
    "continue-sent": frozenset({"pane_idle"}),
    "wait": frozenset({"pane_idle", "aborted"}),
    "dialog-answered": frozenset({"pane_idle"}),
    "question-answered": frozenset({"pane_idle"}),
    "retry-sent": frozenset({"aborted"}),
}
#: ``classify_retry_child`` の 5 値（skill の起動の確認の表と契約テストで照合する）
RETRY_CHILD_STATES: tuple[RetryChild, ...] = (
    "running",
    "completed",
    "failed",
    "not_started",
    "unrelated",
)


class AutoRecoveryInputError(ValueError):
    """自動復旧の入力（event の kind・必須の引数）が検証に通らない。"""


def is_auto_outcome(outcome: str) -> bool:
    """自動復旧の outcome かを返す。"""
    return outcome in AUTO_RECOVERY_LIMITS


def validate_event_for(outcome: str, event: WatchEventFile, reason: str | None) -> None:
    """outcome と event の組み合わせ・``--reason`` を検証する。

    Raises:
        AutoRecoveryInputError: kind が合わない、単位の項目が空、``--reason`` が空の場合。
    """
    kinds = AUTO_OUTCOME_KINDS[outcome]
    if event.kind not in kinds:
        allowed = " / ".join(sorted(kinds))
        raise AutoRecoveryInputError(f"--outcome {outcome} is only valid for {allowed} events")
    if reason is None or not reason.strip():
        raise AutoRecoveryInputError(f"--outcome {outcome} requires a non-empty --reason")
    fields, _ = AUTO_RECOVERY_LIMITS[outcome]
    for name in fields:
        value = getattr(event, name)
        if value is None or value == "":
            raise AutoRecoveryInputError(f"--outcome {outcome} requires event field '{name}'")


def check_until(value: str | None, now: datetime) -> str:
    """``--until`` を検証し、UTC の ISO 8601 に正規化して返す。

    Raises:
        AutoRecoveryInputError: 未指定・時刻帯なし・今以前・7 日より先の場合。
    """
    if value is None or not value.strip():
        raise AutoRecoveryInputError("--outcome wait requires --until")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise AutoRecoveryInputError(f"--until is not ISO 8601: {value}") from exc
    if parsed.tzinfo is None:
        raise AutoRecoveryInputError(f"--until must include a time zone: {value}")
    if parsed <= now:
        raise AutoRecoveryInputError(f"--until must be in the future: {value}")
    if parsed > now + timedelta(seconds=WAIT_HORIZON_SECONDS):
        raise AutoRecoveryInputError(f"--until must be within 7 days: {value}")
    return parsed.astimezone(UTC).isoformat()


def check_paths(paths: Sequence[str] | None, checkouts: Iterable[Path]) -> list[str]:
    """``--path`` が repo の main checkout・worktree の内側かを検証し、解決後のパスを返す。

    Args:
        paths: ``--path`` の値。
        checkouts: main checkout と ``git worktree list --porcelain`` の worktree（絶対パス）。

    Raises:
        AutoRecoveryInputError: 未指定・相対パス・どの checkout の内側でもない場合。
    """
    if not paths:
        raise AutoRecoveryInputError("--outcome dialog-answered requires at least one --path")
    roots = [Path(root).resolve() for root in checkouts]
    resolved: list[str] = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            raise AutoRecoveryInputError(f"--path must be absolute: {value}")
        real = path.resolve()
        if not any(real == root or real.is_relative_to(root) for root in roots):
            raise AutoRecoveryInputError(f"--path is outside the repository checkouts: {value}")
        resolved.append(str(real))
    return resolved


def unit_of(event: WatchEventFile, outcome: str) -> list[UnitValue]:
    """上限を数える単位の値の列を返す（例 ``[101, "261115113853", "implement", 1]``）。"""
    fields, _ = AUTO_RECOVERY_LIMITS[outcome]
    values: list[UnitValue] = []
    for name in fields:
        value = getattr(event, name)
        values.append(value if isinstance(value, str | int) else None)
    return values


def count_in_unit(records: Iterable[WatchRecord], outcome: str, unit: Sequence[UnitValue]) -> int:
    """同じ outcome・同じ単位の記録の数を返す。"""
    target = list(unit)
    return sum(1 for r in records if r.outcome == outcome and r.unit == target)


def recovery_rows(records: Sequence[WatchRecord], issue: int | None) -> list[dict[str, Any]]:
    """``kaji watch recoveries`` の行を記録順に組み立てる（``count`` は同じ単位の通し番号）。

    Returns:
        1 行 1 dict の list（値は JSON の値）。
    """
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    for record in records:
        if not is_auto_outcome(record.outcome):
            continue
        unit = record.unit or []
        bucket = (record.outcome, repr(unit))
        seen[bucket] = seen.get(bucket, 0) + 1
        row_issue = unit[0] if unit and isinstance(unit[0], int) else _issue_of(record.event_key)
        if issue is not None and row_issue != issue:
            continue
        rows.append(
            {
                "recorded_at": record.recorded_at,
                "issue": row_issue,
                "event_key": record.event_key,
                "outcome": record.outcome,
                "unit": unit,
                "count": seen[bucket],
                "limit": AUTO_RECOVERY_LIMITS[record.outcome][1],
                "reason": record.reason,
                "until": record.until,
                "paths": record.paths,
                "judge": record.judge,
            }
        )
    return rows


def _issue_of(event_key: str) -> int | None:
    head = event_key.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def classify_retry_child(status_document: Mapping[str, Any], target_run_id: str) -> RetryChild:
    """``kaji status --json <issue>`` の文書から、``retry`` が起動した子 run の状態を判定する。

    自分の子は「``latest_run.run_id`` が対象の run と違い、``latest_run.recovery.parent_run_id``
    が対象の run と一致する run」。running の瞬間を観測できたかには依存しない。

    Args:
        status_document: ``kaji status --json`` の 1 Issue 分の文書。
        target_run_id: event の ``run_id``（やり直しの対象の run）。

    Returns:
        ``running`` / ``completed`` / ``failed`` / ``not_started`` / ``unrelated``。
        自分の子の ``termination`` が ``unknown`` / ``None`` なら ``running``、``vanished`` なら ``failed``。
    """
    run = status_document.get("latest_run")
    if not isinstance(run, Mapping):
        return "not_started"
    run_id = run.get("run_id")
    if run_id == target_run_id:
        return "not_started"
    recovery = run.get("recovery")
    parent = recovery.get("parent_run_id") if isinstance(recovery, Mapping) else None
    if parent != target_run_id:
        return "unrelated"
    termination = run.get("termination")
    if termination == "ended":
        return "completed" if run.get("end_status") == "COMPLETE" else "failed"
    if termination == "vanished":
        return "failed"
    # ``running`` と、生死をまだ判定できない ``unknown`` / ``None``（起動直後など）は、
    # 自分の子が存在する＝起動した、として扱う（終わっていれば監視が ``aborted`` として検知する）
    return "running"
