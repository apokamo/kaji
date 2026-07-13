"""Medium tests for sequential execution and crash-safe resume."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from kaji_harness.errors import SeriesAbortedError, SeriesInputError
from kaji_harness.providers import Issue
from kaji_harness.series import SeriesConfig, SeriesRunner, SeriesState
from kaji_harness.series.runner import _default_pid_alive

pytestmark = pytest.mark.medium


class FakeProvider:
    """Minimal provider fake for runner state lookups."""

    def __init__(self, issues: dict[int, Issue]):
        self.issues = issues
        self.views: list[int] = []

    def view_issue(self, issue_id: str) -> Issue:
        issue = int(issue_id)
        self.views.append(issue)
        return self.issues[issue]


@dataclass
class FakeProcess:
    """Popen-shaped child process fake."""

    pid: int
    returncode: int = 0
    terminated: bool = False

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


def _config() -> SeriesConfig:
    return SeriesConfig.model_validate(
        {
            "id": "runner-series",
            "strategy": "sequential",
            "members": [
                {"issue": 10, "workflow": ".kaji/wf/dev.yaml"},
                {"issue": 11, "workflow": ".kaji/wf/dev.yaml"},
            ],
            "on_failure": "stop",
        }
    )


def _issue(issue: int, state: str = "open", reason: str = "") -> Issue:
    return Issue(id=str(issue), title="t", body="", state=state, state_reason=reason)


def _runner(
    tmp_path: Path,
    provider: FakeProvider,
    calls: list[list[str]],
    *,
    exit_codes: list[int] | None = None,
    pid_alive: bool = False,
) -> SeriesRunner:
    returns = iter(exit_codes or [0, 0])

    def launch(argv: list[str], cwd: Path) -> FakeProcess:
        assert cwd == tmp_path
        calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=1000 + issue, returncode=next(returns))

    return SeriesRunner(
        config=_config(),
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=launch,
        pid_alive=lambda _pid: pid_alive,
    )


def test_runner_executes_in_order_and_persists_pid_before_wait(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    observed_running_pids: list[int | None] = []
    holder: dict[str, SeriesRunner] = {}

    def launch(argv: list[str], _cwd: Path) -> FakeProcess:
        calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        process = FakeProcess(pid=1000 + issue)

        def wait() -> int:
            persisted = SeriesState.load(holder["runner"].state_path)
            observed_running_pids.append(persisted.members[len(calls) - 1].child_pid)
            return 0

        process.wait = wait  # type: ignore[method-assign]
        return process

    runner = SeriesRunner(
        config=_config(),
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=launch,
    )
    holder["runner"] = runner
    assert runner.run() == 0
    assert [argv[3] for argv in calls] == ["10", "11"]
    assert observed_running_pids == [1010, 1011]
    state = SeriesState.load(runner.state_path)
    assert state.status == "completed"
    assert [member.child_pid for member in state.members] == [1010, 1011]
    assert all(member.status == "completed" for member in state.members)
    output = capsys.readouterr().out
    assert "member 10 started pid=1010" in output
    assert "member 10 exited code=0" in output
    assert "member 10 completed gate=closed_completed" in output


def test_runner_records_discovered_child_run_id(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []

    def launch(argv: list[str], _cwd: Path) -> FakeProcess:
        issue = int(argv[3])
        calls.append(argv)
        provider.issues[issue] = _issue(issue, "closed", "completed")
        process = FakeProcess(pid=1000 + issue)

        def wait() -> int:
            run_dir = tmp_path / "artifacts" / str(issue) / "runs" / f"run-{issue}"
            run_dir.mkdir(parents=True)
            return 0

        process.wait = wait  # type: ignore[method-assign]
        return process

    runner = SeriesRunner(
        config=_config(),
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=launch,
    )
    assert runner.run() == 0
    state = SeriesState.load(runner.state_path)
    assert [member.run_id for member in state.members] == ["run-10", "run-11"]


def test_runner_stops_after_child_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls, exit_codes=[1])
    with pytest.raises(SeriesAbortedError, match="exit:1"):
        runner.run()
    assert [argv[3] for argv in calls] == ["10"]
    state = SeriesState.load(runner.state_path)
    assert state.status == "stopped"
    assert state.members[0].status == "failed"
    output = capsys.readouterr().out
    assert "member 10 exited code=1" in output
    assert "stopped: member 10 failed: exit:1" in output


def test_runner_stops_after_gate_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []

    def launch(argv: list[str], _cwd: Path) -> FakeProcess:
        calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "duplicate")
        return FakeProcess(pid=1000 + issue)

    runner = SeriesRunner(
        config=_config(),
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=launch,
    )
    with pytest.raises(SeriesAbortedError, match="mismatch:closed/duplicate"):
        runner.run()
    assert [argv[3] for argv in calls] == ["10"]
    output = capsys.readouterr().out
    assert "member 10 exited code=0" in output
    assert "stopped: member 10 gate failed: mismatch:closed/duplicate" in output


def test_interrupt_terminates_child_and_persists_interrupted_state(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    process = FakeProcess(pid=1010)

    def interrupted_wait() -> int:
        if not process.terminated:
            raise KeyboardInterrupt
        return 143

    process.wait = interrupted_wait  # type: ignore[method-assign]
    runner = SeriesRunner(
        config=_config(),
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=lambda _argv, _cwd: process,
    )
    with pytest.raises(SeriesAbortedError, match="interrupted by signal"):
        runner.run()
    assert process.terminated is True
    state = SeriesState.load(runner.state_path)
    assert state.status == "stopped"
    assert state.members[0].status == "interrupted"
    assert state.members[0].exit_code == 143


def test_fresh_run_rejects_member_already_closed(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10, "closed", "completed"), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    with pytest.raises(SeriesAbortedError, match="already closed"):
        runner.run()
    assert calls == []


def test_resume_skips_completed_member(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10, "closed", "completed"), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    state = SeriesState.create(_config())
    state.members[0].status = "completed"
    state.members[0].gate = "closed_completed"
    state.save(runner.state_path)
    assert runner.run(resume=True) == 0
    assert [argv[3] for argv in calls] == ["11"]


def test_resume_rejects_fingerprint_change(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    state = SeriesState.create(_config())
    state.fingerprint = "sha256:different"
    state.save(runner.state_path)
    with pytest.raises(SeriesInputError, match="fingerprint"):
        runner.run(resume=True)
    assert calls == []


def test_existing_state_requires_explicit_resume(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    SeriesState.create(_config()).save(runner.state_path)
    with pytest.raises(SeriesInputError, match="pass --resume"):
        runner.run()
    assert calls == []


def test_resume_requires_existing_state(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    with pytest.raises(SeriesInputError, match="resume state not found"):
        runner.run(resume=True)
    assert calls == []


def test_resume_rejects_completed_member_rollback(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    state = SeriesState.create(_config())
    state.members[0].status = "completed"
    state.members[0].gate = "closed_completed"
    state.save(runner.state_path)
    with pytest.raises(SeriesAbortedError, match="rolled back"):
        runner.run(resume=True)
    assert calls == []


def test_resume_running_live_child_is_rejected(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls, pid_alive=True)
    state = SeriesState.create(_config())
    state.members[0].status = "running"
    state.members[0].child_pid = 1234
    state.save(runner.state_path)
    with pytest.raises(SeriesInputError, match="still alive"):
        runner.run(resume=True)
    assert calls == []


def test_pid_permission_error_is_conservatively_alive() -> None:
    with patch("kaji_harness.series.runner.os.kill", side_effect=OSError(errno.EPERM, "denied")):
        assert _default_pid_alive(1234) is True


def test_resume_reconciles_dead_child_that_completed(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10, "closed", "completed"), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    state = SeriesState.create(_config())
    state.members[0].status = "running"
    state.members[0].child_pid = 1234
    state.save(runner.state_path)
    assert runner.run(resume=True) == 0
    assert [argv[3] for argv in calls] == ["11"]


def test_resume_reexecutes_dead_child_that_did_not_complete(tmp_path: Path) -> None:
    provider = FakeProvider({10: _issue(10), 11: _issue(11)})
    calls: list[list[str]] = []
    runner = _runner(tmp_path, provider, calls)
    state = SeriesState.create(_config())
    state.members[0].status = "running"
    state.members[0].child_pid = 1234
    state.save(runner.state_path)
    assert runner.run(resume=True) == 0
    assert [argv[3] for argv in calls] == ["10", "11"]


@pytest.mark.parametrize("fixture_name", ["epic-291.yaml", "standalone.yaml"])
def test_epic_and_standalone_fixtures_share_ordered_execution_semantics(
    tmp_path: Path, fixture_name: str
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "series" / fixture_name
    config = SeriesConfig.model_validate(yaml.safe_load(fixture.read_text(encoding="utf-8")))
    issues = {member.issue: _issue(member.issue) for member in config.members}
    provider = FakeProvider(issues)
    calls: list[list[str]] = []

    def launch(argv: list[str], _cwd: Path) -> FakeProcess:
        calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=1000 + issue)

    runner = SeriesRunner(
        config=config,
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=launch,
    )
    assert runner.run() == 0
    assert calls == [
        ["kaji", "run", member.workflow, str(member.issue)] for member in config.members
    ]


@pytest.mark.parametrize("fixture_name", ["epic-291.yaml", "standalone.yaml"])
def test_epic_and_standalone_fixtures_stop_and_resume_from_failure(
    tmp_path: Path, fixture_name: str
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "series" / fixture_name
    config = SeriesConfig.model_validate(yaml.safe_load(fixture.read_text(encoding="utf-8")))
    issues = {member.issue: _issue(member.issue) for member in config.members}
    provider = FakeProvider(issues)
    first_calls: list[list[str]] = []
    return_codes = iter([0, 0, 1])

    def fail_midway(argv: list[str], _cwd: Path) -> FakeProcess:
        first_calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=1000 + issue, returncode=next(return_codes))

    runner = SeriesRunner(
        config=config,
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=fail_midway,
    )
    with pytest.raises(SeriesAbortedError, match="member 285 failed: exit:1"):
        runner.run()
    expected_argv = [
        ["kaji", "run", member.workflow, str(member.issue)] for member in config.members
    ]
    assert first_calls == expected_argv[:3]
    stopped = SeriesState.load(runner.state_path)
    assert [member.status for member in stopped.members] == [
        "completed",
        "completed",
        "failed",
        "pending",
        "pending",
    ]

    resume_calls: list[list[str]] = []

    def complete_remaining(argv: list[str], _cwd: Path) -> FakeProcess:
        resume_calls.append(argv)
        issue = int(argv[3])
        provider.issues[issue] = _issue(issue, "closed", "completed")
        return FakeProcess(pid=2000 + issue)

    resumed = SeriesRunner(
        config=config,
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        provider=provider,  # type: ignore[arg-type]
        member_launcher=complete_remaining,
    )
    assert resumed.run(resume=True) == 0
    assert resume_calls == expected_argv[2:]
    final = SeriesState.load(resumed.state_path)
    assert final.status == "completed"
    assert all(member.status == "completed" for member in final.members)
