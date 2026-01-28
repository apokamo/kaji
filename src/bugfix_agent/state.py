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
    """実行中の状態（変数として保持）

    Attributes:
        completed_states: 完了したステートのリスト
        current_state: 現在のステート
        loop_counters: ステート名 → ループカウント
        active_conversations: ロール名 → 会話ID
        max_loop_count: デフォルトの最大ループ回数

    Session 3原則:
        1) ロール単位で session_id を保持（analyzer / reviewer / implementer）
        2) RETRY 時は同一ロールの session_id を継続
        3) フェーズ切替（Design→Implement等）では必要に応じて明示リセット
    """

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
    max_loop_count: int = 3

    def increment_loop(self, key: str) -> int:
        """ループカウンタをインクリメントし、新しい値を返す

        Args:
            key: ループカウンタのキー（ステート名等）

        Returns:
            インクリメント後のカウント値
        """
        self.loop_counters[key] = self.loop_counters.get(key, 0) + 1
        return self.loop_counters[key]

    def reset_loop(self, key: str) -> None:
        """ループカウンタをリセット（0に戻す）

        Note:
            キーを削除するのではなく、0を設定する。
            未登録キーの場合は新規作成して0を設定。

        Args:
            key: ループカウンタのキー
        """
        self.loop_counters[key] = 0

    def is_loop_exceeded(self, key: str, max_count: int | None = None) -> bool:
        """ループ上限を超えたか判定

        Args:
            key: ループカウンタのキー
            max_count: 上限値（None の場合は self.max_loop_count を使用）

        Returns:
            カウンタ >= 上限の場合 True
        """
        if max_count is None:
            max_count = self.max_loop_count
        return self.loop_counters.get(key, 0) >= max_count

    def get_conversation_id(self, role: str) -> str | None:
        """ロールのセッションIDを取得

        Args:
            role: エージェントロール名（analyzer/reviewer/implementer 等）

        Returns:
            会話ID、未設定の場合は None
        """
        return self.active_conversations.get(role)

    def set_conversation_id(self, role: str, session_id: str | None) -> None:
        """ロールのセッションIDを設定

        Args:
            role: エージェントロール名
            session_id: 会話ID（None でクリア）
        """
        self.active_conversations[role] = session_id
