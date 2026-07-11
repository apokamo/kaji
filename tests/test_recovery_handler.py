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
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev", "schema_version": 1},
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

    # 末尾の comment は child 終了後の結果報告（follow-up）。
    assert rec.calls == ["comment", "sleep", "child", "comment"]
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


def test_child_result_is_reported_as_follow_up_comment(tmp_path: Path) -> None:
    # triage コメントは child 起動前に投稿するため child_run_status=pending で固定される。
    # 成否は follow-up コメントで Issue から追える。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    provider = _FakeProvider()

    handler = _handler(tmp_path, run_dir, provider=provider, child_launcher=lambda _a, _c: 0)
    handler.run()

    assert len(provider.comments) == 2
    assert "child_run_status | `pending`" in provider.comments[0]
    assert provider.comments[1].startswith("## Workflow auto recovery result")
    assert "child_run_status | `COMPLETE`" in provider.comments[1]


def test_child_result_comment_failure_does_not_break_run(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    stderr = io.StringIO()

    class _P(_FakeProvider):
        def comment_issue(self, issue_id: str, body: str) -> Comment:
            if body.startswith("## Workflow auto recovery result"):
                raise RuntimeError("gh down")
            return super().comment_issue(issue_id, body)

    result = _handler(
        tmp_path, run_dir, provider=_P(), child_launcher=lambda _a, _c: 0, stderr=stderr
    ).run()

    assert result.decision.decision == "resume"
    assert result.child_exit_code == 0
    assert "recovery result comment posting failed" in stderr.getvalue()


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


def test_second_triage_of_same_root_run_is_exhausted(tmp_path: Path) -> None:
    # child が run_dir を作る前に落ちると chain / newer-run では再入を検出できない。
    # 同じ root run に handler を 2 回かけても child 起動は 1 回に留まること。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    def _launch(argv: list[str], _cwd: Path) -> int:
        launched.append(argv)
        return 3  # run_dir も recovery-chain.json も作らずに死んだ child

    first = _handler(tmp_path, run_dir, provider=_FakeProvider(), child_launcher=_launch).run()
    second = _handler(tmp_path, run_dir, provider=_FakeProvider(), child_launcher=_launch).run()

    assert first.decision.decision == "resume"
    assert second.decision.decision == "exhausted"
    assert second.child_exit_code is None
    assert len(launched) == 1
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.decision == "exhausted"


def test_unreadable_recovery_json_is_fail_closed_to_exhausted(tmp_path: Path) -> None:
    # 過去の triage 痕跡が壊れていた場合、読めないことを理由に再開してはならない。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    (run_dir / RECOVERY_FILE).write_text("{ not json", encoding="utf-8")
    launched: list[list[str]] = []

    result = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    ).run()

    assert result.decision.decision == "exhausted"
    assert launched == []


def test_existing_child_run_dir_blocks_relaunch(tmp_path: Path) -> None:
    # recovery.json が失われても、この run を parent とする child run dir が
    # budget 消費の裏取りになる（child run_id を親より小さくして newer-run gate と分離）。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    child = run_dir.parent / "260710110000"
    child.mkdir()
    write_recovery_chain(
        child / RECOVERY_CHAIN_FILE, root_run_id=run_dir.name, parent_run_id=run_dir.name
    )
    launched: list[list[str]] = []

    result = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    ).run()

    assert result.decision.decision == "exhausted"
    assert launched == []


def test_comment_only_triage_does_not_consume_budget(tmp_path: Path) -> None:
    # 再開しなかった triage（auto_recover 無効）は budget を消費しない。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    launched: list[list[str]] = []

    first = _handler(tmp_path, run_dir, provider=_FakeProvider(), auto_recover=False).run()
    second = _handler(
        tmp_path,
        run_dir,
        provider=_FakeProvider(),
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    ).run()

    assert first.decision.decision == "comment_only"
    assert second.decision.decision == "resume"
    assert len(launched) == 1


# --- legacy artifact（failure_event 契約以前の run） ---


def test_legacy_abort_run_is_not_reported_as_kaji_bug(tmp_path: Path) -> None:
    # 本機能導入前の ABORT 終端 run に `kaji recover` を向けても bug issue を起票しない。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / "260710120000"
    run_dir.mkdir(parents=True)
    events = [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev"},
        {"event": "workflow_end", "status": "ABORT", "error": None},
    ]
    (run_dir / "run.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    provider = _FakeProvider()

    result = _handler(tmp_path, run_dir, provider=provider).run()

    assert result.decision.classification.cause == "unknown_external_error"
    assert result.decision.decision == "comment_only"
    assert result.decision.bug_issue is None
    assert provider.created == []


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


# --- Issue #296: interactive terminal model capacity (canonical-only focused message) ---

_CAPACITY_ERROR = (
    "tmux pane exited before writing verdict.yaml; "
    "transient provider error detected (pattern: 'at capacity')"
)


def _build_capacity_run(
    tmp_path: Path, run_id: str = "260710120000", *, error: str = _CAPACITY_ERROR
) -> Path:
    """A failed `implement` run whose ``attempt_error`` is the canonical-only
    focused message `_terminal_exit_detail` produces for a pane that died with a
    provider capacity error buried in its transcript (Issue #296)."""
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / run_id
    run_dir.mkdir(parents=True)
    events = [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev", "schema_version": 1},
        {
            "event": "failure_event",
            "kind": "dispatch_exception",
            "step_id": "implement",
            "exception_type": "CLIExecutionError",
            "cycle_name": None,
            "synthetic": True,
        },
        {"event": "workflow_end", "status": "ERROR", "error": error},
    ]
    (run_dir / "run.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    attempt = run_dir / "steps" / "implement" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "result.json").write_text(
        json.dumps(
            {
                "step_id": "implement",
                "attempt": 1,
                "status": "ERROR",
                "exit_code": 1,
                "signal": None,
                "started_at": "t",
                "ended_at": "t",
                "duration_ms": 1,
                "session_id": None,
                "dispatch": "agent",
                "error": error,
                "synthetic": True,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_capacity_auto_recover_true_launches_child_once_with_candidate(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_capacity_run(tmp_path)
    launched: list[list[str]] = []
    provider = _FakeProvider()

    handler = _handler(
        tmp_path,
        run_dir,
        provider=provider,
        child_launcher=lambda argv, _cwd: (launched.append(argv), 0)[1],
    )
    result = handler.run()

    assert result.decision.classification.cause == "dispatch_failure"
    assert result.decision.classification.recoverability_hint == "candidate"
    assert result.decision.decision == "resume"
    assert len(launched) == 1
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.auto_recovery_attempted is True
    assert persisted.auto_recovery_attempt_no == 1
    assert persisted.resume_scheduled_at is not None
    # sensitive gate（`\btoken\b`）が焦点化メッセージには一切現れないため誤発火しない。
    assert "Token usage" not in "\n".join(provider.comments)


def test_capacity_auto_recover_false_posts_candidate_disabled_without_child(
    tmp_path: Path,
) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_capacity_run(tmp_path)
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
    assert result.decision.recoverable is True
    assert result.child_exit_code is None
    assert launched == []
    assert len(provider.comments) == 1

    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.decision == "comment_only"
    assert persisted.recoverable is True
    assert persisted.auto_recovery_attempted is False
    assert persisted.auto_recovery_attempt_no == 0
    assert persisted.resume_scheduled_at is None
    assert persisted.resume_command is not None


def test_capacity_diagnostic_extraction_failure_is_not_resumable(tmp_path: Path) -> None:
    # Issue #296 EB 5: kaji の診断抽出失敗（terminal.log 不在由来の
    # "diagnostic unavailable"）は transient pattern を含まないため、
    # provider capacity candidate とは語彙上・判定上区別される。
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    no_log_error = (
        "tmux pane exited before writing verdict.yaml; diagnostic unavailable: no terminal.log"
    )
    run_dir = _build_capacity_run(tmp_path, error=no_log_error)

    result = _handler(tmp_path, run_dir, provider=_FakeProvider()).run()

    assert result.decision.classification.recoverability_hint == "no"
    assert result.decision.decision == "not_resumable"
