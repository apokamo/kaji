# PR_CREATE State Prompt

Issue ${issue_url} の実装ブランチから Pull Request を作成してください。

## タスク

1. QA_REVIEW が PASS していることを確認（blocker がないこと）

2. 現在の実装ブランチから `gh pr create` コマンドを実行
   - サマリー + 最終テスト結果を含める

3. PR URL と主要なメタデータを Issue ${issue_number} に投稿

## 出力形式

```
### PR_CREATE / PR情報
- Branch: <name>
- Commit: <sha>
- PR URL: <link>
- Summary: <bullet>
- Final Tests: <table/list>
```

## レポート方法

`gh issue comment` で Issue ${issue_number} にコメント投稿

## 注意事項

Issue #184 の PR_CREATE の注意事項に従ってください。
