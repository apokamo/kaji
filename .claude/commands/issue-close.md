---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ削除を一括実行
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号 (例: 6)

## 前提条件

- `/issue-pr` でPRが作成済みであること
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

### Step 1: Worktree情報の取得

Issue本文からWorktree情報を取得します:

```bash
gh issue view [issue-number] --json body -q '.body'
```

以下の情報を抽出:
- `> **Worktree**: \`../[prefix]-[number]\`` → worktree パス
- `> **Branch**: \`[prefix]/[number]\`` → ブランチ名

### Step 2: メインリポジトリに移動

worktree内にいる場合は先にメインリポジトリに移動:

```bash
cd /home/aki/dev/dev-agent-orchestra/main
```

### Step 3: PRのマージ

```bash
gh pr merge [branch-name] --merge --delete-branch
```

マージコミットを作成してブランチ履歴を保持する。

### Step 4: worktree削除

```bash
git worktree remove [worktree-path]
```

### Step 5: mainを最新化

```bash
git pull origin main
```

### Step 6: 完了報告

以下の形式で報告してください:

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | #[issue-number] |
| PR | マージ済み |
| worktree | 削除済み |
| リモートブランチ | 削除済み (--delete-branch) |
| main | 最新化済み |

作業ディレクトリは /home/aki/dev/dev-agent-orchestra/main に戻りました。
```
