"""Gemini CLI tool wrapper for Bugfix Agent v5

This module provides:
- GeminiTool: Analyzer for issue analysis, documentation, long-context tasks
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..cli import run_cli_streaming
from ..config import get_config_value
from ..context import build_context

if TYPE_CHECKING:
    from ..run_logger import RunLogger



class GeminiTool:
    """Gemini CLI ラッパー（分析・ドキュメント作成担当）

    長文コンテキストに強い AI。調査分析やドキュメント生成を担当。
    """

    def __init__(self, model: str | None = None):
        """
        Args:
            model: モデル名（None で config.toml から取得、"auto" でCLIデフォルト）
        """
        self.model = model or get_config_value("tools.gemini.model", "auto")
        self.timeout = get_config_value("tools.gemini.timeout", 300)

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Gemini CLI を実行する

        Args:
            prompt: 実行する指示/質問
            context: str なら直接追加、list[str] ならファイルパスとして読み込み
            session_id: 継続するセッションの ID
            log_dir: ログ保存ディレクトリ（None で保存しない）
            logger: 実行ロガー

        Returns:
            (応答テキスト, 新しいセッション ID)
        """
        print("🔵 [Gemini] Thinking...")

        # コンテキストを構築（共通ユーティリティ使用）
        context_data = build_context(context, max_chars=0)  # Gemini は制限なし
        full_prompt = f"{prompt}\n\nContext:\n{context_data}" if context_data else prompt

        # CLI 引数を構築
        args = ["gemini", "-o", "stream-json"]
        if self.model != "auto":
            args += ["-m", self.model]
        if session_id:
            args += ["-r", session_id]
        # Enable tools for gh/shell operations and web fetching in non-interactive mode
        # Note: Gemini CLI restricts tools by default in non-interactive mode for security.
        # Using --allowed-tools whitelist is the recommended approach.
        # - run_shell_command: for gh/shell operations
        # - web_fetch: for fetching Issue content from GitHub URLs
        args += ["--allowed-tools", "run_shell_command,web_fetch"]
        # Skip all approval prompts (WSL closed development environment)
        args += ["--approval-mode", "yolo"]
        args.append(full_prompt)

        # CLI 実行（ストリーミング）
        timeout = self.timeout if self.timeout > 0 else None
        try:
            stdout, stderr, returncode = run_cli_streaming(
                args, timeout=timeout, log_dir=log_dir, tool_name="gemini"
            )
            if returncode != 0:
                print(f"❌ Gemini Error: {stderr}")
                return "ERROR", session_id
        except FileNotFoundError:
            print("❌ Gemini CLI not found. Is 'gemini' installed and in PATH?")
            return "ERROR", session_id
        except subprocess.TimeoutExpired:
            print(f"❌ Gemini timeout after {self.timeout}s")
            return "ERROR", session_id

        # JSON Lines パース
        new_session_id = session_id
        assistant_reply = ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            # セッション ID 取得（新規セッション時のみ）
            if payload.get("type") == "init" and not new_session_id:
                new_session_id = payload.get("session_id", new_session_id)

            # アシスタント応答取得
            if payload.get("role") == "assistant":
                assistant_reply = payload.get("content", assistant_reply)

        return assistant_reply.strip(), new_session_id
