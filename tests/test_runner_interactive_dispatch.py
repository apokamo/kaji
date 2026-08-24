"""Medium tests: WorkflowRunner runner-backend dispatch (Issue #224 / #230).

Verifies that ``config.execution.agent_runner`` routes the agent step to either
``execute_interactive_terminal`` (tmux/Herdr path) or ``execute_cli`` (headless),
without changing the existing headless behavior, and that both backends receive
the same ``effective_workdir`` (Issue #230 MF3 regression guard).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import (
    CLIExecutionError,
    HerdrSessionRequiredError,
    SessionResolution,
    StepTimeoutError,
    TmuxSessionRequiredError,
)
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import SkillMetadata

_PASS_YAML = "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n"


def _make_config(tmp_path: Path, *, execution_extra: str = "") -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f"[execution]\ndefault_timeout = 60\n{execution_extra}\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _make_runner(
    config: KajiConfig, tmp_path: Path, *, artifacts_dir: Path | None = None
) -> WorkflowRunner:
    workflow = Workflow(
        name="t",
        description="",
        execution_policy="auto",
        steps=[Step(id="design", skill="plain", agent="claude", on={"PASS": "end"})],
    )
    return WorkflowRunner(
        workflow=workflow,
        issue_number=99,
        project_root=tmp_path,
        artifacts_dir=artifacts_dir or (tmp_path / ".kaji-artifacts"),
        config=config,
    )


@pytest.mark.medium
class TestRunnerBackendDispatch:
    def test_interactive_terminal_config_routes_to_interactive_runner(self, tmp_path: Path) -> None:
        config = _make_config(
            tmp_path,
            execution_extra=(
                'agent_runner = "interactive_terminal"\n'
                "interactive_terminal_close_on_verdict = false"
            ),
        )
        runner = _make_runner(config, tmp_path)
        captured: dict[str, Any] = {}

        def fake_interactive(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            kwargs["verdict_path"].write_text(_PASS_YAML, encoding="utf-8")
            return CLIResult(full_output="", session_id="sess-it")

        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive
            ) as mock_it,
            patch("kaji_harness.runner.execute_cli") as mock_cli,
        ):
            state = runner.run()

        mock_it.assert_called_once()
        mock_cli.assert_not_called()
        assert state.last_completed_step == "design"
        # close_on_verdict flag is threaded from config into the runner call.
        assert captured["close_on_verdict"] is False
        assert captured["backend"] == "tmux"
        assert captured["execution_policy"] == "auto"
        assert captured["prompt_path"].name == "prompt.txt"
        assert captured["verdict_path"].name == "verdict.yaml"

    def test_herdr_backend_is_threaded_to_interactive_runner(self, tmp_path: Path) -> None:
        config = _make_config(
            tmp_path,
            execution_extra=(
                'agent_runner = "interactive_terminal"\ninteractive_terminal_backend = "herdr"'
            ),
        )
        runner = _make_runner(config, tmp_path)
        captured: dict[str, Any] = {}

        def fake_interactive(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            kwargs["verdict_path"].write_text(_PASS_YAML, encoding="utf-8")
            return CLIResult(full_output="", session_id="sess-herdr")

        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch("kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive),
            patch("kaji_harness.runner.execute_cli"),
        ):
            state = runner.run()

        assert state.last_completed_step == "design"
        assert captured["backend"] == "herdr"

    def test_tmux_session_required_type_name_reaches_run_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #322: 送出した例外型名が run.log の failure_event に載ることを固定する。

        recovery classifier は live class ではなく artifact 上の型名文字列で判定する。
        「送出した型名が artifact に正しく載る」ことを押さえないと、classify 側の Small
        テストが実運用と乖離しうる。
        """
        config = _make_config(tmp_path, execution_extra='agent_runner = "interactive_terminal"')
        artifacts_dir = tmp_path / "art-tmux"
        runner = _make_runner(config, tmp_path, artifacts_dir=artifacts_dir)
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TMUX_PANE", raising=False)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
        ):
            with pytest.raises(TmuxSessionRequiredError):
                runner.run()

        run_logs = sorted(artifacts_dir.glob("*/runs/*/run.log"))
        assert len(run_logs) == 1
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines()]
        failure = [e for e in events if e["event"] == "failure_event"]
        assert len(failure) == 1
        assert failure[0]["kind"] == "dispatch_exception"
        assert failure[0]["exception_type"] == "TmuxSessionRequiredError"

    def test_herdr_session_required_type_name_reaches_run_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _make_config(
            tmp_path,
            execution_extra=(
                'agent_runner = "interactive_terminal"\ninteractive_terminal_backend = "herdr"'
            ),
        )
        artifacts_dir = tmp_path / "art-herdr"
        runner = _make_runner(config, tmp_path, artifacts_dir=artifacts_dir)
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)
        monkeypatch.delenv("HERDR_ENV", raising=False)
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.interactive_terminal_herdr.shutil.which",
                return_value="/usr/bin/herdr",
            ),
        ):
            with pytest.raises(HerdrSessionRequiredError):
                runner.run()

        run_logs = sorted(artifacts_dir.glob("*/runs/*/run.log"))
        assert len(run_logs) == 1
        events = [json.loads(line) for line in run_logs[0].read_text().splitlines()]
        failure = [event for event in events if event["event"] == "failure_event"]
        assert len(failure) == 1
        assert failure[0]["kind"] == "dispatch_exception"
        assert failure[0]["exception_type"] == "HerdrSessionRequiredError"

    def test_headless_config_routes_to_execute_cli(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)  # default agent_runner = headless
        runner = _make_runner(config, tmp_path)
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch(
                "kaji_harness.runner.execute_cli",
                return_value=CLIResult(
                    full_output=(
                        "---VERDICT---\nstatus: PASS\nreason: |\n  ok\nevidence: |\n  ok\n"
                        "suggestion: |\n  none\n---END_VERDICT---\n"
                    ),
                    session_id="s1",
                ),
            ) as mock_cli,
            patch("kaji_harness.runner.execute_interactive_terminal") as mock_it,
        ):
            state = runner.run()

        mock_cli.assert_called_once()
        mock_it.assert_not_called()
        assert state.last_completed_step == "design"

    def test_both_backends_receive_identical_effective_workdir(self, tmp_path: Path) -> None:
        """MF3 regression guard: backend choice must not change workdir resolution.

        The agent step's ``effective_workdir`` is resolved backend-independently
        (``step.workdir`` → ``workflow.workdir`` → ``project_root``). This pins
        that the interactive_terminal branch passes the exact same value the
        headless ``execute_cli`` branch would, so switching backends never
        relocates where the agent runs (and thus where artifacts land).
        """
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        # interactive_terminal branch (separate artifacts dir to isolate state).
        it_config = _make_config(tmp_path, execution_extra='agent_runner = "interactive_terminal"')
        it_runner = _make_runner(it_config, tmp_path, artifacts_dir=tmp_path / "art-it")
        it_captured: dict[str, Any] = {}

        def fake_interactive(**kwargs: Any) -> CLIResult:
            it_captured.update(kwargs)
            kwargs["verdict_path"].write_text(_PASS_YAML, encoding="utf-8")
            return CLIResult(full_output="", session_id="sess-it")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch("kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive),
            patch("kaji_harness.runner.execute_cli"),
        ):
            it_runner.run()

        # headless branch (same project_root → same effective_workdir).
        hl_config = _make_config(tmp_path)  # default agent_runner = headless
        hl_runner = _make_runner(hl_config, tmp_path, artifacts_dir=tmp_path / "art-hl")
        hl_captured: dict[str, Any] = {}

        def fake_cli(**kwargs: Any) -> CLIResult:
            hl_captured.update(kwargs)
            return CLIResult(
                full_output=(
                    "---VERDICT---\nstatus: PASS\nreason: |\n  ok\nevidence: |\n  ok\n"
                    "suggestion: |\n  none\n---END_VERDICT---\n"
                ),
                session_id="s1",
            )

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch("kaji_harness.runner.execute_cli", side_effect=fake_cli),
            patch("kaji_harness.runner.execute_interactive_terminal"),
        ):
            hl_runner.run()

        assert it_captured["workdir"] == hl_captured["workdir"] == tmp_path


@pytest.mark.medium
class TestAbnormalExitSessionRecording:
    """Issue #403: 異常終了経路で確定した session ID の ``result.json`` への写し取り。"""

    def _resume_workflow(self) -> Workflow:
        return Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="design", skill="plain", agent="codex", on={"PASS": "review"}),
                Step(
                    id="review",
                    skill="plain",
                    agent="codex",
                    resume="design",
                    on={"PASS": "end"},
                ),
            ],
        )

    def _run_until_review_failure(
        self, tmp_path: Path, artifacts_dir: Path, failure: Exception
    ) -> dict[str, Any]:
        """design を成功させ session を state に残した上で、review を ``failure`` で落とす。"""
        config = _make_config(tmp_path, execution_extra='agent_runner = "interactive_terminal"')
        runner = WorkflowRunner(
            workflow=self._resume_workflow(),
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=artifacts_dir,
            config=config,
        )
        plain_meta = SkillMetadata(name="plain", description="", exec_script=None)

        def fake_interactive(**kwargs: Any) -> CLIResult:
            if kwargs["step"].id == "design":
                kwargs["verdict_path"].write_text(_PASS_YAML, encoding="utf-8")
                return CLIResult(full_output="", session_id="parent-sess")
            raise failure

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=plain_meta),
            patch("kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive),
            pytest.raises(type(failure)),
        ):
            runner.run()

        results = sorted(artifacts_dir.glob("*/runs/*/steps/review/attempt-*/result.json"))
        assert len(results) == 1
        data = json.loads(results[0].read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data

    def test_resolved_session_id_is_written_to_result_json(self, tmp_path: Path) -> None:
        failure = StepTimeoutError(
            "review", 600, session_resolution=SessionResolution("new-rollout-uuid")
        )
        result = self._run_until_review_failure(tmp_path, tmp_path / "art-a", failure)

        assert result["session_id"] == "new-rollout-uuid"

    def test_resolved_none_suppresses_resume_input_fallback(self, tmp_path: Path) -> None:
        # codex の `resume:` step が異常終了しても、親 session ID を推測で書かない。
        failure = StepTimeoutError("review", 600, session_resolution=SessionResolution(None))
        result = self._run_until_review_failure(tmp_path, tmp_path / "art-b", failure)

        assert result["session_id"] is None

    def test_untried_resolution_keeps_existing_resume_fallback(self, tmp_path: Path) -> None:
        # 解決を試みない経路（headless / exec）では既存 fallback が維持される。
        failure = StepTimeoutError("review", 600)
        result = self._run_until_review_failure(tmp_path, tmp_path / "art-c", failure)

        assert result["session_id"] == "parent-sess"

    def test_pane_dead_resolution_is_written_to_result_json(self, tmp_path: Path) -> None:
        failure = CLIExecutionError(
            "review", 1, "agent exited", session_resolution=SessionResolution(None)
        )
        result = self._run_until_review_failure(tmp_path, tmp_path / "art-d", failure)

        assert result["session_id"] is None
