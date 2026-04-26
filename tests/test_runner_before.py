"""Tests for `kaji run --before <step>` barrier option (Issue #156).

Covers:
- CLI argument parsing for --before
- Mutual exclusion with --step
- Runner barrier hit behavior (linear / cycle / branch / unreached / --from compose)
- Validation of nonexistent step
- Logger barrier event methods
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import cmd_run, create_parser
from kaji_harness.config import KajiConfig
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.logger import RunLogger
from kaji_harness.models import CLIResult, CostInfo, CycleDefinition, Step, Verdict, Workflow
from kaji_harness.runner import WorkflowRunner

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
    d = tmp_path / "workdir"
    d.mkdir()
    config_dir = d / ".kaji"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
    )
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


def _three_step_linear_workflow() -> Workflow:
    return Workflow(
        name="linear",
        description="A → B → C",
        execution_policy="auto",
        steps=[
            Step(id="A", skill="s-a", agent="claude", on={"PASS": "B", "ABORT": "end"}),
            Step(id="B", skill="s-b", agent="claude", on={"PASS": "C", "ABORT": "end"}),
            Step(id="C", skill="s-c", agent="claude", on={"PASS": "end", "ABORT": "end"}),
        ],
    )


def _cycle_workflow_with_next() -> Workflow:
    """implement → review (RETRY → fix → verify, PASS → next-step → end)."""
    return Workflow(
        name="cycle-next",
        description="cycle then next-step",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="s-impl",
                agent="claude",
                on={"PASS": "review", "ABORT": "end"},
            ),
            Step(
                id="review",
                skill="s-rev",
                agent="codex",
                on={"PASS": "next-step", "RETRY": "fix", "ABORT": "end"},
            ),
            Step(
                id="fix",
                skill="s-fix",
                agent="claude",
                resume="implement",
                on={"PASS": "verify", "ABORT": "end"},
            ),
            Step(
                id="verify",
                skill="s-ver",
                agent="codex",
                on={"PASS": "next-step", "RETRY": "fix"},
            ),
            Step(
                id="next-step",
                skill="s-next",
                agent="claude",
                on={"PASS": "end", "ABORT": "end"},
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


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    config_file = kaji_dir / "config.toml"
    if not config_file.exists():
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
        )
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


# ============================================================
# Small: argparse parses --before
# ============================================================


class TestBeforeParsingSmall:
    @pytest.mark.small
    def test_before_arg_parsed(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "wf.yaml", "1", "--before", "implement"])
        assert args.before_step == "implement"

    @pytest.mark.small
    def test_before_default_none(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "wf.yaml", "1"])
        assert args.before_step is None


# ============================================================
# Small: --step / --before mutual exclusion
# ============================================================


class TestStepBeforeMutualExclusionSmall:
    @pytest.mark.small
    def test_step_and_before_exclusive(self, workflow_file: Path, workdir: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--step",
            "step1",
            "--before",
            "step1",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2

    @pytest.mark.small
    def test_from_and_before_compatible(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--from",
                "step1",
                "--before",
                "end",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0
        # Verify before_step was passed to WorkflowRunner
        call_kwargs = mock_runner.call_args.kwargs
        assert call_kwargs.get("before_step") == "end"
        assert call_kwargs.get("from_step") == "step1"

    @pytest.mark.small
    def test_before_alone_passed_to_runner(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            cmd_run_with_args(
                str(workflow_file),
                "1",
                "--before",
                "step1",
                "--workdir",
                str(workdir),
            )
            assert mock_runner.call_args.kwargs.get("before_step") == "step1"


# ============================================================
# Small: Logger barrier methods
# ============================================================


class TestLoggerBarrierSmall:
    @pytest.mark.small
    def test_log_barrier_hit_writes_event(self, tmp_path: Path) -> None:
        logger = RunLogger(log_path=tmp_path / "run.log")
        logger.log_barrier_hit("implement")
        content = (tmp_path / "run.log").read_text()
        assert '"event": "barrier_hit"' in content
        assert '"before_step": "implement"' in content

    @pytest.mark.small
    def test_log_barrier_missed_writes_event(self, tmp_path: Path) -> None:
        logger = RunLogger(log_path=tmp_path / "run.log")
        logger.log_barrier_missed("implement")
        content = (tmp_path / "run.log").read_text()
        assert '"event": "barrier_missed"' in content
        assert '"before_step": "implement"' in content


# ============================================================
# Small/Medium: Runner barrier behavior
# ============================================================


@pytest.mark.medium
class TestRunnerBarrier:
    def test_linear_before_C_executes_A_B_only(self, tmp_path: Path) -> None:
        """A → B → C with --before C executes A and B, stops before C."""
        workflow = _three_step_linear_workflow()
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, before_step="C")
            state = runner.run()

        assert called == ["A", "B"]
        assert len(state.step_history) == 2
        assert state.step_history[-1].step_id == "B"

    def test_before_end_equivalent_to_default(self, tmp_path: Path) -> None:
        """--before end is allowed and equivalent to default (full run)."""
        workflow = _three_step_linear_workflow()
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, before_step="end")
            runner.run()

        assert called == ["A", "B", "C"]

    def test_branch_before_unreached_completes_normally(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Branch path that never reaches barrier: WARN to stderr, normal completion."""
        # C exists in workflow but is only reached via ABORT; PASS path skips it.
        workflow = Workflow(
            name="branch",
            description="branch test",
            execution_policy="auto",
            steps=[
                Step(id="A", skill="s-a", agent="claude", on={"PASS": "B", "ABORT": "C"}),
                Step(id="B", skill="s-b", agent="claude", on={"PASS": "end", "ABORT": "end"}),
                Step(id="C", skill="s-c", agent="claude", on={"PASS": "end", "ABORT": "end"}),
            ],
        )
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, before_step="C")
            state = runner.run()

        # PASS path A → B → end, never hits C
        assert called == ["A", "B"]
        assert len(state.step_history) == 2
        captured = capsys.readouterr()
        assert "stop point 'C' was never reached" in captured.err

    def test_cycle_loops_until_pass_then_barrier_hits(self, tmp_path: Path) -> None:
        """Cycle (review ⇄ fix/verify) loops on RETRY, barrier hits before next-step."""
        workflow = _cycle_workflow_with_next()
        # implement PASS → review RETRY → fix PASS → verify RETRY → fix PASS → verify PASS → next-step
        results = [
            _make_cli_result("PASS"),  # implement
            _make_cli_result("RETRY"),  # review → fix
            _make_cli_result("PASS"),  # fix → verify
            _make_cli_result("RETRY"),  # verify → fix
            _make_cli_result("PASS"),  # fix → verify
            _make_cli_result("PASS"),  # verify → next-step (BARRIER HIT)
        ]
        idx = 0
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            nonlocal idx
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            r = results[idx]
            idx += 1
            return r

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, before_step="next-step")
            state = runner.run()

        # next-step should NOT be in called list
        assert "next-step" not in called
        assert called == ["implement", "review", "fix", "verify", "fix", "verify"]
        assert state.step_history[-1].step_id == "verify"

    def test_from_compose_with_before(self, tmp_path: Path) -> None:
        """--from B --before C runs only B."""
        workflow = _three_step_linear_workflow()
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="B", before_step="C")
            runner.run()

        assert called == ["B"]

    def test_before_start_step_stops_before_dispatch(self, tmp_path: Path) -> None:
        """--before で start step を指定 → そのステップは実行されず即停止。"""
        workflow = _three_step_linear_workflow()
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, before_step="A")
            state = runner.run()

        assert called == []
        assert len(state.step_history) == 0

    def test_before_equals_from_step_stops_before_dispatch(self, tmp_path: Path) -> None:
        """--from B --before B → B は実行されず即停止（barrier が --from の開始 step も止める）。"""
        workflow = _three_step_linear_workflow()
        called: list[str] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            step = kwargs["step"]
            called.append(step.id)  # type: ignore[union-attr]
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = _make_runner(tmp_path, workflow, from_step="B", before_step="B")
            state = runner.run()

        assert called == []
        assert len(state.step_history) == 0

    def test_before_nonexistent_raises_validation_error(self, tmp_path: Path) -> None:
        """--before with unknown step → WorkflowValidationError at startup."""
        workflow = _three_step_linear_workflow()

        with patch("kaji_harness.runner.validate_skill_exists"):
            runner = _make_runner(tmp_path, workflow, before_step="nonexistent")
            with pytest.raises(WorkflowValidationError):
                runner.run()
