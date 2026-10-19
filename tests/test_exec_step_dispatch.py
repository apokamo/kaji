"""Medium tests: exec step dispatch (Issue #205).

``execute_exec`` の subprocess 実行（実 ``python -c``・file I/O）と、
``WorkflowRunner`` の exec dispatch 結合を検証する。subprocess + ファイル I/O の
結合テストであり外部ネットワーク疎通は無い（testing-convention § Medium）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import ScriptExecutionError, StepTimeoutError, VerdictNotFound
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.providers.base import Comment, Issue, Label
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.runner import WorkflowRunner
from kaji_harness.script_exec import execute_exec
from kaji_harness.skill import SkillMetadata

pytestmark = pytest.mark.usefixtures("stub_github_issue")


@pytest.fixture(autouse=True)
def _offline_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """runner / triage が触る GitHubProvider の gh 呼び出しを決定的 stub にする（#448）。"""
    monkeypatch.setattr(GitHubProvider, "resolve_pr_context", lambda self, branch: None)
    monkeypatch.setattr(
        GitHubProvider,
        "comment_issue",
        lambda self, issue_id, body: Comment(
            author="",
            body=body,
            created_at="",
            ref=f"https://github.com/owner/name/issues/{issue_id}#issuecomment-1",
        ),
    )
    monkeypatch.setattr(GitHubProvider, "list_issue_comments_all", lambda self, issue_id: [])
    monkeypatch.setattr(GitHubProvider, "search_issues_all", lambda self, **kwargs: [])


def _step() -> Step:
    return Step(id="run", exec=["true"], on={"PASS": "end", "ABORT": "end"})


def _verdict(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  ok\n"
        f"evidence: |\n  ok\nsuggestion: |\n  none\n---END_VERDICT---\n"
    )


# ============================================================
# execute_exec: 実 subprocess（python -c）
# ============================================================


@pytest.mark.medium
class TestExecuteExecSubprocess:
    def test_argv_runs_and_captures_stdout(self, tmp_path: Path) -> None:
        script = "print('---VERDICT---'); print('status: PASS'); print('---END_VERDICT---')"
        log_dir = tmp_path / "log"
        result = execute_exec(
            step=_step(),
            argv=[sys.executable, "-c", script],
            env={},
            workdir=tmp_path,
            log_dir=log_dir,
            timeout=30,
            verbose=False,
        )
        assert "---VERDICT---" in result.full_output
        assert "status: PASS" in result.full_output
        assert result.session_id is None
        assert result.cost is None
        # stdout.log / console.log に保存される。
        assert "---VERDICT---" in (log_dir / "stdout.log").read_text()
        assert "status: PASS" in (log_dir / "console.log").read_text()

    def test_env_propagates_to_subprocess(self, tmp_path: Path) -> None:
        script = "import os; print(os.environ.get('KAJI_ISSUE_ID', 'MISSING'))"
        result = execute_exec(
            step=_step(),
            argv=[sys.executable, "-c", script],
            env={"KAJI_ISSUE_ID": "205-from-env"},
            workdir=tmp_path,
            log_dir=tmp_path / "log",
            timeout=30,
            verbose=False,
        )
        assert "205-from-env" in result.full_output

    def test_nonzero_exit_raises_even_with_verdict(self, tmp_path: Path) -> None:
        # stdout に verdict があっても non-zero exit は fail-loud（決定論性）。
        script = (
            "import sys; print('---VERDICT---'); print('status: PASS'); "
            "sys.stderr.write('boom'); sys.exit(3)"
        )
        with pytest.raises(ScriptExecutionError) as exc_info:
            execute_exec(
                step=_step(),
                argv=[sys.executable, "-c", script],
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=30,
                verbose=False,
            )
        assert exc_info.value.returncode == 3
        # command_label は argv の join であり exec_script 専用語を含まない。
        assert "exec_script" not in str(exc_info.value)
        assert "boom" in str(exc_info.value)

    def test_command_not_found_raises(self, tmp_path: Path) -> None:
        from kaji_harness.errors import CLINotFoundError

        with pytest.raises(CLINotFoundError):
            execute_exec(
                step=_step(),
                argv=["definitely-not-a-real-binary-xyz", "--help"],
                env={},
                workdir=tmp_path,
                log_dir=tmp_path / "log",
                timeout=30,
                verbose=False,
            )

    def test_timeout_raises_step_timeout(self, tmp_path: Path) -> None:
        # test_script_exec.py と同方針: Timer を即時発火させ StepTimeoutError を期待する。
        proc = MagicMock()
        proc.stdout = []
        proc.stderr = iter([])
        proc.wait.return_value = None
        proc.returncode = -15
        proc.terminate = MagicMock()

        with (
            patch("kaji_harness.script_exec.subprocess.Popen", return_value=proc),
            patch("kaji_harness.script_exec.threading.Timer") as timer_cls,
        ):
            timer_instance = MagicMock()
            captured: dict[str, Any] = {}

            def fake_timer(interval: float, fn: Any, *args: Any, **kwargs: Any) -> Any:
                captured["fn"] = fn
                return timer_instance

            timer_cls.side_effect = fake_timer
            timer_instance.start.side_effect = lambda: captured["fn"]()

            with pytest.raises(StepTimeoutError):
                execute_exec(
                    step=_step(),
                    argv=["sleep", "100"],
                    env={},
                    workdir=tmp_path,
                    log_dir=tmp_path / "log",
                    timeout=1,
                    verbose=False,
                )


# ============================================================
# WorkflowRunner: exec dispatch 結合
# ============================================================


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _runner(tmp_path: Path, step: Step) -> WorkflowRunner:
    workflow = Workflow(name="t", description="", execution_policy="auto", steps=[step])
    return WorkflowRunner(
        workflow=workflow,
        issue_number=99,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=_make_config(tmp_path),
    )


@pytest.mark.medium
class TestRunnerExecDispatch:
    def test_runner_dispatches_to_execute_exec(self, tmp_path: Path) -> None:
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end", "ABORT": "end"})
        runner = _runner(tmp_path, step)

        def fake_execute_exec(**kwargs: object) -> CLIResult:
            assert kwargs["argv"] == ["python", "-m", "foo"]
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["KAJI_STEP_ID"] == "run"
            assert "KAJI_ISSUE_ID" in env
            assert "KAJI_VERDICT_PATH" in env
            return CLIResult(full_output=_verdict("PASS"))

        with (
            patch("kaji_harness.runner.execute_exec", side_effect=fake_execute_exec) as mock_exec,
            patch("kaji_harness.runner.execute_cli") as mock_cli,
            patch("kaji_harness.runner.execute_script") as mock_script,
        ):
            state = runner.run()

        mock_exec.assert_called_once()
        mock_cli.assert_not_called()
        mock_script.assert_not_called()
        assert state.last_completed_step == "run"

    def test_runner_skips_ai_formatter_on_exec(self, tmp_path: Path) -> None:
        """exec 経路では VerdictNotFound 時に AI formatter を呼ばない（決定論性）。"""
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end", "ABORT": "end"})
        runner = _runner(tmp_path, step)

        with (
            patch(
                "kaji_harness.runner.execute_exec",
                return_value=CLIResult(full_output="no verdict here"),
            ),
            patch("kaji_harness.runner.create_verdict_formatter") as mock_formatter,
        ):
            with pytest.raises(VerdictNotFound):
                runner.run()

        mock_formatter.assert_not_called()

    def test_runlog_nulls_agent_fields_and_dispatch_exec(self, tmp_path: Path) -> None:
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end"})
        runner = _runner(tmp_path, step)

        with patch(
            "kaji_harness.runner.execute_exec",
            return_value=CLIResult(full_output=_verdict("PASS")),
        ):
            runner.run()

        run_logs = list((tmp_path / ".kaji-artifacts").rglob("run.log"))
        assert run_logs, "run.log was not written"
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines() if line]
        starts = [e for e in events if e["event"] == "step_start" and e["step_id"] == "run"]
        assert starts, "step_start event missing"
        ev = starts[0]
        assert ev["dispatch"] == "exec"
        assert ev["agent"] is None
        assert ev["model"] is None
        assert ev["effort"] is None

    def test_result_json_dispatch_is_exec_and_session_cost_none(self, tmp_path: Path) -> None:
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end"})
        runner = _runner(tmp_path, step)

        with patch(
            "kaji_harness.runner.execute_exec",
            return_value=CLIResult(full_output=_verdict("PASS")),
        ):
            state = runner.run()

        assert state.get_session_id("run") is None
        result_files = list((tmp_path / ".kaji-artifacts").rglob("result.json"))
        assert result_files, "result.json was not written"
        data = json.loads(result_files[0].read_text())
        assert data["dispatch"] == "exec"
        assert data["session_id"] is None

    def test_exec_artifact_verdict_takes_priority(self, tmp_path: Path) -> None:
        """exec script が KAJI_VERDICT_PATH に書いた verdict が stdout より優先される。"""
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end", "RETRY": "end"})
        runner = _runner(tmp_path, step)

        def fake_execute_exec(**kwargs: object) -> CLIResult:
            env = kwargs["env"]
            assert isinstance(env, dict)
            # artifact 経路: KAJI_VERDICT_PATH に PASS を書く。
            Path(env["KAJI_VERDICT_PATH"]).write_text(
                "status: PASS\nreason: artifact primary\nevidence: e\nsuggestion: ''\n",
                encoding="utf-8",
            )
            # stdout には乖離した RETRY を出す（無視されるべき）。
            return CLIResult(full_output=_verdict("RETRY"))

        with patch("kaji_harness.runner.execute_exec", side_effect=fake_execute_exec):
            runner.run()

        vfiles = list((tmp_path / ".kaji-artifacts").rglob("verdict.yaml"))
        assert vfiles, "verdict.yaml not written"
        text = vfiles[0].read_text()
        assert "status: PASS" in text
        assert "artifact primary" in text
        # run.log の verdict_source が artifact
        run_logs = list((tmp_path / ".kaji-artifacts").rglob("run.log"))
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines() if line]
        sources = [e for e in events if e["event"] == "verdict_source"]
        assert sources and sources[-1]["source"] == "artifact"

    def test_exec_ignores_other_step_pass_comment_and_follows_own_retry(
        self, tmp_path: Path
    ) -> None:
        """Issue #449: 前 step の PASS 作業報告コメントではなく自身の stdout RETRY で遷移する。"""
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end", "RETRY": "end"})
        runner = _runner(tmp_path, step)
        other_step_comment = MagicMock(
            body="PR 作成を完了した\n\n" + _verdict("PASS"), created_at="2099-01-01T00:00:00Z"
        )

        with (
            patch("kaji_harness.runner.execute_exec", return_value=CLIResult(_verdict("RETRY"))),
            patch.object(
                GitHubProvider,
                "view_issue",
                return_value=Issue(
                    id="99",
                    title="test issue 99",
                    body="body",
                    state="open",
                    labels=[Label(name="type:feature")],
                    comments=[other_step_comment],
                    slug="test-99",
                ),
            ) as mock_view_issue,
        ):
            runner.run()

        # IssueContext 解決の 1 回のみ。verdict 解決で Issue コメントを読みに行かない。
        assert mock_view_issue.call_count == 1
        run_logs = list((tmp_path / ".kaji-artifacts").rglob("run.log"))
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines() if line]
        sources = [e for e in events if e["event"] == "verdict_source"]
        assert sources and sources[-1]["source"] == "stdout"
        step_ends = [e for e in events if e["event"] == "step_end" and e["step_id"] == "run"]
        assert step_ends and step_ends[-1]["verdict"]["status"] == "RETRY"
        vfiles = list((tmp_path / ".kaji-artifacts").rglob("verdict.yaml"))
        assert vfiles, "verdict.yaml not written"
        assert "status: RETRY" in vfiles[0].read_text()

    def test_exec_nonzero_exit_records_abort_and_reraises(self, tmp_path: Path) -> None:
        """exec の non-zero exit は ScriptExecutionError として伝播し ABORT を記録する。"""
        step = Step(id="run", exec=["python", "-m", "foo"], on={"PASS": "end", "ABORT": "end"})
        runner = _runner(tmp_path, step)

        with patch(
            "kaji_harness.runner.execute_exec",
            side_effect=ScriptExecutionError("run", "python -m foo", 2, "boom", summary="boom"),
        ):
            with pytest.raises(ScriptExecutionError):
                runner.run()

        result_files = list((tmp_path / ".kaji-artifacts").rglob("result.json"))
        assert result_files, "result.json was not written"
        data = json.loads(result_files[0].read_text())
        assert data["status"] == "ABORT"
        assert data["dispatch"] == "exec"


@pytest.mark.medium
class TestRunnerMixedDispatch:
    """exec / exec_script / agent が混在しても後方互換に dispatch される。"""

    def test_agent_step_unaffected_by_exec_support(self, tmp_path: Path) -> None:
        step = Step(id="s", skill="plain", agent="claude", on={"PASS": "end"})
        runner = _runner(tmp_path, step)
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_cli",
                return_value=CLIResult(full_output=_verdict("PASS"), session_id="s1"),
            ) as mock_cli,
            patch("kaji_harness.runner.execute_exec") as mock_exec,
            patch("kaji_harness.runner.execute_script") as mock_script,
        ):
            state = runner.run()

        mock_cli.assert_called_once()
        mock_exec.assert_not_called()
        mock_script.assert_not_called()
        assert state.last_completed_step == "s"


# ============================================================
# Issue #516: 失敗原因行が result.json.error / run.log に残る（両経路）
# ============================================================

_CAUSE_TAIL_SCRIPT = (
    "import sys; "
    "sys.stderr.write('=' * 29 + ' test session starts ' + '=' * 30 + '\\n'); "
    "sys.stderr.write('platform linux -- Python 3.12.3, pytest-9.0.2\\n' * 5); "
    "sys.stderr.write(\"OSError: [Errno 30] Read-only file system: '/x'\\n\"); "
    "sys.exit(1)"
)
_CAUSE_SUMMARY_SUFFIX = "exited with code 1: OSError: [Errno 30] Read-only file system: '<PATH>'"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _result_json(tmp_path: Path) -> dict[str, Any]:
    result_files = list((tmp_path / ".kaji-artifacts").rglob("result.json"))
    assert result_files, "result.json was not written"
    data: dict[str, Any] = json.loads(result_files[0].read_text())
    return data


@pytest.mark.medium
class TestRunnerScriptFailureCauseLine:
    def test_exec_path_records_cause_line(self, tmp_path: Path) -> None:
        step = Step(
            id="run",
            exec=[sys.executable, "-c", _CAUSE_TAIL_SCRIPT],
            on={"PASS": "end", "ABORT": "end"},
        )
        runner = _runner(tmp_path, step)

        with pytest.raises(ScriptExecutionError) as exc_info:
            runner.run()

        assert len(exc_info.value.stderr) > 200
        data = _result_json(tmp_path)
        assert data["dispatch"] == "exec"
        assert data["error"].startswith("ScriptExecutionError: ")
        # command_label（argv の join）に script 本文が入るので、メッセージ末尾の要約だけを見る。
        assert data["error"].endswith(_CAUSE_SUMMARY_SUFFIX)

        run_logs = list((tmp_path / ".kaji-artifacts").rglob("run.log"))
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines() if line]
        ends = [e for e in events if e["event"] == "workflow_end"]
        assert ends and "Read-only file system" in ends[-1]["error"]

    def test_exec_script_path_records_cause_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # subprocess の cwd は project_root（tmp_path）なので、fixture module を解決できるよう
        # repo root を PYTHONPATH に足す。
        monkeypatch.setenv("PYTHONPATH", str(_REPO_ROOT))
        step = Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"})
        runner = _runner(tmp_path, step)
        metadata = SkillMetadata(
            name="rp",
            description="",
            exec_script="tests.fixtures.scripts.dummy_nonzero_cause_tail",
        )

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
        ):
            with pytest.raises(ScriptExecutionError) as exc_info:
                runner.run()

        assert len(exc_info.value.stderr) > 200
        data = _result_json(tmp_path)
        assert data["dispatch"] == "exec_script"
        assert data["error"].endswith(_CAUSE_SUMMARY_SUFFIX)
        assert "test session starts" not in data["error"]
