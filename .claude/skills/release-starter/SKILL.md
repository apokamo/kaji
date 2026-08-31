---
name: release-starter
description: "独立 review 済み starter candidate を承認 gate 後に atomic push し、検証済み snapshot として公開する。"
---

# Release Starter

`/release-starter <tracking_issue_id>` で managed starter の検証済み template snapshot を公開する。

## Pre-flight

1. tracking Issue と [starter sync runbook](../../../docs/operations/release/starter-sync-runbook.md) から
   target（= 本文の現在の `syncing` batch から求まる `active_target`）、`covered_targets`（同じ
   batch の target 集合）、starter identity、local path を解決する。同じ本文読み取りから
   `tracking_issue_has_pending_tasks`（現在の batch 以外に `open` 行が残っているか）も導出する。
2. `kaji issue resolve-verdict <id> --step review-starter-update --require-meta target
   --require-meta base --require-meta candidate` を実行し、最新 verdict が独立 review PASS で、
   meta.target == active_target、meta.candidate == local main HEAD であることを確認する。未検出、
   不正 marker、meta 欠落、不一致は fail-closed で ABORT。
3. local main clean、target kaji Release published、dependency / lockfile version 整合、quality gate を確認する。
4. `meta.base == meta.candidate` の N/A を release-plan より先に分岐し、N/A では
   release-plan を呼ばない。ただし N/A でも close 前に remote main == meta.base
   (== meta.candidate) を必須とし、review PASS 後に remote main が前進していれば
   stale review evidence として ABORT する。変更 candidate の場合だけ、この時点で初めて
   [pre-flight and recovery](references/preflight-and-recovery.md) を読み、手順1で導出した
   `tracking_issue_has_pending_tasks` を含む観測 JSON を release-plan へ渡す。未公開 path は
   remote main == meta.base、公開済み残処理 path は remote main == meta.candidate を必須にする。

## Publish

- N/A (`meta.base == meta.candidate`): 上記の先行分岐で、独立 review PASS かつ remote main ==
  meta.base (== meta.candidate) を確認した後だけ完了 bookkeeping（後述）を実行する。
  remote main が前進していれば stale review evidence として ABORT する。starter tag / Release は
  作らない。
- ref push path: candidate SHA と `kaji-vX.Y.Z` または `kaji-vX.Y.Z-rN` を提示し、workflow 外で
  **人間の明示承認**を得てから annotated tag と main を `git push --atomic` する。
- push 後は [Release notes template](templates/release-notes.md) から `gh release create` を実行し、
  続けて完了 bookkeeping を行う。
- route 2 の部分成功再実行は release-plan の `remaining_actions` のうち `create_release` だけを
  実行する（不足していれば `gh release create` を再試行。新 tag、ref push、再承認は不要）。
  `update_state_table` / `close_tracking_issue` / `promote_next_task` は状態表更新・Issue
  close が未完了であることを示す参考情報に留め、直接実行しない。実行は必ず下記の完了
  bookkeeping（`kaji starter task-plan`）経由で行う（判定の二重化防止。詳細は
  [pre-flight and recovery](references/preflight-and-recovery.md)）。

### 完了 bookkeeping（`kaji starter task-plan` の `completion`）

kaji Release 状態表更新と tracking Issue close は `kaji starter task-plan` に `completion` を
渡して判定する。`completion.batch` は `active_target` を含む batch id、`completed_targets` は
`covered_targets` と**完全一致**する集合（部分集合・上位集合は ABORT）、`published_tag` は
ref push path なら公開した tag 名、N/A path なら `null` にする。

```bash
kaji starter task-plan <<EOF
{
  "issue_id": <tracking_issue_id>,
  "issue_state": "open",
  "body": "<tracking Issue の現在の本文>",
  "completion": {
    "batch": "<active batch id>",
    "completed_targets": <covered_targets>,
    "published_tag": "kaji-vX.Y.Z" 
  }
}
EOF
```

返る `TaskPlan` を次のとおり適用する:

1. `state_table_updates` の各行（`kaji_release` / `status` / `starter_release` / `reason`）を
   kaji Release の repository 別状態表へ適用する（`active_target` の行が `PASS`、束ねた他行が
   理由付き `N/A`。N/A path は全行が理由付き `N/A`）。
2. `next_body` が非 `null` なら、それを tracking Issue 本文へ反映する（対象 batch 行を `done` にする）。
3. `close_allowed` が真の場合に限り tracking Issue を close する。偽の場合（他 batch や `open` の
   後続タスクが残っている）は close せず、Issue は開いたまま維持する。次回の `/update-starter` が
   `promote_next_task` として残りの `open` 行から新しい batch を開始する。
4. 状態表更新・本文更新・close のいずれかが部分失敗した場合、同じ `completion` で
   `kaji starter task-plan` を再実行する（`route: 5` の冪等再適用。`state_table_updates` は
   初回と同一の値になるため、再適用しても値が変わらない）。

## Guardrails

force push、tag 上書き、lightweight tag、review 前 publish を禁止する。GitHub Release 作成の再試行で
`kaji-vX.Y.Z-rN` を増やさない。starter の失敗を理由に公開済み kaji tag / Release / PyPI を rollback
しない。観測矛盾は ABORT し、人間へ値を提示する。

## Verdict

`PASS | ABORT`。報告コメントに `--verdict-step release-starter --verdict-status <STATUS>` を付け、
コメント末尾と stdout に共通 verdict block を出す。注入時は全外部副作用の後、最後に
`verdict_path` へ pure YAML を保存する。ABORT は復旧 suggestion を必須とする。
