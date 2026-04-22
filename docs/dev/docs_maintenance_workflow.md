# docs-maintenance ワークフロー

docs-only Issue のためのドキュメント修正ワークフロー。

## フロー概要

issue-start → i-doc-update → i-doc-review → (i-doc-fix → i-doc-verify) → i-doc-final-check → i-pr

## 実行制約

- コード、設定、テストは変更しない
- 事実確認のための read / search / コマンド実行は許可
- docs だけでは安全に吸収できない問題は ABORT

## 各スキルの責務

| スキル | 責務 | エージェント |
|--------|------|-------------|
| i-doc-update | ドキュメント更新 | claude |
| i-doc-review | 整合性レビュー（新規指摘可） | codex |
| i-doc-fix | レビュー指摘対応 | claude |
| i-doc-verify | 修正確認（新規指摘不可） | codex |

## 整合性監査の観点

- 現行コードとの整合
- CLAUDE.md の運用ルールとの整合
- workflow / skill 構成との整合
- リンク切れ・古いコマンド例の有無

## リンクチェック

- i-doc-update の初回: `python3 scripts/check_doc_links.py`（全体チェック）
- i-doc-review / i-doc-fix / i-doc-verify: `python3 scripts/check_doc_links.py <変更ファイル...>`（限定チェック）

## レビューサイクル

- 最大 3 イテレーション（doc-review cycle）
- 超過時は ABORT
- BACK は使用しない（PASS / RETRY / ABORT のみ）
