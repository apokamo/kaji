---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ安全削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
設計書アーカイブは `/i-dev-final-check` で実施済みの前提です。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

**ワークフロー内の位置**: implement → review-code → doc-check → i-dev-final-check → i-pr → **close**

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提条件

- `/i-pr` でPRが作成済みであること
- `/i-dev-final-check` で設計書アーカイブが完了していること（dev workflow の場合）
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

### Step 1: Worktree情報の取得

Issue本文からWorktree情報を取得します:

```bash
gh issue view [issue-number] --json body -q '.body'
```

以下の情報を抽出:
- `> **Worktree**: \`../kaji-[prefix]-[number]\`` → worktree パス
- `> **Branch**: \`[prefix]/[number]\`` → ブランチ名

### Step 2: メインリポジトリのパスを特定

```bash
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
```

> **注意**: `git rev-parse --show-toplevel` は現在の worktree のルートを返すため、
> worktree 内から実行すると main repo を取得できない。必ず `git worktree list` を使うこと。

worktree 内にいる場合は main repo に移動:

```bash
cd "$MAIN_REPO"
```

### Step 3: PRのマージ

```bash
gh pr merge [branch-name] --merge
```

マージコミットを作成してブランチ履歴を保持する。
`--delete-branch` は使わない（Step 4 で安全に削除する）。

### Step 4: ブランチの安全削除

マージ完了を確認してからブランチを削除する。

#### 4a. マージ確認

```bash
cd "$MAIN_REPO" && git fetch origin main && git fetch origin [branch-name] 2>/dev/null
BRANCH_TIP=$(git rev-parse origin/[branch-name] 2>/dev/null)
if [ -n "$BRANCH_TIP" ]; then
  git merge-base --is-ancestor "$BRANCH_TIP" origin/main && echo "MERGED" || echo "NOT_MERGED"
fi
```

`NOT_MERGED` の場合はブランチ削除を中止し、ユーザーに報告する。

#### 4b. リモートブランチ削除

```bash
git push origin --delete [branch-name] 2>/dev/null || echo "Remote branch already deleted"
```

#### 4c. ローカルブランチ削除

```bash
git branch -d [branch-name] 2>/dev/null || echo "Local branch already deleted or not found"
```

> **注意**: local と remote は独立して処理する。一方の失敗が他方に影響しないこと。

### Step 5: .venv シンボリックリンク削除

worktree 削除前に `.venv` シンボリックリンクを削除（untracked files エラー回避）:

```bash
WORKTREE_PATH=$(realpath "$MAIN_REPO/../kaji-[prefix]-[number]")
rm "$WORKTREE_PATH/.venv"
```

### Step 6: worktree削除

```bash
git worktree remove "$WORKTREE_PATH"
```

### Step 7: mainを最新化

```bash
git pull origin main
```

### Step 8: stale ref のクリーンアップ

```bash
git worktree prune
git remote prune origin
```

### Step 9: 完了報告

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | #[issue-number] |
| PR | マージ済み |
| .venv symlink | 削除済み |
| worktree | 削除済み |
| ブランチ（remote） | 削除済み |
| ブランチ（local） | 削除済み |
| main | 最新化済み |
| stale refs | クリーンアップ済み |
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  クローズ完了
evidence: |
  PR マージ・ブランチ安全削除・worktree 削除・main 最新化済み
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| RETRY | マージ失敗等 |
| ABORT | 重大な問題 |
