"""AI tool error classes."""


class AIToolError(Exception):
    """Base class for AI tool execution errors."""

    def __init__(self, message: str, stderr: str = "", returncode: int | None = None) -> None:
        """Initialize error.

        Args:
            message: Error message
            stderr: Standard error output from CLI
            returncode: Exit code from CLI process
        """
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


class AIToolNotFoundError(AIToolError):
    """CLI not found error."""

    pass


class AIToolTimeoutError(AIToolError):
    """Timeout error."""

    pass


class AIToolExecutionError(AIToolError):
    """Execution error (non-zero exit)."""

    pass
