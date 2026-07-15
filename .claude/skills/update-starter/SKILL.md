---
name: update-starter
description: "公開済み kaji Release に managed starter を追随させ、独立 review 用の未 push candidate を作成する。"
---

# Update Starter

kaji 側の starter-sync tracking Issue を正本として、対象 release 間の変更を全件 3 区分し、
managed starter の local main に review 前の candidate を作る maintainer 専用 skill。

## 入力

`/update-starter <tracking_issue_id>`。Issue 本文から `starter_repo`、
`target_kaji_release`、任意の `starter_path` を読む。通常 path は kaji main worktree の
sibling `../<repo-name>`。remote identity が `starter_repo` と一致しなければ ABORT。

## 実行順

1. [starter sync runbook](../../../docs/operations/release/starter-sync-runbook.md) の前提と
   managed starters 表を確認する。tracking Issue の必須 field、target kaji Release の
   published 状態、対象 starter checkout と remote identity を検証する。
2. 最新の公開済み starter GitHub Release tag を開始点にする。Release 不在、tag / Release /
   dependency pin の矛盾、または同じ starter に古い `PENDING` があれば fallback せず ABORT。
3. この時点で初めて [classification guide](references/classification-guide.md) を読み、開始点から
   target までの CHANGELOG、commit、changed assets を全件 3 区分する。dependency / lockfile
   更新だけで完了と判定しない。
4. starter の remote main と同期した local main に区分 (1) だけを直接 commit する。
   feature branch / worktree / PR / merge は使わず、review 前に push しない。
5. repository 実体から manifest、lockfile、quality gate を解決して実行する。Python 固有名を
   前提にしない。`update-starter` / `review-starter-update` / `release-starter` 自身は starter に
   コピーしない。
6. 3 区分表、根拠、target、base SHA、candidate SHA、quality gate を同じ tracking Issue に報告する。
   区分 (1) が空なら commit を作らず `base == candidate` と N/A 根拠を報告する。

## Guardrails

- starter 内 `AGENTS.md` は consumer payload。品質 gate は読むが maintainer の Git 運用には使わない。
- local main が remote main へ fast-forward 同期不能なら ABORT。force push、tag、Release 作成は禁止。
- 全件 3 区分が埋まるまで PASS にしない。review 前 push 禁止。

## Verdict

`PASS | ABORT`。報告コメント投稿時は status に関係なく次を付ける。

```text
--verdict-step update-starter --verdict-status <STATUS> \
--verdict-meta target=<tag> --verdict-meta base=<SHA> --verdict-meta candidate=<SHA>
```

コメント末尾と stdout に共通 `---VERDICT---` block を出し、`verdict_path` が注入されている場合は
外部副作用完了後に同内容の pure YAML を最後に保存する。ABORT の suggestion は必須。

次は update と別 session で `/review-starter-update <tracking_issue_id>` を実行する。
