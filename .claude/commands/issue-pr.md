---
description: コミット整理、設計書をIssue本文に追記、draft/削除、PR作成を一括実行
---

# Issue PR

コードレビュー完了後、PRを作成します。
コミット履歴を整理し、設計書（draft/design/）がある場合はIssue本文に追記してからPRを作成します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-sync-docs` 完了後 | ✅ 必須 |
| ドキュメント同期未完了 | ❌ 待機 |

**ワークフロー内の位置**: 実装 → コードレビュー → ドキュメント同期 → **PR作成** → 完了

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提条件

- `/issue-start` が実行済みであること
- 実装とコードレビューが完了していること
- `git absorb` がインストール済みであること（任意）
- `draft/design/` に設計書が存在すること（任意 - なくてもPR作成可能）

## 実行手順

### Step 1: Worktree情報の取得と移動

1. **Issue本文からWorktree情報を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Worktreeパスとprefixを抽出**:
   - `> **Worktree**: \`../[prefix]-[number]\`` からパスを抽出
   - `> **Branch**: \`[prefix]/[number]\`` からprefixを抽出

3. **Worktreeへ移動**:
   ```bash
   cd [worktree-path]
   ```

### Step 2: 未コミットの変更確認

```bash
git status
```

未コミットの変更がある場合は先にコミットしてください。

### Step 3: コミット履歴の整理

```bash
git absorb --and-rebase
```

fixup対象がない場合は何も起きません（正常）。
`git absorb` がインストールされていない場合はスキップ。

### Step 4: 設計書の確認と読み込み

1. **設計書の存在確認**:
   ```bash
   ls draft/design/ 2>/dev/null
   ```

2. **設計書がある場合**: 内容を読み込む
   ```bash
   cat draft/design/issue-[number]-*.md
   ```

3. **設計書がない場合**: Step 5 の Issue本文更新と Step 6 の draft/ 削除をスキップ

### Step 5: Issue本文に設計書を追記（設計書がある場合のみ）

1. **現在のIssue本文を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Issue本文の末尾に設計書セクションを追加**:
   ```bash
   gh issue edit [issue-number] --body "$(cat <<'EOF'
   (既存のIssue本文)

   ---

   ## 設計書

   <details>
   <summary>クリックして展開</summary>

   (draft/design/ の内容をここに展開)

   </details>
   EOF
   )"
   ```

**設計書がない場合はこのステップをスキップ。**

### Step 6: draft/ の削除とコミット（設計書がある場合のみ）

```bash
git rm -rf draft/
git commit -m "chore: remove draft design (moved to Issue)"
```

**設計書がない場合はこのステップをスキップ。**

### Step 7: プッシュとPR作成

```bash
git push -u origin HEAD
```

```bash
gh pr create --base main --title "[prefix]: タイトル (#[issue-number])" --body "$(cat <<'EOF'
## Summary

(Issueの概要を1-2文で)

Closes #[issue-number]

## Changes

- (主な変更点)

## Test Plan

- [x] 既存テストがパス
- [ ] 新規テストを追加（該当する場合）
- [ ] 手動検証: (必要な場合)
EOF
)"
```

### Step 8: Issue本文にPR番号を追記

PR作成後、Issue本文のメタ情報にPR番号を追加:

```bash
gh issue edit [issue-number] --body "$(既存本文の **Branch** 行の後に **PR**: #[pr-number] を追加)"
```

### Step 9: 完了報告

以下の形式で報告してください:

```
## PR作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| PR | #[pr-number] |
| URL | [pr-url] |
| コミット整理 | git absorb 実行済み |
| 設計書 | Issue本文に追記済み / なし |
| draft/ | 削除済み / なし |

### 次のステップ

PRのマージ準備ができたら `/issue-close [issue-number]` を実行してください。
```
