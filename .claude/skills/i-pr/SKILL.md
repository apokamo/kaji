---
description: スコープを絞った PR 作成スキル。責務の明確化（何をしないかを明記）、PR テンプレートに Documentation セクション追加
name: i-pr
---

# I PR

スコープを絞った PR 作成スキル。コミット整理・プッシュ・PR 作成のみを行う。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/i-dev-final-check` または `/i-doc-final-check` が PASS した後 | ✅ 必須 |
| final-check 未完了 | ❌ 待機 |

**ワークフロー内の位置（dev）**: i-dev-final-check → **i-pr** → close
**ワークフロー内の位置（docs-only）**: i-doc-final-check → **i-pr** → close

## このスキルがやらないこと

- 品質チェック（`make check`）の実行 → `i-dev-final-check` / `i-doc-final-check` の責務
- 設計書アーカイブ → `i-dev-final-check` の責務
- エビデンス集約 → `i-dev-final-check` / `i-doc-final-check` の責務
- PR マージ・ブランチ削除 → `issue-close` の責務

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

- `/issue-start` が実行済みであること
- `git absorb` がインストール済みであること（任意）

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。

また、Issue 本文から `> **Branch**: \`[prefix]/[number]\`` を抽出して prefix を取得する。

### Step 2: 未コミットの変更確認

```bash
cd [worktree-absolute-path] && git status
```

未コミットの変更がある場合は先にコミットしてください。

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

- (ドキュメントの更新内容。なければ「変更なし」)

## Verification

- [ ] 必要な確認が完了している
EOF
)"
```

### Step 5: Issue本文にPR番号を追記

PR作成後、Issue本文のメタ情報にPR番号を追加:

```bash
CURRENT_BODY=$(gh issue view [issue-number] --json body -q '.body')
# **Branch** 行の後に **PR**: #[pr-number] を追加した本文を作成して更新
gh issue edit [issue-number] --body "..."
```

### Step 6: 完了報告

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

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  PR 作成成功
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
