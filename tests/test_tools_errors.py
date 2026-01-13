"""Tests for AI tool error classes."""

from src.core.tools.errors import (
    AIToolError,
    AIToolExecutionError,
    AIToolNotFoundError,
    AIToolTimeoutError,
)


class TestAIToolError:
    """Tests for AIToolError base class."""

    def test_message_only(self) -> None:
        """Error with message only."""
        error = AIToolError("test message")
        assert str(error) == "test message"
        assert error.stderr == ""
        assert error.returncode is None

    def test_with_stderr(self) -> None:
        """Error with stderr."""
        error = AIToolError("test message", stderr="error output")
        assert str(error) == "test message"
        assert error.stderr == "error output"
        assert error.returncode is None

    def test_with_returncode(self) -> None:
        """Error with returncode."""
        error = AIToolError("test message", returncode=1)
        assert str(error) == "test message"
        assert error.stderr == ""
        assert error.returncode == 1

    def test_with_all_attributes(self) -> None:
        """Error with all attributes."""
        error = AIToolError("test message", stderr="error output", returncode=2)
        assert str(error) == "test message"
        assert error.stderr == "error output"
        assert error.returncode == 2


class TestAIToolNotFoundError:
    """Tests for AIToolNotFoundError."""

    def test_is_aitool_error(self) -> None:
        """AIToolNotFoundError is subclass of AIToolError."""
        error = AIToolNotFoundError("CLI not found")
        assert isinstance(error, AIToolError)
        assert isinstance(error, AIToolNotFoundError)

    def test_message(self) -> None:
        """Error message is preserved."""
        error = AIToolNotFoundError("Claude CLI not found")
        assert str(error) == "Claude CLI not found"


class TestAIToolTimeoutError:
    """Tests for AIToolTimeoutError."""

    def test_is_aitool_error(self) -> None:
        """AIToolTimeoutError is subclass of AIToolError."""
        error = AIToolTimeoutError("timeout")
        assert isinstance(error, AIToolError)
        assert isinstance(error, AIToolTimeoutError)

    def test_message(self) -> None:
        """Error message is preserved."""
        error = AIToolTimeoutError("CLI timed out after 600s")
        assert str(error) == "CLI timed out after 600s"


class TestAIToolExecutionError:
    """Tests for AIToolExecutionError."""

    def test_is_aitool_error(self) -> None:
        """AIToolExecutionError is subclass of AIToolError."""
        error = AIToolExecutionError("execution failed")
        assert isinstance(error, AIToolError)
        assert isinstance(error, AIToolExecutionError)

    def test_with_stderr_and_returncode(self) -> None:
        """Error with stderr and returncode."""
        error = AIToolExecutionError("CLI exited with code 1", stderr="some error", returncode=1)
        assert str(error) == "CLI exited with code 1"
        assert error.stderr == "some error"
        assert error.returncode == 1
