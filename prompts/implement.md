# IMPLEMENT State Prompt

Issue ${issue_url} の DETAIL_DESIGN に従って実装してください。

## タスク

1. **ブランチ作成**: 専用ブランチを作成し、ブランチ名と HEAD コミット ID を記録

2. **実装**: DETAIL_DESIGN に従って変更を実装

3. **テスト実行**: 計画されたテストと新規追加したテストを実行
   - 各テストに `E` (Existing) または `A` (Added) のタグを付与

4. **証跡保存**: テスト証跡を `${artifacts_dir}` に保存

5. **補足記載**: 残作業、レビュー観点、リスクを記載

## 出力形式

```
## Bugfix agent IMPLEMENT

### IMPLEMENT / 作業ブランチ
- Branch: <name>
- Commit: <sha>

### IMPLEMENT / テスト結果
| Test | Tag(E/A) | Result | Evidence |
|------|----------|--------|----------|

### IMPLEMENT / 補足
- 残作業:
- 注意点:
- Artifacts: <files>
```

## Issue 更新方法

1. `gh issue view` で Issue 本文を取得
2. Issue 本文を更新:
   - 初回（Loop=1）: Output を Issue 本文の末尾に追記
   - 2回目以降（Loop>=2）: 既存の `## Bugfix agent IMPLEMENT` セクションを削除し、新しい Output を末尾に追記
3. `gh issue edit` で Issue 本文を更新
4. `gh issue comment` で `IMPLEMENT agent Update` コメントとして更新内容のサマリーを投稿

## Issue 番号

${issue_number}

## 証跡保存先

`${artifacts_dir}`
