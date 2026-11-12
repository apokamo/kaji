"""Safety gates and the retry planner (Issue #540).

``_resume_point`` / ``_safety_gates`` / ``_non_resumable_skill_hits``（旧 ``handler.py``）を
``GateHit(name, detail)`` を返す公開関数として移設し、triage（``plan_recovery``）と
``kaji recover --retry-failed-step`` / ``kaji run-series --resume`` が共有する。

``RetryPlanner`` は ``runstate.facts`` の事実の上にゲート一覧（設計書「安全ゲート一覧」）を
評価し、拒否ゲートをすべて列挙する。``prepare_retry`` は Issue 単位の ``LaunchLock`` の中で
「ゲート判定 → launcher の launch record 書き込み」を行い、lock を解放して返す。
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

from ..errors import LaunchRecordError
from ..logger import RunLogger
from ..models import Workflow
from ..runstate import facts as runstate_facts
from ..runstate.launch import (
    LaunchLock,
    list_launch_records,
    remove_launch_record,
    set_launch_run_id,
    write_launch_record,
)
from ..runstate.process import HostProbe, ProcessRecordError, current_process_record
from .models import (
    NON_RESUMABLE_SKILLS,
    RECOVERY_FILE,
    RecoveryDecision,
    RecoveryWriteLock,
    read_recovery_json,
    write_recovery_json,
)
from .snapshot import FailureSnapshot, collect_snapshot, find_child_run_id

if TYPE_CHECKING:
    # runstate.facts imports recovery.classify, which initialises this package; importing
    # its names eagerly would fail when runstate.facts is the first module imported.
    from ..runstate.facts import IssueFacts, PaneRef, RunFacts

#: 自動再開を禁止する auth / secret / permission 形跡。
#: 単語境界を使うのは、``rate limit ... tokens per minute`` のような quota 文言で
#: 唯一の recovery budget を潰さないため（``tokens`` は ``\btoken\b`` に一致しない）。
SENSITIVE_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)permission denied"),
    re.compile(r"(?i)unauthorized"),
    re.compile(r"(?i)authentication failed"),
    re.compile(r"(?i)\btoken\b"),
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
]

#: retry / series resume を拒否する triage の cause（ゲート名 = cause）。
REFUSING_CAUSES = (
    "agent_declared_abort",
    "cycle_exhausted",
    "user_interrupted",
    "ambiguous_worktree_abort",
)

RetryTrigger = Literal["retry_failed_step", "series_resume"]
AgentPaneState = Literal["active", "shell_only", "absent", "not_owned", "unknown"]
PaneInspector = Callable[["PaneRef"], AgentPaneState]


@dataclass(frozen=True)
class GateHit:
    """拒否ゲート 1 件。``detail`` は ``retry-gate:`` 行の後半、``legacy`` は triage の表記。"""

    name: str
    detail: str = ""
    legacy: str = ""

    @property
    def triage_text(self) -> str:
        """triage（``recovery.json`` の evidence / reason）用の従来表記。"""
        return self.legacy or self.name

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "detail": self.detail}

    def stderr_line(self) -> str:
        """``^retry-gate: (?P<name>[a-z_]+)(?: (?P<detail>.*))?$`` 形式の 1 行。"""
        return (
            f"retry-gate: {self.name} {self.detail}" if self.detail else f"retry-gate: {self.name}"
        )


def resume_point(workflow: Workflow, failed_step: str | None) -> tuple[str | None, bool]:
    """再開 step と session 破棄フラグを返す。

    ``resume:`` を持つ step は保存済み session を引き継がずに再開すると
    ``MissingResumeSessionError`` で即死するため、session 生成元 step へ巻き戻す
    （Issue #288 決定 13）。未知 step は ``(None, False)``。
    """
    if not failed_step:
        return None, False
    step = workflow.find_step(failed_step)
    if step is None:
        return None, False
    if step.resume:
        return step.resume, True
    return step.id, False


def is_sensitive_failure_text(text: str) -> bool:
    """エラー文字列が auth / secret / permission 形跡を含むかを返す。"""
    return any(pattern.search(text) for pattern in SENSITIVE_FAILURE_PATTERNS)


def safety_gate_hits(snapshot: FailureSnapshot, resume_from: str | None) -> list[GateHit]:
    """triage と共有する安全ゲート（worktree / branch / provider / sensitive / artifact /
    失敗 step / 新しい run）を評価する。副作用 skill は ``non_resumable_skill_hits`` が担う。
    """
    gates: list[GateHit] = []
    if not snapshot.state_worktree_dir or not snapshot.git.available:
        gates.append(GateHit("worktree_unavailable", f"worktree={snapshot.state_worktree_dir}"))
    elif snapshot.git.branch != snapshot.state_branch_name:
        gates.append(
            GateHit(
                "branch_mismatch",
                f"worktree={snapshot.git.branch} state={snapshot.state_branch_name}",
                f"branch_mismatch (worktree={snapshot.git.branch}, "
                f"state={snapshot.state_branch_name})",
            )
        )
    if not snapshot.provider_available:
        gates.append(GateHit("provider_unavailable"))
    if is_sensitive_failure_text(snapshot.failure_error_text):
        gates.append(GateHit("sensitive_failure_pattern"))
    if snapshot.artifact_read_errors:
        gates.append(GateHit("artifact_unreadable"))
    if resume_from is None:
        gates.append(GateHit("unknown_failed_step"))
    if snapshot.newer_run_ids:
        newer = ", ".join(snapshot.newer_run_ids)
        gates.append(
            GateHit(
                "newer_run_detected",
                f"newer={','.join(snapshot.newer_run_ids)}",
                f"newer_run_detected ({newer})",
            )
        )
    return gates


def _step_skill(workflow: Workflow, step_id: str | None) -> str | None:
    if not step_id:
        return None
    step = workflow.find_step(step_id)
    return step.skill if step is not None else None


def non_resumable_skill_hits(
    workflow: Workflow, failed_step: str | None, resume_from: str | None
) -> list[GateHit]:
    """failed step と実再開先のうち、再開禁止 skill を実行するものを列挙する（Issue #349）。

    両方が同一 step を指す場合は二重計上しない。
    """
    targets = [("failed_step", failed_step)]
    if resume_from is not None and resume_from != failed_step:
        targets.append(("resume_from", resume_from))
    hits: list[GateHit] = []
    for label, step_id in targets:
        skill = _step_skill(workflow, step_id)
        if skill in NON_RESUMABLE_SKILLS:
            hits.append(
                GateHit(
                    "non_resumable_skill",
                    f"{label}={step_id} skill={skill}",
                    f"non_resumable_skill ({label}={step_id}, skill={skill})",
                )
            )
    return hits


def build_child_argv(
    *,
    workflow_path: Path,
    issue_id: str,
    resume_from: str,
    root_run_id: str,
    parent_run_id: str,
    workdir: Path,
    trigger: str | None = None,
) -> list[str]:
    """recovery child の argv。``kaji`` entry point ではなく module 実行で PATH 非依存にする。"""
    argv = [
        sys.executable,
        "-m",
        "kaji_harness.cli_main",
        "run",
        str(workflow_path),
        issue_id,
        "--from",
        resume_from,
        "--recovery-root",
        root_run_id,
        "--recovery-parent",
        parent_run_id,
    ]
    if trigger is not None:
        argv += ["--recovery-trigger", trigger]
    return [*argv, "--workdir", str(workdir)]


@dataclass
class RetryPlan:
    """ゲート判定の結果。``gates`` が空なら通過。"""

    issue_id: str
    run_id: str | None
    gates: list[GateHit] = field(default_factory=list)
    resume_from: str | None = None
    discarded_resume_session: bool = False
    root_run_id: str | None = None
    facts: RunFacts | None = None
    issue_facts: IssueFacts | None = None

    @property
    def refused(self) -> bool:
        return bool(self.gates)

    @property
    def run_dir(self) -> Path | None:
        return self.facts.run_dir if self.facts is not None else None

    def gate_names(self) -> list[str]:
        return [gate.name for gate in self.gates]


def _pane_gate(facts: RunFacts, inspector: PaneInspector) -> GateHit | None:
    if facts.pane_unreadable:
        return GateHit("agent_pane_unverifiable", "pane-metadata=unreadable")
    pane = facts.pane
    if pane is None:
        return None
    state = inspector(pane)
    if state == "active":
        return GateHit("agent_pane_alive", f"pane={pane.pane_id}")
    if state == "unknown":
        return GateHit("agent_pane_unverifiable", f"pane={pane.pane_id}")
    return None


@dataclass
class RetryPlanner:
    """安全ゲートを評価して ``RetryPlan`` を返す（副作用なし。lock は ``prepare_retry``）。

    Attributes:
        workflow: 指定 workflow。
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        issue_id: canonical Issue ID。
        pane_inspector: Herdr pane の照会（読み取り専用）。
        probe: 生存判定の OS 境界の注入点。
    """

    workflow: Workflow
    artifacts_dir: Path
    issue_id: str
    pane_inspector: PaneInspector
    probe: HostProbe | None = None

    def plan(self, target_run_id: str | None = None) -> RetryPlan:
        """ゲートを評価する。拒否ゲートは評価順にすべて列挙する。"""
        issue = runstate_facts.collect_issue_facts(
            self.artifacts_dir,
            self.issue_id,
            probe=self.probe,
            probe_git=True,
            target_run_id=target_run_id,
        )
        latest_id = issue.run_ids[-1] if issue.run_ids else None
        run = issue.latest_run
        plan = RetryPlan(
            issue_id=self.issue_id,
            run_id=run.run_id if run is not None else target_run_id,
            facts=run,
            issue_facts=issue,
        )
        if run is None:
            plan.gates.append(GateHit("no_target_run"))
            self._issue_gates(plan, issue, None)
            return plan
        if latest_id is not None and run.run_id != latest_id:
            plan.gates.append(
                GateHit(
                    "newer_run_detected",
                    f"newer={','.join(r for r in issue.run_ids if r > run.run_id)}",
                )
            )
        if run.liveness.alive is True:
            plan.gates.append(GateHit("run_alive", f"pid={run.pid} basis={run.liveness.basis}"))
        elif run.liveness.alive is None:
            plan.gates.append(GateHit("liveness_unknown", f"basis={run.liveness.basis}"))
        self._issue_gates(plan, issue, run.run_id)
        if run.termination == "ended" and run.end_status not in ("ERROR", "ABORT"):
            plan.gates.append(GateHit("run_not_failed", f"end_status={run.end_status}"))
        if run.blocking_errors:
            paths = ",".join(error.path for error in run.blocking_errors)
            plan.gates.append(GateHit("artifact_unreadable", f"paths={paths}"))
        if run.workflow is not None and run.workflow != self.workflow.name:
            plan.gates.append(
                GateHit("workflow_mismatch", f"run={run.workflow} requested={self.workflow.name}")
            )
        try:
            self._failure_gates(plan, run)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # 防御: 破損 artifact に由来する値でゲート評価が失敗しても例外を漏らさず、
            # 拒否（artifact_unreadable）に倒す。resume 地点は信頼できないので捨てる。
            plan.resume_from = None
            if not any(g.name == "artifact_unreadable" for g in plan.gates):
                plan.gates.append(
                    GateHit("artifact_unreadable", f"failure gates: {type(exc).__name__}")
                )
        pane_hit = _pane_gate(run, self.pane_inspector)
        if pane_hit is not None:
            plan.gates.append(pane_hit)
        root = run.recovery.root_run_id if run.recovery is not None else run.run_id
        plan.root_run_id = root
        return plan

    def _issue_gates(self, plan: RetryPlan, issue: IssueFacts, target: str | None) -> None:
        others: list[tuple[str, bool | None, str]] = [
            (run_id, liveness.alive, liveness.basis)
            for run_id, liveness in issue.run_liveness.items()
            if run_id != target
        ]
        for entry, liveness in issue.launches:
            pointed = entry.record.run_id if entry.record is not None else None
            if pointed is not None and pointed == target:
                names = {g.name for g in plan.gates}
                if liveness.alive is True and "run_alive" not in names:
                    plan.gates.append(GateHit("run_alive", f"launch={entry.path.name}"))
                elif liveness.alive is None and not names & {"run_alive", "liveness_unknown"}:
                    # 対象 run を指す launcher の生存を確認できない（別ホスト / 照合不能）。
                    # 対象 run 自身の死亡確認だけで再起動せず、拒否に倒す。
                    plan.gates.append(
                        GateHit(
                            "liveness_unknown",
                            f"launch={entry.path.name} basis={liveness.basis}",
                        )
                    )
                continue
            others.append((entry.path.name, liveness.alive, liveness.basis))
        alive = [name for name, value, _ in others if value is True]
        unknown = [name for name, value, _ in others if value is None]
        if alive:
            plan.gates.append(GateHit("issue_run_alive", f"alive={','.join(alive)}"))
        elif unknown:
            plan.gates.append(GateHit("issue_liveness_unknown", f"unknown={','.join(unknown)}"))

    def _failure_gates(self, plan: RetryPlan, run: RunFacts) -> None:
        snapshot = run.snapshot
        failure = run.failure
        # snapshot は失敗した run（ERROR / ABORT 終端、または vanished）にだけ作られる
        if snapshot is None:
            return
        resume_from, discarded = resume_point(self.workflow, run.failed_step)
        plan.resume_from = resume_from
        plan.discarded_resume_session = discarded
        if resume_from is None:
            plan.gates.append(GateHit("unknown_failed_step", f"failed_step={run.failed_step}"))
        plan.gates.extend(non_resumable_skill_hits(self.workflow, run.failed_step, resume_from))
        if failure is not None and failure.cause in REFUSING_CAUSES:
            plan.gates.append(GateHit(failure.cause))
        if is_sensitive_failure_text(snapshot.failure_error_text):
            plan.gates.append(GateHit("sensitive_failure_pattern"))
        if not snapshot.state_worktree_dir or not snapshot.git.available:
            plan.gates.append(
                GateHit("worktree_unavailable", f"worktree={snapshot.state_worktree_dir}")
            )
        elif snapshot.git.branch != snapshot.state_branch_name:
            plan.gates.append(
                GateHit(
                    "branch_mismatch",
                    f"worktree={snapshot.git.branch} state={snapshot.state_branch_name}",
                )
            )
        if not snapshot.provider_available:
            plan.gates.append(GateHit("provider_unavailable"))


@dataclass
class PreparedRetry:
    """``prepare_retry`` の結果。通過時は launcher record を書き終えて lock を解放済み。"""

    plan: RetryPlan
    launcher_record: Path | None = None


def prepare_retry(
    planner: RetryPlanner,
    target_run_id: str | None,
    *,
    after_plan: Callable[[RetryPlan], None] | None = None,
) -> PreparedRetry:
    """LaunchLock の中で ゲート判定 → launcher record 書き込み を行う。

    Args:
        planner: ゲートを評価する planner。
        target_run_id: 対象 run（``None`` は最新 run）。
        after_plan: lock 保持中にゲート判定直後に呼ぶ同期点（テスト用）。

    Returns:
        拒否時は ``launcher_record=None``。lock が取れなければ ``concurrent_launch`` だけを返す。

    Raises:
        LaunchRecordError: lock file を開けない、または launcher record を書けない場合
            （child を起動してはならない）。
    """
    lock = LaunchLock(planner.artifacts_dir, planner.issue_id)
    try:
        acquired = lock.acquire()
    except OSError as exc:
        raise LaunchRecordError(f"cannot open launch lock {lock.path}: {exc}") from exc
    if not acquired:
        plan = RetryPlan(issue_id=planner.issue_id, run_id=target_run_id)
        plan.gates.append(GateHit("concurrent_launch", f"lock={lock.path.name}"))
        return PreparedRetry(plan)
    try:
        plan = planner.plan(target_run_id)
        if after_plan is not None:
            after_plan(plan)
        if plan.refused:
            return PreparedRetry(plan)
        try:
            path = write_launch_record(
                planner.artifacts_dir,
                planner.issue_id,
                current_process_record("launcher", probe=planner.probe),
            )
        except OSError as exc:
            raise LaunchRecordError(f"cannot write launcher record: {exc}") from exc
        return PreparedRetry(plan, path)
    finally:
        lock.release()


# --- 記録（recovery.json の retries / run.log） ---


BaseDecisionFactory = Callable[[FailureSnapshot], RecoveryDecision]


def _base_snapshot(plan: RetryPlan, artifacts_dir: Path) -> FailureSnapshot | None:
    """``recovery.json`` が無い run の土台 decision に使う snapshot。"""
    run_dir = plan.run_dir
    if run_dir is None:
        return None
    if plan.facts is not None and plan.facts.snapshot is not None:
        return plan.facts.snapshot
    return collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        issue_id=plan.issue_id,
        provider_available=True,
        allow_unterminated=True,
        probe_git=False,
    )


@dataclass
class RetryRecorder:
    """対象 run の ``recovery.json`` の ``retries`` と ``run.log`` へ記録する。"""

    plan: RetryPlan
    trigger: RetryTrigger
    artifacts_dir: Path
    #: ``recovery.json`` が無い run の土台 decision（triage と同じ ``plan_recovery``）を作る。
    base_decision: BaseDecisionFactory
    requested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    #: この要求の entry を識別する ID（child 終了時に自分の entry だけを更新するため）。
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def _entry(self, outcome: str, **extra: Any) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "requested_at": self.requested_at,
            "trigger": self.trigger,
            "outcome": outcome,
            "gates": [gate.to_dict() for gate in self.plan.gates],
            "resume_from": self.plan.resume_from,
            "discarded_resume_session": self.plan.discarded_resume_session,
            "child_run_id": extra.get("child_run_id"),
            "child_exit_code": extra.get("child_exit_code"),
        }

    def _write(self, entry: dict[str, Any], *, replace_own: bool) -> None:
        """``retries`` を排他下で read-modify-write する。

        ``replace_own`` なら同じ ``request_id`` の entry を置き換え（無ければ追記）、
        他の要求が追記した entry は保持する。
        """
        run_dir = self.plan.run_dir
        if run_dir is None:
            return
        path = run_dir / RECOVERY_FILE
        with RecoveryWriteLock(run_dir):
            if path.is_file():
                decision = read_recovery_json(path)
            else:
                snapshot = _base_snapshot(self.plan, self.artifacts_dir)
                if snapshot is None:
                    return
                decision = self.base_decision(snapshot)
            retries = list(decision.retries)
            index = next(
                (
                    i
                    for i in range(len(retries) - 1, -1, -1)
                    if retries[i].get("request_id") == self.request_id
                ),
                None,
            )
            if replace_own and index is not None:
                retries[index] = entry
            else:
                retries.append(entry)
            write_recovery_json(path, replace(decision, retries=retries))

    def _logger(self) -> RunLogger | None:
        run_dir = self.plan.run_dir
        return RunLogger(log_path=run_dir / "run.log") if run_dir is not None else None

    def record_refused(self) -> None:
        """拒否を記録する（best-effort。読めない artifact があっても例外にしない）。"""
        logger = self._logger()
        try:
            self._write(self._entry("refused"), replace_own=False)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # 拒否の結果（exit 4 + stderr）は記録の成否で変わらない
        if logger is not None and self.plan.run_id is not None:
            try:
                logger.log_retry_refused(
                    trigger=self.trigger,
                    run_id=self.plan.run_id,
                    gates=[gate.to_dict() for gate in self.plan.gates],
                )
            except OSError:
                pass  # 同上

    def record_launched(self) -> None:
        """起動直前の記録。失敗は呼び出し側で child を起動せず exit 3 にする。

        Raises:
            OSError: ``recovery.json`` / ``run.log`` を書けない場合。
            ValueError: ``recovery.json`` が読めない場合。
        """
        self._write(self._entry("launched"), replace_own=False)
        logger = self._logger()
        assert logger is not None and self.plan.run_id is not None
        logger.log_retry_requested(
            trigger=self.trigger,
            run_id=self.plan.run_id,
            resume_from=self.plan.resume_from,
            outcome="launched",
        )

    def record_launch_failed(self) -> None:
        """起動前の失敗を best-effort で記録する。"""
        try:
            self._write(self._entry("launch_failed"), replace_own=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # 起動失敗の exit 3 は記録の成否で変わらない

    def record_child_end(self, *, child_run_id: str | None, exit_code: int | None) -> None:
        """child 終了後の結果を記録する（失敗は warning 扱いで呼び出し側が続行する）。

        Raises:
            OSError: 書き込みに失敗した場合。
        """
        self._write(
            self._entry("launched", child_run_id=child_run_id, child_exit_code=exit_code),
            replace_own=True,
        )
        logger = self._logger()
        if logger is not None:
            logger.log_retry_child_end(
                trigger=self.trigger, child_run_id=child_run_id, exit_code=exit_code
            )


class ChildProcess(Protocol):
    """child run のプロセス（``subprocess.Popen`` 互換の最小 interface）。"""

    pid: int

    def wait(self) -> int:
        """終了を待って exit code を返す。"""
        ...


ChildPopen = Callable[[list[str], Path], ChildProcess]


def default_child_popen(argv: list[str], cwd: Path) -> ChildProcess:
    """child run を前面（標準入出力を継承）で起動する。"""
    return subprocess.Popen(argv, cwd=cwd)


class LauncherRunIdWatcher:
    """child の ``kaji run`` が自分の launch record に run_id を設定したら、launcher record にも
    同じ run_id を設定する（``launching`` を run dir 採番後に false にするため）。
    """

    def __init__(
        self, artifacts_dir: Path, issue_id: str, launcher_record: Path, child_pid: int
    ) -> None:
        self._artifacts_dir = artifacts_dir
        self._issue_id = issue_id
        self._launcher_record = launcher_record
        self._child_pid = child_pid
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.run_id: str | None = None

    def __enter__(self) -> LauncherRunIdWatcher:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def poll_once(self) -> bool:
        """1 回照会し、設定できたら ``True``。"""
        for entry in list_launch_records(self._artifacts_dir, self._issue_id):
            record = entry.record
            if record is None or record.role != "run" or record.pid != self._child_pid:
                continue
            if record.run_id is None:
                return False
            try:
                set_launch_run_id(self._launcher_record, record.run_id)
            except (OSError, ProcessRecordError):
                return False
            self.run_id = record.run_id
            return True
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.poll_once():
                return
            self._stop.wait(0.5)


def release_launcher_record(path: Path | None) -> str | None:
    """launcher record を削除する。失敗は warning 文字列で返す（呼び出し側が表示）。"""
    if path is None:
        return None
    try:
        remove_launch_record(path)
    except OSError as exc:
        return f"cannot remove launcher record {path}: {exc}"
    return None


def find_retry_child_run_id(artifacts_dir: Path, issue_id: str, parent_run_id: str) -> str | None:
    """``recovery-chain.json.parent_run_id`` が対象 run の child run を探す。"""
    runs_dir = artifacts_dir / issue_id / "runs"
    if not runs_dir.is_dir():
        return None
    return find_child_run_id(runs_dir, parent_run_id)
