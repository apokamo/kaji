---
description: PR の merge 完了をポーリングし、merged 状態に達したら PASS を返す。マージ前提の post-merge ワークフロー終端で使用する。
name: wait-merge
---

# Wait Merge

PR が merged 状態に到達するまで `gh pr view` をポーリングします。
`feature-with-postmerge.yaml` の `i-pr` 直後に配置し、`verify-main-green` 以降を起動するゲートとして使います。

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

### 設定

- ポーリング間隔 / 最大回数は本ファイル冒頭の定数で固定（将来 workflow YAML 経由で上書き可能にする余地あり）
  - `POLL_INTERVAL_SEC=60`
  - `POLL_MAX_TRIES=60` （= 60 分）

## 前提条件

- `/i-pr` で PR が作成済みであること
- `gh` CLI が利用可能であること

## 実行手順

### Step 1: Worktree / branch / PR 番号の特定

Issue 本文の Worktree / Branch メタ情報を読み取り、PR 番号を取得する:

```bash
PR_NUMBER=$(gh pr list --state all --search "head:[branch-name]" --json number -q '.[0].number')
```

### Step 2: ポーリング

```bash
for i in $(seq 1 60); do
    PR_JSON=$(gh pr view "$PR_NUMBER" --json state,mergedAt,mergeCommit)
    STATE=$(python -c "from kaji_harness.postmerge import parse_pr_state; import sys; print(parse_pr_state(sys.stdin.read()))" <<< "$PR_JSON")
    case "$STATE" in
        MERGED) echo "merged"; break ;;
        CLOSED_NOT_MERGED) echo "closed without merge"; exit 2 ;;
        *) sleep 60 ;;
    esac
done
```

### Step 3: 結果判定

- `STATE=MERGED` ⇒ merge commit oid を後続ステップに渡せる状態。PASS
- ループが上限到達 ⇒ RETRY（後続ステップは起動せずワークフロー停止）
- `STATE=CLOSED_NOT_MERGED` または `gh` 失敗 ⇒ ABORT

判定ロジックは `kaji_harness.postmerge.parse_pr_state` を使用すること（独自に state 文字列を比較しない）。

### Step 4: Issue コメント

merged に到達したら Issue にコメントを残す:

```bash
gh issue comment [issue-number] --body "wait-merge: PR #$PR_NUMBER merged at <mergedAt>"
```

## Verdict 出力

```
---VERDICT---
status: PASS | RETRY | ABORT
reason: |
  状況の要約
evidence: |
  PR 状態 / merge commit oid / ポーリング回数
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | PR が merged に到達 |
| RETRY | 上限回ポーリングしても未マージ（タイムアウト相当） |
| ABORT | PR closed (not merged) / PR 未発見 / `gh` API 失敗 |
