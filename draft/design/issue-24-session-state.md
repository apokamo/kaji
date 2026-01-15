# [設計] SessionState 拡張 - ループカウンター・会話ID管理

Issue: #24

## 概要

`SessionState` をデータクラス化し、ループカウンター管理と会話ID管理メソッドを追加する。

## 背景・目的

現行の `SessionState`（`src/workflows/base.py`）は基本構造のみで、メソッドを持たない:

```python
class SessionState:
    def __init__(self) -> None:
        self.completed_states: list[str] = []
        self.loop_counters: dict[str, int] = {}
        self.active_conversations: dict[str, str | None] = {}
```

v5 bugfix-v5 の `SessionState` は以下の機能を持つ:
- **ループカウンター**: 無限ループ防止のためのリトライ回数管理
- **会話ID管理**: エージェント間での会話コンテキスト追跡

これらの機能を dao にも移植し、ワークフロー実行の堅牢性を向上させる。

## インターフェース

### 入力

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| max_loop_count | int | 3 | ループ上限（設定可能） |

### 出力

`SessionState` インスタンス。以下のメソッドを提供:

| メソッド | 戻り値 | 説明 |
|---------|-------|------|
| `increment_loop(state_name)` | int | カウンタをインクリメントし、新しい値を返す |
| `reset_loop(state_name)` | None | カウンタをリセット |
| `is_loop_exceeded(state_name)` | bool | ループ上限に達したか |
| `set_conversation_id(role, conv_id)` | None | 会話IDを設定 |
| `get_conversation_id(role)` | str \| None | 会話IDを取得 |
| `mark_completed(state_name)` | None | ステートを完了としてマーク |
| `is_completed(state_name)` | bool | ステートが完了済みか |

### 使用例

```python
from src.workflows.base import SessionState

# 初期化
session = SessionState(max_loop_count=5)

# ループカウンター
session.increment_loop("DESIGN")  # -> 1
session.increment_loop("DESIGN")  # -> 2
session.is_loop_exceeded("DESIGN")  # -> False (< 5)

# 会話ID管理
session.set_conversation_id("reviewer", "conv-123")
session.get_conversation_id("reviewer")  # -> "conv-123"

# 完了ステート管理
session.mark_completed("INIT")
session.is_completed("INIT")  # -> True
```

## 制約・前提条件

- **技術的制約**:
  - Python 3.11+ (`dataclass` 使用)
  - 既存の `WorkflowBase` との後方互換性を維持
  - `SessionState` は `src/workflows/base.py` に残す（移動しない）

- **設計上の判断**:
  - v5 の `current_state` フィールドは含めない（ワークフロー側で管理）
  - ループカウンター名は任意の文字列を許容（ステート名に限定しない）

## 方針

### 実装方針

1. `SessionState` を `@dataclass` に変換
2. フィールドに `field(default_factory=...)` を使用
3. メソッドは純粋関数的に実装（副作用は自身のフィールド変更のみ）

### 疑似コード

```python
from dataclasses import dataclass, field

@dataclass
class SessionState:
    completed_states: list[str] = field(default_factory=list)
    loop_counters: dict[str, int] = field(default_factory=dict)
    active_conversations: dict[str, str | None] = field(default_factory=dict)
    max_loop_count: int = 3

    def increment_loop(self, state_name: str) -> int:
        self.loop_counters[state_name] = self.loop_counters.get(state_name, 0) + 1
        return self.loop_counters[state_name]

    def reset_loop(self, state_name: str) -> None:
        self.loop_counters[state_name] = 0

    def is_loop_exceeded(self, state_name: str) -> bool:
        return self.loop_counters.get(state_name, 0) >= self.max_loop_count

    def set_conversation_id(self, role: str, conv_id: str | None) -> None:
        self.active_conversations[role] = conv_id

    def get_conversation_id(self, role: str) -> str | None:
        return self.active_conversations.get(role)

    def mark_completed(self, state_name: str) -> None:
        if state_name not in self.completed_states:
            self.completed_states.append(state_name)

    def is_completed(self, state_name: str) -> bool:
        return state_name in self.completed_states
```

## ファイル変更計画

### 変更ファイル

| ファイル | 変更内容 |
|----------|----------|
| `src/workflows/base.py` | `SessionState` をデータクラス化、メソッド追加 |
| `tests/workflows/test_session_state.py` | 新規: ユニットテスト |

### ドキュメント更新

| ファイル | 変更内容 |
|----------|----------|
| `docs/architecture.md` | Core Components セクションに `SessionState` を追加 |

## 検証観点

### 正常系

- `increment_loop()` が正しくインクリメントするか
- `reset_loop()` が 0 にリセットするか
- `is_loop_exceeded()` が `max_loop_count` と正しく比較するか
- `set_conversation_id()` / `get_conversation_id()` が正しく動作するか
- `mark_completed()` が重複登録を防ぐか
- `is_completed()` が正しく判定するか

### 異常系・境界値

- 未登録のステート名に対する `increment_loop()` が 1 を返すか
- 未登録のステート名に対する `is_loop_exceeded()` が False を返すか
- 未登録のロールに対する `get_conversation_id()` が None を返すか
- `max_loop_count=0` の場合、最初の `is_loop_exceeded()` が False を返すか（0 >= 0 は False）
- `max_loop_count=1` の場合、1回目の `increment_loop()` 後に `is_loop_exceeded()` が True になるか

### 後方互換性

- 引数なしの `SessionState()` が正しく動作するか（デフォルト値）
- 既存の `DesignWorkflow` との統合が正常に動作するか

## 参考

- 移植元: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/state.py`
- 親Issue: #1 (ADR レビュー: dev-agent-orchestra 設計検討)
- Python dataclasses: https://docs.python.org/3/library/dataclasses.html
