---
id: local-p1-1
title: forge 採用先確定後タスク bucket（forge 連携 / PR context / GitLabProvider / Large-forge
  テスト群）
state: closed
slug: forge-bucket-forge-pr-context-gitlabprov
labels:
- type:meta
- scope:phase6-or-later
created_at: '2026-05-08T17:26:50Z'
closed_at: '2026-05-13T11:59:45Z'
closed_by: p1
close_reason: completed
---
## 概要

Phase 5 着手前の方針転換 (2026-05-08、`draft/design/local-mode/design.md` § 履歴) で
GitHub 復旧前提を放棄したため、forge 採用先（gitlab 本格採用 / github 復帰 / 他 forge）
が確定するまで後送りされた **forge 連携 / forge 機能依存** タスクを集約 Issue として
保持する。

forge 採用先によって設計が根本から変わるため（例: gitlab → `GitLabProvider` 必要 /
github 復帰 → 既存 `GitHubProvider` 再活用 / 他 forge → 別実装）、現時点で個別 Issue
化しても放置となる。**forge 採用先確定時に、本 Issue を分解して個別 Issue 化** する。

## 着手 trigger

forge 採用先（gitlab 本格採用 / github 復帰 / 他）の確定。
判断基準は `docs/operations/local-mode-runbook.md` § 5「将来 forge への移行」を参照。

## 後送りタスクのチェックリスト

`draft/design/local-mode/design.md` § 残課題 と一致する：

### forge 連携機能（cache / sync）

- [ ] `kaji sync from-github` 実装（または採用 forge に応じた `kaji sync from-<forge>`）
- [ ] `kaji sync status` 実装
- [ ] `kaji sync local-to-github-plan` 実装（または採用 forge 向けの転記計画）
- [ ] `.kaji/cache/` の自動初期化と atomic write
- [ ] `kaji issue list` の local + cache 統合表示
- [ ] forge 移行時の Issue 一括転記支援（local → 採用 forge）

> 注: `kaji issue view gh:N` の cache reader (`view_cached_issue()`) は Phase 3-c で
> 実装済の既存契約。残課題は cache 自動 populate のみ。

### Phase 4 申し送り（PR context 注入）

- [ ] `<Provider>.resolve_pr_context(branch_name)` 実装（採用 forge に応じて）
- [ ] `pr_id` / `pr_ref` の prompt.py 自動注入
- [ ] `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` の暫定運用記述
  （`kaji pr list --search` で取得）を本注入経路に切り替え

### 新規 forge provider（gitlab 本格採用が決まった場合）

- [ ] `GitLabProvider` 実装
- [ ] `requires_provider` enum に `gitlab` 追加
- [ ] Workflow YAML スキーマ拡張（`requires_provider: gitlab`）
- [ ] `kaji_harness/providers/__init__.py` の dispatcher 拡張

### Large-forge テスト群

- [ ] `provider=<forge>` E2E workflow 完走テスト
- [ ] `kaji issue/pr` の実 forge ラウンドトリップテスト
- [ ] 実 forge API での sync 通信テスト
- [ ] 実 PR / MR review-comments 疎通テスト
- [ ] `make test-large-forge` ターゲット新設

## 受け入れ条件

- [ ] forge 採用先確定が記録されている（CHANGELOG / runbook v2 / design.md 履歴）
- [ ] 上記チェックリストが採用 forge 向けに分解され、個別 Issue として起票されている
- [ ] 本集約 Issue は `closed --reason completed`（分解後）または
  `closed --reason not-planned`（採用 forge では不要だった項目がある場合）で
  クローズ可能

## 参照

- 設計書 §残課題: `draft/design/local-mode/design.md`
- 検証期間運用 runbook: `docs/operations/local-mode-runbook.md`
- Phase 5 設計書: `draft/design/local-mode/phase5-design.md`
- Phase 5 implementation report: `draft/design/local-mode/phase5-implementation-report.md`
