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
