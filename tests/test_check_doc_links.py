"""Tests for scripts/check_doc_links.py — Markdown link validator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_links.py"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run check_doc_links.py with given args, using tmp_path as working dir."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# Basic link resolution
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestValidLinks:
    """Valid relative links should pass."""

    def test_relative_link_to_existing_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](b.md)\n")
        _write(tmp_path / "docs" / "b.md", "# B\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_relative_link_to_subdirectory_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "index.md", "[link](sub/page.md)\n")
        _write(tmp_path / "docs" / "sub" / "page.md", "# Page\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_parent_directory_link(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "sub" / "page.md", "[link](../index.md)\n")
        _write(tmp_path / "docs" / "index.md", "# Index\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0


@pytest.mark.small
class TestBrokenLinks:
    """Broken relative links should fail."""

    def test_link_to_nonexistent_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](nonexistent.md)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1
        assert "nonexistent.md" in result.stderr

    def test_link_to_nonexistent_subdirectory(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](no/such/file.md)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# Anchor (heading) validation
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestAnchors:
    """Fragment identifiers (#heading) should be validated."""

    def test_valid_anchor(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](b.md#section-one)\n")
        _write(tmp_path / "docs" / "b.md", "# Section One\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_invalid_anchor(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](b.md#nonexistent)\n")
        _write(tmp_path / "docs" / "b.md", "# Section One\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1
        assert "nonexistent" in result.stderr

    def test_self_anchor(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "# My Heading\n\n[link](#my-heading)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_invalid_self_anchor(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "# My Heading\n\n[link](#wrong)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# External links (should be skipped)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestExternalLinks:
    """External links should be skipped, not validated."""

    def test_https_link_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](https://example.com)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_mailto_link_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[email](mailto:a@b.com)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# CLI: directory and file arguments
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestCLIArguments:
    """CLI argument handling."""

    def test_no_args_checks_docs_dir(self, tmp_path: Path) -> None:
        """With no args, should check docs/ directory."""
        _write(tmp_path / "docs" / "a.md", "[link](b.md)\n")
        _write(tmp_path / "docs" / "b.md", "# B\n")
        result = _run(tmp_path)
        assert result.returncode == 0

    def test_specific_file_argument(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "good.md", "[link](other.md)\n")
        _write(tmp_path / "docs" / "other.md", "# Other\n")
        _write(tmp_path / "docs" / "bad.md", "[link](missing.md)\n")
        # Only check good.md — should pass
        result = _run(tmp_path, "docs/good.md")
        assert result.returncode == 0

    def test_specific_file_with_error(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "bad.md", "[link](missing.md)\n")
        result = _run(tmp_path, "docs/bad.md")
        assert result.returncode == 1

    def test_directory_argument(self, tmp_path: Path) -> None:
        _write(tmp_path / "mydir" / "a.md", "[link](b.md)\n")
        _write(tmp_path / "mydir" / "b.md", "# B\n")
        result = _run(tmp_path, "mydir")
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Error output format
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestErrorFormat:
    """Error output should include file path and line number."""

    def test_error_includes_file_and_line(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "line one\n[link](missing.md)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1
        # Should contain "file:line: message" format
        assert "a.md:2:" in result.stderr


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestEdgeCases:
    """Edge cases."""

    def test_image_links_skipped(self, tmp_path: Path) -> None:
        """Image links (![alt](path)) should not be validated as doc links."""
        _write(tmp_path / "docs" / "a.md", "![image](nonexistent.png)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_empty_docs_dir(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        result = _run(tmp_path, "docs")
        assert result.returncode == 0

    def test_no_links_in_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "Just text, no links.\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0
