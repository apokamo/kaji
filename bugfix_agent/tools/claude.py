"""Claude Code CLI tool wrapper for Bugfix Agent v5

This module provides:
- ClaudeTool: Implementer for file operations, command execution tasks
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cli import run_cli_streaming
from ..config import get_config_value
from ..context import build_context

if TYPE_CHECKING:
    pass


class ClaudeTool:
    """Claude Code CLI ラッパー（実装・操作担当）

    ファイル操作・コマンド実行が可能な AI。実装と操作を担当。
    """

    def __init__(
        self,
        model: str | None = None,
        permission_mode: str | None = None,
    ):
        """
        Args:
            model: モデル名（None で config.toml から取得）
            permission_mode: 権限モード（None で config.toml から取得）
        """
        self.model = model or get_config_value("tools.claude.model", "opus")
        self.permission_mode = permission_mode or get_config_value(
            "tools.claude.permission_mode", "default"
        )
        self.timeout = get_config_value("tools.claude.timeout", 600)

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Claude Code CLI を実行する

        Args:
            prompt: 実行する指示/質問
            context: コンテキスト情報
            session_id: 継続するセッションの ID
            log_dir: ログ保存ディレクトリ（None で保存しない）
            logger: 実行ロガー

        Returns:
            (応答テキスト, 新しいセッション ID)
        """
        print("🟠 [Claude] Acting...")

        # コンテキストを構築（共通ユーティリティを使用、Claude は max_chars=0 で無制限）
        context_str = build_context(context, max_chars=0)
        full_prompt = f"{prompt}\n\nContext:\n{context_str}" if context_str else prompt

        # CLI 引数を構築
        # Use stream-json for real-time output display (requires --verbose)
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if self.model:
            args += ["--model", self.model]
        if self.permission_mode != "default":
            args += ["--permission-mode", self.permission_mode]
        if session_id:
            args += ["-r", session_id]

        # Note: Non-interactive mode requires tools to be allowed via:
        # 1. ~/.claude/settings.json "permissions.allow" (recommended, system-wide)
        # 2. CLI --allowedTools flag (per-session override)
        # 3. CLI --dangerously-skip-permissions flag (skips all permission checks)
        # Current setup uses --dangerously-skip-permissions for WSL closed environment.
        # Skip all permission checks (WSL closed development environment)
        args.append("--dangerously-skip-permissions")

        args.append(full_prompt)

        # 環境変数設定（デバッグ/キャッシュディレクトリ）
        env = os.environ.copy()
        debug_dir = env.get("CLAUDE_DEBUG_DIR", "/tmp/claude-debug")
        cache_dir = env.get("CLAUDE_CACHE_DIR", "/tmp/claude-cache")
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        env["CLAUDE_DEBUG_DIR"] = debug_dir
        env["CLAUDE_CACHE_DIR"] = cache_dir

        # CLI 実行（ストリーミング）
        timeout = self.timeout if self.timeout > 0 else None
        try:
            stdout, stderr, returncode = run_cli_streaming(
                args, timeout=timeout, env=env, log_dir=log_dir, tool_name="claude"
            )
            if returncode != 0:
                print(f"❌ Claude Error: {stderr}")
                if stdout:
                    print(stdout.strip())
                return "ERROR", session_id
        except FileNotFoundError:
            print("❌ Claude CLI not found. Is 'claude' installed and in PATH?")
            return "ERROR", session_id
        except subprocess.TimeoutExpired:
            print(f"❌ Claude timeout after {self.timeout}s")
            return "ERROR", session_id

        # JSON パース（ノイズ混入対策: 正規表現で JSON 部分を抽出）
        response, new_session_id = self._parse_json_output(stdout, session_id)

        return response, new_session_id

    def _parse_json_output(self, stdout: str, session_id: str | None) -> tuple[str, str | None]:
        """CLIの出力からJSON部分を抽出してパースする

        stream-json形式（複数行JSON）とjson形式（単一JSON）の両方に対応。
        """
        # stream-json形式: 複数行のJSONから "type":"result" の行を探す
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if payload.get("type") == "result":
                    return self._extract_from_payload(payload, stdout, session_id)
            except json.JSONDecodeError:
                continue

        # 従来のjson形式: 全体を単一JSONとしてパース
        try:
            payload = json.loads(stdout)
            return self._extract_from_payload(payload, stdout, session_id)
        except json.JSONDecodeError:
            pass

        # 失敗した場合、正規表現でJSON部分を抽出
        json_match = re.search(r'\{[^{}]*"result"[^{}]*\{.*?\}[^{}]*\}', stdout, re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group())
                return self._extract_from_payload(payload, stdout, session_id)
            except json.JSONDecodeError:
                pass

        # それでも失敗した場合は素の出力を返す
        return stdout.strip(), session_id

    def _extract_from_payload(
        self, payload: dict[str, Any], stdout: str, session_id: str | None
    ) -> tuple[str, str | None]:
        """パース済みペイロードから応答とセッションIDを抽出

        stream-json形式: {"type":"result","result":"text","session_id":"uuid"}
        json形式: {"result":{"text":"...","session_id":"..."}}
        """
        result_data = payload.get("result", {})

        # stream-json 形式: result は文字列、session_id はトップレベル
        if isinstance(result_data, str):
            response = result_data if result_data else stdout
            new_session_id = session_id or payload.get("session_id")
        # json 形式: result は辞書
        elif isinstance(result_data, dict):
            response = result_data.get("text", stdout)
            new_session_id = session_id or result_data.get("session_id")
        else:
            response = stdout
            new_session_id = session_id

        return response, new_session_id
