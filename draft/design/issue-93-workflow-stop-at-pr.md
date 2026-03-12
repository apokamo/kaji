# [設計] デフォルトワークフローをPR作成までに縮小

Issue: #93

## 概要

`feature-development.yaml` の自動実行範囲を PR 作成までに縮小し、`close` ステップを削除する。

## 背景・目的

- PR 作成後のマージ判断は人間が行うべき（レビュー確認、CI 結果確認、マージタイミング）
- 自動マージ・close は意図しないマージ事故のリスクがある
- `issue-close` は worktree 削除や `git pull origin main` を伴い、Codex 実行時の CWD / セッション継続挙動に依存する不安定要素がある（[#70 kaji-run-verify 実行結果](https://github.com/apokamo/kaji/issues/70#issuecomment-4047582273) で観測）
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

YAML 変更 3 箇所 + ドキュメント更新 1 箇所:

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

4. **`docs/dev/development_workflow.md` を更新**
   - フロー図（mermaid）から `close` の自動遷移を削除し、`pr` → `end` に変更
   - フェーズ概要テーブルの「6. 完了」行の説明を「手動実行（`/issue-close`）」に変更
   - 詳細フロー（ASCII）の Phase 6 に「※ワークフロー外。手動で実行」の注記を追加

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
- 実装完了後、`/kaji-run-verify workflows/feature-development.yaml <issue>` で実機検証を行い、ワークフローが `pr` ステップ完了後に正常終了することを確認する
- 検証結果は Issue コメントとして記録する（#70 での実績: [kaji-run-verify 実行結果](https://github.com/apokamo/kaji/issues/70#issuecomment-4047582273)）

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
| #70 の観測 | [#70 kaji-run-verify 実行結果](https://github.com/apokamo/kaji/issues/70#issuecomment-4047582273) | close verdict に手動 `cd` + `git pull` が必要だった事実。SKILL の前提と実行環境のずれが原因 |
| 開発ワークフロー | `docs/dev/development_workflow.md` | フロー図に `close` が自動ステップとして含まれており、ドキュメント更新が必要 |
