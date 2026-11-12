"""``kaji status --json`` schema v1 (Issue #540).

``schema_version`` は整数。任意の項目の追加では上げず（利用側は未知の項目を無視する）、
項目の削除・名前や型の変更・null の意味の変更・enum の値の追加と変更で上げる（D2）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

from pydantic import TypeAdapter, ValidationError

from ..series.state import MemberState, SeriesState
from .facts import IssueFacts, ReadError, RunFacts

STATUS_SCHEMA_VERSION = 1

#: schema に固定した enum。``MemberState`` / ``SeriesState`` の ``Literal`` と一致することを
#: テストで照合する（値の追加・変更は ``schema_version`` を上げる）。
MEMBER_STATUSES: tuple[str, ...] = ("pending", "running", "interrupted", "completed", "failed")
SERIES_STATUSES: tuple[str, ...] = ("running", "stopped", "completed")
TERMINATIONS: tuple[str, ...] = ("running", "ended", "vanished", "unknown")

# 値は JSON の値（str / int / bool / None / list / dict）
JsonDict = dict[str, Any]


def read_error_to_json(error: ReadError) -> JsonDict:
    """``ReadError`` を JSON object に変換する。"""
    return {"path": error.path, "error": error.error, "message": error.message}


def run_to_json(run: RunFacts) -> JsonDict:
    """``RunFacts`` を ``RunStatus`` に変換する。"""
    last_step = run.last_step
    failure = run.failure
    return {
        "run_id": run.run_id,
        "workflow": run.workflow,
        "started_at": run.started_at,
        "process": {"pid": run.pid, "alive": run.liveness.alive, "basis": run.liveness.basis},
        "termination": run.termination,
        "end_status": run.end_status if run.termination == "ended" else None,
        "ended_at": run.ended_at if run.termination == "ended" else None,
        "last_step": (
            None
            if last_step is None
            else {
                "id": last_step.id,
                "attempt": last_step.attempt,
                "agent": last_step.agent,
                "session_id": last_step.session_id,
            }
        ),
        "failed_step": run.failed_step,
        "failure": (
            None
            if failure is None
            else {
                "cause": failure.cause,
                "source": failure.source,
                "recoverability_hint": failure.recoverability_hint,
            }
        ),
        "worktree_dir": run.worktree_dir,
        "branch_name": run.branch_name,
        "pane": (
            None
            if run.pane is None
            else {
                "backend": run.pane.backend,
                "pane_id": run.pane.pane_id,
                "origin_pane": run.pane.origin_pane,
            }
        ),
        "recovery": (
            None
            if run.recovery is None
            else {
                "root_run_id": run.recovery.root_run_id,
                "parent_run_id": run.recovery.parent_run_id,
                "chain_index": run.recovery.chain_index,
            }
        ),
    }


def issue_to_json(facts: IssueFacts) -> JsonDict:
    """``IssueFacts`` を ``IssueStatus`` に変換する。"""
    errors = list(facts.read_errors)
    if facts.latest_run is not None:
        errors = list(facts.latest_run.read_errors) + errors
    return {
        "issue": facts.issue_id,
        "latest_run": run_to_json(facts.latest_run) if facts.latest_run is not None else None,
        "any_run_alive": facts.any_run_alive,
        "launching": facts.launching,
        "read_errors": [read_error_to_json(error) for error in _dedupe(errors)],
    }


def _dedupe(errors: list[ReadError]) -> list[ReadError]:
    seen: set[tuple[str, str]] = set()
    result: list[ReadError] = []
    for error in errors:
        key = (error.path, error.error)
        if key in seen:
            continue
        seen.add(key)
        result.append(error)
    return result


_MEMBER_FIELDS: dict[str, Any] = {
    name: MemberState.model_fields[name].annotation
    for name in (
        "issue",
        "workflow",
        "status",
        "child_pid",
        "run_id",
        "exit_code",
        "gate",
        "started_at",
        "finished_at",
    )
}


def _validate_field(annotation: Any, value: Any) -> tuple[Any, bool]:
    try:
        return TypeAdapter(annotation).validate_python(value, strict=True), True
    except ValidationError:
        return None, False


def series_to_json(artifacts_dir: Path, series_id: str) -> tuple[JsonDict, list[int]]:
    """``_series/<id>/state.json`` をフィールド単位で検証して ``series`` object にする。

    Returns:
        ``(series object, member の Issue 番号の列)``。Issue 番号は ``issue`` が正の int の
        member だけ（``issues`` の出力順）。
    """
    relative = f"_series/{series_id}/state.json"
    path = artifacts_dir / relative
    result: JsonDict = {
        "id": series_id,
        "status": None,
        "stop_reason": None,
        "updated_at": None,
        "members": [],
        "read_errors": [],
    }
    errors: list[JsonDict] = result["read_errors"]
    if not path.is_file():
        errors.append({"path": relative, "error": "missing", "message": "state.json not found"})
        return result, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        errors.append({"path": relative, "error": "unreadable", "message": str(exc)})
        return result, []
    except json.JSONDecodeError as exc:
        errors.append({"path": relative, "error": "invalid", "message": str(exc)})
        return result, []
    if not isinstance(data, dict):
        errors.append(
            {"path": relative, "error": "invalid", "message": "top-level is not an object"}
        )
        return result, []

    for name, annotation in (
        ("status", SeriesState.model_fields["status"].annotation),
        ("stop_reason", str | None),
        ("updated_at", str | None),
    ):
        value, ok = _validate_field(annotation, data.get(name))
        if ok:
            result[name] = value
        else:
            errors.append(
                {"path": f"{relative}#{name}", "error": "invalid", "message": "invalid value"}
            )

    members = data.get("members")
    if not isinstance(members, list):
        errors.append(
            {"path": f"{relative}#members", "error": "invalid", "message": "not an array"}
        )
        return result, []
    issues: list[int] = []
    for index, raw in enumerate(members):
        member: JsonDict = dict.fromkeys(_MEMBER_FIELDS)
        if not isinstance(raw, dict):
            errors.append(
                {
                    "path": f"{relative}#members[{index}]",
                    "error": "invalid",
                    "message": "not an object",
                }
            )
            result["members"].append(member)
            continue
        for name, annotation in _MEMBER_FIELDS.items():
            value, ok = _validate_field(annotation, raw.get(name))
            if ok and not (name == "issue" and (value is None or value <= 0)):
                member[name] = value
            elif raw.get(name) is not None or name in ("issue", "workflow", "status"):
                errors.append(
                    {
                        "path": f"{relative}#members[{index}].{name}",
                        "error": "invalid",
                        "message": "invalid value",
                    }
                )
        if isinstance(member["issue"], int):
            issues.append(member["issue"])
        result["members"].append(member)
    return result, issues


def status_document(
    issues: list[JsonDict], *, series: JsonDict | None = None, now: datetime | None = None
) -> JsonDict:
    """top-level の JSON document を組み立てる。"""
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "series": series,
        "issues": issues,
    }


def literal_values(annotation: Any) -> tuple[str, ...]:
    """``Literal[...]`` の値を返す（schema enum と実装の照合用）。"""
    return tuple(str(value) for value in get_args(annotation))
