"""Failure classification (Issue #288).

``classify_failure()`` は ``FailureSnapshot`` を cause 軸の ``FailureClassification`` に
写す純関数。判定の一次入力は runner が emit した構造化 ``failure_event`` であり、
reason 文字列マッチには依存しない。

transient 判定は ``cli.py`` の attempt retry と単一情報源を共有する
（``is_transient_error_text``）。分類不能な外部エラーは解釈せず
``unknown_external_error`` として opaque に扱う（決定 7）。
"""

from __future__ import annotations

from ..cli import is_transient_error_text
from .models import FailureClassification
from .snapshot import FailureSnapshot

#: runner の dispatch/verdict except 節が捕捉する dispatch 系例外。
_DISPATCH_EXCEPTIONS = frozenset(
    {"StepTimeoutError", "CLIExecutionError", "ScriptExecutionError", "CLINotFoundError"}
)

#: 入力不備として決定論的に再発する例外（run を終わらせた型で判定する）。
_DEFINITION_EXCEPTIONS = frozenset(
    {
        "WorkflowValidationError",
        "MissingResumeSessionError",
        "InvalidTransition",
        "WorkdirNotFoundError",
    }
)

#: verdict 解決失敗のうち、新セッションでの再実行に意味がある例外。
_RECOVERABLE_VERDICT_EXCEPTIONS = frozenset({"VerdictNotFound", "VerdictParseError"})

#: ``failure_event.kind`` のうち、対応する attempt の ``result.json`` を伴うはずのもの。
_ATTEMPT_BACKED_KINDS = frozenset({"dispatch_exception", "verdict_exception", "agent_abort"})


def _bug_suspected() -> FailureClassification:
    return FailureClassification(
        cause="kaji_bug_suspected",
        synthetic=True,
        source="runner",
        recoverability_hint="no",
    )


def _detect_contradiction(snapshot: FailureSnapshot) -> bool:
    """artifact / state / runner event の決定論的矛盾を検出する。

    推測ではなく「必ず存在するはずのものが無い」ケースのみを対象にする
    （bug issue 起票の入力になるため、疑わしいだけでは true にしない）。
    """
    if snapshot.artifact_read_errors:
        return True
    if not snapshot.state_loaded:
        return True
    event = snapshot.failure_event
    if event is not None and event.step_id and event.kind in _ATTEMPT_BACKED_KINDS:
        if not snapshot.attempt_result_present:
            return True
    # ABORT 終端は必ず agent_abort / cycle_exhausted / ambiguous_worktree のいずれかを伴う。
    return snapshot.workflow_end_status == "ABORT" and event is None


def _classify_dispatch(
    snapshot: FailureSnapshot, exception_type: str | None
) -> FailureClassification:
    if exception_type not in _DISPATCH_EXCEPTIONS:
        # runner の except 節が捕捉しない型が記録された = 解釈不能な外部エラー。
        return FailureClassification(
            cause="unknown_external_error",
            synthetic=True,
            source="external",
            recoverability_hint="no",
        )
    candidate = exception_type == "StepTimeoutError" or (
        exception_type == "CLIExecutionError" and is_transient_error_text(snapshot.attempt_error)
    )
    return FailureClassification(
        cause="dispatch_failure",
        synthetic=True,
        source="external",
        recoverability_hint="candidate" if candidate else "no",
    )


def _classify_verdict(exception_type: str | None) -> FailureClassification:
    candidate = exception_type in _RECOVERABLE_VERDICT_EXCEPTIONS
    return FailureClassification(
        cause="verdict_resolution_failure",
        synthetic=True,
        source="agent",
        recoverability_hint="candidate" if candidate else "no",
    )


def classify_failure(snapshot: FailureSnapshot) -> FailureClassification:
    """``FailureSnapshot`` を cause 軸で分類する。

    判定順:

    1. artifact / state / event の決定論的矛盾 → ``kaji_bug_suspected``
    2. ``workflow_end.error`` の例外型が定義エラー → ``config_or_definition_error``
       （ABORT verdict に遷移先が無く ``InvalidTransition`` で ERROR 終端した場合など、
       run を終わらせたのは定義エラーであって直前の failure_event ではない）
    3. ``failure_event.kind`` による mapping
    4. ERROR 終端の fallback → ``runtime_error``
    5. それ以外 → ``unknown_external_error``（opaque）

    予約値 ``external_upstream_anomaly`` は emit しない。
    """
    if _detect_contradiction(snapshot):
        return _bug_suspected()

    if (
        snapshot.workflow_end_status == "ERROR"
        and snapshot.workflow_end_exception_type in _DEFINITION_EXCEPTIONS
    ):
        return FailureClassification(
            cause="config_or_definition_error",
            synthetic=True,
            source="config",
            recoverability_hint="no",
        )

    event = snapshot.failure_event
    if event is not None:
        match event.kind:
            case "dispatch_exception":
                return _classify_dispatch(snapshot, event.exception_type)
            case "verdict_exception":
                return _classify_verdict(event.exception_type)
            case "cycle_exhausted":
                return FailureClassification(
                    cause="cycle_exhausted",
                    synthetic=True,
                    source="runner",
                    recoverability_hint="no",
                )
            case "ambiguous_worktree":
                return FailureClassification(
                    cause="ambiguous_worktree_abort",
                    synthetic=True,
                    source="runner",
                    recoverability_hint="no",
                )
            case "agent_abort":
                return FailureClassification(
                    cause="agent_declared_abort",
                    synthetic=False,
                    source="agent",
                    recoverability_hint="no",
                )

    if snapshot.workflow_end_status == "ERROR":
        return FailureClassification(
            cause="runtime_error",
            synthetic=True,
            source="runner",
            recoverability_hint="no",
        )

    return FailureClassification(
        cause="unknown_external_error",
        synthetic=True,
        source="external",
        recoverability_hint="no",
    )
