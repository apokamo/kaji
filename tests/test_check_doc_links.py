"""Tests for scripts/check_doc_links.py — Markdown link validator."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_links.py"

# Repo root for Large tests
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    """Import check_doc_links.py as a module without sys.path mutation."""
    spec = importlib.util.spec_from_file_location("check_doc_links", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
_is_external = _mod._is_external
_is_hidden = _mod._is_hidden
_index_to_line = _mod._index_to_line
_slugify = _mod._slugify


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


@pytest.mark.small
class TestSlugify:
    """_slugify: heading text → GitHub-compatible anchor slug."""

    def test_basic_heading(self) -> None:
        counts: dict[str, int] = {}
        assert _slugify("Hello World", counts) == "hello-world"

    def test_punctuation_removed(self) -> None:
        counts: dict[str, int] = {}
        assert _slugify("What's New?", counts) == "whats-new"

    def test_duplicate_headings_get_suffix(self) -> None:
        counts: dict[str, int] = {}
        assert _slugify("Section", counts) == "section"
        assert _slugify("Section", counts) == "section-1"
        assert _slugify("Section", counts) == "section-2"

    def test_empty_after_strip_becomes_section(self) -> None:
        counts: dict[str, int] = {}
        assert _slugify("***", counts) == "section"

    def test_japanese_heading(self) -> None:
        counts: dict[str, int] = {}
        result = _slugify("テスト戦略", counts)
        assert result == "テスト戦略"

    def test_mixed_case_lowered(self) -> None:
        counts: dict[str, int] = {}
        assert _slugify("CamelCase Title", counts) == "camelcase-title"


@pytest.mark.small
class TestIsExternal:
    """_is_external: detect external URLs."""

    def test_https(self) -> None:
        assert _is_external("https://example.com") is True

    def test_http(self) -> None:
        assert _is_external("http://example.com") is True

    def test_mailto(self) -> None:
        assert _is_external("mailto:a@b.com") is True

    def test_relative_path(self) -> None:
        assert _is_external("../foo.md") is False

    def test_absolute_path(self) -> None:
        assert _is_external("/docs/foo.md") is False


@pytest.mark.small
class TestIsHidden:
    """_is_hidden: detect paths with hidden directory components."""

    def test_hidden_directory(self) -> None:
        assert _is_hidden(Path(".git/config")) is True

    def test_normal_path(self) -> None:
        assert _is_hidden(Path("docs/guide.md")) is False

    def test_hidden_file(self) -> None:
        assert _is_hidden(Path("docs/.hidden.md")) is True


@pytest.mark.small
class TestIndexToLine:
    """_index_to_line: character index → line number."""

    def test_first_line(self) -> None:
        lines = ["hello", "world"]
        assert _index_to_line(0, lines) == 1

    def test_second_line(self) -> None:
        lines = ["hello", "world"]
        assert _index_to_line(6, lines) == 2

    def test_end_of_content(self) -> None:
        lines = ["abc", "def", "ghi"]
        assert _index_to_line(8, lines) == 3


# ===========================================================================
# Medium tests — file I/O via subprocess + tmp_path
# ===========================================================================


@pytest.mark.medium
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


@pytest.mark.medium
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


@pytest.mark.medium
class TestRepoRootBoundary:
    """Links resolving outside repo root should fail."""

    def test_link_escaping_repo_root(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](../../outside/secret.md)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1
        assert "outside repository" in result.stderr

    def test_parent_within_repo_is_ok(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "sub" / "a.md", "[link](../b.md)\n")
        _write(tmp_path / "docs" / "b.md", "# B\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 0


@pytest.mark.medium
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


@pytest.mark.medium
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


@pytest.mark.medium
class TestCLIArguments:
    """CLI argument handling."""

    def test_no_args_checks_docs_dir(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "[link](b.md)\n")
        _write(tmp_path / "docs" / "b.md", "# B\n")
        result = _run(tmp_path)
        assert result.returncode == 0

    def test_specific_file_argument(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "good.md", "[link](other.md)\n")
        _write(tmp_path / "docs" / "other.md", "# Other\n")
        _write(tmp_path / "docs" / "bad.md", "[link](missing.md)\n")
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


@pytest.mark.medium
class TestHiddenDirectoryArgument:
    """Explicitly-passed hidden directories should be traversed."""

    def test_hidden_dir_argument_collects_files(self, tmp_path: Path) -> None:
        """Files under an explicitly-passed dot-prefixed directory are checked."""
        _write(tmp_path / ".claude" / "skills" / "a.md", "# Skill A\n")
        _write(tmp_path / ".claude" / "skills" / "b.md", "# Skill B\n")
        result = _run(tmp_path, ".claude/skills/")
        assert result.returncode == 0
        assert "2 file(s) checked" in result.stdout

    def test_hidden_dir_detects_broken_links(self, tmp_path: Path) -> None:
        """Broken links inside hidden directory arguments are reported."""
        _write(tmp_path / ".claude" / "skills" / "a.md", "[link](missing.md)\n")
        result = _run(tmp_path, ".claude/skills/")
        assert result.returncode == 1
        assert "missing.md" in result.stderr

    def test_hidden_subdir_inside_hidden_dir_excluded(self, tmp_path: Path) -> None:
        """Hidden subdirectories within the traversal are still excluded."""
        _write(tmp_path / ".claude" / "skills" / "a.md", "# OK\n")
        _write(tmp_path / ".claude" / "skills" / ".draft" / "b.md", "# Hidden\n")
        result = _run(tmp_path, ".claude/skills/")
        assert result.returncode == 0
        assert "1 file(s) checked" in result.stdout


@pytest.mark.medium
class TestErrorFormat:
    """Error output should include file path and line number."""

    def test_error_includes_file_and_line(self, tmp_path: Path) -> None:
        _write(tmp_path / "docs" / "a.md", "line one\n[link](missing.md)\n")
        result = _run(tmp_path, "docs")
        assert result.returncode == 1
        assert "a.md:2:" in result.stderr


@pytest.mark.medium
class TestEdgeCases:
    """Edge cases."""

    def test_image_links_skipped(self, tmp_path: Path) -> None:
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


# ===========================================================================
# Large tests — E2E against real repository docs
# ===========================================================================


@pytest.mark.large
class TestRealRepo:
    """Run link checker against the actual repository docs."""

    def test_repo_docs_have_no_broken_links(self) -> None:
        """E2E: check_doc_links.py against the real docs/ directory."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Broken links found in repo docs:\n{result.stderr}"

    def test_repo_readme_has_no_broken_links(self) -> None:
        """E2E: check_doc_links.py against the real README.md."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "README.md"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Broken links found in README.md:\n{result.stderr}"
