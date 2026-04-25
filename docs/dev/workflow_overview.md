# Workflow Overview

Issue の種類に応じて使う workflow を選ぶための入口ドキュメント。

## どの workflow を使うか

| 条件 | 使用する workflow | 主なスキル |
|------|------------------|------------|
| コード変更を含む | dev workflow | `/issue-create` → `/issue-review-ready` → `/issue-start` → `/issue-design` → `/issue-review-design` → `/issue-implement` → `/issue-review-code` → `/i-dev-final-check` → `/i-pr` → `/issue-close` |
| docs のみ変更する | docs-only workflow | `/issue-create` → `/issue-review-ready` → `/issue-start` → `/i-doc-update` → `/i-doc-review` → `/i-doc-final-check` → `/i-pr` → `/issue-close` |

## type 別のフロー分岐（dev workflow 内）

dev workflow は単一のフロー図で表現されるが、Issue の `type:` ラベルに応じて各スキルの**中身**が分岐する。workflow 全体の形（どのスキルを呼ぶか）は type に依存しない — 分岐するのは各スキルが適用するチェック観点・テンプレート・実装手順である。

| type | 対象 Issue | 主な分岐の性格 |
|------|-----------|----------------|
| `type:feature` | 新機能追加 | ユースケース / IF 設計中心。標準 TDD |
| `type:bug` | バグ修正 | OB / EB / 再現手順が必須。再現テスト先行の TDD |
| `type:refactor` | リファクタ | 測定可能な改善指標が必須。振る舞い非変更の保証が絶対要件 |
| `type:docs` | docs-only 変更 | dev workflow ではなく docs-only workflow へルーティング |

**分岐対象スキル**: `issue-review-ready` / `issue-create` / `issue-design` / `issue-implement` / `issue-review-design` / `issue-review-code`

**分岐対象外スキル**: `issue-fix-ready` / `issue-fix-design` / `issue-fix-code` / `issue-verify-design` / `issue-verify-code` / `pr-fix` / `pr-verify` — レビューサイクルの収束保証（`issue-verify-code` の「新規指摘は行わない」原則）を損なうため、type 分岐を**入れない**。

**canonical 外 type のフォールバック**: `type:test` / `type:chore` / `type:perf` / `type:security` を受け取った場合、上記の分岐対象スキルは `type:feature` と同等の経路を適用する。

## 共通原則

- 詳細ルールは skill 本文ではなく `docs/dev/` を正本とする
- 各スキルは必要な docs だけを読む
- 完了条件は各フェーズで段階的に確認し、final-check が全体を確定する
- `.claude/skills/` を実体とし、`.agents/skills/` は必要に応じて symlink で追随する

## 共有スキル

- `/i-pr`: workflow 固有判定を持たず、PR 作成に専念する
- `/issue-close`: merge 後の後始末を担当する

共有スキルの境界は [shared_skill_rules.md](./shared_skill_rules.md) を参照。

## 個別 workflow

- [development_workflow.md](./development_workflow.md)
- [docs_maintenance_workflow.md](./docs_maintenance_workflow.md)

## 関連ドキュメント

- [workflow_completion_criteria.md](./workflow_completion_criteria.md)
- [documentation_update_criteria.md](./documentation_update_criteria.md)
- [testing-convention.md](./testing-convention.md)
- [../../CLAUDE.md](../../CLAUDE.md) — Essential Commands（`make check` / `make verify-docs` 等）
