"""Medium tests: ``RecoveryHandler`` の orchestration (Issue #288).

provider mock + child launcher mock + ``wait_seconds`` 短縮注入で、
triage コメント → ウェイト → child 起動の順序、budget guard、ウェイト明けの
newer-run 検出、SIGINT 中断、safety gate、child 終了後の ``recovery.json``
書き戻し、bug issue 作成経路を検証する。
"""

from __future__ import annotations

import io
import json
import subprocess as _sp
from pathlib import Path

import pytest

from kaji_harness.models import Step, Workflow
from kaji_harness.providers.models import Comment, Issue
from kaji_harness.recovery.handler import RecoveryHandler
from kaji_harness.recovery.models import (
    RECOVERY_CHAIN_FILE,
    RECOVERY_FILE,
    read_recovery_json,
    write_recovery_chain,
)

pytestmark = pytest.mark.medium

_ISSUE = "local-pc1-99"


class _FakeProvider:
    """comment_issue / create_issue のみを提供する最小 provider stub。"""

    def __init__(self, *, comment_error: Exception | None = None) -> None:
        self.comments: list[str] = []
        self.created: list[tuple[str, list[str]]] = []
        self._comment_error = comment_error

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        if self._comment_error is not None:
            raise self._comment_error
        self.comments.append(body)
        return Comment(author="", body=body, created_at="", ref="https://x.invalid/c/1")

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        self.created.append((title, list(labels or [])))
        return Issue(id="301", title=title, body=body, state="open")


def _workflow() -> Workflow:
    return Workflow(
        name="dev",
        description="",
        execution_policy="auto",
        steps=[
            Step(
                id="implement", skill="issue-implement", agent="claude", on={"PASS": "review-code"}
            ),
            Step(id="review-code", skill="issue-review-code", agent="codex", on={"PASS": "end"}),
        ],
    )


def _git_repo(tmp_path: Path, branch: str = "feat/99") -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    _sp.run(["git", "init", "-q", f"--initial-branch={branch}", str(wt)], check=True)
    return wt


def _seed_state(tmp_path: Path, worktree: Path, branch: str = "feat/99") -> None:
    d = tmp_path / ".kaji-artifacts" / _ISSUE
    d.mkdir(parents=True, exist_ok=True)
    (d / "session-state.json").write_text(
        json.dumps(
            {
                "issue_number": _ISSUE,
                "sessions": {},
                "step_history": [],
                "cycle_counts": {},
                "last_completed_step": "implement",
                "last_transition_verdict": None,
                "worktree_dir": str(worktree),
                "branch_name": branch,
            }
        ),
        encoding="utf-8",
    )


def _build_run(
    tmp_path: Path, run_id: str = "260710120000", *, chain: tuple[str, str] | None = None
) -> Path:
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / run_id
    run_dir.mkdir(parents=True)
    events = [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev"},
        {
            "event": "failure_event",
            "kind": "verdict_exception",
            "step_id": "review-code",
            "exception_type": "VerdictNotFound",
            "cycle_name": None,
            "synthetic": True,
        },
        {"event": "workflow_end", "status": "ERROR", "error": "VerdictNotFound: missing block"},
    ]
    (run_dir / "run.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    attempt = run_dir / "steps" / "review-code" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "result.json").write_text(
        json.dumps(
            {
                "step_id": "review-code",
                "attempt": 1,
                "status": "ABORT",
                "exit_code": 0,
                "signal": None,
                "started_at": "t",
                "ended_at": "t",
                "duration_ms": 1,
                "session_id": "sess",
                "dispatch": "agent",
                "error": "VerdictNotFound: missing block",
                "synthetic": True,
            }
        ),
        encoding="utf-8",
    )
    if chain is not None:
        write_recovery_chain(
            run_dir / RECOVERY_CHAIN_FILE, root_run_id=chain[0], parent_run_id=chain[1]
        )
    return run_dir


class _Recorder:
    """コメント投稿・sleep・child 起動の呼び出し順を 1 本の列に記録する。"""

    def __init__(self) -> None:
        self.calls: list[str] = []


def _handler(
    tmp_path: Path,
    run_dir: Path,
    *,
    provider: object,
    auto_recover: bool = True,
    sleep=None,
    child_launcher=None,
    stderr: io.StringIO | None = None,
) -> RecoveryHandler:
    return RecoveryHandler(
        workflow=_workflow(),
        workflow_path=Path("wf.yaml"),
        issue_id=_ISSUE,
        issue_ref=_ISSUE,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        run_dir=run_dir,
        workdir=tmp_path,
        provider=provider,  # type: ignore[arg-type]
        auto_recover=auto_recover,
        wait_seconds=0,
        sleep=sleep or (lambda _s: None),
        child_launcher=child_launcher or (lambda _argv, _cwd: 0),
        stderr=stderr or io.StringIO(),
    )


# --- resume 経路の順序 ---


def test_comment_is_posted_before_wait_and_child_launch(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    rec = _Recorder()

    class _P(_FakeProvider):
        def comment_issue(self, issue_id: str, body: str) -> Comment:
            rec.calls.append("comment")
            return super().comment_issue(issue_id, body)

    provider = _P()
    handler = _handler(
        tmp_path,
        run_dir,
        provider=provider,
        sleep=lambda _s: rec.calls.append("sleep"),
        child_launcher=lambda _argv, _cwd: (rec.calls.append("child"), 0)[1],
    )
    result = handler.run()

    assert rec.calls == ["comment", "sleep", "child"]
    assert result.decision.decision == "resume"
    assert result.child_exit_code == 0
    assert "resume_scheduled_at" in provider.comments[0]


def test_resume_writes_child_run_id_and_status_back(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    runs_dir = run_dir.parent

    def _launch(_argv: list[str], _cwd: Path) -> int:
        child = runs_dir / "260710121500"
        child.mkdir()
        write_recovery_chain(
            child / RECOVERY_CHAIN_FILE, root_run_id="260710120000", parent_run_id="260710120000"
        )
        return 1

    handler = _handler(tmp_path, run_dir, provider=_FakeProvider(), child_launcher=_launch)
    result = handler.run()

    assert result.child_exit_code == 1
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.decision == "resume"
    assert persisted.auto_recovery_attempted is True
    assert persisted.auto_recovery_attempt_no == 1
    assert persisted.recovery_child_run_id == "260710121500"
    assert persisted.recovery_child_final_status == "ABORT"
    assert persisted.resume_started_at is not None
    assert persisted.triage_comment_ref == "https://x.invalid/c/1"

    events = [
        json.loads(line) for line in (run_dir / "run.log").read_text(encoding="utf-8").splitlines()
    ]
    kinds = [e["event"] for e in events]
    assert "recovery_decision" in kinds
    assert "recovery_scheduled" in kinds
    assert "recovery_attempt_start" in kinds
    assert "recovery_attempt_end" in kinds


def test_child_launch_argv_carries_chain_flags(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    captured: list[list[str]] = []

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (captured.append(argv), 0)[1],
    )
    handler.run()

    argv = captured[0]
    assert "run" in argv
    # cycle 上限の安全弁を handler が自動解除しないこと（Issue #288 決定 14）。
    assert "--reset-cycle" not in argv
    assert argv[argv.index("--from") + 1] == "review-code"
    assert argv[argv.index("--recovery-root") + 1] == "260710120000"
    assert argv[argv.index("--recovery-parent") + 1] == "260710120000"


# --- budget guard ---


def test_recovery_child_failure_is_exhausted_without_relaunch(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path, chain=("260710110000", "260710110000"))
    launched: list[list[str]] = []

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "exhausted"
    assert result.child_exit_code is None
    assert launched == []


# --- ウェイト明けの再チェック / 中断 ---


def test_newer_run_during_wait_cancels_child_launch(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    def _sleep(_s: float) -> None:
        (run_dir.parent / "260710130000").mkdir()

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        sleep=_sleep,
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "cancelled_newer_run_detected"
    assert launched == []
    assert read_recovery_json(run_dir / RECOVERY_FILE).decision == "cancelled_newer_run_detected"


def test_sigint_during_wait_cancels_child_launch(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    def _sleep(_s: float) -> None:
        raise KeyboardInterrupt

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        sleep=_sleep,
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "cancelled_interrupted"
    assert launched == []
    assert read_recovery_json(run_dir / RECOVERY_FILE).decision == "cancelled_interrupted"


# --- safety gate ---


def test_branch_mismatch_blocks_resume(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path, branch="main")
    _seed_state(tmp_path, wt, branch="feat/99")
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "not_resumable"
    assert launched == []


def test_comment_failure_suppresses_auto_recovery(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    handler = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(comment_error=RuntimeError("gh down")),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "not_resumable"
    assert result.decision.triage_comment_ref is None
    assert launched == []
    # triage は best-effort: recovery.json / run.log は残る
    assert (run_dir / RECOVERY_FILE).exists()
    assert any("comment" in e for e in result.decision.evidence)


def test_missing_artifacts_yield_bug_issue_path(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / "260710120000"
    run_dir.mkdir(parents=True)  # run.log なし = 必要 artifact を読めない
    provider = _FakeProvider()

    handler = _handler(tmp_path, run_dir, provider=provider)
    result = handler.run()

    assert result.decision.classification.cause == "kaji_bug_suspected"
    assert result.decision.decision == "bug_issue_created"
    assert result.child_exit_code is None
    assert provider.created
    title, labels = provider.created[0]
    assert title.startswith("bug:")
    assert labels == ["type:bug"]
    assert result.decision.bug_issue == {"id": "301", "url": ""}


def test_bug_issue_creation_failure_downgrades_to_not_resumable(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / "260710120000"
    run_dir.mkdir(parents=True)

    class _P(_FakeProvider):
        def create_issue(self, **kwargs: object) -> Issue:  # type: ignore[override]
            raise RuntimeError("gh down")

    handler = _handler(tmp_path, run_dir, provider=_P())
    result = handler.run()

    assert result.decision.decision == "not_resumable"
    assert result.decision.bug_issue is None


# --- auto_recover 無効 / stderr サマリ ---


def test_auto_recover_disabled_posts_comment_without_child(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []
    provider = _FakeProvider()

    handler = _handler(
        tmp_path,
        run_dir,
        provider=provider,
        auto_recover=False,
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.decision == "comment_only"
    assert result.child_exit_code is None
    assert launched == []
    assert len(provider.comments) == 1


def test_stderr_summary_is_emitted(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    err = io.StringIO()

    handler = _handler(tmp_path, run_dir, provider=_FakeProvider(), auto_recover=False, stderr=err)
    handler.run()

    out = err.getvalue()
    assert "--- failure triage ---" in out
    assert "failed_step:    review-code" in out
    assert "comment:        https://x.invalid/c/1" in out


def test_provider_none_blocks_resume_but_still_writes_artifact(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)

    handler = _handler(tmp_path, run_dir, provider=None)
    result = handler.run()

    assert result.decision.decision == "not_resumable"
    assert (run_dir / RECOVERY_FILE).exists()
