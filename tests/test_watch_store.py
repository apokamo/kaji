"""Medium tests: ``kaji_harness.watch.store`` (Issue #543 M4)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from kaji_harness.watch.models import EventState, WatchEventFile, WatchRecord, WatchState
from kaji_harness.watch.store import WatchLockHeld, WatchStore, event_file_name

pytestmark = pytest.mark.medium


def _event(key: str = "101:aborted:261115113853") -> WatchEventFile:
    return WatchEventFile(
        key=key, kind="aborted", issue=101, detected_at="2026-11-15T03:00:00+00:00"
    )


def test_event_file_name() -> None:
    assert event_file_name("101:aborted:261115113853") == "101-aborted-261115113853.json"


def test_state_round_trip(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    assert store.load_state() == WatchState()
    state = WatchState(
        pending_labels={"101": "state:running", "102": None},
        events={
            "k": EventState(
                key="k", kind="aborted", issue=101, detected_at="t", event_file="/x.json"
            )
        },
    )
    store.save_state(state)
    assert store.load_state() == state
    assert not list(store.root.glob(".*.tmp"))


def test_broken_state_is_moved_aside(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = WatchStore(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "state.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kaji.watch"):
        assert store.load_state() == WatchState()
    assert list(store.root.glob("state.json.broken-*"))
    assert any("broken" in record.getMessage() for record in caplog.records)


def test_event_write_read_and_scope(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    path = store.write_event(_event())
    assert path.name == "101-aborted-261115113853.json"
    assert store.read_event(path).key == "101:aborted:261115113853"
    outside = tmp_path / "evil.json"
    outside.write_text(_event().model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError):
        store.read_event(outside)
    path.write_text('{"schema_version": 2}', encoding="utf-8")
    with pytest.raises(ValidationError):
        store.read_event(path)


def test_event_is_not_overwritten(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    path = store.write_event(_event())
    original = path.read_text(encoding="utf-8")
    changed = _event().model_copy(update={"run_id": "other"})
    store.write_event(changed)
    assert path.read_text(encoding="utf-8") == original


def test_records_append_and_skip_invalid(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    record = WatchRecord(event_key="k", outcome="handoff", recorded_at="t", judge="h:1")
    store.append_record(record)
    with (store.root / "records.jsonl").open("a", encoding="utf-8") as f:
        f.write("garbage\n")
    store.append_record(record)
    assert store.read_records() == [record, record]


def test_label_log_has_judge(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    store.append_label_log(101, ["state:running"], ["state:planned"])
    line = (store.root / "labels.jsonl").read_text(encoding="utf-8")
    assert '"judge"' in line and '"issue": 101' in line


def test_lock_is_exclusive(tmp_path: Path) -> None:
    holder = WatchStore(tmp_path)
    holder.acquire_lock("p_12")
    other = WatchStore(tmp_path)
    assert other.is_locked() is True
    with pytest.raises(WatchLockHeld) as exc:
        other.acquire_lock("p_99")
    assert exc.value.holder_pane == "p_12"
    holder.release_lock()
    assert other.is_locked() is False
    other.acquire_lock("p_99")
    other.release_lock()
