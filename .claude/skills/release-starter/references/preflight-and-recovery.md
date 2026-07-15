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
行わず、(a) Release 作成、(b) 状態表 PASS、(c) Issue close の不足 suffix だけを順に実行する。

## Failure recovery

- atomic push reject: remote は不変。force push せず再同期し、candidate が変われば review をやり直す。
- push 成功後の Release failure: tag を消さず同じ tag で Release 作成だけ再試行する。
- Release 成功後の状態表 / close failure: `PENDING` と具体的復旧手順を報告し、route 2 で再実行する。
- 全完了済み: 外部変更なしの idempotent PASS。

kaji 本体 release は独立トランザクションであり、starter 部分成功から rollback しない。
