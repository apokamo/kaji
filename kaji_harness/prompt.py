"""Prompt builder for kaji_harness.

Builds prompts with context variables for CLI execution.
"""

from __future__ import annotations

from .models import Step, Workflow
from .providers import IssueContext
from .state import SessionState, _format_issue_ref


def build_prompt(
    step: Step,
    issue: str,
    state: SessionState,
    workflow: Workflow,
    issue_context: IssueContext | None = None,
) -> str:
    """ステップ実行用のプロンプトを構築する。

    Args:
        step: 実行するステップ
        issue: Issue ID（GitHub の数値、または ``local-<machine>-<n>`` 形式）
        state: 現在のセッション状態
        workflow: ワークフロー定義
        issue_context: provider が解決した `IssueContext`。``None`` の場合は
            Phase 2-B 互換の 2 変数（``issue_id`` / ``issue_ref``）のみ注入する
            （Phase 3-c では `[provider]` 未設定の repo で legacy 互換を維持）。

    Returns:
        CLI に渡すプロンプト文字列
    """
    issue_id = str(issue)
    issue_ref = _format_issue_ref(issue_id)
    if issue_context is not None:
        # provider が IssueContext を解決済の場合、issue_id / issue_ref も
        # context 由来の値を採用する（machine_id 等の正規化を尊重するため）。
        issue_id = issue_context.issue_id
        issue_ref = issue_context.issue_ref
    variables: dict[str, object] = {
        "issue_id": issue_id,
        "issue_ref": issue_ref,
        "step_id": step.id,
    }
    if issue_context is not None:
        variables["issue_input"] = issue_context.issue_input
        variables["branch_prefix"] = issue_context.branch_prefix
        variables["branch_name"] = issue_context.branch_name
        variables["worktree_dir"] = issue_context.worktree_dir
        variables["design_path"] = issue_context.design_path

    # サイクル変数（サイクル内ステップのみ）
    cycle = workflow.find_cycle_for_step(step.id)
    if cycle:
        variables["cycle_count"] = state.cycle_iterations(cycle.name) + 1
        variables["max_iterations"] = cycle.max_iterations

    # 遷移元の verdict（resume または inject_verdict 指定ステップ）
    if (step.resume or step.inject_verdict) and state.last_transition_verdict:
        v = state.last_transition_verdict
        variables["previous_verdict"] = (
            f"reason: {v.reason}\nevidence: {v.evidence}\nsuggestion: {v.suggestion}"
        )

    valid_statuses = list(step.on.keys())
    header = "\n".join(f"- {k}: {v}" for k, v in variables.items())

    return f"""スキル `{step.skill}` を実行してください。

## セッション開始プロトコル
1. Issue {issue_ref} を読み、現在の進捗を把握する
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
