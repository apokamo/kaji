"""Tests for timestamp feature in stream_and_log console output.

Issue #101: kaji run コンソール出力にタイムスタンプを追加する

S/M/L テスト戦略:
- Small: _now_stamp() のフォーマット検証・時刻固定テスト
- Medium: stream_and_log の print 出力タイムスタンプ検証、full_output/console.log 無混入確認
- Large: kaji run サブプロセスで stdout タイムスタンプ検証、--quiet 時の非出力確認
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.adapters import ClaudeAdapter
from kaji_harness.cli import _now_stamp, stream_and_log

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
CONSOLE_LINE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] \[.+\] .+")

MINIMAL_WORKFLOW_YAML = """\
name: test
description: test workflow
steps:
  - id: step1
    skill: test-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


# ============================================================
# Small tests: _now_stamp()
# ============================================================


@pytest.mark.small
class TestNowStampSmall:
    """Small: _now_stamp() のフォーマットと時刻固定テスト。"""

    def test_format_is_iso8601_seconds(self) -> None:
        """戻り値が YYYY-MM-DDTHH:MM:SS 形式であること。"""
        stamp = _now_stamp()
        assert TIMESTAMP_RE.match(stamp), f"Expected ISO 8601 seconds format, got: {stamp!r}"

    def test_returns_fixed_time_when_mocked(self) -> None:
        """datetime.now() をモックした場合、期待値と一致すること。"""
        with patch("kaji_harness.cli.datetime") as mock_dt:
            mock_dt.now.return_value.isoformat.return_value = "2026-03-13T15:04:23"
            stamp = _now_stamp()
        assert stamp == "2026-03-13T15:04:23"
        mock_dt.now.assert_called_once()

    def test_no_timezone_suffix(self) -> None:
        """タイムゾーン表記（+XX:XX や Z）が含まれないこと。"""
        stamp = _now_stamp()
        assert "+" not in stamp
        assert stamp.endswith(stamp[-2:])  # just a length sanity check
        # フォーマット検証で十分だが明示的に確認
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", stamp)


# ============================================================
# Medium tests: stream_and_log のタイムスタンプ挙動
# ============================================================


def _create_mock_script(path: Path, jsonl_lines: list[str]) -> Path:
    """JSONL を出力する mock CLI スクリプトを作成して返す。"""
    script = path / "mock_cli.sh"
    output = "\n".join(f"echo '{line}'" for line in jsonl_lines)
    script.write_text(f"#!/bin/bash\n{output}\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.medium
class TestStreamAndLogTimestampMedium:
    """Medium: stream_and_log のタイムスタンプ混入確認。"""

    def test_verbose_true_json_line_has_timestamp(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """verbose=True 時、JSON 行の print 出力にタイムスタンプが含まれること。"""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            ),
        ]
        script = _create_mock_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        stream_and_log(process, adapter, "design", log_dir, verbose=True)
        process.wait()

        captured = capsys.readouterr()
        assert CONSOLE_LINE_RE.match(captured.out.strip()), (
            f"Expected timestamp in output, got: {captured.out!r}"
        )
        assert "[design]" in captured.out
        assert "hello" in captured.out

    def test_verbose_true_non_json_line_has_timestamp(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """verbose=True 時、非 JSON 行の print 出力にもタイムスタンプが含まれること。"""
        script = tmp_path / "mock_plain.sh"
        script.write_text("#!/bin/bash\necho 'plain text line'\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        stream_and_log(process, adapter, "implement", log_dir, verbose=True)
        process.wait()

        captured = capsys.readouterr()
        assert CONSOLE_LINE_RE.match(captured.out.strip()), (
            f"Expected timestamp in non-JSON output, got: {captured.out!r}"
        )
        assert "[implement]" in captured.out
        assert "plain text line" in captured.out

    def test_verbose_false_no_print_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """verbose=False 時、print 出力がないこと（タイムスタンプも含めて）。"""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "quiet"}]},
                }
            ),
        ]
        script = _create_mock_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        stream_and_log(process, adapter, "step", log_dir, verbose=False)
        process.wait()

        captured = capsys.readouterr()
        assert captured.out == "", f"Expected no output when verbose=False, got: {captured.out!r}"

    def test_full_output_has_no_timestamp(self, tmp_path: Path) -> None:
        """CLIResult.full_output にタイムスタンプが含まれないこと。"""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "result text"}]},
                }
            ),
        ]
        script = _create_mock_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        result = stream_and_log(process, adapter, "step", log_dir, verbose=True)
        process.wait()

        assert "result text" in result.full_output
        assert not re.search(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]", result.full_output), (
            f"Timestamp must not appear in full_output, got: {result.full_output!r}"
        )

    def test_console_log_has_no_timestamp(self, tmp_path: Path) -> None:
        """console.log ファイルにタイムスタンプが含まれないこと。"""
        jsonl_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "log text"}]},
                }
            ),
        ]
        script = _create_mock_script(tmp_path, jsonl_lines)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        process = subprocess.Popen(
            [str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        adapter = ClaudeAdapter()
        stream_and_log(process, adapter, "step", log_dir, verbose=True)
        process.wait()

        console_content = (log_dir / "console.log").read_text()
        assert "log text" in console_content
        assert not re.search(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]", console_content), (
            f"Timestamp must not appear in console.log, got: {console_content!r}"
        )


# ============================================================
# Large tests: kaji run サブプロセス E2E
# ============================================================


def _build_e2e_env(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """E2E テスト用のワークフロー・プロジェクト・環境変数を構築する。"""
    wf = tmp_path / "workflow.yaml"
    wf.write_text(MINIMAL_WORKFLOW_YAML)

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

    return wf, workdir


def _create_mock_claude_script(bin_dir: Path) -> Path:
    """JSONL を出力する mock claude スクリプトを bin_dir に作成する。"""
    claude = bin_dir / "claude"
    jsonl_lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-e2e"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "---VERDICT---\\nstatus: PASS\\nreason: ok\\nevidence: |\\n  ok\\n---END_VERDICT---",
                        }
                    ]
                },
            }
        ),
        json.dumps({"type": "result", "result": "Done", "total_cost_usd": 0.01}),
    ]
    lines_sh = "\n".join(f"printf '%s\\n' '{line}'" for line in jsonl_lines)
    claude.write_text(f"#!/bin/bash\n{lines_sh}\nexit 0\n")
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    return claude


@pytest.mark.large
class TestKajiRunTimestampLarge:
    """Large: kaji run サブプロセスで stdout タイムスタンプを検証。"""

    def test_stdout_contains_timestamp(self, tmp_path: Path) -> None:
        """kaji run 実行時、stdout の各行に [YYYY-MM-DDTHH:MM:SS] が含まれること。"""
        wf, workdir = _build_e2e_env(tmp_path)

        # mock claude を PATH 先頭に配置
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _create_mock_claude_script(bin_dir)

        # Python venv の bin も PATH に含める（kaji モジュール実行のため）
        python_dir = str(Path(sys.executable).parent)
        env = {**os.environ, "PATH": f"{bin_dir}:{python_dir}"}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "101",
                "--workdir",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert result.returncode == 0, (
            f"kaji run failed with returncode={result.returncode}\nstderr={result.stderr!r}"
        )

        # kaji run の stdout は agent output 行（[timestamp] [step_id] text 形式）と
        # "Workflow '...' completed ..." のような summary 行が混在する。
        # ここでは step_id ブラケットを含む agent output 行のみを検証対象とする。
        agent_lines = [
            line
            for line in result.stdout.splitlines()
            if line.strip() and re.search(r"\[step\d*\]|\[step1\]", line)
        ]
        assert agent_lines, (
            f"Expected agent output lines in stdout, got none. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        for line in agent_lines:
            assert re.match(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] \[.+\]", line), (
                f"Agent output line missing timestamp: {line!r}"
            )

    def test_quiet_flag_suppresses_timestamp_output(self, tmp_path: Path) -> None:
        """--quiet フラグ使用時、タイムスタンプ付き出力が stdout に出ないこと。"""
        wf, workdir = _build_e2e_env(tmp_path)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _create_mock_claude_script(bin_dir)

        python_dir = str(Path(sys.executable).parent)
        env = {**os.environ, "PATH": f"{bin_dir}:{python_dir}"}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "101",
                "--workdir",
                str(workdir),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert result.returncode == 0, (
            f"kaji run --quiet failed with returncode={result.returncode}\nstderr={result.stderr!r}"
        )

        timestamp_lines = [
            line
            for line in result.stdout.splitlines()
            if re.match(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]", line)
        ]
        assert not timestamp_lines, (
            f"Expected no timestamp lines with --quiet, got: {timestamp_lines}"
        )
