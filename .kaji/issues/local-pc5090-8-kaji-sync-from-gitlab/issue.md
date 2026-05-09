---
id: local-pc5090-8
title: kaji sync from-gitlab + sync status + cache 自動 populate
state: open
slug: kaji-sync-from-gitlab
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:02:13Z'
---
## 概要

`.kaji/cache/` を GitLab Issue から populate する `kaji sync from-gitlab` と、cache 状態を表示する `kaji sync status` を実装する。`kaji issue list` の local + cache 統合表示にも対応する。

## 目的

- `provider.type='local'` 配下から GitLab Issue を `gl:N` で参照できるよう cache を自動 populate する
- `local-pc5090-1` bucket の forge 連携項目（`kaji sync from-github` の GitLab 版対称実装）を先取りする

## ユーザーストーリー

- kaji ユーザーとして、`provider.type='local'` 配下で `kaji issue view gl:42` を打てば cache 経由で GitLab issue 42 が読める状態にしたい
- kaji ユーザーとして、cache の同期状態を `kaji sync status` で確認し、stale な entry が無いかわかる状態にしたい
- kaji ユーザーとして、`kaji issue list` が local issue + cached gitlab issue を統合表示してほしい

## スコープ

### IN

#### `kaji sync from-gitlab` CLI 実装

- **同期対象は GitLab project の open Issue 全件**（初期実装スコープ）
- `glab issue list --state opened --output json` 等で取得（`--repo` 明示指定）
- 取得した各 Issue を `.kaji/cache/gl-<iid>.json` に **atomic write**（`tmp` → `os.rename`）
- ローカル cache に存在するが GitLab 側で取得結果に含まれない Issue（= 既に closed 化された）は cache に残す（削除しない、参照履歴として保持）
- 最終 sync 時刻を `.kaji/cache/.sync-meta.json`（または同等の単一ファイル）に記録

#### `kaji sync status` CLI 実装

- cache 件数（`.kaji/cache/gl-*.json` の数）
- 最終 sync 時刻（UTC ISO-8601）
- 経過時間（秒 / 人間可読）
- 出力は table / JSON 切替（`--json` flag）

#### closed Issue / 詳細フィルタ（OUT — 将来拡張）

- `--include-closed` / `--state` / `--since` 等の追加 flag は **本 Issue では実装しない**。GitLab 採用が確定し運用上の必要が出た段階で別 Issue として起票
- 初期実装は open 全件取得のシンプルな mental model に留める

#### cache 統合表示

- `.kaji/cache/` の自動初期化（`kaji local init` または初回 sync 時）
- `LocalProvider.list_issues()` 拡張: cache 配下の `gl-*.json` も統合表示
- 表示形式: `gl:42  open  ...` のように `gl:` prefix で local issue と区別

### OUT

- `kaji sync local-to-gitlab-plan` → bucket (`local-pc5090-1`) 残務として残す
- Issue 一括転記支援 → bucket 残務として残す
- `kaji sync from-github`（GitHub 復帰判断後実装）→ `local-pc5090-12` として deferred
- `--include-closed` / `--state` / `--since` 等の追加 flag → 採用確定後の運用要件に応じて別 Issue で対応

## 完了条件

- [ ] `kaji sync from-gitlab` 実行で **GitLab project の open Issue 全件** が `.kaji/cache/gl-<iid>.json` に atomic write される
- [ ] `kaji sync from-gitlab` 完了時、最終 sync 時刻が `.kaji/cache/.sync-meta.json`（または同等）に UTC ISO-8601 で記録される
- [ ] cache に存在するが GitLab 側で open でなくなった Issue は cache に残る（削除されない）
- [ ] `kaji sync status` が以下を表示する: cache 件数 / 最終 sync 時刻 (UTC) / 経過時間 / `--json` 切替
- [ ] `kaji issue list` が local + cache (`gl:*`) を統合表示し、`gl:` prefix で区別される
- [ ] `--include-closed` / `--state` / `--since` 等の追加 flag は **未実装の状態で fail-fast** する（silent ignore しない）
- [ ] Medium テストで以下が緑: open 全件取得 → cache 書き込み round-trip / closed 化された entry の保持 / `sync status` の出力検証 / `kaji issue list` 統合表示
- [ ] `make check` 緑

## 依存

- `local-pc5090-5`（`GitLabProvider` 実装）— 完了必須

## 参照

- 既存 cached read 経路: `kaji_harness/providers/local.py:493`、`kaji_harness/providers/local.py:753` (`view_cached_issue`)
- 既存 `LocalProvider.list_issues()`: `kaji_harness/providers/local.py`
- design.md § 残課題: `draft/design/local-mode/design.md`
- bucket Issue: `local-pc5090-1`
