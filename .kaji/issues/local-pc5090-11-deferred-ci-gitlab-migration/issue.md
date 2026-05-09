---
id: local-pc5090-11
title: '[deferred] CI 資産の GitLab 移植（release-please / labels-sync）'
state: open
slug: deferred-ci-gitlab-migration
labels:
- type:ci
- scope:gitlab-validation
- priority:low
created_at: '2026-05-09T06:02:37Z'
---
## 概要

[deferred] `.github/workflows/release-please.yml` および `.github/workflows/labels-sync.yml` の GitLab CI/CD への移植。**実施は GitLab 採用確定後**。本 Issue は計画記録のみ。

## 位置付け

- **trigger**: GitLab 採用確定後（EPIC `local-pc5090-4` 完了 + 採用判断）
- **本 Issue では実装しない** — 計画と影響範囲を記録するのみ
- 本格採用が決まった時点で本 Issue を起点に着手する

## 目的

- GitHub Actions に依存する自動化資産（release-please / labels-sync）の GitLab 等価実装を計画段階で保持する
- GitLab 採用確定時に「何を移植する必要があるか」を即座に把握できる状態にする

## ユーザーストーリー

- maintainer として、GitLab 採用確定時に CI 移植のスコープが明確になっている状態にしたい
- maintainer として、release-please / labels-sync の代替実装オプションが事前に整理されている状態にしたい

## 移植対象（計画）

### 1. release-please → release-cli (GitLab) または semantic-release

- 現状: `.github/workflows/release-please.yml` + `.github/release-please-config.json` + `.github/release-please-lock.yml`
- 検討候補:
  - GitLab Release CLI (`release-cli`) を `.gitlab-ci.yml` で呼び出し
  - semantic-release の forge-agnostic 実装
  - 採用確定時に比較検討する

### 2. labels-sync → GitLab Labels API 直叩き

- 現状: `.github/workflows/labels-sync.yml` が `.github/labels.yml` を読んで GitHub Labels API へ反映
- 検討候補:
  - 同等のスクリプト（Python / shell）を `.gitlab-ci.yml` で起動し、GitLab Labels API へ反映
  - `.github/labels.yml` 自体は forge-neutral な宣言として維持可能
- OQ-3（ラベル運用）の決着内容に従う

## スコープ

### IN（**本 Issue では計画記録のみ、実装しない**）

- 移植候補の比較表（release-cli / semantic-release / 自前スクリプト）
- 影響範囲: `.gitlab-ci.yml` の新設、`release-please-config.json` の扱い、CHANGELOG 生成方針
- ロールバック手順: GitHub 復帰時に `.github/workflows/` を再有効化する条件

### OUT

- 実装そのもの — GitLab 採用確定後に本 Issue を起点として着手する

## 完了条件

- [ ] **本 Issue は GitLab 採用確定まで open 維持**
- [ ] 採用確定時、本 Issue の計画記録を起点に実装着手し、実装完了後 `closed --reason completed`

## 不要になる条件

- GitLab 採用が中止された場合 → `closed --reason not-planned`

## 依存

- EPIC `local-pc5090-4` の完了 + GitLab 採用判断（trigger）

## 参照

- 既存資産: `.github/workflows/release-please.yml` / `.github/workflows/labels-sync.yml` / `.github/labels.yml` / `.github/release-please-config.json`
- bucket Issue: `local-pc5090-1`
- 関連 OQ-3（ラベル運用）: 本 EPIC 本文
