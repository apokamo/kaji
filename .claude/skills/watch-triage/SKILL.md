---
name: watch-triage
description: kaji watch が検知した停止 event（aborted / pane_idle / log_stall）の原因を読み、自動復旧の一覧の 5 種類は承認なしで戻し、それ以外は Issue に報告して保守者に回す判断用 skill。kaji watch が Herdr の watch-triage-<issue> タブで自動起動する。一覧の外の復旧は保守者が承認したものだけを実行する。
---

# Watch Triage

`kaji watch` が検知した event を 1 件だけ扱う。原因を調べ、自動復旧の一覧（下）に当たれば上限の範囲で
承認なしで戻す。当たらない・上限に達した・直らない場合は Issue に報告し、`kaji watch record` で
保守者に回し、pane を開いたまま保守者の指示を待つ。CLI と JSON の正本は
[`docs/cli-guides/watch.md`](../../../docs/cli-guides/watch.md)。

## 入力

```
/watch-triage <event-file>
```

`<event-file>` は `<artifacts>/_watch/events/` の下の event ファイル（schema v1）。

## 絶対規則

1. **agent の質問には、[`critical-decision-checklist.md`](../_shared/critical-decision-checklist.md) の分類 1・2 だけ答える**。
   分類 2 は仮定・根拠・後段の検査先を Issue にコメントする（1 行目 `<!-- kaji-watch-triage: answer key=<event key> -->`）。
   **分類 3 と、分類に迷ったものは答えずに報告して保守者に回す**（one-way door を無人で埋めない）。
2. **状態の判定は event の `status_document` を正とする**。artifact の本文は原因の調査のために読むだけにする。
3. **パスを組み立てない**。run の artifact は event の `artifacts.run_dir` / `artifacts.attempt_dir` だけを使う。
   `artifacts` が null なら `kaji status --json <issue>` の再取得だけで報告し、artifact が見つからなかったことを書く。
4. 失敗した step のやり直しは、環境が原因の失敗に限り `kaji watch retry`（中で
   `kaji recover <workflow> <issue> --retry-failed-step` を新しいタブで起動する）を **1 回だけ承認なしで**使える。
   2 回目以降と、除外条件に当たるものは、保守者がこの pane で承認した後に
   `kaji recover <workflow> <issue> --retry-failed-step` を使う。
5. 下の禁止リストの操作は、保守者がこの pane で明示的に承認したとき（「この操作を実行してよい」という返答）だけ行う。
   `kaji-state` は kaji watch のサブコマンドを通さない書き換えを禁じるもので、`kaji watch record` / `kaji watch retry`
   の追記は対象外。
6. 自動復旧の各操作の前に、必ず `kaji watch record` / `kaji watch retry` を呼び、**exit 0 のときだけ操作する**。
   exit 5（上限）/ exit 2（検証に通らない）なら操作せずに報告（手順 3）へ進む。

<!-- watch-triage-forbidden:start -->
| ID | 禁止操作 |
|---|---|
| merge | PR の merge |
| force-push | force push |
| rebase | rebase |
| close | Issue / PR の close |
| labels | ラベルの付与・削除 |
| issue-body | Issue 本文の編集 |
| kaji-state | kaji の state（run の artifact・series の state・watch の状態ファイル）の削除・書き換え |
| series-yaml | series YAML の作成・変更 |
| code | コードの修正・commit |
| model-switch | モデル・agent の切り替え |
<!-- watch-triage-forbidden:end -->

## 自動復旧の一覧

承認なしで戻してよいのはこの 5 種類だけ（Issue #545 D1。上限は `kaji watch` が `records.jsonl` の lock の下で数える）。

<!-- watch-triage-auto-recovery:start -->
| ID | 停止 | 操作 | 上限の単位 | 上限 |
|---|---|---|---|---|
| continue-sent | 一時エラー（`at capacity`・rate limit・`overloaded`・5xx・接続の一時的な失敗）で止まった `pane_idle` | `continue` を送り、`working` に戻るかを待つ | issue・run・step・attempt | 3 |
| wait | usage limit（画面に再開の予定時刻がある。なければ continue-sent として扱う） | 予定時刻まで待つ（記録して自分のタブを閉じる） | issue・run | 3 |
| dialog-answered | repo の main checkout・worktree の中のパスへの信頼・取り込みのダイアログ | 許可で答える | issue・run・step・attempt | 3 |
| question-answered | step の agent の質問のうち `critical-decision-checklist.md` の分類 1・2 | 答える（分類 2 は仮定・根拠・後段の検査先を Issue にコメント） | issue・run・step・attempt | 3 |
| retry-sent | 環境が原因の member の失敗（`aborted`） | 失敗した step から 1 回やり直す（`kaji recover <workflow> <issue> --retry-failed-step`） | issue・anchor | 1 |
<!-- watch-triage-auto-recovery:end -->

除外条件（当たれば自動で戻さず、報告して保守者に回す）:

- dialog-answered: CLI の更新・モデルの切り替え・repo の外のパス・認証・ログインのダイアログ
- question-answered: 分類 3 と、分類に迷ったもの
- retry-sent: 正規の ABORT・cycle の上限・worktree / branch の不整合・認証 / 権限の形跡・中断、再開禁止の skill
  （`issue-start` / `i-pr` / `issue-close`）、古い agent / pane の残存。判断を誤っても `kaji recover` の安全ゲートが拒否する

## 手順

### 1. 読む

- event ファイル（`kind` / `issue` / `run_id` / `workflow` / `step` / `attempt` / `failed_step` / `failure` / `pane` /
  `artifacts` / `status_document` / `anchor` / `waited_until`）
- Issue 本文（特に `## 決定事項`）: `kaji issue view <issue> --json body -q .body`
- 最新の事実: `kaji status --json <issue>`
- `artifacts.attempt_dir` の下の `prompt.txt` とログ、`artifacts.run_dir` の下の `run.log`（あるものだけ）
- `kind` が `pane_idle` / `log_stall` のとき: `herdr pane read <pane.pane_id> --source recent --lines 200`
- これまでの自動復旧: `kaji watch recoveries --issue <issue>`

### 2. 原因を分類し、一次判断（自動復旧）を行う

`failure.cause` / `failure.recoverability_hint`、ログの末尾、pane の表示から、次のどれかに分類する:
一過性（usage limit・ネットワーク等）/ agent の質問待ち / ダイアログ / step の不具合 / 設計・要件の問題 / kaji 本体の欠陥 / 不明。

`pane_idle` で agent が作業中だった場合は `still-working`、`log_stall` で進んでいた場合は `not-stalled` を記録して
（手順 4 の `--outcome` を替える）、自分のタブを閉じて終了する。

自動復旧の一覧の 5 種類のどれに当たるかを判定し、当たれば次を行う。当たらなければ手順 3 へ進む。

- **continue-sent**（一時エラー）:

  ```bash
  kaji watch record --event <event-file> --outcome continue-sent --reason "<画面のエラー行>"
  herdr pane send-text <pane.pane_id> continue && herdr pane send-keys <pane.pane_id> Enter
  herdr agent wait <pane.pane_id> --until working --timeout <ms>
  ```

  待ちは 1 回目の後 180000 ms、2・3 回目の後 360000 ms。`working` に戻らなければ、上限の範囲でもう一度
  `record` から繰り返す。3 回とも timeout、または exit 5 なら手順 3 へ進む。
- **dialog-answered**: `kaji watch record --event <event-file> --outcome dialog-answered --reason "<ダイアログ>" --path <絶対パス>`
  の exit 0 の後に、許可で答える。
- **question-answered**: `kaji watch record --event <event-file> --outcome question-answered --reason "<質問と分類>"` の
  exit 0 の後に答える。分類 2 は答える前に answer コメント（絶対規則 1）を投稿する。
- **wait / usage limit**: 下の「usage limit の予定時刻の前後」の表に従う。
- **retry-sent**（環境が原因の `aborted`）: 下の「やり直しと起動の確認」に従う。

continue-sent・dialog-answered・question-answered で戻った（`working` を確認した）ら、記録して通知せずに自分のタブを閉じる:

```bash
kaji watch record --event <event-file> --outcome recovered --summary "<行った自動復旧>"
herdr tab close <自分の tab_id>
```

#### usage limit の予定時刻の前後

「新しい予定時刻」は画面・ログに出ている再開の予定時刻のうち、今より後のもの。予定時刻の読み取り（時刻帯の変換を含む）は
この skill が行い、`--until` の検証（時刻帯付き・未来・7 日以内）は CLI が行う。

| event | 状況 | 行うこと |
|---|---|---|
| `pane_idle`（`waited_until` なし） | usage limit で、新しい予定時刻がある | `record --outcome wait --until <予定時刻>` → 自分のタブを閉じる |
| `pane_idle`（`waited_until` なし） | usage limit だが予定時刻が出ていない | 一時エラーとして `continue-sent` の手順 |
| `pane_idle`（`waited_until` あり） | step の pane が `working` | `still-working` を記録して自分のタブを閉じる |
| `pane_idle`（`waited_until` あり） | `working` でなく、新しい予定時刻がない（古い reset の表示だけ） | 一時エラーと同じ `continue-sent` の手順。**古い予定時刻を `wait` に渡さない**（`--until` が過去で exit 2 になり、誤って handoff するため） |
| `pane_idle`（`waited_until` あり） | `working` でなく、`waited_until` より後の新しい予定時刻がある（制限が再び掛かった） | もう一度 `wait`（同じ Issue・run で 3 回まで。exit 5 なら handoff） |
| `aborted`（`waited_until` なし） | 原因が usage limit で、新しい予定時刻がある | **`retry` より先に** `record --outcome wait --until <予定時刻>` → 自分のタブを閉じる（予定時刻の前にやり直すと同じ制限で止まり、1 回の上限を無駄にするため） |
| `aborted`（`waited_until` なし） | 原因が usage limit だが予定時刻がない、または他の環境の原因 | `retry` の手順 |
| `aborted`（`waited_until` あり） | 新しい予定時刻がない | `retry` の手順 |
| `aborted`（`waited_until` あり） | `waited_until` より後の新しい予定時刻がある | もう一度 `wait`（3 回まで。exit 5 なら handoff） |

`wait` を記録したら自分のタブを閉じる。`kaji watch` は予定時刻まで同じ Issue・run の triage を起動せず、
過ぎたら `waited_until` 付きの event で起動し直す。

#### やり直しと起動の確認

```bash
kaji watch retry --event <event-file> --reason "<環境が原因と判断した根拠>"
```

exit 0 なら、出力の `pane_id` が `kaji recover ... --retry-failed-step` を動かしている新しいタブ `kaji-retry-<issue>`。
10 秒ごと・最大 120 秒、`kaji status --json <issue>` を読み、**自分の子**（`latest_run.run_id` が event の `run_id` と違い、
かつ `latest_run.recovery.parent_run_id` が event の `run_id` と一致する run）で判定する。各観測で、表の上から最初に
当たった行で決める。

| 観測 | 判定 | 行うこと |
|---|---|---|
| 自分の子があり、`termination` が `running`（または生死をまだ判定できない `unknown` / null） | 起動した（`running`） | retry のコメント → `kaji watch start` → 自分のタブを閉じる |
| 自分の子があり、`COMPLETE` で終わっている（最初の観測より前に終わった場合を含む） | 起動して完走した（`completed`） | 同上（コメントに子の結果 `COMPLETE` を書く） |
| 自分の子があり、`ERROR` / `ABORT` で終わっている、または `vanished` | 起動したが失敗した（`failed`） | 同上（コメントに子の結果を書く）。子の失敗は監視が新しい `aborted` event として検知し、同じ anchor の上限 1 に達しているので次の triage は handoff する |
| 120 秒たっても自分の子がない | 起動していない（`not_started`。安全ゲートの拒否・起動の失敗） | `herdr pane read <retry の pane_id>` で `retry-gate:` 行と終了の表示を読み、報告して `handoff` する |
| `latest_run` が対象とも自分の子とも違う run | 無関係の run が割り込んだ（`unrelated`） | 判断せずに報告して `handoff` する |

retry のコメントは 1 行目を `<!-- kaji-watch-triage: retry key=<event key> -->` にし、原因・根拠・やり直した step・
`1/1`・新しいタブの pane を書く。series の member なら「series の残りの member は止まったまま。やり直しが close まで
進んだ後に、保守者が `/run-watched` で続きを起動する（`run-series --resume` は失敗した member を最初の step から
やり直すので使わない）」をコメントに書く。exit 5 / 2 なら操作せずに手順 3 へ進む。

### 3. 報告コメント

1 行目を `<!-- kaji-watch-triage: key=<event key> -->` にしたコメントを投稿し、URL を控える。
本文には、分類・根拠（ログの該当行）・推奨する復旧（コマンドを含む）・agent の質問があればその内容と 3 分類の見立て・
自動復旧の記録と結果（`kaji watch recoveries --issue <issue>` の行。上限に達した場合はその旨）を書く。

```bash
kaji issue comment <issue> --body-file "[skill_tmp_dir]/watch-triage-report.md"
```

kaji 本体の欠陥と判断した場合は、incident Issue の下書きを `[skill_tmp_dir]/incident-draft.md` に作るだけで、起票しない。

### 4. 保守者に回す

```bash
kaji watch record --event <event-file> --outcome handoff --comment-url <url> --summary "<1 行の要約>"
```

通知はこの record が送る。pane を開いたまま、保守者の指示を待つ。

### 5. 承認された復旧だけを実行する

保守者が承認した場合だけ、次を実行する。

```bash
kaji recover <workflow> <issue> --retry-failed-step
kaji watch record --event <event-file> --outcome recovered --summary "<実行した復旧>"
kaji watch start
```

`kaji recover` が exit 2 / 3 / 4 で拒否した場合は、その `retry-gate:` 行を保守者に示して指示を待つ。
