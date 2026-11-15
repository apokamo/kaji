"""Pure target-label computation for ``kaji watch`` (Issue #543).

毎周、事実（GitHub の Issue 状態と #540 の ``IssueStatus``）から ``state:*`` の目標を計算する。
I/O を持たない純粋関数だけを置く。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from .constants import (
    EMPTY_POLLS_TO_EXIT,
    MANAGED_LABELS,
    STATE_ABORTED,
    STATE_DONE,
    STATE_PLANNED,
    STATE_RUNNING,
    USER_LABELS,
)

HoldReason = Literal["unknown_liveness", "user_label", "conflict"]
#: ``IssueStatus`` / ``RunStatus``（#540 schema v1）の JSON object。値は JSON の値。
JsonDict = dict[str, Any]

_FAILED_END_STATUSES = frozenset({"ABORT", "ERROR"})
_ACTIVE_LABELS = frozenset({STATE_PLANNED, STATE_RUNNING, STATE_ABORTED})


class TargetBasis(Enum):
    """目標の根拠。``FAILED_RUN`` だけが ``aborted`` の event の根拠になる。"""

    CLOSED_COMPLETED = "closed_completed"
    CLEAR = "clear"
    ALIVE = "alive"
    NO_RUN = "no_run"
    FAILED_RUN = "failed_run"
    COMPLETE_OPEN = "complete_open"


@dataclass(frozen=True)
class WatchIssue:
    """1 Issue の事実。

    Attributes:
        issue: Issue 番号。
        github_state: ``open`` / ``closed``。
        state_reason: close の理由（``completed`` 等。小文字に正規化済み）。
        state_labels: 今付いている ``state:*`` ラベル。
        status: ``runstate.query.collect_status`` の ``issues[]`` の要素。
    """

    issue: int
    github_state: str
    state_reason: str | None
    state_labels: tuple[str, ...]
    status: JsonDict

    @property
    def is_open(self) -> bool:
        """Issue が open かを返す。"""
        return self.github_state.lower() == "open"

    @property
    def latest_run(self) -> JsonDict | None:
        """``latest_run``（なければ ``None``）。"""
        run = self.status.get("latest_run")
        return run if isinstance(run, dict) else None

    @property
    def current_label(self) -> str | None:
        """``state:*`` が 1 個ならその名前、0 個なら ``None``、2 個以上なら ``"conflict"``。"""
        if not self.state_labels:
            return None
        if len(self.state_labels) > 1:
            return "conflict"
        return self.state_labels[0]


@dataclass(frozen=True)
class Anchor:
    """どの run から数えるか。

    Attributes:
        run_id: 基準の run_id。``None`` なら全 run を数える（``pending_member`` を除く）。
        inclusive: ``True`` なら ``run_id >= anchor``（series の member）、
            ``False`` なら ``run_id > anchor``（``baseline_run_id``）。
        pending_member: series の member が ``pending``（run_id なし）。run は 0 件として扱う。
    """

    run_id: str | None
    inclusive: bool
    pending_member: bool


@dataclass(frozen=True)
class Target:
    """1 Issue の目標。

    Attributes:
        label: 目標の ``state:*``。``None`` は「managed のラベルを外す」（``write`` のとき）
            または「目標なし」。
        write: ラベルを書き換える対象か。
        basis: 目標の根拠（書き換えない Issue は ``None``）。
        hold_reason: 書き換えない理由（``status`` に表示する）。
        run_id: 根拠の run（``FAILED_RUN`` / ``COMPLETE_OPEN`` / ``ALIVE``）。
    """

    label: str | None
    write: bool
    basis: TargetBasis | None = None
    hold_reason: HoldReason | None = None
    run_id: str | None = None


_NO_WRITE = Target(label=None, write=False)


def resolve_anchor(issue: int, series: Sequence[JsonDict], baselines: Mapping[str, str]) -> Anchor:
    """Issue の anchor を決める。

    Args:
        issue: Issue 番号。
        series: ``runstate.query.list_series`` の戻り値。
        baselines: 状態ファイルの ``baselines``（Issue → ``baseline_run_id``。空文字は run なし）。

    Returns:
        ``updated_at`` が最新の series の member があればその run_id（含む）、なければ
        ``baseline_run_id``（含まない）。
    """
    best: tuple[str, JsonDict] | None = None
    for entry in series:
        members = entry.get("members")
        if not isinstance(members, list):
            continue
        for member in members:
            if isinstance(member, dict) and member.get("issue") == issue:
                updated = entry.get("updated_at")
                key = updated if isinstance(updated, str) else ""
                if best is None or key > best[0]:
                    best = (key, member)
    if best is not None:
        run_id = best[1].get("run_id")
        if isinstance(run_id, str) and run_id:
            return Anchor(run_id=run_id, inclusive=True, pending_member=False)
        return Anchor(run_id=None, inclusive=True, pending_member=True)
    baseline = baselines.get(str(issue))
    if baseline:
        return Anchor(run_id=baseline, inclusive=False, pending_member=False)
    return Anchor(run_id=None, inclusive=False, pending_member=False)


def _run_after_anchor(run: JsonDict | None, anchor: Anchor) -> JsonDict | None:
    if run is None or anchor.pending_member:
        return None
    run_id = run.get("run_id")
    if not isinstance(run_id, str):
        return None
    if anchor.run_id is None:
        return run
    if anchor.inclusive:
        return run if run_id >= anchor.run_id else None
    return run if run_id > anchor.run_id else None


def _hold(reason: HoldReason) -> Target:
    return Target(label=None, write=False, hold_reason=reason)


def compute_target(
    issue: WatchIssue,
    anchor: Anchor,
    reopen_baseline: str | None = None,
    *,
    pending: bool = False,
) -> Target:
    """方針の表に従い、Issue の目標ラベルを計算する。

    Args:
        issue: Issue の事実。
        anchor: ``resolve_anchor`` の値。
        reopen_baseline: open の ``state:done`` を初めて見た周の ``latest_run.run_id``
            （状態ファイルの ``reopen_baselines``。空文字は run なし）。
        pending: 自分の ``pending_labels`` にある Issue か（付け替え途中の 2 個を直す）。

    Returns:
        目標。書き換えない Issue は ``write=False``。
    """
    labels = issue.state_labels
    if any(label in USER_LABELS for label in labels):
        return _hold("user_label")
    managed = [label for label in labels if label in MANAGED_LABELS]
    if len(labels) != len(managed):
        return _hold("conflict")
    if len(managed) >= 2 and not pending:
        return _hold("conflict")
    if not managed and not pending:
        return _NO_WRITE

    if not issue.is_open:
        if (issue.state_reason or "").lower() == "completed":
            return Target(label=STATE_DONE, write=True, basis=TargetBasis.CLOSED_COMPLETED)
        return Target(label=None, write=True, basis=TargetBasis.CLEAR)

    status = issue.status
    alive = status.get("any_run_alive")
    launching = status.get("launching")
    run = issue.latest_run

    if managed == [STATE_DONE] and not pending:
        newer = False
        if run is not None and reopen_baseline is not None:
            run_id = run.get("run_id")
            newer = isinstance(run_id, str) and run_id > reopen_baseline
        if not (alive is True or launching is True or newer):
            return _NO_WRITE
        anchor = Anchor(run_id=reopen_baseline or None, inclusive=False, pending_member=False)

    if alive is True or launching is True:
        run_id = run.get("run_id") if run is not None else None
        return Target(
            label=STATE_RUNNING,
            write=True,
            basis=TargetBasis.ALIVE,
            run_id=run_id if isinstance(run_id, str) else None,
        )
    if alive is None:
        return _hold("unknown_liveness")

    counted = _run_after_anchor(run, anchor)
    if counted is None:
        return Target(label=STATE_PLANNED, write=True, basis=TargetBasis.NO_RUN)
    run_id = counted.get("run_id")
    termination = counted.get("termination")
    end_status = counted.get("end_status")
    if (termination == "ended" and end_status in _FAILED_END_STATUSES) or (
        termination == "vanished"
    ):
        return Target(
            label=STATE_ABORTED, write=True, basis=TargetBasis.FAILED_RUN, run_id=str(run_id)
        )
    if termination == "ended" and end_status == "COMPLETE":
        return Target(
            label=STATE_ABORTED, write=True, basis=TargetBasis.COMPLETE_OPEN, run_id=str(run_id)
        )
    return _hold("unknown_liveness")


def count_active(issues: Sequence[WatchIssue], targets: Mapping[int, Target]) -> int:
    """終了判定の件数（open で、今のラベルまたは目標が planned / running / aborted）を返す。"""
    count = 0
    for issue in issues:
        if not issue.is_open:
            continue
        target = targets.get(issue.issue)
        target_label = target.label if target is not None else None
        if any(label in _ACTIVE_LABELS for label in issue.state_labels) or (
            target_label in _ACTIVE_LABELS
        ):
            count += 1
    return count


def should_exit(*, empty_polls: int, has_pending: bool) -> bool:
    """0 件の周が規定回数続き、未処理の記録がなければ終了する。"""
    return empty_polls >= EMPTY_POLLS_TO_EXIT and not has_pending
