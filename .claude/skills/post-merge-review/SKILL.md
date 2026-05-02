---
description: マージ済みコミット範囲を codex でレビューし、PASS / RETRY / ABORT を返す。
name: post-merge-review
---

# Post Merge Review

merge commit を含む差分を codex にレビューさせ、`---VERDICT---` ブロックから
最終判定を抽出する。

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

merge commit oid は `wait-merge` / `verify-main-green` 段階で確定済みである前提。

## 実行手順

### Step 1: コミット範囲の確定

```bash
MERGE_SHA=$(gh pr view "$PR_NUMBER" --json mergeCommit -q '.mergeCommit.oid')
PARENT_COUNT=$(git cat-file -p "$MERGE_SHA" | grep -c '^parent ')
RANGE=$(python -c "from kaji_harness.postmerge import select_review_range; print('..'.join(select_review_range('$MERGE_SHA', $PARENT_COUNT)))")
```

判定ロジックは `kaji_harness.postmerge.select_review_range` を使用する。

### Step 2: 差分取得 → codex 実行

```bash
git diff "$RANGE" > /tmp/postmerge.diff
codex exec --model gpt-5.5 --effort medium "Review the following merged diff. Output a VERDICT block." < /tmp/postmerge.diff > /tmp/postmerge.out
```

### Step 3: verdict 抽出

```bash
RESULT=$(python -c "from kaji_harness.postmerge import parse_codex_review_output; import sys; print(parse_codex_review_output(sys.stdin.read()))" < /tmp/postmerge.out)
```

`kaji_harness.postmerge.parse_codex_review_output` で抽出する。

### Step 4: Issue コメント

レビュー結果と本スキル自身の verdict を Issue にコメントする。

## Verdict 出力

```
---VERDICT---
status: PASS | RETRY | ABORT
reason: |
  codex レビューの要約
evidence: |
  対象コミット範囲 / RETRY 時は指摘箇所の引用
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | codex の verdict が PASS |
| RETRY | codex の verdict が RETRY（指摘あり、ワークフローは PAUSE 相当で停止） |
| ABORT | codex 実行失敗 / verdict ブロック欠落 / 対象 commit range が空 |
