"""Sequential series runner with durable stop and resume semantics."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..errors import (
    LaunchRecordError,
    SeriesAbortedError,
    SeriesInputError,
    SeriesRuntimeError,
    WorkflowValidationError,
)
from ..interactive_terminal_herdr import inspect_agent_pane
from ..models import Workflow
from ..providers import IssueProvider
from ..recovery.classify import classify_failure
from ..recovery.gates import (
    AgentPaneState,
    LauncherRunIdWatcher,
    PaneInspector,
    RetryPlanner,
    RetryRecorder,
    find_retry_child_run_id,
    prepare_retry,
    release_launcher_record,
)
from ..recovery.handler import plan_recovery
from ..recovery.models import RecoveryDecision
from ..recovery.snapshot import FailureSnapshot
from ..runstate.facts import PaneRef
from ..runstate.launch import write_launch_record
from ..runstate.process import HostProbe, current_process_record
from ..workflow import load_workflow
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


def _default_pane_inspector(pane: PaneRef) -> AgentPaneState:
    """Inspect the failed step's Herdr pane read-only (gates 15 / 16)."""
    return inspect_agent_pane(pane.pane_id, origin_pane=pane.origin_pane, run_id=pane.run_id)


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
        pane_inspector: PaneInspector = _default_pane_inspector,
        probe: HostProbe | None = None,
    ):
        self.config = config
        self.repo_root = repo_root
        self.artifacts_dir = artifacts_dir
        self.provider = provider
        self.quiet = quiet
        self.member_launcher = member_launcher
        self.pid_alive = pid_alive
        self.pane_inspector = pane_inspector
        self.probe = probe

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
                issue = self.provider.view_issue(str(member.issue))
                if resume and member.status in ("failed", "interrupted"):
                    # Issue #540 (D8 / D9): 失敗 member は失敗 step から再開する。series の外で
                    # closed/completed になっていれば完了として進め、それ以外の closed は止める。
                    if issue.state == "closed":
                        gate = evaluate_member_gate(0, issue.state, issue.state_reason)
                        if not gate.success:
                            self._abort(
                                state,
                                f"member {member.issue} is already closed before its resumed "
                                f"run: {gate.gate}",
                            )
                        member.status = "completed"
                        member.gate = gate.gate
                        member.finished_at = datetime.now(UTC).isoformat()
                        state.save(self.state_path)
                        print(
                            f"series {self.config.id}: member {member.issue} completed "
                            f"outside the series gate={gate.gate}"
                        )
                        continue
                    self._resume_member(state, index)
                    continue
                if issue.state == "closed":
                    phase = "resumed" if resume else "fresh"
                    self._abort(
                        state,
                        f"member {member.issue} is already closed before its {phase} run",
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

    def _load_member_workflow(self, member: MemberState) -> Workflow:
        try:
            return load_workflow(self.repo_root / member.workflow)
        except (WorkflowValidationError, OSError) as exc:
            raise SeriesInputError(f"member {member.issue} workflow is invalid: {exc}") from exc

    def _stop_before_start(self, state: SeriesState, reason: str) -> None:
        """Stop without launching and without changing the member status (exit 3)."""
        state.status = "stopped"
        state.stop_reason = reason
        state.save(self.state_path)
        print(f"series {self.config.id}: stopped: {reason}")
        raise SeriesRuntimeError(reason)

    def _resume_member(self, state: SeriesState, index: int) -> None:
        """Retry a failed / interrupted member from its failed step behind the safety gates."""
        member = state.members[index]
        workflow = self._load_member_workflow(member)
        workflow_path = (self.repo_root / member.workflow).resolve()
        issue_id = str(member.issue)
        planner = RetryPlanner(
            workflow=workflow,
            artifacts_dir=self.artifacts_dir,
            issue_id=issue_id,
            pane_inspector=self.pane_inspector,
            probe=self.probe,
        )
        try:
            prepared = prepare_retry(planner, None)
        except LaunchRecordError as exc:
            self._stop_before_start(state, f"member {member.issue} could not start: {exc}")
        plan = prepared.plan

        def base_decision(snapshot: FailureSnapshot) -> RecoveryDecision:
            return plan_recovery(
                snapshot=snapshot,
                classification=classify_failure(snapshot),
                workflow=workflow,
                workflow_path=workflow_path,
                issue_id=issue_id,
                auto_recover=False,
                now=datetime.now(UTC),
            )

        recorder = RetryRecorder(
            plan=plan,
            trigger="series_resume",
            artifacts_dir=self.artifacts_dir,
            base_decision=base_decision,
        )
        if plan.refused:
            recorder.record_refused()
            for gate in plan.gates:
                print(gate.stderr_line(), file=sys.stderr)
            self._abort(
                state,
                f"member {member.issue} resume refused by safety gate: "
                f"gates={','.join(plan.gate_names())}",
            )
        assert plan.run_id is not None and plan.resume_from is not None
        assert plan.root_run_id is not None and prepared.launcher_record is not None
        try:
            recorder.record_launched()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            release_launcher_record(prepared.launcher_record)
            recorder.record_launch_failed()
            self._stop_before_start(state, f"member {member.issue} could not start: {exc}")
        extra_argv = [
            "--from",
            plan.resume_from,
            "--recovery-root",
            plan.root_run_id,
            "--recovery-parent",
            plan.run_id,
            "--recovery-trigger",
            "series_resume",
        ]
        print(
            f"series {self.config.id}: member {member.issue} resumes from {plan.resume_from} "
            f"(run {plan.run_id})"
        )
        self._execute_member(
            state,
            index,
            extra_argv=extra_argv,
            launcher_record=prepared.launcher_record,
            recorder=recorder,
            parent_run_id=plan.run_id,
        )

    def _execute_member(
        self,
        state: SeriesState,
        index: int,
        *,
        extra_argv: list[str] | None = None,
        launcher_record: Path | None = None,
        recorder: RetryRecorder | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        member = state.members[index]
        issue_id = str(member.issue)
        argv = ["kaji", "run", member.workflow, issue_id, *(extra_argv or [])]
        if self.quiet:
            argv.append("--quiet")
        if launcher_record is None:
            # Issue #540: Popen → child_pid 保存 → child の launch record の区間を親の記録で塞ぐ。
            try:
                launcher_record = write_launch_record(
                    self.artifacts_dir,
                    issue_id,
                    current_process_record("launcher", probe=self.probe),
                )
            except OSError as exc:
                self._stop_before_start(
                    state,
                    f"member {member.issue} could not start: cannot write launcher record: {exc}",
                )
        assert launcher_record is not None
        try:
            self._run_member_child(state, index, argv, launcher_record, recorder, parent_run_id)
        finally:
            warning = release_launcher_record(launcher_record)
            if warning:
                print(f"WARNING: {warning}", file=sys.stderr)

    def _run_member_child(
        self,
        state: SeriesState,
        index: int,
        argv: list[str],
        launcher_record: Path,
        recorder: RetryRecorder | None,
        parent_run_id: str | None,
    ) -> None:
        member = state.members[index]
        member.status = "running"
        member.started_at = datetime.now(UTC).isoformat()
        member.finished_at = None
        member.exit_code = None
        member.gate = None
        try:
            process = self.member_launcher(argv, self.repo_root)
        except OSError as exc:
            if recorder is not None:
                recorder.record_launch_failed()
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
        watcher = LauncherRunIdWatcher(
            self.artifacts_dir, str(member.issue), launcher_record, process.pid
        )
        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def interrupt_handler(signum: int, _frame: object) -> None:
            raise _SeriesSignalInterrupt(signal.Signals(signum).name)

        try:
            signal.signal(signal.SIGINT, interrupt_handler)
            signal.signal(signal.SIGTERM, interrupt_handler)
            with watcher:
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
        if recorder is not None and parent_run_id is not None:
            child_run_id = find_retry_child_run_id(
                self.artifacts_dir, str(member.issue), parent_run_id
            )
            member.run_id = child_run_id or member.run_id
            try:
                recorder.record_child_end(child_run_id=child_run_id, exit_code=exit_code)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"WARNING: cannot record the resume result: {exc}", file=sys.stderr)
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
