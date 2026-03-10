"""Test context utilities for Bugfix Agent v5.

This module provides:
- create_test_context: Create test context with mock tools and MockIssueProvider
"""

from pathlib import Path

from bugfix_agent.agent_context import AgentContext
from bugfix_agent.run_logger import RunLogger
from bugfix_agent.tools import MockTool

from .providers import MockIssueProvider


def create_test_context(
    analyzer_responses: list[str] | None = None,
    reviewer_responses: list[str] | None = None,
    implementer_responses: list[str] | None = None,
    issue_url: str = "https://github.com/test/repo/issues/999",
    run_timestamp: str = "2511281430",
    issue_provider: MockIssueProvider | None = None,
    artifacts_base: Path | None = None,
) -> AgentContext:
    """テスト用のコンテキストを生成

    Args:
        analyzer_responses: アナライザー（Gemini）の応答リスト
        reviewer_responses: レビュアー（Codex）の応答リスト
        implementer_responses: 実装者（Claude）の応答リスト
        issue_url: Issue URL（デフォルト: テスト用ダミー）
        run_timestamp: 実行タイムスタンプ（デフォルト: テスト用固定値）
        issue_provider: MockIssueProvider インスタンス
                       （None なら自動生成）
        artifacts_base: 成果物ベースディレクトリ（テストでtmp_pathを指定）

    Returns:
        MockTool と MockIssueProvider を注入した AgentContext
    """
    issue_number = int(issue_url.rstrip("/").split("/")[-1])

    # デフォルト応答
    if analyzer_responses is None:
        analyzer_responses = []
    if reviewer_responses is None:
        reviewer_responses = []
    if implementer_responses is None:
        implementer_responses = []

    # Issue プロバイダー（MockIssueProvider を使用）
    if issue_provider is None:
        issue_provider = MockIssueProvider(
            issue_number=issue_number,
            repo_url=issue_url.rsplit("/issues/", 1)[0],
        )

    # テスト時は /tmp/pytest-of-hoge/pytest-current/testname/
    # などに作成される
    if artifacts_base is None:
        artifacts_base = Path("test-artifacts/bugfix-agent")

    artifacts_dir = artifacts_base / str(issue_number) / run_timestamp
    logger = RunLogger(artifacts_dir / "run.log")

    return AgentContext(
        analyzer=MockTool(analyzer_responses),
        reviewer=MockTool(reviewer_responses),
        implementer=MockTool(implementer_responses),
        issue_url=issue_url,
        issue_number=issue_number,
        issue_provider=issue_provider,
        run_timestamp=run_timestamp,
        artifacts_base=artifacts_base,
        logger=logger,
    )
