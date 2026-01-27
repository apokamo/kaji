"""Tests for bugfix_agent SessionState - ループカウンター・会話ID API.

Issue #35: v5 Phase1 - SessionState に loop/conv id API を追加
"""

from src.bugfix_agent.state import SessionState


class TestSessionStateInitialization:
    """SessionState 初期化テスト."""

    def test_default_initialization(self) -> None:
        """デフォルト値で初期化できること."""
        session = SessionState()

        assert session.completed_states == []
        assert isinstance(session.loop_counters, dict)
        assert isinstance(session.active_conversations, dict)

    def test_v5_legacy_loop_counters(self) -> None:
        """v5 互換: Investigate_Loop 等のデフォルトキーが存在すること."""
        session = SessionState()

        assert "Investigate_Loop" in session.loop_counters
        assert "Detail_Design_Loop" in session.loop_counters
        assert "Implement_Loop" in session.loop_counters

    def test_v5_legacy_conversations(self) -> None:
        """v5 互換: Design_Thread_conversation_id 等のデフォルトキーが存在すること."""
        session = SessionState()

        assert "Design_Thread_conversation_id" in session.active_conversations
        assert "Implement_Loop_conversation_id" in session.active_conversations


class TestIncrementLoop:
    """increment_loop() メソッドテスト."""

    def test_increment_loop_new_key(self) -> None:
        """新規キーに対して increment_loop が 1 を返すこと."""
        session = SessionState()

        result = session.increment_loop("DESIGN")

        assert result == 1
        assert session.loop_counters["DESIGN"] == 1

    def test_increment_loop_existing_key(self) -> None:
        """既存キーに対して increment_loop が正しくインクリメントすること."""
        session = SessionState()
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2

        result = session.increment_loop("DESIGN")

        assert result == 3
        assert session.loop_counters["DESIGN"] == 3

    def test_increment_loop_v5_legacy_key(self) -> None:
        """v5 互換キー（Investigate_Loop 等）のインクリメント."""
        session = SessionState()
        # デフォルト値は 0
        initial = session.loop_counters["Investigate_Loop"]

        result = session.increment_loop("Investigate_Loop")

        assert result == initial + 1


class TestResetLoop:
    """reset_loop() メソッドテスト."""

    def test_reset_loop(self) -> None:
        """reset_loop が 0 にリセットすること."""
        session = SessionState()
        session.increment_loop("DESIGN")
        session.increment_loop("DESIGN")

        session.reset_loop("DESIGN")

        assert session.loop_counters["DESIGN"] == 0

    def test_reset_loop_unregistered_key(self) -> None:
        """未登録キーの reset_loop がキーを作成し 0 を設定すること."""
        session = SessionState()

        session.reset_loop("NEW_KEY")

        assert "NEW_KEY" in session.loop_counters
        assert session.loop_counters["NEW_KEY"] == 0


class TestIsLoopExceeded:
    """is_loop_exceeded() メソッドテスト."""

    def test_is_loop_exceeded_with_default_max(self) -> None:
        """デフォルト max_loop_count (3) を使用した判定."""
        session = SessionState()
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2

        assert session.is_loop_exceeded("DESIGN") is False

        session.increment_loop("DESIGN")  # 3
        assert session.is_loop_exceeded("DESIGN") is True

    def test_is_loop_exceeded_with_custom_max(self) -> None:
        """カスタム max_count を指定した判定."""
        session = SessionState()
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2

        # max_count=2 で判定: 2 >= 2 → True
        assert session.is_loop_exceeded("DESIGN", max_count=2) is True

        # max_count=5 で判定: 2 >= 5 → False
        assert session.is_loop_exceeded("DESIGN", max_count=5) is False

    def test_is_loop_exceeded_unregistered_key(self) -> None:
        """未登録キーは False を返すこと."""
        session = SessionState()

        assert session.is_loop_exceeded("UNKNOWN") is False

    def test_is_loop_exceeded_with_zero_max(self) -> None:
        """max_count=0 の場合、常に True."""
        session = SessionState()

        assert session.is_loop_exceeded("DESIGN", max_count=0) is True


class TestGetConversationId:
    """get_conversation_id() メソッドテスト."""

    def test_get_conversation_id_existing_role(self) -> None:
        """既存ロールの会話IDを取得できること."""
        session = SessionState()
        session.active_conversations["reviewer"] = "conv-123"

        result = session.get_conversation_id("reviewer")

        assert result == "conv-123"

    def test_get_conversation_id_unregistered_role(self) -> None:
        """未登録ロールは None を返すこと."""
        session = SessionState()

        result = session.get_conversation_id("unknown_role")

        assert result is None

    def test_get_conversation_id_v5_legacy_role(self) -> None:
        """v5 互換ロール名での取得."""
        session = SessionState()
        session.active_conversations["Design_Thread_conversation_id"] = "conv-v5"

        result = session.get_conversation_id("Design_Thread_conversation_id")

        assert result == "conv-v5"


class TestSetConversationId:
    """set_conversation_id() メソッドテスト."""

    def test_set_conversation_id_new_role(self) -> None:
        """新規ロールに会話IDを設定できること."""
        session = SessionState()

        session.set_conversation_id("reviewer", "conv-456")

        assert session.active_conversations["reviewer"] == "conv-456"

    def test_set_conversation_id_overwrite(self) -> None:
        """既存の会話IDを上書きできること."""
        session = SessionState()
        session.set_conversation_id("reviewer", "conv-123")

        session.set_conversation_id("reviewer", "conv-789")

        assert session.active_conversations["reviewer"] == "conv-789"

    def test_set_conversation_id_none(self) -> None:
        """会話IDに None を設定できること."""
        session = SessionState()
        session.set_conversation_id("reviewer", "conv-123")

        session.set_conversation_id("reviewer", None)

        assert session.active_conversations["reviewer"] is None


class TestMultipleKeysIndependence:
    """複数キー/ロールの独立性テスト."""

    def test_loop_counters_independent(self) -> None:
        """異なるキーのループカウンタは独立していること."""
        session = SessionState()
        session.increment_loop("DESIGN")  # 1
        session.increment_loop("DESIGN")  # 2
        session.increment_loop("REVIEW")  # 1

        assert session.loop_counters["DESIGN"] == 2
        assert session.loop_counters["REVIEW"] == 1

    def test_conversations_independent(self) -> None:
        """異なるロールの会話IDは独立していること."""
        session = SessionState()
        session.set_conversation_id("analyzer", "conv-a")
        session.set_conversation_id("reviewer", "conv-r")
        session.set_conversation_id("implementer", "conv-i")

        assert session.get_conversation_id("analyzer") == "conv-a"
        assert session.get_conversation_id("reviewer") == "conv-r"
        assert session.get_conversation_id("implementer") == "conv-i"


class TestIntegration:
    """統合テスト - Session 3原則のシナリオ."""

    def test_design_workflow_scenario(self) -> None:
        """DesignWorkflow 典型シナリオ: DESIGN ↔ DESIGN_REVIEW ループ."""
        session = SessionState()

        # 1. analyzer セッション開始
        session.set_conversation_id("analyzer", "conv-001")
        assert session.get_conversation_id("analyzer") == "conv-001"

        # 2. DESIGN ループ
        for i in range(3):
            count = session.increment_loop("design")
            assert count == i + 1

            if session.is_loop_exceeded("design", max_count=3):
                break

        # 3. ループ上限到達
        assert session.is_loop_exceeded("design", max_count=3) is True

        # 4. リセット後は再開可能
        session.reset_loop("design")
        assert session.is_loop_exceeded("design", max_count=3) is False

    def test_session_continuity_on_retry(self) -> None:
        """RETRY 遷移時のセッション継続."""
        session = SessionState()

        # DESIGN ステートで analyzer セッション設定
        session.set_conversation_id("analyzer", "conv-design")

        # RETRY 遷移: セッションは変更しない
        # (DESIGN に戻る際、同一 analyzer セッションを継続)
        original = session.get_conversation_id("analyzer")
        assert original == "conv-design"

    def test_session_new_on_phase_change(self) -> None:
        """フェーズ切替時の新セッション."""
        session = SessionState()

        # DESIGN フェーズ
        session.set_conversation_id("analyzer", "conv-design")

        # IMPLEMENT フェーズに切替: implementer に新セッション
        session.set_conversation_id("implementer", "conv-implement")

        assert session.get_conversation_id("analyzer") == "conv-design"
        assert session.get_conversation_id("implementer") == "conv-implement"
