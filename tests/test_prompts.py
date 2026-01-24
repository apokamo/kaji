"""Tests for prompts module - プロンプトロード・テンプレート展開."""

from pathlib import Path

import pytest


class TestExtractTemplateVariables:
    """extract_template_variables 関数テスト."""

    def test_extract_single_variable(self) -> None:
        """単一変数を抽出できること."""
        from src.core.prompts import extract_template_variables

        text = "Hello ${name}"
        result = extract_template_variables(text)

        assert result == {"name"}

    def test_extract_multiple_variables(self) -> None:
        """複数変数を抽出できること."""
        from src.core.prompts import extract_template_variables

        text = "Issue ${issue_url} has ${issue_body}"
        result = extract_template_variables(text)

        assert result == {"issue_url", "issue_body"}

    def test_extract_no_variables(self) -> None:
        """変数がない場合は空セットを返すこと."""
        from src.core.prompts import extract_template_variables

        text = "Plain text without variables"
        result = extract_template_variables(text)

        assert result == set()

    def test_ignore_escaped_variables(self) -> None:
        """エスケープ済み変数（$$）を除外すること."""
        from src.core.prompts import extract_template_variables

        text = "Real ${var} and escaped $${literal}"
        result = extract_template_variables(text)

        # $${literal} は除外され、${var} のみ
        assert result == {"var"}

    def test_extract_duplicate_variables(self) -> None:
        """重複変数は1つにまとめられること."""
        from src.core.prompts import extract_template_variables

        text = "${name} and ${name} again"
        result = extract_template_variables(text)

        assert result == {"name"}


class TestLoadPrompt:
    """load_prompt 関数テスト."""

    def test_load_prompt_with_variables(self, tmp_path: Path) -> None:
        """変数を展開してプロンプトをロードできること."""
        from src.core.prompts import _load_prompt_from_path

        # テスト用プロンプトファイルを作成
        prompt_content = "Issue: ${issue_url}\nBody: ${issue_body}"
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        # 直接ファイルパスでテスト
        result = _load_prompt_from_path(
            prompt_file,
            issue_url="https://github.com/test/repo/issues/1",
            issue_body="Test body",
        )

        assert "Issue: https://github.com/test/repo/issues/1" in result
        assert "Body: Test body" in result

    def test_load_prompt_file_not_found(self) -> None:
        """存在しないファイルで PromptLoadError が発生すること."""
        from src.core.prompts import PromptLoadError, load_prompt

        with pytest.raises(PromptLoadError, match="not found"):
            load_prompt("nonexistent/path.md")

    def test_load_prompt_missing_required_vars(self, tmp_path: Path) -> None:
        """必須変数が欠落した場合 PromptLoadError が発生すること."""
        from src.core.prompts import PromptLoadError, _load_prompt_from_path

        prompt_content = "Issue: ${issue_url}"
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        with pytest.raises(PromptLoadError, match="Missing required"):
            _load_prompt_from_path(
                prompt_file,
                required_vars=["issue_url", "issue_body"],
                issue_url="http://example.com",
                # issue_body が欠落
            )

    def test_load_prompt_required_vars_empty_string(self, tmp_path: Path) -> None:
        """必須変数が空文字列の場合 PromptLoadError が発生すること."""
        from src.core.prompts import PromptLoadError, _load_prompt_from_path

        prompt_content = "Issue: ${issue_url}"
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        with pytest.raises(PromptLoadError, match="Missing required"):
            _load_prompt_from_path(
                prompt_file,
                required_vars=["issue_url"],
                issue_url="",  # 空文字列
            )

    def test_load_prompt_safe_substitute(self, tmp_path: Path) -> None:
        """未定義変数は ${var} のまま残ること（safe_substitute）."""
        from src.core.prompts import _load_prompt_from_path

        prompt_content = "Defined: ${defined_var}, Undefined: ${undefined_var}"
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        result = _load_prompt_from_path(prompt_file, defined_var="value")

        assert "Defined: value" in result
        assert "${undefined_var}" in result  # 残る

    def test_load_prompt_escaped_dollar(self, tmp_path: Path) -> None:
        """$$ でエスケープした $ がリテラルとして残ること."""
        from src.core.prompts import _load_prompt_from_path

        prompt_content = "Variable: ${var}, Literal: $${literal}"
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        result = _load_prompt_from_path(prompt_file, var="value")

        assert "Variable: value" in result
        assert "${literal}" in result  # $$ → $ として展開


class TestSummarizeForPrompt:
    """summarize_for_prompt 関数テスト."""

    def test_short_content_unchanged(self) -> None:
        """短いコンテンツはそのまま返すこと."""
        from src.core.prompts import summarize_for_prompt

        content = "Short content"
        result = summarize_for_prompt(content, max_length=1000)

        assert result == content

    def test_content_at_limit_unchanged(self) -> None:
        """ちょうど上限のコンテンツはそのまま返すこと."""
        from src.core.prompts import summarize_for_prompt

        content = "x" * 100
        result = summarize_for_prompt(content, max_length=100)

        assert result == content

    def test_long_content_summarized(self) -> None:
        """長いコンテンツは先頭・末尾抽出でサマリ化すること."""
        from src.core.prompts import summarize_for_prompt

        content = "A" * 200
        result = summarize_for_prompt(content, max_length=100)

        # 先頭と末尾が含まれ、省略表記がある
        assert result.startswith("A")
        assert result.endswith("A")
        assert "中略" in result or "truncated" in result.lower()
        assert len(result) < len(content)

    def test_default_max_length(self) -> None:
        """デフォルトの max_length が適用されること."""
        from src.core.prompts import MAX_INLINE_CONTENT_LENGTH, summarize_for_prompt

        # デフォルト上限以下
        short_content = "x" * 100
        assert summarize_for_prompt(short_content) == short_content

        # デフォルト上限超過
        long_content = "x" * (MAX_INLINE_CONTENT_LENGTH + 1000)
        result = summarize_for_prompt(long_content)
        assert len(result) < len(long_content)

    def test_full_content_path_included_in_summary(self) -> None:
        """full_content_path が省略メッセージに含まれること."""
        from src.core.prompts import summarize_for_prompt

        content = "A" * 200
        result = summarize_for_prompt(
            content, max_length=100, full_content_path="/path/to/design.md"
        )

        assert "/path/to/design.md" in result

    def test_default_path_message_when_no_path(self) -> None:
        """full_content_path が未指定の場合はデフォルトメッセージが使われること."""
        from src.core.prompts import summarize_for_prompt

        content = "A" * 200
        result = summarize_for_prompt(content, max_length=100)

        assert "the full document" in result
