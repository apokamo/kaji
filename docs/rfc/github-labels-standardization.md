# RFC: GitHub ラベル標準化

## ステータス

採用済み

## 概要

GitHub Issue のラベルに `type:` プレフィックスを導入し、ラベル体系を標準化する。

## 背景

現行のラベル（`enhancement`, `bug`, `documentation` 等）は GitHub デフォルトに依存しており、体系的な分類がない。`security` タイプも未対応。

## 提案

### プレフィックス付きラベル

| type | ラベル | 用途 |
|------|--------|------|
| `feat` | `type:feature` | 新機能追加 |
| `fix` | `type:bug` | バグ修正 |
| `refactor` | `type:refactor` | リファクタリング |
| `docs` | `type:docs` | ドキュメント |
| `test` | `type:test` | テスト追加・改善 |
| `chore` | `type:chore` | 雑務 |
| `perf` | `type:perf` | パフォーマンス改善 |
| `security` | `type:security` | セキュリティ修正 |

### 移行方針

- **新規 Issue のみ対象**: `/issue-create` スキルが `type:` プレフィックス付きラベルを付与
- **既存ラベルは残す**: `enhancement`, `bug`, `documentation` 等の既存ラベルは削除しない
- **共存期間**: 無期限。既存 Issue の再ラベルは行わない

### 根拠

- プレフィックスによりラベルの用途が自明になる
- 将来的に `priority:`, `scope:` 等の名前空間を追加可能
- 既存 Issue への影響がゼロ
