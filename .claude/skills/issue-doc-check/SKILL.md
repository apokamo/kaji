---
description: 互換用エイリアス。新しい正本は i-dev-final-check
name: issue-doc-check
---

# Issue Doc Check

`issue-doc-check` は互換用の旧名称です。新しい正本は `i-dev-final-check` とし、dev workflow の最終ゲート全体を担当します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 互換利用可 |
| PR作成前の最終確認として | ✅ 互換利用可 |

**ワークフロー内の位置**: implement → review-code → **i-dev-final-check** → i-pr → close

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

## 前提知識の読み込み

1. [docs/dev/development_workflow.md](../../../docs/dev/development_workflow.md)
2. [docs/dev/workflow_completion_criteria.md](../../../docs/dev/workflow_completion_criteria.md)
3. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
4. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)
5. `.claude/skills/_shared/promote-design.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

詳細手順は [../i-dev-final-check/SKILL.md](../i-dev-final-check/SKILL.md) を参照。

### Step 2: 完了報告

以下の形式で報告してください:

```
## ドキュメントチェック完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 設計書昇格 | 昇格済み (`docs/...` 配下) / 既存メンテ / なし |
| ドキュメント更新 | あり / なし |
| 対象 | (更新したドキュメント / -) |

### 次のステップ

`/i-pr [issue-number]` でPRを作成してください。
```

## Verdict 出力

正本 `i-dev-final-check` と同一の verdict セマンティクスを返す。

```text
---VERDICT---
status: PASS
reason: |
  dev workflow の最終チェックを完了し、PR に進める状態を確認した
evidence: |
  前段証跡を集約し、全完了条件の充足を確認した。Issue 本文のチェックボックスを更新済み
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全完了条件が充足し、Issue 本文更新済み |
| RETRY | final-check 文脈で閉じる軽微修正が必要 |
| BACK | 前段ステップへ戻す必要がある |
| ABORT | 重大な前提不整合 |
