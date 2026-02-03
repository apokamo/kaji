"""Run logger for workflow execution.

This module provides:
- RunLogger: JSONL format execution logger for workflow orchestration
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RunLogger:
    """JSONL format execution logger.

    Output: artifacts/<workflow>/<timestamp>/run.log
    """

    def __init__(self, log_path: Path):
        """Initialize logger.

        Args:
            log_path: Path to log file
        """
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, event: str, **kwargs: Any) -> None:
        """Write event in JSONL format."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **kwargs,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_run_start(self, issue_url: str, run_id: str) -> None:
        """Log run start event."""
        self._log("run_start", issue_url=issue_url, run_id=run_id)

    def log_state_enter(self, state: str, session_id: str | None = None) -> None:
        """Log state enter event."""
        kwargs: dict[str, Any] = {"state": state}
        if session_id:
            kwargs["session_id"] = session_id
        self._log("state_enter", **kwargs)

    def log_state_exit(self, state: str, result: str, next_state: str) -> None:
        """Log state exit event."""
        self._log("state_exit", state=state, result=result, next=next_state)

    def log_run_end(
        self,
        status: str,
        loop_counters: dict[str, int],
        error: str | None = None,
    ) -> None:
        """Log run end event."""
        kwargs: dict[str, Any] = {"status": status, "loop_counters": loop_counters}
        if error:
            kwargs["error"] = error
        self._log("run_end", **kwargs)
