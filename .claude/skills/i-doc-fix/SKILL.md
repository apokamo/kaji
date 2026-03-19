---
description: docs review の指摘に対応し、ドキュメントのみを修正する。コードやテストは変更しない。
name: i-doc-fix
---

# I Doc Fix

docs review の指摘事項に対応する。このスキルでも **コード、設定、テストは変更しない**。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-doc-review` または `i-doc-verify` が RETRY のとき | ✅ 必須 |

**ワークフロー内の位置**: update-doc → review-doc → **fix-doc** → verify-doc

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |
| `previous_verdict` | str | 前ステップの verdict |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 実行手順

1. `previous_verdict` または Issue コメントから最新レビュー結果を取得
2. Must Fix を 1 件ずつ検討
3. docs のみ修正
4. 実装 / CLI / CLAUDE.md / 関連 docs との整合を再確認
5. 修正対象ファイルに絞って以下を実行:
   ```bash
   cd [worktree-absolute-path] && python3 scripts/check_doc_links.py [changed-markdown-files...]
   ```
6. docs のみコミット
7. 対応内容を Issue にコメント

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  docs review の指摘に対応した
evidence: |
  Must Fix 項目を修正し、関連する docs と実装の整合を再確認した
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | docs 修正だけでは解決不能 |
