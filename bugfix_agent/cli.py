"""CLI execution utilities for Bugfix Agent v5

This module provides CLI streaming execution and output formatting:
- run_cli_streaming: Execute CLI with real-time output streaming
- format_jsonl_line: Extract content from JSONL output lines
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

from .config import get_config_value


def format_jsonl_line(line: str, tool_name: str) -> str | None:
    """JSONL 行からコンテンツを抽出する

    Args:
        line: JSONL 形式の1行
        tool_name: ツール名 ("gemini", "codex", "claude")

    Returns:
        抽出したコンテンツ。抽出不可の場合は None
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        # JSON でない場合は、空でなければそのまま返す
        stripped = line.strip()
        return stripped if stripped else None

    # Gemini 形式: {"type":"response","response":{"content":[{"type":"text","text":"..."}]}}
    if tool_name == "gemini" and data.get("type") == "response":
        content = data.get("response", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else None

    # Codex 形式: {"type":"item.completed","item":{"type":"reasoning|agent_message","text":"..."}}
    #            {"type":"item.completed","item":{"type":"command_execution","aggregated_output":"..."}}
    if tool_name == "codex":
        if data.get("type") == "item.completed":
            item = data.get("item", {})
            item_type = item.get("type")
            # reasoning または agent_message は text を返す
            if item_type in ("reasoning", "agent_message"):
                text = item.get("text", "")
                return text if text else None
            # command_execution: コマンド + 出力の先頭行を表示
            if item_type == "command_execution":
                command = item.get("command", "")
                output = item.get("aggregated_output", "")
                exit_code = item.get("exit_code")
                # コマンド部分を整形（/bin/bash -lc 'cd ... && cmd' から cmd 部分を抽出）
                if " && " in command:
                    command = command.split(" && ", 1)[-1].rstrip("'")
                elif command.startswith("/bin/bash") and "'" in command:
                    # フォールバック: シングルクォート内を抽出
                    command = command.split("'", 1)[-1].rstrip("'")
                # 出力を先頭3行に制限
                max_lines = 3
                lines = output.strip().split("\n") if output else []
                if len(lines) > max_lines:
                    truncated = "\n  > ".join(lines[:max_lines])
                    result = f"$ {command}\n  > {truncated}\n  > ... ({len(lines) - max_lines} more lines)"
                elif lines:
                    result = f"$ {command}\n  > " + "\n  > ".join(lines)
                else:
                    result = f"$ {command}"
                # exit_code が非0の場合は表示
                if exit_code and exit_code != 0:
                    result += f"  [exit: {exit_code}]"
                return result
        return None

    # Claude stream-json 形式:
    # {"type":"result",...,"result":"text string"} - 最終結果（文字列）
    # {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}} - 応答
    # {"type":"system",...} - 初期化情報（スキップ）
    if tool_name == "claude":
        msg_type = data.get("type")

        # msg_type: result - 最終結果（result は文字列）
        if msg_type == "result":
            result = data.get("result")
            if isinstance(result, str) and result:
                return result
            return None

        # msg_type: assistant - 応答メッセージ（content 配列からテキスト抽出）
        if msg_type == "assistant":
            message = data.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(texts) if texts else None
            return None

        # msg_type: system 等はスキップ
        return None

    return None


def run_cli_streaming(
    args: list[str],
    timeout: int | None = None,
    verbose: bool | None = None,
    env: dict[str, str] | None = None,
    log_dir: Path | None = None,
    tool_name: str | None = None,
) -> tuple[str, str, int]:
    """CLI をストリーミング実行し、(stdout, stderr, returncode) を返す

    リアルタイムで出力を表示しながら、JSON パース用に出力をバッファリングする。
    log_dir が指定された場合、stdout.log / stderr.log / cli_console.log を保存する。
    ログファイルは即座に flush されるため、tail -f でリアルタイム監視可能。

    Args:
        args: 実行するコマンドと引数のリスト
        timeout: タイムアウト秒数（None で無制限）
        verbose: 出力をリアルタイム表示するか（None で config から取得）
        env: 環境変数（None で現在の環境を継承）
        log_dir: ログ保存ディレクトリ（None で保存しない）
        tool_name: ツール名 ("gemini", "codex", "claude")。指定時はコンテンツ抽出

    Returns:
        tuple[str, str, int]: (stdout, stderr, returncode)

    Raises:
        FileNotFoundError: コマンドが見つからない場合
        subprocess.TimeoutExpired: タイムアウトした場合

    Example:
        リアルタイムログ監視:
        $ tail -f /path/to/log_dir/cli_console.log
    """
    if verbose is None:
        verbose = get_config_value("agent.verbose", True)

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    console_lines: list[str] = []  # 整形済みコンソール出力

    # タイムアウト用タイマー（案B: threading.Timer）
    # stdout 読み取りループがブロックしても確実にプロセスを終了する
    timeout_occurred = threading.Event()
    timer: threading.Timer | None = None

    def kill_on_timeout() -> None:
        """タイムアウト時にプロセスを強制終了"""
        timeout_occurred.set()
        process.kill()

    if timeout is not None:
        timer = threading.Timer(timeout, kill_on_timeout)
        timer.start()

    # ログファイルを事前に開く（即時 flush 用）
    stdout_file = None
    stderr_file = None
    console_file = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_file = open(log_dir / "stdout.log", "w")  # noqa: SIM115
        stderr_file = open(log_dir / "stderr.log", "w")  # noqa: SIM115
        # tool_name 指定時のみコンソールログを作成
        if tool_name:
            console_file = open(log_dir / "cli_console.log", "w")  # noqa: SIM115

    try:
        # stdout をリアルタイム読み取り
        assert process.stdout is not None  # for type checker
        for line in process.stdout:
            stdout_lines.append(line)

            # ログファイルに即時書き込み
            if stdout_file:
                stdout_file.write(line)
                stdout_file.flush()

            # 整形出力（tool_name が指定されている場合）
            if tool_name:
                formatted = format_jsonl_line(line, tool_name)
                if formatted:
                    console_lines.append(formatted)
                    # コンソールログに即時書き込み
                    if console_file:
                        console_file.write(formatted + "\n")
                        console_file.flush()
                    if verbose:
                        print(formatted, flush=True)
                elif verbose:
                    # フォーマットできない行も表示（進捗確認のため）
                    print(line, end="", flush=True)
            elif verbose:
                print(line, end="", flush=True)

        # stderr を読み取り
        assert process.stderr is not None  # for type checker
        for line in process.stderr:
            if verbose:
                print(line, end="", file=sys.stderr, flush=True)
            stderr_lines.append(line)

            # ログファイルに即時書き込み
            if stderr_file:
                stderr_file.write(line)
                stderr_file.flush()

        returncode = process.wait()

        # タイムアウトが発生していた場合は例外を送出
        if timeout_occurred.is_set():
            raise subprocess.TimeoutExpired(args, timeout or 0)

    finally:
        # タイマーをキャンセル（正常完了時）
        if timer is not None:
            timer.cancel()
        # ファイルを確実にクローズ
        if stdout_file:
            stdout_file.close()
        if stderr_file:
            stderr_file.close()
        if console_file:
            console_file.close()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)

    return stdout, stderr, returncode
