"""Large-local Antigravity workflow contract tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from kaji_harness.cli import execute_cli
from kaji_harness.verdict import parse_verdict
from kaji_harness.workflow import load_workflow_from_str, validate_workflow

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_KAJI_CMD = [sys.executable, "-m", "kaji_harness.cli_main"]


def test_stub_agy_runs_validated_workflow_step_to_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stub AGY の real subprocess 出力を workflow validation から verdict まで運ぶ。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agy = fake_bin / "agy"
    fake_agy.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '---VERDICT---' 'status: PASS' "
        "'reason: stub agy completed' 'evidence: plain stdout' "
        "'suggestion: \"\"' '---END_VERDICT---'\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    workflow = load_workflow_from_str(
        dedent("""\
            name: antigravity-e2e
            execution_policy: auto
            steps:
              - id: implement
                skill: issue-implement
                agent: antigravity
                model: gemini-3-pro
                effort: high
                on:
                  PASS: end
        """)
    )
    validate_workflow(workflow)
    step = workflow.steps[0]

    result = execute_cli(
        step=step,
        prompt="perform the step",
        workdir=tmp_path,
        session_id=None,
        log_dir=tmp_path / "attempt",
        execution_policy=workflow.execution_policy,
        verbose=False,
        default_timeout=10,
    )
    verdict = parse_verdict(result.full_output, {"PASS", "RETRY", "BACK", "ABORT"})

    assert verdict.status == "PASS"
    assert result.session_id is None
    assert result.cost is None


def test_kaji_validate_rejects_antigravity_resume_with_context(tmp_path: Path) -> None:
    """実 CLI が AGY resume を step・agent・capability 付きで拒否する。"""
    workflow_path = tmp_path / "resume.yaml"
    workflow_path.write_text(
        dedent("""\
            name: antigravity-resume
            execution_policy: auto
            steps:
              - id: design
                skill: issue-design
                agent: antigravity
                on:
                  PASS: implement
              - id: implement
                skill: issue-implement
                agent: antigravity
                resume: design
                on:
                  PASS: end
        """),
        encoding="utf-8",
    )

    result = subprocess.run(
        [*_KAJI_CMD, "validate", str(workflow_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "implement" in result.stderr
    assert "antigravity" in result.stderr
    assert "resume" in result.stderr
