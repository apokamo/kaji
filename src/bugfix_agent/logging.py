"""Logging utilities for Bugfix Agent v5

This module provides:
- warn: Output warning messages to stderr and log files
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from .config import get_workdir


def warn(message: str, log_dir: Path | None = None) -> None:
    """警告を出力（コンソールとログファイル両方）

    設計書の警告出力仕様:
    - cli_console.log: `[WARN] {timestamp} {message}` 形式
    - run.log: JSONL `{"event": "warning", "message": "...", "timestamp": "..."}`

    Args:
        message: 警告メッセージ
        log_dir: ログ出力ディレクトリ（None の場合は get_workdir() を使用）

    Example:
        warn("Skipping path outside allowed_root: /etc/passwd")
        # Output to stderr: [WARN] 2026-01-28T10:00:00 Skipping path outside ...
        # Output to cli_console.log: [WARN] 2026-01-28T10:00:00 Skipping path ...
        # Output to run.log: {"event": "warning", "message": "...", "timestamp": "..."}
    """
    timestamp = datetime.now().isoformat()
    console_msg = f"[WARN] {timestamp} {message}"

    # 1. stderr に出力
    print(console_msg, file=sys.stderr)

    # 2. ログファイルに出力
    if log_dir is None:
        try:
            log_dir = get_workdir()
        except Exception:
            # workdir が取得できない場合は stderr 出力のみ
            return

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # cli_console.log に追記
        cli_console_path = log_dir / "cli_console.log"
        with cli_console_path.open("a", encoding="utf-8") as f:
            f.write(console_msg + "\n")

        # run.log に JSONL 形式で追記
        run_log_path = log_dir / "run.log"
        log_entry = {
            "event": "warning",
            "message": message,
            "timestamp": timestamp,
        }
        with run_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except (OSError, PermissionError):
        # ログファイルへの書き込みに失敗しても、stderr 出力は完了しているので無視
        pass
