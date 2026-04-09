# [設計] config/workflow の暗黙デフォルト値廃止と必須フィールド厳格化

Issue: #135

## 概要

`artifacts_dir` と `execution_policy` のサイレントフォールバックを廃止し、未設定時にバリデーションエラーとする。併せてドキュメントの記載をコードの実態に合わせる。

## 背景・目的

設定値にサイレントフォールバックがあると、設定ミスが実行時まで発覚しない。Issue #901 の実装時に `execution_policy` のフォールバック問題で `bypassPermissions` の挙動が不安定になった実績がある。また `artifacts_dir` が黙って `~/.kaji/artifacts` にファイルを書くのはユーザーの意図に反する。`skill_dir` や `default_timeout` と同じ「未設定ならエラー」方針に統一する。

## インターフェース

### 入力

#### config.toml（変更後の最小構成）

```toml
[paths]
artifacts_dir = ".kaji/artifacts"   # 必須（デフォルト値なし）
skill_dir = ".claude/skills"        # 必須（既存）

[execution]
default_timeout = 1800              # 必須（既存）
```

#### workflow YAML（execution_policy）

```yaml
execution_policy: auto   # 必須（デフォルト値なし）
```

### 出力

- `artifacts_dir` 未設定時: `ConfigLoadError("paths.artifacts_dir is required")`
- `execution_policy` 未設定時: `WorkflowValidationError("'execution_policy' is required")`

### 使用例

```python
# artifacts_dir 未設定の config.toml → エラー
# [paths]
# skill_dir = ".claude/skills"
config = KajiConfig.discover()
# => ConfigLoadError: paths.artifacts_dir is required

# execution_policy 未設定の workflow YAML → エラー
workflow = load_workflow(Path("workflow.yaml"), config)
# => WorkflowValidationError: 'execution_policy' is required
```

## 制約・前提条件

- 既存の `config.toml` で `artifacts_dir` を省略しているユーザーは、アップグレード時にエラーになる（意図的な breaking change）
- 既存の workflow YAML で `execution_policy` を省略しているものも同様
- `execution_policy` の有効値 `auto` / `sandbox` / `interactive` 自体は変更しない

## 方針

### 1. `artifacts_dir` のデフォルト値削除（config.py）

- `PathsConfig.artifacts_dir` のデフォルト値 `"~/.kaji/artifacts"` を削除し、`skill_dir` と同様に必須フィールド化
- `_load()` で `paths_data.get("artifacts_dir")` が `None` の場合に `ConfigLoadError` を送出
- 既存の `_validate_artifacts_dir` ロジックはそのまま維持

```python
# 変更前 (config.py:20)
artifacts_dir: str = "~/.kaji/artifacts"

# 変更後
artifacts_dir: str = ""  # Required. Empty string = not set.
```

```python
# 変更前 (config.py:71)
artifacts_raw = paths_data.get("artifacts_dir", PathsConfig.artifacts_dir)

# 変更後
artifacts_raw = paths_data.get("artifacts_dir")
if artifacts_raw is None:
    raise ConfigLoadError(path, "paths.artifacts_dir is required")
```

### 2. `execution_policy` のフォールバック削除（workflow.py）

- `data.get("execution_policy", "auto")` → `data.get("execution_policy")` に変更
- `None` の場合に `WorkflowValidationError` を送出

```python
# 変更前 (workflow.py:196)
execution_policy = data.get("execution_policy", "auto")

# 変更後
execution_policy = data.get("execution_policy")
if execution_policy is None:
    raise WorkflowValidationError("'execution_policy' is required")
```

### 3. ドキュメント更新（workflow-authoring.md）

- 最小構成例に `skill_dir` を追加
- `artifacts_dir` のコメントから「省略時のデフォルト値」を削除し「必須」に変更
- `execution_policy` セクションに各エージェント別フラグの具体的動作を追記:

| policy | Claude | Codex | Gemini |
|--------|--------|-------|--------|
| `auto` | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` |
| `sandbox` | (フラグなし) | `-s workspace-write` | `-s` |
| `interactive` | (フラグなし) | (フラグなし) | (フラグなし) |

### 4. runner.py のフォールバック削除

- `runner.py:55` の `self.workflow.execution_policy or "auto"` → `self.workflow.execution_policy` に変更（workflow パース時点で必須保証済みのため）

### 5. テスト fixture の更新

- `tests/fixtures/test_workflow.yaml` に `execution_policy: auto` を追加する
- これは既存テストが workflow パース時のバリデーション強化で失敗しないようにするための最小変更
- `execution_policy` 未設定エラーの検証は、専用の fixture（インラインまたは別ファイル）で行う

### 6. README.md の最小導入例更新

- `README.md:47-53` の最小導入例に `skill_dir` を追加
- `artifacts_dir` のコメントから旧デフォルト値の示唆を削除し「必須」に変更

### 7. docs/ARCHITECTURE.md の記載更新

- `docs/ARCHITECTURE.md:203` の「デフォルト: `~/.kaji/artifacts`」を「必須設定項目」に修正

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ
- 実行時コード変更（バリデーションロジックの変更）+ docs 更新

### Small テスト
- `artifacts_dir` 未設定時に `ConfigLoadError` が送出されること
- `artifacts_dir` 設定時に従来通りパースされること（既存テストの修正: fixture に `artifacts_dir` を明示追加）
- `execution_policy` 未設定時に `WorkflowValidationError` が送出されること
- `execution_policy` 設定時に従来通りパースされること（既存テスト fixture `tests/fixtures/test_workflow.yaml` に `execution_policy: auto` を追加）
- `PathsConfig.artifacts_dir` のデフォルト値が空文字列であること（直接構築時の未設定 sentinel としてのテスト。`_load()` で必須化するため実運用では到達しないが、dataclass の直接構築時にデフォルト空文字列 = 未設定を表現する sentinel として機能することを検証）

### Medium テスト
- 実ファイルシステム上で `config.toml` に `artifacts_dir` 未記載の場合にエラーになること
- 実ファイルシステム上で workflow YAML に `execution_policy` 未記載の場合にエラーになること

### Large テスト
- 該当なし（外部サービス疎通は不要）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| README.md | あり | `README.md:47-53` の最小導入例に `skill_dir` が欠落し、`artifacts_dir = "~/.kaji/artifacts"` が旧デフォルト値のまま記載されている。必須化に伴い `skill_dir` 追加 + `artifacts_dir` コメント修正が必要 |
| docs/ARCHITECTURE.md | あり | `docs/ARCHITECTURE.md:203` に「デフォルト: `~/.kaji/artifacts`」と明記されている。デフォルト廃止に伴い「必須」に修正が必要 |
| docs/dev/workflow-authoring.md | あり | 最小構成例の修正 + execution_policy 動作詳細追記 |
| docs/adr/ | なし | 新しい技術選定なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| config.py | `kaji_harness/config.py:20` | `artifacts_dir: str = "~/.kaji/artifacts"` — 現行のデフォルト値。`skill_dir` は空文字列（必須）なのに `artifacts_dir` だけデフォルトがある不整合 |
| workflow.py | `kaji_harness/workflow.py:196` | `data.get("execution_policy", "auto")` — サイレントフォールバック箇所 |
| runner.py | `kaji_harness/runner.py:55` | `self.workflow.execution_policy or "auto"` — 二重フォールバック箇所 |
| workflow-authoring.md | `docs/dev/workflow-authoring.md:9-16` | 最小構成例に `skill_dir` 欠落 |
| workflow-authoring.md | `docs/dev/workflow-authoring.md:97-103` | `execution_policy` の動作説明にエージェント別フラグの記載なし |
| cli.py | `kaji_harness/cli.py:247-294` | 各エージェントの `execution_policy` → CLI フラグ変換ロジック。設計書の動作表はこのコードから導出 |
| README.md | `README.md:47-53` | 最小導入例に `skill_dir` がなく、`artifacts_dir = "~/.kaji/artifacts"` が旧デフォルト相当で残っている |
| ARCHITECTURE.md | `docs/ARCHITECTURE.md:203` | 「デフォルト: `~/.kaji/artifacts`」と明記されており、必須化後は不整合 |
| test_workflow.yaml | `tests/fixtures/test_workflow.yaml:1-49` | `execution_policy` 未記載。必須化後に既存テストが `WorkflowValidationError` で失敗する |
| Issue #135 本文 | GitHub Issue #135 | 発見経緯: `bypassPermissions` モードで `.claude/skills/` への書き込みがブロックされた事象から `execution_policy` フォールバック問題を発見 |
