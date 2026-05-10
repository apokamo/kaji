---
id: local-p1-19
title: 'skill: issue-close offline fallback (record / 14dc29e)'
state: closed
slug: skill-issue-close-offline-fallback
labels:
- type:fix
created_at: '2026-05-10T01:00:14Z'
closed_at: '2026-05-10T01:00:17Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **記録 issue**: skill 修正の audit trail 専用。即クローズする。

## 背景

local-p1-7 の `feature-development-local.yaml` 実行中、`issue-close` skill (provider=local) の Step 2 で `git fetch origin main` が失敗 (GitHub アカウント suspended → exit 128) し、workflow が exit 1 で abort した。

## 原因

Step 2 で `git fetch origin [default_branch]` 失敗時の fallback が skill に未定義。Claude は対話的に AskUserQuestion で local-only 継続 / abort を確認しようとしたが、`kaji run` 非対話モードでは AskUserQuestion が発火不可で破綻。

## 修正内容 (commit 14dc29e)

`.claude/skills/issue-close/SKILL.md` Step 2 (provider=local) を以下に変更:

\`\`\`bash
if git remote get-url origin >/dev/null 2>&1; then
    if git fetch origin [default_branch] 2>&1; then
        git merge --ff-only "origin/[default_branch]" || { echo "ABORT: ff-only merge failed in base worktree"; exit 1; }
    else
        echo "WARNING: git fetch origin [default_branch] failed; proceeding with local-only close (manual push needed after remote recovery)"
    fi
fi
\`\`\`

Step 6 (push) と同じく WARNING で skip し local-only close を deterministic に完結させる。

## 影響範囲

- provider=local の `kaji run` 経由 close が remote 到達不可下でも完走するようになる
- provider=github / gitlab 経路 (Step 4.5 の git fetch origin) は対象外。GitHub 復旧見込みなしの方針下では provider=github は当面使用しないため後回し
- ABORT 条件は ff-only merge 失敗 / dirty file 残存に限定（fetch 失敗は WARNING）

## 関連作業

- local-p1-7: 修正後に kaji run --step close で close 再実行
- local-p1-8 / 9 / 10: 当該 skill を経由するため修正後に再開
