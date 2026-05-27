"""Skill validation and metadata loading for kaji_harness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import SecurityError, SkillFrontmatterError, SkillNotFound

# Python identifier dotted path (`foo.bar.baz`). Used to gate `exec_script`
# values against shell injection / path traversal at the syntax level.
_EXEC_SCRIPT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class SkillMetadata:
    """SKILL.md frontmatter から抽出した skill メタデータ。"""

    name: str
    description: str
    exec_script: str | None


def validate_skill_exists(skill_name: str, workdir: Path, skill_dir: str) -> Path:
    """CLI 起動前のスキル存在確認（pre-flight check）。

    Args:
        skill_name: スキル名
        workdir: プロジェクトルートディレクトリ
        skill_dir: スキルディレクトリ（workdir からの相対パス）

    Returns:
        Path: 解決済みの SKILL.md 絶対パス（load_skill_metadata で再利用）。

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

    return resolved


def load_skill_metadata(skill_name: str, workdir: Path, skill_dir: str) -> SkillMetadata:
    """SKILL.md frontmatter を読み込み SkillMetadata を返す。

    frontmatter が存在しない / `exec_script` が無い場合は ``exec_script=None``
    の ``SkillMetadata`` を返す。``exec_script`` が含まれる場合は Python
    identifier dotted path 形式かを検証する（path traversal / shell metachar 等を
    構文段階で遮断するため）。

    Args:
        skill_name: スキル名
        workdir: プロジェクトルートディレクトリ
        skill_dir: スキルディレクトリ（workdir からの相対パス）

    Returns:
        SkillMetadata

    Raises:
        SkillNotFound: SKILL.md が存在しない
        SecurityError: スキル名にパストラバーサル
        SkillFrontmatterError: frontmatter の YAML parse 失敗 / `exec_script`
            形式違反
    """
    skill_path = validate_skill_exists(skill_name, workdir, skill_dir)
    content = skill_path.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        # frontmatter なし → metadata 不在として扱う
        return SkillMetadata(name=skill_name, description="", exec_script=None)

    try:
        data: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillFrontmatterError(skill_name, f"YAML parse error: {exc}") from exc

    if data is None:
        return SkillMetadata(name=skill_name, description="", exec_script=None)
    if not isinstance(data, dict):
        raise SkillFrontmatterError(
            skill_name, f"frontmatter must be a mapping, got {type(data).__name__}"
        )

    exec_script = data.get("exec_script")
    if exec_script is not None:
        if not isinstance(exec_script, str) or not exec_script:
            raise SkillFrontmatterError(
                skill_name,
                f"'exec_script' must be a non-empty string, got {exec_script!r}",
            )
        if not _EXEC_SCRIPT_RE.match(exec_script):
            raise SkillFrontmatterError(
                skill_name,
                f"'exec_script' {exec_script!r} is not a valid Python dotted "
                "module path (must match [A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_]"
                "[A-Za-z0-9_]*)*)",
            )

    name = data.get("name", skill_name)
    description = data.get("description", "")
    if not isinstance(name, str):
        raise SkillFrontmatterError(
            skill_name, f"'name' must be a string, got {type(name).__name__}"
        )
    if not isinstance(description, str):
        raise SkillFrontmatterError(
            skill_name,
            f"'description' must be a string, got {type(description).__name__}",
        )

    return SkillMetadata(name=name, description=description, exec_script=exec_script)
