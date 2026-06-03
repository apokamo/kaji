"""Prompt builder for kaji_harness.

Builds prompts with context variables for CLI execution.
"""

from __future__ import annotations

from .models import Step, Workflow
from .providers import IssueContext, PRContext
from .state import SessionState


def build_prompt(
    step: Step,
    issue: str,
    state: SessionState,
    workflow: Workflow,
    issue_context: IssueContext,
    *,
    pr_context: PRContext | None = None,
    verdict_path: str | None = None,
) -> str:
    """ステップ実行用のプロンプトを構築する。

    Phase 4 で ``issue_context`` を required 化。Phase 3-e で
    ``WorkflowRunner._resolve_issue_context()`` が ``IssueContext`` を必ず
    返す設計に変わったため、``build_prompt`` 呼び出し時点で
    ``IssueContext`` は確定している（``runner.py:_resolve_run_issue_context``）。

    Args:
        step: 実行するステップ
        issue: Issue ID（呼出側のログ・互換のため signature には残すが、
            注入される値は ``issue_context.issue_id`` を採用する）
        state: 現在のセッション状態
        workflow: ワークフロー定義
        issue_context: provider が解決した `IssueContext`。
        pr_context: provider が解決した `PRContext`。``None`` の場合
            ``pr_id`` / ``pr_ref`` は variables に含まれない（branch 未 push /
            MR 未作成 / GitHub・Local provider の no-op 実装等）。
        verdict_path: Issue #220。当該 attempt の ``verdict.yaml`` 絶対パス。
            runner は常に渡す。``None`` の場合は ``[verdict_path]`` placeholder で
            出力要件をレンダリングする（直接呼び出し / legacy 互換）。

    Returns:
        CLI に渡すプロンプト文字列
    """
    del issue  # 互換 signature。注入は issue_context 経由で行う
    variables: dict[str, object] = {
        "issue_id": issue_context.issue_id,
        "issue_ref": issue_context.issue_ref,
        "step_id": step.id,
        "issue_input": issue_context.issue_input,
        "branch_prefix": issue_context.branch_prefix,
        "branch_name": issue_context.branch_name,
        "worktree_dir": issue_context.worktree_dir,
        "design_path": issue_context.design_path,
        "provider_type": issue_context.provider_type,
        "default_branch": issue_context.default_branch,
        "git_remote": issue_context.git_remote,
    }

    if pr_context is not None:
        variables["pr_id"] = pr_context.pr_id
        variables["pr_ref"] = pr_context.pr_ref

    if verdict_path is not None:
        variables["verdict_path"] = verdict_path

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
    status_choices = " | ".join(valid_statuses)
    verdict_target = verdict_path if verdict_path is not None else "[verdict_path]"

    return f"""スキル `{step.skill}` を実行してください。

## セッション開始プロトコル
1. Issue {issue_context.issue_ref} を読み、現在の進捗を把握する
2. git log --oneline -10 で最近の変更を確認する
3. 以下のコンテキスト変数を確認する
4. 上記を踏まえて、スキルの指示に従って作業を実行する

## コンテキスト変数
{header}

## 出力要件
作業完了後、以下を必ず実施してください:

1. 次の YAML を `{verdict_target}` に保存する（pure YAML。`---VERDICT---` delimiter は付けない）:
   status: {status_choices} のいずれか
   reason: 判定理由
   evidence: 具体的根拠（複数行可。抽象表現禁止）
   suggestion: 次のアクション提案（ABORT/BACK 時必須）
2. 作業報告 Issue comment の末尾に、同じ内容を次の `---VERDICT---` block として追記する。
3. 互換のため、同じ block を stdout にも出力する:

---VERDICT---
status: {status_choices}
reason: "判定理由"
evidence: |
  具体的根拠（複数行可。抽象表現禁止）
suggestion: "次のアクション提案"（ABORT/BACK時必須）
---END_VERDICT---
"""
