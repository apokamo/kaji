# [設計] モデル設定: config.toml のモデル/ツール設定移植

Issue: #40

## 概要

v5 の config.toml にあるモデル設定機能を dao に移植し、ツール別のモデル指定を可能にする。

## 背景・目的

現状:
- `src/bugfix_agent/tools/claude.py` は `get_config_value()` で config.toml 対応済み
- `src/core/tools/claude.py` はコンストラクタ引数でハードコード、config.toml 未対応
- config.toml ファイル自体がプロジェクトに存在しない
- ステート別設定（`[states.XXX]`）は未実装

目的:
- config.toml でツール別のデフォルト設定を一元管理
- 将来的なステート別設定の基盤を整備

## インターフェース

### 入力

**config.toml（プロジェクトルート）**:
```toml
[tools.claude]
model = "opus"
permission_mode = "default"
timeout = 1800

[tools.codex]
model = "gpt-5.1-codex-max"
sandbox = "workspace-write"
timeout = 900

[tools.gemini]
model = "gemini-2.5-pro"
timeout = 900

# ステート別設定（Phase 2 以降）
# [states.INIT]
# agent = "claude"
# model = "opus"
```

### 出力

- `ClaudeTool()` 初期化時に config.toml からデフォルト値を読み込む
- 明示的なコンストラクタ引数 > config.toml > ハードコードデフォルト の優先順位

### 使用例

```python
# 1. config.toml のデフォルト値を使用
tool = ClaudeTool()
assert tool.model == "opus"  # config.toml から

# 2. コンストラクタ引数で上書き
tool = ClaudeTool(model="sonnet")
assert tool.model == "sonnet"  # 引数優先

# 3. config.toml がない場合はハードコードデフォルト
# (config.toml を削除した状態)
tool = ClaudeTool()
assert tool.model == "sonnet"  # ハードコードデフォルト
```

## 制約・前提条件

- **config.toml 探索パス**: 既存の `find_config_file()` ロジックを使用
- **後方互換性**: コンストラクタ引数は引き続き動作する
- **ステート別設定は Phase 2**: 本 Issue では `[tools.XXX]` セクションのみ対応

## 対象範囲

### ツール実装状況

| ツール | src/core/tools/ | src/bugfix_agent/tools/ | 本 Issue 対応 |
|--------|-----------------|-------------------------|---------------|
| claude | `claude.py` 存在 | `claude.py` 存在（対応済み） | **対象** |
| codex | 未実装 | 未実装 | スコープ外 |
| gemini | 未実装 | 未実装 | スコープ外 |

**注記**: `src/core/tools/` には現時点で `claude.py` のみ実装。codex/gemini は将来の拡張時に同様のパターンで対応。

### 設定キーの適用対象

| 設定キー | claude | codex | gemini | 備考 |
|----------|--------|-------|--------|------|
| `model` | ✅ | ✅ | ✅ | 全ツール共通 |
| `timeout` | ✅ | ✅ | ✅ | 全ツール共通 |
| `permission_mode` | ✅ | - | - | Claude CLI 固有 |
| `sandbox` | - | ✅ | - | Codex CLI 固有 |

**未対応時の扱い**: 設定キーが存在しない場合、各ツールのハードコードデフォルト値を使用。

## 方針

### アーキテクチャ: 依存関係の整理

```
src/core/config.py          ← config.toml 読み込み機能（移動先）
    ↑
src/core/tools/claude.py    ← core 層ツール

src/bugfix_agent/config.py  ← core/config.py を import（wrapper）
    ↑
src/bugfix_agent/tools/     ← bugfix_agent 層ツール（既存）
```

**原則**: core 層は上位レイヤ（bugfix_agent）に依存しない。

### Phase 1: config.toml 作成と設定読み込み共通化

1. **config.toml をプロジェクトルートに作成**
   - v5 の `[tools.XXX]` セクションを踏襲
   - `[states.XXX]` はコメントアウトで将来対応を示唆

2. **`src/core/config.py` に設定読み込み機能を追加**
   - `find_config_file()` を移動
   - `load_config()` を移動
   - `get_config_value()` を移動
   - 環境変数プレフィックスは `DAO_` を使用（core 層の既存規約）

3. **`src/bugfix_agent/config.py` のリファクタリング**
   - `src/core/config.py` から import
   - 後方互換性のため既存 API は維持（re-export）
   - `BUGFIX_AGENT_` プレフィックスの Settings は維持

4. **`src/core/tools/claude.py` の修正**
   - `src/core/config.py` から `get_config_value()` を import
   - コンストラクタで config.toml を参照するよう変更

### Phase 2: ステート別設定（将来対応）

- `[states.XXX]` セクションの読み込み
- ワークフロー実行時にステートに応じたツール/モデル切り替え
- **本 Issue のスコープ外**

## 検証観点

### 正常系
- config.toml が存在する場合、ツール設定が正しく読み込まれる
- コンストラクタ引数が config.toml より優先される
- config.toml がない場合、ハードコードデフォルトが使用される
- `src/bugfix_agent/config.py` の既存 API が引き続き動作する

### 異常系
- config.toml の構文エラー時にわかりやすいエラーメッセージ
- 存在しないキーへのアクセスでデフォルト値が返る

### 境界値
- 空の config.toml（ファイル存在、内容なし）
- `[tools]` セクションのみ存在（`[tools.claude]` なし）

### 依存関係
- `src/core/tools/claude.py` が `src/bugfix_agent/` に依存しないこと
- `src/bugfix_agent/config.py` が `src/core/config.py` を正しく import すること

## 参考

- v5 config.toml: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/config.toml`
- v5 config.py: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/config.py`
- 既存実装: `src/bugfix_agent/config.py` - `get_config_value()`, `find_config_file()`
