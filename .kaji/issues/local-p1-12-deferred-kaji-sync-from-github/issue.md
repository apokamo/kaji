---
id: local-p1-12
title: '[deferred] kaji sync from-github 実装（GitHubProvider 対称化）'
state: open
slug: deferred-kaji-sync-from-github
labels:
- type:feature
- scope:gitlab-validation
- priority:low
created_at: '2026-05-09T06:02:43Z'
---
## 概要

[deferred] `kaji sync from-gitlab`（子 Issue #4）の対称実装として、`kaji sync from-github` を `GitHubProvider` 側に実装する。**実施は GitHub 復帰判断後**。本 Issue は計画記録のみ。

## 位置付け

- **trigger**: GitHub 復帰判断後
- **本 Issue では実装しない** — 計画と影響範囲を記録するのみ
- `local-p1-1` bucket の forge 連携項目を、本 EPIC で先取りせず GitHub 復帰時に着手するための独立 tracking

## 目的

- GitHub 側の cache 自動 populate 機能を `from-gitlab` と対称な形で計画段階で保持する
- GitHub 復帰時に `kaji sync from-github` が即座に着手できる状態にする

## ユーザーストーリー

- maintainer として、GitHub 復帰時に cache populate の実装スコープが明確になっている状態にしたい
- maintainer として、`from-gitlab` の実装結果が `from-github` の参考実装になる状態にしたい

## スコープ

### IN（**本 Issue では計画記録のみ、実装しない**）

- 実装範囲（`kaji sync from-gitlab` の対称実装、子 Issue #4 の構造を踏襲）
- 影響範囲:
  - `kaji_harness/cli_main.py` の `sync` dispatcher への `from-github` 追加
  - `GitHubProvider` 拡張（cache populate 用 list 取得）
  - `.kaji/cache/gh-*.json` 形式の維持
- 子 Issue #4（`from-gitlab`）完了後、その実装パターンを再利用する旨

### OUT

- 実装そのもの — GitHub 復帰判断後に本 Issue を起点として着手する

## 完了条件

- [ ] **本 Issue は GitHub 復帰判断まで open 維持**
- [ ] 復帰判断時、本 Issue の計画記録を起点に実装着手し、実装完了後 `closed --reason completed`

## 不要になる条件

- GitHub 復帰の見込みがなくなった場合 → `closed --reason not-planned`

## 依存

- 子 Issue #4（`kaji sync from-gitlab`）の完了（実装パターンの参照元）
- GitHub 復帰判断（trigger）

## 参照

- bucket Issue: `local-p1-1`
- 子 Issue #4: 本 EPIC（実装後に対称構造を踏襲）
- 既存 GitHubProvider: `kaji_harness/providers/github.py`
- 既存 cached read 経路: `kaji_harness/providers/local.py:493`、`kaji_harness/providers/local.py:753`
- design.md § 残課題: `draft/design/local-mode/design.md`
