"""Prompt builder for dao_harness.

Builds prompts with context variables for CLI execution.
"""

from __future__ import annotations

from .models import Step, Workflow
from .state import SessionState


def build_prompt(step: Step, issue: int, state: SessionState, workflow: Workflow) -> str:
    """ステップ実行用のプロンプトを構築する。

    Args:
        step: 実行するステップ
        issue: GitHub Issue 番号
        state: 現在のセッション状態
        workflow: ワークフロー定義

    Returns:
        CLI に渡すプロンプト文字列
    """
    variables: dict[str, object] = {
        "issue_number": issue,
        "step_id": step.id,
    }

    # サイクル変数（サイクル内ステップのみ）
    cycle = workflow.find_cycle_for_step(step.id)
    if cycle:
        variables["cycle_count"] = state.cycle_iterations(cycle.name) + 1
        variables["max_iterations"] = cycle.max_iterations

    # 遷移元の verdict（resume 指定ステップのみ）
    if step.resume and state.last_transition_verdict:
        v = state.last_transition_verdict
        variables["previous_verdict"] = (
            f"reason: {v.reason}\nevidence: {v.evidence}\nsuggestion: {v.suggestion}"
        )

    valid_statuses = list(step.on.keys())
    header = "\n".join(f"- {k}: {v}" for k, v in variables.items())

    return f"""スキル `{step.skill}` を実行してください。

## セッション開始プロトコル
1. GitHub Issue #{issue} を読み、現在の進捗を把握する
2. git log --oneline -10 で最近の変更を確認する
3. 以下のコンテキスト変数を確認する
4. 上記を踏まえて、スキルの指示に従って作業を実行する

## コンテキスト変数
{header}

## 出力要件
実行完了後、以下の YAML 形式で verdict を出力してください:

---VERDICT---
status: {" | ".join(valid_statuses)}
reason: "判定理由"
evidence: |
  具体的根拠（複数行可。抽象表現禁止）
suggestion: "次のアクション提案"（ABORT/BACK時必須）
---END_VERDICT---
"""
