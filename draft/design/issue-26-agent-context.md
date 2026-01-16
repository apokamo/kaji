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
├── context.py           # AgentContext dataclass
├── providers.py         # IssueProvider ABC, GitHubIssueProvider
├── artifacts.py         # ArtifactManager（証跡管理）
└── tools/
    └── protocol.py      # AIToolProtocol（既存）
```

### 1. IssueProvider Protocol

```python
# src/core/providers.py
from abc import ABC, abstractmethod

class IssueProvider(ABC):
    @abstractmethod
    def get_issue_body(self) -> str: ...

    @abstractmethod
    def add_comment(self, body: str) -> None: ...

    @abstractmethod
    def update_body(self, body: str) -> None: ...

    @property
    @abstractmethod
    def issue_number(self) -> int: ...

    @property
    @abstractmethod
    def issue_url(self) -> str: ...
```

### 2. GitHubIssueProvider（本番用）

- `gh` CLI を使用して GitHub API にアクセス
- リトライロジック（`pydantic-settings` で設定可能）
- v5 の実装をベースに移植

### 3. AgentContext dataclass

```python
# src/core/context.py
@dataclass
class AgentContext:
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
        """実行単位の証跡ディレクトリ"""
        return self.artifacts_base / str(self.issue_provider.issue_number) / self.run_timestamp

    def artifacts_state_dir(self, state: str) -> Path:
        """ステート別の証跡ディレクトリ"""
        return self.artifacts_dir / state.lower()
```

### 4. ファクトリ関数

```python
def create_context(
    issue_url: str,
    tool_override: str | None = None,
    model_override: str | None = None,
) -> AgentContext:
    """本番用コンテキストを生成"""
```

### 5. base.py からの移行

- `src/workflows/base.py` の `AgentContext` クラスを削除
- `src/core.context` から import するよう変更

## 検証観点

### 正常系

- AgentContext が正しく生成できること
- analyzer/reviewer/implementer で異なるツールを設定できること
- artifacts_dir が期待通りのパスを返すこと
- IssueProvider 経由で Issue 本文を取得できること

### 異常系

- IssueProvider が None の場合のエラーハンドリング
- 不正な issue_url の場合のバリデーションエラー
- AIツールの run() が例外を投げた場合の伝播

### 境界値

- artifacts_base が存在しないディレクトリの場合（自動作成）
- issue_number が 0 や負の値の場合

### テスト容易性

- MockIssueProvider でオフラインテストが可能なこと
- MockTool（既存）と組み合わせてハンドラのユニットテストが可能なこと

## 参考

- v5 実装: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/agent_context.py`
- v5 プロバイダー: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/providers.py`
- 既存 Protocol: `src/core/tools/protocol.py`
