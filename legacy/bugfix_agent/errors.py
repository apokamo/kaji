"""Custom exceptions for Bugfix Agent v5

This module defines all custom exception classes:
- ToolError: AI tool returned an error
- LoopLimitExceeded: Circuit breaker for loop count
- VerdictParseError: Failed to parse VERDICT format
- AgentAbortError: Agent returned ABORT verdict
"""


class ToolError(Exception):
    """AI ツールがエラーを返した場合の例外"""

    pass


class LoopLimitExceeded(Exception):
    """ループ回数制限を超過した場合の例外（Circuit Breaker）"""

    pass


class VerdictParseError(Exception):
    """VERDICT形式のパースに失敗した場合の例外

    This is raised when no Result/Status line is found.
    Recoverable via fallback parsing (Step 2) or AI formatter (Step 3).
    """

    pass


class InvalidVerdictValueError(VerdictParseError):
    """VERDICT値が不正な場合の例外

    This is raised when a Result/Status line is found but contains an invalid value.
    NOT recoverable via fallback - indicates a prompt violation or implementation bug.
    """

    pass


class AgentAbortError(Exception):
    """エージェントがABORTを返した場合の例外

    Attributes:
        reason: ABORT理由
        suggestion: 次のアクション提案
    """

    def __init__(self, reason: str, suggestion: str = ""):
        self.reason = reason
        self.suggestion = suggestion
        super().__init__(f"Agent aborted: {reason}")


def check_tool_result(result: str, tool_name: str) -> str:
    """ツール結果をチェックし、ERROR なら例外を投げる

    Args:
        result: ツールの戻り値
        tool_name: ツール名（エラーメッセージ用）

    Returns:
        result をそのまま返す（ERROR でない場合）

    Raises:
        ToolError: result が "ERROR" の場合
    """
    if result == "ERROR":
        raise ToolError(f"{tool_name} returned ERROR")
    return result
