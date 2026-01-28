"""Tests for bugfix_agent prompts - プロンプト移植チェックリスト検証.

設計書のチェックリストに基づき、各プロンプトファイルの必須要素を検証する。
"""

import pytest


class TestPromptDirectoryExists:
    """プロンプトディレクトリの存在確認."""

    def test_prompts_directory_exists(self) -> None:
        """src/bugfix_agent/prompts/ ディレクトリが存在すること."""
        from src.bugfix_agent.prompts import PROMPT_DIR

        assert PROMPT_DIR.exists(), f"PROMPT_DIR does not exist: {PROMPT_DIR}"
        assert PROMPT_DIR.is_dir(), f"PROMPT_DIR is not a directory: {PROMPT_DIR}"


class TestDetailDesignPrompt:
    """detail_design.md のチェックリスト検証."""

    @pytest.fixture
    def prompt_content(self) -> str:
        """detail_design.md の内容を取得."""
        from src.bugfix_agent.prompts import PROMPT_DIR

        prompt_file = PROMPT_DIR / "detail_design.md"
        assert prompt_file.exists(), f"detail_design.md not found: {prompt_file}"
        return prompt_file.read_text(encoding="utf-8")

    def test_has_output_format_section(self, prompt_content: str) -> None:
        """出力形式セクション（マークダウン構造指定）が存在すること."""
        assert "## 出力形式" in prompt_content or "## Output" in prompt_content.lower()
        # マークダウン構造の指定（コードブロック内にフォーマット例）
        assert "```" in prompt_content

    def test_has_template_variables(self, prompt_content: str) -> None:
        """テンプレート変数が含まれること: issue_url, artifacts_dir."""
        assert "${issue_url}" in prompt_content, "Missing ${issue_url}"
        assert "${artifacts_dir}" in prompt_content, "Missing ${artifacts_dir}"

    def test_has_issue_update_method(self, prompt_content: str) -> None:
        """Issue更新方法セクション（Loop=1 vs Loop>=2 の分岐）が存在すること."""
        # Loop=1 と Loop>=2 の分岐説明
        assert "Loop=1" in prompt_content or "初回" in prompt_content
        assert "Loop>=2" in prompt_content or "2回目" in prompt_content
        # Issue 更新に関する説明
        assert "gh issue" in prompt_content or "Issue 更新" in prompt_content


class TestDetailDesignReviewPrompt:
    """detail_design_review.md のチェックリスト検証."""

    @pytest.fixture
    def prompt_content(self) -> str:
        """detail_design_review.md の内容を取得."""
        from src.bugfix_agent.prompts import PROMPT_DIR

        prompt_file = PROMPT_DIR / "detail_design_review.md"
        assert prompt_file.exists(), f"detail_design_review.md not found: {prompt_file}"
        return prompt_file.read_text(encoding="utf-8")

    def test_has_completion_checklist(self, prompt_content: str) -> None:
        """完了条件チェックリスト（4項目の表形式）が存在すること."""
        # チェックリストセクション
        assert "チェックリスト" in prompt_content or "checklist" in prompt_content.lower()
        # 表形式（| で区切られた行）
        assert "|" in prompt_content
        # 4項目: 変更計画、実装手順、テストケース、補足
        assert "変更計画" in prompt_content
        assert "実装手順" in prompt_content
        assert "テストケース" in prompt_content
        assert "補足" in prompt_content

    def test_has_prohibited_actions(self, prompt_content: str) -> None:
        """禁止事項セクション（次ステート責務の実行禁止）が存在すること."""
        assert "禁止事項" in prompt_content or "Prohibited" in prompt_content
        # 次ステートの責務に関する禁止
        assert "実装" in prompt_content or "implement" in prompt_content.lower()

    def test_has_verdict_format(self, prompt_content: str) -> None:
        """VERDICT出力形式が存在すること."""
        assert "## VERDICT" in prompt_content or "VERDICT" in prompt_content
        assert "Result:" in prompt_content
        assert "PASS" in prompt_content
        assert "RETRY" in prompt_content

    def test_has_judgment_guideline(self, prompt_content: str) -> None:
        """判定ガイドライン（PASS→IMPLEMENT / RETRY→DETAIL_DESIGN）が存在すること."""
        assert "判定" in prompt_content or "ガイドライン" in prompt_content
        # PASS と RETRY の遷移先
        assert "IMPLEMENT" in prompt_content
        assert "DETAIL_DESIGN" in prompt_content or "設計" in prompt_content


class TestCommonPrompt:
    """_common.md のチェックリスト検証."""

    @pytest.fixture
    def prompt_content(self) -> str:
        """_common.md の内容を取得."""
        from src.bugfix_agent.prompts import COMMON_PROMPT_FILE

        assert COMMON_PROMPT_FILE.exists(), f"_common.md not found: {COMMON_PROMPT_FILE}"
        return COMMON_PROMPT_FILE.read_text(encoding="utf-8")

    def test_has_output_format_verdict(self, prompt_content: str) -> None:
        """Output Format (VERDICT) セクションが存在すること."""
        assert "VERDICT" in prompt_content
        # フォーマット指定（Result 行など）
        assert "Result:" in prompt_content

    def test_has_status_keywords(self, prompt_content: str) -> None:
        """Status Keywords (PASS/RETRY/BACK_DESIGN/ABORT) が定義されていること."""
        assert "PASS" in prompt_content
        assert "RETRY" in prompt_content
        assert "BACK_DESIGN" in prompt_content
        assert "ABORT" in prompt_content

    def test_has_abort_conditions(self, prompt_content: str) -> None:
        """ABORT Conditions が記載されていること."""
        # ABORT に関する説明
        assert "ABORT" in prompt_content
        # ABORT の条件・使用方法
        assert "緊急" in prompt_content or "続行不能" in prompt_content

    def test_has_prohibited_actions(self, prompt_content: str) -> None:
        """Prohibited Actions が記載されていること."""
        # 注意事項または禁止事項
        assert "注意" in prompt_content or "禁止" in prompt_content

    def test_has_issue_operation_rules(self, prompt_content: str) -> None:
        """Issue Operation Rules が記載されていること."""
        assert "Issue" in prompt_content
        # 更新ルール
        assert "更新" in prompt_content or "追記" in prompt_content

    def test_has_evidence_storage(self, prompt_content: str) -> None:
        """Evidence Storage が記載されていること."""
        assert "証跡" in prompt_content or "Evidence" in prompt_content
        assert "artifacts" in prompt_content.lower() or "保存" in prompt_content


class TestReviewPreamble:
    """_review_preamble.md のチェックリスト検証."""

    @pytest.fixture
    def prompt_content(self) -> str:
        """_review_preamble.md の内容を取得."""
        from src.bugfix_agent.prompts import REVIEW_PREAMBLE_FILE

        assert REVIEW_PREAMBLE_FILE.exists(), (
            f"_review_preamble.md not found: {REVIEW_PREAMBLE_FILE}"
        )
        return REVIEW_PREAMBLE_FILE.read_text(encoding="utf-8")

    def test_has_devils_advocate_declaration(self, prompt_content: str) -> None:
        """Devil's Advocate 宣言が含まれること."""
        assert "Devil's Advocate" in prompt_content or "devil" in prompt_content.lower()
        # 批判的レビューの姿勢
        assert "not an approver" in prompt_content.lower() or "承認者" in prompt_content


class TestFooterVerdict:
    """_footer_verdict.md のチェックリスト検証."""

    @pytest.fixture
    def prompt_content(self) -> str:
        """_footer_verdict.md の内容を取得."""
        from src.bugfix_agent.prompts import FOOTER_VERDICT_FILE

        assert FOOTER_VERDICT_FILE.exists(), f"_footer_verdict.md not found: {FOOTER_VERDICT_FILE}"
        return FOOTER_VERDICT_FILE.read_text(encoding="utf-8")

    def test_has_verdict_output_rules(self, prompt_content: str) -> None:
        """VERDICT出力ルールが含まれること."""
        assert "VERDICT" in prompt_content
        # 出力に関するルール
        assert "出力" in prompt_content or "output" in prompt_content.lower()

    def test_has_evidence_quality_check(self, prompt_content: str) -> None:
        """Evidence の品質チェックに関する記載があること."""
        assert "Evidence" in prompt_content
        # 品質に関する説明
        assert "具体的" in prompt_content or "specific" in prompt_content.lower()


class TestLoadPromptFunction:
    """load_prompt 関数のテスト."""

    def test_load_detail_design(self) -> None:
        """detail_design プロンプトをロードできること."""
        from src.bugfix_agent.prompts import load_prompt

        result = load_prompt(
            "detail_design",
            issue_url="https://github.com/test/repo/issues/1",
            artifacts_dir="/tmp/artifacts",
            loop_count=1,
            max_loop_count=3,
        )

        assert "https://github.com/test/repo/issues/1" in result
        assert "/tmp/artifacts" in result

    def test_load_detail_design_review_includes_common(self) -> None:
        """detail_design_review は _common.md を自動的に含むこと."""
        from src.bugfix_agent.prompts import load_prompt

        result = load_prompt(
            "detail_design_review",
            issue_url="https://github.com/test/repo/issues/1",
        )

        # _common.md の内容（VERDICT キーワード定義）が含まれている
        assert "VERDICT" in result
        assert "PASS" in result
        assert "RETRY" in result

    def test_load_detail_design_review_includes_preamble(self) -> None:
        """detail_design_review は _review_preamble.md を自動的に含むこと."""
        from src.bugfix_agent.prompts import load_prompt

        result = load_prompt(
            "detail_design_review",
            issue_url="https://github.com/test/repo/issues/1",
        )

        # Devil's Advocate 宣言が含まれている
        assert "Devil's Advocate" in result or "devil" in result.lower()

    def test_load_detail_design_review_includes_footer(self) -> None:
        """detail_design_review は _footer_verdict.md を自動的に含むこと."""
        from src.bugfix_agent.prompts import load_prompt

        result = load_prompt(
            "detail_design_review",
            issue_url="https://github.com/test/repo/issues/1",
        )

        # footer の特徴的な文言
        assert "VERDICT" in result
        # Evidence 品質チェックの文言
        assert "Evidence" in result

    def test_load_nonexistent_prompt_raises_error(self) -> None:
        """存在しないプロンプトで FileNotFoundError が発生すること."""
        from src.bugfix_agent.prompts import load_prompt

        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_state")


class TestPromptDirPath:
    """PROMPT_DIR のパス検証."""

    def test_prompt_dir_is_under_bugfix_agent(self) -> None:
        """PROMPT_DIR が src/bugfix_agent/prompts/ を指していること."""
        from src.bugfix_agent.prompts import PROMPT_DIR

        # パスの末尾が bugfix_agent/prompts であること
        assert PROMPT_DIR.name == "prompts"
        assert PROMPT_DIR.parent.name == "bugfix_agent"
