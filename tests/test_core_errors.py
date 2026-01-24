"""Tests for core error classes."""

import pytest

from src.core.errors import LoopLimitExceededError


class TestLoopLimitExceededError:
    """LoopLimitExceededError テスト."""

    def test_basic_creation(self) -> None:
        """基本的なエラー作成ができること."""
        error = LoopLimitExceededError(state="design", count=3, max_count=3)

        assert error.state == "design"
        assert error.count == 3
        assert error.max_count == 3

    def test_error_message(self) -> None:
        """エラーメッセージが適切にフォーマットされること."""
        error = LoopLimitExceededError(state="design", count=5, max_count=3)

        assert "design" in str(error)
        assert "5" in str(error)
        assert "3" in str(error)
        assert "Loop limit exceeded" in str(error) or "loop" in str(error).lower()

    def test_is_exception(self) -> None:
        """Exception のサブクラスであること."""
        error = LoopLimitExceededError(state="design", count=3, max_count=3)

        assert isinstance(error, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """raise して catch できること."""
        with pytest.raises(LoopLimitExceededError) as exc_info:
            raise LoopLimitExceededError(state="review", count=4, max_count=3)

        assert exc_info.value.state == "review"
        assert exc_info.value.count == 4
        assert exc_info.value.max_count == 3
