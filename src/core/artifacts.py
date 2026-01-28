"""Artifact storage utilities.

This module provides functions for saving workflow artifacts and event logs.
All artifact operations are best-effort and do not block workflow execution.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def save_artifact(
    artifacts_dir: Path,
    filename: str,
    content: str,
    *,
    append: bool = False,
) -> Path | None:
    """Save artifact to the specified directory.

    Best-effort operation: IO failures are logged as warnings but do not
    stop workflow execution.

    Args:
        artifacts_dir: Directory to save to (must exist).
        filename: Name of the artifact file.
        content: Content to save.
        append: If True, append to existing file.

    Returns:
        Path to the saved file, or None if save failed.
    """
    filepath = artifacts_dir / filename
    mode = "a" if append else "w"
    try:
        with open(filepath, mode, encoding="utf-8") as f:
            f.write(content)
        return filepath
    except OSError as e:
        # Artifact save failure does not stop workflow (best-effort)
        print(f"Warning: Failed to save artifact {filename}: {e}", file=sys.stderr)
        return None


def save_jsonl_log(
    artifacts_dir: Path,
    event_type: str,
    data: dict[str, object],
    *,
    log_filename: str = "run.log",
) -> None:
    """Append event to JSONL log file.

    Best-effort logging: IO failures are logged as warnings but do not
    stop workflow execution.

    設計書仕様:
    - run.log: {workdir}/artifacts/ に出力（ハンドラ実行ログ）
    - 内容: handler_start, ai_call_*, verdict_*, workflow_* イベント

    Args:
        artifacts_dir: Directory containing log file.
        event_type: Type of event (e.g., "ai_call", "verdict").
        data: Event data dictionary.
        log_filename: Name of log file (default: "run.log").
    """
    log_path = artifacts_dir / log_filename
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        **data,
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        # Log failure does not stop workflow (best-effort)
        print(f"Warning: Failed to write event log: {e}", file=sys.stderr)
