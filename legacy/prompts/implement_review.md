# IMPLEMENT Review Prompt (QA統合版)

Issue ${issue_url} の `## Bugfix agent IMPLEMENT` セクションをレビューし、QA観点での検証を行ってください。

## 役割

IMPLEMENT_REVIEWは従来のQA/QA_REVIEWを統合したステートです。
実装結果のレビューに加え、QA観点での検証も同時に行います。

## タスク

1. **独立テスト実行**: レビュワー自身が以下を実行し、結果を記録する
   ```
   ruff check bugfix_agent/ tests/ && ruff format bugfix_agent/ tests/ && mypy bugfix_agent/ && pytest
   ```
2. `gh issue view` で最新の Issue 本文を取得
3. IMPLEMENT セクションの必須アウトプットを確認
4. PR_CREATE ステートに移行可能か判定

## 完了条件チェックリスト

### 実装レビュー観点

| # | 項目 | 確認内容 |
|---|------|----------|
| 1 | **ブランチ情報** | 作業ブランチ名が記載されている |
| 2 | **実装内容** | 変更ファイル・関数が記載され、設計通りに実装されている |
| 3 | **テスト結果** | DETAIL_DESIGNのテストケースが実行され、結果が記載されている |

### QA観点（統合）

| # | 項目 | 確認内容 |
|---|------|----------|
| 4 | **ソースレビュー** | 変更全体のソースレビュー（diff + 周辺コード）に問題がない |
| 5 | **リグレッション** | 既存機能への影響がないことが確認されている |
| 6 | **テスト S/M/L 網羅性（PASSED）** | S・M・L 各サイズのテストが実装され、すべて PASSED であること |
| 7 | **独立テスト実行** | レビュワー自身が pytest を実行し、PASS を確認している |

## 禁止事項

**次ステート以降の責務を新規に実行しない**
- 例：PR作成、マージ

**既に完了した責務を再実行しない**
- 例：設計を作り直す、調査を再実行

※ 記載内容の検証（品質チェック、整合性確認）は**許可**されています。

## 出力形式

```markdown
### IMPLEMENT Review Result (QA統合)

#### 検証内容
- <実施した検証と結果を具体的に記載>

#### 独立テスト実行結果
```
<ruff check / ruff format / mypy / pytest の実行ログを貼り付ける>
```

#### チェックリスト
- ブランチ情報: <OK/NG + 具体的根拠>
- 実装内容: <OK/NG + 具体的根拠>
- テスト結果: <OK/NG + 具体的根拠>
- テスト S/M/L 網羅性（PASSED）: <OK/NG + 各サイズの件数>
- 独立テスト実行（PASSED）: <OK/NG + pytest サマリー>
- ソースレビュー: <OK/NG + 具体的根拠>
- リグレッション: <OK/NG + 具体的根拠>

## VERDICT
- Result: PASS | RETRY | BACK_DESIGN
- Reason: <判定理由>
- Evidence: <具体的な判断根拠>
- Suggestion: <RETRY/BACK_DESIGN時: 具体的な修正指示>
```

## 判定ガイドライン

| 状況 | VERDICT | 次のステート |
|------|---------|-------------|
| 実装完了、矛盾・問題がない | PASS | PR_CREATE |
| 実装に軽微な問題があり修正が必要 | RETRY | IMPLEMENT |
| pytest 出力が Issue コメントに記載されていない | RETRY | IMPLEMENT |
| テスト S/M/L いずれかが欠如または FAILED | RETRY | IMPLEMENT |
| 設計レベルの問題があり設計からやり直す必要がある | BACK_DESIGN | DETAIL_DESIGN |

### BACK_DESIGNの判断基準

以下の場合は BACK_DESIGN を選択:
- 実装中に設計上の矛盾が発見された
- テストケース自体に問題がある
- アーキテクチャレベルの変更が必要

## レポート方法

1. `gh issue comment` で Issue にコメント投稿（VERDICT判定）
2. **PASS判定時のみ**: 共通ルールに従い `gh issue edit` で Issue 本文を更新
