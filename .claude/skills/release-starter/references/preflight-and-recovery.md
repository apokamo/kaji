# Starter release pre-flight and recovery

この reference は最新 review verdict を構造化解決した後に読む。

## Observation input

N/A (`meta.base == meta.candidate`) は本 helper を呼ぶ前に分岐する。変更 candidate だけが
`kaji starter release-plan` の stdin へ target、candidate、対象 version の tag 名 / SHA / annotated、
GitHub Release tag 名、kaji Release 状態表行、tracking Issue state を JSON で渡す。出力 route:

1. tag なし: `kaji-vX.Y.Z` を新規公開
2. latest tag SHA == candidate: 同じ tag を再利用し不足 bookkeeping のみ実行
3. latest SHA != candidate: `kaji-vX.Y.Z-r(maxN+1)` を新規公開
4. 旧 revision SHA == candidate: ABORT
5. tag / Release / annotated / 状態表の観測矛盾: ABORT

route 1 / 3 は人間承認後に `git push --atomic <remote> main <tag>`。route 2 は新 tag と ref push を
行わず、(a) Release 作成、(b) 完了 bookkeeping（後述）の不足 suffix だけを順に実行する。

## 完了 bookkeeping（`kaji starter task-plan` の `completion`）

kaji Release の repository 別状態表更新と tracking Issue close は、`release-plan` ではなく
`kaji starter task-plan` に `completion` を渡して判定する（Issue #423）。`completion.batch` は
今回の `active_target` を含む batch id、`completed_targets` は `covered_targets`（同じ batch の
target 集合）と**完全一致**する集合（部分集合・上位集合・batch 外 target は route 6 の ABORT）、
`published_tag` は ref push path なら公開した tag 名、N/A path（`meta.base == meta.candidate`）
なら `null` にする。

返る `TaskPlan` の適用順:

1. `state_table_updates` の各行を kaji Release の repository 別状態表へ適用する
   （`active_target` の行が `PASS` + `starter_release`、束ねた他行が理由付き `N/A`。N/A path は
   batch の全行が理由付き `N/A`）。
2. `next_body` が非 `null` なら tracking Issue 本文へ反映する（対象 batch 行を `done` にする）。
3. `close_allowed` が真のときだけ tracking Issue を close する。偽なら close せず維持する
   （他 batch や後続 `open` task が残っている。次回の `/update-starter` が `promote_next_task` と
   して新しい batch を開始する）。

## Failure recovery

- atomic push reject: remote は不変。force push せず再同期し、candidate が変われば review をやり直す。
- push 成功後の Release failure: tag を消さず同じ tag で Release 作成だけ再試行する。
- Release 成功後の状態表 / 本文更新 / close failure: 同じ `completion` で `kaji starter task-plan`
  を再実行する（`route: 5` の冪等再適用。`state_table_updates` は初回と同一の値になるため、
  何度再試行しても値が変わらない）。route 2 の部分成功再実行として扱う。
- 全完了済み: 外部変更なしの idempotent PASS。

kaji 本体 release は独立トランザクションであり、starter 部分成功から rollback しない。
