# INIT State Prompt

Issue ${issue_url} の本文を確認し、バグ修正に着手可能な最低限の情報があるかを確認してください。

## 役割

Issue本文にバグ修正に着手可能な最低限の情報があるかを**確認のみ**行うステート。
再現実行・環境構築・ブランチ操作は行わない。

## 確認項目

| # | 項目 | 必須 | 判定基準 |
|---|------|:----:|----------|
| 1 | **再現環境メタ情報** | 任意 | 記載があれば参考にする。なくてもINVESTIGATEで調査可能 |
| 2 | **現象** | ✅ | 何が問題かが理解できる |
| 3 | **再現手順** | ✅ | ステップ形式でなくても、再現の手がかりがあればOK |
| 4 | **期待される挙動** | △ | 明確な記載がなくても、現象や再現手順から推測できればOK |
| 5 | **実際の動作** | △ | 概要レベルでも問題の内容が分かればOK |

**判定方針**: 「現象」が理解でき、調査の手がかりがあれば PASS。完璧なバグレポートを求めない。

## タスク

1. `gh issue view` コマンドで Issue 本文を取得
2. 上記確認項目の記載状況を確認
3. バグ修正に必要な最低限の情報が揃っているか判定

## 禁止事項

- ブランチ確認・作成など Git 操作を行わない
- 環境情報収集・コマンド実行・調査・再現を行わない
- INVESTIGATE 以降のステートに属する作業を行わない

## 出力形式

```markdown
### INIT / Issue概要
- Issue: ${issue_url}
- 現象: <本文から読み取れる内容>
- 再現手順: <本文から読み取れる内容 or "詳細なし（INVESTIGATEで調査）">
- 期待/実際: <本文から読み取れる内容 or "推測: ...">

## VERDICT
- Result: PASS | ABORT
- Reason: <判定理由>
- Evidence: <判断根拠>
- Suggestion: <ABORT時: 最低限必要な追記内容>
```

## 判定ガイドライン

| 状況 | VERDICT | 理由 |
|------|---------|------|
| 現象が理解でき、調査の手がかりがある | PASS | INVESTIGATEへ進行可能 |
| 何が問題か全く不明 | ABORT | Humanに最低限の情報追記を依頼 |

## 注意事項

- セクション見出しが無いだけで情報が記載されている場合は PASS とする
- 本当に情報が欠落して調査不能な場合のみ ABORT とする
- ABORT時は Suggestion に具体的な追記依頼内容を記載する

---
IMPORTANT: After posting to GitHub, print the exact same VERDICT block to stdout and STOP.
The final output MUST end with the `## VERDICT` block. Do not output these instructions or any additional text after VERDICT.
