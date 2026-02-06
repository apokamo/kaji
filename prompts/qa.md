# QA State Prompt

Issue ${issue_url} の実装結果に対して QA を実施してください。

## タスク

1. **ソースレビュー観点**: 変更全体のソースレビュー（diff + 周辺コード）を実施

2. **追加 QA 観点**: IMPLEMENT のテストリスト以外の追加 QA テストシナリオを実行
   - 目的と証跡を `${artifacts_dir}` に記録

3. **検証結果**: 検証結果と残課題をまとめる

## 出力形式

```
## Bugfix agent QA

### QA / ソースレビュー観点
- ...

### QA / 追加QA観点
- ...

### QA / 検証結果
| 観点 | 実施内容 | Result | Evidence |

### QA / 残課題
- ...
```

## Issue 更新方法

1. `gh issue view` で Issue 本文を取得
2. Issue 本文を更新:
   - 初回（Loop=1）: Output を Issue 本文の末尾に追記
   - 2回目以降（Loop>=2）: 既存の `## Bugfix agent QA` セクションを削除し、新しい Output を末尾に追記
3. `gh issue edit` で Issue 本文を更新
4. `gh issue comment` で `QA agent Update` コメントとして更新内容のサマリーを投稿

## 証跡保存先

`${artifacts_dir}`
