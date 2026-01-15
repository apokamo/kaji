"""Tests for SessionState - ループカウンター・会話ID管理."""

import pytest

from src.workflows.base import SessionState


class TestSessionStateInitialization:
    """SessionState 初期化テスト."""

    def test_default_initialization(self) -> None:
        """引数なしで初期化できること（後方互換性）."""
        session = SessionState()

        assert session.completed_states == []
        assert session.loop_counters == {}
        assert session.active_conversations == {}
        assert session.max_loop_count == 3

    def test_custom_max_loop_count(self) -> None:
        """max_loop_count をカスタム値で初期化できること."""
        session = SessionState(max_loop_count=5)

        assert session.max_loop_count == 5

    def test_max_loop_count_zero(self) -> None:
        """max_loop_count=0 で初期化できること（ループ禁止）."""
        session = SessionState(max_loop_count=0)

        assert session.max_loop_count == 0

    def test_negative_max_loop_count_raises_error(self) -> None:
        """max_loop_count < 0 で ValueError が発生すること."""
        with pytest.raises(ValueError, match="max_loop_count must be >= 0"):
            SessionState(max_loop_count=-1)


class TestLoopCounter:
    """ループカウンター機能テスト."""

    def test_increment_loop_new_state(self) -> None:
        """未登録ステートの increment_loop が 1 を返すこと."""
        session = SessionState()

        result = session.increment_loop("DESIGN")

        assert result == 1
        assert session.loop_counters["DESIGN"] == 1

    def test_increment_loop_existing_state(self) -> None:
        """既存ステートの increment_loop が正しくインクリメントすること."""
        session = SessionState()
        session.increment_loop("DESIGN")
        session.increment_loop("DESIGN")

        result = session.increment_loop("DESIGN")

        assert result == 3
        assert session.loop_counters["DESIGN"] == 3

    def test_reset_loop(self) -> None:
        """reset_loop が 0 にリセットすること（キー削除ではない）."""
        session = SessionState()
        session.increment_loop("DESIGN")
        session.increment_loop("DESIGN")

        session.reset_loop("DESIGN")

        assert session.loop_counters["DESIGN"] == 0
        assert "DESIGN" in session.loop_counters

    def test_reset_loop_unregistered_state(self) -> None:
        """未登録ステートの reset_loop がキーを作成すること."""
        session = SessionState()

        session.reset_loop("NEW_STATE")

        assert session.loop_counters["NEW_STATE"] == 0
        assert "NEW_STATE" in session.loop_counters

    def test_is_loop_exceeded_false(self) -> None:
        """カウンタが上限未満の場合 False を返すこと."""
        session = SessionState(max_loop_count=3)
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2

        assert session.is_loop_exceeded("DESIGN") is False

    def test_is_loop_exceeded_at_limit(self) -> None:
        """カウンタが上限に達した場合 True を返すこと."""
        session = SessionState(max_loop_count=3)
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2
        session.increment_loop("DESIGN")  # 3

        assert session.is_loop_exceeded("DESIGN") is True

    def test_is_loop_exceeded_above_limit(self) -> None:
        """カウンタが上限を超えた場合 True を返すこと."""
        session = SessionState(max_loop_count=3)
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2
        session.increment_loop("DESIGN")  # 3
        session.increment_loop("DESIGN")  # 4

        assert session.is_loop_exceeded("DESIGN") is True

    def test_is_loop_exceeded_unregistered_state(self) -> None:
        """未登録ステートは False を返すこと（max_loop_count > 0）."""
        session = SessionState(max_loop_count=3)

        assert session.is_loop_exceeded("UNKNOWN") is False

    def test_is_loop_exceeded_with_zero_max(self) -> None:
        """max_loop_count=0 の場合、初回 increment 前から True."""
        session = SessionState(max_loop_count=0)

        # 0 >= 0 は True なのでループ禁止
        assert session.is_loop_exceeded("DESIGN") is True

    def test_is_loop_exceeded_with_one_max(self) -> None:
        """max_loop_count=1 の場合、1回目の increment 後に True."""
        session = SessionState(max_loop_count=1)

        assert session.is_loop_exceeded("DESIGN") is False  # 0 >= 1 は False
        session.increment_loop("DESIGN")  # 1
        assert session.is_loop_exceeded("DESIGN") is True  # 1 >= 1 は True

    def test_multiple_states_independent(self) -> None:
        """異なるステートのカウンタは独立していること."""
        session = SessionState(max_loop_count=3)
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2
        session.increment_loop("REVIEW")  # 1

        assert session.loop_counters["DESIGN"] == 2
        assert session.loop_counters["REVIEW"] == 1
        assert session.is_loop_exceeded("DESIGN") is False
        assert session.is_loop_exceeded("REVIEW") is False


class TestConversationId:
    """会話ID管理機能テスト."""

    def test_set_and_get_conversation_id(self) -> None:
        """会話IDを設定・取得できること."""
        session = SessionState()

        session.set_conversation_id("reviewer", "conv-123")

        assert session.get_conversation_id("reviewer") == "conv-123"

    def test_get_conversation_id_unregistered_role(self) -> None:
        """未登録ロールは None を返すこと."""
        session = SessionState()

        assert session.get_conversation_id("unknown") is None

    def test_set_conversation_id_none(self) -> None:
        """会話IDに None を設定できること."""
        session = SessionState()
        session.set_conversation_id("reviewer", "conv-123")

        session.set_conversation_id("reviewer", None)

        assert session.get_conversation_id("reviewer") is None

    def test_multiple_roles_independent(self) -> None:
        """異なるロールの会話IDは独立していること."""
        session = SessionState()

        session.set_conversation_id("reviewer", "conv-123")
        session.set_conversation_id("implementer", "conv-456")

        assert session.get_conversation_id("reviewer") == "conv-123"
        assert session.get_conversation_id("implementer") == "conv-456"

    def test_overwrite_conversation_id(self) -> None:
        """既存の会話IDを上書きできること."""
        session = SessionState()
        session.set_conversation_id("reviewer", "conv-123")

        session.set_conversation_id("reviewer", "conv-789")

        assert session.get_conversation_id("reviewer") == "conv-789"


class TestCompletedStates:
    """完了ステート管理機能テスト."""

    def test_mark_completed(self) -> None:
        """ステートを完了としてマークできること."""
        session = SessionState()

        session.mark_completed("INIT")

        assert "INIT" in session.completed_states
        assert session.is_completed("INIT") is True

    def test_is_completed_false(self) -> None:
        """未完了ステートは False を返すこと."""
        session = SessionState()

        assert session.is_completed("INIT") is False

    def test_mark_completed_no_duplicates(self) -> None:
        """重複登録を防ぐこと."""
        session = SessionState()

        session.mark_completed("INIT")
        session.mark_completed("INIT")
        session.mark_completed("INIT")

        assert session.completed_states.count("INIT") == 1

    def test_multiple_states_completed(self) -> None:
        """複数ステートを完了としてマークできること."""
        session = SessionState()

        session.mark_completed("INIT")
        session.mark_completed("DESIGN")
        session.mark_completed("REVIEW")

        assert session.is_completed("INIT") is True
        assert session.is_completed("DESIGN") is True
        assert session.is_completed("REVIEW") is True
        assert len(session.completed_states) == 3


class TestIntegration:
    """統合テスト - 複合シナリオ."""

    def test_typical_workflow_scenario(self) -> None:
        """典型的なワークフローシナリオ."""
        session = SessionState(max_loop_count=3)

        # 初期化
        session.set_conversation_id("analyzer", "conv-001")

        # DESIGN ステートを3回リトライ
        for i in range(3):
            count = session.increment_loop("DESIGN")
            assert count == i + 1
            if session.is_loop_exceeded("DESIGN"):
                break

        assert session.is_loop_exceeded("DESIGN") is True

        # ステート完了
        session.mark_completed("DESIGN")
        assert session.is_completed("DESIGN") is True

        # 次のステートへ（カウンタリセット）
        session.reset_loop("DESIGN")
        assert session.loop_counters["DESIGN"] == 0
        assert session.is_loop_exceeded("DESIGN") is False

    def test_dataclass_behavior(self) -> None:
        """dataclass としての基本動作確認."""
        session1 = SessionState()
        session2 = SessionState()

        # インスタンスが独立していること
        session1.increment_loop("TEST")
        assert session1.loop_counters["TEST"] == 1
        assert "TEST" not in session2.loop_counters
