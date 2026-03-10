"""Agent context for Bugfix Agent v5

This module provides:
- AgentContext: Context dataclass passed to state handlers
- create_default_context: Create production context with real AI tools

For testing, use create_test_context from tests.utils.context.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .providers import GitHubIssueProvider, IssueProvider
from .run_logger import RunLogger
from .tools import AIToolProtocol, ClaudeTool, CodexTool


@dataclass
class AgentContext:
    """ステートハンドラに渡すコンテキスト"""

    # AI ツール（依存性注入）
    analyzer: AIToolProtocol  # Gemini: 分析・ドキュメント作成
    reviewer: AIToolProtocol  # Codex: レビュー・判断
    implementer: AIToolProtocol  # Claude: 実装・操作

    # Issue 情報
    issue_url: str  # https://github.com/apokamo/kamo2/issues/182
    issue_number: int  # 182

    # Issue プロバイダー（GitHub API抽象化）
    issue_provider: IssueProvider

    # 実行ロガー
    logger: RunLogger

    # 証跡ベースパス
    artifacts_base: Path = field(default_factory=lambda: Path("test-artifacts/bugfix-agent"))
    run_timestamp: str = ""  # YYMMDDhhmm 形式（実行開始時に設定）

    @property
    def artifacts_dir(self) -> Path:
        """実行単位の証跡ディレクトリ"""
        return self.artifacts_base / str(self.issue_number) / self.run_timestamp

    def artifacts_state_dir(self, state: str) -> Path:
        """ステート別の証跡ディレクトリ"""
        return self.artifacts_dir / state.lower()


def _create_tool(
    tool_name: str,
    model: str | None = None,
) -> AIToolProtocol:
    """ツール名からツールインスタンスを生成

    Args:
        tool_name: ツール名 (codex, gemini, claude)
        model: モデル名（None でデフォルト）

    Returns:
        AIToolProtocol 実装
    """
    # Note: GeminiTool is imported only when needed to avoid unnecessary dependency
    if tool_name == "codex":
        return CodexTool(model=model) if model else CodexTool()
    elif tool_name == "gemini":
        from .tools import GeminiTool

        return GeminiTool(model=model) if model else GeminiTool()
    elif tool_name == "claude":
        return ClaudeTool(model=model) if model else ClaudeTool()
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


def create_default_context(
    issue_url: str,
    tool_override: str | None = None,
    model_override: str | None = None,
    issue_provider: IssueProvider | None = None,
) -> AgentContext:
    """本番用のコンテキストを生成

    Args:
        issue_url: Issue URL (例: https://github.com/apokamo/kamo2/issues/182)
        tool_override: ツール指定（全ロールで同一ツールを使用）
        model_override: モデル指定（tool_override と併用）
        issue_provider: Issue プロバイダー（None なら GitHubIssueProvider を生成）

    Returns:
        本番用 AI ツールを注入した AgentContext
    """
    # issue_url から issue_number を抽出
    issue_number = int(issue_url.rstrip("/").split("/")[-1])

    # 実行タイムスタンプを生成（YYMMDDhhmm 形式）
    run_timestamp = datetime.now(UTC).strftime("%y%m%d%H%M")

    # 証跡ディレクトリ
    artifacts_base = Path("test-artifacts/bugfix-agent")
    artifacts_dir = artifacts_base / str(issue_number) / run_timestamp

    # ロガー
    logger = RunLogger(artifacts_dir / "run.log")

    # Issue プロバイダー（デフォルトは GitHub API）
    if issue_provider is None:
        issue_provider = GitHubIssueProvider(issue_url)

    # ツールオーバーライドがある場合は全ロールで同一ツールを使用
    if tool_override:
        tool = _create_tool(tool_override, model_override)
        return AgentContext(
            analyzer=tool,
            reviewer=tool,
            implementer=tool,
            issue_url=issue_url,
            issue_number=issue_number,
            issue_provider=issue_provider,
            logger=logger,
            run_timestamp=run_timestamp,
            artifacts_base=artifacts_base,
        )

    # デフォルト: 各ロールに専用ツール
    # Note: analyzer を Gemini → Claude に変更 (Issue #194 テスト設計)
    # Note: コスト削減のため sonnet / codex-mini を使用 (E2E Test 11)
    return AgentContext(
        analyzer=ClaudeTool(model="sonnet"),
        reviewer=CodexTool(model="gpt-5.1-codex-mini"),
        implementer=ClaudeTool(model="sonnet"),
        issue_url=issue_url,
        issue_number=issue_number,
        issue_provider=issue_provider,
        logger=logger,
        run_timestamp=run_timestamp,
        artifacts_base=artifacts_base,
    )
