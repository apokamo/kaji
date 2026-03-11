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

    def log_workflow_start(self, issue: int, workflow: str) -> None:
        """ワークフロー開始イベントを記録。"""
        self._write("workflow_start", issue=issue, workflow=workflow)

    def log_step_start(
        self,
        step_id: str,
        agent: str,
        model: str | None,
        effort: str | None,
        session_id: str | None,
    ) -> None:
        """ステップ開始イベントを記録。"""
        self._write(
            "step_start",
            step_id=step_id,
            agent=agent,
            model=model,
            effort=effort,
            session_id=session_id,
        )

    def log_step_end(
        self,
        step_id: str,
        verdict: Verdict,
        duration_ms: int,
        cost: CostInfo | None,
    ) -> None:
        """ステップ終了イベントを記録。"""
        self._write(
            "step_end",
            step_id=step_id,
            verdict=asdict(verdict),
            duration_ms=duration_ms,
            cost=asdict(cost) if cost else None,
        )

    def log_cycle_iteration(self, cycle_name: str, iteration: int, max_iter: int) -> None:
        """サイクルイテレーションイベントを記録。"""
        self._write(
            "cycle_iteration",
            cycle_name=cycle_name,
            iteration=iteration,
            max_iterations=max_iter,
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
