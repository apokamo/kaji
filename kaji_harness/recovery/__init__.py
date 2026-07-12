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
from .incident import (
    INCIDENT_CAUSE_TRANSIENT,
    INCIDENT_LABEL,
    INCIDENT_STATUS_INVESTIGATING,
    OCCURRENCE_SCHEMA_VERSION,
    FuzzyCandidate,
    IncidentAction,
    IncidentCandidate,
    IncidentContext,
    IncidentOutcome,
    OccurrenceRecord,
    append_occurrence,
    backfill_run_ids,
    compute_fuzzy_candidates,
    execute_incident_action,
    parse_candidates,
    parse_identity_marker,
    parse_occurrence_markers,
    plan_incident_action,
    posted_run_ids,
    read_occurrences,
    render_identity_marker,
    render_incident_issue,
    render_occurrence_comment,
    render_occurrence_marker,
)
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
    recovery_budget_consumed,
    select_newer_run_ids,
    write_recovery_chain,
    write_recovery_json,
)
from .report import render_child_result_comment, render_stderr_summary, render_triage_comment
from .signature import (
    FINGERPRINT_LIMIT,
    SIGNATURE_SCHEMA_VERSION,
    SIMILARITY_THRESHOLD,
    IncidentSignature,
    compute_signature,
    normalize_error_text,
    similarity,
)
from .snapshot import FailureEvent, FailureSnapshot, GitStateSummary, collect_snapshot

__all__ = [
    "FAILURE_CAUSES",
    "FINGERPRINT_LIMIT",
    "INCIDENT_CAUSE_TRANSIENT",
    "INCIDENT_LABEL",
    "INCIDENT_STATUS_INVESTIGATING",
    "NON_RESUMABLE_STEPS",
    "OCCURRENCE_SCHEMA_VERSION",
    "RECOVERY_BUDGET",
    "RECOVERY_CHAIN_FILE",
    "RECOVERY_DECISIONS",
    "RECOVERY_FILE",
    "RECOVERY_SCHEMA_VERSION",
    "RECOVERY_WAIT_SECONDS",
    "SIGNATURE_SCHEMA_VERSION",
    "SIMILARITY_THRESHOLD",
    "FailureClassification",
    "FailureEvent",
    "FailureSnapshot",
    "FuzzyCandidate",
    "GitStateSummary",
    "IncidentAction",
    "IncidentCandidate",
    "IncidentContext",
    "IncidentOutcome",
    "IncidentSignature",
    "OccurrenceRecord",
    "RecoveryDecision",
    "RecoveryHandler",
    "RecoveryResult",
    "append_occurrence",
    "backfill_run_ids",
    "classify_failure",
    "collect_snapshot",
    "compute_fuzzy_candidates",
    "compute_signature",
    "derive_child_final_status",
    "execute_incident_action",
    "normalize_error_text",
    "parse_candidates",
    "parse_identity_marker",
    "parse_occurrence_markers",
    "plan_incident_action",
    "plan_recovery",
    "posted_run_ids",
    "read_occurrences",
    "read_recovery_chain",
    "read_recovery_json",
    "recovery_budget_consumed",
    "render_child_result_comment",
    "render_identity_marker",
    "render_incident_issue",
    "render_occurrence_comment",
    "render_occurrence_marker",
    "render_stderr_summary",
    "render_triage_comment",
    "similarity",
    "select_newer_run_ids",
    "write_recovery_chain",
    "write_recovery_json",
]
