---
id: local-pc5090-9
title: 'docs: gitlab-mode.md 新設 + gh 直接記述の forge-neutral 化'
state: open
slug: docs-gitlab-mode
labels:
- type:docs
- scope:gitlab-validation
created_at: '2026-05-09T06:02:20Z'
---
> [!NOTE]
> **Worktree**: `../kaji-docs-local-pc5090-9`
> **Branch**: `docs/local-pc5090-9`

## 概要

`docs/cli-guides/gitlab-mode.md` を新設して GitLab provider のセットアップ / 運用 / 前提を記述し、既存 docs / skill 内の `gh` 直接記述を forge-neutral に整理する。`kaji-pr-mr-bridge.md` の merge method 保証範囲 (ii) docs 前提も本ガイドに集約する。

> **本 Issue は docs-only**: 変更対象は `docs/` 配下および `.claude/skills/` 配下の文書のみ。`kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` 等の実装変更は **含めない**。実装変更が必要と判明した場合は別 Issue として切り出す。

## 目的

- GitLab project を kaji の provider として運用するための手順と前提を 1 ファイルで提供する
- skill 側に GitHub/GitLab 分岐を持ち込まない原則に沿い、残存する `gh` 直接記述を kaji コマンド or forge-neutral 表現に置換する
- `make test-large-gitlab` 実行前提（env / glab auth / 検証用 project）を子 Issue #6 と協調して docs に明記する

## ユーザーストーリー

- maintainer として、`docs/cli-guides/gitlab-mode.md` を読めば GitLab project の準備（SSH 鍵 / token / project 作成 / Settings → Merge requests の設定）と kaji の `provider.type='gitlab'` 起動手順がわかる状態にしたい
- maintainer として、kaji project の Merge method / Squash 設定が運用と矛盾しないことを docs から確認できる状態にしたい
- maintainer として、`make test-large-gitlab` を回す前提が docs から把握できる状態にしたい
- skill 開発者として、skill 内に GitHub 直接記述（`gh auth status` 等）が残らない状態にしたい

## スコープ

### IN

#### `docs/cli-guides/gitlab-mode.md` 新設

- 前提（`glab` install、SSH 鍵、Personal Access Token と scope）
- GitLab project 設定の必須前提（`kaji-pr-mr-bridge.md` の merge method 保証範囲 (ii) 準拠）:
  - **Merge method**: `Merge commit` に設定する
  - **Squash commits when merging**: `Do not allow` または `Allow`（`Require` は不可）
- `.kaji/config.toml` の `[provider]` `type = "gitlab"` / `[provider.gitlab]` 設定例
- 認証フロー（`glab auth login` または `GITLAB_TOKEN` env、OQ-1 の決定に従う）
- `kaji sync from-gitlab` の使い方
- `make test-large-gitlab` 実行前提（env / glab auth / 検証用 project）
- トラブルシューティング

#### 既存 docs の参照追加

- `docs/cli-guides/local-mode.md`: 既存 GitLab 言及を gitlab-mode.md へリンク
- `docs/operations/local-mode-runbook.md`: § 5「将来 forge への移行」に gitlab-mode.md への参照を追加

#### skill 内の `gh` 直接記述置換

- `.claude/skills/i-pr/SKILL.md:234` の `gh auth status` → forge-neutral 表現
- 他 skill 内に残る gh 直接記述（`gh-*` 等の固有名詞を除く）の grep 全数調査と置換
- skill 内の暫定運用記述（`pr-fix/SKILL.md` / `pr-verify/SKILL.md` の `kaji pr list --search` で取得）は子 Issue #3 完了に応じて整理

### OUT

- skill のプロンプト注入経路自体の変更（実装変更）→ `local-pc5090-7`
- E2E テスト本体 → `local-pc5090-10`
- `kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` 等の実装変更 → 必要時に別 Issue として切り出す（本 Issue では扱わない）

## 完了条件

- [ ] `docs/cli-guides/gitlab-mode.md` が存在し、上記 IN 項目をすべてカバーする
- [ ] `docs/cli-guides/local-mode.md` および `docs/operations/local-mode-runbook.md` から gitlab-mode.md へのリンクがある
- [ ] skill 内に `gh ` 直接呼び出しが残っていない（`grep -rn "gh " .claude/skills/` で 0 件、もしくは forge-neutral コメント等の説明的言及のみ）
- [ ] `make verify-docs` 緑（doc link checker）
- [ ] `make check` 緑
- [ ] **PR の diff が `docs/` および `.claude/skills/` 配下に限定されており、`kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` への変更を含まない**（docs-only 検証）

## 依存

- `local-pc5090-5`〜`local-pc5090-8` と並走可能
- merge method 保証範囲の docs 前提は `kaji-pr-mr-bridge.md` 確定後（既に確定）

## 参照

- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`
- 既存 setup ログ: `draft/lab/gitlab/setup-log.md`
- 既存 docs: `docs/cli-guides/local-mode.md` / `docs/operations/local-mode-runbook.md`
- skill: `.claude/skills/i-pr/SKILL.md:234`