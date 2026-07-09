"""Medium tests: runner が emit する ``failure_event`` と ``AttemptResult.synthetic`` (Issue #288).

emit 箇所 4 / kind 5 種（dispatch 例外 / verdict 例外 / cycle exhaust /
ambiguous worktree / agent ABORT）が ``run.log`` に構造化記録されること、
``result.json`` の ``synthetic`` が except 経路で true・agent ABORT で false に
なること、``recovery-chain.json`` が ``--recovery-*`` 付き run でのみ書かれることを
tmp fs + stub dispatch で検証する。
"""

from __future__ import annotations

import json
import subprocess as _sp
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import StepTimeoutError, VerdictNotFound
from kaji_harness.models import CLIResult, CostInfo, CycleDefinition, Step, Workflow
from kaji_harness.result import AttemptResult
from kaji_harness.runner import WorkflowRunner
from kaji_harness.worktree_discovery import AmbiguousWorktreeError

pytestmark = pytest.mark.medium

_CANONICAL = "local-pc1-99"


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    config_file = kaji_dir / "config.toml"
    config_file.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(config_file)


def _verdict_block(status: str) -> str:
    return (
        "---VERDICT---\n"
        f"status: {status}\n"
        'reason: "r"\n'
        'evidence: "e"\n'
        'suggestion: "s"\n'
        "---END_VERDICT---\n"
    )


def _cli_result(output: str) -> CLIResult:
    return CLIResult(full_output=output, session_id="sess", cost=CostInfo(usd=0.0), exit_code=0)


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
            )
        ],
    )


def _cycle_workflow() -> Workflow:
    return Workflow(
        name="cyc",
        description="cycle",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="issue-implement",
                agent="claude",
                on={"PASS": "end", "RETRY": "implement", "ABORT": "end"},
            )
        ],
        cycles=[
            CycleDefinition(
                name="impl",
                entry="implement",
                loop=["implement"],
                max_iterations=1,
                on_exhaust="ABORT",
            )
        ],
    )


def _make_runner(tmp_path: Path, workflow: Workflow, **kwargs: object) -> WorkflowRunner:
    return WorkflowRunner(
        workflow=workflow,
        issue_number="99",
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=_make_config(tmp_path),
        **kwargs,  # type: ignore[arg-type]
    )


def _run_dir(tmp_path: Path) -> Path:
    runs = tmp_path / ".kaji-artifacts" / _CANONICAL / "runs"
    dirs = sorted(p for p in runs.iterdir() if p.is_dir())
    assert dirs, "no run dir created"
    return dirs[-1]


def _events(run_dir: Path, event: str) -> list[dict[str, object]]:
    lines = (run_dir / "run.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if json.loads(line).get("event") == event]


def _result_json(run_dir: Path, step_id: str, attempt: str = "attempt-001") -> dict[str, object]:
    path = run_dir / "steps" / step_id / attempt / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_state(tmp_path: Path, cycle_counts: dict[str, int]) -> None:
    state_dir = tmp_path / ".kaji-artifacts" / _CANONICAL
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session-state.json").write_text(
        json.dumps(
            {
                "issue_number": _CANONICAL,
                "sessions": {},
                "step_history": [],
                "cycle_counts": cycle_counts,
                "last_completed_step": None,
                "last_transition_verdict": None,
                "worktree_dir": str(tmp_path),
                "branch_name": "feat/99",
            }
        ),
        encoding="utf-8",
    )


# --- kind 5 種の emit ---


def test_dispatch_exception_event_and_synthetic_result(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", side_effect=StepTimeoutError("implement", 5)),
        patch("kaji_harness.runner.validate_skill_exists"),
        pytest.raises(StepTimeoutError),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    events = _events(run_dir, "failure_event")
    assert len(events) == 1
    assert events[0]["kind"] == "dispatch_exception"
    assert events[0]["exception_type"] == "StepTimeoutError"
    assert events[0]["step_id"] == "implement"
    assert events[0]["synthetic"] is True
    assert _result_json(run_dir, "implement")["synthetic"] is True


def test_verdict_exception_event(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result("no verdict here")),
        patch("kaji_harness.runner.validate_skill_exists"),
        # AI formatter fallback は外部 CLI を起動するため無効化する。
        patch("kaji_harness.runner.create_verdict_formatter", return_value=None),
        pytest.raises(VerdictNotFound),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    events = _events(run_dir, "failure_event")
    assert len(events) == 1
    assert events[0]["kind"] == "verdict_exception"
    assert events[0]["exception_type"] == "VerdictNotFound"
    assert events[0]["synthetic"] is True
    assert _result_json(run_dir, "implement")["synthetic"] is True


def test_agent_abort_event_is_not_synthetic(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result(_verdict_block("ABORT"))),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    events = _events(run_dir, "failure_event")
    assert len(events) == 1
    assert events[0]["kind"] == "agent_abort"
    assert events[0]["step_id"] == "implement"
    assert events[0]["synthetic"] is False
    assert _result_json(run_dir, "implement")["synthetic"] is False


def test_pass_verdict_emits_no_failure_event(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result(_verdict_block("PASS"))),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    assert _events(run_dir, "failure_event") == []
    assert _result_json(run_dir, "implement")["synthetic"] is False


def test_cycle_exhausted_event(tmp_path: Path) -> None:
    _seed_state(tmp_path, {"impl": 1})
    runner = _make_runner(tmp_path, _cycle_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result(_verdict_block("PASS"))),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    events = _events(run_dir, "failure_event")
    assert len(events) == 1
    assert events[0]["kind"] == "cycle_exhausted"
    assert events[0]["cycle_name"] == "impl"
    assert events[0]["step_id"] == "implement"
    assert events[0]["synthetic"] is True


def test_ambiguous_worktree_event(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    err = AmbiguousWorktreeError(_CANONICAL, [("/a", "feat/99"), ("/b", "fix/99")])
    with (
        patch("kaji_harness.runner.discover_existing_worktree", side_effect=err),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()

    run_dir = _run_dir(tmp_path)
    events = _events(run_dir, "failure_event")
    assert len(events) == 1
    assert events[0]["kind"] == "ambiguous_worktree"
    assert events[0]["synthetic"] is True


# --- last_run_dir / recovery chain ---


def test_last_run_dir_is_exposed_after_failure(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    assert runner.last_run_dir is None
    with (
        patch("kaji_harness.runner.execute_cli", side_effect=StepTimeoutError("implement", 5)),
        patch("kaji_harness.runner.validate_skill_exists"),
        pytest.raises(StepTimeoutError),
    ):
        runner.run()
    assert runner.last_run_dir == _run_dir(tmp_path)


def test_recovery_chain_json_written_for_child_run(tmp_path: Path) -> None:
    runner = _make_runner(
        tmp_path,
        _single_step_workflow(),
        recovery_root="260710110000",
        recovery_parent="260710110000",
    )
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result(_verdict_block("PASS"))),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()

    chain = json.loads((_run_dir(tmp_path) / "recovery-chain.json").read_text(encoding="utf-8"))
    assert chain == {"root_run_id": "260710110000", "parent_run_id": "260710110000"}


def test_no_recovery_chain_json_without_flags(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, _single_step_workflow())
    with (
        patch("kaji_harness.runner.execute_cli", return_value=_cli_result(_verdict_block("PASS"))),
        patch("kaji_harness.runner.validate_skill_exists"),
    ):
        runner.run()
    assert not (_run_dir(tmp_path) / "recovery-chain.json").exists()


# --- result.json 後方互換 ---


def test_attempt_result_synthetic_defaults_to_false() -> None:
    result = AttemptResult(
        step_id="implement",
        attempt=1,
        status="PASS",
        exit_code=0,
        signal=None,
        started_at="t",
        ended_at="t",
        duration_ms=1,
        session_id=None,
        dispatch="agent",
    )
    assert result.synthetic is False


def test_legacy_result_json_without_synthetic_key_loads(tmp_path: Path) -> None:
    legacy = {
        "step_id": "implement",
        "attempt": 1,
        "status": "ABORT",
        "exit_code": 1,
        "signal": None,
        "started_at": "t",
        "ended_at": "t",
        "duration_ms": 1,
        "session_id": None,
        "dispatch": "agent",
        "error": "VerdictNotFound: x",
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = AttemptResult(**json.loads(path.read_text(encoding="utf-8")))
    assert loaded.synthetic is False
