---
description: 互換用エイリアス。新しい正本は i-pr
name: issue-pr
---

# Issue PR

`issue-pr` は互換用の旧名称です。新しい正本は `i-pr` とし、workflow 共通の PR 作成責務のみを扱います。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-dev-final-check` 完了後 | ✅ 互換利用可 |
| `i-doc-final-check` 完了後 | ✅ 互換利用可 |
| 必要なレビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**:
- 通常フロー: implement → review-code → i-dev-final-check → **i-pr** → close
- docs-only フロー: update-doc → review-doc → (fix-doc → verify-doc) → i-doc-final-check → **i-pr** → close

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

1. [docs/dev/workflow_overview.md](../../../docs/dev/workflow_overview.md)
2. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)

## 前提条件

- `/issue-start` が実行済みであること
- 通常フローでは実装とコードレビューが完了していること
- 通常フローでは `i-dev-final-check` が完了していること
- docs-only フローでは `i-doc-final-check` が完了していること
- `git absorb` がインストール済みであること（任意）

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

正本 `i-pr/SKILL.md` の実行手順に従うこと。

詳細は [../i-pr/SKILL.md](../i-pr/SKILL.md) を参照。

## Verdict 出力

正本 `i-pr` と同一の verdict セマンティクスを返す。

```text
---VERDICT---
status: PASS
reason: |
  PR 作成を完了した
evidence: |
  push と gh pr create が成功した
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | PR 作成成功 |
| RETRY | 再試行で解決可能な失敗 |
| ABORT | 継続不能な失敗 |
