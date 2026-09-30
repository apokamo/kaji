"""Small tests for the artifact verdict fallback helpers (Issue #426)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.artifact_verdict import (
    RunContext,
    attempt_number,
    evaluate_result,
    parse_run_id,
    run_context_from_env,
    run_context_from_verdict_path,
)
from kaji_harness.errors import VerdictArtifactUnusableError
from kaji_harness.providers.markers import is_valid_verdict_status

ISSUE = "122"
RUN_ID = "260903210335"


def _verdict_path(
    root: str = "/w/.kaji/artifacts",
    *,
    issue: str = ISSUE,
    run: str = RUN_ID,
    step: str = "final-check",
    attempt: str = "attempt-001",
) -> str:
    return f"{root}/{issue}/runs/{run}/steps/{step}/{attempt}/verdict.yaml"


# ---------- run_id grammar ----------


@pytest.mark.small
@pytest.mark.parametrize("run_id", ["260903210335", "260903210335-002", "260903210335-1000"])
def test_parse_run_id_accepts_allocated_forms(run_id: str) -> None:
    assert parse_run_id(run_id) == run_id


@pytest.mark.small
@pytest.mark.parametrize(
    "run_id",
    ["", "../x", "26090321033", "2609032103355", "260903210335-2", "260903210335/../x", "abc"],
)
def test_parse_run_id_rejects_invalid(run_id: str) -> None:
    with pytest.raises(ValueError, match="run id"):
        parse_run_id(run_id)


# ---------- verdict_path -> run context ----------


@pytest.mark.small
def test_run_context_from_verdict_path_derives_run() -> None:
    ctx = run_context_from_verdict_path(_verdict_path(), issue_id=ISSUE)

    assert ctx == RunContext(run_id=RUN_ID, runs_dir=Path("/w/.kaji/artifacts") / ISSUE / "runs")
    assert ctx.run_dir == Path("/w/.kaji/artifacts") / ISSUE / "runs" / RUN_ID


@pytest.mark.small
@pytest.mark.parametrize(
    "path",
    [
        "/w/a/122/runs/260903210335/steps/final-check/latest/verdict.yaml",
        "/w/a/122/runs/260903210335/steps/final-check/attempt-1/verdict.yaml",
        "/w/a/122/runs/260903210335/steps/final-check/attempt-001/result.json",
        "/w/a/122/runs/260903210335/final-check/attempt-001/verdict.yaml",
        "/w/a/122/run/260903210335/steps/final-check/attempt-001/verdict.yaml",
        "/w/a/122/runs/bad/steps/final-check/attempt-001/verdict.yaml",
        "verdict.yaml",
    ],
)
def test_run_context_from_verdict_path_rejects_shape_mismatch(path: str) -> None:
    with pytest.raises(ValueError):
        run_context_from_verdict_path(path, issue_id=ISSUE)


@pytest.mark.small
def test_run_context_from_verdict_path_rejects_issue_mismatch() -> None:
    with pytest.raises(ValueError, match="issue"):
        run_context_from_verdict_path(_verdict_path(issue="999"), issue_id=ISSUE)


@pytest.mark.small
def test_run_context_from_env_ignores_invalid_values() -> None:
    assert run_context_from_env({}, ISSUE) is None
    assert run_context_from_env({"KAJI_VERDICT_PATH": ""}, ISSUE) is None
    assert run_context_from_env({"KAJI_VERDICT_PATH": "/tmp/x/verdict.yaml"}, ISSUE) is None
    assert run_context_from_env({"KAJI_VERDICT_PATH": _verdict_path(issue="999")}, ISSUE) is None


@pytest.mark.small
def test_run_context_from_env_uses_valid_path() -> None:
    ctx = run_context_from_env({"KAJI_VERDICT_PATH": _verdict_path()}, ISSUE)

    assert ctx is not None
    assert ctx.run_id == RUN_ID


# ---------- attempt numbering ----------


@pytest.mark.small
def test_attempt_number_parses_numeric_suffix() -> None:
    assert attempt_number("attempt-001") == 1
    assert attempt_number("attempt-010") == 10
    assert attempt_number("attempt-1000") == 1000


@pytest.mark.small
@pytest.mark.parametrize(
    "name", ["latest", "attempt-1", "attempt-", "attempt-00a", "x-attempt-001"]
)
def test_attempt_number_rejects_other_names(name: str) -> None:
    assert attempt_number(name) is None


# ---------- result.json evaluation ----------


@pytest.mark.small
def test_evaluate_result_returns_recorded_ended_at() -> None:
    ended = "2026-09-03T12:03:11.482113+00:00"
    assert evaluate_result({"status": "PASS", "ended_at": ended}, "PASS") == ended


@pytest.mark.small
@pytest.mark.parametrize(
    "ended_at",
    ["not-a-time", "2026-09-03T12:03:11", "", None, 123],
)
def test_evaluate_result_unknown_ended_at_is_none(ended_at: object) -> None:
    assert evaluate_result({"status": "PASS", "ended_at": ended_at}, "PASS") is None


@pytest.mark.small
def test_evaluate_result_missing_ended_at_is_none() -> None:
    assert evaluate_result({"status": "PASS"}, "PASS") is None


@pytest.mark.small
def test_evaluate_result_keeps_z_suffix_verbatim() -> None:
    assert evaluate_result({"status": "PASS", "ended_at": "2026-09-03T12:03:11Z"}, "PASS") == (
        "2026-09-03T12:03:11Z"
    )


@pytest.mark.small
@pytest.mark.parametrize(
    ("result", "match"),
    [
        ({"status": "ABORT", "synthetic": True}, "abnormal"),
        ({"status": "PASS", "error": "boom"}, "abnormal"),
        ({"status": "RETRY"}, "status"),
    ],
)
def test_evaluate_result_rejects_abnormal_or_conflicting(
    result: dict[str, object], match: str
) -> None:
    with pytest.raises(VerdictArtifactUnusableError, match=match):
        evaluate_result(result, "PASS")


@pytest.mark.small
@pytest.mark.parametrize("synthetic", ["true", 1, [], {}, None])
def test_evaluate_result_rejects_invalid_synthetic_type(synthetic: object) -> None:
    with pytest.raises(VerdictArtifactUnusableError, match="invalid"):
        evaluate_result({"status": "PASS", "synthetic": synthetic}, "PASS")


@pytest.mark.small
def test_evaluate_result_accepts_legacy_record_without_synthetic() -> None:
    assert evaluate_result({"status": "PASS", "error": None}, "PASS") is None


@pytest.mark.small
def test_evaluate_result_ignores_exit_code_and_signal() -> None:
    result = {"status": "PASS", "exit_code": 143, "signal": "SIGTERM", "synthetic": False}
    assert evaluate_result(result, "PASS") is None


# ---------- marker status grammar ----------


@pytest.mark.small
@pytest.mark.parametrize("status", ["PASS", "RETRY", "ABORT", "BACK", "BACK_DESIGN", "BACK_V2"])
def test_is_valid_verdict_status_accepts(status: str) -> None:
    assert is_valid_verdict_status(status)


@pytest.mark.small
@pytest.mark.parametrize("status", ["back", "BACK_", "BACK_lower", "pass", "", "FAIL", "PASS "])
def test_is_valid_verdict_status_rejects(status: str) -> None:
    assert not is_valid_verdict_status(status)
