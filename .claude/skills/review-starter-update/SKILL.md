---
name: review-starter-update
description: "starter 追随 candidate を別 session で独立検証し、過不足と検証証跡を判定する。"
---

# Review Starter Update

`update-starter` と別 session で、updater の結論を正解とせず starter candidate を独立 review する。

## 入力

`/review-starter-update <tracking_issue_id>`。tracking Issue、対象 kaji tag、starter remote main の
base SHA、starter local main の candidate SHA を固定する。

## 実行順

1. tracking Issue と [starter sync runbook](../../../docs/operations/release/starter-sync-runbook.md) を読み、
   target / base / candidate と checkout identity を確認する。
2. updater の分類表を見ずに、target tag、CHANGELOG、upstream diff から調査集合を独立に再構成する。
3. この時点で初めて [review rubric](references/review-rubric.md) を読み、全件 3 区分、移植の完全性、
   過剰コピー、dependency / lock / docs、template cleanliness、quality gate evidence を照合する。
4. review 中に starter のファイルを修正しない。candidate SHA が変わったら review を中止して ABORT。
5. target tag、base SHA、candidate SHA を evidence に含むレビュー報告を tracking Issue に投稿する。

## 判定

- `PASS`: 全 rubric を満たし、candidate が固定されている。
- `RETRY`: 修正または分類の再検討で収束できる。`/update-starter` 再実行後は candidate が変わるため再 review。
- `ABORT`: 一次情報・identity・固定 SHA を安全に確定できない。

## Verdict

`PASS | RETRY | ABORT`。status に関係なくコメントコマンドへ以下を付ける。

```text
--verdict-step review-starter-update --verdict-status <STATUS> \
--verdict-meta target=<tag> --verdict-meta base=<SHA> --verdict-meta candidate=<SHA>
```

共通 schema は変更せず、target / base / candidate は marker meta と人間可読 evidence の両方へ残す。
コメント末尾、stdout、注入時は最後に `verdict_path` の pure YAML へ同じ verdict を保存する。
