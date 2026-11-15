"""Tests: ``kaji_harness.watch.notify`` (Issue #543 D5)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from kaji_harness.watch import notify
from kaji_harness.watch.notify import build_payload, send_notification

_FIELDS = {
    "schema_version",
    "kind",
    "repo",
    "issue",
    "summary",
    "comment_url",
    "pane_id",
    "event_key",
    "generated_at",
}


@pytest.mark.small
def test_payload_has_schema_v2_fields() -> None:
    payload = build_payload("watch_exit", repo="o/r", issue=None, summary="done")
    assert set(payload) == _FIELDS
    assert payload["schema_version"] == 2
    assert payload["issue"] is None


@pytest.mark.small
def test_unset_command_is_not_called(tmp_path: Path) -> None:
    assert (
        send_notification(
            None, build_payload("aborted", repo=None, issue=1, summary="x"), cwd=tmp_path
        )
        is False
    )


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "notify.sh"
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.medium
def test_payload_reaches_stdin(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    script = _script(tmp_path, f'cat > "{out}"\necho "$#" > "{tmp_path / "argc"}"')
    payload = build_payload("aborted", repo="o/r", issue=101, summary="run ended ERROR")
    assert send_notification([str(script)], payload, cwd=tmp_path) is True
    assert json.loads(out.read_text(encoding="utf-8"))["summary"] == "run ended ERROR"
    # argv には notify_command 以外を足さない
    assert (tmp_path / "argc").read_text(encoding="utf-8").strip() == "0"


@pytest.mark.medium
def test_failure_warns_without_stderr_body(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    script = _script(tmp_path, 'echo "SECRET-TOKEN" >&2\nexit 7')
    payload = build_payload("handoff", repo="o/r", issue=101, summary="SECRET-SUMMARY")
    with caplog.at_level(logging.WARNING, logger="kaji.watch"):
        assert send_notification([str(script)], payload, cwd=tmp_path) is False
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "exit=7" in text
    assert "SECRET-TOKEN" not in text
    assert "SECRET-SUMMARY" not in text


@pytest.mark.medium
def test_timeout_warns_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify, "NOTIFY_TIMEOUT_SECONDS", 1)
    script = _script(tmp_path, "sleep 5")
    payload = build_payload("aborted", repo=None, issue=1, summary="x")
    with caplog.at_level(logging.WARNING, logger="kaji.watch"):
        assert send_notification([str(script)], payload, cwd=tmp_path) is False
    assert any("timed out" in record.getMessage() for record in caplog.records)


@pytest.mark.medium
def test_missing_command_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    payload = build_payload("aborted", repo=None, issue=1, summary="x")
    with caplog.at_level(logging.WARNING, logger="kaji.watch"):
        assert send_notification([str(tmp_path / "nope")], payload, cwd=tmp_path) is False
    assert any("failed to start" in record.getMessage() for record in caplog.records)
