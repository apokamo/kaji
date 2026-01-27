"""Tests for bugfix_agent logging - warn() 関数のテスト.

Issue #35: v5 Phase1 - warn() 関数の cli_console.log / run.log 出力
"""

import json
from pathlib import Path

import pytest

from src.bugfix_agent.logging import warn


class TestWarnStderr:
    """warn() - stderr 出力のテスト."""

    def test_warn_outputs_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """warn() が stderr に出力すること."""
        warn("Test warning message", log_dir=None)

        captured = capsys.readouterr()
        assert "[WARN]" in captured.err
        assert "Test warning message" in captured.err

    def test_warn_includes_timestamp(self, capsys: pytest.CaptureFixture[str]) -> None:
        """warn() の出力にタイムスタンプが含まれること."""
        warn("Test message", log_dir=None)

        captured = capsys.readouterr()
        # ISO 8601 形式のタイムスタンプが含まれる（例: 2026-01-28T10:00:00）
        assert "202" in captured.err  # Year prefix


class TestWarnCliConsoleLog:
    """warn() - cli_console.log 出力のテスト."""

    def test_warn_creates_cli_console_log(self, tmp_path: Path) -> None:
        """warn() が cli_console.log を作成すること."""
        warn("Test warning", log_dir=tmp_path)

        cli_console_path = tmp_path / "cli_console.log"
        assert cli_console_path.exists()

    def test_warn_appends_to_cli_console_log(self, tmp_path: Path) -> None:
        """warn() が cli_console.log に追記すること."""
        warn("First warning", log_dir=tmp_path)
        warn("Second warning", log_dir=tmp_path)

        cli_console_path = tmp_path / "cli_console.log"
        content = cli_console_path.read_text()

        assert "First warning" in content
        assert "Second warning" in content

    def test_cli_console_log_format(self, tmp_path: Path) -> None:
        """cli_console.log が [WARN] {timestamp} {message} 形式であること."""
        warn("Formatted message", log_dir=tmp_path)

        cli_console_path = tmp_path / "cli_console.log"
        content = cli_console_path.read_text()

        assert content.startswith("[WARN]")
        assert "Formatted message" in content


class TestWarnRunLog:
    """warn() - run.log 出力のテスト."""

    def test_warn_creates_run_log(self, tmp_path: Path) -> None:
        """warn() が run.log を作成すること."""
        warn("Test warning", log_dir=tmp_path)

        run_log_path = tmp_path / "run.log"
        assert run_log_path.exists()

    def test_warn_appends_to_run_log(self, tmp_path: Path) -> None:
        """warn() が run.log に追記すること."""
        warn("First warning", log_dir=tmp_path)
        warn("Second warning", log_dir=tmp_path)

        run_log_path = tmp_path / "run.log"
        lines = run_log_path.read_text().strip().split("\n")

        assert len(lines) == 2

    def test_run_log_jsonl_format(self, tmp_path: Path) -> None:
        """run.log が JSONL 形式であること."""
        warn("JSON message", log_dir=tmp_path)

        run_log_path = tmp_path / "run.log"
        line = run_log_path.read_text().strip()

        data = json.loads(line)
        assert data["event"] == "warning"
        assert data["message"] == "JSON message"
        assert "timestamp" in data

    def test_run_log_timestamp_format(self, tmp_path: Path) -> None:
        """run.log の timestamp が ISO 8601 形式であること."""
        warn("Timestamp test", log_dir=tmp_path)

        run_log_path = tmp_path / "run.log"
        line = run_log_path.read_text().strip()
        data = json.loads(line)

        # ISO 8601 形式: YYYY-MM-DDTHH:MM:SS
        assert "T" in data["timestamp"]
        assert data["timestamp"].startswith("202")


class TestWarnErrorHandling:
    """warn() - エラーハンドリングのテスト."""

    def test_warn_handles_invalid_log_dir(self, capsys: pytest.CaptureFixture[str]) -> None:
        """無効な log_dir でも stderr 出力は成功すること."""
        # 存在しないパスの下に書き込もうとする
        invalid_path = Path("/nonexistent/path/that/does/not/exist")

        # エラーを起こさずに実行できること
        warn("Test message", log_dir=invalid_path)

        # stderr には出力されている
        captured = capsys.readouterr()
        assert "Test message" in captured.err

    def test_warn_creates_log_dir_if_not_exists(self, tmp_path: Path) -> None:
        """log_dir が存在しない場合は作成すること."""
        new_dir = tmp_path / "new" / "nested" / "dir"
        assert not new_dir.exists()

        warn("Test message", log_dir=new_dir)

        assert new_dir.exists()
        assert (new_dir / "cli_console.log").exists()
        assert (new_dir / "run.log").exists()
