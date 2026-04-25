# AI ドキュメント管理方針

## 概要

kaji における Docs-as-Code 運用ルール。ドキュメントはコードと同じリポジトリで管理し、同じレビュープロセスを通す。

## 原則

### 1. ドキュメントはコードの一部

- `docs/` 配下で Markdown 管理
- コード変更に伴うドキュメント更新は同一 PR に含める
- 各フェーズでドキュメント影響を確認し、`/i-dev-final-check` で最終確定

### 2. 段階的開示

- ドキュメントは小さく保つ
- 大きくなるなら構造化して分割する
- コードから推論できる情報は書かない

### 3. 追加より削除が難しい

- 追加時に本当に必要か判断する
- 不要な情報は積極的に削除・スリム化する
- 具体的なコードはドキュメントに書かず、実コードへのポインターを記載する

## ドキュメント構成

[Diataxis フレームワーク](https://diataxis.fr/) に基づく分類:

| カテゴリ | ディレクトリ | 用途 |
|---------|-------------|------|
| Tutorials | docs/guides/ | 手順ガイド（Git worktree、コミット戦略等） |
| How-to | docs/dev/ | 開発ワークフロー・規約 |
| Reference | docs/reference/ | テストサイズガイド等 |
| Explanation | docs/concepts/ | 設計思想・戦略の説明 |
| ADR | docs/adr/ | アーキテクチャ決定記録 |
| RFC | docs/rfc/ | 提案・標準化方針 |
| CLI | docs/cli-guides/ | CLI 操作ガイド |

## ワークフローとの統合

ドキュメント更新の要否判断は [documentation_update_criteria.md](../dev/documentation_update_criteria.md) を一次情報源とする。本節はそのワークフローへの組み込みを述べる。

### feature-development

ドキュメント整合性は 3 段階の防衛線で担保する:

1. **設計書のテーブル**: 設計書に「影響ドキュメント」テーブルを記載し、想定スコープを明示する
2. **各サイクル内の影響確認**: 設計・実装・レビュー各サイクルで影響範囲の差分を確認する
3. **`/i-dev-final-check`**: PR 作成前に網羅性をゲートし、漏れがあれば差し戻す

### docs-maintenance

1. `/i-doc-update` でドキュメントを更新
2. `/i-doc-review` で整合性レビュー
3. `/i-doc-final-check` でリンクチェック・完了条件検証

## 設計書のライフサイクル

| フェーズ | 場所 | 説明 |
|---------|------|------|
| 作業中 | `draft/design/issue-XXX-*.md` | worktree 内、コミット対象 |
| final-check 時 | Issue 本文にアーカイブ | `<details>` タグで折りたたんで本文末尾に追記（[shared_skill_rules.md の「設計書アーカイブ」節](../dev/shared_skill_rules.md) を参照） |
| 恒久化 | `docs/adr/` | ADR として永続化（該当する場合のみ） |
