---
id: local-pc5090-7
title: GitLabProvider.resolve_pr_context + prompt 注入経路
state: open
slug: gitlab-resolve-pr-context
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:02:09Z'
---
## 概要

branch 名から GitLab MR を逆引きし、`pr_id`（project-local `merge_request_iid`） / `pr_ref` を `prompt.py` に注入する経路を実装する。GitLab 固有の `resolved` 状態などは確定事項 #7 に従い provider 内部で保持する。

## 目的

- skill（`pr-fix` / `pr-verify` / `i-pr`）が forge 切替時にも同じ前提で動作するよう、PR/MR コンテキストの自動注入を GitLab でも実現する
- `local-pc5090-1` bucket の Phase 4 申し送り「`<Provider>.resolve_pr_context(branch_name)`」を GitLab 側で先取り実装する

## ユーザーストーリー

- kaji ユーザーとして、`/pr-fix` 等の skill が起動したとき、現在の branch から MR を自動検出して `pr_id` / `pr_ref` がプロンプトに自動注入されてほしい
- maintainer として、`pr-fix/SKILL.md` / `pr-verify/SKILL.md` の暫定運用記述（`kaji pr list --search` で取得）を本注入経路に切替できる状態にしたい

## スコープ

### IN

- `GitLabProvider.resolve_pr_context(branch_name) -> PRContext | IssueContext` 実装:
  - `glab mr list --source-branch <branch>` 等で MR を逆引き（`--repo` 明示指定）
  - `pr_id` = project-local `merge_request_iid`
  - `pr_ref` は `gl:<iid>` 形式で統一（`kaji-pr-mr-bridge.md` 準拠）
- MR の `resolved` 状態 / approval state 等の GitLab 固有情報は **確定事項 #7 に従い provider 内部で保持**、外向きには GitHub 互換 shape を返す
- `kaji_harness/prompt.py` の prompt 注入経路:
  - `provider.type='gitlab'` 配下でも `pr_id` / `pr_ref` が自動注入される統合
  - 既存 GitHub 経路と同じ public interface を維持（skill 修正不要）

### OUT

- skill 側の暫定運用記述削除 → 子 Issue #5 で扱う（docs / skill 整理）
- 実 GitLab 通信 E2E → 子 Issue #6

## 完了条件

- [ ] `GitLabProvider.resolve_pr_context(branch)` が MR を IID で返す
- [ ] `kaji_harness/prompt.py` が `provider.type='gitlab'` 配下で `pr_id` / `pr_ref` を自動注入する
- [ ] Medium テストで branch → MR 逆引きの round-trip が緑（mock 経由）
- [ ] skill のプロンプト注入経路に GitHub/GitLab 分岐が入っていない（diff 確認）
- [ ] `make check` 緑

## 依存

- 子 Issue #1（`GitLabProvider` 実装）— 完了必須

## 参照

- 確定事項 #7: 本 EPIC 本文
- 既存 GitHub 経路: `kaji_harness/providers/github.py:281` `resolve_issue_context` ほか
- prompt 注入: `kaji_harness/prompt.py`
- bucket 由来の Phase 4 申し送り: `local-pc5090-1`
- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`
