---
id: local-pc5090-10
title: make test-large-gitlab + provider=gitlab E2E
state: open
slug: test-large-gitlab-e2e
labels:
- type:test
- scope:gitlab-validation
created_at: '2026-05-09T06:02:27Z'
---
> [!NOTE]
> **Worktree**: `../kaji-test-local-pc5090-10`
> **Branch**: `test/local-pc5090-10`

## 概要

GitLab provider の E2E テスト（`make test-large-gitlab`）を新設し、`provider=gitlab` で 1 本の workflow が完走することを検証する。`make check` のデフォルト実行から分離し、env / glab auth / 検証用 project 前提を Makefile および docs に明記する。

## 目的

- `provider.type='gitlab'` の本番運用可能性を E2E で実証する（EPIC `local-pc5090-4` の主要完了条件）
- 実通信テストを通常 `pytest` から分離し、`make check` の安定性を維持する

## ユーザーストーリー

- maintainer として、`make test-large-gitlab` を打てば provider=gitlab の workflow round-trip が検証できる状態にしたい
- maintainer として、`make check` が実通信に依存せず安定して通り続けてほしい
- maintainer として、E2E テスト失敗時に env / project 設定の問題か kaji 実装の問題かを切り分けられる状態にしたい

## スコープ

### IN

#### Makefile 拡張

- `make test-large-gitlab` ターゲット新設（`pytest -m large_gitlab` 等）
- `make check` のデフォルト実行から `large_gitlab` マーカーを除外
- 必要 env / `glab auth` / 検証用 project の前提を `make help` 出力に明記

#### `tests/test_large_gitlab/` 新設

- workflow E2E（`feature-development.yaml` 等を `provider.type='gitlab'` で 1 本完走）
- `kaji issue` round-trip（create → view → edit → comment → close）
- `kaji pr` round-trip（create → view → list → review --approve --body / review --request-changes --body / merge）
- **`kaji pr merge --squash` / `--rebase` の拒否確認**（`kaji-pr-mr-bridge.md` 準拠）
- **`kaji pr review --approve --body-file` の note 投稿 → approve シーケンス検証**（`kaji-pr-mr-bridge.md` body 取り扱い原則）
- **`kaji pr review --request-changes` の未 approve 時 no-op 挙動検証**
- **未対応 sub（`kaji pr approvers` 等）の明示エラー検証**（silent passthrough 禁止）
- `review-comments` / `reviews` / `reply-to-comment` の GitHub 互換 shape 検証（確定事項 #7）
- `reply-to-comment` の provider-local ID 形式が GitLab 側で復元可能であることの検証
- `kaji sync from-gitlab` 実通信

#### Skip 条件

- `GITLAB_TOKEN` 未設定、`glab auth status` 失敗時は test を skip

### OUT

- 検証用 GitLab project 自体の準備手順 → 子 Issue #5（docs）

## 完了条件

- [ ] `make test-large-gitlab` ターゲットが Makefile に存在する
- [ ] `make check` から `large_gitlab` テストが除外される（マーカー条件付き）
- [ ] `provider.type='gitlab'` で `feature-development.yaml` 等の workflow が 1 本完走する
- [ ] `kaji issue` / `kaji pr` の全 sub round-trip テストが緑
- [ ] `kaji-pr-mr-bridge.md` の review note シーケンス / merge flag 拒否 / 未対応 sub エラー / `reply-to-comment` 復元が E2E で検証されている
- [ ] env / 前提が docs に明記されている（`gitlab-mode.md`、子 Issue #5 と協調）

## 依存

- 子 Issue #1〜#4（`GitLabProvider` / passthrough / resolve_pr_context / sync）— 完了必須
- 子 Issue #5（docs）と並走可能（前提記載のみ協調）

## 参照

- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`
- 確定事項 #7: 本 EPIC 本文
- testing-size-guide: `docs/reference/testing-size-guide.md`
- testing-convention: `docs/dev/testing-convention.md`