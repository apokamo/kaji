"""Skill validation for kaji_harness."""

from pathlib import Path

from .errors import SecurityError, SkillNotFound


def validate_skill_exists(skill_name: str, workdir: Path, skill_dir: str) -> None:
    """CLI 起動前のスキル存在確認（pre-flight check）。

    Args:
        skill_name: スキル名
        workdir: プロジェクトルートディレクトリ
        skill_dir: スキルディレクトリ（workdir からの相対パス）

    Raises:
        SkillNotFound: スキルファイルが見つからない
        SecurityError: パストラバーサル検出
    """
    # パストラバーサル防御（resolve 前にチェック）
    if ".." in skill_name.split("/"):
        raise SecurityError(f"Skill name contains path traversal: {skill_name}")

    base = workdir / skill_dir / skill_name / "SKILL.md"
    resolved = base.resolve()

    if not resolved.is_relative_to(workdir.resolve()):
        raise SecurityError(f"Skill path escapes workdir: {resolved}")

    if not resolved.exists():
        raise SkillNotFound(f"{base} not found")
