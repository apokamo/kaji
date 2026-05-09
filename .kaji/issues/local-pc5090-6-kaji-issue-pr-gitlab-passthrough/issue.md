---
id: local-pc5090-6
title: kaji issue / kaji pr passthrough gitlab 対応 + ID 規約 (gl:N) 拡張
state: open
slug: kaji-issue-pr-gitlab-passthrough
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:02:00Z'
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-pc5090-6`
> **Branch**: `feat/local-pc5090-6`

## 概要

`kaji issue` / `kaji pr` の CLI dispatch を GitLab provider に対応させ、`gl:N` ID 規約を `normalize_id` に追加する。OQ-2 決定文書 `kaji-pr-mr-bridge.md` の互換 contract を実装する。

## 目的

- skill 側に GitHub/GitLab 分岐を持ち込まずに `kaji issue` / `kaji pr` を GitLab で動作させる
- `gl:N` 形式の ID を kaji の `normalize_id` 規約に統合し、`provider=gitlab` 配下の操作と `provider=local` 配下の cache 参照両方に対応する

## ユーザーストーリー

- kaji ユーザーとして、`provider.type='gitlab'` 配下で `kaji issue {create / view / edit / list / close / comment}` がそのまま動作してほしい
- kaji ユーザーとして、`kaji pr {create / view / list / merge / comment / review (--approve / --request-changes) / review-comments / reviews / reply-to-comment}` が GitHub 互換 shape で動作してほしい
- kaji ユーザーとして、`kaji issue view gl:42` が GitLab issue の IID 42 を表示してくれる状態にしたい

## スコープ

### IN

#### `normalize_id` 拡張（`kaji_harness/providers/_mappings.py`）

- `gl:N` パターン追加（`N` は project-local IID = `issue_iid` / `merge_request_iid`）
- `provider.type='gitlab'` 配下: `gl:N` → kind=`gitlab`、`N` → kind=`gitlab` (numeric)
- `provider.type='local'` 配下: `gl:N` → kind=`remote_cache`（読み取り用）
- 表示用 `issue_ref` を `gl:<iid>` 形式で統一
- `ResolvedKind` Literal に `"gitlab"` 追加

#### `kaji issue` dispatcher（`kaji_harness/cli_main.py`）

- `provider.type='gitlab'` 分岐 — `glab issue ...` への subprocess 転送（`--repo` 明示指定）

#### `kaji pr` Tier B 実装（`kaji-pr-mr-bridge.md` 準拠）

- `create` / `merge` / `view` / `list` / `comment` / `review`
- `review --approve --body[-file]` → `glab mr note --message <body>` + `glab mr approve` のシーケンス
- `review --request-changes --body[-file]` → `glab mr note --message <body>` + `glab mr revoke` のシーケンス（未 approve 状態では revoke を no-op として skip し note のみ実施）
- body 取り扱い原則: body は捨てない、順序は「note 投稿 → approve / revoke」、kaji が成功 / 失敗を responsible に判定
- `merge` で `--squash` / `--rebase` を kaji 側で拒否（`EXIT_INVALID_INPUT`）
- `comment` の name → `glab mr note` 変換
- `--json fields` / `--jq expr` 引数体系を GitHub 命名で揃え（GitLab field を internal で変換）

#### `kaji pr` Tier A 実装（確定事項 #7 / `kaji-pr-mr-bridge.md` 準拠）

- `review-comments` / `reviews` / `reply-to-comment` を `glab` discussion API ベースで実装
- 出力は GitHub 互換 subset を正本
- `reply-to-comment` の comment_id は **GitLab 側で復元可能な provider-local ID 形式**（GitHub comment id をそのまま使うのではない）
- GitLab 固有情報（`discussion_id` / `note_id` / `resolved` / `position`）は provider 内部で保持

#### 未対応 sub のエラー化

- `glab mr` 固有 sub（`approvers` / `for` / `subscribe` 等）は `EXIT_INVALID_INPUT` で **silent passthrough せず明示エラー**

### OUT

- `GitLabProvider` 本体実装 → 子 Issue #1（依存）
- `resolve_pr_context` → 子 Issue #3
- 実 GitLab 通信 E2E → 子 Issue #6

## 完了条件

- [x] `normalize_id` の Small テストで `gl:N` / 数値 / local-form の各組み合わせが緑
- [x] `kaji issue {create / view / edit / list / close / comment}` が GitLab project に対して動作（mock テスト緑）
- [x] `kaji pr {create / view / list / merge / comment / review / review-comments / reviews / reply-to-comment}` が GitLab project に対して GitHub 互換 shape を返す（mock テスト緑）
- [x] `kaji pr merge --squash` / `--rebase` が拒否される（rc=2）
- [x] `kaji pr review --approve --body-file` が note 投稿 → approve のシーケンスで動作（mock 検証）
- [x] `kaji pr review --request-changes` の未 approve 時 no-op 挙動が確認できる（mock 検証）
- [x] `kaji pr <未対応 sub>` が `EXIT_INVALID_INPUT` で明示エラー
- [x] skill 側に GitHub/GitLab 分岐が入っていない（diff 確認）
- [x] `make check` 緑

## 依存

- 子 Issue #1（`GitLabProvider` 実装）— 完了必須

## 参照

- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`
- 確定事項 #7: 本 EPIC 本文
- 既存実装: `kaji_harness/cli_main.py:436-723`、`kaji_harness/providers/_mappings.py`
- skill 一覧: `.claude/skills/{i-pr,issue-close,pr-fix,pr-verify}/SKILL.md`
