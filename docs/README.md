# ドキュメント索引

kaji のドキュメント一覧。[Diataxis フレームワーク](https://diataxis.fr/) に基づいて分類。

## How-to（開発ワークフロー）

| ドキュメント | 概要 |
|-------------|------|
| [ワークフロー概要](dev/workflow_overview.md) | Issue 種別から workflow を選択するエントリポイント |
| [feature-development](dev/development_workflow.md) | TDD ベースで設計 → 実装 → レビュー → PR まで進める開発ワークフロー |
| [docs-maintenance](dev/docs_maintenance_workflow.md) | コード変更を含まない docs-only Issue 専用ワークフロー |
| [ワークフローガイド](dev/workflow_guide.md) | dev / docs-only の選択基準とスキル選択指針 |
| [完了条件](dev/workflow_completion_criteria.md) | 各フェーズで PASS とみなすための具体的チェックリスト |
| [テスト規約](dev/testing-convention.md) | S/M/L サイズ別テスト戦略と Given-When-Then 原則 |
| [ドキュメント更新基準](dev/documentation_update_criteria.md) | コード変更ごとに docs 更新要否を判断するフレームワーク |
| [スキル横断ルール](dev/shared_skill_rules.md) | review / fix / verify サイクルの責務分離と新規指摘禁止ルール |
| [GitHub ラベル運用](dev/labels.md) | `.github/labels.yml` 管理・追加削除手順・bot 所有ラベルとの境界 |
| [ワークフロー作成](dev/workflow-authoring.md) | workflows/*.yaml の step / cycle / verdict 遷移の書き方 |
| [スキル作成](dev/skill-authoring.md) | `.claude/skills/` 配下スキルファイルの構造と verdict 規約 |

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

### Python 品質規約

| ドキュメント | 概要 |
|-------------|------|
| [Python スタイル規約](reference/python/python-style.md) | フォーマット・インポート・クラス設計の規約 |
| [命名規則](reference/python/naming-conventions.md) | 変数・関数・kaji 固有語の命名パターン |
| [型ヒント](reference/python/type-hints.md) | 型アノテーション・dataclass の書き方 |
| [docstring スタイル](reference/python/docstring-style.md) | Google style docstring の記述規約 |
| [エラーハンドリング](reference/python/error-handling.md) | HarnessError 階層と例外処理パターン |
| [ロギング](reference/python/logging.md) | RunLogger JSONL 契約とイベント仕様 |

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
