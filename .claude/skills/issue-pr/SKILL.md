---
description: コミット整理・プッシュ・PR作成を一括実行（i-pr への委譲ラッパー）
name: issue-pr
---

# Issue PR

> **注意**: このスキルは `/i-pr` への委譲ラッパーです。実際の処理は `/i-pr` が行います。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| workflow の pr ステップに到達した場合 | ✅ |
| 前段ステップが未完了 | ❌ 待機 |

**ワークフロー内の位置**: i-dev-final-check → **i-pr (= issue-pr)** → close

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

## 実行手順

`/i-pr [issue-number]` を実行してください。このスキルの全手順は `/i-pr` に記載されています。

## Verdict 出力

このスキルは `/i-pr` に委譲するため、verdict は `/i-pr` の実行結果をそのまま使用する。

---VERDICT---
status: PASS
reason: |
  PR 作成成功（i-pr への委譲）
evidence: |
  PR #XX を作成
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | PR 作成成功 |
| RETRY | push 失敗等 |
| ABORT | 重大な問題 |
