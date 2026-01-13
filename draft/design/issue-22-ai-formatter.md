# [設計] verdict.py AI Formatter対応追加

Issue: #22

## 概要

verdict.py に AI Formatter 機能を追加し、Strict→Relaxed パースで失敗した場合の最終手段として AI による VERDICT 再整形を可能にする。

## 背景・目的

現在の verdict.py は Strict→Relaxed の2段階パースのみ対応している（98行）。v5 では AI Formatter による Step 3 が実装されており（280行）、パース失敗時の救済手段として有効に機能している。

AI の出力は常に期待どおりのフォーマットとは限らない。VERDICT セクションが不完全、フォーマットが崩れている、日本語混在などのケースで、AI Formatter が整形することでパース成功率を向上させる。

## インターフェース

### 入力

#### `parse_verdict()` 関数（拡張）

```python
def parse_verdict(
    text: str,
    ai_formatter: AIFormatterFunc | None = None,
    max_retries: int = 2,
) -> Verdict:
```

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| text | str | Yes | パース対象のAI出力テキスト |
| ai_formatter | AIFormatterFunc \| None | No | Step 3 用の AI 整形関数 |
| max_retries | int | No | AI Formatter リトライ回数（デフォルト: 2） |

#### `handle_abort_verdict()` 関数（新規）

```python
def handle_abort_verdict(verdict: Verdict, raw_output: str) -> Verdict:
```

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| verdict | Verdict | Yes | parse_verdict() の戻り値 |
| raw_output | str | Yes | パース元の生テキスト（reason/suggestion 抽出用） |

#### `create_ai_formatter()` 関数（新規）

```python
def create_ai_formatter(
    tool: AIToolProtocol,
    *,
    context: str = "",
    log_dir: Path | None = None,
) -> AIFormatterFunc:
```

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| tool | AIToolProtocol | Yes | AI ツール実装 |
| context | str | No | AI に渡す追加コンテキスト |
| log_dir | Path \| None | No | ログ出力先ディレクトリ |

### 出力

- `parse_verdict()`: `Verdict` enum（ABORT 含む）
- `handle_abort_verdict()`: `Verdict`（ABORT以外）、または `AgentAbortError` を raise
- `create_ai_formatter()`: `AIFormatterFunc` 型の関数

### 型定義

```python
AIFormatterFunc = Callable[[str], str]
```

### 使用例

```python
from dao.core.verdict import parse_verdict, create_ai_formatter, handle_abort_verdict
from dao.core.tools.claude import ClaudeTool

# AI Formatter なしで使用（従来どおり）
verdict = parse_verdict(ai_output)

# AI Formatter ありで使用
tool = ClaudeTool()
ai_formatter = create_ai_formatter(tool, log_dir=Path("logs"))
verdict = parse_verdict(ai_output, ai_formatter=ai_formatter)

# ABORT verdict のハンドリング
verdict = handle_abort_verdict(verdict, ai_output)  # ABORT なら例外
```

## 制約・前提条件

- **責務分離**: パーサーは Verdict enum を返すのみ。ABORT 判定後の例外送出はオーケストレーター側（`handle_abort_verdict()`）の責務
- **InvalidVerdictValueError は即座に raise**: 不正な VERDICT 値はフォールバック対象外（プロンプト違反/実装バグを示すため）
- **AI Formatter 入力制限**: 最大 8000 文字（約 2000 トークン）。超過分は head+tail 方式で切り詰め
- **max_retries >= 1**: 1未満の場合は ValueError
- **AIToolProtocol 準拠**: create_ai_formatter() は AIToolProtocol を実装したツールを要求

## 方針

### 3段階フォールバック戦略

```
Step 1: Strict Parse
    └─ "Result: <STATUS>" パターンで検索
    └─ 成功 → Verdict 返却
    └─ InvalidVerdictValueError → 即座に re-raise
    └─ VerdictParseError → Step 2 へ

Step 2: Relaxed Parse
    └─ 複数パターンで検索（Status:, **Status**:, ステータス: 等）
    └─ 成功 → Verdict 返却
    └─ VerdictParseError → Step 3 へ

Step 3: AI Formatter Retry
    └─ ai_formatter が None → VerdictParseError
    └─ テキストを切り詰め（8000文字上限）
    └─ AI で整形 → Strict + Relaxed で再パース
    └─ max_retries 回まで繰り返し
    └─ 全失敗 → VerdictParseError
```

### Relaxed パターン（拡張）

```python
RELAXED_PATTERNS = [
    r"Result:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"-\s*Result:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"Status:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"-\s*Status:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"\*\*Status\*\*:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"ステータス:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"Status\s*=\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"Result\s*=\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
]
```

### AI Formatter プロンプト

```python
FORMATTER_PROMPT = """以下の出力からVERDICTを抽出し、正確なフォーマットで出力してください。

## 入力
{raw_output}

## 出力フォーマット（厳密に従ってください）
## VERDICT
- Result: <PASS|RETRY|BACK_DESIGN|ABORT のいずれか1つ>
- Reason: <1行の要約>
- Evidence: <詳細>
- Suggestion: <次のアクション>

重要: Result行は必ず "- Result: " で始め、4つの値のいずれかを出力してください。
"""
```

### エラー定義の追加

`src/core/errors.py` に以下を追加:

```python
class AgentAbortError(Exception):
    """エージェントが ABORT を返した場合の例外"""
    def __init__(self, reason: str, suggestion: str = ""):
        self.reason = reason
        self.suggestion = suggestion
        super().__init__(f"Agent aborted: {reason}")
```

## 検証観点

### 正常系

- Step 1 で成功: "Result: PASS" 形式のテキストが正しくパースされる
- Step 2 で成功: "Status: PASS", "ステータス: PASS" 等がパースされる
- Step 3 で成功: AI Formatter が整形したテキストがパースされる
- ABORT verdict が正しく返却される

### 異常系

- InvalidVerdictValueError: "Result: PENDING" など無効値で即座に例外
- VerdictParseError: 全ステップ失敗時に例外
- ValueError: max_retries < 1 で例外
- AgentAbortError: handle_abort_verdict() で ABORT 時に例外

### 境界値

- 空文字列入力
- 8000文字ちょうど / 8001文字の入力（切り詰めの境界）
- max_retries = 1 での動作
- ai_formatter が None の場合の Step 3 スキップ

### 後方互換性

- ai_formatter 引数なしで従来どおり動作
- 既存テストが全てパス

## 参考

- v5 実装: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/verdict.py`
- v5 エラー定義: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/errors.py`
- AIToolProtocol: `src/core/tools/protocol.py`
