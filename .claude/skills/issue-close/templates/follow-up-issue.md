# Follow-up Issue 本文テンプレート

`issue-close` が `### ワークフロー完了後の確認項目` の未完了項目を親 Issue から
移管するときに使う。角括弧の placeholder は作成前に実値へ置換する。

```markdown
<!-- kaji-follow-up-parent: [parent_issue_ref] -->

## 親 Issue

- [parent_issue_ref] [parent_issue_title]

## 未完了の確認項目

[unchecked_items]

## 参照 docs

[reference_docs]

## 証跡の記録先

- 各確認の実行日時、対象環境、操作またはコマンド、観測結果をこの Issue のコメントへ記録する
- 親 Issue のコメントや PR を根拠に使う場合は、参照先をコメントに明記する
- すべての証跡が揃った項目だけを `[x]` に更新する

## 完了と close 方針

- 本 Issue は workflow が自動 close しない
- 全項目の証跡を人間が確認し、すべて `[x]` になった後に手動で close する
- 未確認、失敗、判定不能の項目が 1 件でもあれば open のまま維持する
```
