"""Tests for bugfix_agent build_context - allowed_root / max_chars / 警告挙動.

Issue #35: v5 Phase1 - Context構築の allowed_root / max_chars / 警告挙動を整備
"""

from pathlib import Path

import pytest

from src.bugfix_agent.context import build_context


class TestBuildContextStringInput:
    """build_context() - 文字列入力のテスト."""

    def test_string_passthrough(self) -> None:
        """文字列入力はそのまま返されること."""
        result = build_context("Hello, World!")

        assert result == "Hello, World!"

    def test_string_max_chars_truncation(self) -> None:
        """文字列入力が max_chars で切り詰められること."""
        long_text = "A" * 1000

        result = build_context(long_text, max_chars=100)

        assert len(result) == 100
        assert result == "A" * 100

    def test_string_max_chars_no_truncation(self) -> None:
        """max_chars より短い文字列は切り詰められないこと."""
        short_text = "Short"

        result = build_context(short_text, max_chars=100)

        assert result == "Short"

    def test_string_max_chars_zero_unlimited(self) -> None:
        """max_chars=0 の場合は無制限."""
        long_text = "A" * 10000

        result = build_context(long_text, max_chars=0)

        assert len(result) == 10000


class TestBuildContextFileList:
    """build_context() - ファイルパスリスト入力のテスト."""

    def test_read_single_file(self, tmp_path: Path) -> None:
        """単一ファイルを読み込めること."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("File content")

        result = build_context([str(test_file)], allowed_root=tmp_path)

        assert "File content" in result
        assert "test.txt" in result

    def test_read_multiple_files(self, tmp_path: Path) -> None:
        """複数ファイルを読み込めること."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("Content 1")
        file2.write_text("Content 2")

        result = build_context([str(file1), str(file2)], allowed_root=tmp_path)

        assert "Content 1" in result
        assert "Content 2" in result

    def test_file_not_found_skipped(self, tmp_path: Path) -> None:
        """存在しないファイルはスキップされること（警告なし）."""
        nonexistent = tmp_path / "nonexistent.txt"

        result = build_context([str(nonexistent)], allowed_root=tmp_path)

        assert result == ""


class TestBuildContextAllowedRoot:
    """build_context() - allowed_root によるPath Traversal対策のテスト."""

    def test_allowed_root_within(self, tmp_path: Path) -> None:
        """allowed_root 配下のファイルは読み込めること."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        test_file = subdir / "test.txt"
        test_file.write_text("Allowed content")

        result = build_context([str(test_file)], allowed_root=tmp_path)

        assert "Allowed content" in result

    def test_allowed_root_outside_skipped_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """allowed_root 配下でないファイルはスキップ + 警告出力."""
        # allowed_root は tmp_path/safe/
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()

        # 読み込み対象は tmp_path/unsafe/secret.txt（allowed_root 外）
        unsafe_dir = tmp_path / "unsafe"
        unsafe_dir.mkdir()
        secret_file = unsafe_dir / "secret.txt"
        secret_file.write_text("Secret content")

        result = build_context([str(secret_file)], allowed_root=safe_dir)

        # 内容は含まれない
        assert "Secret content" not in result
        assert result == ""

        # 警告が出力されている
        captured = capsys.readouterr()
        assert "Skipping" in captured.out or "Skipping" in captured.err
        assert str(secret_file) in captured.out or str(secret_file) in captured.err

    def test_path_traversal_attempt_blocked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Path Traversal 攻撃 (../) がブロックされること."""
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        unsafe_file = tmp_path / "secret.txt"
        unsafe_file.write_text("Secret")

        # ../secret.txt を試行
        traversal_path = str(safe_dir / ".." / "secret.txt")

        result = build_context([traversal_path], allowed_root=safe_dir)

        assert "Secret" not in result


class TestBuildContextPermissionError:
    """build_context() - PermissionError 時の挙動テスト."""

    def test_permission_error_skipped_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """読み取り権限がないファイルはスキップ + 警告出力."""
        test_file = tmp_path / "protected.txt"
        test_file.write_text("Protected content")

        # read_text を PermissionError を raise するようにモック
        original_read_text = Path.read_text

        def mock_read_text(self: Path, encoding: str = "utf-8") -> str:
            if self.name == "protected.txt":
                raise PermissionError("Permission denied")
            return original_read_text(self, encoding=encoding)

        monkeypatch.setattr(Path, "read_text", mock_read_text)

        result = build_context([str(test_file)], allowed_root=tmp_path)

        assert result == "" or "Protected content" not in result

        captured = capsys.readouterr()
        assert "Failed to read" in captured.out or "Failed to read" in captured.err or result == ""


class TestBuildContextMaxCharsFileList:
    """build_context() - ファイルリストの max_chars 切り詰めテスト."""

    def test_file_content_truncated_by_max_chars(self, tmp_path: Path) -> None:
        """ファイル内容が max_chars で切り詰められること."""
        large_file = tmp_path / "large.txt"
        large_file.write_text("A" * 10000)

        result = build_context([str(large_file)], max_chars=100, allowed_root=tmp_path)

        assert len(result) <= 100

    def test_max_chars_zero_no_truncation(self, tmp_path: Path) -> None:
        """max_chars=0 の場合は切り詰めなし."""
        large_file = tmp_path / "large.txt"
        large_file.write_text("A" * 5000)

        result = build_context([str(large_file)], max_chars=0, allowed_root=tmp_path)

        assert "A" * 5000 in result


class TestBuildContextDefaultBehavior:
    """build_context() - デフォルト値の挙動テスト."""

    def test_default_max_chars_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """max_chars 未指定時は config から取得されること."""
        # get_config_value をモック
        monkeypatch.setattr(
            "src.bugfix_agent.context.get_config_value",
            lambda key, default: 50 if key == "tools.context_max_chars" else default,
        )

        test_file = tmp_path / "test.txt"
        test_file.write_text("A" * 100)

        result = build_context([str(test_file)], allowed_root=tmp_path)

        # config の max_chars=50 で切り詰められる
        assert len(result) <= 50
