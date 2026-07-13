"""Sequential series runner with durable stop and resume semantics."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..errors import SeriesAbortedError, SeriesInputError, SeriesRuntimeError
from ..providers import IssueProvider
from .lock import SeriesLock
from .models import SeriesConfig, evaluate_member_gate, series_fingerprint
from .state import MemberState, SeriesState


class ChildProcess(Protocol):
    """Minimal child process interface required by the runner."""

    pid: int

    def wait(self) -> int:
        """Wait for completion and return the child exit code."""
        ...

    def terminate(self) -> None:
        """Request graceful child termination."""
        ...


MemberLauncher = Callable[[list[str], Path], ChildProcess]
PidAlive = Callable[[int], bool]


class _SeriesSignalInterrupt(Exception):
    """Internal control flow raised from SIGINT/SIGTERM handlers."""


def _default_member_launcher(argv: list[str], cwd: Path) -> ChildProcess:
    """Launch one existing ``kaji run`` command with inherited streams."""
    return subprocess.Popen(argv, cwd=cwd)


def _default_pid_alive(pid: int) -> bool:
    """Conservatively report whether a saved child PID may still be alive."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


class SeriesRunner:
    """Run configured members one at a time and persist every transition."""

    def __init__(
        self,
        *,
        config: SeriesConfig,
        repo_root: Path,
        artifacts_dir: Path,
        provider: IssueProvider,
        quiet: bool = False,
        member_launcher: MemberLauncher = _default_member_launcher,
        pid_alive: PidAlive = _default_pid_alive,
    ):
        self.config = config
        self.repo_root = repo_root
        self.artifacts_dir = artifacts_dir
        self.provider = provider
        self.quiet = quiet
        self.member_launcher = member_launcher
        self.pid_alive = pid_alive

    @property
    def state_dir(self) -> Path:
        """Return this series' artifact directory."""
        return self.artifacts_dir / "_series" / self.config.id

    @property
    def state_path(self) -> Path:
        """Return this series' durable state path."""
        return self.state_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        """Return this series' advisory lock path."""
        return self.state_dir / "lock"

    def run(self, *, resume: bool = False) -> int:
        """Execute or resume the series, returning zero only after all members complete."""
        with SeriesLock(self.lock_path):
            state = self._prepare_state(resume=resume)
            for index, member in enumerate(state.members):
                if member.status == "completed":
                    continue
                if not resume:
                    issue = self.provider.view_issue(str(member.issue))
                    if issue.state == "closed":
                        self._abort(
                            state,
                            f"member {member.issue} is already closed before its fresh run",
                        )
                self._execute_member(state, index)
            state.status = "completed"
            state.stop_reason = None
            state.save(self.state_path)
        return 0

    def _prepare_state(self, *, resume: bool) -> SeriesState:
        if resume:
            if not self.state_path.is_file():
                raise SeriesInputError(f"resume state not found: {self.state_path}")
            state = SeriesState.load(self.state_path)
            expected = series_fingerprint(self.config)
            if state.fingerprint != expected:
                raise SeriesInputError(
                    "series fingerprint differs from persisted state; use a new series id "
                    "or explicitly remove the old state"
                )
            expected_members = [(member.issue, member.workflow) for member in self.config.members]
            state_members = [(member.issue, member.workflow) for member in state.members]
            if state.series_id != self.config.id or state_members != expected_members:
                raise SeriesInputError(
                    "persisted state members do not match the validated series definition"
                )
            self._reconcile_resume(state)
            state.status = "running"
            state.stop_reason = None
            state.save(self.state_path)
            return state
        if self.state_path.exists():
            raise SeriesInputError(
                f"state already exists for series {self.config.id!r}; pass --resume"
            )
        state = SeriesState.create(self.config)
        state.save(self.state_path)
        return state

    def _reconcile_resume(self, state: SeriesState) -> None:
        for member in state.members:
            if member.status == "completed":
                issue = self.provider.view_issue(str(member.issue))
                gate = evaluate_member_gate(0, issue.state, issue.state_reason)
                if not gate.success:
                    self._abort(
                        state,
                        f"completed member {member.issue} rolled back: {gate.gate}",
                    )
                continue
            if member.status == "running":
                if member.child_pid is not None and self.pid_alive(member.child_pid):
                    raise SeriesInputError(
                        f"member {member.issue} child pid {member.child_pid} is still alive"
                    )
                issue = self.provider.view_issue(str(member.issue))
                gate = evaluate_member_gate(0, issue.state, issue.state_reason)
                if gate.success:
                    member.status = "completed"
                    member.gate = gate.gate
                    member.finished_at = datetime.now(UTC).isoformat()
                else:
                    member.status = "interrupted"
                    member.gate = gate.gate
                    member.finished_at = datetime.now(UTC).isoformat()
                state.save(self.state_path)
            break

    def _execute_member(self, state: SeriesState, index: int) -> None:
        member = state.members[index]
        argv = ["kaji", "run", member.workflow, str(member.issue)]
        if self.quiet:
            argv.append("--quiet")
        member.status = "running"
        member.started_at = datetime.now(UTC).isoformat()
        member.finished_at = None
        member.exit_code = None
        member.gate = None
        try:
            process = self.member_launcher(argv, self.repo_root)
        except OSError as exc:
            member.status = "failed"
            member.finished_at = datetime.now(UTC).isoformat()
            reason = f"member {member.issue} could not start: {exc}"
            state.status = "stopped"
            state.stop_reason = reason
            state.save(self.state_path)
            raise SeriesRuntimeError(reason) from exc
        member.child_pid = process.pid
        state.save(self.state_path)
        print(f"series {self.config.id}: member {member.issue} started pid={process.pid}")
        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def interrupt_handler(signum: int, _frame: object) -> None:
            raise _SeriesSignalInterrupt(signal.Signals(signum).name)

        try:
            signal.signal(signal.SIGINT, interrupt_handler)
            signal.signal(signal.SIGTERM, interrupt_handler)
            exit_code = process.wait()
        except (KeyboardInterrupt, _SeriesSignalInterrupt) as exc:
            process.terminate()
            exit_code = process.wait()
            member.exit_code = exit_code
            print(f"series {self.config.id}: member {member.issue} exited code={exit_code}")
            member.status = "interrupted"
            member.finished_at = datetime.now(UTC).isoformat()
            reason = f"member {member.issue} interrupted by signal"
            state.status = "stopped"
            state.stop_reason = reason
            state.save(self.state_path)
            raise SeriesAbortedError(reason) from exc
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)
        member.exit_code = exit_code
        print(f"series {self.config.id}: member {member.issue} exited code={exit_code}")
        member.run_id = self._find_member_run_id(member)
        member.finished_at = datetime.now(UTC).isoformat()
        if exit_code != 0:
            member.gate = f"exit:{exit_code}"
            member.status = "failed"
            self._abort(state, f"member {member.issue} failed: {member.gate}")
        issue = self.provider.view_issue(str(member.issue))
        gate = evaluate_member_gate(exit_code, issue.state, issue.state_reason)
        member.gate = gate.gate
        if not gate.success:
            member.status = "failed"
            self._abort(state, f"member {member.issue} gate failed: {gate.gate}")
        member.status = "completed"
        state.save(self.state_path)
        print(f"series {self.config.id}: member {member.issue} completed gate={gate.gate}")

    def _find_member_run_id(self, member: MemberState) -> str | None:
        """Best-effort locate the newest run artifact created after member start."""
        runs_dir = self.artifacts_dir / str(member.issue) / "runs"
        if not runs_dir.is_dir() or member.started_at is None:
            return None
        started = datetime.fromisoformat(member.started_at).timestamp()
        candidates: list[tuple[float, str]] = []
        try:
            for path in runs_dir.iterdir():
                # Some filesystems expose directory mtimes at one-second precision.
                if path.is_dir() and path.stat().st_mtime >= started - 1.0:
                    candidates.append((path.stat().st_mtime, path.name))
        except OSError:
            return None
        return max(candidates)[1] if candidates else None

    def _abort(self, state: SeriesState, reason: str) -> None:
        state.status = "stopped"
        state.stop_reason = reason
        state.save(self.state_path)
        print(f"series {self.config.id}: stopped: {reason}")
        raise SeriesAbortedError(reason)
