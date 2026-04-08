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

## feature-development

コード変更を伴う Issue のワークフロー。設計 → 実装 → コードレビュー → ドキュメントチェック → 最終チェック → PR。

詳細: [workflow_feature_development.md](workflow_feature_development.md)

## docs-maintenance

ドキュメント修正のみの Issue のワークフロー。コード・設定・テストは変更せず、現行実装との整合性を監査しながら docs を更新する。

詳細: [workflow_docs_maintenance.md](workflow_docs_maintenance.md)

## 関連ドキュメント

- [完了条件](workflow_completion_criteria.md) — フェーズ別の完了条件チェックリスト
- [スキル横断ルール](shared_skill_rules.md) — スキル間の責務境界
- [ドキュメント更新基準](documentation_update_criteria.md) — ドキュメント更新要否の判断
