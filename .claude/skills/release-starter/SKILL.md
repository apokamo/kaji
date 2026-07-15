---
name: release-starter
description: "独立 review 済み starter candidate を承認 gate 後に atomic push し、検証済み snapshot として公開する。"
---

# Release Starter

`/release-starter <tracking_issue_id>` で managed starter の検証済み template snapshot を公開する。

## Pre-flight

1. tracking Issue と [starter sync runbook](../../../docs/operations/release/starter-sync-runbook.md) から
   target、starter identity、local path を解決する。
2. `kaji issue resolve-verdict <id> --step review-starter-update --require-meta target
   --require-meta base --require-meta candidate` を実行し、最新 verdict が独立 review PASS で、
   meta.target == target、meta.candidate == local main HEAD であることを確認する。未検出、不正 marker、
   meta 欠落、不一致は fail-closed で ABORT。
3. local main clean、target kaji Release published、dependency / lockfile version 整合、quality gate を確認する。
4. `meta.base == meta.candidate` の N/A を release-plan より先に分岐し、N/A では
   release-plan を呼ばない。ただし N/A でも close 前に remote main == meta.base
   (== meta.candidate) を必須とし、review PASS 後に remote main が前進していれば
   stale review evidence として ABORT する。変更 candidate の場合だけ、この時点で初めて
   [pre-flight and recovery](references/preflight-and-recovery.md) を読み、観測 JSON を release-plan へ
   渡す。未公開 path は remote main == meta.base、公開済み残処理 path は remote main ==
   meta.candidate を必須にする。

## Publish

- N/A (`meta.base == meta.candidate`): 上記の先行分岐で、独立 review PASS かつ remote main ==
  meta.base (== meta.candidate) を確認した後だけ kaji Release の repository 別状態表を
  `N/A` と理由付きで更新し tracking Issue を close する。remote main が前進していれば
  stale review evidence として ABORT する。starter tag / Release は作らない。
- ref push path: candidate SHA と `kaji-vX.Y.Z` または `kaji-vX.Y.Z-rN` を提示し、workflow 外で
  **人間の明示承認**を得てから annotated tag と main を `git push --atomic` する。
- push 後は [Release notes template](templates/release-notes.md) から `gh release create`、kaji Release
  状態表 `PENDING -> PASS`、tracking Issue close の固定順で処理する。
- route 2 の部分成功再実行は release-plan が返す不足分だけを処理する。新 tag、ref push、再承認は不要。

## Guardrails

force push、tag 上書き、lightweight tag、review 前 publish を禁止する。GitHub Release 作成の再試行で
`kaji-vX.Y.Z-rN` を増やさない。starter の失敗を理由に公開済み kaji tag / Release / PyPI を rollback
しない。観測矛盾は ABORT し、人間へ値を提示する。

## Verdict

`PASS | ABORT`。報告コメントに `--verdict-step release-starter --verdict-status <STATUS>` を付け、
コメント末尾と stdout に共通 verdict block を出す。注入時は全外部副作用の後、最後に
`verdict_path` へ pure YAML を保存する。ABORT は復旧 suggestion を必須とする。
