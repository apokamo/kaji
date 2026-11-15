"""Medium tests: automatic recovery records, ``retry`` and ``recoveries`` (Issue #545)."""

from __future__ import annotations

import json
import multiprocessing
import shlex
from datetime import timedelta
from pathlib import Path

import pytest

from kaji_harness.commands.parser import create_parser
from kaji_harness.errors import AutoRecoveryLimitReached, WatchPreconditionError
from kaji_harness.watch import service
from kaji_harness.watch.models import WatchEventFile, WatchRecord
from kaji_harness.watch.store import RECORDS_FILE, WatchStore
from kaji_harness.watch.workflows import resolve_workflow_name
from kaji_harness.workflow import load_workflow
from tests.test_watch_loop import NOW, FakeHerdr, FakeIssue, FakeProvider, _deps, _notify_script

pytestmark = pytest.mark.medium

ROOT = Path(__file__).resolve().parents[1]
UNIT = [101, "r1", "implement", 1]


def _record(
    outcome: str = "continue-sent", unit: list[str | int | None] | None = None
) -> WatchRecord:
    return WatchRecord.model_validate(
        {
            "event_key": "101:pane_idle:r1:implement:1:0",
            "outcome": outcome,
            "recorded_at": NOW.isoformat(),
            "judge": "h:1",
            "unit": UNIT if unit is None else unit,
        }
    )


def test_append_record_limited_stops_at_limit(tmp_path: Path) -> None:
    store = WatchStore(tmp_path)
    path = store.root / RECORDS_FILE
    path.parent.mkdir(parents=True)
    # 既存の行（新項目なし）と壊れた行は読めて、壊れた行は数えない
    path.write_text(
        '{"event_key":"101:aborted:1","outcome":"handoff","recorded_at":"t","judge":"h:1"}\n'
        "{broken\n",
        encoding="utf-8",
    )
    assert [store.append_record_limited(_record(), unit=UNIT, limit=3) for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]
    # 別の単位・別の outcome は数えない
    other = [101, "r1", "implement", 2]
    assert store.append_record_limited(_record(unit=other), unit=other, limit=3)
    assert store.append_record_limited(_record("dialog-answered"), unit=UNIT, limit=3)
    outcomes = [r.outcome for r in store.read_records()]
    assert outcomes.count("continue-sent") == 4
    assert outcomes[0] == "handoff"


def _append_worker(root: str, results: object) -> None:
    store = WatchStore(Path(root))
    ok = store.append_record_limited(_record(), unit=UNIT, limit=3)
    results.put(ok)  # type: ignore[attr-defined]


def test_append_record_limited_is_exclusive_across_processes(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    procs = [ctx.Process(target=_append_worker, args=(str(tmp_path), results)) for _ in range(6)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    outcomes = [results.get(timeout=5) for _ in procs]
    assert outcomes.count(True) == 3
    lines = (WatchStore(tmp_path).root / RECORDS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def _event(tmp_path: Path, kind: str, **overrides: object) -> Path:
    data: dict[str, object] = {
        "key": f"101:{kind}:261115000000" + (":implement:1:0" if kind == "pane_idle" else ""),
        "kind": kind,
        "issue": 101,
        "detected_at": NOW.isoformat(),
        "run_id": "261115000000",
        "workflow": "dev",
        "step": "implement",
        "attempt": 1,
        "failed_step": "implement",
        "anchor": "member:261115000000",
    }
    data.update(overrides)
    return WatchStore(tmp_path / ".kaji-artifacts").write_event(WatchEventFile.model_validate(data))


def _workflow(tmp_path: Path, name: str = "dev") -> Path:
    path = tmp_path / ".kaji" / "wf" / "official" / "feature.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: {name}\nsteps: []\n", encoding="utf-8")
    return path.resolve()


def _payloads(out: Path) -> list[dict[str, object]]:
    if not out.is_file():
        return []
    return [json.loads(line) for line in out.read_text(encoding="utf-8").split("\n") if line]


def test_record_auto_outcomes_limit_and_no_notification(tmp_path: Path) -> None:
    script, out = _notify_script(tmp_path)
    deps = _deps(tmp_path, FakeProvider({101: FakeIssue()}), FakeHerdr(), script)
    event = _event(tmp_path, "pane_idle")
    for _ in range(3):
        entry = service.record(
            deps, event, "continue-sent", comment_url=None, summary=None, reason="at capacity"
        )
        assert entry.unit == [101, "261115000000", "implement", 1]
    with pytest.raises(AutoRecoveryLimitReached) as exc:
        service.record(deps, event, "continue-sent", comment_url=None, summary=None, reason="x")
    assert exc.value.limit == 3
    assert len(deps.store.read_records()) == 3
    until = (NOW + timedelta(hours=2)).isoformat()
    waited = service.record(
        deps, event, "wait", comment_url=None, summary=None, reason="usage limit", until=until
    )
    assert waited.until == until
    assert _payloads(out) == []


def test_record_dialog_paths_and_invalid_arguments(tmp_path: Path) -> None:
    deps = _deps(tmp_path, FakeProvider({101: FakeIssue()}), FakeHerdr())
    event = _event(tmp_path, "pane_idle")
    inside = tmp_path / "src"
    inside.mkdir()
    entry = service.record(
        deps,
        event,
        "dialog-answered",
        comment_url=None,
        summary=None,
        reason="trust",
        paths=[str(inside)],
    )
    assert entry.paths == [str(inside.resolve())]
    bad_calls: list[dict[str, object]] = [
        {"outcome": "dialog-answered", "reason": "x", "paths": ["/etc"]},
        {"outcome": "wait", "reason": "x", "until": "2020-01-01T00:00:00+00:00"},
        {"outcome": "continue-sent", "reason": ""},
        {"outcome": "continue-sent", "reason": "x", "until": "2030-01-01T00:00:00+00:00"},
        {"outcome": "still-working", "reason": "x"},
        {"outcome": "retry-sent", "reason": "x"},
    ]
    for call in bad_calls:
        outcome = call.pop("outcome")
        with pytest.raises(WatchPreconditionError):
            service.record(deps, event, outcome, comment_url=None, summary=None, **call)  # type: ignore[arg-type]
    aborted = _event(tmp_path, "aborted")
    with pytest.raises(WatchPreconditionError):
        service.record(deps, aborted, "continue-sent", comment_url=None, summary=None, reason="x")


def test_retry_launches_recover_tab_once_and_notifies(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    script, out = _notify_script(tmp_path)
    herdr = FakeHerdr()
    deps = _deps(tmp_path, FakeProvider({101: FakeIssue()}), herdr, script)
    event = _event(tmp_path, "aborted")
    result = service.retry(deps, event, "provider at capacity")
    expected = shlex.join(
        [
            "kaji",
            "recover",
            str(workflow),
            "101",
            "--retry-failed-step",
            "--run-id",
            "261115000000",
        ]
    )
    assert result["command"] == expected
    assert [t.label for t in herdr.tabs] == ["kaji-retry-101"]
    assert herdr.runs == [(result["pane_id"], expected)]
    assert result["record"]["workflow_path"] == str(workflow)
    assert result["record"]["unit"] == [101, "member:261115000000"]
    payloads = _payloads(out)
    assert [(p["kind"], p["schema_version"]) for p in payloads] == [("retry", 2)]
    with pytest.raises(AutoRecoveryLimitReached):
        service.retry(deps, event, "again")
    assert len(herdr.tabs) == 1
    assert len(_payloads(out)) == 1


@pytest.mark.parametrize("names", [[], ["dev", "dev"]])
def test_retry_unresolved_workflow_records_nothing(tmp_path: Path, names: list[str]) -> None:
    for index, name in enumerate(names):
        path = tmp_path / ".kaji" / "wf" / f"w{index}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"name: {name}\n", encoding="utf-8")
    herdr = FakeHerdr()
    deps = _deps(tmp_path, FakeProvider({101: FakeIssue()}), herdr)
    event = _event(tmp_path, "aborted")
    with pytest.raises(WatchPreconditionError, match="workflow 'dev'"):
        service.retry(deps, event, "x")
    assert deps.store.read_records() == []
    assert herdr.tabs == []


def test_retry_requires_herdr_and_valid_event(tmp_path: Path) -> None:
    _workflow(tmp_path)
    outside = _deps(tmp_path, FakeProvider({101: FakeIssue()}), None)
    with pytest.raises(WatchPreconditionError, match="Herdr"):
        service.retry(outside, _event(tmp_path, "aborted"), "x")
    deps = _deps(tmp_path, FakeProvider({101: FakeIssue()}), FakeHerdr())
    with pytest.raises(WatchPreconditionError):
        service.retry(deps, _event(tmp_path, "pane_idle"), "x")
    no_anchor = _event(tmp_path, "aborted", key="101:aborted:261115000001", anchor=None)
    with pytest.raises(WatchPreconditionError):
        service.retry(deps, no_anchor, "x")
    assert deps.store.read_records() == []


def test_retry_command_reaches_recover_input_contract() -> None:
    workflow = resolve_workflow_name("dev", ROOT)
    argv = shlex.split(service.retry_command(workflow, 101, "261115000000"))
    assert argv[0] == "kaji"
    args = create_parser().parse_args(argv[1:])
    assert args.command == "recover"
    assert args.retry_failed_step is True
    assert args.run_id == "261115000000"
    assert args.workflow.exists()
    assert load_workflow(args.workflow.resolve()).name == "dev"


def test_events_v2_status_v1_and_recoveries(tmp_path: Path) -> None:
    deps = _deps(tmp_path, FakeProvider({}), FakeHerdr())
    event = _event(tmp_path, "pane_idle")
    service.record(deps, event, "question-answered", comment_url=None, summary=None, reason="q")
    document = service.events(deps.store)
    assert document["schema_version"] == 2
    assert document["records"][0]["outcome"] == "question-answered"
    assert service.status(deps)["schema_version"] == 1
    listing = service.recoveries(deps.store, 101)
    assert listing["schema_version"] == 1
    assert [(r["outcome"], r["count"], r["limit"]) for r in listing["recoveries"]] == [
        ("question-answered", 1, 3)
    ]
    assert service.recoveries(deps.store, 102)["recoveries"] == []
