"""Tests for artifacts module - 証跡保存機能."""

import json
from pathlib import Path


class TestSaveArtifact:
    """save_artifact 関数テスト."""

    def test_save_artifact_basic(self, tmp_path: Path) -> None:
        """基本的なファイル保存ができること."""
        from src.core.artifacts import save_artifact

        result = save_artifact(tmp_path, "test.md", "# Test Content")

        assert result == tmp_path / "test.md"
        assert result.read_text() == "# Test Content"

    def test_save_artifact_overwrite(self, tmp_path: Path) -> None:
        """既存ファイルを上書きできること."""
        from src.core.artifacts import save_artifact

        (tmp_path / "test.md").write_text("Old content")

        save_artifact(tmp_path, "test.md", "New content")

        assert (tmp_path / "test.md").read_text() == "New content"

    def test_save_artifact_append(self, tmp_path: Path) -> None:
        """append=True で追記できること."""
        from src.core.artifacts import save_artifact

        save_artifact(tmp_path, "test.md", "First line\n")
        save_artifact(tmp_path, "test.md", "Second line\n", append=True)

        content = (tmp_path / "test.md").read_text()
        assert "First line" in content
        assert "Second line" in content

    def test_save_artifact_utf8(self, tmp_path: Path) -> None:
        """UTF-8 文字を正しく保存できること."""
        from src.core.artifacts import save_artifact

        content = "日本語テスト\n🎉"
        save_artifact(tmp_path, "test.md", content)

        assert (tmp_path / "test.md").read_text(encoding="utf-8") == content


class TestSaveJsonlLog:
    """save_jsonl_log 関数テスト."""

    def test_save_jsonl_log_basic(self, tmp_path: Path) -> None:
        """JSONL ログを保存できること."""
        from src.core.artifacts import save_jsonl_log

        save_jsonl_log(tmp_path, "test_event", {"key": "value"})

        log_path = tmp_path / "events.jsonl"
        assert log_path.exists()

        content = log_path.read_text()
        data = json.loads(content.strip())

        assert data["type"] == "test_event"
        assert data["key"] == "value"
        assert "timestamp" in data

    def test_save_jsonl_log_multiple_events(self, tmp_path: Path) -> None:
        """複数イベントを追記できること."""
        from src.core.artifacts import save_jsonl_log

        save_jsonl_log(tmp_path, "event1", {"n": 1})
        save_jsonl_log(tmp_path, "event2", {"n": 2})
        save_jsonl_log(tmp_path, "event3", {"n": 3})

        log_path = tmp_path / "events.jsonl"
        lines = log_path.read_text().strip().split("\n")

        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "event1"
        assert json.loads(lines[1])["type"] == "event2"
        assert json.loads(lines[2])["type"] == "event3"

    def test_save_jsonl_log_timestamp_format(self, tmp_path: Path) -> None:
        """タイムスタンプが ISO 形式であること."""
        from src.core.artifacts import save_jsonl_log

        save_jsonl_log(tmp_path, "test_event", {})

        content = (tmp_path / "events.jsonl").read_text()
        data = json.loads(content.strip())
        timestamp = data["timestamp"]

        # ISO 形式チェック（簡易）
        assert "T" in timestamp
        assert len(timestamp) >= 19  # YYYY-MM-DDTHH:MM:SS

    def test_save_jsonl_log_utf8_data(self, tmp_path: Path) -> None:
        """UTF-8 データを正しく保存できること."""
        from src.core.artifacts import save_jsonl_log

        save_jsonl_log(tmp_path, "test_event", {"message": "日本語", "emoji": "🎉"})

        content = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        data = json.loads(content.strip())

        assert data["message"] == "日本語"
        assert data["emoji"] == "🎉"

    def test_save_jsonl_log_io_error_does_not_raise(self, tmp_path: Path, capsys: object) -> None:
        """IO エラー時に例外を送出せず、警告を出力すること."""
        from src.core.artifacts import save_jsonl_log

        # 書き込み不可のディレクトリを作成
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()

        # events.jsonl をディレクトリとして作成（書き込みエラーを発生させる）
        (readonly_dir / "events.jsonl").mkdir()

        # Should not raise
        save_jsonl_log(readonly_dir, "test_event", {"key": "value"})

        # 警告が stderr に出力されること
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "Warning" in captured.err or "warning" in captured.err.lower()
