---
id: local-pc5090-18
title: 'post-fix: dev workflow final-check に BACK transition 不足 (skill/YAML 不整合)'
state: closed
slug: post-fix-dev-workflow-final-check-back-t
labels:
- type:bug
created_at: '2026-05-09T13:35:15Z'
closed_at: '2026-05-09T13:35:22Z'
closed_by: pc5090
close_reason: skill/YAML 不整合を commit e7b9c96 で修正済み。記録目的のため即クローズ。
---
## 概要

dev workflow `feature-development-local.yaml` の `final-check` step で `BACK` transition が宣言されておらず、`/i-dev-final-check` skill が仕様通り `BACK` 判定を返した際に harness が `InvalidVerdictValue` で reject していた問題を修正した。

## 検出経緯

local-pc5090-16 の作業中、fix-code 完了後に verify-code 未取得のまま `/i-dev-final-check local-pc5090-16` を実行した際、skill は `SKILL.md:104-106` の規定通り `BACK` を返したが、harness が以下のエラーで停止:

```
Error: 'BACK' not in {'PASS', 'RETRY', 'ABORT'}.
This indicates a prompt violation — do not retry.
```

## 原因

- `.claude/skills/i-dev-final-check/SKILL.md:300-305` は `BACK` を valid status として明記
- `.kaji/wf/feature-development-local.yaml:112-115` の `final-check` step の `on:` ブロックは `PASS / RETRY / ABORT` のみ
- harness は step の `on:` keys から `valid_statuses` を構築する (`kaji_harness/prompt.py:65`)
- → skill prompt と workflow YAML の transition 宣言が不整合

## 対応内容

`.kaji/wf/feature-development-local.yaml` の `final-check` step に `BACK: review-code` を追加（commit `e7b9c96`）。

`review-code` は `cycles.code-review.entry` であり、code-review cycle のエントリポイント。`BACK` で fix/verify cycle を再評価するのが構造的に整合する。

## 関連

- 該当 commit: `e7b9c96` chore(wf): add BACK: review-code transition to dev workflow final-check
- 検出時の Issue: local-pc5090-16（作業中、verify-code は別途手動実行で復帰）
- 同種の不整合の可能性: `i-doc-final-check` skill / docs workflow YAML 側は未調査（本 Issue のスコープ外）

## ステータス

修正済み・即クローズ（記録目的のみ）。
