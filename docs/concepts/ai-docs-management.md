# AI ドキュメント管理方針

## 概要

kaji における Docs-as-Code 運用ルール。ドキュメントはコードと同じリポジトリで管理し、同じレビュープロセスを通す。

## 原則

### 1. ドキュメントはコードの一部

- `docs/` 配下で Markdown 管理
- コード変更に伴うドキュメント更新は同一 PR に含める
- `/issue-doc-check` で更新漏れを検出

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

### feature-development

1. 設計書に「影響ドキュメント」テーブルを記載
2. 実装後に `/issue-doc-check` で更新要否を再判定
3. `/i-dev-final-check` で最終確認

### docs-maintenance

1. `/i-doc-update` でドキュメントを更新
2. `/i-doc-review` で整合性レビュー
3. `/i-doc-final-check` でリンクチェック・完了条件検証

## 設計書のライフサイクル

| フェーズ | 場所 | 説明 |
|---------|------|------|
| 作業中 | `draft/design/issue-XXX-*.md` | worktree 内、コミット対象 |
| final-check 時 | Issue 本文にアーカイブ | `<details>` タグで折りたたみ |
| 恒久化 | `docs/adr/` | ADR として永続化（該当する場合のみ） |
