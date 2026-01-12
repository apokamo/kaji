# Issue #19: AIToolProtocol定義 + ClaudeTool実装

## 概要

AIツール（Claude/Codex/Gemini）の共通インターフェース `AIToolProtocol` を強化し、`ClaudeTool` を実装する。

## 現状分析

### 既存の実装

`src/core/tools/protocol.py` に基本的なProtocol定義が存在:

```python
class AIToolProtocol(Protocol):
    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        ...
```

### 課題

1. `run()` メソッドのみで、同期実行のみサポート
2. 実装クラス（ClaudeTool等）が存在しない
3. エラーハンドリングの仕様が未定義
4. 設定管理（APIキー、モデル名等）の方針が未定義

## 設計方針

### 1. Protocol拡張

既存のシンプルなProtocolを維持しつつ、以下を追加:

- `name` プロパティ: ツール識別子（"claude", "codex", "gemini"）
- `model` プロパティ: 使用モデル名

```python
class AIToolProtocol(Protocol):
    """Protocol for AI tool implementations."""

    @property
    def name(self) -> str:
        """Tool identifier (e.g., 'claude', 'codex', 'gemini')."""
        ...

    @property
    def model(self) -> str:
        """Model name being used."""
        ...

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Execute the AI tool."""
        ...
```

### 2. ClaudeTool実装

#### クラス構成

```python
class ClaudeTool:
    """Claude Code CLI wrapper."""

    def __init__(
        self,
        model: str = "sonnet",
        cwd: Path | None = None,
    ) -> None:
        """Initialize ClaudeTool.

        Args:
            model: Model to use ("sonnet", "opus", "haiku")
            cwd: Working directory for Claude execution
        """
        ...
```

#### 実行方式

`subprocess` を使用して `claude` CLI を呼び出す:

```bash
claude --model <model> --print --output-format text
```

- `--print`: 非対話モード
- `--output-format text`: プレーンテキスト出力

#### セッション管理

- `--continue <session_id>` または `--resume <session_id>` オプションで会話継続
- 新規セッションIDは出力から解析（実装時に詳細検討）

#### エラーハンドリング

```python
class AIToolError(Exception):
    """Base exception for AI tool errors."""
    pass

class AIToolExecutionError(AIToolError):
    """Raised when AI tool execution fails."""
    pass

class AIToolTimeoutError(AIToolError):
    """Raised when AI tool execution times out."""
    pass
```

### 3. ファイル構成

```
src/core/tools/
├── __init__.py      # 更新: ClaudeTool, エラークラスをエクスポート
├── protocol.py      # 更新: Protocol拡張
├── claude.py        # 新規: ClaudeTool実装
└── errors.py        # 新規: エラークラス定義
```

### 4. 設定管理

APIキー等の機密情報は環境変数から取得:

- `ANTHROPIC_API_KEY`: Claude API key（Claude CLI が使用）

ClaudeToolは環境変数を直接参照せず、Claude CLIが管理する認証を利用する。

## テスト方針

### ユニットテスト

1. **Protocol準拠テスト**: ClaudeToolがAIToolProtocolを満たすことを確認
2. **初期化テスト**: デフォルト値、カスタム値での初期化
3. **プロパティテスト**: `name`, `model` の値確認

### モックテスト

`subprocess.run` をモックして以下をテスト:

1. 正常実行: レスポンス取得
2. タイムアウト: `subprocess.TimeoutExpired` 処理
3. 実行失敗: 非ゼロ終了コード処理
4. コンテキスト処理: 文字列/リスト両対応

### 統合テスト（オプション）

実際のClaude CLI呼び出しは手動テストまたはCI/CDで別途実施。

## 実装順序

1. `errors.py`: エラークラス定義
2. `protocol.py`: Protocol拡張
3. `claude.py`: ClaudeTool実装
4. `__init__.py`: エクスポート更新
5. テスト作成・実行

## 互換性

- 既存の `AIToolProtocol` インターフェースは維持
- `run()` メソッドのシグネチャ変更なし
- 新規プロパティ追加のみ

## 依存関係

- 追加パッケージなし（標準ライブラリの `subprocess` を使用）

## 確認事項

- [ ] Claude CLIの `--continue` または `--resume` オプションの正確な仕様
- [ ] セッションID取得方法の確認
