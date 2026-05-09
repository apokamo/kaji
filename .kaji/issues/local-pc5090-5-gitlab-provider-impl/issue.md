---
id: local-pc5090-5
title: GitLabProvider 実装 + config + dispatcher 拡張
state: open
slug: gitlab-provider-impl
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:01:49Z'
---
## 概要

`provider.type='gitlab'` を kaji の正式な provider として認識させるための基盤実装。`IssueProvider` Protocol の 8 メソッドを `glab` CLI subprocess で実装し、`ProviderConfig` / `Workflow` schema enum を拡張、`get_provider()` に GitLab 分岐を追加する。

## 目的

- GitLab を採用可能な状態を早期整備する（EPIC `local-pc5090-4` の主目的）ための基盤。本 Issue 完了で `provider.type='gitlab'` の dispatch が成立する
- GitHub mode と対称な provider 構造を維持し、後続子 Issue（#2 / #3 / #4）の実装前提を整える

## ユーザーストーリー

- maintainer として、`.kaji/config.toml` に `[provider]` `type = "gitlab"` を書けば既存の `kaji issue list` 等が GitLab project で動く状態にしたい
- maintainer として、`requires_provider: gitlab` の workflow YAML が `kaji validate` で正しく検査される状態にしたい
- maintainer として、`glab` の context 状態（current project / login）に依存せず、`provider.gitlab.repo` で対象を明示できる状態にしたい

## スコープ

### IN

- `kaji_harness/providers/gitlab.py` に `GitLabProvider` クラス実装
  - `IssueProvider` Protocol 8 メソッド: `create_issue` / `view_issue` / `edit_issue` / `comment_issue` / `close_issue` / `list_issues` / `list_labels` / `resolve_issue_context`
  - `glab` CLI subprocess 方式（`GitHubProvider` の `gh` CLI と対称構造、確定事項 #1）
  - `glab` 呼び出し時に `--repo <provider.gitlab.repo>` を必ず明示指定（context 暗黙依存禁止）
- `kaji_harness/config.py`:
  - `ProviderConfig.type` Literal に `"gitlab"` 追加
  - `GitLabProviderConfig` dataclass 新設（`repo: str` 必須 / `default_branch: str` 必須 / `hostname` フィールドは持たない、`gitlab.com` を内部固定 — 確定事項 #3 の論理的帰結）
  - `KajiConfig` への `gitlab` フィールド追加と overlay merge 対応
- `kaji_harness/providers/__init__.py`:
  - `get_provider()` の `provider.type == "gitlab"` 分岐
  - `actual_provider_type()` 戻り値型拡張
- `kaji_harness/workflow.py` / `kaji_harness/models.py`:
  - `Workflow.requires_provider` enum への `"gitlab"` 追加（`Literal["github", "local", "gitlab", "any"]`）
- builtin workflow（`feature-development.yaml` 等）の `requires_provider` ポリシー決定（`any` 化または `github` / `gitlab` allow list）
- `kaji validate` の workflow provider match テスト更新

### OUT

- `kaji issue` / `kaji pr` の CLI passthrough 拡張 → 子 Issue #2
- `gl:N` ID 規約と `normalize_id` 拡張 → 子 Issue #2
- `resolve_pr_context` → 子 Issue #3
- `kaji sync from-gitlab` → 子 Issue #4
- 実 GitLab 通信 E2E → 子 Issue #6

## 完了条件

- [ ] `GitLabProvider` クラスが `IssueProvider` Protocol を mypy で満たす
- [ ] `provider.type='gitlab'` の config が `KajiConfig` でロードできる
- [ ] `get_provider(config)` が `provider.type='gitlab'` に対し `GitLabProvider` を返す
- [ ] `requires_provider: gitlab` の workflow YAML が `kaji validate` で PASS する
- [ ] builtin workflow の `requires_provider` ポリシーが design 通りに反映されている
- [ ] Small / Medium テストで CRUD round-trip が緑（mock 経由、実通信は #6）
- [ ] `make check` 緑

## 依存

なし（本 EPIC の起点 Issue）

## 参照

- EPIC: `local-pc5090-4`
- 確定事項 #1〜#7: 本 EPIC 本文
- 既存 GitHubProvider: `kaji_harness/providers/github.py`
- IssueProvider Protocol: `kaji_harness/providers/base.py:16-83`
- 既存 ProviderConfig: `kaji_harness/config.py:40-57`
- 既存 get_provider dispatcher: `kaji_harness/providers/__init__.py:70-102`
- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`（本 Issue では参照のみ、実装は #2）
