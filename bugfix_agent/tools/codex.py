"""Codex CLI tool wrapper for Bugfix Agent v5

This module provides:
- CodexTool: Reviewer for code review, judgment, web search tasks
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..cli import run_cli_streaming
from ..config import get_config_value, get_workdir
from ..context import build_context

if TYPE_CHECKING:
    from ..run_logger import RunLogger



class CodexTool:
    """Codex CLI ラッパー（レビュー・判断担当）

    論理的思考と外部情報収集に強い AI。レビューと判断を担当。
    """

    def __init__(
        self,
        model: str | None = None,
        workdir: str | None = None,
        sandbox: str | None = None,
    ):
        """
        Args:
            model: モデル名（None で config.toml から取得）
            workdir: 作業ディレクトリ（None で自動検出）
            sandbox: サンドボックスモード（None で config.toml から取得）
        """
        self.model = model or get_config_value("tools.codex.model", "gpt-5.1-codex")
        self.workdir = workdir or str(get_workdir())
        self.sandbox = sandbox or get_config_value("tools.codex.sandbox", "workspace-write")
        self.timeout = get_config_value("tools.codex.timeout", 300)

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Codex CLI を実行する

        Args:
            prompt: 実行する指示/質問
            context: コンテキスト情報（config の context_max_chars まで）
            session_id: 継続するセッションの ID（thread_id）
            log_dir: ログ保存ディレクトリ（None で保存しない）
            logger: 実行ロガー

        Returns:
            (応答テキスト, 新しいセッション ID)
        """
        print("🟢 [Codex] Judging...")

        # コンテキストを構築（共通ユーティリティ使用、max_chars は config から）
        context_str = build_context(context)
        full_prompt = f"{prompt}\n\nTarget Content to Review:\n{context_str}"

        # CLI 引数を構築
        # Note: --json は codex exec resume でサポートされないため、
        #       resume モードではテキスト出力を使用
        if session_id:
            # resume モード（--json なし）
            # Note: --dangerously-bypass-approvals-and-sandbox is global; --skip-git-repo-check is exec subcommand option
            # Note: -s オプションは resume で使用不可のため、-c sandbox_mode= でオーバーライド
            # Ref: docs/technical/shared/tools/codex-cli-reference.md section 3.4
            args = [
                "codex",
                "--dangerously-bypass-approvals-and-sandbox",
                "exec",
                "--skip-git-repo-check",
                "resume",
                session_id,
                "-c",
                'sandbox_mode="danger-full-access"',
                "-c",
                "sandbox_workspace_write.network_access=true",
            ]
        else:
            # 新規セッション
            # Note: --dangerously-bypass-approvals-and-sandbox is global; --skip-git-repo-check is exec subcommand option
            args = [
                "codex",
                "--dangerously-bypass-approvals-and-sandbox",
                "exec",
                "--skip-git-repo-check",
                "-m",
                self.model,
                "-C",
                self.workdir,
                "-s",
                self.sandbox,
                "--enable",
                "web_search_request",
                "--json",
            ]
        args.append(full_prompt)

        # CLI 実行（ストリーミング）
        timeout = self.timeout if self.timeout > 0 else None
        try:
            stdout, stderr, returncode = run_cli_streaming(
                args, timeout=timeout, log_dir=log_dir, tool_name="codex"
            )
            if returncode != 0:
                print(f"❌ Codex Error: {stderr}")
                return "ERROR", session_id
        except FileNotFoundError:
            print("❌ Codex CLI not found. Is 'codex' installed and in PATH?")
            return "ERROR", session_id
        except subprocess.TimeoutExpired:
            print(f"❌ Codex timeout after {self.timeout}s")
            return "ERROR", session_id

        # JSON Lines パース（新規セッション時）/ テキストパース（resume時）
        new_session_id = session_id
        assistant_replies: list[str] = []  # 全ての agent_message を収集

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # JSON以外のテキスト行（VERDICTを含む可能性）を収集
                # Note: mcp_tool_callモードではVERDICTがプレーンテキストとして出力される
                assistant_replies.append(line)
                continue

            # セッション ID 取得（新規セッション時のみ）
            if payload.get("type") == "thread.started" and not new_session_id:
                new_session_id = payload.get("thread_id", new_session_id)

            # アシスタント応答取得（全ての agent_message を収集）
            if payload.get("type") == "item.completed":
                item = payload.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        assistant_replies.append(text)

        # 全ての応答を結合（VERDICTが途中にあっても検出可能）
        assistant_reply = "\n\n".join(assistant_replies) if assistant_replies else ""

        # JSON が取れなかった場合は素の stdout を返す
        if not assistant_reply:
            assistant_reply = stdout.strip()

        return assistant_reply, new_session_id
