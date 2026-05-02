---
description: post-merge ワークフロー専用の close スキル。マージ操作は行わず、worktree 削除・branch 削除・main 同期・Issue クローズのみを実施する。
name: post-merge-close
---

# Post Merge Close

`wait-merge` / `verify-main-green` / `post-merge-review` を通過した状態から
クリーンアップを行います。既存 `issue-close` の Step 4 以降と等価ですが、
**マージ操作は行いません**（PR は既に merged 状態である前提）。

## いつ使うか

| 状況 | このスキル |
|------|-----------|
| post-merge ワークフローの終端 | ✅ |
| 通常の `feature-development.yaml` 終端 | ❌ — `issue-close` を使う |

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

## 前提条件

- `wait-merge` / `verify-main-green` / `post-merge-review` が PASS で完了していること
- PR が既に merged 状態であること（本スキルでも検証する）

## 実行手順

### Step 0: PR が merged であることの検証

```bash
PR_JSON=$(gh pr view "$PR_NUMBER" --json state,mergedAt,mergeCommit)
STATE=$(python -c "from kaji_harness.postmerge import parse_pr_state; import sys; print(parse_pr_state(sys.stdin.read()))" <<< "$PR_JSON")
if [ "$STATE" != "MERGED" ]; then
    echo "ERROR: PR not in MERGED state ($STATE)" >&2
    # → ABORT
fi
```

### Step 1: Worktree / Branch 情報の取得

`issue-close` の Step 1〜2 と同じ手順で、Issue 本文から worktree 絶対パスと
branch 名を取得し、main repo に移動する。

### Step 2: worktree 削除

```bash
git worktree remove "$MAIN_REPO/../kaji-[prefix]-[number]"
```

### Step 3: ブランチ削除

`issue-close` の Step 4.5 と同じ手順:
- `git fetch origin`
- ローカル: `merge-base --is-ancestor` で安全削除（未マージならスキップしつつ警告）
- リモート: `git ls-remote` 確認 → `git push origin --delete`
- `git fetch --prune origin`

### Step 4: main 同期

```bash
git pull origin main
```

### Step 5: Issue クローズ

```bash
gh issue close [issue-number] --reason completed
```

`gh issue close` が失敗した場合は **ABORT**（Issue が未クローズで残ることは許容しない）。

### Step 6: 完了報告

stdout への報告と Issue タイムラインへのコメント投稿を行う。
フォーマットは `issue-close` Step 6 と同等。「PR」項目は「マージ済み（本スキルでは触らず）」と明記する。

## Verdict 出力

```
---VERDICT---
status: PASS | ABORT
reason: |
  クリーンアップ結果
evidence: |
  worktree / branch / pull / issue close の結果
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クリーンアップ完了 |
| ABORT | PR が MERGED でない / `gh issue close` 失敗 / worktree・branch 削除で復旧不能なエラー |
