# [設計] デフォルトワークフローをPR作成までに縮小

Issue: #93

## 概要

`feature-development.yaml` の自動実行範囲を PR 作成までに縮小し、`close` ステップを削除する。

## 背景・目的

- PR 作成後のマージ判断は人間が行うべき（レビュー確認、CI 結果確認、マージタイミング）
- 自動マージ・close は意図しないマージ事故のリスクがある
- `issue-close` は worktree 削除や `git pull origin main` を伴い、Codex 実行時の CWD / セッション継続挙動に依存する不安定要素がある（#70 の `kaji-run-verify` で観測済み）
- PR 作成を自然な「一時停止ポイント」とすることで、ワークフローの安全性を高める

## インターフェース

### 入力

変更対象ファイル: `workflows/feature-development.yaml`

### 出力

ワークフロー実行時の動作変更:
- **Before**: `pr` → PASS → `close` → PASS → `end`
- **After**: `pr` → PASS → `end`

### 使用例

```bash
# 変更後もワークフロー実行コマンドは同じ
kaji run workflows/feature-development.yaml 99

# PR作成で自動実行が終了する
# close は手動で実行する
/issue-close 99
```

## 制約・前提条件

- `kaji validate` が変更後も通ること（`on` ターゲットの参照先存在チェック）
- 既存テストが通ること
- `close` ステップを参照している他のステップが存在しないこと（`pr` の `PASS` のみ）

## 方針

3箇所の変更のみで完結する最小限の修正:

1. **`pr` ステップの `PASS` 遷移先を `close` → `end` に変更**
   ```yaml
   # Before
   on:
     PASS: close
   # After
   on:
     PASS: end
   ```

2. **`close` ステップ定義を削除**（L114-L121 の 8 行）

3. **`description` を更新**
   ```yaml
   # Before
   description: |
     Issue の設計から PR クローズまでの開発ワークフロー。
   # After
   description: |
     Issue の設計から PR 作成までの開発ワークフロー。
   ```

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト
- 変更後の YAML を `load_workflow_from_str()` でパースし、`validate_workflow()` がエラーなしで通ることを検証
- `pr` ステップの `on.PASS` が `"end"` であることを検証
- `close` ステップが存在しないことを検証
- description が更新されていることを検証

### Medium テスト
- `kaji validate workflows/feature-development.yaml` CLI コマンドが正常終了することを検証（ファイルI/O + バリデーション結合）

### Large テスト
- `kaji run` で実際にワークフローを実行し、PR作成ステップで `end` に遷移して終了することを検証

### スキップするサイズ（該当する場合のみ）
- Large: ワークフロー実行には実際の GitHub Issue・エージェント接続が必要であり、テスト環境で物理的に再現不可能。変更は YAML の静的構造のみであるため、Small + Medium で十分にカバーされる。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/development_workflow.md | あり | ワークフローフロー図・フェーズ概要に `close` の自動実行が含まれている |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 変更対象ファイル | `workflows/feature-development.yaml` | `pr` ステップの `on.PASS: close` と `close` ステップ定義（L105-L121） |
| ワークフローバリデーション | `kaji_harness/workflow.py` L231-235 | `on` ターゲットが存在するステップID or `"end"` であることを検証。`close` 削除時に `pr.on.PASS` を `end` に変更しないとバリデーションエラー |
| #70 の観測 | Issue #93 本文「補足観測」 | close verdict に手動 `cd` + `git pull` が必要だった事実。SKILL の前提と実行環境のずれが原因 |
| 開発ワークフロー | `docs/dev/development_workflow.md` | フロー図に `close` が自動ステップとして含まれており、ドキュメント更新が必要 |
