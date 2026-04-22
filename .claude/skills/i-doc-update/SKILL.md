---
description: docs-only の更新を行う。コードやテストは変更せず、現行実装・CLI・運用方針との整合を確認しながら docs を修正する。
name: i-doc-update
---

# I Doc Update

ドキュメント修正専用のスキル。
このスキルの目的は **ドキュメントのみを更新すること** である。コード、設定、テストは変更しない。
ただし、docs の記述が現行実装や運用方針と矛盾していないかは厳格に確認する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| docs-only Issue の主作業 | ✅ 必須 |
| コード変更を伴う Issue | ❌ `issue-implement` / `issue-doc-check` を使用 |

**ワークフロー内の位置**: start → **update-doc** → review-doc → (fix-doc → verify-doc) → pr → close

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

## 確認対象

1. `docs/dev/development_workflow.md`
2. `README.md`
3. 変更対象 docs
4. 関連する実装、workflow、設計書、運用ドキュメント

## ドキュメント品質の原則

- 段階的開示の方針を取る。ドキュメントは小さく保つ。大きくなるなら構造化して分割する
- 追加より削除が難しい。追加時に本当に必要か判断する。不要な情報は削除、スリム化を検討する
- コードから推論できる情報は書かない。具体的なコードもドキュメントに書かない。必要な場合は実コードへのポインターを記載する

## ガードレール

- コード、設定、テストは変更しない
- 事実確認のための read / search / 最小限のコマンド確認は許可
- `python3 scripts/check_doc_links.py` による全体確認は許可
- docs だけでは解決できない不整合を見つけた場合は `ABORT`

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 2: 設計書と Issue の確認

1. Issue 本文とコメントを確認
2. 設計書があれば確認:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```
3. 変更対象 docs と expected outcome を整理

### Step 3: 整合性監査

最低限、以下を確認する。

- `docs/` の記述が現行コードと矛盾していないか
- `CLAUDE.md` のコマンド、禁止事項、運用ルールと矛盾しないか
- `docs/dev/development_workflow.md` と workflow/skill 構成が一致しているか
- links、参照パス、コマンド例が壊れていないか

### Step 4: docs 更新

必要なドキュメントだけを更新する。

### Step 5: 全体リンクチェック

初回に以下を実行し、既存 docs 全体の状態を確認する。

```bash
cd [worktree-absolute-path] && python3 scripts/check_doc_links.py
```

- 今回の変更と無関係な既存エラーは、この Issue で無理に解消しない
- 無関係な既存エラーは別 Issue を作成して追跡する

### Step 6: コミット

```bash
cd [worktree-absolute-path] && git add docs/ README.md workflows/ .claude/skills/ .agents/skills/ && git commit -m "docs: update documentation for #[issue-number]"
```

必要に応じて変更対象パスを絞ってよい。

### Step 7: Issue コメント

何を更新し、どの観点を確認したかを Issue にコメントする。

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  docs-only の更新を完了した
evidence: |
  対象ドキュメントを更新し、現行実装・CLI・運用方針との整合を確認した
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | docs 更新完了 |
| ABORT | docs だけでは安全に対処できない |
