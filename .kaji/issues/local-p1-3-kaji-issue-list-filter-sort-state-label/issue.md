---
id: local-p1-3
title: kaji issue list の filter / sort 強化（state / label / assignee / sort）
state: open
slug: kaji-issue-list-filter-sort-state-label
labels:
- type:feature
- scope:local-mode
- priority:low
created_at: '2026-05-08T17:27:36Z'
---
## 概要

`kaji issue list` の filter / sort オプションを強化する。検証期間中に local-mode で
SoT として運用するため、Issue 件数が増えた時に list の絞り込みが必要になる想定。

## 背景

現行の `kaji issue list` は `--state` のみサポート。`--label` / `--assignee` /
`--sort` / `--limit` 等は未実装。`draft/design/local-mode/design.md` § 残課題
（local-mode 機能拡張）に記載されている。

## 設計

### CLI 仕様（案）

```
kaji issue list [--state open|closed|all]
                [--label LABEL]...           # 複数指定で AND
                [--assignee LOGIN]
                [--sort created_at|updated_at|id]
                [--reverse]                  # sort の逆順
                [--limit N]
                [--json FIELDS] [--jq EXPR]
```

### filter ロジック

- `--state`: 既存実装維持（`open` / `closed` / `all`）
- `--label`: frontmatter `labels` 配列に全て含まれるものだけ表示（AND マッチ）
- `--assignee`: frontmatter `assignees` 配列に含まれるものだけ表示
- `--sort`: 指定 field で昇順ソート（default: `id` 昇順）
- `--reverse`: ソート順を逆転
- `--limit`: 先頭 N 件のみ表示

### 実装方針

- `LocalProvider.list_issues()` に filter / sort パラメータを追加
- `kaji_harness/cli_main.py:cmd_issue_list` で argparse 拡張
- 既存の `--json` / `--jq` 経路と整合（filter 後の結果に jq を適用）

## 受け入れ条件

- [ ] `--label` で AND マッチが動作（複数 `--label` を指定したら全て含むもののみ）
- [ ] `--assignee` で frontmatter `assignees` のフィルタが動作
- [ ] `--sort` の各 field（`created_at` / `updated_at` / `id`）で正しくソートされる
- [ ] `--reverse` でソート順が反転する
- [ ] `--limit N` で先頭 N 件に絞られる
- [ ] 既存の `--state` / `--json` / `--jq` との組合せで破壊的変更なく動作する
- [ ] Small テスト（各 filter / sort のユニットテスト）が緑
- [ ] Medium テスト（実 file I/O での filter / sort）が緑
- [ ] `docs/cli-guides/local-mode.md` に拡張仕様が追記される

## 着手 trigger

- 検証期間中の運用で local Issue 件数が増えた結果、絞り込みが必要になった時点
- 具体的には Issue 件数が概ね 20-30 件を超えたあたりが目安（ただし数値固定の KPI ではない）

## 不要になる条件

- 検証期間中に list の絞り込みが必要にならなかった場合
- forge 採用先確定後、local Issue を採用 forge へ移行することで local list の運用が
  終了した場合（`local-p1-1` 集約 Issue 内で扱う）
  → いずれかで `closed --reason not-planned`

## 参照

- 設計書 §残課題（local-mode 機能拡張）: `draft/design/local-mode/design.md`
- Phase 5 設計書: `draft/design/local-mode/phase5-design.md`
