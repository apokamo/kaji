"""issue-close SKILL.md の jq 検証が producer model (I1〜I9) と同じ判定をすることを確認する。

consumer（SKILL.md の jq）と producer（``ReviewPollEvidence``）は別実装のため、
時刻不変条件（小数秒・暦日）の境界で判定が乖離しないことを差分で固定する。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from kaji_harness.review_poll_evidence import ReviewPollEvidence
from tests.test_review_poll_evidence import _VIOLATIONS, _set, _valid_payload

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_JQ_RE = re.compile(r"VERIFIED=\$\(jq -r '\n(?P<program>.*?)\n' \"\$EVIDENCE\"\)", re.DOTALL)

_REACTION = ("approval", "reaction", "created_at")
_COMPLETED = ("approval", "review_summary", "completed_at")
_UPDATED = ("approval", "review_summary", "comment_updated_at")
_COMMITTED = ("reviewed_head", "committed_at")

_TIME_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "fetched_after_decided_by_fraction",
        _set(
            _set(_valid_payload(), ("fetched_at",), "2026-09-08T01:33:41.9Z"),
            ("decided_at",),
            "2026-09-08T01:33:41.1Z",
        ),
    ),
    (
        "reaction_before_commit_by_fraction",
        _set(
            _set(_valid_payload(), _COMMITTED, "2026-09-08T01:33:35.9Z"),
            _REACTION,
            "2026-09-08T01:33:35Z",
        ),
    ),
    (
        "fetched_equals_decided_fraction_ok",
        _set(
            _set(_valid_payload(), ("fetched_at",), "2026-09-08T01:33:41.5Z"),
            ("decided_at",),
            "2026-09-08T01:33:41.5Z",
        ),
    ),
    ("reaction_fraction_ok", _set(_valid_payload(), _REACTION, "2026-09-08T01:33:35.123456Z")),
    ("nanosecond_fraction_ok", _set(_valid_payload(), _REACTION, "2026-09-08T01:33:35.123456789Z")),
    ("invalid_calendar_day", _set(_valid_payload(), _COMMITTED, "2026-02-30T01:23:50Z")),
    ("invalid_hour", _set(_valid_payload(), _COMMITTED, "2026-09-08T24:23:50Z")),
    ("invalid_second", _set(_valid_payload(), _COMMITTED, "2026-09-08T01:23:60Z")),
    (
        "invalid_calendar_day_with_fraction",
        _set(_valid_payload(), ("fetched_at",), "2026-04-31T01:33:41.5Z"),
    ),
    ("same_second_as_completed", _set(_valid_payload(), _REACTION, "2026-09-08T01:33:32Z")),
    ("next_second_after_completed_ok", _set(_valid_payload(), _REACTION, "2026-09-08T01:33:33Z")),
    (
        "updated_same_second_as_completed_ok",
        _set(_valid_payload(), _UPDATED, "2026-09-08T01:33:32Z"),
    ),
    ("updated_before_completed_second", _set(_valid_payload(), _UPDATED, "2026-09-08T01:33:31.9Z")),
    ("not_utc_z", _set(_valid_payload(), _COMMITTED, "2026-09-08T01:23:50+00:00")),
]


def _jq_program() -> str:
    match = _JQ_RE.search(_SKILL.read_text(encoding="utf-8"))
    assert match, "issue-close SKILL.md の jq プログラムを抽出できない"
    return match.group("program")


def _consumer_verdict(payload: dict[str, Any], tmp_path: Path) -> str:
    evidence = tmp_path / "review-poll-evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        ["jq", "-r", _jq_program(), str(evidence)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _producer_accepts(payload: dict[str, Any]) -> bool:
    try:
        ReviewPollEvidence.model_validate(payload)
    except ValidationError:
        return False
    return True


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
class TestIssueCloseJqMatchesModel:
    def test_valid_payload_is_ok(self, tmp_path: Path) -> None:
        assert _consumer_verdict(_valid_payload(), tmp_path) == "OK"

    @pytest.mark.parametrize(
        "payload", [p for _, p in _VIOLATIONS], ids=[name for name, _ in _VIOLATIONS]
    )
    def test_model_violations_are_rejected_by_jq(
        self, payload: dict[str, Any], tmp_path: Path
    ) -> None:
        assert _consumer_verdict(payload, tmp_path) != "OK"

    @pytest.mark.parametrize(
        "payload", [p for _, p in _TIME_CASES], ids=[n for n, _ in _TIME_CASES]
    )
    def test_time_boundaries_agree_with_model(
        self, payload: dict[str, Any], tmp_path: Path
    ) -> None:
        consumer_ok = _consumer_verdict(payload, tmp_path) == "OK"
        assert consumer_ok == _producer_accepts(payload)
