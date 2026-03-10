# DETAIL_DESIGN State Prompt

Issue ${issue_url} の INVESTIGATE 結果に基づき、詳細設計を作成してください。

## タスク

1. **変更計画**: 対象ファイル・関数と計画する変更内容を詳細に記載
   - 実装に十分な詳細度で記述
   - 必要に応じてコードスニペットを含める

2. **実装手順**: ステップバイステップの実装手順を記載

3. **テストケース一覧**: テストケースを列挙（目的、入力、期待結果、サイズを含む）
   - Size S: 単一関数・境界値・正常系 など単体テスト
   - Size M: 複数コンポーネント連携・異常系・エッジケース
   - Size L: エンドツーエンド・統合テスト・シナリオテスト

4. **影響ドキュメント**: 本実装に伴い更新が必要なドキュメントを列挙

5. **補足**: 追加の実装注意事項

## 出力形式

```
## Bugfix agent DETAIL_DESIGN

### DETAIL_DESIGN / 変更計画
- File/Function: <...>
- Steps: <bullet list>

### DETAIL_DESIGN / テストケース
| ID | Purpose | Input | Expected | Size(S/M/L) |
|----|---------|-------|----------|-------------|

### DETAIL_DESIGN / 影響ドキュメント
| ドキュメント | 更新内容 | 必要性 |
|-------------|---------|--------|

### DETAIL_DESIGN / 補足
- ...
```

## Issue 更新方法

1. `gh issue view` で Issue 本文を取得
2. Issue 本文を更新:
   - 初回（Loop=1）: Output を Issue 本文の末尾に追記
   - 2回目以降（Loop>=2）: 既存の `## Bugfix agent DETAIL_DESIGN` セクションを削除し、新しい Output を末尾に追記
3. `gh issue edit` で Issue 本文を更新
4. `gh issue comment` で `DETAIL_DESIGN agent Update` コメントとして更新内容のサマリーを投稿

## 証跡保存先

`${artifacts_dir}`
