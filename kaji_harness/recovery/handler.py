"""Recovery decision planner and handler orchestrator (Issue #288).

``plan_recovery()`` は snapshot + classification から ``RecoveryDecision`` を導く純関数
（fs / provider / subprocess に触れない）。``RecoveryHandler`` はその decision を根拠に
``recovery.json`` / ``run.log`` / Issue コメント / stderr サマリへ証跡を固定し、
``decision: resume`` のときだけ固定ウェイト後に child run を 1 回起動する。

自動再開の budget は **recovery chain 単位で 1**。budget 判定は「自 run が recovery
child か」だけで機械的に決まるため、scan も counter も要らず、「別 run_id だからもう
1 回」という抜け道が構造的に消える（決定 1 / 4）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING

from ..logger import RunLogger
from ..models import Workflow
from .classify import classify_failure
from .models import (
    NON_RESUMABLE_STEPS,
    RECOVERY_FILE,
    RECOVERY_WAIT_SECONDS,
    FailureClassification,
    RecoveryDecision,
    RecoveryDecisionValue,
    derive_child_final_status,
    write_recovery_json,
)
from .report import render_stderr_summary, render_triage_comment, sanitize_evidence
from .snapshot import FailureSnapshot, collect_snapshot, find_child_run_id, list_newer_run_ids

if TYPE_CHECKING:  # pragma: no cover
    from ..providers import IssueProvider

_logger = logging.getLogger(__name__)

#: 自動再開を禁止する auth / secret / permission 形跡。
#: 単語境界を使うのは、``rate limit ... tokens per minute`` のような quota 文言で
#: 唯一の recovery budget を潰さないため（``tokens`` は ``\btoken\b`` に一致しない）。
_SENSITIVE_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)permission denied"),
    re.compile(r"(?i)unauthorized"),
    re.compile(r"(?i)authentication failed"),
    re.compile(r"(?i)\btoken\b"),
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
]

#: 非 candidate cause の decision mapping。列挙外は ``not_resumable``。
_COMMENT_ONLY_CAUSES = frozenset(
    {"agent_declared_abort", "runtime_error", "unknown_external_error", "external_upstream_anomaly"}
)

ChildLauncher = Callable[[list[str], Path], int]


@dataclass(frozen=True)
class RecoveryResult:
    """handler の実行結果。``child_exit_code`` は child run を起動した場合のみ非 None。"""

    decision: RecoveryDecision
    child_exit_code: int | None = None


def _resume_point(workflow: Workflow, failed_step: str | None) -> tuple[str | None, bool]:
    """再開 step と session 破棄フラグを返す。

    ``resume:`` を持つ step は保存済み session を引き継がずに再開すると
    ``MissingResumeSessionError`` で即死するため、session 生成元 step へ巻き戻す
    （決定 13）。未知 step は ``(None, False)``。
    """
    if not failed_step:
        return None, False
    step = workflow.find_step(failed_step)
    if step is None:
        return None, False
    if step.resume:
        return step.resume, True
    return step.id, False


def _sensitive_failure_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SENSITIVE_FAILURE_PATTERNS)


def _safety_gates(snapshot: FailureSnapshot, resume_from: str | None) -> list[str]:
    """自動再開を止める gate 名を列挙する（空なら通過）。"""
    gates: list[str] = []
    if not snapshot.state_worktree_dir or not snapshot.git.available:
        gates.append("worktree_unavailable")
    elif snapshot.git.branch != snapshot.state_branch_name:
        gates.append(
            f"branch_mismatch (worktree={snapshot.git.branch}, state={snapshot.state_branch_name})"
        )
    if not snapshot.provider_available:
        gates.append("provider_unavailable")
    if _sensitive_failure_text(snapshot.failure_error_text):
        gates.append("sensitive_failure_pattern")
    if snapshot.artifact_read_errors:
        gates.append("artifact_unreadable")
    if resume_from is None:
        gates.append("unknown_failed_step")
    elif snapshot.failed_step in NON_RESUMABLE_STEPS or resume_from in NON_RESUMABLE_STEPS:
        gates.append("non_resumable_step")
    if snapshot.newer_run_ids:
        gates.append(f"newer_run_detected ({', '.join(snapshot.newer_run_ids)})")
    return gates


def _build_resume_command(
    *, workflow_path: Path, issue_id: str, resume_from: str, root_run_id: str, run_id: str
) -> str:
    return (
        f"kaji run {workflow_path} {issue_id} --from {resume_from} "
        f"--recovery-root {root_run_id} --recovery-parent {run_id}"
    )


def plan_recovery(
    *,
    snapshot: FailureSnapshot,
    classification: FailureClassification,
    workflow: Workflow,
    workflow_path: Path,
    issue_id: str,
    auto_recover: bool,
    now: datetime,
) -> RecoveryDecision:
    """snapshot と classification から ``RecoveryDecision`` を決定する（純関数）。

    判定は上から順に確定する:

    1. ``kaji_bug_suspected`` かつ根拠 artifact を列挙できる → ``bug_issue_created``
    2. ``recoverability_hint != candidate`` → cause 別の ``comment_only`` / ``not_resumable``
    3. budget guard（自身が recovery child）→ ``exhausted``
    4. safety gate 抵触 → ``not_resumable``（抵触 gate を evidence に記録）
    5. ``auto_recover`` 無効 → ``comment_only``（``resume_command`` は提示する）
    6. すべて通過 → ``resume``（``resume_scheduled_at`` を確定）
    """
    root_run_id = snapshot.recovery_root_run_id or snapshot.run_id
    evidence = list(snapshot.evidence)

    def build(
        decision: RecoveryDecisionValue,
        *,
        recoverable: bool,
        reason: str,
        resume_from: str | None = None,
        discarded: bool = False,
        resume_command: str | None = None,
        resume_scheduled_at: str | None = None,
        extra_evidence: list[str] | None = None,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            run_id=snapshot.run_id,
            recoverable=recoverable,
            decision=decision,
            classification=classification,
            failed_step=snapshot.failed_step,
            resume_from=resume_from,
            resume_mode="from" if resume_from else None,
            resume_command=resume_command,
            reason=reason,
            evidence=evidence + (extra_evidence or []),
            recovery_parent_run_id=snapshot.recovery_parent_run_id,
            recovery_root_run_id=root_run_id,
            resume_scheduled_at=resume_scheduled_at,
            discarded_resume_session=discarded,
            workflow_path=str(workflow_path),
        )

    if classification.cause == "kaji_bug_suspected":
        if evidence:
            return build(
                "bug_issue_created",
                recoverable=False,
                reason="deterministic contradiction between run artifacts and runner events",
            )
        return build(
            "not_resumable",
            recoverable=False,
            reason="kaji bug suspected but no artifact path could be enumerated",
        )

    if classification.recoverability_hint != "candidate":
        decision: RecoveryDecisionValue = (
            "comment_only" if classification.cause in _COMMENT_ONLY_CAUSES else "not_resumable"
        )
        return build(
            decision,
            recoverable=False,
            reason=f"cause {classification.cause} is not an auto-resume candidate",
        )

    if snapshot.is_recovery_child:
        return build(
            "exhausted",
            recoverable=False,
            reason="recovery budget (1 per recovery chain) already consumed by this chain",
        )

    resume_from, discarded = _resume_point(workflow, snapshot.failed_step)
    gates = _safety_gates(snapshot, resume_from)
    if gates:
        return build(
            "not_resumable",
            recoverable=False,
            reason=f"safety gate blocked auto recovery: {', '.join(gates)}",
            extra_evidence=[f"safety gate: {gate}" for gate in gates],
        )
    assert resume_from is not None  # gate `unknown_failed_step` で排除済み

    command = _build_resume_command(
        workflow_path=workflow_path,
        issue_id=issue_id,
        resume_from=resume_from,
        root_run_id=root_run_id,
        run_id=snapshot.run_id,
    )
    if not auto_recover:
        return build(
            "comment_only",
            recoverable=True,
            reason="auto recovery is disabled; resume command is offered as a manual next action",
            resume_from=resume_from,
            discarded=discarded,
            resume_command=command,
        )

    scheduled = (now + timedelta(seconds=RECOVERY_WAIT_SECONDS)).isoformat()
    return build(
        "resume",
        recoverable=True,
        reason=f"{classification.cause} is a recoverable candidate; new session can continue",
        resume_from=resume_from,
        discarded=discarded,
        resume_command=command,
        resume_scheduled_at=scheduled,
    )


def _default_child_launcher(argv: list[str], cwd: Path) -> int:
    """child run を subprocess として起動し、その exit code を返す。"""
    return subprocess.run(argv, cwd=cwd).returncode


@dataclass
class RecoveryHandler:
    """failure triage を実行し、必要なら 1 回だけ child run を起動する orchestrator。

    ``wait_seconds`` / ``sleep`` / ``child_launcher`` はテスト注入点。config / CLI へは
    露出しない（wait は決定 9 により固定値）。
    """

    workflow: Workflow
    workflow_path: Path
    issue_id: str
    issue_ref: str
    artifacts_dir: Path
    run_dir: Path
    workdir: Path
    provider: IssueProvider | None
    auto_recover: bool
    wait_seconds: int = RECOVERY_WAIT_SECONDS
    # 関数を dataclass の直接 default にすると descriptor として bound method 化される。
    # default_factory 経由で instance attribute として束縛する。
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)
    child_launcher: ChildLauncher = field(default_factory=lambda: _default_child_launcher)
    stderr: IO[str] = field(default_factory=lambda: sys.stderr)
    _run_logger: RunLogger = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._run_logger = RunLogger(log_path=self.run_dir / "run.log")

    @property
    def _runs_dir(self) -> Path:
        return self.run_dir.parent

    def run(self) -> RecoveryResult:
        """triage を実行し、``decision: resume`` なら child run まで面倒を見る。"""
        snapshot = collect_snapshot(
            run_dir=self.run_dir,
            artifacts_dir=self.artifacts_dir,
            issue_id=self.issue_id,
            provider_available=self.provider is not None,
        )
        classification = classify_failure(snapshot)
        decision = plan_recovery(
            snapshot=snapshot,
            classification=classification,
            workflow=self.workflow,
            workflow_path=self.workflow_path,
            issue_id=self.issue_id,
            auto_recover=self.auto_recover,
            now=datetime.now(UTC),
        )
        self._record(decision)

        if decision.decision == "bug_issue_created":
            decision = self._create_bug_issue(decision)
            self._record(decision)

        decision = self._post_triage_comment(decision)
        self._record(decision)
        self.stderr.write(render_stderr_summary(decision))

        if decision.decision != "resume":
            return RecoveryResult(decision)
        return self._resume(decision)

    # --- 証跡 ---

    def _record(self, decision: RecoveryDecision) -> None:
        """``recovery.json`` を上書きし、``run.log`` に ``recovery_decision`` を追記する。"""
        try:
            write_recovery_json(self.run_dir / RECOVERY_FILE, decision)
        except OSError as exc:
            _logger.warning("recovery.json write failed (%s): %s", self.run_dir, exc)
        self._run_logger.log_recovery_decision(decision)

    def _create_bug_issue(self, decision: RecoveryDecision) -> RecoveryDecision:
        """矛盾の根拠を添えて bug issue を起票する。失敗時は ``not_resumable`` へ降格。"""
        if self.provider is None:
            return replace(
                decision,
                decision="not_resumable",
                reason="kaji bug suspected but no provider is available to file a bug issue",
            )
        title = f"bug: kaji harness inconsistency in run {decision.run_id} ({self.issue_ref})"
        body = self._bug_issue_body(decision)
        try:
            issue = self.provider.create_issue(title=title, body=body, labels=["type:bug"])
        except Exception as exc:  # noqa: BLE001 — provider 実装ごとの例外型を跨ぐ best-effort
            self.stderr.write(f"WARNING: bug issue creation failed: {exc}\n")
            return replace(
                decision,
                decision="not_resumable",
                reason=f"kaji bug suspected but bug issue creation failed: {exc}",
                evidence=[*decision.evidence, f"bug issue creation failed: {exc}"],
            )
        return replace(decision, bug_issue={"id": issue.id, "url": self._issue_url(issue.id)})

    def _issue_url(self, issue_id: str) -> str:
        """GitHub provider では Issue URL、それ以外は空文字（``n/a`` 表示）。"""
        repo = getattr(self.provider, "repo", "")
        if isinstance(repo, str) and repo:
            return f"https://github.com/{repo}/issues/{issue_id}"
        return ""

    def _bug_issue_body(self, decision: RecoveryDecision) -> str:
        lines = [
            "## 概要",
            "",
            f"`kaji run` の failure triage が run `{decision.run_id}` "
            f"（Issue `{self.issue_ref}`）で決定論的な矛盾を検出した。",
            "",
            "## 判断根拠",
            "",
            *[f"- {sanitize_evidence(item)}" for item in decision.evidence],
            "",
            "## 元 run の artifact",
            "",
            f"- `{self.run_dir}`",
            f"- workflow: `{decision.workflow_path}`",
            "",
            "## 自動再開の実施有無",
            "",
            f"- auto recovery: attempted={str(decision.auto_recovery_attempted).lower()}",
        ]
        return "\n".join(lines) + "\n"

    def _post_triage_comment(self, decision: RecoveryDecision) -> RecoveryDecision:
        """triage コメントを即時投稿する。失敗時は自動再開を抑止する（safety）。"""
        if self.provider is None:
            return decision
        body = render_triage_comment(decision=decision, issue_ref=self.issue_ref)
        try:
            comment = self.provider.comment_issue(self.issue_id, body)
        except Exception as exc:  # noqa: BLE001 — provider 実装ごとの例外型を跨ぐ best-effort
            self.stderr.write(f"WARNING: triage comment posting failed: {exc}\n")
            evidence = [*decision.evidence, f"triage comment posting failed: {exc}"]
            if decision.decision != "resume":
                return replace(decision, evidence=evidence)
            return replace(
                decision,
                decision="not_resumable",
                recoverable=False,
                resume_scheduled_at=None,
                reason="triage comment could not be posted; auto recovery suppressed",
                evidence=evidence,
            )
        return replace(decision, triage_comment_ref=comment.ref or None)

    # --- 自動再開 ---

    def _resume(self, decision: RecoveryDecision) -> RecoveryResult:
        assert decision.resume_scheduled_at is not None
        assert decision.resume_from is not None
        self._run_logger.log_recovery_scheduled(
            resume_scheduled_at=decision.resume_scheduled_at, wait_seconds=self.wait_seconds
        )
        try:
            self.sleep(self.wait_seconds)
        except KeyboardInterrupt:
            cancelled = replace(
                decision,
                decision="cancelled_interrupted",
                recoverable=False,
                reason="auto recovery cancelled: interrupted during the recovery wait",
            )
            self._record(cancelled)
            return RecoveryResult(cancelled)

        newer = list_newer_run_ids(self._runs_dir, decision.run_id)
        if newer:
            cancelled = replace(
                decision,
                decision="cancelled_newer_run_detected",
                recoverable=False,
                reason="auto recovery cancelled: a newer run was started for this issue",
                evidence=[*decision.evidence, f"newer run dirs at launch: {', '.join(newer)}"],
            )
            self._record(cancelled)
            self.stderr.write(render_stderr_summary(cancelled))
            return RecoveryResult(cancelled)

        started_at = datetime.now(UTC).isoformat()
        decision = replace(
            decision,
            auto_recovery_attempted=True,
            auto_recovery_attempt_no=1,
            resume_started_at=started_at,
        )
        self._record(decision)
        self._run_logger.log_recovery_attempt_start(
            resume_command=decision.resume_command or "", resume_started_at=started_at
        )

        argv = self._child_argv(decision)
        exit_code = self.child_launcher(argv, self.workdir)
        child_run_id = find_child_run_id(self._runs_dir, decision.run_id)
        final_status = derive_child_final_status(exit_code)
        decision = replace(
            decision,
            recovery_child_run_id=child_run_id,
            recovery_child_final_status=final_status,
        )
        self._record(decision)
        self._run_logger.log_recovery_attempt_end(
            child_run_id=child_run_id, child_final_status=final_status, exit_code=exit_code
        )
        return RecoveryResult(decision, child_exit_code=exit_code)

    def _child_argv(self, decision: RecoveryDecision) -> list[str]:
        """child run の argv。``kaji`` entry point ではなく module 実行で PATH 非依存にする。"""
        assert decision.resume_from is not None
        assert decision.recovery_root_run_id is not None
        return [
            sys.executable,
            "-m",
            "kaji_harness.cli_main",
            "run",
            str(self.workflow_path),
            self.issue_id,
            "--from",
            decision.resume_from,
            "--recovery-root",
            decision.recovery_root_run_id,
            "--recovery-parent",
            decision.run_id,
            "--workdir",
            str(self.workdir),
        ]
