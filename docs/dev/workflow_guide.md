# ワークフローガイド

## ワークフロー選択基準

| Issue の種類 | 使用するワークフロー |
|-------------|---------------------|
| 機能追加・バグ修正・リファクタ | feature-development |
| ドキュメント修正のみ | docs-maintenance |

## feature-development

コード変更を伴う Issue のワークフロー。設計 → 実装 → コードレビュー → ドキュメントチェック → PR。

詳細: [workflow_feature_development.md](workflow_feature_development.md)

## docs-maintenance

ドキュメント修正のみの Issue のワークフロー。コード・設定・テストは変更せず、現行実装との整合性を監査しながら docs を更新する。

詳細: [workflow_docs_maintenance.md](workflow_docs_maintenance.md)
