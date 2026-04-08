# ドキュメント索引

kaji のドキュメント一覧。[Diataxis フレームワーク](https://diataxis.fr/) に基づいて分類。

## How-to（開発ワークフロー）

| ドキュメント | 概要 |
|-------------|------|
| [ワークフロー概要](dev/workflow_overview.md) | ワークフロー選択のエントリポイント |
| [feature-development](dev/workflow_feature_development.md) | コード変更を伴う開発ワークフロー |
| [docs-maintenance](dev/workflow_docs_maintenance.md) | ドキュメント修正ワークフロー |
| [ワークフローガイド](dev/workflow_guide.md) | ワークフロー選択基準 |
| [完了条件](dev/workflow_completion_criteria.md) | フェーズ別完了条件チェックリスト |
| [テスト規約](dev/testing-convention.md) | テストサイズ定義・テスト戦略の原則 |
| [ドキュメント更新基準](dev/documentation_update_criteria.md) | ドキュメント更新要否の判断フレームワーク |
| [スキル横断ルール](dev/shared_skill_rules.md) | スキル間の責務境界 |
| [ワークフロー作成](dev/workflow-authoring.md) | ワークフロー YAML の書き方 |
| [スキル作成](dev/skill-authoring.md) | スキルファイルの書き方 |

## Tutorials（ガイド）

| ドキュメント | 概要 |
|-------------|------|
| [Git Worktree ガイド](guides/git-worktree.md) | Bare Repository + Worktree パターン |
| [Git コミット戦略](guides/git-commit-flow.md) | git absorb + --no-ff ワークフロー |

## Reference（リファレンス）

| ドキュメント | 概要 |
|-------------|------|
| [アーキテクチャ](ARCHITECTURE.md) | システム構成・モジュール依存関係 |
| [テストサイズ判断ガイド](reference/testing-size-guide.md) | S/M/L の境界ケース判断基準 |
| [CLI ガイド](cli-guides/) | CLI 操作リファレンス |

## Explanation（コンセプト）

| ドキュメント | 概要 |
|-------------|------|
| [AI 駆動開発戦略](concepts/ai-driven-strategy.md) | 95% AI / 5% 人間の開発モデル |
| [AI ドキュメント管理方針](concepts/ai-docs-management.md) | Docs-as-Code 運用ルール |

## ADR（アーキテクチャ決定記録）

[docs/adr/](adr/) を参照。

## RFC（提案・標準化）

| ドキュメント | 概要 |
|-------------|------|
| [GitHub ラベル標準化](rfc/github-labels-standardization.md) | `type:` プレフィックス体系 |
