---
id: local-pc5090-2
title: kaji issue edit --add-frontmatter 実装（reserved key 保護 + 文法検証）
state: open
slug: kaji-issue-edit-add-frontmatter-reserved
labels:
- type:feature
- scope:local-mode
- priority:low
created_at: '2026-05-08T17:27:17Z'
---
## 概要

`kaji issue edit --add-frontmatter KEY=VALUE` を実装する。Issue frontmatter に
ユーザー拡張 key を追記する CLI オプション。当初用途は `migrated_to=<gh-number>`
（forge 移行時の手動転記補助）だったが、forge 移行が後送りになったため優先度は
低下。検証期間中の user 拡張 metadata 機構として将来必要になった時点で着手。

## 背景

`draft/design/local-mode/design.md` § インターフェース に CLI 仕様が記載されている。
本 Issue 着手時に詳細仕様を再確認すること。

## 設計

### CLI 仕様

```
kaji issue edit ID --add-frontmatter KEY=VALUE [--add-frontmatter KEY2=VALUE2]...
```

### reserved key 保護

frontmatter の core field は `--add-frontmatter` で**上書き不可**。
違反入力はエラー停止し、専用 CLI（`kaji issue edit --body`、`--add-label`、
`kaji issue close` 等）の使用をガイドする。

| 分類 | key | 上書き手段 |
|------|-----|----------|
| reserved（禁止） | `id`, `state`, `labels`, `assignees`, `created_at`, `updated_at`, `closed_at`, `close_reason`, `closed_by`, `created_by`, `title` | 専用 CLI のみ |
| user 拡張（許可） | 上記以外の任意 key | `--add-frontmatter` で許可 |

### 実装方針

- `RESERVED_FRONTMATTER_KEYS: frozenset[str]` を `kaji_harness/providers/models.py` で定義
- KEY 文法: `[a-z][a-z0-9_]{0,31}`（snake_case、32 文字以内）
- 値は string 固定（YAML scalar として安全。複合型は schema 肥大化を避けるため不採用）
- 違反時のエラー文言は reserved 各 key に専用ガイドを示す
  例: 「`--add-frontmatter` cannot overwrite reserved key '`state`'. Use `kaji issue close` instead.」

## 受け入れ条件

- [ ] `kaji issue edit ID --add-frontmatter KEY=VALUE` が user 拡張 key に対して動作する
- [ ] reserved key を指定すると exit 2 + 専用 CLI のガイドで停止する
- [ ] KEY 文法（`[a-z][a-z0-9_]{0,31}`）違反は exit 2 で停止する
- [ ] 値は string として frontmatter に書き込まれる（YAML scalar）
- [ ] Small テスト（reserved 各 key の reject / 文法違反の reject / 正常系の round-trip）が緑
- [ ] Medium テスト（実 file I/O での frontmatter 更新と round-trip 読み取り）が緑
- [ ] `docs/cli-guides/local-mode.md` に CLI 仕様が追記される

## 着手 trigger

- 検証期間中に user 拡張 metadata の必要性が出た場合
- forge 採用先確定時に `migrated_to` 用途で必要になった場合（`local-pc5090-1` 集約 Issue で再確認）

## 不要になる条件

- 検証期間が終了するまで user 拡張 metadata の必要性が発生しなかった、かつ
  forge 採用先確定後の転記支援設計で `--add-frontmatter` が不要と判断された場合
  → `closed --reason not-planned` でクローズ

## 参照

- 設計書: `draft/design/local-mode/design.md` § インターフェース / § 残課題
- Phase 5 設計書: `draft/design/local-mode/phase5-design.md` § 残課題
