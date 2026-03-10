# IMPLEMENT State Prompt

Issue ${issue_url} の DETAIL_DESIGN に従って実装してください。

> **CRITICAL: テスト省略禁止**
> Size M（連携/異常系）および Size L（統合/E2E）のテストを省略してはならない。
> 「時間がない」「難しい」などの理由でテストをスキップすることは禁止。
> テストなし実装は IMPLEMENT_REVIEW で必ず RETRY される。

## タスク

1. **ブランチ作成**: 専用ブランチを作成し、ブランチ名と HEAD コミット ID を記録

2. **Red フェーズ**: DETAIL_DESIGN のテストケースをすべて先に実装し、失敗することを確認

3. **Green フェーズ**: テストが PASS するよう実装を行う

4. **リファクタリング**: コードの整理・重複排除（テストは引き続き PASS であること）

5. **品質チェック**: 以下をすべてパスすること
   ```
   ruff check bugfix_agent/ tests/ && ruff format bugfix_agent/ tests/ && mypy bugfix_agent/ && pytest
   ```

6. **証跡保存**: テスト証跡を `${artifacts_dir}` に保存

7. **補足記載**: 残作業、レビュー観点、リスクを記載

## 出力形式

```
## Bugfix agent IMPLEMENT

### IMPLEMENT / 作業ブランチ
- Branch: <name>
- Commit: <sha>

### IMPLEMENT / テスト結果
| Test | Tag(E/A) | Size(S/M/L) | Result | Evidence |
|------|----------|-------------|--------|----------|

### IMPLEMENT / 品質チェック
- ruff check: PASS/FAIL
- ruff format: PASS/FAIL
- mypy: PASS/FAIL
- pytest: PASS/FAIL (<passed>/<total> tests)

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
