# [設計] モデル設定: config.toml のモデル/ツール設定移植

Issue: #40

## 概要

v5 の config.toml にあるモデル設定機能を dao に移植し、ツール別・ステート別のモデル指定を可能にする。

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
- **bugfix_agent は既に対応済み**: 主に `src/core/tools/` の対応が必要

## 方針

### Phase 1: config.toml 作成と core/tools 対応

1. **config.toml をプロジェクトルートに作成**
   - v5 の `[tools.XXX]` セクションを踏襲
   - `[states.XXX]` はコメントアウトで将来対応を示唆

2. **src/core/tools/claude.py の修正**
   - `get_config_value()` を `src/bugfix_agent/config.py` から import
   - コンストラクタで config.toml を参照するよう変更

3. **設定読み込みの共通化（オプション）**
   - `src/core/config.py` に `get_config_value()` を移動または re-export
   - `src/bugfix_agent/config.py` は `src/core/config.py` を import

### Phase 2: ステート別設定（将来対応）

- `[states.XXX]` セクションの読み込み
- ワークフロー実行時にステートに応じたツール/モデル切り替え
- **本 Issue のスコープ外**

## 検証観点

### 正常系
- config.toml が存在する場合、ツール設定が正しく読み込まれる
- コンストラクタ引数が config.toml より優先される
- config.toml がない場合、ハードコードデフォルトが使用される

### 異常系
- config.toml の構文エラー時にわかりやすいエラーメッセージ
- 存在しないキーへのアクセスでデフォルト値が返る

### 境界値
- 空の config.toml（ファイル存在、内容なし）
- `[tools]` セクションのみ存在（`[tools.claude]` なし）

## 参考

- v5 config.toml: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/config.toml`
- v5 config.py: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/config.py`
- 既存実装: `src/bugfix_agent/config.py` - `get_config_value()`, `find_config_file()`
