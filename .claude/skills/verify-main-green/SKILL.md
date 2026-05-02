---
description: マージコミットを含む main の最新 CI run が green であることを `gh run list` で確認する。
name: verify-main-green
---

# Verify Main Green

`wait-merge` で取得した merge commit oid を使い、`gh run list --branch main --commit <oid>` で
当該コミットに紐付く CI run を取得し、判定する。

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

`wait-merge` 段階で確定した merge commit oid を、Issue コメントまたは `gh pr view` から再取得する。

## 実行手順

### Step 1: merge commit oid の取得

```bash
MERGE_SHA=$(gh pr view "$PR_NUMBER" --json mergeCommit -q '.mergeCommit.oid')
```

### Step 2: CI run 一覧取得

```bash
RUNS_JSON=$(gh run list --branch main --commit "$MERGE_SHA" --json status,conclusion,databaseId,name)
```

### Step 3: 判定

```bash
RESULT=$(python -c "from kaji_harness.postmerge import judge_main_ci; import sys; print(judge_main_ci(sys.stdin.read()))" <<< "$RUNS_JSON")
```

判定ロジックは `kaji_harness.postmerge.judge_main_ci` を使用する。

| RESULT | verdict |
|--------|---------|
| GREEN | PASS |
| IN_PROGRESS | RETRY（同一ステップを再走させて待機） |
| FAILED / NOT_FOUND | ABORT |

### Step 4: Issue コメント

PASS / ABORT のときに結果を Issue へコメントする（IN_PROGRESS 時は省略）。

## Verdict 出力

```
---VERDICT---
status: PASS | RETRY | ABORT
reason: |
  CI 状態
evidence: |
  run id / conclusion / status の要約
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全 run が success（skipped/neutral 含む） |
| RETRY | いずれかが in_progress / queued |
| ABORT | いずれかが failure / cancelled / timed_out、もしくは run 未発見 |
