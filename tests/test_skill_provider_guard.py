"""Phase 4 commit 7: forge 専用 Skill の Step 0 ガード文言検証。

Medium 静的検証:

- pr-fix / pr-verify / i-pr の SKILL.md に Step 0 provider check が含まれる
- ``[provider_type]`` への分岐句が含まれる
- 手動実行 fallback として ``kaji config provider-type`` の呼び出しが含まれる
- ABORT verdict 例が含まれる
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills"

_FORGE_SKILLS = ("pr-fix", "pr-verify", "i-pr")


@pytest.mark.medium
@pytest.mark.parametrize("skill", _FORGE_SKILLS)
def test_skill_has_step0_provider_check(skill: str) -> None:
    text = (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "Step 0: provider check" in text, f"{skill}: missing 'Step 0: provider check' section"


@pytest.mark.medium
@pytest.mark.parametrize("skill", _FORGE_SKILLS)
def test_skill_has_provider_type_placeholder(skill: str) -> None:
    """``[provider_type]`` がコンテキスト変数表 / 解決手順で参照されている。"""
    text = (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "[provider_type]" in text or "provider_type" in text, (
        f"{skill}: missing reference to provider_type"
    )


@pytest.mark.medium
@pytest.mark.parametrize("skill", _FORGE_SKILLS)
def test_skill_has_kaji_config_provider_type_fallback(skill: str) -> None:
    """手動実行 fallback として ``kaji config provider-type`` を呼ぶ手順がある。"""
    text = (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "kaji config provider-type" in text, (
        f"{skill}: missing 'kaji config provider-type' manual fallback"
    )


@pytest.mark.medium
@pytest.mark.parametrize("skill", _FORGE_SKILLS)
def test_skill_step0_aborts_on_local(skill: str) -> None:
    """Step 0 で ``provider.type='local'`` 時に ABORT verdict を返す指示がある。"""
    text = (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    # ABORT verdict（``status: ABORT`` を Step 0 セクション内で出す）
    assert "status: ABORT" in text, f"{skill}: missing ABORT verdict in Step 0"
    assert "forge-only" in text, f"{skill}: missing 'forge-only' guidance text in Step 0 message"


@pytest.mark.medium
@pytest.mark.parametrize(
    "skill,placeholder",
    [
        # pr-fix / pr-verify は既存 PR を解決して操作するため [pr_id] をコマンド
        # 引数として直接使う。
        ("pr-fix", "[pr_id]"),
        ("pr-fix", "[pr_ref]"),
        ("pr-verify", "[pr_id]"),
        ("pr-verify", "[pr_ref]"),
        # i-pr は PR を新規作成する Skill のため [pr_id] は確定後に shell
        # 変数として保持する（``$pr_id``）。表示用は [pr_ref] / [pr_url] を使う。
        ("i-pr", "[pr_ref]"),
        ("i-pr", "[pr_url]"),
    ],
)
def test_skill_uses_underscore_placeholder(skill: str, placeholder: str) -> None:
    text = (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert placeholder in text, f"{skill}: missing placeholder {placeholder!r}"
