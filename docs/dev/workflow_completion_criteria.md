# ワークフロー完了条件

フェーズ別の完了条件チェックリスト。`/i-dev-final-check` および `/i-doc-final-check` でエビデンス確認に使用する。

## feature-development ワークフロー

### フェーズ別完了条件

| フェーズ | スキル | 完了条件 | エビデンス |
|----------|--------|----------|-----------|
| 設計 | `/issue-design` | 設計書が作成・コミットされている | Issue コメント「設計書作成完了」 |
| 設計レビュー | `/issue-review-design` | レビューで PASS 判定 | Issue コメント「設計レビュー結果」 |
| 実装 | `/issue-implement` | テスト・実装が完了し品質チェック通過 | Issue コメント「実装完了報告」 |
| コードレビュー | `/issue-review-code` | レビューで PASS 判定 | Issue コメント「コードレビュー結果」 |
| ドキュメントチェック | `/issue-doc-check` | 影響ドキュメントの更新完了 | Issue コメント |
| 最終チェック | `/i-dev-final-check` | 全エビデンス確認・品質チェック全パス | Issue コメント「Final Check 結果」 |

### 品質チェック基準

- `ruff check`: エラーなし
- `ruff format --check`: フォーマット差分なし
- `mypy`: エラーなし
- `pytest`: 全パス（baseline failure を除く）

## docs-maintenance ワークフロー

### フェーズ別完了条件

| フェーズ | スキル | 完了条件 | エビデンス |
|----------|--------|----------|-----------|
| docs 更新 | `/i-doc-update` | ドキュメントが更新・コミットされている | Issue コメント |
| docs レビュー | `/i-doc-review` | レビューで PASS 判定 | Issue コメント |
| 最終チェック | `/i-doc-final-check` | リンクチェック通過・完了条件達成 | Issue コメント「Doc Final Check 結果」 |

### 品質チェック基準

- `make verify-docs`: リンク切れなし
