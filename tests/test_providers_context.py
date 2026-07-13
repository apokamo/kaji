"""Tests for IssueContext builders + _mappings.

phase3-design.md § Small / IssueContext builder 全パターン。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.providers._mappings import (
    DEFAULT_BRANCH_PREFIX,
    LABEL_TO_PREFIX,
    labels_to_branch_prefix,
)
from kaji_harness.providers.context import (
    build_branch_name,
    build_design_path,
    build_worktree_dir,
    build_worktree_note_body,
    derive_slug_from_title,
    validate_slug,
)

pytestmark = pytest.mark.small


class TestLabelMapping:
    def test_dict_order_is_priority(self) -> None:
        # type:feature が type:bug より先 → feat 採用
        prefix, fallback = labels_to_branch_prefix(["type:bug", "type:feature"])
        assert prefix == "feat"
        assert fallback is False

    def test_single_label(self) -> None:
        assert labels_to_branch_prefix(["type:docs"]) == ("docs", False)

    def test_no_type_label_falls_back(self) -> None:
        prefix, fallback = labels_to_branch_prefix(["priority:high"])
        assert prefix == DEFAULT_BRANCH_PREFIX
        assert fallback is True

    def test_empty_labels_falls_back(self) -> None:
        prefix, fallback = labels_to_branch_prefix([])
        assert prefix == DEFAULT_BRANCH_PREFIX
        assert fallback is True

    def test_all_known_types_present(self) -> None:
        # phase3-design.md § branch_prefix mapping の正本化 で挙げた 8 種
        assert set(LABEL_TO_PREFIX.values()) >= {
            "feat",
            "fix",
            "refactor",
            "docs",
            "test",
            "chore",
            "perf",
            "security",
        }


class TestSlug:
    def test_validate_slug_ok(self) -> None:
        validate_slug("foo-bar")
        validate_slug("a")
        validate_slug("a" * 40)

    def test_validate_slug_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError):
            validate_slug("Foo")

    def test_validate_slug_rejects_leading_hyphen(self) -> None:
        with pytest.raises(ValueError):
            validate_slug("-foo")

    def test_validate_slug_rejects_too_long(self) -> None:
        with pytest.raises(ValueError):
            validate_slug("a" * 41)

    def test_derive_slug_lowercases_and_separates(self) -> None:
        assert (
            derive_slug_from_title("Add Feature: Local Provider!") == "add-feature-local-provider"
        )

    def test_derive_slug_compresses_repeats(self) -> None:
        assert derive_slug_from_title("foo --- bar") == "foo-bar"

    def test_derive_slug_trims_to_40_no_trailing_hyphen(self) -> None:
        long = "a" * 50
        assert derive_slug_from_title(long) == "a" * 40

    def test_derive_slug_empty_yields_untitled(self) -> None:
        assert derive_slug_from_title("!!!") == "untitled"
        assert derive_slug_from_title("") == "untitled"


class TestPathBuilders:
    def test_branch_name(self) -> None:
        assert build_branch_name("feat", "153") == "feat/153"
        assert build_branch_name("fix", "local-pc1-3") == "fix/local-pc1-3"

    def test_worktree_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "main"
        repo.mkdir()
        result = build_worktree_dir("feat", "153", repo)
        assert result == str(tmp_path / "kaji-feat-153")

    def test_design_path(self) -> None:
        assert build_design_path("153", "auth") == "draft/design/issue-153-auth.md"
        assert (
            build_design_path("local-pc1-3", "local-mode")
            == "draft/design/issue-local-pc1-3-local-mode.md"
        )


class TestBuildWorktreeNoteBody:
    def test_blank_line_guaranteed_between_note_and_heading(self) -> None:
        result = build_worktree_note_body(
            "## 概要\n\n本文",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert "> **Branch**: `fix/200`\n\n## 概要" in result
        assert "`fix/200`## 概要" not in result

    def test_full_note_block_layout(self) -> None:
        result = build_worktree_note_body(
            "## 概要",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result == (
            "> [!NOTE]\n> **Worktree**: `../kaji-fix-200`\n> **Branch**: `fix/200`\n\n## 概要"
        )

    def test_leading_blank_lines_normalized_to_one(self) -> None:
        result = build_worktree_note_body(
            "\n\n\n## 概要",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert "> **Branch**: `fix/200`\n\n## 概要" in result
        assert "`fix/200`\n\n\n" not in result

    @pytest.mark.parametrize("body", ["", "\n\n"])
    def test_empty_body_produces_note_only(self, body: str) -> None:
        result = build_worktree_note_body(
            body,
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result == ("> [!NOTE]\n> **Worktree**: `../kaji-fix-200`\n> **Branch**: `fix/200`\n")

    def test_special_chars_in_body_preserved(self) -> None:
        body = "## 概要\n\n`code` と $VAR を含む `inline`\n- 行 2"
        result = build_worktree_note_body(
            body,
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result.endswith(body)
