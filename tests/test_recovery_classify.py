"""Small tests: failure classification と transient helper の単一情報源化 (Issue #288).

``classify_failure()`` の分類表（cause × synthetic × source × recoverability_hint）を
行ごとに検証し、予約値 ``external_upstream_anomaly`` を初期 classifier が emit しない
ことを固定する。あわせて ``cli.py`` から抽出した ``is_transient_error_text()`` が
attempt retry の既存 pattern 判定を変えないことを回帰として押さえる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.cli import (
    _TRANSIENT_PATTERNS,
    _is_transient,
    find_transient_pattern,
    is_transient_error_text,
)
from kaji_harness.errors import CLIExecutionError
from kaji_harness.interactive_terminal import _terminal_exit_detail
from kaji_harness.recovery.classify import classify_failure
from kaji_harness.recovery.snapshot import FailureEvent, FailureSnapshot
from tests.conftest import capacity_terminal_log_text

pytestmark = pytest.mark.small


def _snapshot(**overrides: object) -> FailureSnapshot:
    base: dict[str, object] = {
        "run_id": "260710120000",
        "run_dir": Path("/tmp/runs/260710120000"),
        # 既定は failure_event 契約下の run（本機能導入以降に生成された run.log）。
        "run_log_schema_version": 1,
        "workflow_end_status": "ERROR",
        "state_loaded": True,
        "attempt_result_present": True,
        "evidence": ("run.log: workflow_end status=ERROR",),
    }
    base.update(overrides)
    return FailureSnapshot(**base)  # type: ignore[arg-type]


# --- transient helper（単一情報源化の回帰） ---


def test_is_transient_error_text_matches_all_known_patterns() -> None:
    for pattern in _TRANSIENT_PATTERNS:
        assert is_transient_error_text(f"prefix {pattern.upper()} suffix") is True


def test_is_transient_error_text_rejects_unrelated_and_empty() -> None:
    assert is_transient_error_text("") is False
    assert is_transient_error_text(None) is False
    assert is_transient_error_text("syntax error near token") is False


def test_private_is_transient_delegates_to_public_helper() -> None:
    # attempt retry と recovery classifier が同一 pattern list を参照する保証。
    assert _is_transient(CLIExecutionError("s", 1, "Overloaded, try later")) is True
    assert _is_transient(CLIExecutionError("s", 1, "fatal: not a git repository")) is False


# --- find_transient_pattern（Issue #296: matched_pattern 取得の正本 IF） ---


def test_find_transient_pattern_returns_matched_literal() -> None:
    assert find_transient_pattern("Selected model is at capacity.") == "at capacity"
    assert find_transient_pattern("HTTP 429: rate limit exceeded") == "rate limit"
    assert find_transient_pattern("provider overloaded, please retry") == "overloaded"


def test_find_transient_pattern_returns_none_for_unrelated_or_empty() -> None:
    assert find_transient_pattern("syntax error near token") is None
    assert find_transient_pattern("") is None
    assert find_transient_pattern(None) is None


def test_is_transient_error_text_delegates_to_find_transient_pattern() -> None:
    # 二重実装ではなく、find_transient_pattern の有無へ委譲していることの回帰。
    for text in ["at capacity", "unrelated text", "", None]:
        assert is_transient_error_text(text) == (find_transient_pattern(text) is not None)


def test_classify_dispatch_candidate_with_canonical_only_capacity_message(
    tmp_path: Path,
) -> None:
    # Issue #296: attempt_error はハンドライトの文字列ではなく、実 ANSI/TUI
    # terminal.log fixture に対して実装関数 _terminal_exit_detail を呼んで生成した
    # ものを使う。焦点化契約（transcript 部分文字列を含まず pattern literal の
    # みを載せる）と既存 classify ロジックの接続を、生成元から一続きで固定する。
    terminal_log = tmp_path / "terminal.log"
    terminal_log.write_text(capacity_terminal_log_text(), encoding="utf-8")
    message = _terminal_exit_detail(terminal_log)
    assert "at capacity" in message
    assert "Token usage" not in message

    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="start", exception_type="CLIExecutionError"
            ),
            failed_step="start",
            attempt_error=message,
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.recoverability_hint == "candidate"


# --- 分類表の行ごとの検証 ---


def test_dispatch_timeout_is_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="StepTimeoutError"
            ),
            failed_step="implement",
            attempt_error="StepTimeoutError: Step 'implement' timed out after 600s",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.synthetic is True
    assert c.source == "external"
    assert c.recoverability_hint == "candidate"


def test_dispatch_cli_error_transient_is_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="CLIExecutionError"
            ),
            failed_step="implement",
            attempt_error="CLIExecutionError: Step 'implement' CLI exited with code 1: overloaded",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.recoverability_hint == "candidate"


def test_dispatch_cli_error_non_transient_is_not_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="CLIExecutionError"
            ),
            failed_step="implement",
            attempt_error="CLIExecutionError: Step 'implement' CLI exited with code 2: bad flag",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.recoverability_hint == "no"


def test_dispatch_cli_not_found_is_not_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="CLINotFoundError"
            ),
            failed_step="implement",
            attempt_error="CLINotFoundError: CLI 'claude' not found. Is it installed?",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.recoverability_hint == "no"


def test_dispatch_script_error_is_not_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="poll", exception_type="ScriptExecutionError"
            ),
            failed_step="poll",
            attempt_error="ScriptExecutionError: exited with code 1",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.recoverability_hint == "no"


def test_dispatch_exception_with_unknown_type_is_opaque_external() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="WeirdVendorError"
            ),
            failed_step="implement",
            attempt_error="WeirdVendorError: something opaque",
        )
    )
    assert c.cause == "unknown_external_error"
    assert c.source == "external"
    assert c.recoverability_hint == "no"


@pytest.mark.parametrize("exc", ["VerdictNotFound", "VerdictParseError"])
def test_verdict_resolution_failure_is_candidate(exc: str) -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="verdict_exception", step_id="review-code", exception_type=exc
            ),
            failed_step="review-code",
            attempt_error=f"{exc}: no verdict block",
        )
    )
    assert c.cause == "verdict_resolution_failure"
    assert c.synthetic is True
    assert c.source == "agent"
    assert c.recoverability_hint == "candidate"


def test_invalid_verdict_value_is_not_candidate() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="verdict_exception",
                step_id="review-code",
                exception_type="InvalidVerdictValue",
            ),
            failed_step="review-code",
            attempt_error="InvalidVerdictValue: status 'MAYBE' not in on:",
        )
    )
    assert c.cause == "verdict_resolution_failure"
    assert c.recoverability_hint == "no"


def test_cycle_exhausted() -> None:
    c = classify_failure(
        _snapshot(
            workflow_end_status="ABORT",
            failure_event=FailureEvent(
                kind="cycle_exhausted", step_id="review-code", cycle_name="code-review"
            ),
            failed_step="review-code",
            attempt_result_present=False,
        )
    )
    assert c.cause == "cycle_exhausted"
    assert c.synthetic is True
    assert c.source == "runner"
    assert c.recoverability_hint == "no"


def test_agent_declared_abort_is_not_synthetic() -> None:
    c = classify_failure(
        _snapshot(
            workflow_end_status="ABORT",
            failure_event=FailureEvent(
                kind="agent_abort", step_id="review-design", synthetic=False
            ),
            failed_step="review-design",
        )
    )
    assert c.cause == "agent_declared_abort"
    assert c.synthetic is False
    assert c.source == "agent"
    assert c.recoverability_hint == "no"


def test_ambiguous_worktree_abort() -> None:
    c = classify_failure(
        _snapshot(
            workflow_end_status="ABORT",
            failure_event=FailureEvent(kind="ambiguous_worktree"),
            attempt_result_present=False,
        )
    )
    assert c.cause == "ambiguous_worktree_abort"
    assert c.source == "runner"
    assert c.recoverability_hint == "no"


@pytest.mark.parametrize(
    "exc",
    [
        "WorkflowValidationError",
        "MissingResumeSessionError",
        "InvalidTransition",
        "WorkdirNotFoundError",
    ],
)
def test_config_or_definition_error(exc: str) -> None:
    c = classify_failure(
        _snapshot(
            workflow_end_status="ERROR",
            workflow_end_error=f"{exc}: boom",
            attempt_result_present=False,
        )
    )
    assert c.cause == "config_or_definition_error"
    assert c.source == "config"
    assert c.recoverability_hint == "no"


def test_definition_error_wins_over_stale_agent_abort_event() -> None:
    # ABORT verdict に遷移先が無く InvalidTransition で ERROR 終端した場合、
    # run を終わらせたのは定義エラーであって agent の ABORT ではない。
    c = classify_failure(
        _snapshot(
            workflow_end_status="ERROR",
            workflow_end_error="InvalidTransition: Step 'x' has no transition for verdict 'ABORT'",
            failure_event=FailureEvent(kind="agent_abort", step_id="x", synthetic=False),
            failed_step="x",
        )
    )
    assert c.cause == "config_or_definition_error"


def test_runtime_error_fallback() -> None:
    c = classify_failure(
        _snapshot(workflow_end_status="ERROR", workflow_end_error="RuntimeError: unreachable")
    )
    assert c.cause == "runtime_error"
    assert c.source == "runner"
    assert c.recoverability_hint == "no"


def test_missing_attempt_result_is_kaji_bug_suspected() -> None:
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="verdict_exception", step_id="review-code", exception_type="VerdictNotFound"
            ),
            failed_step="review-code",
            attempt_result_present=False,
        )
    )
    assert c.cause == "kaji_bug_suspected"
    assert c.source == "runner"
    assert c.recoverability_hint == "no"


def test_unreadable_state_is_kaji_bug_suspected() -> None:
    c = classify_failure(_snapshot(state_loaded=False))
    assert c.cause == "kaji_bug_suspected"


def test_unreadable_artifact_is_kaji_bug_suspected() -> None:
    c = classify_failure(_snapshot(artifact_read_errors=("run.log: unreadable",)))
    assert c.cause == "kaji_bug_suspected"


def test_abort_without_failure_event_is_kaji_bug_suspected() -> None:
    c = classify_failure(_snapshot(workflow_end_status="ABORT", failure_event=None))
    assert c.cause == "kaji_bug_suspected"


def test_legacy_abort_without_schema_version_is_not_kaji_bug_suspected() -> None:
    # 本機能導入前の run.log は failure_event を持たないのが正常。`kaji recover` を
    # 向けただけで harness のバグと断定し bug issue を起票してはならない。
    c = classify_failure(
        _snapshot(
            run_log_schema_version=None,
            workflow_end_status="ABORT",
            failure_event=None,
        )
    )
    assert c.cause == "unknown_external_error"
    assert c.recoverability_hint == "no"


def test_legacy_run_still_reports_genuine_artifact_contradiction() -> None:
    # schema version 不明でも、読めない artifact / state 欠損は決定論的矛盾のまま。
    c = classify_failure(
        _snapshot(
            run_log_schema_version=None,
            workflow_end_status="ABORT",
            failure_event=None,
            state_loaded=False,
        )
    )
    assert c.cause == "kaji_bug_suspected"


def test_dispatch_cli_not_found_is_non_candidate_dispatch_failure() -> None:
    # runner の except 節が捕捉する型。`unknown_external_error` に落ちてはならない。
    c = classify_failure(
        _snapshot(
            failure_event=FailureEvent(
                kind="dispatch_exception", step_id="implement", exception_type="CLINotFoundError"
            ),
            failed_step="implement",
            attempt_error="CLI 'claude' not found. Is it installed?",
        )
    )
    assert c.cause == "dispatch_failure"
    assert c.source == "external"
    assert c.recoverability_hint == "no"


def test_classifier_never_emits_reserved_external_upstream_anomaly() -> None:
    kinds = [
        FailureEvent(kind="dispatch_exception", exception_type="StepTimeoutError", step_id="s"),
        FailureEvent(kind="verdict_exception", exception_type="VerdictNotFound", step_id="s"),
        FailureEvent(kind="cycle_exhausted", cycle_name="c", step_id="s"),
        FailureEvent(kind="ambiguous_worktree"),
        FailureEvent(kind="agent_abort", step_id="s", synthetic=False),
        None,
    ]
    for event in kinds:
        c = classify_failure(
            _snapshot(failure_event=event, failed_step="s", attempt_result_present=True)
        )
        assert c.cause != "external_upstream_anomaly"
