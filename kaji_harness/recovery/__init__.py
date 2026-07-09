"""Failure triage / recovery handler package (Issue #288).

`kaji run` が ``ERROR`` / triage 対象の ``ABORT`` で終了したときに、run artifact を
根拠に原因を機械分類し、Issue コメント・``recovery.json``・``run.log``・stderr サマリへ
証跡を固定する。whitelist 条件を満たす場合のみ、**1 recovery chain につき 1 回だけ**
固定 10 分ウェイト後に child run を自動起動する。

layering:

- ``models``: 直列化対象の data class と定数（budget / wait / denylist）
- ``snapshot``: run artifact / state / git state の収集（I/O 境界）
- ``classify``: cause 軸の分類（純関数）
- ``report``: Issue コメント / stderr サマリの生成（純関数）
- ``handler``: decision planner（純関数）と orchestrator
"""

from __future__ import annotations

from .classify import classify_failure
from .handler import RecoveryHandler, RecoveryResult, plan_recovery
from .models import (
    FAILURE_CAUSES,
    NON_RESUMABLE_STEPS,
    RECOVERY_BUDGET,
    RECOVERY_CHAIN_FILE,
    RECOVERY_DECISIONS,
    RECOVERY_FILE,
    RECOVERY_SCHEMA_VERSION,
    RECOVERY_WAIT_SECONDS,
    FailureClassification,
    RecoveryDecision,
    derive_child_final_status,
    read_recovery_chain,
    read_recovery_json,
    select_newer_run_ids,
    write_recovery_chain,
    write_recovery_json,
)
from .report import render_stderr_summary, render_triage_comment
from .snapshot import FailureEvent, FailureSnapshot, GitStateSummary, collect_snapshot

__all__ = [
    "FAILURE_CAUSES",
    "NON_RESUMABLE_STEPS",
    "RECOVERY_BUDGET",
    "RECOVERY_CHAIN_FILE",
    "RECOVERY_DECISIONS",
    "RECOVERY_FILE",
    "RECOVERY_SCHEMA_VERSION",
    "RECOVERY_WAIT_SECONDS",
    "FailureClassification",
    "FailureEvent",
    "FailureSnapshot",
    "GitStateSummary",
    "RecoveryDecision",
    "RecoveryHandler",
    "RecoveryResult",
    "classify_failure",
    "collect_snapshot",
    "derive_child_final_status",
    "plan_recovery",
    "read_recovery_chain",
    "read_recovery_json",
    "render_stderr_summary",
    "render_triage_comment",
    "select_newer_run_ids",
    "write_recovery_chain",
    "write_recovery_json",
]
