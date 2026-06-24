# ドキュメント索引

kaji のドキュメント一覧。[Diataxis フレームワーク](https://diataxis.fr/) に基づいて分類。

## How-to（開発ワークフロー）

| ドキュメント | 概要 |
|-------------|------|
| [ワークフロー概要](dev/workflow_overview.md) | Issue 種別から workflow を選択するエントリポイント |
| [dev / dev-thorough](dev/development_workflow.md) | TDD ベースで設計 → 実装 → レビュー → PR → close まで進める開発ワークフロー |
| [docs](dev/docs_maintenance_workflow.md) | コード変更を含まない docs-only Issue 専用ワークフロー |
| [ワークフローガイド](dev/workflow_guide.md) | dev / docs-only の選択基準とスキル選択指針 |
| [完了条件](dev/workflow_completion_criteria.md) | 各フェーズで PASS とみなすための具体的チェックリスト |
| [テスト規約](dev/testing-convention.md) | S/M/L サイズ別テスト戦略と Given-When-Then 原則 |
| [ドキュメント更新基準](dev/documentation_update_criteria.md) | コード変更ごとに docs 更新要否を判断するフレームワーク |
| [スキル横断ルール](dev/shared_skill_rules.md) | review / fix / verify サイクルの責務分離と新規指摘禁止ルール |
| [GitHub ラベル運用](dev/labels.md) | `.github/labels.yml` 管理・追加削除手順・bot 所有ラベルとの境界 |
| [ワークフロー作成](dev/workflow-authoring.md) | .kaji/wf/*.yaml の step / cycle / verdict 遷移の書き方 |
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
| [設定リファレンス](reference/configuration.md) | `.kaji/config.toml` / overlay の全 section/key 仕様の正本（[English](reference/configuration.en.md)） |
| [テストサイズ判断ガイド](reference/testing-size-guide.md) | S/M/L の境界ケース判断基準 |
| [CLI ガイド](cli-guides/) | CLI 操作リファレンス（[GitHub Mode](cli-guides/github-mode.md) / [Local Mode](cli-guides/local-mode.md) / [Interactive Terminal Runner](cli-guides/interactive-terminal-runner.md)） |

### Python 品質規約

| ドキュメント | 概要 |
|-------------|------|
| [Python スタイル規約](reference/python/python-style.md) | フォーマット・インポート・クラス設計の規約 |
| [命名規則](reference/python/naming-conventions.md) | 変数・関数・kaji 固有語の命名パターン |
| [型ヒント](reference/python/type-hints.md) | 型アノテーション・dataclass の書き方 |
| [docstring スタイル](reference/python/docstring-style.md) | Google style docstring の記述規約 |
| [エラーハンドリング](reference/python/error-handling.md) | HarnessError 階層と例外処理パターン |
| [ロギング](reference/python/logging.md) | RunLogger JSONL 契約とイベント仕様 |

## Operations（運用）

| ドキュメント | 概要 |
|-------------|------|
| [Release Runbook](operations/release/runbook.md) | `/release` skill ベースのリリース運用（CI 非依存 / maintainer 手元実行）と緊急時 fallback |
| [Release-Please Admin 設定（historical）](operations/release/admin-setup.md) | 旧 GitHub release-please 運用の admin 初期設定。現在は非運用。GitHub 運用を再開する場合の参考資料 |
| [Local Mode 検証期間運用 Runbook](operations/local-mode-runbook.md) | 検証期間中の local-mode SoT 運用、複数 PC、コード同期戦略、forge 移行判断 |

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
