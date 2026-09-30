"""``kaji issue resolve-verdict`` artifact fallback tests (Issue #426)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.commands.issue import (
    EXIT_VERDICT_ARTIFACT_UNUSABLE,
    _handle_issue_resolve_verdict,
)
from kaji_harness.providers.github import GitHubProviderError
from kaji_harness.providers.local import LocalProvider

pytestmark = pytest.mark.medium

PRODUCER = "implement-precheck"
CONSUMER = "final-check"
RUN = "260903210335"
OTHER_RUN = "260903220114"
MARKER_PASS = f"<!-- kaji-verdict: step={PRODUCER} status=PASS -->\nmarker comment"


@dataclass
class Env:
    provider: LocalProvider
    issue_id: str
    artifacts: Path

    @property
    def runs_dir(self) -> Path:
        return self.artifacts / self.issue_id / "runs"

    def consumer_path(self, run: str = RUN) -> Path:
        return self.runs_dir / run / "steps" / CONSUMER / "attempt-001" / "verdict.yaml"

    def attempt_dir(self, run: str, step: str, n: int) -> Path:
        return self.runs_dir / run / "steps" / step / f"attempt-{n:03d}"

    def write_attempt(
        self,
        n: int,
        *,
        run: str = RUN,
        step: str = PRODUCER,
        status: str = "PASS",
        suggestion: str = "",
        result: dict[str, Any] | None = None,
        verdict_text: str | None = None,
        write_verdict: bool = True,
    ) -> Path:
        attempt = self.attempt_dir(run, step, n)
        attempt.mkdir(parents=True, exist_ok=True)
        if write_verdict:
            text = verdict_text
            if text is None:
                text = (
                    f"status: {status}\nreason: r\nevidence: e\nsuggestion: {suggestion!r}\n"
                    if suggestion
                    else f"status: {status}\nreason: r\nevidence: e\nsuggestion: ''\n"
                )
            (attempt / "verdict.yaml").write_text(text, encoding="utf-8")
        if result is not None:
            (attempt / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return attempt

    def write_chain(self, run: str, parent: str) -> None:
        run_dir = self.runs_dir / run
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "recovery-chain.json").write_text(
            json.dumps({"root_run_id": parent, "parent_run_id": parent}), encoding="utf-8"
        )

    def comment(self, body: str) -> None:
        self.provider.comment_issue(self.issue_id, body)

    def artifact_files(self) -> list[str]:
        return sorted(str(p) for p in self.artifacts.rglob("*"))

    def run(self, *args: str, resolver: Any = None) -> int:
        return _handle_issue_resolve_verdict(
            self.provider,
            [self.issue_id, "--step", PRODUCER, *args],
            artifacts_dir_resolver=resolver,
        )


def _result(status: str = "PASS", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "step_id": PRODUCER,
        "attempt": 1,
        "status": status,
        "exit_code": 0,
        "signal": None,
        "started_at": "2026-09-03T12:00:00+00:00",
        "ended_at": "2026-09-03T12:03:11.482113+00:00",
        "duration_ms": 191482,
        "session_id": None,
        "dispatch": "agent",
        "error": None,
        "synthetic": False,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _no_inherited_verdict_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAJI_VERDICT_PATH", raising=False)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    provider = LocalProvider(repo_root=repo, machine_id="pc1")
    issue = provider.create_issue(title="t", body="b", slug="x", labels=["type:bug"])
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return Env(provider=provider, issue_id=issue.id, artifacts=artifacts)


def _out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    return payload


# ---------- reproduction (OB/EB) ----------


def test_marker_missing_falls_back_to_artifact_via_env(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#122 OB: producer verdict.yaml is PASS but the marker is missing -> exit 4 (before fix)."""
    env.write_attempt(1, result=_result())
    env.comment("report without marker")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(env.consumer_path()))

    rc = env.run()

    assert rc == 0
    payload = _out(capsys)
    assert payload["source"] == "artifact"
    assert payload["status"] == "PASS"
    assert payload["step"] == PRODUCER


# ---------- marker priority / compatibility ----------


def test_marker_output_is_unchanged_when_artifact_coexists(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, status="RETRY", result=_result("RETRY"))
    env.write_attempt(2, verdict_text=": : broken")
    env.comment(MARKER_PASS)

    rc = env.run("--current-verdict-path", str(env.consumer_path()))

    assert rc == 0
    payload = _out(capsys)
    assert set(payload) == {"step", "status", "meta", "created_at"}
    assert payload["status"] == "PASS"
    assert payload["meta"] == {}


@pytest.mark.parametrize(
    ("body", "extra", "expected"),
    [
        (f"<!-- kaji-verdict: step={PRODUCER} status=PASS bad -->", [], 5),
        (f"<!-- kaji-verdict: step={PRODUCER} status=PASS -->", ["--require-meta", "target"], 6),
    ],
)
def test_malformed_or_meta_missing_marker_does_not_fall_back(
    env: Env, body: str, extra: list[str], expected: int
) -> None:
    env.write_attempt(1, result=_result())
    env.comment(body)

    assert env.run("--current-verdict-path", str(env.consumer_path()), *extra) == expected


def test_comment_fetch_failure_does_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kaji_harness.commands.exit_codes import EXIT_RUNTIME_ERROR

    class FailingProvider:
        def list_issue_comments_all(self, issue_id: str) -> list[Any]:
            raise GitHubProviderError("gh failed")

    verdict = tmp_path / "artifacts" / "426" / "runs" / RUN / "steps" / PRODUCER / "attempt-001"
    verdict.mkdir(parents=True)
    (verdict / "verdict.yaml").write_text(
        "status: PASS\nreason: r\nevidence: e\n", encoding="utf-8"
    )
    consumer = tmp_path / "artifacts" / "426" / "runs" / RUN / "steps" / CONSUMER / "attempt-001"
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(consumer / "verdict.yaml"))

    rc = _handle_issue_resolve_verdict(FailingProvider(), ["426", "--step", PRODUCER])  # type: ignore[arg-type]

    assert rc == EXIT_RUNTIME_ERROR


# ---------- run identification ----------


def test_no_run_context_is_not_found_even_if_other_run_has_pass(env: Env) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    env.comment("no marker")

    assert env.run() == 4


def test_invalid_env_path_is_not_found_not_usage_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")
    monkeypatch.setenv("KAJI_VERDICT_PATH", "/not/an/attempt/verdict.yaml")

    assert env.run() == 4


def test_env_with_other_issue_is_ignored(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")
    foreign = env.artifacts / "999" / "runs" / RUN / "steps" / CONSUMER / "attempt-001"
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(foreign / "verdict.yaml"))

    assert env.run() == 4


def test_explicit_option_takes_priority_over_env(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, status="RETRY", run=OTHER_RUN, result=_result("RETRY"))
    env.write_attempt(1, run=RUN, result=_result())
    env.comment("no marker")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(env.consumer_path(OTHER_RUN)))

    assert env.run("--current-verdict-path", str(env.consumer_path(RUN))) == 0
    assert _out(capsys)["status"] == "PASS"


def test_run_dir_absent_is_not_found(env: Env) -> None:
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


# ---------- latest attempt ----------


def test_latest_attempt_wins_over_older_pass(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.write_attempt(1, result=_result())
    env.write_attempt(2, status="RETRY", result=_result("RETRY", attempt=2))
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    payload = _out(capsys)
    assert payload["status"] == "RETRY"
    assert payload["attempt"] == 2


def test_attempts_are_ordered_numerically(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.write_attempt(9, result=_result())
    env.write_attempt(10, status="RETRY", result=_result("RETRY", attempt=10))
    (env.runs_dir / RUN / "steps" / PRODUCER / "latest").symlink_to("attempt-009")
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    assert _out(capsys)["attempt"] == 10


@pytest.mark.parametrize("status", ["ABORT", "BACK", "BACK_DESIGN"])
def test_abort_and_back_pass_through(
    env: Env, status: str, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, status=status, suggestion="fix it", result=_result(status))
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    assert _out(capsys)["status"] == status


@pytest.mark.parametrize(
    "case",
    ["missing-file", "broken-yaml", "bad-status", "lower-back", "abort-no-suggestion"],
)
def test_unusable_latest_attempt_stops_without_falling_back(env: Env, case: str) -> None:
    env.write_attempt(1, result=_result())
    if case == "missing-file":
        env.write_attempt(2, write_verdict=False)
    elif case == "broken-yaml":
        env.write_attempt(2, verdict_text="status: [PASS\n")
    elif case == "bad-status":
        env.write_attempt(2, status="DONE")
    elif case == "lower-back":
        env.write_attempt(2, status="back", suggestion="x")
    else:
        env.write_attempt(2, status="ABORT")
    env.comment("no marker")

    rc = env.run("--current-verdict-path", str(env.consumer_path()))

    assert rc == EXIT_VERDICT_ARTIFACT_UNUSABLE == 7


# ---------- recovery chain ----------


def test_recovers_from_parent_run_when_step_never_ran(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    (env.runs_dir / RUN / "steps" / CONSUMER).mkdir(parents=True)
    env.write_chain(RUN, OTHER_RUN)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    payload = _out(capsys)
    assert payload["run_id"] == OTHER_RUN
    assert payload["requested_run_id"] == RUN


def test_does_not_consult_parent_when_requested_run_has_attempt(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    env.write_attempt(1, run=RUN, status="RETRY", result=_result("RETRY"))
    env.write_chain(RUN, OTHER_RUN)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    payload = _out(capsys)
    assert payload["status"] == "RETRY"
    assert payload["run_id"] == RUN


def test_parent_with_broken_latest_attempt_is_unusable(env: Env) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    env.write_attempt(2, run=OTHER_RUN, verdict_text=": : broken")
    (env.runs_dir / RUN).mkdir(parents=True)
    env.write_chain(RUN, OTHER_RUN)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 7


def test_no_chain_does_not_search_sibling_runs(env: Env) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    (env.runs_dir / RUN).mkdir(parents=True)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


@pytest.mark.parametrize("parent", ["../escape", "not-a-run", RUN])
def test_invalid_or_cyclic_parent_is_not_found(env: Env, parent: str) -> None:
    env.write_attempt(1, run=OTHER_RUN, result=_result())
    (env.runs_dir / RUN).mkdir(parents=True)
    env.write_chain(RUN, parent)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


def test_two_run_cycle_is_not_found(env: Env) -> None:
    (env.runs_dir / RUN).mkdir(parents=True)
    (env.runs_dir / OTHER_RUN).mkdir(parents=True)
    env.write_chain(RUN, OTHER_RUN)
    env.write_chain(OTHER_RUN, RUN)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


def test_unreadable_chain_is_not_found(env: Env) -> None:
    (env.runs_dir / RUN).mkdir(parents=True)
    (env.runs_dir / RUN / "recovery-chain.json").write_text("{not json", encoding="utf-8")
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


@pytest.mark.parametrize("payload", [b"[]", b"null", b"\xff", b'{"root_run_id": 1}'])
def test_malformed_chain_shape_or_encoding_is_not_found(env: Env, payload: bytes) -> None:
    (env.runs_dir / RUN).mkdir(parents=True)
    (env.runs_dir / RUN / "recovery-chain.json").write_bytes(payload)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 4


# ---------- output / ended_at ----------


def test_artifact_output_contract(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    attempt = env.write_attempt(1, result=_result())
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0

    payload = _out(capsys)
    assert payload == {
        "step": PRODUCER,
        "status": "PASS",
        "meta": {},
        "source": "artifact",
        "run_id": RUN,
        "requested_run_id": RUN,
        "attempt": 1,
        "verdict_path": str(attempt / "verdict.yaml"),
        "ended_at": "2026-09-03T12:03:11.482113+00:00",
    }
    assert "created_at" not in payload


@pytest.mark.parametrize("case", ["no-result", "bad-ended-at", "no-tz", "missing-key"])
def test_unknown_ended_at_is_null_and_not_filled_from_mtime(
    env: Env, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if case == "no-result":
        attempt = env.write_attempt(1)
    elif case == "bad-ended-at":
        attempt = env.write_attempt(1, result=_result(ended_at="yesterday"))
    elif case == "no-tz":
        attempt = env.write_attempt(1, result=_result(ended_at="2026-09-03T12:03:11"))
    else:
        result = _result()
        del result["ended_at"]
        attempt = env.write_attempt(1, result=result)
    env.comment("no marker")
    import os

    os.utime(attempt / "verdict.yaml", (1_000_000_000, 1_000_000_000))

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    assert _out(capsys)["ended_at"] is None


# ---------- result.json conflicts ----------


@pytest.mark.parametrize(
    "result",
    [
        _result("ABORT", synthetic=True),
        _result(error="dispatch failed"),
        _result("RETRY"),
    ],
)
def test_abnormal_or_conflicting_result_json_is_unusable(env: Env, result: dict[str, Any]) -> None:
    env.write_attempt(1, status="PASS", result=result)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 7


@pytest.mark.parametrize(
    "synthetic", ["true", 1, [], {}, None], ids=["str", "int", "list", "dict", "null"]
)
def test_invalid_synthetic_type_in_result_json_is_unusable(env: Env, synthetic: object) -> None:
    env.write_attempt(1, result=_result(synthetic=synthetic))
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 7


@pytest.mark.parametrize("error", [1, [], {}], ids=["int", "list", "dict"])
def test_invalid_error_type_in_result_json_is_unusable(env: Env, error: object) -> None:
    env.write_attempt(1, result=_result(error=error))
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 7


def test_legacy_result_json_without_synthetic_is_accepted(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _result()
    del result["synthetic"]
    env.write_attempt(1, result=result)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0
    assert _out(capsys)["status"] == "PASS"


@pytest.mark.parametrize("text", ["{broken", "[]", '"str"', "null"])
def test_unreadable_result_json_is_unusable(env: Env, text: str) -> None:
    attempt = env.write_attempt(1)
    (attempt / "result.json").write_text(text, encoding="utf-8")
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 7


def test_missing_result_json_is_accepted(env: Env) -> None:
    env.write_attempt(1)
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0


def test_exit_code_and_signal_do_not_mark_attempt_abnormal(env: Env) -> None:
    env.write_attempt(1, result=_result(exit_code=143, signal="SIGTERM"))
    env.comment("no marker")

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0


# ---------- require-meta / read-only ----------


def test_require_meta_with_artifact_resolution_is_meta_missing(env: Env) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")

    rc = env.run("--current-verdict-path", str(env.consumer_path()), "--require-meta", "target")

    assert rc == 6


def test_artifact_resolution_is_read_only(env: Env) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")
    comments_before = len(env.provider.list_issue_comments_all(env.issue_id))
    files_before = env.artifact_files()

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0

    assert len(env.provider.list_issue_comments_all(env.issue_id)) == comments_before
    assert env.artifact_files() == files_before


# ---------- arguments ----------


def test_run_and_current_verdict_path_are_mutually_exclusive(env: Env) -> None:
    with pytest.raises(SystemExit) as excinfo:
        env.run("--run", RUN, "--current-verdict-path", str(env.consumer_path()))

    assert excinfo.value.code == 2


@pytest.mark.parametrize("run_id", ["../x", "123", ""])
def test_invalid_run_id_is_usage_error(env: Env, run_id: str) -> None:
    env.comment(MARKER_PASS)

    assert env.run("--run", run_id) == 2


def test_current_verdict_path_for_other_issue_is_usage_error(env: Env) -> None:
    env.comment(MARKER_PASS)
    foreign = env.artifacts / "999" / "runs" / RUN / "steps" / CONSUMER / "attempt-001"

    assert env.run("--current-verdict-path", str(foreign / "verdict.yaml")) == 2


def test_current_verdict_path_with_bad_shape_is_usage_error(env: Env) -> None:
    env.comment(MARKER_PASS)

    assert env.run("--current-verdict-path", "/tmp/verdict.yaml") == 2


def test_current_verdict_path_does_not_require_existing_file(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")
    assert not env.consumer_path().exists()

    assert env.run("--current-verdict-path", str(env.consumer_path())) == 0


# ---------- --run ----------


def test_run_option_resolves_via_injected_artifacts_dir(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_attempt(1, result=_result())
    env.comment("no marker")

    rc = env.run("--run", RUN, resolver=lambda: env.artifacts)

    assert rc == 0
    payload = _out(capsys)
    assert payload["run_id"] == RUN
    assert payload["source"] == "artifact"


def test_resolver_is_not_called_when_marker_exists(env: Env) -> None:
    env.comment(MARKER_PASS)
    calls: list[int] = []

    def resolver() -> Path:
        calls.append(1)
        return env.artifacts

    assert env.run("--run", RUN, resolver=resolver) == 0
    assert calls == []


def test_run_option_with_unknown_run_is_not_found(env: Env) -> None:
    env.comment("no marker")

    assert env.run("--run", RUN, resolver=lambda: env.artifacts) == 4
