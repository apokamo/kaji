---
description: コード修正が適切に行われたかを確認する。新規指摘は行わない（レビュー収束のため）。
---

# Issue Verify Code

コード修正後の確認を行います。

**重要**: このコマンドは「指摘事項が適切に修正されたか」のみを確認します。
**新規の指摘は行いません**。これはレビューサイクルの収束を保証するためです。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-fix-code` 後の修正確認 | ✅ 必須 |
| 新規レビューが必要な場合 | ❌ `/issue-review-code` を使用 |

**ワークフロー内の位置**: 実装 → コードレビュー → (fix → **verify** →) → ドキュメント同期 → PR作成

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## verify と review の違い

| 項目 | review | verify |
|------|--------|--------|
| 目的 | フルレビュー | 修正確認のみ |
| 新規指摘 | する | **しない** |
| 確認範囲 | コード全体 | 前回指摘箇所のみ |
| 使用タイミング | 実装完了後 | fix 後 |

## 実行手順

### Step 1: コンテキスト取得

1. **Issue本文からWorktree情報を取得して移動**

2. **前回の指摘内容を取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   「コードレビュー結果」と「レビュー指摘への対応報告」を確認。

3. **修正差分を確認**:
   ```bash
   git diff HEAD~1
   ```

### Step 2: 修正確認（限定的チェック）

**確認すること:**
- 前回の「指摘事項 (Must Fix)」が適切に修正されているか
- 「見送り」とした項目の理由が妥当か
- 修正によるデグレードがないか

**確認しないこと:**
- 新しい問題点の探索
- 追加の改善提案

### Step 3: 確認結果のコメント

```bash
gh issue comment [issue-number] --body "..."
```

**コメント本文構成:**

```markdown
# コード修正確認結果

## 確認結果

| 指摘項目 | 状態 |
|----------|------|
| (項目1) | OK / 要再修正 |
| (項目2) | OK / 要再修正 |

## 判定

[ ] Approve (PR作成可)
[ ] Changes Requested (再修正が必要)

## 次のステップ

(Approve の場合)
`/issue-pr [issue-number]` でPRを作成してください。

(Changes Requested の場合)
`/issue-fix-code [issue-number]` で再度修正してください。
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## コード修正確認完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-pr [issue-number]` でPR作成
- Changes Requested: `/issue-fix-code [issue-number]` で再修正
```
