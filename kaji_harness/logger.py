"""Run logger for kaji_harness.

JSONL format execution logger with immediate flush.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CostInfo, Verdict


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
        """ワークフロー開始イベントを記録。"""
        self._write("workflow_start", issue=issue, workflow=workflow)

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

    def log_barrier_hit(self, before_step: str) -> None:
        """`--before` barrier ヒット（指定 step を dispatch する直前で停止）を記録。"""
        self._write("barrier_hit", before_step=before_step)

    def log_barrier_missed(self, before_step: str) -> None:
        """`--before` barrier 未到達（workflow が自然完了）を記録。"""
        self._write("barrier_missed", before_step=before_step)

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
