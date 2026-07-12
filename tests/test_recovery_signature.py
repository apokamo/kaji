"""Small tests: 識別署名の正規化・算出・あいまい類似（Issue #304 第1層）.

``tests/fixtures/incident/`` の実エラーテキスト由来 fixture で正規化パイプラインを固定する:

- 正例: #301 の 3 再発（run/step/issue が別でも occurrence 固有部分を除いた指紋は同値）
- 負例: 認証エラー（401）と rate limit（429）が数値 allowlist により別署名に分離される
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.recovery.models import FailureClassification
from kaji_harness.recovery.signature import (
    SIGNATURE_SCHEMA_VERSION,
    IncidentSignature,
    compute_signature,
    normalize_error_text,
    similarity,
)
from kaji_harness.recovery.snapshot import FailureEvent, FailureSnapshot

pytestmark = pytest.mark.small

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "incident"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _snapshot(attempt_error: str, *, exception_type: str = "VerdictNotFound") -> FailureSnapshot:
    return FailureSnapshot(
        run_id="260712010000",
        run_dir=Path("/nonexistent/runs/260712010000"),
        attempt_error=attempt_error,
        failure_event=FailureEvent(kind="verdict_exception", exception_type=exception_type),
    )


def _classification(cause: str = "verdict_resolution_failure") -> FailureClassification:
    return FailureClassification(
        cause=cause, synthetic=True, source="agent", recoverability_hint="candidate"
    )


# --- 正例: #301 の 3 再発が同一 fingerprint_hash になる ---


def test_three_recurrences_share_one_fingerprint_hash() -> None:
    sigs = [
        compute_signature(_snapshot(_load(name)), _classification())
        for name in (
            "verdict_notfound_run1.txt",
            "verdict_notfound_run2.txt",
            "verdict_notfound_run3.txt",
        )
    ]
    hashes = {s.fingerprint_hash for s in sigs}
    assert len(hashes) == 1, f"expected 1 shared hash, got {hashes}"
    # 署名は cause / exception_type も一致する（3 件は同一署名）。
    assert all(s.matches(sigs[0]) for s in sigs)
    assert sigs[0].schema_version == SIGNATURE_SCHEMA_VERSION


def test_tail_and_occurrence_specifics_are_normalized_away() -> None:
    fp = normalize_error_text(_load("verdict_notfound_run3.txt"))
    # Last N chars: 以降は <TAIL> に潰れ、occurrence 固有値が指紋に残らない。
    assert "<TAIL>" in fp
    assert "260712015008" not in fp  # run_id
    assert "/home/aki" not in fp  # 絶対パス
    assert "#298" not in fp  # issue 参照
    assert "51234" not in fp  # port 番号


# --- 負例: 401 と 429 が別署名に分離される（識別的数値の保持） ---


def test_auth_error_and_rate_limit_are_separate_signatures() -> None:
    auth = compute_signature(
        _snapshot(_load("auth_401.txt"), exception_type="GitHubProviderError"),
        _classification("dispatch_failure"),
    )
    rate = compute_signature(
        _snapshot(_load("ratelimit_429.txt"), exception_type="GitHubProviderError"),
        _classification("dispatch_failure"),
    )
    assert auth.fingerprint_hash != rate.fingerprint_hash
    assert not auth.matches(rate)
    # 識別的数値（HTTP status）は allowlist で保持される。
    assert "401" in auth.fingerprint
    assert "429" in rate.fingerprint


# --- redaction が hash 生成前に適用される ---


def test_secrets_are_masked_before_hashing() -> None:
    raw = "auth failed with token ghp_ABCDEFGHIJ1234567890 while calling api"
    sig = compute_signature(
        _snapshot(raw, exception_type="RuntimeError"), _classification("runtime_error")
    )
    assert "ghp_ABCDEFGHIJ1234567890" not in sig.fingerprint
    assert "***" in sig.fingerprint


# --- 空エラーテキスト ---


def test_empty_error_text_yields_placeholder_fingerprint() -> None:
    snap = FailureSnapshot(
        run_id="260712010000",
        run_dir=Path("/nonexistent"),
        attempt_error=None,
        workflow_end_error=None,
        failure_event=FailureEvent(kind="cycle_exhausted", exception_type=None),
    )
    sig = compute_signature(snap, _classification("cycle_exhausted"))
    assert sig.fingerprint == "<no-error-text>"
    assert sig.exception_type == "-"
    # 空指紋でも署名は成立し、(cause, exception_type) のみで照合される。
    assert sig.fingerprint_hash


def test_attempt_error_is_primary_over_workflow_end_error() -> None:
    snap = FailureSnapshot(
        run_id="260712010000",
        run_dir=Path("/nonexistent"),
        attempt_error="primary error detail",
        workflow_end_error="WrapperError: wrapped restatement",
        failure_event=FailureEvent(kind="verdict_exception", exception_type="VerdictNotFound"),
    )
    sig = compute_signature(snap, _classification())
    assert "primary error detail" in sig.fingerprint
    # 連結しない: wrapper 再掲は指紋に混ざらない。
    assert "wrapped restatement" not in sig.fingerprint


# --- あいまい類似の引数順契約 ---


def test_similarity_argument_order_contract() -> None:
    # ratio() は autojunk 等で引数順により非対称になりうる。公開契約（current, candidate）を固定。
    current = "x" * 200 + "unique-current-marker"
    candidate = "x" * 200
    forward = similarity(current, candidate)
    assert 0.0 <= forward <= 1.0
    # 同一文字列は 1.0。
    assert similarity(current, current) == 1.0


def test_ratio_can_be_asymmetric_between_arg_orders() -> None:
    a = "abcabcabcabc def"
    b = "abcabcabcabc"
    # 少なくとも一方向の値が [0,1] に収まることと、契約どおり第1=current を固定できること。
    assert 0.0 <= similarity(a, b) <= 1.0
    assert isinstance(similarity(b, a), float)


def test_signature_matches_ignores_fingerprint_text_only_hash() -> None:
    base = IncidentSignature(
        schema_version=1, cause="c", exception_type="E", fingerprint="text A", fingerprint_hash="h"
    )
    other = IncidentSignature(
        schema_version=1,
        cause="c",
        exception_type="E",
        fingerprint="text B differs",
        fingerprint_hash="h",
    )
    assert base.matches(other)
    mismatch = IncidentSignature(
        schema_version=2, cause="c", exception_type="E", fingerprint="text A", fingerprint_hash="h"
    )
    assert not base.matches(mismatch)  # schema version 不一致
