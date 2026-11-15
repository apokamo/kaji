"""Small tests: automatic recovery helpers (Issue #545)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaji_harness.watch.constants import AUTO_RECOVERY_LIMITS
from kaji_harness.watch.models import WatchEventFile, WatchRecord
from kaji_harness.watch.notify import build_payload
from kaji_harness.watch.recovery import (
    RETRY_CHILD_STATES,
    AutoRecoveryInputError,
    check_paths,
    check_until,
    classify_retry_child,
    recovery_rows,
    unit_of,
    validate_event_for,
)
from kaji_harness.watch.workflows import WorkflowResolutionError, resolve_workflow_name

pytestmark = pytest.mark.small

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)


def _event(kind: str = "pane_idle", **overrides: object) -> WatchEventFile:
    data: dict[str, object] = {
        "key": f"101:{kind}:261115000000:implement:1:0",
        "kind": kind,
        "issue": 101,
        "detected_at": NOW.isoformat(),
        "run_id": "261115000000",
        "workflow": "dev",
        "step": "implement",
        "attempt": 1,
        "anchor": "member:261115000000",
    }
    data.update(overrides)
    return WatchEventFile.model_validate(data)


def test_limits_values() -> None:
    assert AUTO_RECOVERY_LIMITS == {
        "continue-sent": (("issue", "run_id", "step", "attempt"), 3),
        "wait": (("issue", "run_id"), 3),
        "dialog-answered": (("issue", "run_id", "step", "attempt"), 3),
        "question-answered": (("issue", "run_id", "step", "attempt"), 3),
        "retry-sent": (("issue", "anchor"), 1),
    }


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        ("continue-sent", "aborted"),
        ("dialog-answered", "log_stall"),
        ("question-answered", "aborted"),
        ("wait", "log_stall"),
        ("retry-sent", "pane_idle"),
    ],
)
def test_kind_mismatch_rejected(outcome: str, kind: str) -> None:
    with pytest.raises(AutoRecoveryInputError, match="only valid"):
        validate_event_for(outcome, _event(kind), "reason")


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_empty_reason_rejected(reason: str | None) -> None:
    with pytest.raises(AutoRecoveryInputError, match="--reason"):
        validate_event_for("continue-sent", _event(), reason)


def test_missing_unit_field_rejected() -> None:
    with pytest.raises(AutoRecoveryInputError, match="'step'"):
        validate_event_for("continue-sent", _event(step=None), "x")
    with pytest.raises(AutoRecoveryInputError, match="'anchor'"):
        validate_event_for("retry-sent", _event("aborted", anchor=None), "x")
    validate_event_for("wait", _event("aborted", step=None, attempt=None), "x")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-11-15T04:00:00",  # 時刻帯なし
        "2026-11-15T02:00:00+00:00",  # 過去
        "2026-11-15T03:00:00+00:00",  # ちょうど今
        "2026-11-22T03:00:01+00:00",  # 7 日より先
        "not a time",
    ],
)
def test_until_rejected(value: str | None) -> None:
    with pytest.raises(AutoRecoveryInputError):
        check_until(value, NOW)


def test_until_normalized_to_utc() -> None:
    assert check_until("2026-11-15T13:00:00+09:00", NOW) == "2026-11-15T04:00:00+00:00"
    assert check_until("2026-11-22T03:00:00+00:00", NOW) == "2026-11-22T03:00:00+00:00"


def test_paths_inside_checkouts(tmp_path: Path) -> None:
    main = tmp_path / "repo"
    worktree = tmp_path / "repo-feat-1"
    (main / "src").mkdir(parents=True)
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (main / "link").symlink_to(outside)
    checkouts = [main, worktree]
    assert check_paths([str(main / "src"), str(worktree)], checkouts) == [
        str((main / "src").resolve()),
        str(worktree.resolve()),
    ]
    for bad in ([], ["relative/path"], [str(main / ".." / "outside")], [str(main / "link")]):
        with pytest.raises(AutoRecoveryInputError):
            check_paths(bad, checkouts)
    with pytest.raises(AutoRecoveryInputError):
        check_paths(None, checkouts)


def test_unit_of_each_outcome() -> None:
    event = _event()
    assert unit_of(event, "continue-sent") == [101, "261115000000", "implement", 1]
    assert unit_of(event, "wait") == [101, "261115000000"]
    assert unit_of(_event("aborted"), "retry-sent") == [101, "member:261115000000"]


def _wf(repo: Path, rel: str, name: str | None, *, broken: bool = False) -> Path:
    path = repo / ".kaji" / "wf" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "name: [unclosed" if broken else f"name: {name}\nsteps: []\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_resolve_workflow_name_by_name_not_stem(tmp_path: Path) -> None:
    official = _wf(tmp_path, "official/feature.yaml", "dev")
    _wf(tmp_path, "dev.yaml", "other")
    assert resolve_workflow_name("dev", tmp_path) == official.resolve()
    assert resolve_workflow_name("dev", tmp_path).is_absolute()


def test_resolve_workflow_name_failures(tmp_path: Path) -> None:
    _wf(tmp_path, "broken.yaml", None, broken=True)
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_workflow_name("dev", tmp_path)
    assert exc.value.reason == "unresolvable"
    _wf(tmp_path, "a.yaml", "dev")
    _wf(tmp_path, "custom/b.yaml", "dev")
    with pytest.raises(WorkflowResolutionError) as exc:
        resolve_workflow_name("dev", tmp_path)
    assert exc.value.reason == "ambiguous"


def _status(run_id: str, *, parent: str | None, termination: str, end: str | None) -> dict:
    return {
        "issue": "101",
        "latest_run": {
            "run_id": run_id,
            "termination": termination,
            "end_status": end,
            "recovery": None
            if parent is None
            else {"root_run_id": parent, "parent_run_id": parent, "chain_index": 1},
        },
    }


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_status("child", parent="target", termination="running", end=None), "running"),
        (_status("child", parent="target", termination="ended", end="COMPLETE"), "completed"),
        (_status("child", parent="target", termination="ended", end="ERROR"), "failed"),
        (_status("child", parent="target", termination="ended", end="ABORT"), "failed"),
        (_status("child", parent="target", termination="vanished", end=None), "failed"),
        (_status("child", parent="target", termination="unknown", end=None), "running"),
        ({"latest_run": {"run_id": "child", "recovery": {"parent_run_id": "target"}}}, "running"),
        (_status("target", parent=None, termination="ended", end="ERROR"), "not_started"),
        ({"issue": "101", "latest_run": None}, "not_started"),
        (_status("other", parent=None, termination="running", end=None), "unrelated"),
        (_status("other", parent="someone", termination="running", end=None), "unrelated"),
    ],
)
def test_classify_retry_child(document: dict, expected: str) -> None:
    assert classify_retry_child(document, "target") == expected
    assert expected in RETRY_CHILD_STATES


def _rec(key: str, outcome: str, unit: list[str | int | None] | None, at: str) -> WatchRecord:
    return WatchRecord.model_validate(
        {"event_key": key, "outcome": outcome, "recorded_at": at, "judge": "h:1", "unit": unit}
    )


def test_recovery_rows_count_and_filter() -> None:
    unit = [101, "r1", "implement", 1]
    records = [
        _rec("101:pane_idle:r1:implement:1:0", "continue-sent", unit, "t1"),
        _rec("101:pane_idle:r1:implement:1:0", "recovered", None, "t2"),
        _rec("102:pane_idle:r2:design:1:0", "continue-sent", [102, "r2", "design", 1], "t3"),
        _rec("101:pane_idle:r1:implement:1:1", "continue-sent", unit, "t4"),
    ]
    rows = recovery_rows(records, None)
    assert [(r["issue"], r["count"], r["limit"]) for r in rows] == [
        (101, 1, 3),
        (102, 1, 3),
        (101, 2, 3),
    ]
    filtered = recovery_rows(records, 101)
    assert [r["recorded_at"] for r in filtered] == ["t1", "t4"]
    assert set(filtered[0]) == {
        "recorded_at",
        "issue",
        "event_key",
        "outcome",
        "unit",
        "count",
        "limit",
        "reason",
        "until",
        "paths",
        "judge",
    }


def test_notify_payload_v2_accepts_retry() -> None:
    payload = build_payload("retry", repo="o/r", issue=101, summary="s", now=NOW)
    assert payload["schema_version"] == 2
    assert payload["kind"] == "retry"


def test_old_record_line_without_new_fields_reads() -> None:
    line = '{"event_key":"101:aborted:1","outcome":"handoff","recorded_at":"t","judge":"h:1"}'
    record = WatchRecord.model_validate_json(line)
    assert record.unit is None and record.reason is None
    assert re.fullmatch(r"\S+", record.outcome)


def test_old_event_file_without_new_fields_reads() -> None:
    event = WatchEventFile.model_validate(
        {"key": "101:aborted:1", "kind": "aborted", "issue": 101, "detected_at": "t"}
    )
    assert event.anchor is None and event.waited_until is None
