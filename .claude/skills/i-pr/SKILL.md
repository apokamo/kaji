---
description: workflow 共通の PR 作成スキル。worktree 解決、未コミット確認、push、gh pr create のみを担当する。
name: i-pr
---

# I PR

workflow 共通の PR 作成スキル。
workflow 固有の完了判定は持たず、PR 作成そのものに責務を限定する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-dev-final-check` 完了後 | ✅ 必須 |
| `i-doc-final-check` 完了後 | ✅ 必須 |

## このスキルがやらないこと

- 品質チェック（`make check`）の実行 → `i-dev-final-check` / `i-doc-final-check` の責務
- 設計書アーカイブ → `i-dev-final-check` の責務
- エビデンス集約 → `i-dev-final-check` / `i-doc-final-check` の責務
- PR マージ・ブランチ削除 → `issue-close` の責務

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
3. `docs/guides/git-commit-flow.md`（kaji の Conventional Commits 運用、`--no-ff` merge 規約）

## 前提条件

- `/issue-start` が実行済みであること
- `git absorb` がインストール済みであること（任意）

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

また、Issue 本文から `> **Branch**: \`[prefix]/[number]\`` を抽出して prefix を取得する。

### Step 2: 未コミットの変更確認

```bash
cd [worktree-absolute-path] && git status
```

未コミットの変更がある場合は先にコミットしてください。
workflow 固有の docs 同梱判定は `i-dev-final-check` / `i-doc-final-check` 側の責務とする。

### Step 3: コミット履歴の整理

```bash
cd [worktree-absolute-path] && git absorb --and-rebase
```

fixup対象がない場合は何も起きません（正常）。
`git absorb` がインストールされていない場合はスキップ。

### Step 4: プッシュとPR作成

```bash
cd [worktree-absolute-path] && git push -u origin HEAD
```

```bash
cd [worktree-absolute-path] && gh pr create --base main --title "[prefix]: タイトル (#[issue-number])" --body "$(cat <<'EOF'
## Summary

(Issueの概要を1-2文で)

Closes #[issue-number]

## Changes

- (主な変更点)

## Documentation

- (ドキュメントの更新内容。設計書昇格 / 既存 docs 更新 / なし)

## Test Plan

- [x] 既存テストがパス
- [ ] 新規テストを追加（該当する場合）
- [ ] 手動検証: (必要な場合)
EOF
)"
```

> **重要**: PR body に `Closes #[issue-number]` を必ず含めること。これにより GitHub の Development sidebar に正式リンクが作成される。

> **マージ規約**: kaji の merge 規約は `--no-ff` only（squash merge 禁止）。マージ自体は `/issue-close` の責務だが、PR タイトルとコミットは Conventional Commits に従うこと（`docs/guides/git-commit-flow.md` 参照）。

### Step 5: Issue 本文に PR 番号を追記

PR 作成後、Issue 本文のメタ情報（NOTE ブロック）に PR 番号を追加:

```bash
CURRENT_BODY=$(gh issue view [issue-number] --json body -q '.body')
# **Branch** 行の後に **PR**: #[pr-number] を追加した本文を作成して更新
gh issue edit [issue-number] --body "..."
```

### Step 6: 完了報告

以下の形式で報告してください:

```
## PR作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| PR | #[pr-number] |
| URL | [pr-url] |
| コミット整理 | git absorb 実行済み / スキップ |

### 次のステップ

PRのマージ準備ができたら `/issue-close [issue-number]` を実行してください。
```

## 非責務

- dev / docs-only の個別ルール判定
- docs 昇格や docs 同梱の妥当性判定
- final-check 実行済みかどうかの代行判断
- マージ実行（`/issue-close` の責務）

## Verdict 出力

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
