"""Prompt management for Bugfix Agent v5

This module provides:
- PROMPT_DIR: Path to prompts directory
- COMMON_PROMPT_FILE: Path to common prompt file
- REVIEW_PREAMBLE_FILE: Path to Devil's Advocate preamble file
- FOOTER_VERDICT_FILE: Path to VERDICT footer file
- VERDICT_REQUIRED_STATES: States that require VERDICT output
- REVIEW_STATES: States that require Devil's Advocate preamble
- load_prompt: Load and render prompt templates
"""

from pathlib import Path
from string import Template
from typing import Any

# プロンプトディレクトリの定義
# src/bugfix_agent/prompts/ を指す
PROMPT_DIR = Path(__file__).parent / "prompts"
COMMON_PROMPT_FILE = PROMPT_DIR / "_common.md"
REVIEW_PREAMBLE_FILE = PROMPT_DIR / "_review_preamble.md"
FOOTER_VERDICT_FILE = PROMPT_DIR / "_footer_verdict.md"

# VERDICT出力が必要なステート（REVIEWステート + INIT）
VERDICT_REQUIRED_STATES = {
    "init",
    "investigate_review",
    "detail_design_review",
    "implement_review",
}

# REVIEWステート（Devil's Advocate適用対象）
# NOTE: qa_review は v5 で IMPLEMENT_REVIEW に統合されたため除外
#       prompts/qa_review.md は後方互換性のため残存（DEPRECATED）
REVIEW_STATES = {
    "investigate_review",
    "detail_design_review",
    "implement_review",
}


def load_prompt(
    state_name: str,
    *,
    include_common: bool | None = None,
    include_review_preamble: bool | None = None,
    include_footer: bool | None = None,
    **kwargs: Any,
) -> str:
    """ステート用プロンプトをロードしてテンプレート変数を展開

    Args:
        state_name: ステート名（小文字、例: "investigate_review"）
        include_common: 共通プロンプトを含めるか
            - None: VERDICT_REQUIRED_STATESに含まれる場合のみ自動追加
            - True/False: 強制指定
        include_review_preamble: Devil's Advocateプリアンブルを含めるか
            - None: REVIEW_STATESに含まれる場合のみ自動追加
            - True/False: 強制指定
        include_footer: VERDICTフッターを含めるか
            - None: VERDICT_REQUIRED_STATESに含まれる場合のみ自動追加
            - True/False: 強制指定
        **kwargs: テンプレート変数

    Returns:
        展開後のプロンプト文字列

    Raises:
        FileNotFoundError: プロンプトファイルが存在しない場合
        KeyError: 必須テンプレート変数が不足している場合
    """
    prompt_file = PROMPT_DIR / f"{state_name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    state_lower = state_name.lower()

    # 自動判定
    if include_common is None:
        include_common = state_lower in VERDICT_REQUIRED_STATES
    if include_review_preamble is None:
        include_review_preamble = state_lower in REVIEW_STATES
    if include_footer is None:
        include_footer = state_lower in VERDICT_REQUIRED_STATES

    parts: list[str] = []

    # 1. 共通プロンプト（先頭）
    if include_common and COMMON_PROMPT_FILE.exists():
        parts.append(COMMON_PROMPT_FILE.read_text(encoding="utf-8"))
        parts.append("\n---\n\n")

    # 2. REVIEWプリアンブル（Devil's Advocate）
    if include_review_preamble and REVIEW_PREAMBLE_FILE.exists():
        parts.append(REVIEW_PREAMBLE_FILE.read_text(encoding="utf-8"))
        parts.append("\n---\n\n")

    # 3. メインプロンプト
    parts.append(prompt_file.read_text(encoding="utf-8"))

    # 4. フッター（末尾）
    if include_footer and FOOTER_VERDICT_FILE.exists():
        parts.append("\n\n")
        parts.append(FOOTER_VERDICT_FILE.read_text(encoding="utf-8"))

    template = Template("".join(parts))
    return template.substitute(**kwargs)
