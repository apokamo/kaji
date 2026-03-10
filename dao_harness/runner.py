"""Workflow execution runner for dao_harness.

Main loop that executes workflow steps sequentially,
manages state transitions, and handles cycle limits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cli import execute_cli
from .errors import (
    InvalidTransition,
    MissingResumeSessionError,
    WorkflowValidationError,
)
from .logger import RunLogger
from .models import CostInfo, Verdict, Workflow
from .prompt import build_prompt
from .skill import validate_skill_exists
from .state import SessionState
from .verdict import parse_verdict
from .workflow import validate_workflow


@dataclass
class WorkflowRunner:
    """ワークフロー実行エンジン。"""

    workflow: Workflow
    issue_number: int
    workdir: Path
    from_step: str | None = None
    single_step: str | None = None
    verbose: bool = True

    def run(self) -> SessionState:
        """ワークフローを実行し、最終状態を返す。

        Returns:
            SessionState: 実行後のセッション状態

        Raises:
            WorkflowValidationError: ワークフロー定義エラー
            MissingResumeSessionError: resume 先のセッション ID が見つからない
            InvalidTransition: verdict に対応する遷移先がない
        """
        execution_policy = self.workflow.execution_policy or "auto"

        # 0. 全ステップのスキル存在を事前検証
        for step in self.workflow.steps:
            validate_skill_exists(step.skill, step.agent, self.workdir)

        # 1. ワークフロー定義のバリデーション
        validate_workflow(self.workflow)

        # 2. issue-scoped な状態をロード
        state = SessionState.load_or_create(self.issue_number)

        # 3. run ログディレクトリを作成
        run_dir = Path(
            f"test-artifacts/{self.issue_number}/runs/{datetime.now().strftime('%y%m%d%H%M')}"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        logger = RunLogger(log_path=run_dir / "run.log")
        logger.log_workflow_start(self.issue_number, self.workflow.name)

        # 4. 開始ステップの決定
        if self.single_step:
            current_step = self.workflow.find_step(self.single_step)
            if not current_step:
                raise WorkflowValidationError(f"Step '{self.single_step}' not found")
        elif self.from_step:
            current_step = self.workflow.find_step(self.from_step)
            if not current_step:
                raise WorkflowValidationError(f"Step '{self.from_step}' not found")
        else:
            current_step = self.workflow.find_start_step()

        total_cost = 0.0
        workflow_start = time.monotonic()
        end_status = "COMPLETE"
        end_error: str | None = None
        last_verdict: Verdict | None = None

        # 5. メインループ
        try:
            while current_step and current_step.id != "end":
                start_time = time.monotonic()
                cycle = self.workflow.find_cycle_for_step(current_step.id)

                # サイクル上限チェック
                if cycle and state.cycle_iterations(cycle.name) >= cycle.max_iterations:
                    verdict = Verdict(
                        status=cycle.on_exhaust,
                        reason=f"Cycle '{cycle.name}' exhausted",
                        evidence=f"{cycle.max_iterations} iterations reached",
                        suggestion="手動で確認してください",
                    )
                    cost: CostInfo | None = None
                else:
                    # プロンプト構築
                    prompt = build_prompt(current_step, self.issue_number, state, self.workflow)

                    # セッション ID の取得
                    session_id = (
                        state.get_session_id(current_step.resume) if current_step.resume else None
                    )
                    if current_step.resume and session_id is None:
                        raise MissingResumeSessionError(current_step.id, current_step.resume)

                    # ログディレクトリ
                    step_log_dir = run_dir / current_step.id
                    step_log_dir.mkdir(parents=True, exist_ok=True)

                    logger.log_step_start(
                        current_step.id,
                        current_step.agent,
                        current_step.model,
                        current_step.effort,
                        session_id,
                    )

                    # CLI 実行
                    result = execute_cli(
                        step=current_step,
                        prompt=prompt,
                        workdir=self.workdir,
                        session_id=session_id,
                        log_dir=step_log_dir,
                        execution_policy=execution_policy,
                        verbose=self.verbose,
                    )

                    # セッション ID を保存
                    if result.session_id:
                        state.save_session_id(current_step.id, result.session_id)
                    cost = result.cost

                    # verdict をパース
                    verdict = parse_verdict(
                        result.full_output,
                        valid_statuses=set(current_step.on.keys()),
                    )

                # ログ記録 + 状態更新
                duration_ms = int((time.monotonic() - start_time) * 1000)
                logger.log_step_end(current_step.id, verdict, duration_ms, cost)
                state.record_step(current_step.id, verdict)
                last_verdict = verdict

                if cost and cost.usd:
                    total_cost += cost.usd

                # サイクルカウント
                if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY":
                    state.increment_cycle(cycle.name)
                    logger.log_cycle_iteration(
                        cycle.name,
                        state.cycle_iterations(cycle.name),
                        cycle.max_iterations,
                    )

                # 次のステップを決定
                if self.single_step:
                    break

                next_step_id = current_step.on.get(verdict.status)
                if next_step_id is None:
                    raise InvalidTransition(current_step.id, verdict.status)
                current_step = self.workflow.find_step(next_step_id)

            # 正常終了時のステータス判定
            if last_verdict and last_verdict.status == "ABORT":
                end_status = "ABORT"
        except Exception as exc:
            end_status = "ERROR"
            end_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            total_duration_ms = int((time.monotonic() - workflow_start) * 1000)
            logger.log_workflow_end(
                end_status,
                state.cycle_counts,
                total_duration_ms=total_duration_ms,
                total_cost=total_cost if total_cost > 0 else None,
                error=end_error,
            )
        return state
