"""Logging utilities for Bugfix Agent v5

This module provides:
- warn: Output warning messages to stderr with timestamp
"""

import sys
from datetime import datetime


def warn(message: str) -> None:
    """警告を出力（コンソールとログファイル両方）

    設計書の警告出力仕様:
    - cli_console.log: `[WARN] {timestamp} {message}` 形式
    - run.log: JSONL `{"event": "warning", "message": "...", "timestamp": "..."}`

    現在の実装:
    - stderr に `[WARN] {timestamp} {message}` 形式で出力
    - cli_console.log / run.log への追記は将来実装

    Args:
        message: 警告メッセージ

    Example:
        warn("Skipping path outside allowed_root: /etc/passwd")
        # Output: [WARN] 2026-01-28T10:00:00 Skipping path outside allowed_root: /etc/passwd
    """
    timestamp = datetime.now().isoformat()
    console_msg = f"[WARN] {timestamp} {message}"
    print(console_msg, file=sys.stderr)

    # TODO: cli_console.log / run.log への追記
    # - cli_console.log: ハンドラ実行コンテキストで log_dir が設定されている場合
    # - run.log: RunLogger インスタンスが利用可能な場合
