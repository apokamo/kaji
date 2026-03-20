"""Large tests: Verdict E2E.

Tests using real agent output fixtures and actual CLI execution.
Verifies the full verdict parsing pipeline from raw output to Verdict.
"""

from __future__ import annotations

import json
import stat
import textwrap
from pathlib import Path

import pytest

from kaji_harness.models import Verdict
from kaji_harness.verdict import parse_verdict

VALID_STATUSES = {"PASS", "RETRY", "BACK", "ABORT"}
FIXTURES_DIR = Path(__file__).parent.parent / "test-artifacts" / "verdict-fixtures"


def _ensure_fixtures_dir() -> Path:
    """Ensure the fixtures directory exists."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURES_DIR


# ============================================================
# Real agent output fixture tests
# ============================================================


@pytest.mark.large
class TestRealAgentOutputFixtures:
    """Parse verdicts from real agent output samples."""

    def test_issue_73_end_verdict_space(self) -> None:
        """#73 actual case: ---END VERDICT--- (space instead of underscore).

        This was the triggering incident for issue #77.
        """
        # This is the approximate output structure from the #73 issue-pr step
        output = (
            "## PR作成完了\n\n"
            "| 項目 | 値 |\n"
            "|------|-----|\n"
            "| Issue | #73 |\n"
            "| PR | #75 |\n\n"
            "### 次のステップ\n\n"
            "`/issue-close 73` でIssueをクローズしてください。\n\n"
            "---VERDICT---\n"
            "status: PASS\n"
            "reason: |\n"
            "  PR作成・プッシュ完了\n"
            "evidence: |\n"
            "  gh pr create 正常終了、PR #75 作成済み\n"
            "suggestion: |\n"
            "---END VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert isinstance(result, Verdict)

    def test_issue_73_fixture_file(self) -> None:
        """Parse from saved fixture file if it exists."""
        fixture_path = FIXTURES_DIR / "issue-73-end-verdict-space.txt"
        if not fixture_path.exists():
            _ensure_fixtures_dir()
            # Save the fixture for future regression tests
            fixture_path.write_text(
                "---VERDICT---\n"
                "status: PASS\n"
                "reason: |\n"
                "  PR作成・プッシュ完了\n"
                "evidence: |\n"
                "  gh pr create 正常終了\n"
                "suggestion: |\n"
                "---END VERDICT---\n",
                encoding="utf-8",
            )

        content = fixture_path.read_text(encoding="utf-8")
        result = parse_verdict(content, VALID_STATUSES)
        assert result.status == "PASS"

    def test_codex_mcp_tool_call_output(self) -> None:
        """Codex output where VERDICT appears in mcp_tool_call result text."""
        # Simulates the scenario described in legacy/docs/E2E_TEST_FINDINGS.md
        output = (
            "Analyzing the codebase...\n"
            "Running tests...\n"
            "All tests passed.\n\n"
            "## VERDICT\n"
            "- Result: PASS\n"
            "- Reason: 全テスト通過、品質チェッククリア\n"
            "- Evidence: pytest 15 passed, ruff/mypy clean\n"
            "- Suggestion: なし\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert "全テスト通過" in result.reason

    def test_verbose_output_with_thinking_traces(self) -> None:
        """Output with extensive thinking traces before verdict."""
        lines = [
            "思考中...",
            "ステップ1: コードを分析",
            "ステップ2: テストを実行",
            "ステップ3: 結果を確認",
            "",
            "分析結果: すべてのテストが通過しました。",
            "カバレッジ: 85%",
            "",
            "詳細ログ:" + "\n  debug line " * 50,  # Lots of noise
            "",
            "---VERDICT---",
            "status: PASS",
            'reason: "全テスト通過・品質チェック完了"',
            'evidence: "pytest 20 passed, coverage 85%, ruff/mypy clean"',
            'suggestion: ""',
            "---END_VERDICT---",
        ]
        output = "\n".join(lines)
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"

    def test_abort_with_detailed_suggestion(self) -> None:
        """ABORT verdict with multi-line suggestion from a real scenario."""
        output = (
            "環境チェック失敗\n"
            "---VERDICT---\n"
            "status: ABORT\n"
            "reason: |\n"
            "  外部APIに接続できません\n"
            "evidence: |\n"
            "  ConnectionError: Failed to connect to api.example.com:443\n"
            "  Traceback (most recent call last):\n"
            '    File "test_api.py", line 42\n'
            "    requests.get(url, timeout=5)\n"
            "suggestion: |\n"
            "  1. VPN接続を確認してください\n"
            "  2. API_KEY環境変数が設定されているか確認\n"
            "  3. 手動で curl api.example.com を試行\n"
            "---END_VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "ABORT"
        assert "VPN" in result.suggestion
        assert "ConnectionError" in result.evidence


# ============================================================
# kaji run workflow execution
# ============================================================


@pytest.mark.large
class TestKajiRunWorkflowExecution:
    """E2E test: kaji run executes workflow and parses verdicts successfully.

    Uses a fake agent CLI script that emits Claude-compatible JSONL events,
    allowing the full pipeline (CLI → runner → adapter → verdict parser →
    state transition) to be exercised without real API calls.
    """

    def test_kaji_validate_workflows(self) -> None:
        """kaji validate succeeds on all workflow files (no agent required)."""
        import shutil
        import subprocess

        kaji_path = shutil.which("kaji")
        if kaji_path is None:
            pytest.skip("kaji CLI not installed")

        workflows_dir = Path(__file__).parent.parent / "workflows"
        if not workflows_dir.exists():
            pytest.skip("workflows/ directory not found")

        yaml_files = list(workflows_dir.glob("*.yaml"))
        if not yaml_files:
            pytest.skip("No workflow YAML files found")

        result = subprocess.run(
            ["kaji", "validate", *[str(f) for f in yaml_files]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"kaji validate failed: {result.stderr}"

    def test_kaji_run_strict_verdict_single_step(self, tmp_path: Path) -> None:
        """kaji run parses a strict VERDICT from a fake agent and exits 0."""
        import os
        import subprocess
        import sys

        _setup_fake_agent_env(tmp_path, verdict_style="strict")
        workflow = tmp_path / "workflow.yaml"
        workdir = tmp_path / "project"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflow),
                "9990",
                "--step",
                "step1",
                "--workdir",
                str(workdir),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workdir),
            env={**os.environ, "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]},
        )
        assert result.returncode == 0, (
            f"kaji run failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_kaji_run_relaxed_verdict_single_step(self, tmp_path: Path) -> None:
        """kaji run recovers a relaxed VERDICT (---END VERDICT---) via fallback.

        Reproduces the #73 incident where the agent output used a space
        instead of underscore in the end delimiter.
        """
        import os
        import subprocess
        import sys

        _setup_fake_agent_env(tmp_path, verdict_style="relaxed")
        workflow = tmp_path / "workflow.yaml"
        workdir = tmp_path / "project"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflow),
                "9991",
                "--step",
                "step1",
                "--workdir",
                str(workdir),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workdir),
            env={**os.environ, "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]},
        )
        assert result.returncode == 0, (
            f"kaji run with relaxed verdict failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_kaji_run_multi_step_workflow(self, tmp_path: Path) -> None:
        """kaji run executes a 2-step workflow with verdict-driven transitions."""
        import os
        import subprocess
        import sys

        _setup_fake_agent_env(tmp_path, verdict_style="strict", multi_step=True)
        workflow = tmp_path / "workflow.yaml"
        workdir = tmp_path / "project"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflow),
                "9992",
                "--workdir",
                str(workdir),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workdir),
            env={**os.environ, "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]},
        )
        assert result.returncode == 0, (
            f"kaji run multi-step failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ============================================================
# Regression fixture management
# ============================================================


# ============================================================
# Helpers for kaji run E2E tests
# ============================================================


def _setup_fake_agent_env(
    tmp_path: Path,
    *,
    verdict_style: str = "strict",
    multi_step: bool = False,
) -> None:
    """Create a fake ``claude`` CLI, workflow YAML, and skill directory.

    The fake agent emits Claude-compatible JSONL events containing a
    VERDICT block so that the full kaji pipeline can be exercised:
    CLI → runner → subprocess → adapter → verdict parser → state.

    Args:
        tmp_path: Temporary directory (pytest fixture).
        verdict_style: ``"strict"`` uses ``---END_VERDICT---``,
            ``"relaxed"`` uses ``---END VERDICT---`` (the #73 case).
        multi_step: If True, create a 2-step workflow with transitions.
    """
    if verdict_style == "strict":
        end_delimiter = "---END_VERDICT---"
    else:
        end_delimiter = "---END VERDICT---"

    verdict_text = (
        "---VERDICT---\\n"
        "status: PASS\\n"
        "reason: |\\n"
        "  Fake agent completed successfully\\n"
        "evidence: |\\n"
        "  All checks passed\\n"
        "suggestion: |\\n"
        f"{end_delimiter}\\n"
    )

    # Build JSONL events that ClaudeAdapter expects
    init_event = json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sess-001"})
    text_event = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": verdict_text.replace("\\n", "\n")}]},
        }
    )
    result_event = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.0})

    # Create fake claude script
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        print({init_event!r})
        print({text_event!r})
        print({result_event!r})
        sys.exit(0)
        """)
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    # Create workflow YAML
    workflow = tmp_path / "workflow.yaml"
    if multi_step:
        workflow.write_text(
            textwrap.dedent("""\
            name: test-multi
            description: Two-step test workflow
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                on:
                  PASS: step2
                  ABORT: end
              - id: step2
                skill: test-skill
                agent: claude
                on:
                  PASS: end
                  ABORT: end
            """)
        )
    else:
        workflow.write_text(
            textwrap.dedent("""\
            name: test-single
            description: Single-step test workflow
            steps:
              - id: step1
                skill: test-skill
                agent: claude
                on:
                  PASS: end
                  ABORT: end
            """)
        )

    # Create project directory with config and skill
    workdir = tmp_path / "project"
    workdir.mkdir()
    config_dir = workdir / ".kaji"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n'
    )
    skill_dir = workdir / ".claude" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")


# ============================================================
# Regression fixture management
# ============================================================


@pytest.mark.large
class TestFixtureManagement:
    """Ensure fixture directory and files are maintained."""

    def test_fixtures_dir_exists(self) -> None:
        """test-artifacts/verdict-fixtures/ directory is accessible."""
        _ensure_fixtures_dir()
        assert FIXTURES_DIR.exists()
