"""Skill validation for dao_harness."""

from pathlib import Path

from .errors import SecurityError, SkillNotFound

SKILL_DIRS: dict[str, str] = {
    "claude": ".claude/skills",
    "codex": ".agents/skills",
    "gemini": ".agents/skills",
}


def validate_skill_exists(skill_name: str, agent: str, workdir: Path) -> None:
    """CLI 起動前のスキル存在確認（pre-flight check）。

    Args:
        skill_name: スキル名
        agent: エージェント名 ("claude", "codex", "gemini")
        workdir: プロジェクトルートディレクトリ

    Raises:
        SkillNotFound: スキルファイルが見つからない
        SecurityError: パストラバーサル検出
    """
    skill_dir = SKILL_DIRS.get(agent)
    if skill_dir is None:
        raise SkillNotFound(f"Unknown agent: {agent}")

    # パストラバーサル防御（resolve 前にチェック）
    if ".." in skill_name.split("/"):
        raise SecurityError(f"Skill name contains path traversal: {skill_name}")

    base = workdir / skill_dir / skill_name / "SKILL.md"
    resolved = base.resolve()

    if not resolved.is_relative_to(workdir.resolve()):
        raise SecurityError(f"Skill path escapes workdir: {resolved}")

    if not resolved.exists():
        raise SkillNotFound(f"{base} not found")
