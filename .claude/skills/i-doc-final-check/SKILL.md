---
description: docs-only workflow 向けの最終チェック。docs 整合と Issue 状態を確認し、PR に進めるか判定する。
name: i-doc-final-check
---

# I Doc Final Check

docs-only workflow の PR 前最終ゲート。
現行実装、CLI、運用方針、workflow 定義との整合を確認し、docs-only として PR に進めるかを判定する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/i-doc-review` または `/i-doc-verify` で Approve 後 | ✅ 必須 |

**ワークフロー内の位置**: i-doc-update → i-doc-review → **i-doc-final-check** → i-pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

## 前提知識の読み込み

1. [docs/dev/docs_maintenance_workflow.md](../../../docs/dev/docs_maintenance_workflow.md)
2. [docs/dev/workflow_completion_criteria.md](../../../docs/dev/workflow_completion_criteria.md)
3. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
4. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)
5. `docs/README.md`

## 実施内容

1. worktree と branch を解決する
2. docs / workflow / skill 参照の整合を確認する
3. links、コマンド例、導線の整合を確認する（`make verify-docs`）
4. Issue 本文の完了条件を照合し、充足状態を更新する
5. Issue に最終チェック結果をコメントする

## Step 3 詳細: リンク整合性

```bash
cd [worktree_dir] && source .venv/bin/activate && make verify-docs
```

exit 0 必須。

## Step 4 詳細: Issue 本文の完了条件更新

Issue 本文に `## 完了条件` セクション（チェックボックス形式）がある場合:

### PASS の場合

チェックボックスを `[x]` に更新する。

```bash
kaji issue view [issue_id] --json body -q '.body' > /tmp/issue-body.md
# 確認済み条件を [x] に変更
kaji issue edit [issue_id] --commit --body-file /tmp/issue-body.md
```

### BACK の場合

チェックボックスは `[ ]` のまま残す。コメントで未充足条件と戻し先を明示する。

### RETRY の場合

本文更新は行わない（軽微修正後に再実行するため）。

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  docs-only workflow の最終チェックを完了し、PR に進める状態を確認した
evidence: |
  make verify-docs 通過、完了条件充足、Issue 本文更新済み
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | PR に進める（全完了条件が充足、本文更新済み） |
| RETRY | final-check 文脈で閉じる軽微修正後に再実行する |
| BACK | docs 更新フェーズに戻す（未充足条件と戻し先を明示） |
| ABORT | docs だけでは解決できない |
