# ワークフローガイド

ワークフローの選択基準と各ワークフローへのポインター。
ワークフロー全体の概要は [workflow_overview.md](workflow_overview.md) を参照。

## ワークフロー選択基準

| Issue の種類 | 使用するワークフロー |
|-------------|---------------------|
| 機能追加・バグ修正・リファクタ | feature-development |
| スキルファイルの改善 | feature-development |
| ドキュメント修正のみ | docs-maintenance |

判断に迷うケースは [workflow_overview.md](workflow_overview.md) の判断テーブルを参照。

## provider × workflow の対応表（Phase 4 以降）

各 builtin workflow が要求する provider type。`kaji run` 起動時に
`config.provider.type` と突合し、不整合を exit 2 で fail-fast する。

| Workflow | `requires_provider` | 末尾 step | 備考 |
|----------|---------------------|-----------|------|
| `feature-development.yaml` | `github` | `i-pr` | forge 必須 |
| `feature-development-light.yaml` | `github` | `i-pr` | forge 必須 |
| `implement-to-pr.yaml` | `github` | `i-pr` | forge 必須 |
| `feature-development-local.yaml` | `local` | `issue-close` | local merge (`--no-ff`) 前提 |
| `docs-maintenance-local.yaml` | `local` | `issue-close` | docs-only / local。Phase 5 追加 |
| `design-only.yaml` | `any` | `verify-design` | 設計完了で終わるため provider 中立 |

custom workflow への `requires_provider` 追加は推奨（[workflow-authoring.md](workflow-authoring.md)
§ `requires_provider` 参照）。

## 検証期間中の主 workflow（2026-05-08 以降）

GitHub 復旧前提を放棄した後の検証期間中は、**`feature-development-local.yaml`
と `docs-maintenance-local.yaml` が主 workflow** となる。github 用
(`feature-development.yaml` / `feature-development-light.yaml` /
`implement-to-pr.yaml`) は forge 通信を伴うため検証期間中は使用しない。
provider 切替の手順は [docs/cli-guides/local-mode.md](../cli-guides/local-mode.md)
を、検証期間中の運用は [docs/operations/local-mode-runbook.md](../operations/local-mode-runbook.md)
を参照。`provider=local` を default にする変更は行わない（user の設定に従う）。

## feature-development

コード変更を伴う Issue のワークフロー。設計 → 設計レビュー → 実装 → コードレビュー → 最終チェック → PR。

各 hand-off 直前（`design → review-design` / `implement → review-code`）には **pre-handoff review** が挟まる（capability-based: Claude Code は `kaji-code-reviewer` subagent、Codex / Gemini は main-session self-check）。詳細は [development_workflow.md § Pre-Handoff Review](development_workflow.md#prehandoff-review) を参照。

詳細: [development_workflow.md](development_workflow.md)

## docs-maintenance

ドキュメント修正のみの Issue のワークフロー。コード・設定・テストは変更せず、現行実装との整合性を監査しながら docs を更新する。

詳細: [docs_maintenance_workflow.md](docs_maintenance_workflow.md)

## 関連ドキュメント

- [完了条件](workflow_completion_criteria.md) — フェーズ別の完了条件チェックリスト
- [スキル横断ルール](shared_skill_rules.md) — スキル間の責務境界
- [ドキュメント更新基準](documentation_update_criteria.md) — ドキュメント更新要否の判断
