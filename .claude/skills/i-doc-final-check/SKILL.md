---
description: docs-only ワークフローの PR 前最終チェック。リンク整合性・完了条件の検証
name: i-doc-final-check
---

# I Doc Final Check

docs-only ワークフローの PR 作成前最終チェック。リンク整合性と完了条件を検証する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| docs レビュー承認後、PR 作成前 | ✅ 必須 |
| docs レビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: i-doc-update → i-doc-review → (i-doc-fix → i-doc-verify) → **i-doc-final-check** → i-pr

## 入力

### ハーネス経由（コンテキスト変数）

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

- docs レビューが完了・承認されていること
- worktree が存在すること

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。

### Step 2: エビデンス確認

Issue コメントから以下を確認する:

| フェーズ | 確認事項 |
|----------|----------|
| docs 更新 | `i-doc-update` 完了コメントが存在すること |
| docs レビュー | `i-doc-review` で PASS 判定が出ていること |

いずれかが欠けている場合は BACK verdict を出す。

### Step 3: リンク整合性チェック

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make verify-docs
```

リンク切れがある場合は RETRY verdict を出す。

### Step 4: 完了条件の確認

Issue 本文の完了条件（チェックリスト）を確認し、docs 関連の条件がすべて満たされているか検証する。

### Step 5: 結果を Issue にコメント

```bash
gh issue comment [issue-number] --body "$(cat <<'COMMENT_EOF'
## Doc Final Check 結果

### エビデンス確認

| フェーズ | 状態 |
|----------|------|
| docs 更新 | ✅ / ❌ |
| docs レビュー | ✅ / ❌ |

### リンクチェック結果

```
(make verify-docs の出力)
```

### 完了条件

- [x] / [ ] (各条件の状態)

### 判定

PASS / RETRY / BACK
COMMENT_EOF
)"
```

### Step 6: 完了報告

```
## Doc Final Check 完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| エビデンス | 全フェーズ確認済み |
| リンクチェック | パス |

### 次のステップ

`/i-pr [issue-number]` で PR を作成してください。
```

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  エビデンス確認・リンクチェック全パス
evidence: |
  make verify-docs パス、完了条件すべて達成
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | エビデンス確認・リンクチェック全パス |
| RETRY | リンク切れ等（修正可能） |
| BACK | エビデンス不足（前フェーズに差し戻し） |
| ABORT | 重大な問題 |
