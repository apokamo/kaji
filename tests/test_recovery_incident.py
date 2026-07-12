"""Small tests: incident marker / 照合 / count 導出 / あいまい / テンプレート（Issue #304）.

すべて純関数・fs / provider 非依存。設計書 § テスト戦略 § Small を固定する。
"""

from __future__ import annotations

import re

import pytest

from kaji_harness.providers.models import Comment, Issue, Label
from kaji_harness.recovery.incident import (
    IncidentCandidate,
    IncidentContext,
    backfill_run_ids,
    compute_fuzzy_candidates,
    parse_candidates,
    parse_identity_marker,
    parse_occurrence_markers,
    plan_incident_action,
    posted_run_ids,
    render_identity_marker,
    render_incident_issue,
    render_occurrence_comment,
    render_occurrence_marker,
)
from kaji_harness.recovery.models import (
    FailureClassification,
    RecoveryDecision,
)
from kaji_harness.recovery.signature import IncidentSignature

pytestmark = pytest.mark.small

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_AUTO_CLOSE_RE = re.compile(
    r"(?i)\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s+#\d"
)


def _sig(
    hash_: str = _HASH_A,
    *,
    cause: str = "verdict_resolution_failure",
    exc: str = "VerdictNotFound",
    fp: str = "normalized fingerprint text",
) -> IncidentSignature:
    return IncidentSignature(
        schema_version=1, cause=cause, exception_type=exc, fingerprint=fp, fingerprint_hash=hash_
    )


def _candidate(
    issue_id: str, state: str, *, sig: IncidentSignature | None, labels: tuple[str, ...] = ()
) -> IncidentCandidate:
    return IncidentCandidate(issue_id=issue_id, state=state, labels=labels, signature=sig)


def _ctx(sig: IncidentSignature, *, run_id: str = "260712010000") -> IncidentContext:
    return IncidentContext(
        signature=sig,
        run_id=run_id,
        source_issue="304",
        source_issue_ref="#304",
        failed_step="implement",
        workflow_path="dev.yaml",
        evidence=("run.log: workflow_end status=ERROR",),
        error_excerpt="VerdictNotFound: missing block",
    )


# --- marker round-trip / 厳格 parse ---


def test_identity_marker_round_trip() -> None:
    sig = _sig()
    body = render_identity_marker(sig) + "\n\n```kaji-fingerprint\n" + sig.fingerprint + "\n```\n"
    parsed = parse_identity_marker(body)
    assert parsed is not None
    assert parsed.matches(sig)
    assert parsed.fingerprint == sig.fingerprint  # block から補完


@pytest.mark.parametrize(
    "body",
    [
        "not a marker at all",
        "<!-- kaji-incident: schema=1 cause=c exception=E hash=tooshort -->",  # 不正 hash 長
        "<!-- kaji-incident: schema=1 cause=c hash=" + _HASH_A + " -->",  # exception 欠損
        "prefix text\n<!-- kaji-incident: schema=1 cause=c exception=E hash="
        + _HASH_A
        + " -->",  # 1 行目でない
    ],
)
def test_broken_identity_marker_falls_to_none(body: str) -> None:
    assert parse_identity_marker(body) is None


def test_occurrence_marker_round_trip_and_broken() -> None:
    marker = render_occurrence_marker(_sig(), run_id="260712010000", source_issue="304")
    got = parse_occurrence_markers(marker + "\nsome body text")
    assert len(got) == 1
    assert got[0].run_id == "260712010000"
    assert got[0].hash == _HASH_A
    # 引用（marker 前に非空白）は弾く。
    assert parse_occurrence_markers("> " + marker) == []
    # 不正 hash 長は弾く。
    assert (
        parse_occurrence_markers(
            "<!-- kaji-incident-occurrence: schema=1 hash=zzz run_id=x source_issue=1 -->"
        )
        == []
    )


# --- plan_incident_action の分岐 ---


def test_plan_open_match_recurs_to_min_issue() -> None:
    sig = _sig()
    cands = [
        _candidate("310", "open", sig=_sig()),
        _candidate("305", "open", sig=_sig()),
    ]
    action = plan_incident_action(sig, cands)
    assert action.kind == "recur"
    assert action.target_id == "305"  # issue 番号最小（最古）
    assert action.also_matched == ("310",)


def test_plan_closed_transient_recurs_without_reopen() -> None:
    sig = _sig()
    cands = [_candidate("305", "closed", sig=_sig(), labels=("incident:cause:transient",))]
    action = plan_incident_action(sig, cands)
    assert action.kind == "recur"
    assert action.target_id == "305"


def test_plan_closed_resolved_creates_regression() -> None:
    sig = _sig()
    cands = [_candidate("305", "closed", sig=_sig(), labels=("incident:resolved",))]
    action = plan_incident_action(sig, cands)
    assert action.kind == "create_regression"
    assert action.regression_of == "305"


def test_plan_open_takes_priority_over_closed() -> None:
    sig = _sig()
    cands = [
        _candidate("305", "closed", sig=_sig(), labels=("incident:resolved",)),
        _candidate("320", "open", sig=_sig()),
    ]
    action = plan_incident_action(sig, cands)
    assert action.kind == "recur"
    assert action.target_id == "320"


def test_plan_no_match_creates() -> None:
    assert (
        plan_incident_action(_sig(_HASH_A), [_candidate("1", "open", sig=_sig(_HASH_B))]).kind
        == "create"
    )


def test_plan_schema_version_mismatch_creates() -> None:
    sig = _sig()
    other = IncidentSignature(
        schema_version=2,
        cause=sig.cause,
        exception_type=sig.exception_type,
        fingerprint="x",
        fingerprint_hash=_HASH_A,
    )
    assert plan_incident_action(sig, [_candidate("1", "open", sig=other)]).kind == "create"


def test_plan_unreadable_identity_marker_candidate_skipped() -> None:
    assert plan_incident_action(_sig(), [_candidate("1", "open", sig=None)]).kind == "create"


def test_parse_candidates_from_issues() -> None:
    sig = _sig()
    body = render_identity_marker(sig) + "\n\n```kaji-fingerprint\n" + sig.fingerprint + "\n```\n"
    issue = Issue(id="305", title="t", body=body, state="open", labels=[Label(name="incident")])
    cands = parse_candidates([issue])
    assert cands[0].issue_id == "305"
    assert cands[0].signature is not None and cands[0].signature.matches(sig)


# --- 再発回数導出 / backfill ---


def test_count_derivation_and_crash_window_dedup() -> None:
    sig = _sig()
    m1 = render_occurrence_marker(sig, run_id="r1", source_issue="304")
    comments = [Comment(author="", body=m1, created_at="")]
    posted = posted_run_ids(comments, sig.fingerprint_hash)
    assert posted == {"r1"}
    # crash window: 同一 run_id の重複 marker で N 不変。
    dup = [Comment(author="", body=m1, created_at=""), Comment(author="", body=m1, created_at="")]
    assert posted_run_ids(dup, sig.fingerprint_hash) == {"r1"}
    # 本文の marker 類似文字列（hash 不一致）はカウントに含めない。
    other = render_occurrence_marker(_sig(_HASH_B), run_id="r9", source_issue="304")
    assert (
        posted_run_ids([Comment(author="", body=other, created_at="")], sig.fingerprint_hash)
        == set()
    )


def test_backfill_excludes_posted_run_ids() -> None:
    from kaji_harness.recovery.incident import OccurrenceRecord

    sig = _sig()
    records = [
        OccurrenceRecord(1, sig, "r_old", "304", "implement", "dev.yaml", "t"),
        OccurrenceRecord(1, _sig(_HASH_B), "r_other", "304", "implement", "dev.yaml", "t"),
    ]
    ids = backfill_run_ids(
        current_run_id="r_new",
        local_records=records,
        fingerprint_hash=sig.fingerprint_hash,
        posted={"r_posted"},
    )
    # 今回 + 同一 hash のローカル記録、posted 除外、hash 不一致除外。
    assert ids == ["r_new", "r_old"]


# --- あいまい照合（助言専用） ---


def test_fuzzy_threshold_and_filter() -> None:
    current = _sig(_HASH_A, fp="alpha beta gamma delta epsilon zeta eta theta")
    # 類似（同一 exception_type、fingerprint 近い）→ 閾値超え。
    near = _sig(_HASH_B, fp="alpha beta gamma delta epsilon zeta eta xxxxx")
    # 別 exception かつ別 cause → filter で除外。
    unrelated = IncidentSignature(
        1, "other_cause", "OtherExc", "alpha beta gamma delta epsilon zeta eta theta", "c" * 64
    )
    cands = [
        _candidate("400", "open", sig=near),
        _candidate("401", "open", sig=unrelated),
    ]
    fuzzy = compute_fuzzy_candidates(current, cands)
    ids = [f.issue_id for f in fuzzy]
    assert "400" in ids
    assert "401" not in ids
    assert all(f.score >= 0.8 for f in fuzzy)


def test_fuzzy_excludes_exact_match_and_empty_fingerprint() -> None:
    current = _sig(_HASH_A, fp="some fingerprint")
    exact = _sig(_HASH_A, fp="some fingerprint")  # 完全一致 → あいまい対象外
    empty = _sig(_HASH_B, fp="")  # fingerprint 空 → 除外
    fuzzy = compute_fuzzy_candidates(
        current, [_candidate("1", "open", sig=exact), _candidate("2", "open", sig=empty)]
    )
    assert fuzzy == []


# --- テンプレート描画 ---


def test_incident_issue_body_markers_and_no_auto_close() -> None:
    sig = _sig()
    title, body = render_incident_issue(_ctx(sig))
    assert body.splitlines()[0] == render_identity_marker(sig)  # 1 行目 identity marker
    assert "```kaji-fingerprint" in body
    # 本文に occurrence marker は置かない。
    assert "kaji-incident-occurrence" not in body
    assert not _AUTO_CLOSE_RE.search(body), "auto-close hazard pattern must not appear"
    assert not _AUTO_CLOSE_RE.search(title)
    assert title.startswith("incident: verdict_resolution_failure / VerdictNotFound")


def test_incident_issue_regression_links_prior() -> None:
    _, body = render_incident_issue(_ctx(_sig()), regression_of="305")
    assert "#305" in body
    assert "リグレッション" in body
    assert not _AUTO_CLOSE_RE.search(body)


def test_occurrence_comment_markers_at_line_start() -> None:
    sig = _sig()
    body = render_occurrence_comment(_ctx(sig), marker_run_ids=["r_new", "r_old"], count=2)
    lines = body.splitlines()
    assert lines[0] == render_occurrence_marker(sig, run_id="r_new", source_issue="304")
    assert lines[1] == render_occurrence_marker(sig, run_id="r_old", source_issue="304")
    assert "`2`" in body  # N=2
    assert not _AUTO_CLOSE_RE.search(body)


# --- incident 状態の直列化 ---


def _decision(**over: object) -> RecoveryDecision:
    base = dict(
        run_id="260712010000",
        recoverable=False,
        decision="comment_only",
        classification=FailureClassification(
            cause="runtime_error", synthetic=True, source="runner", recoverability_hint="no"
        ),
        failed_step="implement",
    )
    base.update(over)
    return RecoveryDecision(**base)  # type: ignore[arg-type]


def test_incident_fields_round_trip() -> None:
    d = _decision(incident_ref="305", incident_action="created", incident_transient_closed=True)
    restored = RecoveryDecision.from_dict(d.to_dict())
    assert restored.incident_ref == "305"
    assert restored.incident_action == "created"
    assert restored.incident_transient_closed is True


def test_legacy_recovery_json_defaults_incident_fields() -> None:
    data = _decision().to_dict()
    del data["incident_ref"]
    del data["incident_action"]
    del data["incident_transient_closed"]
    restored = RecoveryDecision.from_dict(data)
    assert restored.incident_ref is None
    assert restored.incident_action is None
    assert restored.incident_transient_closed is False
