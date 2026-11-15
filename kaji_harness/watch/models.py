"""Persisted watch artifacts under ``<artifacts>/_watch/`` (Issue #543 A5).

状態ファイル・event ファイル・record は永続化して後で読み戻す構造なので Pydantic で検証する。
event ファイルは triage skill の入力にもなるため ``schema_version`` を持つ。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WATCH_SCHEMA_VERSION: Literal[1] = 1

EventKindName = Literal["pane_idle", "aborted", "log_stall"]
LaunchStatus = Literal["detected", "launching", "launched", "launch_failed"]
OutcomeName = Literal[
    "handoff",
    "still-working",
    "not-stalled",
    "recovered",
    "continue-sent",
    "wait",
    "dialog-answered",
    "question-answered",
    "retry-sent",
]
#: 自動復旧の outcome（Issue #545 D1）。
AutoOutcomeName = Literal[
    "continue-sent", "wait", "dialog-answered", "question-answered", "retry-sent"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepArtifactsModel(_Strict):
    """event の ``artifacts``（``runstate.query.locate_step_artifacts`` の結果）。"""

    run_dir: str | None = None
    attempt_dir: str | None = None


class WatchEventFile(_Strict):
    """event ファイル（schema v1）。検知時点の事実を固定し、以後は書き換えない。"""

    schema_version: Literal[1] = WATCH_SCHEMA_VERSION
    key: str
    kind: EventKindName
    issue: int = Field(gt=0)
    detected_at: str
    run_id: str | None = None
    workflow: str | None = None
    step: str | None = None
    attempt: int | None = None
    failed_step: str | None = None
    # 値は #540 schema v1 の JSON object（``failure`` / ``pane`` / 文書全体）
    failure: dict[str, Any] | None = None
    pane: dict[str, Any] | None = None
    artifacts: StepArtifactsModel = Field(default_factory=StepArtifactsModel)
    status_document: dict[str, Any] = Field(default_factory=dict)
    #: 検知時点の進み具合 ``[目標ラベル, run_id, step, attempt, episode]``（handoff の抑止用）
    progress: tuple[str | None, str | None, str | None, int | None, int] | None = None
    #: event を出した時点の anchor（``member:<run_id>`` / ``baseline:<run_id>`` / ``none``。#545）
    anchor: str | None = None
    #: ``wait`` の予定時刻を過ぎてから出し直した event のとき、その予定時刻（UTC。#545）
    waited_until: str | None = None


class EventState(_Strict):
    """event の起動状態（状態ファイル内）。"""

    key: str
    kind: EventKindName
    issue: int
    run_id: str | None = None
    detected_at: str
    event_file: str
    launch_status: LaunchStatus = "detected"
    launch_attempts: int = Field(default=0, ge=0)
    triage_tab: str | None = None
    triage_pane: str | None = None


class ReportState(_Strict):
    """``aborted`` の報告の結果。"""

    comment_url: str | None = None
    notified: bool = False


class PaneIdleState(_Strict):
    """step の pane の idle 回数。"""

    run_id: str
    step: str
    attempt: int | None = None
    idle_polls: int = Field(default=0, ge=0)
    episode: int = Field(default=0, ge=0)
    #: 反映済みの ``recovered`` の ``recorded_at``（#545 MF-5）
    recovered_seen_at: str | None = None


class StallState(_Strict):
    """進み具合の不変の開始時刻。"""

    signature: list[str | None]
    since: str


class WatchState(_Strict):
    """状態ファイル ``_watch/state.json``。

    Attributes:
        pending_labels: 付け替え途中の Issue → 目標（``None`` は managed のラベルを外す）。
        pending_reports: 報告待ちの Issue → event key。
        reports: event key → 報告の結果。
        baselines: series の外の Issue の ``baseline_run_id``（空文字は run なし）。
        reopen_baselines: open の ``state:done`` を初めて見た周の ``latest_run.run_id``。
        pane_idle: Issue → pane の idle 回数。
        stall: Issue → 進み具合の不変の開始時刻。
        empty_polls: 対象が 0 件の周が続いた回数。
        events: event key → 起動状態。
    """

    schema_version: Literal[1] = WATCH_SCHEMA_VERSION
    pending_labels: dict[str, str | None] = Field(default_factory=dict)
    pending_reports: dict[str, str] = Field(default_factory=dict)
    reports: dict[str, ReportState] = Field(default_factory=dict)
    baselines: dict[str, str] = Field(default_factory=dict)
    reopen_baselines: dict[str, str] = Field(default_factory=dict)
    pane_idle: dict[str, PaneIdleState] = Field(default_factory=dict)
    stall: dict[str, StallState] = Field(default_factory=dict)
    empty_polls: int = Field(default=0, ge=0)
    events: dict[str, EventState] = Field(default_factory=dict)


class WatchRecord(_Strict):
    """``records.jsonl`` の 1 行（``kaji watch record`` の結果）。"""

    event_key: str
    outcome: OutcomeName
    recorded_at: str
    comment_url: str | None = None
    summary: str | None = None
    judge: str
    #: 自動復旧の判断理由（#545）
    reason: str | None = None
    #: ``wait`` の予定時刻（#545）
    until: str | None = None
    #: ``dialog-answered`` で許可したパス（#545）
    paths: list[str] | None = None
    #: 上限を数える単位の値の列（#545。例 ``[101, "261115113853", "implement", 1]``）
    unit: list[str | int | None] | None = None
    #: ``retry-sent`` で ``kaji recover`` に渡した workflow YAML の絶対パス（#545）
    workflow_path: str | None = None
