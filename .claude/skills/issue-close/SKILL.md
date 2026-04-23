---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ安全削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
PR マージ、worktree 削除、ブランチ削除、Issue クローズを一括実行します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

**ワークフロー内の位置**: implement → review-code → i-dev-final-check → i-pr → **close**

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

`git worktree list` の最初の行が常に main worktree（bare repository のルート）を示す:

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

マージコミットを作成してブランチ履歴を保持する。ブランチ削除は worktree 削除後に Step 4.5 で行う。

> **結果を記録**: `pr_merge_result` = 「マージ済み」。この値は Step 6 で使用する。

### Step 4: worktree削除

```bash
git worktree remove "$MAIN_REPO/../kaji-[prefix]-[number]"
```

> `$MAIN_REPO` は Step 2 で取得済み。

> **結果を記録**: `worktree_result` = 「削除済み」。この値は Step 6 で使用する。

### Step 4.5: ブランチ削除

worktree 削除後にローカル・リモートブランチを削除する。
`git fetch origin` で `origin/main` を最新化してから、`merge-base --is-ancestor` でマージ済み判定を行い、安全に削除する。
ローカル削除とリモート削除は独立して実行し、片方の失敗がもう片方をブロックしない。
ブランチが既に存在しない場合はスキップする。

```bash
# 1. fetch して origin/main を更新
git fetch origin

# 2. ローカルブランチ削除: 存在確認 → マージ済み判定 → 安全な -D
if git show-ref --verify --quiet refs/heads/[branch-name]; then
    if git merge-base --is-ancestor [branch-name] origin/main; then
        git branch -D [branch-name]
    else
        echo "WARNING: branch not merged into origin/main, skipping local delete"
    fi
fi

# 3. リモートブランチ削除（ローカル削除の成否に依存しない）
git ls-remote --exit-code --heads origin [branch-name] >/dev/null 2>&1
LS_EXIT=$?
if [ "$LS_EXIT" -eq 0 ]; then
    if ! git push origin --delete [branch-name]; then
        echo "ERROR: git push origin --delete failed"
        exit 1
    fi
elif [ "$LS_EXIT" -eq 2 ]; then
    echo "INFO: remote branch already deleted"
else
    echo "ERROR: git ls-remote failed (exit $LS_EXIT)"
    exit 1
fi

# 4. stale remote-tracking ref を掃除
git fetch --prune origin
```

> **結果を記録**:
> - `local_branch_result` = 「削除済み」/「未存在を確認」/「未マージのためスキップ」
> - `remote_branch_result` = 「削除済み」/「未存在を確認」/「削除失敗（要手動対応）」
>
> これらの値は Step 6 で使用する。

### Step 5: mainを最新化

```bash
git pull origin main
```

> **結果を記録**: `pull_result` = 「最新化済み」。この値は Step 6 で使用する。

### Step 5.5: Issue クローズ

```bash
gh issue close [issue-number] --reason completed
```

> **結果を記録**: `close_result` = 「クローズ済み」/「クローズ失敗（要手動対応）」。この値は Step 6 で使用する。
>
> **重要**: `gh issue close` が失敗した場合は verdict を **ABORT** にすること。Issue が未クローズのまま残ることは許容しない。

### Step 6: 完了報告

Step 3〜5.5 の結果を使って、**stdout への報告**と **Issue タイムラインへのコメント投稿**の両方を行う。

#### 6a. Issue コメント投稿

各ステップで記録した結果変数を使い、コメント内容を動的に組み立てて投稿する:

```bash
gh issue comment [issue-number] --body-file - <<'COMMENT_EOF'
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| Issue | [close_result] |
COMMENT_EOF

```

> `[pr_merge_result]` 等のプレースホルダーは、実際の実行結果に置き換えること。ハードコードしない。

#### 6b. stdout 報告

以下の形式で報告してください:

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | #[issue-number] |
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| Issue 状態 | [close_result] |
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  クローズ完了
evidence: |
  PR マージ・worktree 削除・main 最新化・Issue クローズ済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| ABORT | クローズ失敗（`gh issue close` 失敗を含む） |
