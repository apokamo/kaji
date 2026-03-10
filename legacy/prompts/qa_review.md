# QA Review Prompt

Issue ${issue_url} の `## Bugfix agent QA` セクションをレビューしてください。

## タスク

1. `gh issue view` で最新の Issue 本文を取得
2. QA セクションの必須アウトプットを確認
3. PR_CREATE ステートに移行可能か判定

## 完了条件チェックリスト

| # | 項目 | 確認内容 |
|---|---|---|
| 1 | **ソースレビュー** | ソースレビューの観点と結果が妥当である |
| 2 | **追加テスト結果** | 追加テストが実行され、結果が記載されている |
| 3 | **全体品質** | 実装品質がPR作成に進むために十分なレベルに達している |

## 禁止事項

**次ステート以降の責務を新規に実行しない**
- 例：PR作成、マージ

**既に完了した責務を再実行しない**
- 例：設計を作り直す、調査を再実行

※ 記載内容の検証（品質チェック、整合性確認）は**許可**されています。

## 出力形式

```markdown
### QA Review Result

#### 検証内容
- <実施した検証と結果を具体的に記載>

#### チェックリスト
- ソースレビュー: <OK/NG + 具体的根拠>
- 追加テスト結果: <OK/NG + 具体的根拠>
- 全体品質: <OK/NG + 具体的根拠>

## VERDICT
- Result: PASS | RETRY_QA | RETRY_IMPLEMENT | BACK_DESIGN
- Reason: <判定理由>
- Evidence: <具体的な判断根拠>
- Suggestion: <RETRY/BACK時: 具体的な修正指示>
```

## 判定ガイドライン

| 状況 | VERDICT | 次のステート |
|---|---|---|
| QA完了、矛盾・問題がない | PASS | PR_CREATE |
| QAに軽微な問題があり修正が必要 | RETRY_QA | QA |
| 実装に問題があり修正が必要 | RETRY_IMPLEMENT | IMPLEMENT |
| 設計レベルの問題があり設計からやり直す必要がある | BACK_DESIGN | DETAIL_DESIGN |

### BACK_DESIGNの判断基準

以下の場合は BACK_DESIGN を選択:
- QA中に設計上の矛盾が発見された
- テストケース自体に問題がある
- アーキテクチャレベルの変更が必要

## レポート方法

1. `gh issue comment` で Issue にコメント投稿（VERDICT判定）
2. **PASS判定時のみ**: 共通ルールに従い `gh issue edit` で Issue 本文を更新