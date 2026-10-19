"""issue-close SKILL.md の jq 検証が producer model（schema v2 の I1〜I9）と同じ判定をすることを確認する。

consumer（SKILL.md の jq）と producer（``ReviewPollEvidenceV2``）は別実装のため、
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

from kaji_harness.review_poll_evidence import ReviewPollEvidenceV2
from tests.test_review_poll_evidence import (
    _SECURITY,
    _STATE,
    _VALID_SECURITY_V2,
    _VIOLATIONS,
    _VIOLATIONS_V2,
    _set,
    _v1_payload,
    _valid_payload_v2,
)

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_JQ_RE = re.compile(r"VERIFIED=\$\(jq -r '\n(?P<program>.*?)\n' \"\$EVIDENCE\"\)", re.DOTALL)

_REACTION = ("approval", "reaction", "created_at")
_COMMITTED = ("reviewed_head", "committed_at")

_TIME_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "fetched_after_decided_by_fraction",
        _set(
            _set(_valid_payload_v2(), ("fetched_at",), "2026-09-08T01:33:41.9Z"),
            ("decided_at",),
            "2026-09-08T01:33:41.1Z",
        ),
    ),
    (
        "reaction_before_commit_by_fraction",
        _set(
            _set(_valid_payload_v2(), _COMMITTED, "2026-09-08T01:33:35.9Z"),
            _REACTION,
            "2026-09-08T01:33:35Z",
        ),
    ),
    (
        "fetched_equals_decided_fraction_ok",
        _set(
            _set(_valid_payload_v2(), ("fetched_at",), "2026-09-08T01:33:41.5Z"),
            ("decided_at",),
            "2026-09-08T01:33:41.5Z",
        ),
    ),
    ("reaction_fraction_ok", _set(_valid_payload_v2(), _REACTION, "2026-09-08T01:33:35.123456Z")),
    (
        "nanosecond_fraction_ok",
        _set(_valid_payload_v2(), _REACTION, "2026-09-08T01:33:35.123456789Z"),
    ),
    ("invalid_calendar_day", _set(_valid_payload_v2(), _COMMITTED, "2026-02-30T01:23:50Z")),
    ("invalid_hour", _set(_valid_payload_v2(), _COMMITTED, "2026-09-08T24:23:50Z")),
    ("invalid_second", _set(_valid_payload_v2(), _COMMITTED, "2026-09-08T01:23:60Z")),
    (
        "invalid_calendar_day_with_fraction",
        _set(_valid_payload_v2(), ("fetched_at",), "2026-04-31T01:33:41.5Z"),
    ),
    ("not_utc_z", _set(_valid_payload_v2(), _COMMITTED, "2026-09-08T01:23:50+00:00")),
]


_V2_REACTION_AT = "2026-09-08T01:33:35Z"

_TIME_CASES_V2: list[tuple[str, dict[str, Any]]] = [
    (
        "v2_reaction_next_second_after_fractional_update_ok",
        _set(_valid_payload_v2(), (*_STATE, "comment_updated_at"), "2026-09-08T01:33:34.9Z"),
    ),
    (
        "v2_reaction_same_second_as_fractional_update",
        _set(_valid_payload_v2(), (*_STATE, "comment_updated_at"), "2026-09-08T01:33:35.1Z"),
    ),
    (
        "v2_update_invalid_calendar_day",
        _set(_valid_payload_v2(), (*_STATE, "comment_updated_at"), "2026-02-30T01:33:33Z"),
    ),
    (
        "v2_reaction_before_commit_by_fraction",
        _set(_valid_payload_v2(), ("reviewed_head", "committed_at"), "2026-09-08T01:33:35.5Z"),
    ),
]

# producer と consumer の判定が一致すべき v2 の境界（受理・拒否の両方を含む）。
_AGREEMENT_V2: list[tuple[str, dict[str, Any]]] = [
    *_TIME_CASES,
    *_TIME_CASES_V2,
    *[
        (f"v2_security_{name}", _set(_valid_payload_v2(), _SECURITY, security))
        for name, security in _VALID_SECURITY_V2
    ],
    *_VIOLATIONS_V2,
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


def _producer_accepts_v2(payload: dict[str, Any]) -> bool:
    try:
        ReviewPollEvidenceV2.model_validate(payload)
    except ValidationError:
        return False
    return True


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
class TestIssueCloseJqMatchesModelV2:
    def test_valid_payload_is_ok(self, tmp_path: Path) -> None:
        assert _consumer_verdict(_valid_payload_v2(), tmp_path) == "OK"

    @pytest.mark.parametrize(
        "payload",
        [p for _, p in [*_VIOLATIONS, *_VIOLATIONS_V2]],
        ids=[name for name, _ in [*_VIOLATIONS, *_VIOLATIONS_V2]],
    )
    def test_model_violations_are_rejected_by_jq(
        self, payload: dict[str, Any], tmp_path: Path
    ) -> None:
        assert _consumer_verdict(payload, tmp_path) != "OK"

    @pytest.mark.parametrize(
        "payload", [p for _, p in _AGREEMENT_V2], ids=[n for n, _ in _AGREEMENT_V2]
    )
    def test_boundaries_agree_with_model(self, payload: dict[str, Any], tmp_path: Path) -> None:
        consumer_ok = _consumer_verdict(payload, tmp_path) == "OK"
        assert consumer_ok == _producer_accepts_v2(payload)

    def test_unknown_schema_version_is_i1(self, tmp_path: Path) -> None:
        payload = _set(_valid_payload_v2(), ("schema_version",), 3)
        assert _consumer_verdict(payload, tmp_path) == "I1"

    def test_removed_v1_is_i1(self, tmp_path: Path) -> None:
        # schema v1 は Issue #478 で撤去した。撤去前に妥当だった v1 の証跡も I1 違反になる
        assert _consumer_verdict(_v1_payload(), tmp_path) == "I1"
