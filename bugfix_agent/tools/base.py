"""Base tool definitions for Bugfix Agent v5

This module provides:
- AIToolProtocol: Interface for all AI tools
- MockTool: Test mock implementation
"""

from pathlib import Path
from typing import Protocol

from ..run_logger import RunLogger


class AIToolProtocol(Protocol):
    """AI CLI ツールの統一インターフェース

    すべての AI ツール（Gemini, Codex, Claude）が実装すべきインターフェース。
    テスト時は MockTool で差し替え可能。
    """

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """AI ツールを実行する。

        Args:
            prompt: 実行する指示/質問
            context: コンテキスト情報
                - str: テキストとして渡す（Codex 向け）
                - list[str]: ファイルパスリスト（Gemini 向け）
                - 各実装で適切に処理
            session_id: 継続するセッションの ID（None で新規）
            log_dir: ログ保存ディレクトリ（None で保存しない）
            logger: 実行ロガー

        Returns:
            tuple[str, str | None]: (応答テキスト, 新しいセッション ID)
        """
        ...


class MockTool:
    """テスト用モックツール

    予め設定した応答を順番に返す。セッション ID は自動生成。
    """

    def __init__(self, responses: list[str]):
        """
        Args:
            responses: 返す応答のリスト（順番に消費される）
        """
        self._responses = iter(responses)
        self._session_counter = 0

    def run(
        self,
        prompt: str,  # noqa: ARG002 - interface compatibility
        context: str | list[str] = "",  # noqa: ARG002 - interface compatibility
        session_id: str | None = None,
        log_dir: Path | None = None,  # noqa: ARG002 - interface compatibility
    ) -> tuple[str, str | None]:
        """設定された応答を順番に返す（prompt, context, log_dir は interface 互換のため受け取るが使用しない）"""
        del prompt, context, log_dir  # Explicitly mark as unused (Pylance)
        response = next(self._responses, "MOCK_RESPONSE")
        self._session_counter += 1
        new_session = session_id or f"mock-session-{self._session_counter}"
        return (response, new_session)
