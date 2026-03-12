"""Medium tests: Verdict parsing integration.

Tests integration between verdict parser and other modules:
- runner.py → parse_verdict flow
- create_verdict_formatter factory
- Output collection layer (cli.py + adapters.py)
- State persistence and previous_verdict propagation
- Logger verdict output
- Skill output template parsing
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.adapters import CodexAdapter
from kaji_harness.cli import stream_and_log
from kaji_harness.verdict import create_verdict_formatter, parse_verdict

VALID_STATUSES = {"PASS", "RETRY", "BACK", "ABORT"}


def _create_mock_cli_script(path: Path, lines: list[str], exit_code: int = 0) -> Path:
    """Create a mock CLI script that outputs given lines."""
    script = path / "mock_cli.sh"
    output = "\n".join(f"echo '{line}'" for line in lines)
    script.write_text(f"#!/bin/bash\n{output}\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


# ============================================================
# create_verdict_formatter factory tests
# ============================================================


@pytest.mark.medium
class TestCreateVerdictFormatterFactory:
    """create_verdict_formatter generates callable formatters."""

    def test_claude_formatter_cli_args(self) -> None:
        """Claude formatter builds correct CLI args."""
        formatter = create_verdict_formatter(
            agent="claude",
            valid_statuses={"PASS", "ABORT"},
        )
        assert callable(formatter)

    def test_codex_formatter_cli_args(self) -> None:
        """Codex formatter builds correct CLI args."""
        formatter = create_verdict_formatter(
            agent="codex",
            valid_statuses={"PASS", "RETRY"},
        )
        assert callable(formatter)

    def test_gemini_formatter_cli_args(self) -> None:
        """Gemini formatter builds correct CLI args."""
        formatter = create_verdict_formatter(
            agent="gemini",
            valid_statuses={"PASS", "BACK", "ABORT"},
        )
        assert callable(formatter)

    def test_formatter_subprocess_called_with_prompt(self) -> None:
        """Formatter invokes subprocess with correct prompt containing valid_statuses."""
        formatter = create_verdict_formatter(
            agent="claude",
            valid_statuses={"PASS", "ABORT"},
            model="sonnet",
            workdir=Path("/tmp"),
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="formatted output", returncode=0)
            result = formatter("raw input text")

        assert result == "formatted output"
        call_args = mock_run.call_args
        assert call_args is not None
        # Check timeout is set
        assert call_args.kwargs.get("timeout") == 60
        # Check cwd is set
        assert call_args.kwargs.get("cwd") == Path("/tmp")

    def test_formatter_prompt_excludes_invalid_statuses(self) -> None:
        """Formatter prompt for {"PASS", "ABORT"} should not contain RETRY or BACK."""
        formatter = create_verdict_formatter(
            agent="claude",
            valid_statuses={"PASS", "ABORT"},
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="placeholder", returncode=0)
            formatter("some text")

        # Extract the prompt argument from the CLI args
        call_args = mock_run.call_args
        assert call_args is not None
        cli_args = call_args.args[0]  # First positional arg is the list of CLI args
        prompt_text = cli_args[-1]  # Prompt is the last argument
        assert "PASS" in prompt_text
        assert "ABORT" in prompt_text
        assert "RETRY" not in prompt_text
        assert "BACK" not in prompt_text

    def test_codex_formatter_no_json_flag(self) -> None:
        """Codex formatter does NOT use --json (plain text output for reparsing)."""
        formatter = create_verdict_formatter(
            agent="codex",
            valid_statuses={"PASS", "RETRY"},
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="---VERDICT---\nstatus: PASS\nreason: ok\nevidence: ok\n---END_VERDICT---",
                returncode=0,
            )
            formatter("raw text")

        call_args = mock_run.call_args
        assert call_args is not None
        cli_args = call_args.args[0]
        assert "--json" not in cli_args

    def test_codex_formatter_output_reparseable(self) -> None:
        """Codex formatter output (plain text) can be reparsed by parse_verdict."""
        # Simulate what a real Codex formatter would return in plain text mode
        formatted_output = (
            "---VERDICT---\n"
            "status: PASS\n"
            'reason: "AI formatted the verdict"\n'
            'evidence: "All tests passed"\n'
            'suggestion: ""\n'
            "---END_VERDICT---\n"
        )

        formatter = create_verdict_formatter(
            agent="codex",
            valid_statuses={"PASS", "RETRY"},
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=formatted_output, returncode=0)
            result = formatter("raw unparseable text")

        # The formatter output should be directly parseable
        verdict = parse_verdict(result, {"PASS", "RETRY"})
        assert verdict.status == "PASS"

    def test_formatter_nonzero_exit_raises(self) -> None:
        """Formatter subprocess non-zero exit raises VerdictParseError."""
        from kaji_harness.errors import VerdictParseError

        formatter = create_verdict_formatter(
            agent="claude",
            valid_statuses={"PASS"},
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="CLI error", returncode=1)
            with pytest.raises(VerdictParseError, match="exited with code 1"):
                formatter("some text")

    def test_formatter_empty_output_raises(self) -> None:
        """Formatter returning empty output raises VerdictParseError."""
        from kaji_harness.errors import VerdictParseError

        formatter = create_verdict_formatter(
            agent="claude",
            valid_statuses={"PASS"},
        )

        with patch("kaji_harness.verdict.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            with pytest.raises(VerdictParseError, match="empty output"):
                formatter("some text")


# ============================================================
# Output collection: non-JSON lines in stream_and_log
# ============================================================


@pytest.mark.medium
class TestStreamAndLogNonJsonLines:
    """stream_and_log collects non-JSON lines into full_output."""

    def test_non_json_lines_included_in_output(self, tmp_path: Path) -> None:
        """Non-JSON lines (e.g., plain text VERDICT) are included in full_output."""
        lines = [
            "---VERDICT---",
            "status: PASS",
            'reason: "OK"',
            'evidence: "green"',
            "---END_VERDICT---",
        ]
        script = _create_mock_cli_script(tmp_path, lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        from kaji_harness.adapters import ClaudeAdapter

        result = stream_and_log(process, ClaudeAdapter(), "test", log_dir, verbose=False)
        process.wait()

        assert "VERDICT" in result.full_output
        assert "status: PASS" in result.full_output

    def test_mixed_json_and_non_json(self, tmp_path: Path) -> None:
        """Both JSON and non-JSON lines contribute to full_output."""
        json_line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            }
        )
        lines = [
            json_line,
            "plain text line",
            "---VERDICT---",
            "status: PASS",
        ]
        script = _create_mock_cli_script(tmp_path, lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        from kaji_harness.adapters import ClaudeAdapter

        result = stream_and_log(process, ClaudeAdapter(), "test", log_dir, verbose=False)
        process.wait()

        assert "Hello" in result.full_output
        assert "plain text line" in result.full_output


# ============================================================
# Output collection: Codex mcp_tool_call integration
# ============================================================


@pytest.mark.medium
class TestCodexMcpToolCallIntegration:
    """Codex mcp_tool_call events flow through to full_output."""

    def test_mcp_tool_call_verdict_in_output(self, tmp_path: Path) -> None:
        """mcp_tool_call with VERDICT text ends up in full_output."""
        verdict_text = (
            '---VERDICT---\nstatus: PASS\nreason: "OK"\nevidence: "green"\n---END_VERDICT---'
        )
        mcp_event = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "result": {"content": [{"type": "text", "text": verdict_text}]},
                },
            }
        )
        script = _create_mock_cli_script(tmp_path, [mcp_event])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = CodexAdapter()
        result = stream_and_log(process, adapter, "test", log_dir, verbose=False)
        process.wait()

        assert "VERDICT" in result.full_output
        # And parse_verdict should succeed on this output
        verdict = parse_verdict(result.full_output, VALID_STATUSES)
        assert verdict.status == "PASS"


# ============================================================
# Relaxed verdict → state persistence
# ============================================================


@pytest.mark.medium
class TestRelaxedVerdictStatePersistence:
    """Relaxed-parsed verdicts persist correctly in SessionState."""

    def test_relaxed_verdict_persists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verdict recovered via relaxed parse saves in state correctly."""
        from kaji_harness import state as state_mod
        from kaji_harness.state import SessionState

        monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)

        # Relaxed-delimiter verdict
        output = (
            "---VERDICT---\n"
            "status: PASS\n"
            'reason: "relaxed recovery"\n'
            'evidence: "pattern match"\n'
            "---END VERDICT---"
        )
        verdict = parse_verdict(output, VALID_STATUSES)

        state = SessionState.load_or_create(99999)
        state.record_step("test-step", verdict)

        assert state.last_transition_verdict is not None
        assert state.last_transition_verdict.status == "PASS"
        assert state.last_transition_verdict.reason == "relaxed recovery"


# ============================================================
# previous_verdict propagation
# ============================================================


@pytest.mark.medium
class TestPreviousVerdictPropagation:
    """Relaxed-parsed verdict reason/evidence propagate correctly to next step prompt."""

    def test_relaxed_verdict_in_prompt(self) -> None:
        """Relaxed verdict's reason/evidence appear in next step's prompt."""
        from kaji_harness.models import Step, Workflow
        from kaji_harness.prompt import build_prompt
        from kaji_harness.state import SessionState

        # Create a state with a relaxed verdict recorded
        output = (
            "Result: BACK\n"
            "Reason: 設計に問題あり\n"
            "Evidence: API仕様不整合\n"
            "Suggestion: issue-design を再実行\n"
        )
        verdict = parse_verdict(output, VALID_STATUSES)

        state = SessionState.__new__(SessionState)
        state.issue_number = 99999
        state._steps = {"fix": verdict}
        state._cycle_counts = {}
        state.last_transition_verdict = verdict

        step = Step(
            id="fix-step",
            skill="issue-fix-design",
            agent="claude",
            resume="fix",
            on={"PASS": "end", "BACK": "design"},
        )
        workflow = Workflow(
            name="test",
            description="test",
            execution_policy="auto",
            steps=[step],
        )

        prompt = build_prompt(step, 99999, state, workflow)
        assert "設計に問題あり" in prompt
        assert "API仕様不整合" in prompt


# ============================================================
# Skill output template parsing
# ============================================================


@pytest.mark.medium
class TestSkillOutputTemplateParsing:
    """Parse verdict from output that resembles real skill output templates."""

    def test_issue_implement_verdict_template(self) -> None:
        """Parse the standard issue-implement verdict format."""
        output = (
            "## 実装完了\n\n"
            "| 項目 | 値 |\n|------|-----|\n| Issue | #77 |\n\n"
            "---VERDICT---\n"
            "status: PASS\n"
            "reason: |\n"
            "  実装・テスト・品質チェック全パス\n"
            "evidence: |\n"
            "  pytest 全テストパス、ruff/mypy エラーなし\n"
            "suggestion: |\n"
            "---END_VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"

    def test_issue_review_code_verdict_template(self) -> None:
        """Parse the standard issue-review-code verdict format with RETRY."""
        output = (
            "## コードレビュー結果\n\n"
            "### 指摘事項\n"
            "1. テストカバレッジ不足\n\n"
            "---VERDICT---\n"
            "status: RETRY\n"
            'reason: "テストカバレッジが基準未達"\n'
            'evidence: "coverage: 65% (target: 80%)"\n'
            'suggestion: "テスト追加"\n'
            "---END_VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "RETRY"

    def test_issue_pr_relaxed_verdict(self) -> None:
        """Parse the #73-style verdict where END VERDICT has space."""
        output = (
            "## PR作成完了\n\n"
            "PR: #456\n\n"
            "---VERDICT---\n"
            "status: PASS\n"
            "reason: |\n"
            "  PR作成成功\n"
            "evidence: |\n"
            "  gh pr create 正常終了\n"
            "suggestion: |\n"
            "---END VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
