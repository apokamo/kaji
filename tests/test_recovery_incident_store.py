"""Medium tests: ローカル occurrence store の I/O（Issue #304 第1層）.

append / read の round-trip、破損行 skip、ディレクトリ自動作成を検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.recovery.incident import (
    OCCURRENCE_SCHEMA_VERSION,
    OccurrenceRecord,
    append_occurrence,
    occurrences_path,
    read_occurrences,
)
from kaji_harness.recovery.signature import IncidentSignature

pytestmark = pytest.mark.medium


def _record(run_id: str, *, hash_: str = "a" * 64) -> OccurrenceRecord:
    sig = IncidentSignature(
        schema_version=1,
        cause="verdict_resolution_failure",
        exception_type="VerdictNotFound",
        fingerprint="fp",
        fingerprint_hash=hash_,
    )
    return OccurrenceRecord(
        schema_version=OCCURRENCE_SCHEMA_VERSION,
        signature=sig,
        run_id=run_id,
        source_issue="304",
        failed_step="implement",
        workflow_path="dev.yaml",
        recorded_at="2026-07-12T01:00:00+00:00",
    )


def test_append_creates_directory_and_round_trips(tmp_path: Path) -> None:
    assert not occurrences_path(tmp_path).exists()
    append_occurrence(tmp_path, _record("r1"))
    append_occurrence(tmp_path, _record("r2", hash_="b" * 64))
    records = read_occurrences(tmp_path)
    assert [r.run_id for r in records] == ["r1", "r2"]
    assert records[0].signature.fingerprint_hash == "a" * 64
    assert records[1].signature.fingerprint_hash == "b" * 64
    assert records[0].source_issue == "304"


def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    append_occurrence(tmp_path, _record("r1"))
    path = occurrences_path(tmp_path)
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ not json\n")
        f.write('{"schema_version": 1}\n')  # signature 欠落 → skip
        f.write("\n")  # 空行 → skip
    append_occurrence(tmp_path, _record("r2"))
    records = read_occurrences(tmp_path)
    assert [r.run_id for r in records] == ["r1", "r2"]


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_occurrences(tmp_path) == []
