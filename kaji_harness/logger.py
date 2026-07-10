"""Run logger for kaji_harness.

JSONL format execution logger with immediate flush.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import CostInfo, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .recovery.models import RecoveryDecision

#: run.log の event 契約バージョン。``workflow_start`` に記録する。
#: 1 = Issue #288 の ``failure_event`` 契約（ABORT / ERROR 終端は必ず failure_event を伴う）。
RUN_LOG_SCHEMA_VERSION = 1


@dataclass
class RunLogger:
    """JSONL 形式の実行ログを出力するクラス。"""

    log_path: Path

    def __post_init__(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, event: str, **kwargs: Any) -> None:
        """イベントを JSONL 形式で出力。"""
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **kwargs,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()

    def log_workflow_start(self, issue: str, workflow: str) -> None:
        """ワークフロー開始イベントを記録。

        ``schema_version`` は run.log が満たす event 契約の版。Issue #288 の
        ``failure_event`` を必ず emit する runner でのみ記録されるため、recovery
        classifier は「この run に failure_event が無いのは矛盾である」と断定して
        よいかをこの値で判別する（本機能導入前の run には存在しない）。
        """
        self._write(
            "workflow_start",
            issue=issue,
            workflow=workflow,
            schema_version=RUN_LOG_SCHEMA_VERSION,
        )

    def log_step_start(
        self,
        step_id: str,
        agent: str | None,
        model: str | None,
        effort: str | None,
        session_id: str | None,
        *,
        attempt: int | None = None,
        dispatch: str = "agent",
    ) -> None:
        """ステップ開始イベントを記録。

        Issue #222: ``attempt`` は ``attempt-NNN`` の 1 始まり整数。dispatch される
        step では常に整数。dispatch を伴わない合成 step（cycle 上限 exhaust 等）では
        ``None``。
        """
        self._write(
            "step_start",
            step_id=step_id,
            agent=agent,
            model=model,
            effort=effort,
            session_id=session_id,
            attempt=attempt,
            dispatch=dispatch,
        )

    def log_step_end(
        self,
        step_id: str,
        verdict: Verdict,
        duration_ms: int,
        cost: CostInfo | None,
        *,
        attempt: int | None = None,
        exit_code: int | None = None,
        signal: str | None = None,
        dispatch: str = "agent",
    ) -> None:
        """ステップ終了イベントを記録（正常終了・タイムアウト・エラーを問わず）。

        Issue #222: ``attempt`` / ``exit_code`` / ``signal`` を付与し、step retry の
        時系列と異常終了の終了コードを run.log から復元可能にする。``exit_code`` は
        subprocess の returncode（取得不能なら ``None``）、``signal`` はそこから導出
        した signal 名（clean exit / signal 由来でなければ ``None``）。
        """
        self._write(
            "step_end",
            step_id=step_id,
            verdict=asdict(verdict),
            duration_ms=duration_ms,
            cost=asdict(cost) if cost else None,
            attempt=attempt,
            exit_code=exit_code,
            signal=signal,
            dispatch=dispatch,
        )

    def log_verdict_source(self, step_id: str, source: str, attempt: str) -> None:
        """verdict 解決経路（artifact / comment / stdout）と attempt を記録する。

        Issue #220: artifact-primary 解決の追跡性確保。``attempt`` は
        ``attempt-NNN`` ディレクトリ名。
        """
        self._write("verdict_source", step_id=step_id, source=source, attempt=attempt)

    def log_cycle_iteration(self, cycle_name: str, iteration: int, max_iter: int) -> None:
        """サイクルイテレーションイベントを記録。"""
        self._write(
            "cycle_iteration",
            cycle_name=cycle_name,
            iteration=iteration,
            max_iterations=max_iter,
        )

    def log_cycle_reset(self, cycle_name: str, previous_iterations: int) -> None:
        """`--reset-cycle` によるサイクル反復回数のリセットを記録。"""
        self._write(
            "cycle_reset",
            cycle_name=cycle_name,
            previous_iterations=previous_iterations,
            new_iterations=0,
        )

    def log_barrier_hit(self, before_step: str) -> None:
        """`--before` barrier ヒット（指定 step を dispatch する直前で停止）を記録。"""
        self._write("barrier_hit", before_step=before_step)

    def log_barrier_missed(self, before_step: str) -> None:
        """`--before` barrier 未到達（workflow が自然完了）を記録。"""
        self._write("barrier_missed", before_step=before_step)

    def log_failure_event(
        self,
        *,
        kind: str,
        step_id: str | None = None,
        exception_type: str | None = None,
        cycle_name: str | None = None,
        synthetic: bool = True,
    ) -> None:
        """Issue #288: run を終端させた失敗を構造化記録する。

        ``kind`` は ``dispatch_exception`` / ``verdict_exception`` / ``cycle_exhausted`` /
        ``ambiguous_worktree`` / ``agent_abort``。recovery classifier はこの event を
        一次入力とし、reason 文字列マッチには依存しない。``synthetic`` は failure record
        が runner 生成かを表す直交属性（agent の正規 ABORT のみ ``False``）。
        """
        self._write(
            "failure_event",
            kind=kind,
            step_id=step_id,
            exception_type=exception_type,
            cycle_name=cycle_name,
            synthetic=synthetic,
        )

    def log_recovery_decision(self, decision: RecoveryDecision) -> None:
        """Issue #288: failure triage の判定結果を記録する（更新のたびに追記）。"""
        self._write(
            "recovery_decision",
            run_id=decision.run_id,
            decision=decision.decision,
            recoverable=decision.recoverable,
            cause=decision.classification.cause,
            synthetic=decision.classification.synthetic,
            failed_step=decision.failed_step,
            resume_from=decision.resume_from,
            recovery_root_run_id=decision.recovery_root_run_id,
            recovery_parent_run_id=decision.recovery_parent_run_id,
            reason=decision.reason,
        )

    def log_recovery_scheduled(self, *, resume_scheduled_at: str, wait_seconds: int) -> None:
        """Issue #288: 自動再開の予定時刻とウェイト長を記録する。"""
        self._write(
            "recovery_scheduled",
            resume_scheduled_at=resume_scheduled_at,
            wait_seconds=wait_seconds,
        )

    def log_recovery_attempt_start(self, *, resume_command: str, resume_started_at: str) -> None:
        """Issue #288: ウェイト明けに child run 起動を開始した時刻を記録する。"""
        self._write(
            "recovery_attempt_start",
            resume_command=resume_command,
            resume_started_at=resume_started_at,
        )

    def log_recovery_attempt_end(
        self,
        *,
        child_run_id: str | None,
        child_final_status: str | None,
        exit_code: int | None,
    ) -> None:
        """Issue #288: child run の終了と、その run_id / 導出 status を記録する。"""
        self._write(
            "recovery_attempt_end",
            child_run_id=child_run_id,
            child_final_status=child_final_status,
            exit_code=exit_code,
        )

    def log_workflow_end(
        self,
        status: str,
        cycle_counts: dict[str, int],
        total_duration_ms: int,
        total_cost: float | None,
        error: str | None = None,
    ) -> None:
        """ワークフロー終了イベントを記録。"""
        kwargs: dict[str, Any] = {
            "status": status,
            "cycle_counts": cycle_counts,
            "total_duration_ms": total_duration_ms,
            "total_cost": total_cost,
        }
        if error is not None:
            kwargs["error"] = error
        self._write("workflow_end", **kwargs)
