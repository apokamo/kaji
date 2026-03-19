---
description: コミット整理・プッシュ・PR作成を一括実行
name: issue-pr
---

# Issue PR

workflow の前段ステップ完了後、PRを作成します。
コミット履歴を整理してからPRを作成します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| workflow の pr ステップに到達した場合 | ✅ |
| 前段ステップが未完了 | ❌ 待機 |

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

実行完了後、以下の形式で verdict を出力すること:

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
