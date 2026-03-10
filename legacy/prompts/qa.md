# QA State Prompt

Issue ${issue_url} の実装結果に対して QA を実施してください。

## タスク

1. **ソースレビュー**: 変更全体のソースレビュー（diff + 周辺コード）を実施
   - レビュー観点と結果を記録

2. **追加テスト実行**: IMPLEMENT のテストリスト以外の追加 QA テストシナリオを計画・実行
   - 各テストに `Q` (QA) のタグを付与

3. **証跡保存**: テスト証跡を `${artifacts_dir}` に保存

4. **補足記載**: 残作業、レビュー観点、リスクを記載

## 出力形式

```
## Bugfix agent QA

### QA / ソースレビュー
- 観点: ...
- 結果: ...

### QA / 追加テスト結果
| Test | Tag(Q) | Result | Evidence |
|------|--------|--------|----------|

### QA / 補足
- 残作業:
- 注意点:
- Artifacts: <files>
```

## Issue 更新方法

1. `gh issue view` で Issue 本文を取得
2. Issue 本文を更新:
   - 初回（Loop=1）: Output を Issue 本文の末尾に追記
   - 2回目以降（Loop>=2）: 既存の `## Bugfix agent QA` セクションを削除し、新しい Output を末尾に追記
3. `gh issue edit` で Issue 本文を更新
4. `gh issue comment` で `QA agent Update` コメントとして更新内容のサマリーを投稿

## Issue 番号

${issue_number}

## 証跡保存先

`${artifacts_dir}`