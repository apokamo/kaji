"""Medium tests: ``RetryPlanner`` gates and ``kaji recover --retry-failed-step`` (Issue #540)."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.exit_codes import (
    EXIT_INVALID_INPUT,
    EXIT_RETRY_REFUSED,
    EXIT_RUNTIME_ERROR,
)
from kaji_harness.commands.main import main
from kaji_harness.providers.base import Comment
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.recovery.gates import GateHit, RetryPlanner, prepare_retry
from kaji_harness.runstate.launch import LaunchLock, write_launch_record
from kaji_harness.runstate.process import ProcessRecord, current_process_record
from kaji_harness.workflow import load_workflow
from tests.runstate_helpers import (
    end,
    ev,
    record,
    start,
    step_end,
    step_start,
    write_config,
    write_run,
    write_state,
)

pytestmark = [pytest.mark.medium, pytest.mark.usefixtures("stub_github_issue")]

ISSUE = "540"
RUN = "261112000000"
GATE_LINE = re.compile(r"^retry-gate: (?P<name>[a-z_]+)(?: (?P<detail>.*))?$")
WORKFLOW = """name: dev
description: d
requires_provider: any
execution_policy: auto

steps:
  - id: start
    skill: issue-start
    agent: claude
    on:
      PASS: implement
      ABORT: end
  - id: implement
    skill: issue-implement
    agent: claude
    on:
      PASS: review
      ABORT: end
  - id: review
    skill: issue-review-code
    agent: claude
    resume: implement
    on:
      PASS: end
      ABORT: end
"""


@pytest.fixture(autouse=True)
def _no_github_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """D7: retry は GitHub に書き込まない。書き込み系が呼ばれたら失敗させる。"""

    def _forbidden(*_args: object, **_kwargs: object) -> Comment:
        raise AssertionError("retry must not write to GitHub")

    monkeypatch.setattr(GitHubProvider, "comment_issue", _forbidden)
    monkeypatch.setattr(GitHubProvider, "create_issue", _forbidden, raising=False)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _dead_record(run_id: str | None = RUN) -> ProcessRecord:
    rec = current_process_record("run", run_id=run_id)
    return rec.model_copy(update={"pid": _dead_pid(), "pid_start_ticks": 1})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=feat/540", str(root)], check=True)
    write_config(root)
    (root / "wf.yaml").write_text(WORKFLOW, encoding="utf-8")
    for skill in ("issue-start", "issue-implement", "issue-review-code"):
        path = root / ".claude" / "skills" / skill / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# {skill}\n", encoding="utf-8")
    write_state(root / ".kaji-artifacts", ISSUE, worktree=str(root), branch="feat/540")
    return root


def _artifacts(repo: Path) -> Path:
    return repo / ".kaji-artifacts"


def _failed_run(
    repo: Path,
    *,
    step: str = "implement",
    kind: str = "dispatch_exception",
    exception: str | None = "StepTimeoutError",
    error: str = "timed out",
    run_id: str = RUN,
    process: ProcessRecord | None = None,
    end_status: str = "ERROR",
    extra: list[dict[str, object]] | None = None,
) -> Path:
    events = [
        start(),
        step_start(step),
        ev("failure_event", kind=kind, step_id=step, exception_type=exception, synthetic=True),
        step_end(step, 1, "ABORT"),
        end(end_status, f"{exception}: {error}" if exception else None),
        *(extra or []),
    ]
    return write_run(
        _artifacts(repo),
        ISSUE,
        run_id,
        events,
        process=process if process is not None else _dead_record(run_id),
        results={(step, 1): {"error": error, "synthetic": True}},
    )


def _planner(repo: Path, pane_state: str = "absent") -> RetryPlanner:
    return RetryPlanner(
        workflow=load_workflow(repo / "wf.yaml"),
        artifacts_dir=_artifacts(repo),
        issue_id=ISSUE,
        pane_inspector=lambda _pane: pane_state,  # type: ignore[arg-type,return-value]
    )


def _names(plan_gates: list[GateHit]) -> list[str]:
    return [gate.name for gate in plan_gates]


class TestPlannerGates:
    def test_passing_run_resumes_from_failed_step(self, repo: Path) -> None:
        _failed_run(repo)
        plan = _planner(repo).plan()
        assert plan.gates == []
        assert plan.resume_from == "implement"
        assert plan.discarded_resume_session is False
        assert plan.root_run_id == RUN

    def test_resume_step_rewinds_to_session_source(self, repo: Path) -> None:
        _failed_run(repo, step="review")
        plan = _planner(repo).plan()
        assert plan.gates == []
        assert plan.resume_from == "implement"
        assert plan.discarded_resume_session is True

    def test_non_candidate_hint_and_spent_budget_do_not_refuse(self, repo: Path) -> None:
        # D4 / D6: recoverability_hint=no（unknown_external_error）と budget 消費済みでも通過
        run_dir = _failed_run(repo, exception="WeirdError")
        (run_dir / "recovery.json").write_text(
            json.dumps(
                {
                    "run_id": RUN,
                    "recoverable": False,
                    "decision": "exhausted",
                    "classification": {
                        "cause": "unknown_external_error",
                        "synthetic": True,
                        "source": "external",
                        "recoverability_hint": "no",
                    },
                    "failed_step": "implement",
                    "auto_recovery_attempted": True,
                    "auto_recovery_attempt_no": 1,
                }
            ),
            encoding="utf-8",
        )
        plan = _planner(repo).plan()
        assert plan.gates == []

    def test_no_target_run(self, repo: Path) -> None:
        assert _names(_planner(repo).plan().gates) == ["no_target_run"]

    def test_newer_run_detected(self, repo: Path) -> None:
        _failed_run(repo)
        _failed_run(repo, run_id="261112010000")
        assert "newer_run_detected" in _names(_planner(repo).plan(RUN).gates)

    def test_run_alive(self, repo: Path) -> None:
        _failed_run(repo, process=current_process_record("run", run_id=RUN))
        assert _names(_planner(repo).plan().gates) == ["run_alive"]

    def test_liveness_unknown(self, repo: Path) -> None:
        _failed_run(repo, process=record(1, host="elsewhere", run_id=RUN))
        assert _names(_planner(repo).plan().gates) == ["liveness_unknown"]

    def test_issue_run_alive_from_launch_record(self, repo: Path) -> None:
        _failed_run(repo)
        write_launch_record(_artifacts(repo), ISSUE, current_process_record("run"))
        assert _names(_planner(repo).plan().gates) == ["issue_run_alive"]

    def test_issue_liveness_unknown(self, repo: Path) -> None:
        _failed_run(repo, run_id="261112010000")
        write_run(
            _artifacts(repo), ISSUE, RUN, [start(), step_start("x")]
        )  # 記録なし・未終端の旧 run
        assert _names(_planner(repo).plan().gates) == ["issue_liveness_unknown"]

    def test_finished_other_run_does_not_block(self, repo: Path) -> None:
        _failed_run(repo, run_id="261112010000")
        write_run(_artifacts(repo), ISSUE, RUN, [start(), end("COMPLETE")])
        assert _planner(repo).plan().gates == []

    def test_run_not_failed(self, repo: Path) -> None:
        write_run(_artifacts(repo), ISSUE, RUN, [start(), end("COMPLETE")], process=_dead_record())
        assert _names(_planner(repo).plan().gates) == ["run_not_failed"]

    def test_artifact_unreadable(self, repo: Path) -> None:
        run_dir = _failed_run(repo)
        (run_dir / "steps" / "implement" / "attempt-001" / "result.json").unlink()
        assert "artifact_unreadable" in _names(_planner(repo).plan().gates)

    def test_workflow_mismatch(self, repo: Path) -> None:
        run_dir = _failed_run(repo)
        log = run_dir / "run.log"
        log.write_text(log.read_text().replace('"workflow": "dev"', '"workflow": "other"'))
        assert _names(_planner(repo).plan().gates) == ["workflow_mismatch"]

    def test_unknown_failed_step(self, repo: Path) -> None:
        _failed_run(repo, step="ghost")
        assert _names(_planner(repo).plan().gates) == ["unknown_failed_step"]

    def test_non_resumable_skill(self, repo: Path) -> None:
        _failed_run(repo, step="start")
        assert _names(_planner(repo).plan().gates) == ["non_resumable_skill"]

    @pytest.mark.parametrize(
        ("kind", "exception", "end_status", "gate"),
        [
            ("agent_abort", None, "ABORT", "agent_declared_abort"),
            ("cycle_exhausted", None, "ABORT", "cycle_exhausted"),
            ("interrupted", None, "ERROR", "user_interrupted"),
            ("ambiguous_worktree", None, "ABORT", "ambiguous_worktree_abort"),
        ],
    )
    def test_refusing_causes(
        self, repo: Path, kind: str, exception: str | None, end_status: str, gate: str
    ) -> None:
        _failed_run(repo, kind=kind, exception=exception, end_status=end_status)
        assert _names(_planner(repo).plan().gates) == [gate]

    def test_sensitive_failure_pattern(self, repo: Path) -> None:
        _failed_run(repo, exception="CLIExecutionError", error="HTTP 401 Unauthorized")
        assert _names(_planner(repo).plan().gates) == ["sensitive_failure_pattern"]

    def test_worktree_unavailable(self, repo: Path) -> None:
        _failed_run(repo)
        write_state(_artifacts(repo), ISSUE, worktree=str(repo / "missing"), branch="feat/540")
        assert _names(_planner(repo).plan().gates) == ["worktree_unavailable"]

    def test_branch_mismatch(self, repo: Path) -> None:
        _failed_run(repo)
        write_state(_artifacts(repo), ISSUE, worktree=str(repo), branch="feat/9")
        assert _names(_planner(repo).plan().gates) == ["branch_mismatch"]

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("active", ["agent_pane_alive"]),
            ("unknown", ["agent_pane_unverifiable"]),
            ("shell_only", []),
            ("absent", []),
            ("not_owned", []),
        ],
    )
    def test_agent_pane(self, repo: Path, state: str, expected: list[str]) -> None:
        run_dir = _failed_run(repo)
        pane = run_dir / "steps" / "implement" / "attempt-001" / "pane-metadata.json"
        pane.write_text(json.dumps({"pane_id": "p1", "origin_pane": "o", "kaji_run": RUN}))
        assert _names(_planner(repo, state).plan().gates) == expected

    def test_unreadable_pane_metadata_is_unverifiable(self, repo: Path) -> None:
        run_dir = _failed_run(repo)
        pane = run_dir / "steps" / "implement" / "attempt-001" / "pane-metadata.json"
        pane.write_text("{bad")
        assert _names(_planner(repo).plan().gates) == ["agent_pane_unverifiable"]

    def test_vanished_run_is_retried_from_unterminated_step(self, repo: Path) -> None:
        # R3 形: kill された run（workflow_end なし、生存していない pid、result.json なし）
        write_run(
            _artifacts(repo),
            ISSUE,
            RUN,
            [start(), step_start("implement")],
            process=_dead_record(),
        )
        plan = _planner(repo).plan()
        assert plan.gates == []
        assert plan.resume_from == "implement"


class TestConcurrency:
    def test_lock_holder_blocks_second_and_launcher_record_blocks_third(self, repo: Path) -> None:
        _failed_run(repo)
        planner = _planner(repo)
        seen: list[list[str]] = []

        def _second(_plan: object) -> None:
            seen.append(_names(prepare_retry(planner, None).plan.gates))

        first = prepare_retry(planner, None, after_plan=_second)
        assert first.plan.gates == [] and first.launcher_record is not None
        assert seen == [["concurrent_launch"]]
        third = prepare_retry(planner, None)
        assert _names(third.plan.gates) == ["issue_run_alive"]
        assert third.launcher_record is None

    def test_launcher_record_failure_releases_lock(self, repo: Path) -> None:
        _failed_run(repo)
        with patch("kaji_harness.recovery.gates.write_launch_record", side_effect=OSError("ro fs")):
            rc = main(_recover_argv(repo))
        assert rc == EXIT_RUNTIME_ERROR
        lock = LaunchLock(_artifacts(repo), ISSUE)
        assert lock.acquire() is True
        lock.release()
        assert list((_artifacts(repo) / ISSUE / "launches").glob("*.json")) == []

    def test_lock_file_failure_exits_3(self, repo: Path) -> None:
        _failed_run(repo)
        with (
            patch.object(LaunchLock, "acquire", side_effect=OSError("cannot open")),
            patch("kaji_harness.commands.recover.default_child_popen") as popen,
        ):
            assert main(_recover_argv(repo)) == EXIT_RUNTIME_ERROR
        popen.assert_not_called()


def _recover_argv(repo: Path, *extra: str) -> list[str]:
    return [
        "recover",
        str(repo / "wf.yaml"),
        ISSUE,
        "--retry-failed-step",
        "--workdir",
        str(repo),
        *extra,
    ]


class _FakeChild:
    def __init__(self, code: int) -> None:
        self.pid = os.getpid()
        self._code = code

    def wait(self) -> int:
        return self._code


class TestRecoverCommand:
    @pytest.mark.parametrize("child_code", [0, 1, 3])
    def test_launches_child_and_returns_its_exit_code(self, repo: Path, child_code: int) -> None:
        run_dir = _failed_run(repo)
        with patch(
            "kaji_harness.commands.recover.default_child_popen",
            return_value=_FakeChild(child_code),
        ) as popen:
            rc = main(_recover_argv(repo))
        assert rc == child_code
        argv = popen.call_args.args[0]
        joined = " ".join(argv)
        assert f"--from implement --recovery-root {RUN} --recovery-parent {RUN}" in joined
        assert "--recovery-trigger retry_failed_step" in joined
        retries = json.loads((run_dir / "recovery.json").read_text())["retries"]
        assert retries[-1]["outcome"] == "launched"
        assert retries[-1]["trigger"] == "retry_failed_step"
        assert retries[-1]["child_exit_code"] == child_code
        events = [
            json.loads(line)["event"] for line in (run_dir / "run.log").read_text().splitlines()
        ]
        assert "retry_requested" in events and "retry_child_end" in events
        assert list((_artifacts(repo) / ISSUE / "launches").glob("*.json")) == []

    def test_existing_triage_record_is_kept(self, repo: Path) -> None:
        run_dir = _failed_run(repo)
        triage = {
            "run_id": RUN,
            "recoverable": False,
            "decision": "comment_only",
            "classification": {
                "cause": "dispatch_failure",
                "synthetic": True,
                "source": "external",
                "recoverability_hint": "no",
            },
            "failed_step": "implement",
            "triage_comment_ref": "https://example/1",
        }
        (run_dir / "recovery.json").write_text(json.dumps(triage))
        with patch("kaji_harness.commands.recover.default_child_popen", return_value=_FakeChild(0)):
            assert main(_recover_argv(repo)) == 0
        saved = json.loads((run_dir / "recovery.json").read_text())
        assert saved["decision"] == "comment_only"
        assert saved["triage_comment_ref"] == "https://example/1"
        assert len(saved["retries"]) == 1

    def test_refusal_exits_4_with_gate_lines(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _failed_run(repo, process=current_process_record("run", run_id=RUN))
        write_state(_artifacts(repo), ISSUE, worktree=str(repo), branch="feat/9")
        with patch("kaji_harness.commands.recover.default_child_popen") as popen:
            rc = main(_recover_argv(repo))
        assert rc == EXIT_RETRY_REFUSED
        popen.assert_not_called()
        err = capsys.readouterr().err.splitlines()
        assert err[0] == "Error: retry refused by safety gate(s): run_alive, branch_mismatch"
        names = [m.group("name") for line in err[1:] if (m := GATE_LINE.match(line))]
        assert names == ["run_alive", "branch_mismatch"]
        assert "pid=" in err[1] and "worktree=feat/540 state=feat/9" in err[2]
        retries = json.loads((run_dir / "recovery.json").read_text())["retries"]
        assert retries[-1]["outcome"] == "refused"
        assert [g["name"] for g in retries[-1]["gates"]] == ["run_alive", "branch_mismatch"]

    def test_retry_and_auto_recover_are_mutually_exclusive(self, repo: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main(_recover_argv(repo, "--auto-recover"))
        assert exc.value.code == 2

    def test_missing_run_id_dir_exits_2(self, repo: Path) -> None:
        _failed_run(repo)
        assert main(_recover_argv(repo, "--run-id", "999999999999")) == EXIT_INVALID_INPUT

    def test_record_failure_before_launch_exits_3(self, repo: Path) -> None:
        _failed_run(repo)
        with (
            patch(
                "kaji_harness.recovery.gates.RetryRecorder.record_launched",
                side_effect=OSError("disk full"),
            ),
            patch("kaji_harness.commands.recover.default_child_popen") as popen,
        ):
            assert main(_recover_argv(repo)) == EXIT_RUNTIME_ERROR
        popen.assert_not_called()
        assert list((_artifacts(repo) / ISSUE / "launches").glob("*.json")) == []

    def test_record_failure_after_child_keeps_child_exit_code(self, repo: Path) -> None:
        _failed_run(repo)
        with (
            patch(
                "kaji_harness.recovery.gates.RetryRecorder.record_child_end",
                side_effect=OSError("disk full"),
            ),
            patch("kaji_harness.commands.recover.default_child_popen", return_value=_FakeChild(1)),
        ):
            assert main(_recover_argv(repo)) == 1

    def test_child_start_failure_exits_3(self, repo: Path) -> None:
        run_dir = _failed_run(repo)
        with patch(
            "kaji_harness.commands.recover.default_child_popen", side_effect=OSError("no exec")
        ):
            assert main(_recover_argv(repo)) == EXIT_RUNTIME_ERROR
        retries = json.loads((run_dir / "recovery.json").read_text())["retries"]
        assert retries[-1]["outcome"] == "launch_failed"

    def test_plain_recover_still_rejects_unterminated_run(self, repo: Path) -> None:
        write_run(
            _artifacts(repo), ISSUE, RUN, [start(), step_start("implement")], process=_dead_record()
        )
        rc = main(["recover", str(repo / "wf.yaml"), ISSUE, "--workdir", str(repo)])
        assert rc == EXIT_INVALID_INPUT


def test_hostname_fixture_matches_real_host() -> None:
    assert current_process_record("run").hostname == socket.gethostname()
