"""External notification via ``notify_command`` (Issue #543 D5).

通知の JSON（schema v1）を ``notify_command`` の標準入力に渡す。argv には
``notify_command`` 以外を足さない（秘密を argv に載せない）。失敗は警告して続行し、
ログには argv[0] と exit code だけを出す（stderr の本文は秘密を含みうるため出さない）。
再送はしない（at-most-once）。
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

NOTIFY_SCHEMA_VERSION = 1
NOTIFY_TIMEOUT_SECONDS = 30

NotifyKind = Literal["aborted", "handoff", "watch_exit"]

_console = logging.getLogger("kaji.watch")


def build_payload(
    kind: NotifyKind,
    *,
    repo: str | None,
    issue: int | None,
    summary: str,
    comment_url: str | None = None,
    pane_id: str | None = None,
    event_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """通知の JSON（schema v1）を組み立てる。"""
    return {
        "schema_version": NOTIFY_SCHEMA_VERSION,
        "kind": kind,
        "repo": repo,
        "issue": issue,
        "summary": summary,
        "comment_url": comment_url,
        "pane_id": pane_id,
        "event_key": event_key,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
    }


def send_notification(
    command: Sequence[str] | None, payload: dict[str, object], *, cwd: Path
) -> bool:
    """``notify_command`` を 1 回だけ呼ぶ。

    Args:
        command: ``[watch].notify_command``。``None`` なら呼ばない。
        payload: ``build_payload`` の値。
        cwd: 実行ディレクトリ（repo root）。

    Returns:
        送信に成功したか（未設定・失敗は ``False``）。
    """
    if not command:
        return False
    argv = list(command)
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=NOTIFY_TIMEOUT_SECONDS,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _console.warning("notify_command timed out: %s", argv[0])
        return False
    except OSError as exc:
        _console.warning("notify_command failed to start: %s (%s)", argv[0], type(exc).__name__)
        return False
    if completed.returncode != 0:
        _console.warning("notify_command failed: %s exit=%d", argv[0], completed.returncode)
        return False
    return True
