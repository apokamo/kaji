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


# --- 非 exempt cause の fingerprint_hash 不変性（Issue #405 EB-4） ---


def test_non_exempt_cause_fingerprint_hash_is_pinned() -> None:
    """``signature.py`` を無変更にする Issue #405 の制約を、golden hash 4 件で固定する。

    invariant guard であり回帰テストではない: #405 の実装前後どちらでも Green になる
    （修正対象は ``models.py`` / ``report.py`` の定数のみで ``signature.py`` は触らない）。
    意図は「将来 ``signature.py`` を触ったときに既存 incident イシューとの照合が静かに
    壊れることを検出する」こと。値は実運用データ（``occurrences.jsonl``）と既存 fixture
    から 2026-08-24 に main（``221997d``）で実測した。
    """
    dispatch_cls = FailureClassification(
        cause="dispatch_failure", synthetic=True, source="external", recoverability_hint="no"
    )

    def _dispatch_snapshot(attempt_error: str, exception_type: str) -> FailureSnapshot:
        return FailureSnapshot(
            run_id="260712010000",
            run_dir=Path("/nonexistent/runs/260712010000"),
            attempt_error=attempt_error,
            failure_event=FailureEvent(kind="dispatch_exception", exception_type=exception_type),
        )

    cases = [
        (
            _dispatch_snapshot(
                "StepTimeoutError: Step 'implement' timed out after 3600s", "StepTimeoutError"
            ),
            dispatch_cls,
            "5a0e69f403a1e37cb09cc23e5a40ee64a77501a9655ce37264d4d044dbf0a046",
        ),
        (
            _dispatch_snapshot(
                "StepTimeoutError: Step 'pr' timed out after 1800s", "StepTimeoutError"
            ),
            dispatch_cls,
            "6dd57a743123862400b6b3294be7648c11432f79b681d9e445a64bcab88ddaa3",
        ),
        (
            _dispatch_snapshot(
                "CLINotFoundError: interactive terminal runner requires tmux. "
                "Run `kaji run` inside tmux or use agent_runner='headless'.",
                "CLINotFoundError",
            ),
            dispatch_cls,
            "d7b6c1ecd57db0f730316cf705304375b143c8b6b79394e2e5f9b1aa781ef4cb",
        ),
    ]
    for snapshot, classification, expected_hash in cases:
        sig = compute_signature(snapshot, classification)
        assert sig.fingerprint_hash == expected_hash

    # 4-d: verdict_resolution_failure は既存 fixture を再利用し、3 件すべてが 1 値に固定
    # されることを検査する（既存 test_three_recurrences_share_one_fingerprint_hash は
    # 「3 件が同値」だけを検査し、値そのものは固定していない）。
    expected_verdict_hash = "35856983d74433e1b1db9a4089da1de1fbf3d1e736ab130150121d10b33d0fc4"
    for name in (
        "verdict_notfound_run1.txt",
        "verdict_notfound_run2.txt",
        "verdict_notfound_run3.txt",
    ):
        sig = compute_signature(_snapshot(_load(name)), _classification())
        assert sig.fingerprint_hash == expected_verdict_hash


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
