"""Tests for skill existence validation.

Covers validate_skill_exists: agent-specific directory resolution,
not-found error, and path traversal security checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dao_harness.errors import SecurityError, SkillNotFound
from dao_harness.skill import validate_skill_exists

# ============================================================
# Helper: create skill directory structure in tmp_path
# ============================================================


def _create_skill(tmp_path: Path, skill_dir: str, skill_name: str) -> None:
    """Create a SKILL.md file under the given skill directory."""
    skill_path = tmp_path / skill_dir / skill_name
    skill_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")


# ============================================================
# 1. Skill exists for claude agent → no error
# ============================================================


@pytest.mark.small
class TestSkillExistsClaude:
    """validate_skill_exists succeeds for claude when skill file is present."""

    def test_claude_skill_found(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, ".claude/skills", "my-skill")

        validate_skill_exists("my-skill", "claude", tmp_path)


# ============================================================
# 2. Skill exists for codex agent → no error
# ============================================================


@pytest.mark.small
class TestSkillExistsCodex:
    """validate_skill_exists succeeds for codex when skill file is present."""

    def test_codex_skill_found(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, ".agents/skills", "my-skill")

        validate_skill_exists("my-skill", "codex", tmp_path)


# ============================================================
# 3. Skill exists for gemini agent → no error
# ============================================================


@pytest.mark.small
class TestSkillExistsGemini:
    """validate_skill_exists succeeds for gemini when skill file is present."""

    def test_gemini_skill_found(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, ".agents/skills", "my-skill")

        validate_skill_exists("my-skill", "gemini", tmp_path)


# ============================================================
# 4. Skill not found → SkillNotFound
# ============================================================


@pytest.mark.small
class TestSkillNotFound:
    """validate_skill_exists raises SkillNotFound when the skill does not exist."""

    def test_missing_skill_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SkillNotFound):
            validate_skill_exists("nonexistent-skill", "claude", tmp_path)


# ============================================================
# 5. Path traversal attempt → SecurityError
# ============================================================


@pytest.mark.small
class TestPathTraversalPasswd:
    """Path traversal with ../../etc/passwd raises SecurityError."""

    def test_path_traversal_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError):
            validate_skill_exists("../../etc/passwd", "claude", tmp_path)


# ============================================================
# 6. Path traversal with .. in skill name → SecurityError
# ============================================================


@pytest.mark.small
class TestPathTraversalDotDot:
    """Skill name containing '..' raises SecurityError."""

    def test_dotdot_in_skill_name_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError):
            validate_skill_exists("../secret-skill", "claude", tmp_path)
