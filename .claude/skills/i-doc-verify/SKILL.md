---
description: docs review の指摘が適切に修正されたかを確認する。新規指摘は行わない。
name: i-doc-verify
---

# I Doc Verify

> **重要**: このスキルは更新/修正を行ったセッションとは別のセッションで実行することを推奨します。

docs 修正後の確認を行う。
**新規指摘は行わず、前回レビューの指摘が解消したかのみ確認する。**

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-doc-fix` 後の確認 | ✅ 必須 |

**ワークフロー内の位置**: review-doc → fix-doc → **verify-doc** → i-doc-final-check → i-pr

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |
| `cycle_count` | int | 現在のイテレーション |
| `max_iterations` | int | 上限回数 |
| `previous_verdict` | str | 前ステップの verdict |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

1. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
2. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)

## 実行手順

1. 前回の `i-doc-review` / `i-doc-fix` コメントを確認
2. 指摘事項ごとに OK / NG を判定
3. 必要最小限で根拠となる実装 / docs / workflow / CLAUDE.md を再確認
4. 変更ファイルに絞ったリンクチェック結果を確認:
   ```bash
   cd [worktree-absolute-path] && python3 scripts/check_doc_links.py [changed-markdown-files...]
   ```
5. 新規発見事項があっても今回の判定には含めない
6. 結果を Issue にコメント

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  前回の docs review 指摘は適切に修正されている
evidence: |
  Must Fix 項目の解消を確認した。新規指摘は判定に含めていない
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 前回指摘が解消 |
| RETRY | 修正不足 |
| ABORT | 継続が危険 |
