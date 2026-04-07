"""Data models for kaji_harness."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostInfo:
    """CLI 実行コスト情報。"""

    usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Verdict:
    """ステップ出力から抽出した判定結果。"""

    status: str
    reason: str
    evidence: str
    suggestion: str


@dataclass
class CLIResult:
    """CLI プロセスの実行結果。"""

    full_output: str
    session_id: str | None = None
    cost: CostInfo | None = None
    stderr: str = ""
    error_messages: list[str] = field(default_factory=list)


@dataclass
class Step:
    """ワークフロー内の1ステップ定義。"""

    id: str
    skill: str
    agent: str
    model: str | None = None
    effort: str | None = None
    max_budget_usd: float | None = None
    max_turns: int | None = None
    timeout: int | None = None
    workdir: str | None = None
    resume: str | None = None
    inject_verdict: bool = False
    on: dict[str, str] = field(default_factory=dict)


@dataclass
class CycleDefinition:
    """ループサイクルの定義。"""

    name: str
    entry: str
    loop: list[str]
    max_iterations: int
    on_exhaust: str


@dataclass
class Workflow:
    """ワークフロー全体の定義。"""

    name: str
    description: str
    execution_policy: str
    steps: list[Step]
    cycles: list[CycleDefinition] = field(default_factory=list)
    default_timeout: int | None = None
    workdir: str | None = None

    def find_step(self, step_id: str) -> Step | None:
        """ID でステップを検索。見つからなければ None。"""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def find_start_step(self) -> Step:
        """最初のステップを返す。"""
        return self.steps[0]

    def find_cycle_for_step(self, step_id: str) -> CycleDefinition | None:
        """ステップが属するサイクルを検索。見つからなければ None。"""
        for cycle in self.cycles:
            if step_id in cycle.loop or step_id == cycle.entry:
                return cycle
        return None
