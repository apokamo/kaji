# series の監視（`kaji watch`）

[English](watch.md)（英語正本）

`kaji watch` は series に投入した Issue を LLM なしで見張る。毎周、事実から各 Issue の `state:*` ラベルを
計算し直し、GitHub との差分だけを直す。run の停止を検知すると、報告コメントを投稿し、外部へ通知し、
判断用の skill `/watch-triage` を Herdr の新しいタブで起動する。

**opt-in** の機能で、tracked の `.kaji/config.toml` に `[watch]` を宣言した repo でだけ動く。宣言しない repo では
`issue-close` を含む既存の挙動は変わらない。run と series の事実は [`kaji status --json`](run-status.ja.md) と
同じ経路（`kaji_harness.runstate.query`）だけから取得し、kaji の内部ファイルを直接読まない。

## 設定

```toml
[watch]                                         # table があること自体が opt-in の宣言（空でもよい）
notify_command = ["scripts/notify-discord.sh"]  # 任意の argv。未設定なら通知しない
```

- 読むのは tracked の `.kaji/config.toml` だけ。`config.local.toml` の `[watch]` は有効にも無効にもせず、警告して無視する。
- `provider.type` は `github` であること。
- 契約の 6 ラベル（`state:planned` / `state:running` / `state:aborted` / `state:done` / `state:hold` /
  `state:needs-maintainer`）が GitHub にあること。kaji はラベルを作らない（`.github/labels.yml` から同期する）。
- 周期・しきい値は定数（`kaji_harness/watch/constants.py`）: 180 秒ごと、`working` 以外が 2 周で `pane_idle`、
  進み具合が 90 分変わらなければ `log_stall`、対象 0 件が 2 周続けば終了。

## コマンド

```bash
kaji watch start                 # `kaji-watch` タブで監視の loop を起動（冪等）
kaji watch status [--json]       # 監視対象の Issue を表示（読むだけ）
kaji watch once [--dry-run]      # 1 周分を実行。--dry-run は計算結果だけを出し何も書かない
kaji watch record --event FILE --outcome OUTCOME [--comment-url URL] [--summary TEXT]
                 [--reason TEXT] [--until ISO8601] [--path ABS ...]
kaji watch retry --event FILE --reason TEXT   # 環境が原因の失敗を 1 回だけやり直す
kaji watch events [--json]       # event と判断の記録（読むだけ）
kaji watch recoveries [--issue N] [--json]    # 自動復旧の一覧（読むだけ）
```

すべて `--workdir DIR` を受け付ける。`stop` はない。`start` と `once`（`--dry-run` なし）と `retry` は Herdr の中
（`HERDR_ENV=1` と `HERDR_PANE_ID`）でだけ動く。`OUTCOME` は `handoff` / `still-working`（`pane_idle` 専用）/
`not-stalled`（`log_stall` 専用）/ `recovered` と、下の自動復旧の outcome（`retry-sent` は `kaji watch retry` だけが書く）。

## 自動復旧（Issue #545）

`/watch-triage` は次の 5 種類だけを保守者の承認なしで戻す。各操作の前に `kaji watch record` /
`kaji watch retry` を呼び、exit 0 のときだけ操作する。上限は `records.jsonl` の `flock` の下で行から数える。

| outcome | 停止 | 操作 | 上限の単位 | 上限 |
|---|---|---|---|---|
| `continue-sent` | 一時エラーで止まった `pane_idle` | `continue` を送る | issue・run・step・attempt | 3 |
| `wait` | usage limit（予定時刻あり） | 予定時刻まで待つ（`--until` は時刻帯付き・未来・7 日以内） | issue・run | 3 |
| `dialog-answered` | repo の main checkout・worktree の中のパスのダイアログ | 許可で答える（`--path` は絶対パスで checkout の内側） | issue・run・step・attempt | 3 |
| `question-answered` | 分類 1・2 の質問 | 答える | issue・run・step・attempt | 3 |
| `retry-sent` | 環境が原因の `aborted` | `kaji watch retry`（`.kaji/wf/` の `name:` 完全一致 1 個に解決し、`kaji-retry-<issue>` タブで `kaji recover <yaml> <issue> --retry-failed-step --run-id <run_id>`） | issue・anchor | 1 |

- `wait` の予定時刻の前は、その Issue・run の `pane_idle` / `log_stall` を出さず、新しい `aborted` は報告して起動だけを
  遅らせる。過ぎたら `waited_until` 付きの `<key>:w<n>` を出す（`aborted` の `:w<n>` は報告・通知を出し直さない）。
- `pane_idle` の `wait` は、同じ step の pane（run・step・attempt・episode）が実行中の間だけ効く。新しい run、別の step・
  attempt への進行、予定時刻の前の `working` への戻りで破棄する。run の終了・Issue の close・pane の消失では
  `pane_idle` の `:w<n>` を出さない。run が `ERROR` で終わった場合は `aborted` を検知し、その起動だけを予定時刻まで遅らせる。
- 出し直した `pane_idle` の `<key>:w<n>` に `still-working` を記録した場合も、通常どおり snooze の後に同じ episode を
  `<key>:w<n>:r<m>` で再確認する。系列のどの key（`:r<m>` / `:w<n>:r<m>`）に記録した `wait` も、次の `<key>:w<n>` として
  1 件だけ出し直す。
- 周の間に記録された `recovered` は `working` の観測と同じに扱い（1 件につき 1 回）、同じ attempt の再停止も 2 周で再検知する。
- 1.〜4. で戻った場合は通知しない。`retry` と `handoff` は通知する。series の member（event の `anchor` が
  `member:<run_id>`）の `retry` の通知の summary には、残りの member は止まったままで、やり直しが close まで進んだ後に
  保守者が `/run-watched` で続きを起動する（`run-series --resume` は使わない）旨を足す。
- event ファイルには任意の `anchor`（`member:<run_id>` / `baseline:<run_id>` / `none`）と `waited_until` が入る。
- 通知の payload と `events --json` は schema v2（`kind` に `retry`、`records[].outcome` に 5 値と任意の項目を追加）。
  `schema_version == 1` を確かめている通知スクリプトは 2 も受け付けるように直す。`status --json` は v1 のまま、
  `recoveries --json` は v1。詳細は英語正本の Schema history。

| 終了コード | 意味 |
|------|---------|
| 0 | 正常（2 周続けて対象が 0 件になって終えた場合、動作中の `start` を含む） |
| 2 | 引数の誤り・config がない・`[watch]` がない・ラベルがない・github 以外の provider・Herdr の外での `start` / `once` / `retry`・lock 保持中の `once`・不正な event ファイルや outcome・outcome と event の kind の不一致・`--reason` / `--until` / `--path` の不足や不正・`retry` の workflow を解決できない |
| 3 | 予期しないエラー（`retry` で記録の後にタブの起動に失敗した場合を含む。記録は残る） |
| 5 | 自動復旧の上限に達した。追記しない（`retry` はタブも起動しない）。stderr に単位と上限を 1 行出す |

## `/run-watched`

保守者の明示的な依頼（「#101, #102 を dev で直列に」）から series を作って起動し、監視させる入口。
skill は判断（workflow の選択・順番）だけを行い、決定的な処理は script が行う。

```bash
python -m kaji_harness.scripts.run_watched plan --issues 101,102 --workflow dev
python -m kaji_harness.scripts.run_watched execute --draft <draft-id>
```

- `plan`: Issue・`kaji status`・worktree・open の PR を読んで分類し、draft を
  `<artifacts>/_watch/run-watched/<draft-id>.json` に保存する。GitHub には書かない。series に入る Issue が
  0 件なら exit 2。
- `execute`: 前提の検証 → series YAML → `validate-series` / `run-series --dry-run` → YAML だけの commit と
  push（force なし）→ `state:planned` → `kaji-series-<id>` タブでの `kaji run-series` → `kaji watch start`。
  各副作用の直前に、Issue の open/closed と `plan` 時点の `state:*`・`wf:*` ラベルを再検査し、自分の
  `state:planned` 以外の変化があれば exit 2 で止まる。各段は外部の状態との照合で済んだかを判定するため、落ちた後も同じ draft で再実行すれば二重の副作用なしに
  収束する。exit 2 は前提違反・外部状態の相違、exit 3 は remote が先に進んだ等の人手の解決が要る状況。

`watch-triage-<issue>` タブが開いている間は triage を新たに起動せず、`pane_idle` / `log_stall` も出さない。
ただし新しい run の失敗は `aborted` の event として記録・報告し、そのタブが閉じた後に triage を起動する。

ラベルの契約・event の種類・通知の JSON（schema v1）・`issue-close`・`/run-watched` の動作の詳細は英語正本
[watch.md](watch.md) を参照する。
