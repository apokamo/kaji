"""Workflow execution runner for kaji_harness.

Main loop that executes workflow steps sequentially,
manages state transitions, and handles cycle limits.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cli import execute_cli
from .config import KajiConfig
from .errors import (
    InvalidTransition,
    MissingResumeSessionError,
    WorkdirNotFoundError,
    WorkflowValidationError,
)
from .logger import RunLogger
from .models import CostInfo, Verdict, Workflow
from .prompt import build_prompt
from .skill import validate_skill_exists
from .state import SessionState
from .verdict import create_verdict_formatter, parse_verdict
from .workflow import validate_workflow


@dataclass
class WorkflowRunner:
    """ワークフロー実行エンジン。"""

    workflow: Workflow
    issue_number: int
    project_root: Path
    artifacts_dir: Path
    config: KajiConfig
    from_step: str | None = None
    single_step: str | None = None
    before_step: str | None = None
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
        execution_policy = self.workflow.execution_policy

        # 0. 全ステップのスキル存在を事前検証
        for step in self.workflow.steps:
            validate_skill_exists(step.skill, self.project_root, self.config.paths.skill_dir)

        # 1. ワークフロー定義のバリデーション
        validate_workflow(self.workflow)

        # 1.5. --before 指定 step の存在検証（"end" は許容）
        if self.before_step and self.before_step != "end":
            if not self.workflow.find_step(self.before_step):
                raise WorkflowValidationError(f"Step '{self.before_step}' not found (--before)")

        # 2. issue-scoped な状態をロード
        state = SessionState.load_or_create(self.issue_number, self.artifacts_dir)

        # 3. run ログディレクトリを作成
        run_dir = (
            self.artifacts_dir
            / str(self.issue_number)
            / "runs"
            / datetime.now().strftime("%y%m%d%H%M")
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
        barrier_hit = False

        # 5. メインループ
        try:
            while current_step and current_step.id != "end":
                # --before barrier: dispatch 直前で停止（開始 step / --from 開始 step も含む）
                if self.before_step and current_step.id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
                    barrier_hit = True
                    break

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

                    # タイムアウト解決: workflow.default_timeout → config.execution.default_timeout
                    default_timeout = (
                        self.workflow.default_timeout
                        if self.workflow.default_timeout is not None
                        else self.config.execution.default_timeout
                    )

                    # workdir 解決: step.workdir → workflow.workdir → project_root
                    raw_workdir = current_step.workdir or self.workflow.workdir
                    effective_workdir = Path(raw_workdir) if raw_workdir else self.project_root
                    if not effective_workdir.is_dir():
                        raise WorkdirNotFoundError(current_step.id, effective_workdir)

                    # CLI 実行
                    result = execute_cli(
                        step=current_step,
                        prompt=prompt,
                        workdir=effective_workdir,
                        session_id=session_id,
                        log_dir=step_log_dir,
                        execution_policy=execution_policy,
                        verbose=self.verbose,
                        default_timeout=default_timeout,
                    )

                    # セッション ID を保存
                    if result.session_id:
                        state.save_session_id(current_step.id, result.session_id)
                    cost = result.cost

                    # verdict をパース (3-stage fallback: strict → relaxed → AI formatter)
                    valid = set(current_step.on.keys())
                    formatter = create_verdict_formatter(
                        agent=current_step.agent,
                        valid_statuses=valid,
                        model=current_step.model,
                        workdir=effective_workdir,
                    )
                    verdict = parse_verdict(
                        result.full_output,
                        valid_statuses=valid,
                        ai_formatter=formatter,
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

                # --before barrier: dispatch 直前で停止
                if self.before_step and next_step_id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
                    barrier_hit = True
                    break

                current_step = self.workflow.find_step(next_step_id)

            # --before 未到達検知（"end" は WARN 対象外）
            if self.before_step and not barrier_hit and self.before_step != "end":
                logger.log_barrier_missed(self.before_step)
                print(
                    f"WARN: stop point '{self.before_step}' was never reached; "
                    "workflow completed naturally",
                    file=sys.stderr,
                )

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
