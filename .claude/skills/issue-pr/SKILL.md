---
description: コミット整理・プッシュ・PR作成を一括実行
name: issue-pr
---

# Issue PR

コードレビュー・ドキュメントチェック完了後、PRを作成します。
コミット履歴を整理してからPRを作成します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-doc-check` 完了後 | ✅ 必須 |
| ドキュメントチェック未完了 | ❌ 待機 |

**ワークフロー内の位置**: implement → review-code → doc-check → **pr** → close

## 引数

```
$ARGUMENTS = <issue-number>
```

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/development_workflow.md`

## 前提条件

- `/issue-start` が実行済みであること
- 実装とコードレビューが完了していること
- `/issue-doc-check` が実行済みであること
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

## Test Plan

- [x] 既存テストがパス
- [ ] 新規テストを追加（該当する場合）
- [ ] 手動検証: (必要な場合)
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
