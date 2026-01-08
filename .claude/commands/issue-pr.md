---
description: コミット整理、設計書をPR本文に転記、draft/削除、PR作成を一括実行
---

# Issue PR

コードレビュー完了後、PRを作成します。
コミット履歴を整理し、設計書（draft/design/）がある場合はPR本文に転記してからPRを作成します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 必須 |
| レビュー未完了 | ❌ 待機 |

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

3. **設計書がない場合**: Step 5-6 の Design セクションと draft/ 削除をスキップ

### Step 5: PR本文の作成

**設計書がある場合:**

```markdown
## Summary

(Issueの概要を1-2文で)

Closes #[issue-number]

## Design

<details>
<summary>設計書</summary>

(draft/design/ の内容をここに展開)

</details>

## Changes

- (主な変更点)

## Test Plan

- [x] 既存テストがパス
- [ ] 新規テストを追加（該当する場合）
- [ ] 手動検証: (必要な場合)
```

**設計書がない場合:**

```markdown
## Summary

(Issueの概要を1-2文で)

Closes #[issue-number]

## Changes

- (主な変更点)

## Test Plan

- [x] 既存テストがパス
- [ ] 手動検証: (必要な場合)
```

### Step 6: draft/ の削除とコミット（設計書がある場合のみ）

```bash
git rm -rf draft/
git commit -m "chore: remove draft design (moved to PR)"
```

**設計書がない場合はこのステップをスキップ。**

### Step 7: プッシュとPR作成

```bash
git push -u origin HEAD

gh pr create --title "[prefix]: タイトル (#[issue-number])" --body "..."
```

### Step 8: 完了報告

以下の形式で報告してください:

```
## PR作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| PR | #[pr-number] |
| URL | [pr-url] |
| コミット整理 | git absorb 実行済み |
| 設計書 | PR本文に転記済み / なし |
| draft/ | 削除済み / なし |

### 次のステップ

PRのマージ準備ができたら `/issue-close [issue-number]` を実行してください。
```
