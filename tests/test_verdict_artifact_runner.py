"""Runner-level tests for Issue #220 artifact verdict + attempt layout.

Small: ``allocate_attempt_dir`` の採番 / latest symlink。
Medium: ``WorkflowRunner.run()`` を mock CLI + 実 filesystem で回し、
artifact-primary 解決 / stdout 正規化保存 / cycle attempt 分離 /
comment fallback の現在 attempt scoping / legacy layout 互換を検証する。

``test_verdict_artifact.py`` は ``resolve_verdict`` 等の純関数を Small で
網羅する。本ファイルは runner への wiring（attempt_dir 採番・``verdict_path``
注入・``attempt_started_at`` 記録・正規化保存）を検証する補完層。
"""

from __future__ import annotations

import json
import os
import subprocess as _sp
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import CLIExecutionError, StepTimeoutError, VerdictNotFound
from kaji_harness.models import CLIResult, CostInfo, CycleDefinition, Step, Verdict, Workflow
from kaji_harness.providers import LocalProvider
from kaji_harness.providers.models import Comment
from kaji_harness.runner import WorkflowRunner, allocate_attempt_dir, allocate_run_dir
from kaji_harness.verdict import load_verdict_yaml, write_verdict_yaml

VALID = {"PASS", "RETRY", "BACK", "ABORT"}


# ============================================================
# Small: allocate_run_dir
# ============================================================


@pytest.mark.small
class TestAllocateRunDir:
    def test_first_run_uses_second_precision_base_id(self, tmp_path: Path) -> None:
        timestamp = datetime(2026, 7, 10, 1, 43, 1)

        run_dir = allocate_run_dir(tmp_path, timestamp)

        assert run_dir == tmp_path / "260710014301"
        assert run_dir.is_dir()

    def test_existing_base_id_allocates_002_suffix(self, tmp_path: Path) -> None:
        timestamp = datetime(2026, 7, 10, 1, 43, 1)
        (tmp_path / "260710014301").mkdir()

        run_dir = allocate_run_dir(tmp_path, timestamp)

        assert run_dir == tmp_path / "260710014301-002"
        assert run_dir.is_dir()

    def test_existing_suffix_allocates_next_suffix(self, tmp_path: Path) -> None:
        timestamp = datetime(2026, 7, 10, 1, 43, 1)
        (tmp_path / "260710014301").mkdir()
        (tmp_path / "260710014301-002").mkdir()

        run_dir = allocate_run_dir(tmp_path, timestamp)

        assert run_dir == tmp_path / "260710014301-003"
        assert run_dir.is_dir()


# ============================================================
# Small: allocate_attempt_dir
# ============================================================


@pytest.mark.small
class TestAllocateAttemptDir:
    def test_first_attempt_is_001(self, tmp_path: Path) -> None:
        attempt = allocate_attempt_dir(tmp_path, "implement")
        assert attempt == tmp_path / "steps" / "implement" / "attempt-001"
        assert attempt.is_dir()

    def test_second_dispatch_increments_to_002(self, tmp_path: Path) -> None:
        first = allocate_attempt_dir(tmp_path, "implement")
        second = allocate_attempt_dir(tmp_path, "implement")
        assert first.name == "attempt-001"
        assert second.name == "attempt-002"
        assert first.is_dir() and second.is_dir()

    def test_distinct_steps_have_independent_counters(self, tmp_path: Path) -> None:
        a = allocate_attempt_dir(tmp_path, "design")
        b = allocate_attempt_dir(tmp_path, "review")
        assert a.name == "attempt-001"
        assert b.name == "attempt-001"
        assert a.parent != b.parent

    def test_latest_symlink_points_to_newest(self, tmp_path: Path) -> None:
        allocate_attempt_dir(tmp_path, "implement")
        second = allocate_attempt_dir(tmp_path, "implement")
        latest = tmp_path / "steps" / "implement" / "latest"
        if not latest.is_symlink():
            pytest.skip("filesystem does not support symlinks")
        assert os.readlink(latest) == second.name

    def test_symlink_failure_does_not_break_allocation(self, tmp_path: Path) -> None:
        with patch("kaji_harness.runner.os.symlink", side_effect=OSError("no symlink")):
            attempt = allocate_attempt_dir(tmp_path, "implement")
        # symlink 失敗でも attempt は採番・作成される
        assert attempt.name == "attempt-001"
        assert attempt.is_dir()


# ============================================================
# Medium: runner-level resolution / layout
# ============================================================


def _verdict_block(status: str, reason: str = "ok", evidence: str = "e") -> str:
    suggestion = "next" if status in ("ABORT", "BACK") else ""
    return (
        "報告本文\n\n"
        "---VERDICT---\n"
        f"status: {status}\n"
        f'reason: "{reason}"\n'
        f'evidence: "{evidence}"\n'
        f'suggestion: "{suggestion}"\n'
        "---END_VERDICT---\n"
    )


def _cli_result(
    status: str | None,
    session_id: str = "sess",
    *,
    exit_code: int | None = 0,
    signal: str | None = None,
) -> CLIResult:
    """status=None なら verdict block を含まない stdout を返す。

    Issue #222: ``exit_code`` / ``signal`` を CLIResult に載せ、runner が
    attempt result.json へ運ぶ経路を検証できるようにする。
    """
    output = "verdict 無しの作業ログ" if status is None else _verdict_block(status)
    return CLIResult(
        full_output=output,
        session_id=session_id,
        cost=CostInfo(usd=0.0),
        stderr="",
        exit_code=exit_code,
        signal=signal,
    )


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    config_file = kaji_dir / "config.toml"
    config_file.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(config_file)


def _ensure_local_issue(tmp_path: Path, issue: int) -> None:
    counter_path = tmp_path / ".kaji" / "counters" / "pc1.txt"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    issues_root = tmp_path / ".kaji" / "issues"
    issues_root.mkdir(parents=True, exist_ok=True)
    if any(d.name.startswith(f"local-pc1-{issue}-") for d in issues_root.iterdir()):
        return
    counter_path.write_text(str(issue - 1))
    provider = LocalProvider(repo_root=tmp_path, machine_id="pc1")
    provider.create_issue(
        title=f"test issue {issue}",
        body="body",
        labels=["type:feature"],
        slug=f"test-{issue}",
    )


def _make_runner(
    tmp_path: Path, workflow: Workflow, issue: int = 99, **kwargs: object
) -> WorkflowRunner:
    config = _make_config(tmp_path)
    _ensure_local_issue(tmp_path, issue)
    return WorkflowRunner(
        workflow=workflow,
        issue_number=str(issue),
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=config,
        **kwargs,  # type: ignore[arg-type]
    )


def _single_step_workflow() -> Workflow:
    return Workflow(
        name="single",
        description="one agent step",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="issue-implement",
                agent="claude",
                on={"PASS": "end", "RETRY": "end", "ABORT": "end", "BACK": "end"},
            ),
        ],
    )


def _cycle_workflow() -> Workflow:
    return Workflow(
        name="cycle",
        description="review cycle",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="issue-implement",
                agent="claude",
                on={"PASS": "review", "ABORT": "end"},
            ),
            Step(
                id="review",
                skill="issue-review",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix", "ABORT": "end"},
            ),
            Step(
                id="fix",
                skill="issue-fix-code",
                agent="claude",
                resume="implement",
                on={"PASS": "verify", "ABORT": "end"},
            ),
            Step(
                id="verify",
                skill="issue-verify-code",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix", "ABORT": "end"},
            ),
        ],
        cycles=[
            CycleDefinition(
                name="code-review",
                entry="review",
                loop=["fix", "verify"],
                max_iterations=3,
                on_exhaust="ABORT",
            ),
        ],
    )


def _run_root(tmp_path: Path, issue: int = 99) -> Path:
    """``.kaji-artifacts/local-pc1-<issue>/runs/<run_id>/`` の最新 run dir。"""
    runs = tmp_path / ".kaji-artifacts" / f"local-pc1-{issue}" / "runs"
    run_dirs = sorted(p for p in runs.iterdir() if p.is_dir())
    assert run_dirs, f"no run dir under {runs}"
    return run_dirs[-1]


def _verdict_sources(run_dir: Path) -> list[dict[str, str]]:
    log = run_dir / "run.log"
    events = []
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry.get("event") == "verdict_source":
            events.append(entry)
    return events


def _events(run_dir: Path, event: str) -> list[dict]:
    """run.log から指定 event の行を順序保持で抽出する。"""
    log = run_dir / "run.log"
    out: list[dict] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry.get("event") == event:
            out.append(entry)
    return out


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        fixed = datetime(2026, 7, 10, 1, 43, 1)
        if tz is UTC:
            return fixed.replace(tzinfo=UTC)
        return fixed


@pytest.mark.medium
class TestRunnerStdoutNormalization:
    def test_stdout_only_normalized_to_attempt_verdict_yaml(self, tmp_path: Path) -> None:
        """agent が verdict.yaml を書かない（stdout のみ）→ stdout 解決し、
        attempt-001/verdict.yaml に正規化保存される。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.last_completed_step == "implement"
        run_dir = _run_root(tmp_path)
        attempt = run_dir / "steps" / "implement" / "attempt-001"
        vfile = attempt / "verdict.yaml"
        assert vfile.exists(), "stdout 解決でも verdict.yaml が正規化保存される"
        loaded = load_verdict_yaml(vfile, VALID)
        assert loaded.status == "PASS"
        sources = _verdict_sources(run_dir)
        assert sources and sources[-1]["source"] == "stdout"
        assert sources[-1]["attempt"] == "attempt-001"
        # prompt.txt も保存される
        assert (attempt / "prompt.txt").exists()

    def test_two_runs_in_same_second_use_distinct_run_dirs(self, tmp_path: Path) -> None:
        """同一秒 base id の 2 run が同じ run.log / steps を共有しない。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result("PASS")

        with (
            patch("kaji_harness.runner.datetime", _FixedDateTime),
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            _make_runner(tmp_path, workflow).run()
            _make_runner(tmp_path, workflow).run()

        runs = tmp_path / ".kaji-artifacts" / "local-pc1-99" / "runs"
        run_dirs = sorted(p for p in runs.iterdir() if p.is_dir())
        assert [p.name for p in run_dirs] == ["260710014301", "260710014301-002"]

        for run_dir in run_dirs:
            starts = _events(run_dir, "workflow_start")
            assert len(starts) == 1
            assert (run_dir / "steps" / "implement" / "attempt-001" / "verdict.yaml").exists()


@pytest.mark.medium
class TestRunnerArtifactPrimary:
    def test_artifact_wins_over_divergent_stdout(self, tmp_path: Path) -> None:
        """agent が verdict.yaml=PASS を書く。stdout は ABORT。artifact を採用。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            log_dir = kwargs["log_dir"]
            assert isinstance(log_dir, Path)
            write_verdict_yaml(
                log_dir / "verdict.yaml",
                Verdict(status="PASS", reason="agent wrote", evidence="e", suggestion=""),
            )
            return _cli_result("ABORT")  # stdout は別 verdict

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.step_history[-1].verdict_status == "PASS"
        assert state.step_history[-1].verdict_reason == "agent wrote"
        run_dir = _run_root(tmp_path)
        sources = _verdict_sources(run_dir)
        assert sources[-1]["source"] == "artifact"


@pytest.mark.medium
class TestRunnerCycleAttemptSeparation:
    def test_retry_creates_separate_attempt_dirs(self, tmp_path: Path) -> None:
        """RETRY ループで同一 step が 2 回 dispatch → attempt-001 / attempt-002 が
        各々の verdict.yaml を持ち、誤って共有しない。"""
        workflow = _cycle_workflow()
        # implement → review(RETRY) → fix → verify(RETRY) → fix → verify(PASS)
        results = iter(
            [
                _cli_result("PASS", "s-impl"),
                _cli_result("RETRY", "s-rev1"),
                _cli_result("PASS", "s-fix1"),
                _cli_result("RETRY", "s-ver1"),
                _cli_result("PASS", "s-fix2"),
                _cli_result("PASS", "s-ver2"),
            ]
        )

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return next(results)

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.last_completed_step == "verify"
        run_dir = _run_root(tmp_path)
        fix_steps = run_dir / "steps" / "fix"
        assert (fix_steps / "attempt-001" / "verdict.yaml").exists()
        assert (fix_steps / "attempt-002" / "verdict.yaml").exists()
        verify_steps = run_dir / "steps" / "verify"
        assert (verify_steps / "attempt-001" / "verdict.yaml").exists()
        assert (verify_steps / "attempt-002" / "verdict.yaml").exists()
        # attempt-001 と attempt-002 は別個（共有しない）
        assert (verify_steps / "attempt-001" / "verdict.yaml").read_text() != (
            verify_steps / "attempt-002" / "verdict.yaml"
        ).read_text()


class _CommentProvider:
    """real LocalProvider に委譲しつつ ``view_issue`` のみ制御 comment を返す。

    comment fallback の wiring を runner レベルで検証するための注入用 provider。
    ``resolve_issue_context`` は実 provider に委譲し、``resolve_pr_context`` は
    no-op（None）。
    """

    def __init__(self, real: LocalProvider, comments: list[Comment]) -> None:
        self._real = real
        self._comments = comments

    def resolve_issue_context(self, rid: str) -> object:
        return self._real.resolve_issue_context(rid)

    def resolve_pr_context(self, branch_name: str) -> None:
        return None

    def view_issue(self, issue_id: str) -> object:
        return SimpleNamespace(comments=list(self._comments))


@pytest.mark.medium
class TestRunnerCommentFallback:
    def test_current_comment_adopted_when_no_artifact_no_stdout(self, tmp_path: Path) -> None:
        """artifact 無し + stdout に verdict 無し + 現在 attempt 以降の comment 有り
        → comment で解決し、verdict.yaml に正規化保存される。"""
        workflow = _single_step_workflow()
        config = _make_config(tmp_path)
        _ensure_local_issue(tmp_path, 99)
        real = LocalProvider(repo_root=tmp_path, machine_id="pc1")
        # far-future timestamp → 必ず attempt_started_at 以降に scope される
        comment = Comment(
            author="agent",
            body=_verdict_block("PASS", reason="from comment"),
            created_at="2099-01-01T00:00:00Z",
        )
        fake = _CommentProvider(real, [comment])

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result(None)  # stdout に verdict 無し / artifact も書かない

        runner = WorkflowRunner(
            workflow=workflow,
            issue_number="99",
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.get_provider", return_value=fake),
        ):
            state = runner.run()

        assert state.step_history[-1].verdict_status == "PASS"
        assert state.step_history[-1].verdict_reason == "from comment"
        run_dir = _run_root(tmp_path)
        sources = _verdict_sources(run_dir)
        assert sources[-1]["source"] == "comment"
        vfile = run_dir / "steps" / "implement" / "attempt-001" / "verdict.yaml"
        assert vfile.exists()
        assert load_verdict_yaml(vfile, VALID).status == "PASS"

    def test_stale_comment_excluded_on_resume(self, tmp_path: Path) -> None:
        """resume(--from) 経由で、前 attempt の古い comment(created_at < 現在 attempt)
        のみ存在し artifact / stdout が無い場合、古い comment を採用せず VerdictNotFound。"""
        workflow = _single_step_workflow()
        config = _make_config(tmp_path)
        _ensure_local_issue(tmp_path, 99)
        real = LocalProvider(repo_root=tmp_path, machine_id="pc1")
        # far-past timestamp → 現在 attempt の dispatch より前 = stale
        stale = Comment(
            author="agent",
            body=_verdict_block("PASS", reason="stale prev attempt"),
            created_at="2000-01-01T00:00:00Z",
        )
        fake = _CommentProvider(real, [stale])

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result(None)  # artifact も stdout verdict も無し

        runner = WorkflowRunner(
            workflow=workflow,
            issue_number="99",
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
            from_step="implement",
        )
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.get_provider", return_value=fake),
            pytest.raises(VerdictNotFound),
        ):
            runner.run()

    def test_stale_comment_excluded_on_retry(self, tmp_path: Path) -> None:
        """retry ループの 2 回目 dispatch（cycle 内で同一 step を再 dispatch）で、
        前段までに投稿された古い comment(created_at < 当該 attempt) のみが存在し
        artifact / stdout verdict が無い場合、古い comment を採用せず VerdictNotFound。

        前段までの dispatch は stdout verdict で解決させ、最後の fix 再 dispatch だけ
        verdict を一切出さない状況を作る。stale comment は far-past 固定なので、
        どの attempt の ``attempt_started_at`` より前に位置し常に除外される。"""
        workflow = _cycle_workflow()
        config = _make_config(tmp_path)
        _ensure_local_issue(tmp_path, 99)
        real = LocalProvider(repo_root=tmp_path, machine_id="pc1")
        stale = Comment(
            author="agent",
            body=_verdict_block("PASS", reason="stale prev attempt"),
            created_at="2000-01-01T00:00:00Z",
        )
        fake = _CommentProvider(real, [stale])
        # implement(PASS) → review(RETRY) → fix#1(PASS) → verify(RETRY) → fix#2(verdict 無し)
        results = iter(
            [
                _cli_result("PASS"),
                _cli_result("RETRY"),
                _cli_result("PASS"),
                _cli_result("RETRY"),
                _cli_result(None),  # fix attempt-002: artifact も stdout verdict も無し
            ]
        )

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return next(results)

        runner = WorkflowRunner(
            workflow=workflow,
            issue_number="99",
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.get_provider", return_value=fake),
            pytest.raises(VerdictNotFound),
        ):
            runner.run()
        # fix は 2 回 dispatch され、2 回目は stale comment を採らず解決失敗している
        run_dir = _run_root(tmp_path)
        assert (run_dir / "steps" / "fix" / "attempt-001").is_dir()
        assert (run_dir / "steps" / "fix" / "attempt-002").is_dir()


@pytest.mark.medium
class TestRunnerLegacyLayoutCompat:
    def test_new_run_coexists_with_legacy_flat_layout(self, tmp_path: Path) -> None:
        """旧 flat layout (runs/<old_run_id>/<step_id>/) が残っていても、
        新 run は steps/<step_id>/attempt-NNN/ で正常完了し crash しない。"""
        workflow = _single_step_workflow()
        # 旧 layout を事前作成（attempt 無しの flat 構造）
        legacy = tmp_path / ".kaji-artifacts" / "local-pc1-99" / "runs" / "2401010000" / "implement"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "stdout.log").write_text("legacy run stdout", encoding="utf-8")

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.last_completed_step == "implement"
        # 旧 layout は温存される
        assert (legacy / "stdout.log").exists()
        # 新 run は新 layout
        runs = tmp_path / ".kaji-artifacts" / "local-pc1-99" / "runs"
        new_runs = [p for p in runs.iterdir() if p.is_dir() and p.name != "2401010000"]
        assert len(new_runs) == 1
        assert (new_runs[0] / "steps" / "implement" / "attempt-001" / "verdict.yaml").exists()


# ============================================================
# Medium: Issue #222 — result.json / run.log attempt / abort best-effort
# ============================================================


def _result_json(run_dir: Path, step_id: str, attempt: str) -> dict:
    """``steps/<step_id>/<attempt>/result.json`` を読む。"""
    path = run_dir / "steps" / step_id / attempt / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.medium
class TestRunnerResultJsonNormal:
    def test_single_attempt_writes_result_json_and_attempt_in_log(self, tmp_path: Path) -> None:
        """正常 single-attempt で result.json が書かれ、run.log の step イベントに
        attempt / exit_code / signal が付く。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result("PASS", exit_code=0, signal=None)

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.last_completed_step == "implement"
        run_dir = _run_root(tmp_path)
        result = _result_json(run_dir, "implement", "attempt-001")
        assert result["step_id"] == "implement"
        assert result["attempt"] == 1
        assert result["status"] == "PASS"
        assert result["exit_code"] == 0
        assert result["signal"] is None
        assert result["dispatch"] == "agent"
        assert result["error"] is None
        assert result["session_id"] == "sess"
        # started_at <= ended_at
        assert result["started_at"] <= result["ended_at"]
        assert isinstance(result["duration_ms"], int)

        # run.log: step_start / step_end に attempt 付与、step_end に exit_code/signal
        starts = _events(run_dir, "step_start")
        ends = _events(run_dir, "step_end")
        assert starts[-1]["attempt"] == 1
        assert ends[-1]["attempt"] == 1
        assert ends[-1]["exit_code"] == 0
        assert ends[-1]["signal"] is None

        # progress.md に attempt 番号が出る（正常終了は exit/signal を出さない）
        progress = (tmp_path / ".kaji-artifacts" / "local-pc1-99" / "progress.md").read_text()
        assert "implement (attempt 1): PASS" in progress
        # clean exit は (exit 0) を付けない（設計書 § C の例示と一致）
        assert "(exit 0)" not in progress
        assert "(exit" not in progress


@pytest.mark.medium
class TestRunner143RetryRegression:
    """Issue #222 完了条件: 143 で失敗した attempt → retry で PASS。

    同一 step (verify) が cycle 内で 2 回 dispatch され、attempt-001 が
    exit_code=143 の RETRY、attempt-002 が PASS。attempt-001/result.json が
    上書きされず残ることを検証する。"""

    def test_143_retry_then_pass_keeps_failed_attempt_result(self, tmp_path: Path) -> None:
        workflow = _cycle_workflow()
        # implement(PASS) → review(RETRY) → fix#1(PASS) → verify#1(RETRY,143)
        #   → fix#2(PASS) → verify#2(PASS)
        results = iter(
            [
                _cli_result("PASS", "s-impl"),
                _cli_result("RETRY", "s-rev1"),
                _cli_result("PASS", "s-fix1"),
                _cli_result("RETRY", "s-ver1", exit_code=143, signal="SIGTERM"),
                _cli_result("PASS", "s-fix2"),
                _cli_result("PASS", "s-ver2"),
            ]
        )

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return next(results)

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            state = _make_runner(tmp_path, workflow).run()

        assert state.last_completed_step == "verify"
        run_dir = _run_root(tmp_path)

        # attempt-001: RETRY / 143 / SIGTERM
        ver1 = _result_json(run_dir, "verify", "attempt-001")
        assert ver1["status"] == "RETRY"
        assert ver1["exit_code"] == 143
        assert ver1["signal"] == "SIGTERM"
        assert ver1["attempt"] == 1

        # attempt-002: PASS（attempt-001 を上書きしない）
        ver2 = _result_json(run_dir, "verify", "attempt-002")
        assert ver2["status"] == "PASS"
        assert ver2["attempt"] == 2
        # attempt-001 が retry 後も残存している
        assert ver1["exit_code"] == 143

        # run.log の時系列: verify の step_start(1) → step_end(1,143) →
        # step_start(2) → step_end(2)
        verify_starts = [e for e in _events(run_dir, "step_start") if e["step_id"] == "verify"]
        verify_ends = [e for e in _events(run_dir, "step_end") if e["step_id"] == "verify"]
        assert [e["attempt"] for e in verify_starts] == [1, 2]
        assert verify_ends[0]["attempt"] == 1
        assert verify_ends[0]["exit_code"] == 143
        assert verify_ends[0]["signal"] == "SIGTERM"
        assert verify_ends[1]["attempt"] == 2

        # progress.md に failed attempt（exit 143）と最終 PASS の両方
        progress = (tmp_path / ".kaji-artifacts" / "local-pc1-99" / "progress.md").read_text()
        assert "verify (attempt 1): RETRY" in progress
        assert "(exit 143, SIGTERM)" in progress
        assert "verify (attempt 2): PASS" in progress


@pytest.mark.medium
class TestRunnerAbortBestEffort:
    """Issue #222 完了条件: timeout / SIGTERM の異常終了で best-effort 記録。"""

    def test_cli_execution_error_records_abort_result_and_reraises(self, tmp_path: Path) -> None:
        """CLIExecutionError(143) → result.json(status=ABORT, exit_code=143,
        signal=SIGTERM, error)、step_end 発火、record_step(ABORT)、元例外 re-raise。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            raise CLIExecutionError("implement", 143, "terminal failure")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            pytest.raises(CLIExecutionError),
        ):
            _make_runner(tmp_path, workflow).run()

        run_dir = _run_root(tmp_path)
        result = _result_json(run_dir, "implement", "attempt-001")
        assert result["status"] == "ABORT"
        assert result["exit_code"] == 143
        assert result["signal"] == "SIGTERM"
        assert result["attempt"] == 1
        assert "CLIExecutionError" in result["error"]

        # step_end が異常終了でも発火
        ends = [e for e in _events(run_dir, "step_end") if e["step_id"] == "implement"]
        assert len(ends) == 1
        assert ends[0]["attempt"] == 1
        assert ends[0]["exit_code"] == 143
        assert ends[0]["verdict"]["status"] == "ABORT"

        # progress.md に aborted attempt が現れる
        progress = (tmp_path / ".kaji-artifacts" / "local-pc1-99" / "progress.md").read_text()
        assert "implement (attempt 1): ABORT" in progress
        assert "(exit 143, SIGTERM)" in progress

    def test_step_timeout_records_abort_result_and_reraises(self, tmp_path: Path) -> None:
        """StepTimeoutError(returncode=-15) → result.json(status=ABORT,
        signal=SIGTERM)、元例外 re-raise（crash semantics 維持）。"""
        workflow = _single_step_workflow()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            raise StepTimeoutError("implement", 1800, returncode=-15)

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            pytest.raises(StepTimeoutError),
        ):
            _make_runner(tmp_path, workflow).run()

        run_dir = _run_root(tmp_path)
        result = _result_json(run_dir, "implement", "attempt-001")
        assert result["status"] == "ABORT"
        assert result["exit_code"] == -15
        assert result["signal"] == "SIGTERM"
        assert "StepTimeoutError" in result["error"]

    def test_verdict_not_found_records_abort_result_and_reraises(self, tmp_path: Path) -> None:
        """codex P2: dispatch は成功し CLI は正常 exit したが ``resolve_verdict`` が
        ``VerdictNotFound`` を raise する場合も best-effort で result.json / step_end を
        残す（従来は dispatch 例外のみ捕捉し、verdict 解決失敗は記録なしで run 停止）。

        exit_code は dispatch 成功時に result から捕捉済みの値（0）を保持し、verdict
        例外には returncode が無いため None で潰さないことを併せて検証する。
        """
        workflow = _single_step_workflow()

        # status=None → verdict block 無し。artifact / comment にも verdict が無いため
        # resolve_verdict は VerdictNotFound を raise する。exit_code=0 で正常 exit。
        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return _cli_result(None, exit_code=0)

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            pytest.raises(VerdictNotFound),
        ):
            _make_runner(tmp_path, workflow).run()

        run_dir = _run_root(tmp_path)
        result = _result_json(run_dir, "implement", "attempt-001")
        assert result["status"] == "ABORT"
        # 正常 exit の exit_code(0) を verdict 例外の returncode(None) で潰さない。
        assert result["exit_code"] == 0
        assert result["signal"] is None
        assert result["attempt"] == 1
        assert "VerdictNotFound" in result["error"]

        # step_end が verdict 解決失敗でも発火し attempt を識別できる。
        ends = [e for e in _events(run_dir, "step_end") if e["step_id"] == "implement"]
        assert len(ends) == 1
        assert ends[0]["attempt"] == 1
        assert ends[0]["exit_code"] == 0
        assert ends[0]["verdict"]["status"] == "ABORT"

        # progress.md に aborted attempt が現れる。
        progress = (tmp_path / ".kaji-artifacts" / "local-pc1-99" / "progress.md").read_text()
        assert "implement (attempt 1): ABORT" in progress
