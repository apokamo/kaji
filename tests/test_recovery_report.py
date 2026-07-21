"""Small tests: triage コメント / stderr サマリ生成の純関数 (Issue #288).

テンプレート充足、auto-close hazard pattern 不含、credential masking、
500 文字引用上限、``Comment.ref`` 空文字 → ``n/a`` fallback を検証する。
"""

from __future__ import annotations

import re

import pytest

from kaji_harness.recovery.models import FailureClassification, RecoveryDecision
from kaji_harness.recovery.report import (
    _CAUSE_DESCRIPTIONS,
    EVIDENCE_LIMIT,
    mask_secrets,
    render_stderr_summary,
    render_triage_comment,
    sanitize_evidence,
)

pytestmark = pytest.mark.small

# docs/dev/shared_skill_rules.md § auto close keyword 回避規約 と同じ検出式。
_AUTO_CLOSE_HAZARD = re.compile(
    r"\b(Clos(e[sd]?|ing)|Fix(e[sd]|ing)?|Resolv(e[sd]?|ing)|Implement(s|ing|ed)?)\s+#\d",
    re.IGNORECASE,
)


def _decision(**overrides: object) -> RecoveryDecision:
    base: dict[str, object] = {
        "run_id": "260710120000",
        "recoverable": True,
        "decision": "resume",
        "classification": FailureClassification(
            cause="verdict_resolution_failure",
            synthetic=True,
            source="agent",
            recoverability_hint="candidate",
        ),
        "failed_step": "review-code",
        "resume_from": "review-code",
        "resume_mode": "from",
        "resume_command": "kaji run .kaji/wf/official/dev.yaml 288 --from review-code",
        "reason": "VerdictNotFound after successful dispatch",
        "evidence": ["run.log: workflow_end status=ERROR"],
        "recovery_root_run_id": "260710120000",
        "resume_scheduled_at": "2026-07-10T12:10:00+00:00",
        "workflow_path": ".kaji/wf/official/dev.yaml",
    }
    base.update(overrides)
    return RecoveryDecision(**base)  # type: ignore[arg-type]


# --- masking / truncation ---


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("token=ghp_abcdefghijklmnop1234", "ghp_abcdefghijklmnop1234"),
        ("github_pat_11ABCDEFG0abcdefghij", "github_pat_11ABCDEFG0abcdefghij"),
        ("Authorization: Bearer sk-ant-secret-value", "sk-ant-secret-value"),
        ("api_key: super-secret-value", "super-secret-value"),
    ],
)
def test_mask_secrets_removes_credentials(raw: str, leaked: str) -> None:
    masked = mask_secrets(raw)
    assert leaked not in masked
    assert "***" in masked


def test_mask_secrets_keeps_ordinary_text() -> None:
    assert mask_secrets("VerdictNotFound: no verdict block") == "VerdictNotFound: no verdict block"


def test_sanitize_evidence_truncates_to_limit() -> None:
    out = sanitize_evidence("x" * (EVIDENCE_LIMIT + 200))
    assert len(out) <= EVIDENCE_LIMIT + len("…")
    assert out.endswith("…")


def test_sanitize_evidence_masks_before_truncation() -> None:
    assert "ghp_" not in sanitize_evidence("ghp_" + "a" * 40 + "b" * 600)


def test_evidence_limit_is_500() -> None:
    assert EVIDENCE_LIMIT == 500


# --- triage コメント ---


def test_triage_comment_contains_required_rows() -> None:
    body = render_triage_comment(decision=_decision(), issue_ref="#288")
    assert body.startswith("## Workflow failure triage")
    for key in (
        "workflow",
        "issue",
        "run_id",
        "recovery_root_run_id",
        "recovery_parent_run_id",
        "failed_step",
        "classification",
        "synthetic",
        "decision",
        "auto_recovery",
        "resume_command",
        "resume_scheduled_at",
        "discarded_resume_session",
        "child_run_status",
    ):
        assert f"| {key} |" in body, f"missing row: {key}"
    assert "### 原因（機械判定）" in body
    assert "### 根拠" in body
    assert "### 次アクション" in body


def test_triage_comment_has_no_auto_close_hazard_pattern() -> None:
    body = render_triage_comment(decision=_decision(), issue_ref="#288")
    assert _AUTO_CLOSE_HAZARD.search(body) is None


def test_triage_comment_has_no_verdict_marker() -> None:
    body = render_triage_comment(decision=_decision(), issue_ref="#288")
    assert "kaji-verdict" not in body


def test_triage_comment_resume_mentions_scheduled_time() -> None:
    body = render_triage_comment(decision=_decision(), issue_ref="#288")
    assert "2026-07-10T12:10:00+00:00" in body
    assert "10 分" in body


def test_triage_comment_cycle_exhausted_offers_reset_cycle() -> None:
    decision = _decision(
        decision="not_resumable",
        recoverable=False,
        resume_command=None,
        resume_scheduled_at=None,
        classification=FailureClassification(
            cause="cycle_exhausted", synthetic=True, source="runner", recoverability_hint="no"
        ),
    )
    body = render_triage_comment(decision=decision, issue_ref="#288")
    assert "--reset-cycle" in body
    assert "| resume_command | `n/a` |" in body


def test_triage_comment_renders_user_precondition_error_cause() -> None:
    # Issue #322: 新 cause の説明文が欠けると render_triage_comment が KeyError で落ちる。
    decision = _decision(
        decision="not_resumable",
        recoverable=False,
        resume_command=None,
        resume_scheduled_at=None,
        classification=FailureClassification(
            cause="user_precondition_error",
            synthetic=True,
            source="config",
            recoverability_hint="no",
        ),
    )
    body = render_triage_comment(decision=decision, issue_ref="#288")
    assert "| classification | `user_precondition_error` |" in body
    # 固定説明文（incident 起票の対象外である旨）がそのまま載る。
    assert _CAUSE_DESCRIPTIONS["user_precondition_error"] in body
    assert "incident 起票の対象外" in _CAUSE_DESCRIPTIONS["user_precondition_error"]


def test_triage_comment_masks_credentials_in_evidence() -> None:
    decision = _decision(evidence=["result.json error=Bearer sk-secret-token-value"])
    body = render_triage_comment(decision=decision, issue_ref="#288")
    assert "sk-secret-token-value" not in body


def test_triage_comment_includes_bug_issue_when_created() -> None:
    decision = _decision(
        decision="bug_issue_created",
        recoverable=False,
        resume_command=None,
        resume_scheduled_at=None,
        bug_issue={"id": "301", "url": "https://example.invalid/issues/301"},
        classification=FailureClassification(
            cause="kaji_bug_suspected", synthetic=True, source="runner", recoverability_hint="no"
        ),
    )
    body = render_triage_comment(decision=decision, issue_ref="#288")
    assert "301" in body
    assert "https://example.invalid/issues/301" in body


def test_triage_comment_child_run_status_pending_when_unknown() -> None:
    body = render_triage_comment(decision=_decision(), issue_ref="#288")
    assert "| child_run_status | `pending` |" in body


# --- stderr サマリ ---


def test_stderr_summary_shape() -> None:
    summary = render_stderr_summary(_decision(triage_comment_ref="https://x.invalid/c/1"))
    assert summary.splitlines()[0] == "--- failure triage ---"
    assert "failed_step:    review-code" in summary
    assert "classification: verdict_resolution_failure (synthetic=true)" in summary
    assert "decision:       resume" in summary
    assert "comment:        https://x.invalid/c/1" in summary
    assert "resume_scheduled_at: 2026-07-10T12:10:00+00:00" in summary
    assert "next action:" in summary


def test_stderr_summary_comment_ref_falls_back_to_na() -> None:
    assert "comment:        n/a" in render_stderr_summary(_decision(triage_comment_ref=None))
    assert "comment:        n/a" in render_stderr_summary(_decision(triage_comment_ref=""))


def test_stderr_summary_omits_scheduled_at_when_not_resuming() -> None:
    summary = render_stderr_summary(
        _decision(decision="comment_only", recoverable=True, resume_scheduled_at=None)
    )
    assert "resume_scheduled_at:" not in summary
    assert "decision:       comment_only" in summary
