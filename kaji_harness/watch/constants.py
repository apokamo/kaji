"""Fixed periods, thresholds and label names for ``kaji watch`` (Issue #543 A4)."""

from __future__ import annotations

#: 1 周の間隔（秒）。
POLL_INTERVAL_SECONDS = 180
#: step の pane が ``working`` 以外の周がこの回数続いたら ``pane_idle`` を検知する。
PANE_IDLE_POLLS = 2
#: 進み具合が変わらない時間がこれを超えたら ``log_stall`` を検知する（秒）。
LOG_STALL_SECONDS = 5400
#: 対象の Issue が 0 件の周がこの回数続いたら終了する。
EMPTY_POLLS_TO_EXIT = 2
#: ``still-working`` を記録した後、再確認を控える時間（秒）。
STILL_WORKING_SNOOZE_SECONDS = 1200
#: triage の起動の試行上限。
MAX_LAUNCH_ATTEMPTS = 3

#: 自動復旧の outcome → (上限を数える単位の項目, 上限)（Issue #545 D1。kamo2 ``AUTO_LIMITS``）。
#: 単位の項目は event ファイルの項目名（``issue`` / ``run_id`` / ``step`` / ``attempt`` / ``anchor``）。
AUTO_RECOVERY_LIMITS: dict[str, tuple[tuple[str, ...], int]] = {
    "continue-sent": (("issue", "run_id", "step", "attempt"), 3),
    "wait": (("issue", "run_id"), 3),
    "dialog-answered": (("issue", "run_id", "step", "attempt"), 3),
    "question-answered": (("issue", "run_id", "step", "attempt"), 3),
    "retry-sent": (("issue", "anchor"), 1),
}
#: ``wait`` の ``--until`` の上限（今から 7 日以内）。
WAIT_HORIZON_SECONDS = 7 * 24 * 3600
#: ``kaji watch retry`` が ``kaji recover`` を起動するタブの label の接頭辞。
RETRY_TAB_PREFIX = "kaji-retry-"

STATE_PLANNED = "state:planned"
STATE_RUNNING = "state:running"
STATE_ABORTED = "state:aborted"
STATE_DONE = "state:done"
STATE_HOLD = "state:hold"
STATE_NEEDS_MAINTAINER = "state:needs-maintainer"

#: 契約の 6 ラベル（D2）。
CONTRACT_LABELS: tuple[str, ...] = (
    STATE_PLANNED,
    STATE_RUNNING,
    STATE_ABORTED,
    STATE_DONE,
    STATE_HOLD,
    STATE_NEEDS_MAINTAINER,
)
#: ``kaji watch`` が付け外しするラベル。
MANAGED_LABELS: tuple[str, ...] = (STATE_PLANNED, STATE_RUNNING, STATE_ABORTED, STATE_DONE)
#: 人だけが付けてよいラベル（``kaji watch`` はこれが付いた Issue に触れない）。
USER_LABELS: tuple[str, ...] = (STATE_HOLD, STATE_NEEDS_MAINTAINER)
STATE_LABEL_PREFIX = "state:"
