"""State machine definitions for Bugfix Agent v5

This module provides:
- State: Workflow state enumeration (9 states)
- ExecutionMode: Execution mode enumeration
- ExecutionConfig: Execution configuration dataclass
- SessionState: Runtime session state dataclass
- infer_result_label: State transition label inference
"""

from dataclasses import dataclass, field
from enum import Enum, auto

from .verdict import Verdict


class State(Enum):
    """ステートマシンの状態定義 (Issue #194 Protocol)

    9ステート構成（QA/QA_REVIEWはIMPLEMENT_REVIEWに統合済み）:
    - INIT: Issue情報の確認
    - INVESTIGATE: 再現・原因調査
    - INVESTIGATE_REVIEW: 調査結果レビュー
    - DETAIL_DESIGN: 詳細設計
    - DETAIL_DESIGN_REVIEW: 設計レビュー
    - IMPLEMENT: 実装
    - IMPLEMENT_REVIEW: 実装レビュー（QA機能統合）
    - PR_CREATE: PR作成
    - COMPLETE: 完了
    """

    INIT = auto()
    INVESTIGATE = auto()
    INVESTIGATE_REVIEW = auto()
    DETAIL_DESIGN = auto()
    DETAIL_DESIGN_REVIEW = auto()
    IMPLEMENT = auto()
    IMPLEMENT_REVIEW = auto()
    PR_CREATE = auto()
    COMPLETE = auto()


def infer_result_label(current: State, next_state: State) -> str:
    """ステート遷移から判定ラベルを推論する

    レビューステートの遷移パターンから結果を判定:
    - 前進 → PASS
    - 直前のワークステートへ戻る → BLOCKED (INVESTIGATE/DETAIL_DESIGN系) or FIX_REQUIRED (IMPLEMENT系)
    - 設計まで戻る → DESIGN_FIX

    Note:
        この関数は State 列挙型と密結合しています。State に新しいレビューステートを
        追加した場合は、transitions マップも同時に更新する必要があります。

    Args:
        current: 現在のステート
        next_state: 次のステート

    Returns:
        判定ラベル ("PASS", "BLOCKED", "FIX_REQUIRED", "DESIGN_FIX")
    """
    # 非レビューステートは常に PASS（作業実行のみ）
    if not current.name.endswith("_REVIEW"):
        return "PASS"

    # レビューステートの遷移パターン
    # Issue #194 Protocol: VERDICT形式に統一
    transitions = {
        # INVESTIGATE_REVIEW: PASS→DETAIL_DESIGN, RETRY→INVESTIGATE
        (State.INVESTIGATE_REVIEW, State.DETAIL_DESIGN): Verdict.PASS.value,
        (State.INVESTIGATE_REVIEW, State.INVESTIGATE): Verdict.RETRY.value,
        # DETAIL_DESIGN_REVIEW: PASS→IMPLEMENT, RETRY→DETAIL_DESIGN
        (State.DETAIL_DESIGN_REVIEW, State.IMPLEMENT): Verdict.PASS.value,
        (State.DETAIL_DESIGN_REVIEW, State.DETAIL_DESIGN): Verdict.RETRY.value,
        # IMPLEMENT_REVIEW (QA統合): PASS→PR_CREATE, RETRY→IMPLEMENT, BACK_DESIGN→DETAIL_DESIGN
        (State.IMPLEMENT_REVIEW, State.PR_CREATE): Verdict.PASS.value,
        (State.IMPLEMENT_REVIEW, State.IMPLEMENT): Verdict.RETRY.value,
        (State.IMPLEMENT_REVIEW, State.DETAIL_DESIGN): Verdict.BACK_DESIGN.value,
    }

    return transitions.get((current, next_state), "UNKNOWN")


class ExecutionMode(Enum):
    """実行モード定義"""

    FULL = auto()  # INIT → COMPLETE まで通常実行
    SINGLE = auto()  # 指定ステートのみ1回実行して終了
    FROM_END = auto()  # 指定ステートから COMPLETE まで実行


@dataclass
class ExecutionConfig:
    """実行設定"""

    mode: ExecutionMode  # 実行モード
    target_state: State | None = None  # SINGLE/FROM_END 時の対象ステート
    issue_url: str = ""  # Issue URL
    issue_number: int = 0  # issue_url から抽出
    tool_override: str | None = None  # ツール指定 (codex, gemini, claude)
    model_override: str | None = None  # モデル指定 (--tool-model で使用)


@dataclass
class SessionState:
    """実行中の状態（変数として保持）"""

    completed_states: list[str] = field(default_factory=list)
    current_state: State = State.INIT
    loop_counters: dict[str, int] = field(
        default_factory=lambda: {
            "Investigate_Loop": 0,
            "Detail_Design_Loop": 0,
            "Implement_Loop": 0,
        }
    )
    active_conversations: dict[str, str | None] = field(
        default_factory=lambda: {
            "Design_Thread_conversation_id": None,
            "Implement_Loop_conversation_id": None,
        }
    )
