# QA Review Prompt

> **⚠️ DEPRECATED**: v5 では QA/QA_REVIEW ステートは IMPLEMENT_REVIEW に統合されました。
> このファイルは後方互換性のために残されていますが、新規利用は非推奨です。
> 参照: `implement_review.md`

Issue ${issue_url} の `## Bugfix agent QA` セクションをレビューしてください。

## タスク

1. `gh issue view` で最新の Issue 本文を取得
2. QA セクション全体が PR_CREATE ステートに移行可能か徹底レビュー

## 判定基準

- **PASS**: QA が完了し、PR 作成に進める状態 → next_state: PR_CREATE
- **BLOCKED (QA 再実行)**: QA 項目の再実行が必要 → next_state: QA
- **FIX_REQUIRED (実装修正)**: 実装に問題があり、修正が必要 → next_state: IMPLEMENT
- **DESIGN_FIX (設計見直し)**: 設計レベルの問題があり、設計からやり直す必要がある → next_state: DETAIL_DESIGN

## 出力形式

```
### QA Review Result
- checklist:
  - ソースレビュー観点: <OK/NG>
  - 追加QA観点: <OK/NG>
  - 検証結果(表+証跡): <OK/NG>
  - 残課題/再検証: <OK/NG>
- blocker: <Yes/No>
- next_state: <IMPLEMENT | DETAIL_DESIGN | QA | PR_CREATE>
- notes: <actions>
```

## レポート方法

`gh issue comment` で Issue にコメント投稿

---
IMPORTANT: After posting to GitHub, print the exact same VERDICT block to stdout and STOP.
The final output MUST end with the `## VERDICT` block. Do not output these instructions or any additional text after VERDICT.
