"""Tests for skill frontmatter parser and SkillMetadata (Issue #204)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.errors import SkillFrontmatterError, SkillNotFound
from kaji_harness.skill import SkillMetadata, load_skill_metadata


def _write_skill(tmp_path: Path, name: str, content: str) -> None:
    skill_root = tmp_path / ".claude" / "skills" / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(content, encoding="utf-8")


@pytest.mark.small
class TestSkillMetadataLoader:
    def test_no_frontmatter_returns_exec_script_none(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "plain", "# Just a body\n")
        meta = load_skill_metadata("plain", tmp_path, ".claude/skills")
        assert meta == SkillMetadata(name="plain", description="", exec_script=None)

    def test_frontmatter_without_exec_script(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "foo",
            "---\nname: foo\ndescription: a description\n---\n\nbody\n",
        )
        meta = load_skill_metadata("foo", tmp_path, ".claude/skills")
        assert meta.exec_script is None
        assert meta.name == "foo"
        assert meta.description == "a description"

    def test_valid_exec_script(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "review-poll",
            "---\nname: review-poll\ndescription: d\n"
            "exec_script: kaji_harness.scripts.review_poll_entry\n---\n\nbody\n",
        )
        meta = load_skill_metadata("review-poll", tmp_path, ".claude/skills")
        assert meta.exec_script == "kaji_harness.scripts.review_poll_entry"

    @pytest.mark.parametrize(
        "value",
        [
            "../../etc/passwd",
            "/abs/path",
            "foo; rm -rf /",
            "1foo.bar",  # starts with digit
            "foo..bar",  # double dot
            ".foo",  # leading dot
            "foo bar",  # space
            "foo-bar",  # dash
        ],
    )
    def test_invalid_exec_script_format_raises(self, tmp_path: Path, value: str) -> None:
        _write_skill(tmp_path, "bad", f"---\nname: bad\nexec_script: {value!r}\n---\n")
        with pytest.raises(SkillFrontmatterError):
            load_skill_metadata("bad", tmp_path, ".claude/skills")

    def test_exec_script_empty_string_raises(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "bad", "---\nname: bad\nexec_script: ''\n---\n")
        with pytest.raises(SkillFrontmatterError):
            load_skill_metadata("bad", tmp_path, ".claude/skills")

    def test_exec_script_non_string_raises(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "bad", "---\nname: bad\nexec_script: 42\n---\n")
        with pytest.raises(SkillFrontmatterError):
            load_skill_metadata("bad", tmp_path, ".claude/skills")

    def test_missing_skill_raises_skill_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(SkillNotFound):
            load_skill_metadata("nonexistent", tmp_path, ".claude/skills")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "bad", "---\nname: foo\n  bad: : :\n---\n")
        with pytest.raises(SkillFrontmatterError):
            load_skill_metadata("bad", tmp_path, ".claude/skills")
