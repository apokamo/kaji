"""Medium tests: ``kaji run-series --resume`` from the failed step (Issue #540, D8〜D10)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.errors import SeriesAbortedError, SeriesRuntimeError
from kaji_harness.providers import Issue
from kaji_harness.recovery.gates import RetryPlanner, prepare_retry, release_launcher_record
from kaji_harness.runstate.facts import collect_issue_facts
from kaji_harness.runstate.launch import LaunchLock
from kaji_harness.runstate.process import ProcessRecord, current_process_record
from kaji_harness.series import SeriesConfig, SeriesRunner, SeriesState, series_fingerprint
from kaji_harness.workflow import load_workflow
from tests.runstate_helpers import end, ev, start, step_end, step_start, write_run, write_state

pytestmark = pytest.mark.medium

RUN = "261112000000"
WORKFLOW_PATH = ".kaji/wf/official/dev.yaml"
STOP_GATES = re.compile(r"gates=(?P<gates>[a-z_]+(?:,[a-z_]+)*)$")
WORKFLOW = """name: dev
description: d
requires_provider: any
execution_policy: auto

steps:
  - id: start
    skill: issue-start
    agent: claude
    on:
      PASS: implement
      ABORT: end
  - id: implement
    skill: issue-implement
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


class FakeProvider:
    def __init__(self, issues: dict[int, Issue]):
        self.issues = issues

    def view_issue(self, issue_id: str) -> Issue:
        return self.issues[int(issue_id)]


@dataclass
class FakeProcess:
    pid: int
    returncode: int = 0

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None


def _issue(issue: int, state: str = "open", reason: str = "") -> Issue:
    return Issue(id=str(issue), title="t", body="", state=state, state_reason=reason)


def _config() -> SeriesConfig:
    return SeriesConfig.model_validate(
        {
            "id": "resume-series",
            "strategy": "sequential",
            "members": [
                {"issue": 10, "workflow": WORKFLOW_PATH},
                {"issue": 11, "workflow": WORKFLOW_PATH},
            ],
            "on_failure": "stop",
        }
    )


def _dead_record(run_id: str = RUN) -> ProcessRecord:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return current_process_record("run", run_id=run_id).model_copy(
        update={"pid": proc.pid, "pid_start_ticks": 1}
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".kaji" / "wf" / "official").mkdir(parents=True)
    (root / WORKFLOW_PATH).write_text(WORKFLOW, encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=feat/10", str(worktree)], check=True)
    write_state(root / "artifacts", "10", worktree=str(worktree), branch="feat/10")
    return root


def _artifacts(repo: Path) -> Path:
    return repo / "artifacts"


def _failed_member_state(repo: Path, status: str = "failed") -> SeriesState:
    config = _config()
    state = SeriesState.create(config)
    state.members[0].status = status  # type: ignore[assignment]
    state.members[0].gate = "exit:3"
    state.status = "stopped"
    path = _artifacts(repo) / "_series" / config.id / "state.json"
    state.save(path)
    assert state.fingerprint == series_fingerprint(config)
    return state


def _failed_run(
    repo: Path, *, kind: str = "dispatch_exception", process: ProcessRecord | None = None
) -> Path:
    events = [
        start(),
        step_start("implement"),
        ev(
            "failure_event",
            kind=kind,
            step_id="implement",
            exception_type="StepTimeoutError",
            synthetic=True,
        ),
        step_end("implement", 1, "ABORT"),
        end("ERROR", "StepTimeoutError: timed out"),
    ]
    return write_run(
        _artifacts(repo),
        "10",
        RUN,
        events,
        process=process if process is not None else _dead_record(),
        results={("implement", 1): {"error": "timed out", "synthetic": True}},
    )


def _runner(
    repo: Path, provider: FakeProvider, calls: list[list[str]], **kwargs: object
) -> SeriesRunner:
    def launch(argv: list[str], cwd: Path) -> FakeProcess:
        calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=4_000_000 + issue)

    return SeriesRunner(
        config=_config(),
        repo_root=repo,
        artifacts_dir=_artifacts(repo),
        provider=provider,  # type: ignore[arg-type]
        member_launcher=kwargs.pop("member_launcher", launch),  # type: ignore[arg-type]
        pid_alive=lambda _pid: False,
        pane_inspector=lambda _pane: "absent",
    )


def _saved(repo: Path) -> SeriesState:
    return SeriesState.load(_artifacts(repo) / "_series" / "resume-series" / "state.json")


def test_failed_member_resumes_from_failed_step_then_continues(repo: Path) -> None:
    run_dir = _failed_run(repo)
    _failed_member_state(repo)
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    assert _runner(repo, provider, calls).run(resume=True) == 0
    assert calls[0] == [
        "kaji",
        "run",
        WORKFLOW_PATH,
        "10",
        "--from",
        "implement",
        "--recovery-root",
        RUN,
        "--recovery-parent",
        RUN,
        "--recovery-trigger",
        "series_resume",
    ]
    assert calls[1] == ["kaji", "run", WORKFLOW_PATH, "11"]
    state = _saved(repo)
    assert [m.status for m in state.members] == ["completed", "completed"]
    retries = json.loads((run_dir / "recovery.json").read_text())["retries"]
    assert retries[-1]["trigger"] == "series_resume"
    assert retries[-1]["outcome"] == "launched"
    assert list((_artifacts(repo) / "10" / "launches").glob("*.json")) == []


def test_member_closed_completed_outside_series_is_completed(repo: Path) -> None:
    _failed_member_state(repo)
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10, "closed", "completed"), 11: _issue(11)})
    assert _runner(repo, provider, calls).run(resume=True) == 0
    assert calls == [["kaji", "run", WORKFLOW_PATH, "11"]]
    state = _saved(repo)
    assert state.members[0].status == "completed"
    assert state.members[0].gate == "closed_completed"


def test_member_closed_not_planned_stops(repo: Path) -> None:
    _failed_member_state(repo, "interrupted")
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10, "closed", "not_planned"), 11: _issue(11)})
    with pytest.raises(SeriesAbortedError, match="mismatch:closed/not_planned"):
        _runner(repo, provider, calls).run(resume=True)
    assert calls == []
    state = _saved(repo)
    assert state.status == "stopped"
    assert state.stop_reason == (
        "member 10 is already closed before its resumed run: mismatch:closed/not_planned"
    )


@pytest.mark.parametrize(
    ("setup", "gates"),
    [
        ("alive", "run_alive"),
        ("no_run", "no_target_run"),
        ("interrupted", "user_interrupted"),
        ("broken", "artifact_unreadable"),
        ("unknown", "issue_liveness_unknown"),
    ],
)
def test_gate_refusal_stops_without_launch(
    repo: Path, setup: str, gates: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if setup == "alive":
        _failed_run(repo, process=current_process_record("run", run_id=RUN))
    elif setup == "interrupted":
        _failed_run(repo, kind="interrupted")
    elif setup == "broken":
        run_dir = _failed_run(repo)
        (run_dir / "steps" / "implement" / "attempt-001" / "result.json").unlink()
    elif setup == "unknown":
        # R2: an older run with neither a liveness record nor ``workflow_end`` is undecidable
        _failed_run(repo)
        write_run(_artifacts(repo), "10", "261111000000", [start(), step_start("implement")])
    _failed_member_state(repo)
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    with pytest.raises(SeriesAbortedError):
        _runner(repo, provider, calls).run(resume=True)
    assert calls == []
    state = _saved(repo)
    assert state.status == "stopped"
    assert state.members[0].status == "failed"
    assert state.stop_reason is not None
    match = STOP_GATES.search(state.stop_reason)
    assert match is not None and gates in match.group("gates").split(",")
    assert f"retry-gate: {gates}" in capsys.readouterr().err


def test_launcher_record_failure_stops_with_exit_3_without_launch(repo: Path) -> None:
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    with (
        patch("kaji_harness.series.runner.write_launch_record", side_effect=OSError("ro fs")),
        pytest.raises(SeriesRuntimeError, match="could not start"),
    ):
        _runner(repo, provider, calls).run()
    assert calls == []
    state = _saved(repo)
    assert state.status == "stopped"
    assert state.members[0].status == "pending"


def test_resume_launcher_record_failure_stops_with_exit_3(repo: Path) -> None:
    _failed_run(repo)
    _failed_member_state(repo)
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    with (
        patch("kaji_harness.recovery.gates.write_launch_record", side_effect=OSError("ro fs")),
        pytest.raises(SeriesRuntimeError, match="could not start"),
    ):
        _runner(repo, provider, calls).run(resume=True)
    assert calls == []
    assert _saved(repo).members[0].status == "failed"


def test_issue_is_alive_before_child_pid_is_saved(repo: Path) -> None:
    """#1909 形: Popen 前に launcher record があり、child_pid 保存前も生存が見える。"""
    observed: list[tuple[bool | None, bool | None]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})

    def launch(argv: list[str], _cwd: Path) -> FakeProcess:
        facts = collect_issue_facts(_artifacts(repo), argv[3])
        observed.append((facts.any_run_alive, facts.launching))
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=4_000_000 + issue)

    runner = _runner(repo, provider, [], member_launcher=launch)
    assert runner.run() == 0
    assert observed == [(True, True), (True, True)]


def _retry_planner(repo: Path) -> RetryPlanner:
    return RetryPlanner(
        workflow=load_workflow(repo / WORKFLOW_PATH),
        artifacts_dir=_artifacts(repo),
        issue_id="10",
        pane_inspector=lambda _pane: "absent",
    )


def test_resume_is_refused_while_a_retry_holds_the_launch_lock(repo: Path) -> None:
    # R1 (concurrent requests, retry x series resume): the lock holder wins
    _failed_run(repo)
    _failed_member_state(repo)
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    lock = LaunchLock(_artifacts(repo), "10")
    assert lock.acquire() is True
    try:
        with pytest.raises(SeriesAbortedError, match="gates=concurrent_launch"):
            _runner(repo, provider, calls).run(resume=True)
    finally:
        lock.release()
    assert calls == []
    assert _saved(repo).members[0].status == "failed"


def test_resume_is_refused_after_a_retry_wrote_its_launcher_record(repo: Path) -> None:
    # R1: once the retry launcher is recorded, the series resume sees the Issue as alive and
    # launches nothing, so only one child (the retry's) can start.
    _failed_run(repo)
    _failed_member_state(repo)
    prepared = prepare_retry(_retry_planner(repo), None)
    assert prepared.plan.gates == [] and prepared.launcher_record is not None
    calls: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    try:
        with pytest.raises(SeriesAbortedError, match="gates=issue_run_alive"):
            _runner(repo, provider, calls).run(resume=True)
    finally:
        release_launcher_record(prepared.launcher_record)
    assert calls == []


def test_retry_is_refused_while_a_series_resume_launcher_is_recorded(repo: Path) -> None:
    # R1 (the other order): a series resume child in flight blocks a retry request
    _failed_run(repo)
    _failed_member_state(repo)
    observed: list[list[str]] = []
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []

    def launch(argv: list[str], cwd: Path) -> FakeProcess:
        calls.append(argv)
        issue = int(argv[3])
        if issue == 10:
            plan = prepare_retry(_retry_planner(repo), None).plan
            observed.append([gate.name for gate in plan.gates])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=4_000_000 + issue)

    assert _runner(repo, provider, calls, member_launcher=launch).run(resume=True) == 0
    assert observed == [["issue_run_alive"]]
    assert len(calls) == 2  # the resumed member and the next member; the retry launched nothing
