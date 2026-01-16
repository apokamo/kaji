# [設計] AgentContext の実装（v5ベース）

Issue: #26

## 概要

ステートハンドラに渡すコンテキストを管理する `AgentContext` を `src/core/` に実装する。
AIツール（Claude/Codex/Gemini）への依存性注入、Issue操作の抽象化、証跡管理を提供する。

## 背景・目的

現在の `AgentContext` は `src/workflows/base.py:14-21` に空クラス（TODO）として存在。
v5（bugfix-v5）では完全実装されており、これをワークフロー非依存な形で移植する。

**目的:**
- AIツール切り替え（Claude ↔ Codex ↔ Gemini）を容易にする
- オフラインテスト・モックテストを可能にする
- 実行ログ・証跡をデバッグ可能な形で保存する

### v5 実装の要点（移植対象）

v5 の `AgentContext` は以下の構造を持つ:

```python
@dataclass
class AgentContext:
    # AIツール（依存性注入）- 役割ごとに異なるツールを設定可能
    analyzer: AIToolProtocol    # 分析・ドキュメント作成
    reviewer: AIToolProtocol    # レビュー・判断
    implementer: AIToolProtocol # 実装・操作

    # Issue情報
    issue_url: str
    issue_number: int

    # Issueプロバイダー（GitHub API抽象化）
    issue_provider: IssueProvider

    # 証跡管理
    artifacts_base: Path
    run_timestamp: str  # YYMMDDhhmm形式

    @property
    def artifacts_dir(self) -> Path:
        return self.artifacts_base / str(self.issue_number) / self.run_timestamp

    def artifacts_state_dir(self, state: str) -> Path:
        return self.artifacts_dir / state.lower()
```

v5 の `IssueProvider` は ABC で以下のメソッドを定義:
- `get_issue_body() -> str`
- `add_comment(body: str) -> None`
- `update_body(body: str) -> None`
- `issue_number: int` (property)
- `issue_url: str` (property)

## インターフェース

### 入力

```python
# AgentContext の生成
ctx = AgentContext(
    analyzer=ClaudeTool(model="sonnet"),
    reviewer=CodexTool(),
    implementer=ClaudeTool(),
    issue_provider=GitHubIssueProvider(issue_url),
    artifacts_base=Path("artifacts"),
)
```

### 出力

- ステートハンドラ内で AIToolProtocol 経由でツール呼び出し
- IssueProvider 経由で Issue 操作（コメント追加、本文更新）
- artifacts_dir にログ・中間成果物を保存

### 使用例

```python
# ステートハンドラ内での使用
def handle_design(ctx: AgentContext, state: SessionState) -> DesignState:
    prompt = load_prompt("design.md")
    issue_body = ctx.issue_provider.get_issue_body()

    response, session_id = ctx.analyzer.run(
        prompt=prompt,
        context=issue_body,
        log_dir=ctx.artifacts_state_dir("design"),
    )

    state.set_conversation_id("analyzer", session_id)
    return DesignState.DESIGN_REVIEW
```

## 制約・前提条件

- `AIToolProtocol` は既に `src/core/tools/protocol.py` に定義済み
- `SessionState` は `src/workflows/base.py` に定義済み
- `gh` CLI がインストール・認証済み（GitHubIssueProvider の前提）
- Python 3.11+

## 方針

### ファイル構成

```
src/core/
├── __init__.py          # AgentContext, IssueProvider をエクスポート
├── context.py           # AgentContext dataclass, create_context()
├── providers.py         # IssueProvider Protocol, GitHubIssueProvider
└── tools/
    └── protocol.py      # AIToolProtocol（既存）
```

**Note:** `artifacts.py` は作成しない。証跡管理は `AgentContext` のメソッドで完結する（YAGNI）。

### 1. IssueProvider Protocol

`typing.Protocol` を使用し、`AIToolProtocol` と一貫性を持たせる。

```python
# src/core/providers.py
from typing import Protocol

class IssueProvider(Protocol):
    """Issue操作のプロトコル。

    GitHub/GitLab等の Issue システムを抽象化する。
    structural subtyping により、明示的な継承なしでモック可能。
    """

    def get_issue_body(self) -> str:
        """Issue本文を取得する。

        Raises:
            IssueProviderError: API呼び出し失敗時
        """
        ...

    def add_comment(self, body: str) -> None:
        """Issueにコメントを追加する。

        Args:
            body: コメント本文

        Raises:
            IssueProviderError: API呼び出し失敗時（リトライ後も失敗した場合）
        """
        ...

    def update_body(self, body: str) -> None:
        """Issue本文を更新する。

        Args:
            body: 新しい本文

        Raises:
            IssueProviderError: API呼び出し失敗時
        """
        ...

    @property
    def issue_number(self) -> int:
        """Issue番号を取得する。"""
        ...

    @property
    def issue_url(self) -> str:
        """Issue URLを取得する。"""
        ...
```

### 2. IssueProvider エラー契約

**例外ベースのエラーハンドリング**を採用:

```python
# src/core/errors.py
class IssueProviderError(Exception):
    """IssueProvider操作の基底例外。"""
    pass

class IssueNotFoundError(IssueProviderError):
    """Issueが存在しない。"""
    pass

class IssueAuthenticationError(IssueProviderError):
    """認証エラー（gh CLI未認証等）。"""
    pass

class IssueRateLimitError(IssueProviderError):
    """APIレート制限。"""
    pass
```

**エラー処理方針:**
- `get_issue_body()`: 失敗時は `IssueProviderError` を送出（リトライなし）
- `add_comment()`: 設定可能なリトライ後も失敗なら `IssueProviderError` を送出
- `update_body()`: 失敗時は `IssueProviderError` を送出（リトライなし）
- Mock実装では例外を投げないか、テスト用に設定可能とする

### 3. GitHubIssueProvider（本番用）

```python
class GitHubIssueProvider:
    """GitHub Issue操作の実装。

    gh CLI を使用して GitHub API にアクセスする。
    """

    def __init__(self, issue_url: str) -> None:
        """初期化。

        Args:
            issue_url: GitHub Issue URL
                形式: https://github.com/{owner}/{repo}/issues/{number}

        Raises:
            ValueError: URL形式が不正な場合
        """
        ...
```

**リトライ設定（pydantic-settings）:**
- `max_comment_retries`: デフォルト 2
- `retry_delay`: デフォルト 1.0 秒

### 4. AgentContext dataclass

```python
# src/core/context.py
@dataclass
class AgentContext:
    """ステートハンドラに渡すコンテキスト。

    AIツール、Issueプロバイダー、証跡管理を一元化する。
    """

    # AIツール（依存性注入）
    analyzer: AIToolProtocol
    reviewer: AIToolProtocol
    implementer: AIToolProtocol

    # Issue プロバイダー
    issue_provider: IssueProvider

    # 証跡管理
    artifacts_base: Path = field(default_factory=lambda: Path("artifacts"))
    run_timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%y%m%d%H%M"))

    @property
    def artifacts_dir(self) -> Path:
        """実行単位の証跡ディレクトリ。

        Returns:
            Path: {artifacts_base}/{issue_number}/{run_timestamp}
        """
        return self.artifacts_base / str(self.issue_provider.issue_number) / self.run_timestamp

    def artifacts_state_dir(self, state: str) -> Path:
        """ステート別の証跡ディレクトリ。

        Args:
            state: ステート名（例: "design", "implement"）

        Returns:
            Path: {artifacts_dir}/{state} (小文字化)
        """
        return self.artifacts_dir / state.lower()

    def ensure_artifacts_dir(self, state: str | None = None) -> Path:
        """証跡ディレクトリを作成して返す。

        Args:
            state: ステート名。Noneの場合は実行単位ディレクトリ。

        Returns:
            Path: 作成されたディレクトリパス
        """
        target = self.artifacts_state_dir(state) if state else self.artifacts_dir
        target.mkdir(parents=True, exist_ok=True)
        return target
```

### 5. ファクトリ関数

```python
def create_context(
    issue_url: str,
    tool_override: str | None = None,
    model_override: str | None = None,
    artifacts_base: Path | None = None,
) -> AgentContext:
    """本番用コンテキストを生成する。

    Args:
        issue_url: GitHub Issue URL
            形式: https://github.com/{owner}/{repo}/issues/{number}
        tool_override: 全ロールで使用するツール名 ("claude", "codex", "gemini")
            指定時は analyzer/reviewer/implementer すべてに同一ツールを使用
        model_override: モデル名（tool_override と併用）
        artifacts_base: 証跡ベースパス（デフォルト: Path("artifacts")）

    Returns:
        AgentContext: 本番用ツールを注入したコンテキスト

    Raises:
        ValueError: issue_url の形式が不正な場合
        ValueError: tool_override が未知のツール名の場合

    優先順位:
        1. 引数で指定された値
        2. 環境変数 / pydantic-settings（将来対応）
        3. デフォルト値

    デフォルトツール構成:
        - analyzer: ClaudeTool(model="sonnet")
        - reviewer: CodexTool()
        - implementer: ClaudeTool(model="sonnet")
    """
    ...
```

**issue_url の許容形式:**
- `https://github.com/{owner}/{repo}/issues/{number}`
- 末尾スラッシュあり/なし両対応
- `{number}` は正の整数

### 6. base.py からの移行

- `src/workflows/base.py` の `AgentContext` クラスを削除
- `src/core.context` から import するよう変更

## 検証観点

### 正常系

- AgentContext が正しく生成できること
- analyzer/reviewer/implementer で異なるツールを設定できること
- artifacts_dir が期待通りのパスを返すこと（`{base}/{issue_number}/{timestamp}`）
- ensure_artifacts_dir でディレクトリが作成されること
- IssueProvider 経由で Issue 本文を取得できること

### 異常系

- 必須フィールド（analyzer, reviewer, implementer, issue_provider）が欠けた場合に TypeError が発生すること
- 不正な issue_url の場合に ValueError が発生すること
- IssueProvider の操作失敗時に IssueProviderError が伝播すること
- AIツールの run() が例外を投げた場合に伝播すること

### 境界値

- artifacts_base が存在しないディレクトリの場合（ensure_artifacts_dir で自動作成）
- issue_number が 0 の場合（許容するか要検討）

### テスト容易性

- Protocol による structural subtyping で明示的な継承なしにモック可能
- MockIssueProvider でオフラインテストが可能なこと
- MockTool（既存）と組み合わせてハンドラのユニットテストが可能なこと

## 参考

- 既存 Protocol: `src/core/tools/protocol.py`
- Issue #1 コメント: v5 との差分分析
