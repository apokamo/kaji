"""Tests for `kaji run --reset-cycle` (Issue #189).

Covers:
- CLI argument parsing for --reset-cycle (default False)
- `--help` output mentions --reset-cycle
- --from dependency guard (cmd_run level, before config discovery; and
  runner level, for direct WorkflowRunner usage)
- SessionState.reset_cycle() pure logic
- Runner behavior: exhaust → ABORT (regression control), reset → continues
- Misuse detection: cycle-external step, nonexistent step
- validate/apply separation: misuse leaves session-state.json untouched,
  even for old-format state files (no worktree_dir/branch_name)
- log_cycle_reset() event emission
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import cmd_run, create_parser
from kaji_harness.config import KajiConfig
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.logger import RunLogger
from kaji_harness.models import CLIResult, CostInfo, CycleDefinition, Step, Verdict, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.state import STATE_FILE, SessionState

# ============================================================
# Fixtures / helpers
# ============================================================

MINIMAL_WORKFLOW_YAML = """\
name: test
description: test workflow
execution_policy: auto
steps:
  - id: step1
    skill: test-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


@pytest.fixture()
def workflow_file(tmp_path: Path) -> Path:
    p = tmp_path / "workflow.yaml"
    p.write_text(MINIMAL_WORKFLOW_YAML)
    return p


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    import subprocess as _sp

    d = tmp_path / "workdir"
    d.mkdir()
    config_dir = d / ".kaji"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        '[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    # gl:21: provider.type='local' requires a git repo.
    _sp.run(["git", "init", "-q", "--initial-branch=main", str(d)], check=True)
    return d


def cmd_run_with_args(*args: str) -> int:
    parser = create_parser()
    parsed = parser.parse_args(["run", *args])
    return cmd_run(parsed)


def _make_verdict_output(status: str) -> str:
    suggestion = "fix it" if status in ("ABORT", "BACK") else ""
    return (
        "Some output text here.\n\n"
        "---VERDICT---\n"
        f"status: {status}\n"
        'reason: "ok"\n'
        'evidence: "test"\n'
        f'suggestion: "{suggestion}"\n'
        "---END_VERDICT---\n"
    )


def _make_cli_result(status: str, session_id: str = "sess-001") -> CLIResult:
    return CLIResult(
        full_output=_make_verdict_output(status),
        session_id=session_id,
        cost=CostInfo(usd=0.01),
        stderr="",
    )


def _cycle_workflow() -> Workflow:
    """review ⇄ fix/verify (RETRY loop), max_iterations=3, on_exhaust=ABORT."""
    return Workflow(
        name="cycle",
        description="review/fix/verify cycle",
        execution_policy="auto",
        steps=[
            Step(
                id="review",
                skill="s-rev",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix", "ABORT": "end"},
            ),
            Step(
                id="fix",
                skill="s-fix",
                agent="claude",
                on={"PASS": "verify", "ABORT": "end"},
            ),
            Step(
                id="verify",
                skill="s-ver",
                agent="codex",
                on={"PASS": "end", "RETRY": "fix", "ABORT": "end"},
            ),
        ],
        cycles=[
            CycleDefinition(
                name="rev",
                entry="review",
                loop=["fix", "verify"],
                max_iterations=3,
                on_exhaust="ABORT",
            ),
        ],
    )


def _linear_workflow() -> Workflow:
    return Workflow(
        name="linear",
        description="A → B",
        execution_policy="auto",
        steps=[
            Step(id="A", skill="s-a", agent="claude", on={"PASS": "B", "ABORT": "end"}),
            Step(id="B", skill="s-b", agent="claude", on={"PASS": "end", "ABORT": "end"}),
        ],
    )


def _make_config(tmp_path: Path) -> KajiConfig:
    import subprocess as _sp

    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    config_file = kaji_dir / "config.toml"
    if not config_file.exists():
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
            '[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n'
            '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )
    # gl:21: provider.type='local' requires a git repo.
    if not (tmp_path / ".git").exists():
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(config_file)


def _make_runner(
    tmp_path: Path,
    workflow: Workflow,
    issue: int = 99,
    **kwargs: object,
) -> WorkflowRunner:
    return WorkflowRunner(
        workflow=workflow,
        issue_number=issue,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=_make_config(tmp_path),
        **kwargs,  # type: ignore[arg-type]
    )


def _write_state(
    artifacts_dir: Path,
    canonical_id: str,
    *,
    cycle_counts: dict[str, int],
    old_format: bool = False,
) -> Path:
    """session-state.json を直接書き込む（旧形式は worktree_dir/branch_name を省く）。"""
    state_dir = artifacts_dir / canonical_id
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / STATE_FILE
    data: dict[str, object] = {
        "issue_number": canonical_id,
        "sessions": {},
        "step_history": [],
        "cycle_counts": cycle_counts,
        "last_completed_step": None,
        "last_transition_verdict": None,
    }
    if not old_format:
        data["worktree_dir"] = None
        data["branch_name"] = None
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ============================================================
# Small: argparse parses --reset-cycle
# ============================================================


class TestResetCycleParsingSmall:
    @pytest.mark.small
    def test_reset_cycle_arg_parsed(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "wf.yaml", "1", "--from", "step1", "--reset-cycle"])
        assert args.reset_cycle is True

    @pytest.mark.small
    def test_reset_cycle_default_false(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "wf.yaml", "1"])
        assert args.reset_cycle is False

    @pytest.mark.small
    def test_help_mentions_reset_cycle(self) -> None:
        parser = create_parser()
        run_parser = parser._subparsers._group_actions[0].choices["run"]  # type: ignore[union-attr]
        help_text = run_parser.format_help()
        assert "--reset-cycle" in help_text


# ============================================================
# Small: --reset-cycle requires --from (cmd_run level)
# ============================================================


class TestResetCycleRequiresFromSmall:
    @pytest.mark.small
    def test_reset_cycle_without_from_errors(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--reset-cycle",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "--reset-cycle requires --from" in captured.err

    @pytest.mark.small
    def test_reset_cycle_with_step_only_errors(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--step",
            "step1",
            "--reset-cycle",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "--reset-cycle requires --from" in captured.err

    @pytest.mark.small
    def test_reset_cycle_guard_precedes_config_discovery(
        self, workflow_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--workdir が不正でも --reset-cycle 依存ガードが先に発火する。"""
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--reset-cycle",
            "--workdir",
            "/nonexistent/path/for/kaji/test",
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "--reset-cycle requires --from" in captured.err

    @pytest.mark.small
    def test_reset_cycle_with_from_passed_to_runner(
        self, workflow_file: Path, workdir: Path
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--from",
                "step1",
                "--reset-cycle",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0
        assert mock_runner.call_args.kwargs.get("reset_cycle") is True


# ============================================================
# Small: SessionState.reset_cycle()
# ============================================================


class TestSessionStateResetCycleSmall:
    @pytest.mark.small
    def test_reset_cycle_resets_to_zero(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("1", tmp_path)
        state.cycle_counts["rev"] = 3
        state.reset_cycle("rev")
        assert state.cycle_iterations("rev") == 0

    @pytest.mark.small
    def test_reset_cycle_idempotent(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("1", tmp_path)
        state.cycle_counts["rev"] = 3
        state.reset_cycle("rev")
        state.reset_cycle("rev")
        assert state.cycle_iterations("rev") == 0

    @pytest.mark.small
    def test_reset_cycle_leaves_other_cycles(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("1", tmp_path)
        state.cycle_counts["rev"] = 3
        state.cycle_counts["other"] = 2
        state.reset_cycle("rev")
        assert state.cycle_iterations("rev") == 0
        assert state.cycle_iterations("other") == 2

    @pytest.mark.small
    def test_reset_cycle_unknown_name_no_exception(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("1", tmp_path)
        state.reset_cycle("does-not-exist")
        assert state.cycle_iterations("does-not-exist") == 0

    @pytest.mark.small
    def test_reset_cycle_persists(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("1", tmp_path)
        state.cycle_counts["rev"] = 3
        state.reset_cycle("rev")
        reloaded = SessionState.load_or_create("1", tmp_path)
        assert reloaded.cycle_iterations("rev") == 0


# ============================================================
# Small: RunLogger.log_cycle_reset()
# ============================================================


class TestLoggerCycleResetSmall:
    @pytest.mark.small
    def test_log_cycle_reset_writes_event(self, tmp_path: Path) -> None:
        logger = RunLogger(log_path=tmp_path / "run.log")
        logger.log_cycle_reset("rev", 3)
        content = (tmp_path / "run.log").read_text()
        assert '"event": "cycle_reset"' in content
        assert '"cycle_name": "rev"' in content
        assert '"previous_iterations": 3' in content
        assert '"new_iterations": 0' in content


# ============================================================
# Medium: Runner behavior
# ============================================================


@pytest.mark.medium
class TestRunnerResetCycle:
    def test_exhausted_cycle_without_reset_aborts(self, tmp_path: Path) -> None:
        """回帰の再現（対照群）: --reset-cycle なしでは即 ABORT する。"""
        workflow = _cycle_workflow()
        artifacts_dir = tmp_path / ".kaji-artifacts"
        _write_state(artifacts_dir, "local-pc1-99", cycle_counts={"rev": 3})

        with (
            patch("kaji_harness.runner.execute_cli") as mock_execute,
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="review")
            state = runner.run()

        mock_execute.assert_not_called()
        assert state.last_transition_verdict is not None
        assert state.last_transition_verdict.status == "ABORT"
        assert "Cycle 'rev' exhausted" in state.last_transition_verdict.reason

    def test_exhausted_cycle_with_reset_continues(self, tmp_path: Path) -> None:
        """実験群: --reset-cycle 併用で ABORT せず step が dispatch される。"""
        workflow = _cycle_workflow()
        artifacts_dir = tmp_path / ".kaji-artifacts"
        _write_state(artifacts_dir, "local-pc1-99", cycle_counts={"rev": 3})

        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="review", reset_cycle=True)
            state = runner.run()

        assert called == ["review"]
        assert state.last_transition_verdict is not None
        assert state.last_transition_verdict.status == "PASS"

    def test_reset_cycle_state_postcondition_other_cycle_preserved(self, tmp_path: Path) -> None:
        workflow = _cycle_workflow()
        artifacts_dir = tmp_path / ".kaji-artifacts"
        _write_state(artifacts_dir, "local-pc1-99", cycle_counts={"rev": 3, "other": 2})

        with (
            patch("kaji_harness.runner.execute_cli", return_value=_make_cli_result("PASS")),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="review", reset_cycle=True)
            state = runner.run()

        assert state.cycle_iterations("other") == 2

    def test_cycle_external_step_misuse_raises(self, tmp_path: Path) -> None:
        """linear step（cycle 外）を --from に与えると誤用エラー。"""
        workflow = _linear_workflow()

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = _make_runner(tmp_path, workflow, from_step="A", reset_cycle=True)
            with pytest.raises(WorkflowValidationError, match="does not belong to any cycle"):
                runner.run()

    def test_misuse_does_not_mutate_old_format_state(self, tmp_path: Path) -> None:
        """validate/apply 分離の回帰テスト: 旧形式 state を入力にした誤用で state が不変。"""
        workflow = _linear_workflow()
        artifacts_dir = tmp_path / ".kaji-artifacts"
        state_path = _write_state(artifacts_dir, "local-pc1-99", cycle_counts={}, old_format=True)
        before_mtime = state_path.stat().st_mtime_ns
        before_content = state_path.read_text()

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = _make_runner(tmp_path, workflow, from_step="A", reset_cycle=True)
            with pytest.raises(WorkflowValidationError):
                runner.run()

        after_mtime = state_path.stat().st_mtime_ns
        after_content = state_path.read_text()
        assert before_mtime == after_mtime
        assert before_content == after_content

    def test_from_step_missing_at_runner_level_raises(self, tmp_path: Path) -> None:
        """WorkflowRunner 直接利用時、--from なしで reset_cycle=True は例外。"""
        workflow = _cycle_workflow()

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = _make_runner(tmp_path, workflow, reset_cycle=True)
            with pytest.raises(WorkflowValidationError, match="requires --from"):
                runner.run()

    def test_nonexistent_step_raises(self, tmp_path: Path) -> None:
        workflow = _cycle_workflow()

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = _make_runner(tmp_path, workflow, from_step="bogus", reset_cycle=True)
            with pytest.raises(WorkflowValidationError, match="Step 'bogus' not found"):
                runner.run()

    def test_run_log_records_cycle_reset_event(self, tmp_path: Path) -> None:
        workflow = _cycle_workflow()
        artifacts_dir = tmp_path / ".kaji-artifacts"
        _write_state(artifacts_dir, "local-pc1-99", cycle_counts={"rev": 3})

        with (
            patch("kaji_harness.runner.execute_cli", return_value=_make_cli_result("PASS")),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="review", reset_cycle=True)
            runner.run()

        run_dirs = list((artifacts_dir / "local-pc1-99" / "runs").iterdir())
        assert len(run_dirs) == 1
        log_content = (run_dirs[0] / "run.log").read_text()
        assert '"event": "cycle_reset"' in log_content
        assert '"cycle_name": "rev"' in log_content
        assert '"previous_iterations": 3' in log_content


# ============================================================
# Medium/E2E: full cmd_run() drive (exhaust → reset → continue)
# ============================================================


@pytest.mark.medium
class TestCmdRunResetCycleE2E:
    def test_cmd_run_from_reset_cycle_continues_past_exhaust(self, workdir: Path) -> None:
        cycle_wf = workdir / "cycle.yaml"
        cycle_wf.write_text(
            "name: cycle\n"
            "description: review/fix/verify cycle\n"
            "execution_policy: auto\n"
            "steps:\n"
            "  - id: review\n"
            "    skill: s-rev\n"
            "    agent: codex\n"
            "    on:\n"
            "      PASS: end\n"
            "      RETRY: fix\n"
            "      ABORT: end\n"
            "  - id: fix\n"
            "    skill: s-fix\n"
            "    agent: claude\n"
            "    on:\n"
            "      PASS: verify\n"
            "      ABORT: end\n"
            "  - id: verify\n"
            "    skill: s-ver\n"
            "    agent: codex\n"
            "    on:\n"
            "      PASS: end\n"
            "      RETRY: fix\n"
            "      ABORT: end\n"
            "cycles:\n"
            "  rev:\n"
            "    entry: review\n"
            "    loop: [fix, verify]\n"
            "    max_iterations: 3\n"
            "    on_exhaust: ABORT\n"
        )
        # config.toml (workdir fixture) declares paths.artifacts_dir = ".kaji/artifacts"
        artifacts_dir = workdir / ".kaji" / "artifacts"
        _write_state(artifacts_dir, "local-pc1-1", cycle_counts={"rev": 3})

        with (
            patch("kaji_harness.runner.execute_cli", return_value=_make_cli_result("PASS")),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            exit_code = cmd_run_with_args(
                str(cycle_wf),
                "1",
                "--from",
                "review",
                "--reset-cycle",
                "--workdir",
                str(workdir),
            )

        assert exit_code == 0
        state = SessionState.load_or_create("local-pc1-1", artifacts_dir)
        assert state.last_transition_verdict is not None
        assert state.last_transition_verdict.status == "PASS"
