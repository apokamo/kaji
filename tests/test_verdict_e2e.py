"""Large tests: Verdict E2E.

Tests using real agent output fixtures and actual CLI execution.
Verifies the full verdict parsing pipeline from raw output to Verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.models import Verdict
from kaji_harness.verdict import parse_verdict

VALID_STATUSES = {"PASS", "RETRY", "BACK", "ABORT"}
FIXTURES_DIR = Path(__file__).parent.parent / "test-artifacts" / "verdict-fixtures"


def _ensure_fixtures_dir() -> Path:
    """Ensure the fixtures directory exists."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURES_DIR


# ============================================================
# Real agent output fixture tests
# ============================================================


@pytest.mark.large
class TestRealAgentOutputFixtures:
    """Parse verdicts from real agent output samples."""

    def test_issue_73_end_verdict_space(self) -> None:
        """#73 actual case: ---END VERDICT--- (space instead of underscore).

        This was the triggering incident for issue #77.
        """
        # This is the approximate output structure from the #73 issue-pr step
        output = (
            "## PR作成完了\n\n"
            "| 項目 | 値 |\n"
            "|------|-----|\n"
            "| Issue | #73 |\n"
            "| PR | #75 |\n\n"
            "### 次のステップ\n\n"
            "`/issue-close 73` でIssueをクローズしてください。\n\n"
            "---VERDICT---\n"
            "status: PASS\n"
            "reason: |\n"
            "  PR作成・プッシュ完了\n"
            "evidence: |\n"
            "  gh pr create 正常終了、PR #75 作成済み\n"
            "suggestion: |\n"
            "---END VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert isinstance(result, Verdict)

    def test_issue_73_fixture_file(self) -> None:
        """Parse from saved fixture file if it exists."""
        fixture_path = FIXTURES_DIR / "issue-73-end-verdict-space.txt"
        if not fixture_path.exists():
            _ensure_fixtures_dir()
            # Save the fixture for future regression tests
            fixture_path.write_text(
                "---VERDICT---\n"
                "status: PASS\n"
                "reason: |\n"
                "  PR作成・プッシュ完了\n"
                "evidence: |\n"
                "  gh pr create 正常終了\n"
                "suggestion: |\n"
                "---END VERDICT---\n",
                encoding="utf-8",
            )

        content = fixture_path.read_text(encoding="utf-8")
        result = parse_verdict(content, VALID_STATUSES)
        assert result.status == "PASS"

    def test_codex_mcp_tool_call_output(self) -> None:
        """Codex output where VERDICT appears in mcp_tool_call result text."""
        # Simulates the scenario described in legacy/docs/E2E_TEST_FINDINGS.md
        output = (
            "Analyzing the codebase...\n"
            "Running tests...\n"
            "All tests passed.\n\n"
            "## VERDICT\n"
            "- Result: PASS\n"
            "- Reason: 全テスト通過、品質チェッククリア\n"
            "- Evidence: pytest 15 passed, ruff/mypy clean\n"
            "- Suggestion: なし\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"
        assert "全テスト通過" in result.reason

    def test_verbose_output_with_thinking_traces(self) -> None:
        """Output with extensive thinking traces before verdict."""
        lines = [
            "思考中...",
            "ステップ1: コードを分析",
            "ステップ2: テストを実行",
            "ステップ3: 結果を確認",
            "",
            "分析結果: すべてのテストが通過しました。",
            "カバレッジ: 85%",
            "",
            "詳細ログ:" + "\n  debug line " * 50,  # Lots of noise
            "",
            "---VERDICT---",
            "status: PASS",
            'reason: "全テスト通過・品質チェック完了"',
            'evidence: "pytest 20 passed, coverage 85%, ruff/mypy clean"',
            'suggestion: ""',
            "---END_VERDICT---",
        ]
        output = "\n".join(lines)
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "PASS"

    def test_abort_with_detailed_suggestion(self) -> None:
        """ABORT verdict with multi-line suggestion from a real scenario."""
        output = (
            "環境チェック失敗\n"
            "---VERDICT---\n"
            "status: ABORT\n"
            "reason: |\n"
            "  外部APIに接続できません\n"
            "evidence: |\n"
            "  ConnectionError: Failed to connect to api.example.com:443\n"
            "  Traceback (most recent call last):\n"
            '    File "test_api.py", line 42\n'
            "    requests.get(url, timeout=5)\n"
            "suggestion: |\n"
            "  1. VPN接続を確認してください\n"
            "  2. API_KEY環境変数が設定されているか確認\n"
            "  3. 手動で curl api.example.com を試行\n"
            "---END_VERDICT---\n"
        )
        result = parse_verdict(output, VALID_STATUSES)
        assert result.status == "ABORT"
        assert "VPN" in result.suggestion
        assert "ConnectionError" in result.evidence


# ============================================================
# Regression fixture management
# ============================================================


@pytest.mark.large
class TestFixtureManagement:
    """Ensure fixture directory and files are maintained."""

    def test_fixtures_dir_exists(self) -> None:
        """test-artifacts/verdict-fixtures/ directory is accessible."""
        _ensure_fixtures_dir()
        assert FIXTURES_DIR.exists()
