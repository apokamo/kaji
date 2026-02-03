"""Tests for RunLogger - ワークフローレベルの JSONL ログ (run.log) 出力テスト.

Issue #38: Tests/Verification Phase1/2 - RunLogger のユニットテスト

ログファイルの役割分担:
- run.log: ワークフロー全体のライフサイクルログ ({workdir}/artifacts/)
  → RunLogger が run_start, state_enter/exit, run_end を記録
  → このテストクラスでカバー
- events.jsonl: ハンドラ内の詳細イベントログ ({workdir}/artifacts/{state}/)
  → save_jsonl_log が handler_start, ai_call_*, handler_end を記録
  → test_design_handlers.py の TestDesignWorkflowEventLogs でテスト

設計書セクション: C. ログ・実行基盤
"""

import json
from pathlib import Path

from src.bugfix_agent.run_logger import RunLogger
from src.core import RunLogger as CoreRunLogger
from src.core.run_logger import RunLogger as DirectCoreRunLogger


class TestRunLoggerInit:
    """RunLogger 初期化テスト."""

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """親ディレクトリが存在しない場合は作成すること."""
        log_path = tmp_path / "nested" / "dir" / "run.log"
        assert not log_path.parent.exists()

        RunLogger(log_path)

        assert log_path.parent.exists()

    def test_log_path_stored(self, tmp_path: Path) -> None:
        """log_path が保存されること."""
        log_path = tmp_path / "run.log"

        logger = RunLogger(log_path)

        assert logger.log_path == log_path


class TestRunLoggerLogRunStart:
    """log_run_start イベントテスト."""

    def test_creates_log_file(self, tmp_path: Path) -> None:
        """ログファイルを作成すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_start(
            issue_url="https://github.com/test/repo/issues/1", run_id="20260129T100000"
        )

        assert log_path.exists()

    def test_jsonl_format(self, tmp_path: Path) -> None:
        """JSONL 形式で出力すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_start(
            issue_url="https://github.com/test/repo/issues/1", run_id="20260129T100000"
        )

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "run_start"
        assert data["issue_url"] == "https://github.com/test/repo/issues/1"
        assert data["run_id"] == "20260129T100000"

    def test_includes_timestamp(self, tmp_path: Path) -> None:
        """タイムスタンプが含まれること（キー名は設計書準拠で timestamp）."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_start(issue_url="https://github.com/test/repo/issues/1", run_id="test")

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert "timestamp" in data
        # ISO 8601 形式チェック
        assert "T" in data["timestamp"]
        assert data["timestamp"].startswith("20")


class TestRunLoggerLogStateEnter:
    """log_state_enter イベントテスト."""

    def test_state_enter_basic(self, tmp_path: Path) -> None:
        """state_enter イベントを記録すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_state_enter(state="DESIGN")

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "state_enter"
        assert data["state"] == "DESIGN"

    def test_state_enter_with_session_id(self, tmp_path: Path) -> None:
        """session_id 付きで state_enter を記録すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_state_enter(state="DESIGN", session_id="session-123")

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "state_enter"
        assert data["state"] == "DESIGN"
        assert data["session_id"] == "session-123"

    def test_state_enter_without_session_id(self, tmp_path: Path) -> None:
        """session_id なしの場合は含めないこと."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_state_enter(state="DESIGN")

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert "session_id" not in data


class TestRunLoggerLogStateExit:
    """log_state_exit イベントテスト."""

    def test_state_exit_basic(self, tmp_path: Path) -> None:
        """state_exit イベントを記録すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_state_exit(state="DESIGN", result="PASS", next_state="DESIGN_REVIEW")

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "state_exit"
        assert data["state"] == "DESIGN"
        assert data["result"] == "PASS"
        assert data["next"] == "DESIGN_REVIEW"


class TestRunLoggerLogRunEnd:
    """log_run_end イベントテスト."""

    def test_run_end_success(self, tmp_path: Path) -> None:
        """成功時の run_end イベントを記録すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_end(status="completed", loop_counters={"design": 2, "review": 1})

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "run_end"
        assert data["status"] == "completed"
        assert data["loop_counters"] == {"design": 2, "review": 1}
        assert "error" not in data

    def test_run_end_with_error(self, tmp_path: Path) -> None:
        """エラー時の run_end イベントを記録すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_end(
            status="failed", loop_counters={"design": 3}, error="Loop limit exceeded"
        )

        content = log_path.read_text().strip()
        data = json.loads(content)

        assert data["event"] == "run_end"
        assert data["status"] == "failed"
        assert data["error"] == "Loop limit exceeded"


class TestRunLoggerMultipleEvents:
    """複数イベントの出力テスト."""

    def test_appends_events(self, tmp_path: Path) -> None:
        """複数イベントを追記すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_start(issue_url="https://github.com/test/repo/issues/1", run_id="test")
        logger.log_state_enter(state="DESIGN")
        logger.log_state_exit(state="DESIGN", result="PASS", next_state="DESIGN_REVIEW")
        logger.log_run_end(status="completed", loop_counters={"design": 1})

        lines = log_path.read_text().strip().split("\n")

        assert len(lines) == 4
        assert json.loads(lines[0])["event"] == "run_start"
        assert json.loads(lines[1])["event"] == "state_enter"
        assert json.loads(lines[2])["event"] == "state_exit"
        assert json.loads(lines[3])["event"] == "run_end"


class TestRunLoggerUtf8:
    """UTF-8 文字のテスト."""

    def test_utf8_issue_url(self, tmp_path: Path) -> None:
        """UTF-8 文字を含む issue_url を正しく保存すること."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)

        logger.log_run_start(
            issue_url="https://github.com/テスト/リポジトリ/issues/1", run_id="test"
        )

        content = log_path.read_text(encoding="utf-8").strip()
        data = json.loads(content)

        assert data["issue_url"] == "https://github.com/テスト/リポジトリ/issues/1"


class TestRunLoggerImports:
    """RunLogger import paths test."""

    def test_import_from_core_module(self) -> None:
        """Can import from src.core module."""
        assert CoreRunLogger is RunLogger

    def test_import_from_core_run_logger(self) -> None:
        """Can import directly from src.core.run_logger."""
        assert DirectCoreRunLogger is RunLogger

    def test_backward_compatibility(self, tmp_path: Path) -> None:
        """Backward compatibility: bugfix_agent import works."""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)
        logger.log_run_start(issue_url="https://example.com", run_id="test")
        assert log_path.exists()
