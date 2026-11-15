# [設計] series の監視（`kaji watch`）・判断用の skill・起動の入口・`state:*` / `wf:*` のラベル契約

Issue: #543

## 概要

series に投入した Issue を LLM なしで見張る `kaji watch` を追加する。`kaji watch` は毎周、事実から `state:*` ラベルを計算し直して GitHub との差分だけを直す。停止を検知すると、判断用の skill `/watch-triage` を Herdr の新しいタブで起動し、外部へ通知する。あわせて、保守者が Issue 番号と workflow を指定するだけで、検証済みの series を監視付きで起動できる `/run-watched` を加える。これらはすべて、`.kaji/config.toml` に `[watch]` を宣言した repo でだけ動く opt-in の機能とする。

## 背景・目的

詳細は Issue #543 の「現状の問題」を正とし、ここには要点だけを書く。

- `kaji run-series` の member が止まったり失敗したりしても、kaji からは誰にも知らせない。原因の調査も保守者が手で行っている。
- Issue の実行段階を表すラベルがない。そのため、止まっている Issue と待っている Issue を一覧で区別できない。
- 利用側の repo（apokamo/kamo2）は同じ仕組みを kaji の外で運用している（`scripts/ops/lane_watch/`・`run_watched/`、合計約 5,600 行）。この実装は kaji の内部ファイルを直接読んでいるため、kaji を変更すると壊れうる（apokamo/kamo2#1909 / #1910）。

### ユースケース

- **UC1**（起動）: 保守者は「#101, #102 を dev で直列に流して」と依頼する。それだけで、`/run-watched` が series を生成・検証して commit し、`state:planned` を付けて、Herdr のタブで `kaji run-series` と `kaji watch` を起動する。承認を求めるのは、workflow を skill が選んだ場合・順番が曖昧な場合・除外した Issue がある場合だけとする。
- **UC2**（離席）: 保守者が席を離れている間に member が失敗した、または step が止まったとする。`kaji watch` がラベルを `state:aborted` に替え、Issue へのコメントと通知を送り、`/watch-triage` のタブを開く。保守者は通知を見て、そのタブで原因と復旧案を読み、承認する。
- **UC3**（一覧）: 保守者は GitHub の Issue 一覧を `label:state:running` / `state:aborted` / `state:needs-maintainer` で絞り込み、実行中・停止中・自分の対応待ちの Issue を確認する。
- **UC4**（収束）: 監視を何度起動し直しても、ラベルは事実と一致する状態に戻る。
- **UC5**（opt-in）: `[watch]` を宣言していない repo では、`issue-close` を含むすべての既存の挙動が変わらない。

### 代替案と採らない理由

| 代替案 | 採らない理由 |
|---|---|
| kamo2 の `lane_watch` をそのまま移植する | `/plan-run` のレーン・自動復旧（#545）・`wait`・anchor の時計の補正など、本 Issue の範囲外の機能が半分以上を占める。kaji は自前で事実の API（#540）を持つので、kamo2 がプロセス表や `_series/*/state.json` を直接読んでいる部分は不要になる |
| `kaji run-series` に通知と監視を組み込む | 監視の対象には series の外で起動する run（`kaji recover --retry-failed-step` の子など）も含まれる。また、ラベルの収束は run-series のプロセスが落ちた後にも必要になる。監視は独立したプロセスにする（保守者の決定「監視は CLI」） |
| 監視が GitHub のイベント（webhook）を受けて動く | kaji はローカルで動く CLI であり、受信の口を持たない。毎周の再計算なら、再起動で自然に収束する（UC4） |

## インターフェース

### 1. 設定 `[watch]`（`.kaji/config.toml`）

```toml
[watch]                                  # table があること自体が opt-in の宣言（空でもよい）
notify_command = ["scripts/notify-discord.sh"]   # 任意。argv の配列。未設定なら通知しない
```

| key | 型 | 既定 | 検証 |
|---|---|---|---|
| `[watch]` | table | なし（＝無効） | table 以外の値は `ConfigLoadError` |
| `notify_command` | `list[str]`（要素は 1 個以上、各要素は空でない文字列） | `None` | Pydantic で検証する。未知の key はエラーにする（`[incident]` と同じ `extra="forbid"`） |

- 読むのは tracked の `.kaji/config.toml` だけとする。`config.local.toml` の overlay に `[watch]` を書いても、有効にも無効にもならない（D1。`IncidentConfig` と同じ作法で `_parse_watch` は overlay を merge しない）。overlay に `[watch]` があれば警告を 1 行出して無視する。
- `KajiConfig.watch: WatchConfig | None` を追加する。`None` は未宣言を表す。
- 周期・しきい値は設定にせず、`kaji_harness/watch/constants.py` の定数にする（A4）。

| 定数 | 値 |
|---|---|
| `POLL_INTERVAL_SECONDS` | 180 |
| `PANE_IDLE_POLLS` | 2（`working` 以外が 2 周続いたら検知） |
| `LOG_STALL_SECONDS` | 5400（90 分） |
| `EMPTY_POLLS_TO_EXIT` | 2 |
| `STILL_WORKING_SNOOZE_SECONDS` | 1200（`still-working` を記録した後、再確認を控える時間） |

### 2. ラベル契約

`.github/labels.yml` に次のラベルを追加する（D2）。`wf:*` の 4 個（下記）と合わせて、管理対象は 29 → 39 個になる。

| ラベル | 意味 | 付ける主体 | 外す主体 |
|---|---|---|---|
| `state:planned` | series に投入済みで、まだ run が始まっていない | `/run-watched`（投入時）、`kaji watch`（事実から） | `kaji watch` |
| `state:running` | その Issue の `kaji run` が生きている | `kaji watch` | `kaji watch` |
| `state:aborted` | 投入後の最新の run が失敗した、または消えた。あるいは COMPLETE したのに Issue が open のまま | `kaji watch` | `kaji watch`、`issue-close`（close 時） |
| `state:done` | close 済み（completed） | `issue-close`、`kaji watch` | — |
| `state:hold` | 保留中。無人の workflow に渡さない | 人 | 人、`issue-close`（close 時） |
| `state:needs-maintainer` | 保守者の対応待ち（人の作業・完了後の確認） | 人、`issue-close`（follow-up の起票時） | 人、`issue-close`（その Issue の close 時） |

- **排他**: 1 Issue に付く `state:*` は高々 1 個とする。`kaji watch` は「付ける → 外す」の 2 段で付け替えるので、途中で落ちると一時的に 2 個付くことがある。この状態は次の周で直る。
- **close 時の排他（M1）**: `issue-close` の `mark-done` は、`state:done` を付けて**ほかのすべての `state:*`**（`planned` / `running` / `aborted` / `hold` / `needs-maintainer`）を外す（Issue 完了条件「state:done を付け（他の state:* は外す）」）。人用のラベルに触れないという規則は `kaji watch` の規則であり、`issue-close` には適用しない。close 済みの Issue に保留・対応待ちの意味は残らないので、排他を優先する。
- **人が付けてよいもの**: `state:hold` と `state:needs-maintainer` だけ。`kaji watch` はこの 2 つが付いた Issue に触れない。2 個以上の `state:*` が付いた Issue（人の操作によるもの）にも触れず、`status` の行に `conflict` と表示する。ただし、自分の `pending_labels` に記録した付け替えの途中で生じた 2 個は直す。
- **`wf:<name>`**: `<name>` には workflow YAML の `name:` を入れる。1 Issue に高々 1 個とする。付けるのは人（起票時の任意）だけで、kaji は書かない。読むのは `/run-watched` で、workflow を指定しなかったときの既定に使う。解決方法: `.kaji/wf/**/*.yaml` のうち `name:` が `<name>` と一致するものがちょうど 1 個ならそれを採る。0 個なら「解決不能」、2 個以上なら「曖昧」とし、どちらも除外の理由にする。
- kaji の repo で定義する `wf:*`: `wf:dev` / `wf:dev-thorough` / `wf:dev-small` / `wf:docs`。`incident` / `starter-sync` / `*-codex` は、Issue から指定する運用がないので定義しない。
- `state:ready` / `state:needs-verify` / `state:needs-grill` / `prio:*` は定義しない（D2）。

### 3. `kaji watch` CLI（D3・D4）

```
kaji watch start [--workdir DIR]
kaji watch status [--json] [--workdir DIR]
kaji watch once [--dry-run] [--workdir DIR]
kaji watch record --event FILE --outcome OUTCOME [--comment-url URL] [--summary TEXT] [--workdir DIR]
kaji watch events [--json] [--workdir DIR]
```

各サブコマンドの前提は次のとおり。

| サブコマンド | 前提の検証（違反は exit 2） | 動作 |
|---|---|---|
| 共通（5 つすべて） | `.kaji/config.toml` がある。tracked の `[watch]` が宣言されている。`provider.type == github`。契約の 6 ラベルが GitHub にある（`list_labels`）。D1 の「宣言があるのにラベルがなければ `kaji watch` はエラー」を、読むだけの `status` / `events` / `record` にも同じく適用する | — |
| Herdr の前提（`start`・`once`（`--dry-run` なし）・内部 loop） | `HERDR_ENV=1` かつ `HERDR_PANE_ID` が空でなく、`herdr` が解決できる（D4）。書き込みを行う入口はすべて、最初の副作用（lock 以外）より前にこれを検証する（M7） | — |
| `start` | 上記 | lock が取られていれば `watch already running (pane <id>)` を出して exit 0。そうでなければ、label `kaji-watch` のタブを探し、シェルに戻った pane があれば再利用し、なければ `herdr tab create --label kaji-watch --no-focus` で作る。その pane で内部の entrypoint `python -m kaji_harness.watch` を起動する |
| `status` | — | 読むだけ。対象の Issue ごとに、事実・今のラベル・目標・保留の理由・triage タブ・抑止の状態を表示する。GitHub と artifacts には書かない |
| `once` | 書き込みあり: Herdr の前提と、lock を取れること（取れなければ exit 2 `watch is running`） | 1 周分を実行する。`--dry-run` なら Herdr の前提は求めず、計算結果（目標・差分・event）を JSON で出し、何も書かない（GitHub・状態ファイル・Herdr・通知のいずれにも）。Herdr の外での `--dry-run` は pane の観測を行わず、出力の `herdr` を `"unavailable"` にし、`pane_idle` の候補を出さない |
| `record` | event ファイルが `_watch/events/` の下にあり、schema v1 として読める | 判断の結果を `records.jsonl` に追記する。`--outcome handoff` のときは、通知（kind `handoff`）も送る |
| `events` | — | event と record を時系列で一覧する（読むだけ） |

- `OUTCOME` の enum: `handoff`（保守者に回した）/ `still-working`（`pane_idle` だが作業中だった）/ `not-stalled`（`log_stall` だが進んでいた）/ `recovered`（保守者が承認した復旧を実行した）。`still-working` を `pane_idle` 以外の event に、`not-stalled` を `log_stall` 以外の event に使うと exit 2 にする。
- 終了コード: 0 = 正常（2 周続けて対象が 0 件になって終えた場合を含む）、2 = 引数の誤り・config がない・`[watch]` がない・ラベルがない・Herdr の外での `start` / `once`（`--dry-run` なし）、3 = 予期しないエラー（`exit_codes` の既存の定数）。
- `stop` は作らない（D3）。
- 監視の loop の本体は内部の entrypoint `python -m kaji_harness.watch` に置き、公開の CLI にはしない（D3 のサブコマンド一覧を増やさないため）。前提の検証は `kaji_harness/watch/preflight.py` の 1 関数（`check_watch_preconditions(config, *, require_herdr: bool) -> WatchContext`）に集約し、5 つのサブコマンド・内部 loop・`run_watched execute`・`watch_labels` が共有する（`watch_labels` は `[watch]` がない場合だけ skip で exit 0 にする点が異なる）。lock は `<artifacts>/_watch/lock` の `fcntl.flock` で取る（kaji の `series/lock.py` と同じ方式）。

#### `status --json` / `events --json`（schema v1。A6）

`schema_version` の規則は `docs/cli-guides/run-status.md` § Schema compatibility と同じにする（optional なフィールドの追加では上げない。削除・改名・型の変更・`null` の意味の変更・enum の追加と変更では上げる）。

```jsonc
// kaji watch status --json
{
  "schema_version": 1,
  "generated_at": "2026-11-15T03:00:00+00:00",
  "watcher": {"running": true, "pane_id": "p_12"},       // lock の保持者（pane_id は不明なら null）
  "issues": [
    {
      "issue": 101,
      "github_state": "open",                    // open | closed
      "current_label": "state:running",          // state:* が 0 個なら null、2 個以上なら "conflict"
      "target_label": "state:running",           // 目標。書き換えない Issue は null
      "hold_reason": null,                        // null | "unknown_liveness" | "user_label" | "conflict"
      "run_id": "261115113853",                  // latest_run.run_id（なければ null）
      "triage_tab": null,                         // 開いている triage タブの pane_id
      "suppressed_until_progress": false          // handoff 後の抑止中
    }
  ],
  "pending_labels": [101],
  "pending_reports": []
}
// kaji watch events --json
{
  "schema_version": 1,
  "events": [
    {
      "key": "101:aborted:261115113853",   // str
      "kind": "aborted",                   // pane_idle | aborted | log_stall
      "issue": 101,                        // int
      "run_id": "261115113853",            // str | null（run がない event はない想定だが、読めなければ null）
      "detected_at": "...",                // ISO 8601
      "launch_status": "launched",         // detected | launching | launched | launch_failed
      "launch_attempts": 1,                // int
      "triage_pane": "p_30",               // str | null（未起動・不明なら null）
      "event_file": "/abs/.kaji-artifacts/_watch/events/101-aborted-261115113853.json"
    }
  ],
  "records": [
    {
      "event_key": "101:aborted:261115113853",  // str
      "outcome": "handoff",                     // handoff | still-working | not-stalled | recovered
      "recorded_at": "...",                     // ISO 8601
      "comment_url": "https://…",               // str | null（--comment-url を省略したら null）
      "summary": "…",                           // str | null（--summary を省略したら null）
      "judge": "host:pid"                       // str（record を書いたプロセス）
    }
  ]
}
```

#### event ファイル（`<artifacts>/_watch/events/<event-key のファイル名化>.json`。A5: 内部の形式だが、triage skill の入力にはなる）

```jsonc
{
  "schema_version": 1,
  "key": "101:aborted:261115113853",
  "kind": "aborted",               // pane_idle | aborted | log_stall
  "issue": 101,
  "detected_at": "...",
  "run_id": "261115113853",
  "workflow": "dev",
  "step": "implement", "attempt": 1,   // latest_run.last_step（なければ null）
  "failed_step": "implement",          // latest_run.failed_step
  "failure": {"cause": "...", "source": "...", "recoverability_hint": "..."},  // なければ null
  "pane": {"pane_id": "p_9", "origin_pane": "p_1"},   // step の pane（headless なら null）
  "artifacts": {                       // runstate.query.locate_step_artifacts の結果（M2）。見つからなければ各値 null
    "run_dir": "/abs/.kaji-artifacts/101/runs/261115113853",
    "attempt_dir": "/abs/.kaji-artifacts/101/runs/261115113853/steps/implement/attempt-001"
  },
  "status_document": { ... }           // 検知時点の `kaji status --json <issue>` と同じ文書（#540 schema v1。runstate.query.collect_status の戻り値）
}
```

#### 通知の JSON（`notify_command` の標準入力。schema v1。D5・A6）

```jsonc
{
  "schema_version": 1,
  "kind": "aborted",            // aborted（watch がラベルを aborted に替えた）| handoff（triage が保守者に回した）| watch_exit（watch が終了した）
  "repo": "apokamo/kaji",       // provider の repo（なければ null）
  "issue": 101,                 // watch_exit では null
  "summary": "run 261115113853 ended ERROR at step implement",
  "comment_url": "https://github.com/apokamo/kaji/issues/101#issuecomment-…",   // なければ null
  "pane_id": "p_9",             // 判断用のタブの pane（なければ null）
  "event_key": "101:aborted:261115113853",  // なければ null
  "generated_at": "..."
}
```

- 呼び出し: `subprocess.run(notify_command, input=json, timeout=30, cwd=repo_root)`。argv には `notify_command` 以外を足さない（秘密を argv に載せない）。
- 失敗（exit が 0 でない・timeout・`OSError`）は警告を 1 行出して続行する。ログには argv[0] と exit code だけを出し、stderr の本文は出さない（秘密を含みうるため）。再送はしない。
- `notify_command` が未設定なら呼ばない（`status` に `notify: disabled` と表示する）。

### 4. `issue-close` のラベル操作（D1・A8）

skill からは、決定的な script を呼ぶだけにする。

```
python -m kaji_harness.scripts.watch_labels mark-done <issue>
python -m kaji_harness.scripts.watch_labels mark-follow-up <issue>
```

| 状況 | 動作 | exit |
|---|---|---|
| `[watch]` がない（tracked の config） | 何もせず、`watch labels: skipped (no [watch])` を出す | 0 |
| `[watch]` があり、契約の 6 ラベルのどれかが GitHub にない | 不足しているラベル名を stderr に出す | 2 |
| `mark-done` | `state:done` を付け、ほかの `state:*`（`planned` / `running` / `aborted` / `hold` / `needs-maintainer`）をすべて外す（M1。結果として `state:*` は `done` の 1 個だけになる） | 0 |
| `mark-follow-up` | `state:needs-maintainer` を付ける | 0 |
| GitHub の書き込みが失敗した | — | 3 |

`issue-close` は、close の直後と follow-up の起票直後にこれを呼ぶ。exit 2 / 3 のときは ABORT する（close は済んでいるので、再実行で `mark-done` だけをやり直せることを報告に書く）。

### 5. `/run-watched` と決定的な処理（A2・A3）

```
python -m kaji_harness.scripts.run_watched plan --issues 101,102 [--workflow dev | --member 101=dev --member 102=docs] [--chosen-by-skill] [--order-ambiguous]
python -m kaji_harness.scripts.run_watched execute --draft <draft-id>
```

- `plan`: 次の順に処理する。読み取り・分類・承認の要否の計算・draft の保存（`<artifacts>/_watch/run-watched/<draft-id>.json`）を行い、表（JSON）を標準出力に出す。GitHub には書かない。
  - workflow の解決: 引数（stem・ファイル名・パス）→ Issue の `wf:*` ラベル → どちらもなければ `needs_selection`（skill が選び、`--chosen-by-skill` を付けて `plan` をやり直す）。stem は `.kaji/wf/**/<stem>.yaml` のうちちょうど 1 個に一致するものに解決する。
  - 除外（理由付き）: closed、`state:planned` / `running` / `aborted` / `done` / `hold` / `needs-maintainer`、`state:*` が 2 個以上、指定した workflow と `wf:*` が食い違う、`wf:*` が解決できない、または曖昧、`kaji status` で `any_run_alive` / `launching` が `true` または `null`、worktree（`kaji status` の `latest_run.worktree_dir` が存在する）、または open の PR（`head` = Issue の branch）が残っている。
  - 承認が要る条件: `--chosen-by-skill`、`--order-ambiguous`、除外が 1 件以上ある。どれにも当たらなければ `approval_required: false`。series に入る Issue が 0 件なら exit 2 にする。
- `execute`: draft の各段を順に実行する。各段は**外部の結果との照合**で済んだかどうかを判定し、draft の完了フラグだけには依存しない（M3）。各段の receipt（識別子と期待状態）を、副作用の直前（intent）と直後（result）に draft へ保存する。
  0. **前提の検証（M7・M8。最初の副作用より前）**: `check_watch_preconditions(require_herdr=True)`（tracked の `[watch]`・6 ラベル・github provider・Herdr）。git の前提は次のとおり。cwd が main checkout で、現在の branch が `default_branch`。index と作業ツリーに、series YAML 以外の staged 変更がない。`git fetch <git_remote> <default_branch>` の後、`HEAD == <git_remote>/<default_branch>` であること（ahead も behind もない）。ただし、自分の receipt の commit が HEAD である再実行の場合は、`HEAD^ == receipt.parent` を条件とする。満たさなければ exit 2 で、何も書かない。
  1. **状態の再検査（毎回、各副作用の直前）**: 各 Issue の state・labels を読み直し、**期待状態**と比べる。期待状態は「draft の snapshot に、自分の receipt にある変更（段 5 で付けた `state:planned`）を足したもの」とする。自分の変更以外の違い（close・hold の付与・ほかの `state:*`・別の run の開始など）があれば、そこで止まり（exit 2）、以降の副作用を行わない。済んだ段は報告に書く。この検査は段 2・4・5・6 の直前に毎回行い、再実行でも飛ばさない。
  2. **series YAML**: 識別子は draft に固定した `series_id` とパスで、期待状態は生成した内容の sha256。ファイルがなければ、`kaji_harness.series.generator` で生成し、sha256 を receipt に保存する。ファイルがあり、sha256 が receipt（または draft から再生成した内容）と一致する場合は、済んだものとして扱う。一致しなければ exit 2（他者のファイルを上書きしない）。series-id は `rw-<最初の issue>-<最後の issue>-<YYMMDDHHMM>`（`^[a-z0-9][a-z0-9-]{0,63}$` に収まる）で、`plan` の時点で draft に固定する。
  3. **検証**: `kaji validate-series <yaml>` と `kaji run-series <yaml> --dry-run` を毎回実行する（冪等で副作用がない）。どちらかが失敗したら exit 2（ラベル・タブは変えない）。
  4. **commit / push（D7・M8）**:
     - commit: receipt に `commit` がなく、HEAD が `<git_remote>/<default_branch>` と一致している場合だけ、次を行う。`git add -- <yaml>` → `git diff --cached --name-only` が `<yaml>` の 1 行だけであることを確かめる → `git commit -m "chore(series): add <id> for #101, #102"`。その後、`{sha, parent}` を保存する。parent は fetch した remote の sha。
     - push: `git push <git_remote> <sha>:refs/heads/<default_branch>`（force なし）。送る履歴は `parent..sha` の 1 commit だけになる。段 0 で、HEAD が remote と一致すること（無関係な ahead commit がないこと）を確かめているためである。
     - 再実行時の照合: fetch の後、`git merge-base --is-ancestor <sha> <remote>/<default_branch>` が真なら push は済んでいる。偽で、remote が `parent` のままで、HEAD が `sha` なら push だけをやり直す。remote が先に進んでいれば exit 3 で止まる（rebase・force push はしない。保守者に、ローカルの commit を `git reset --soft <parent>` で戻すか、手で解決するかを報告する）。
  5. **`state:planned` の付与**: Issue ごとに intent を保存し、`provider.edit_issue(add_labels=["state:planned"])` を実行して result を保存する。再実行時は、`view_issue` で `state:planned` が付いていれば済んだものとする。
  6. **series のタブと起動**: 識別子は label `kaji-series-<id>`。
     - 照合: `runstate.query.list_series()` で `<id>` の series state を確かめる。`status` が null でない（run-series が state を書いた）か、member に `run_id` / `child_pid` があれば、起動は済んでいるので段 7 へ進む。そうでなければ、label `kaji-series-<id>` のタブを探す。pane の前景に `kaji run-series` がいれば済んだものとし、シェルだけなら、その pane で起動する。タブがなければ `herdr tab create --label kaji-series-<id> --no-focus` で作り、tab_id・pane_id を保存してから起動する。
     - 起動は `herdr pane run <pane> "kaji run-series <yaml>"` で、実行後に `series_pane` を保存する。run-series 自体も `series/lock.py` の lock で重複起動を拒否するので、照合の窓で二重に `pane run` しても、2 つ目は起動しない（二重の安全）。
  7. **監視の起動**: `kaji watch start`（冪等）。
- 出力は最後に JSON（`series_id`・`yaml`・`commit`・`series_pane`・`watch`）を出す。
- skill の絶対規則: series YAML と draft を LLM が手で書かない。`kaji run` / `kaji run-series` を直接起動しない。明示された依頼（Issue と workflow を保守者が指定した）は、それ自体を承認として扱う。

### 6. `/watch-triage`（判断用の skill）

- 起動: `kaji watch` が `herdr tab create --label watch-triage-<issue> --no-focus` → `herdr pane run <pane> "claude --dangerously-skip-permissions '/watch-triage <event-file>'"`（D6・A7）。
- 入力: event ファイル。読むもの: event、Issue 本文（特に `## 決定事項`）、`kaji status --json <issue>`、run の artifact（event の `artifacts.run_dir` / `artifacts.attempt_dir` の下の `prompt.txt`・ログ・`run.log`。パスは watch が `runstate.query.locate_step_artifacts` で解決して event に書いたものだけを使い、skill がパスを組み立てない。読むのは原因の調査のための本文の閲読で、状態の判定は event の `status_document` を正とする。`artifacts` が null なら `kaji status --json <issue>` の再取得だけで報告し、artifact が見つからなかったことを報告に書く）、`pane_idle` / `log_stall` なら `herdr pane read <step_pane> --source recent --lines 200`。
- 手順: 読む → 原因を分類する → 報告コメント（1 行目 `<!-- kaji-watch-triage: key=<event key> -->`）→ `kaji watch record --event <file> --outcome handoff --comment-url <url> --summary <要約>`（通知はこの record が送る）→ pane を開いたまま、保守者の指示を待つ。
- `pane_idle` で agent が作業中だった、または `log_stall` で進んでいた場合は、`still-working` / `not-stalled` を記録して自分のタブを閉じる。
- 保守者の承認を得た復旧だけを実行する。失敗した step のやり直しは `kaji recover <workflow> <issue> --retry-failed-step` だけを使い、実行後に `kaji watch record --outcome recovered` と `kaji watch start` を行う。
- kaji 本体の欠陥と判断した場合は、incident Issue の下書きを `[skill_tmp_dir]/incident-draft.md` に作るだけで、起票しない。

#### 絶対規則と禁止リスト（契約テストの正本）

agent の質問には答えない（答えて戻すのは #545 の範囲）。質問の内容と、`critical-decision-checklist.md` の 3 分類による見立ては報告に含める。

次の禁止リストの操作は、保守者がその pane で明示的に承認したとき（「この操作を実行してよい」という返答）だけ行う。下の表は SKILL.md の同じマーカー区間と一致しなければならず、その一致を契約テストで検査する。

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

### 7. 使用例

```bash
# UC1: 保守者が Herdr の pane の Claude に「#101, #102 を dev で直列に」と依頼する → /run-watched が次を実行する
python -m kaji_harness.scripts.run_watched plan --issues 101,102 --workflow dev
#   → {"draft_id": "d-261115-0301", "members": [...], "excluded": [], "approval_required": false}
python -m kaji_harness.scripts.run_watched execute --draft d-261115-0301
#   → {"series_id": "rw-101-102-2611150301", "commit": "abc1234", "series_pane": "p_20", "watch": "started"}

# UC4: 状態を確かめる・監視を起動し直す（何度起動しても同じ状態に収束する）
kaji watch status
kaji watch once --dry-run | jq '.issues[] | {issue, current_label, target_label}'
kaji watch start            # 動いていれば "watch already running" で exit 0

# UC2: /watch-triage が保守者に回したことを記録する（通知もここで送る）
kaji watch record --event .kaji-artifacts/_watch/events/101-aborted-261115113853.json \
  --outcome handoff --comment-url https://github.com/apokamo/kaji/issues/101#issuecomment-1 \
  --summary "implement が usage limit で ERROR。retry-failed-step を推奨"

# issue-close から（[watch] がない repo では skipped で exit 0）
python -m kaji_harness.scripts.watch_labels mark-done 101
```

```bash
# notify_command の例（docs に載せる。秘密は env にだけ置く）
#!/usr/bin/env bash
set -euo pipefail
payload=$(cat)
[ -n "${DISCORD_WEBHOOK_URL:-}" ] || exit 0
jq -n --arg c "$(jq -r '"[\(.kind)] #\(.issue // "-") \(.summary) \(.comment_url // "")"' <<<"$payload")" '{content: $c}' \
  | curl -fsS -H 'Content-Type: application/json' -d @- "$DISCORD_WEBHOOK_URL" >/dev/null
```

## 制約・前提条件

- **#540 の API だけを通す（M2）**: `kaji_harness/runstate/query.py` を新設し、watch はこのモジュールだけを runstate から import する。中身は既存の収集器の再利用だけで、判定を再実装しない。
  - `collect_status(artifacts_dir, issue_ids: list[str], *, probe=None) -> JsonDict`: `collect_issue_facts` → `issue_to_json` → `status_document` を順に呼び、`kaji status --json <issue>...` と同じ schema v1 の文書を返す。`commands/status.py` の Issue 指定の経路もこの関数を呼ぶように置き換え、CLI と watch が 1 つの経路を共有する（CLI の出力は不変。既存の status のテストで担保）。
  - `list_series(artifacts_dir) -> list[JsonDict]`: `_series/` の下の series id を列挙する `list_series_ids`（`series_to_json` が既に使っている `_series/<id>` の配置の規則に揃えて `runstate.schema` に追加。id は `is_valid_series_id` を満たすものだけ）と、既存の `series_to_json` を組み合わせ、`kaji status --json --series <id>` の `series` 部分と同じ形を返す。
  - `locate_step_artifacts(artifacts_dir, issue_id, run_id, step_id, attempt) -> StepArtifacts(run_dir: Path | None, attempt_dir: Path | None)`: 既存の `facts._attempt_dir` を `facts.attempt_dir` として公開し直して使う（挙動は不変）。triage がパスを自分で組み立てずに済むようにするため。
  - facade（`runstate/__init__.py`）は循環を避けて `facts` / `schema` を再 export しない方針（同ファイルの docstring）なので、`query` も facade には載せず、`kaji_harness.runstate.query` として import する。
  - watch は `run.log` / `session-state.json` / `process.json` / `_series/*/state.json` を開かない。自分の永続化（`_watch/` の下）の I/O は許可する。検査はテスト戦略の契約テスト（S1 で範囲を限定）で行う。
- **GitHub**: `kaji_harness.providers` の `GitHubProvider`（`search_issues_all` / `view_issue` / `edit_issue` / `comment_issue` / `list_labels`）を使う。`provider.type` は `github` だけを対象にし、`local` provider で `[watch]` を宣言した場合は exit 2 にする（ラベルの概念がないため）。
- **Herdr**: `kaji_harness.interactive_terminal_herdr` の既存のヘルパー（`_resolve_herdr` / `_run_herdr_json` 等）を、公開関数として切り出したもの（`list_tabs` / `create_tab` / `run_in_pane` / `close_tab` / `read_pane_agent_status`）を使う。`read_pane_agent_status` は `inspect_agent_pane` と同じ所有の確認（tokens の `kaji_run` / `kaji_origin`）を行い、所有が確認できない pane の状態は `unknown` として扱う。
- **API の回数**: 1 周あたり、`search_issues_all` が 4 回（`planned` / `running` / `aborted` は `state=all`、`done` は `state=open`）、`aborted` の報告前のコメントの確認（`list_issue_comments_all`、報告待ちの Issue だけ）、差分のある Issue だけ `edit_issue` / `comment_issue`、それに Herdr のローカルな呼び出しが加わる。周期は 180 秒なので、GitHub の rate limit（5,000 req/h）に対して十分に小さい。
- **互換**: `[watch]` を宣言しない repo では、`kaji watch` の exit 2 と `watch_labels` の skip のほかに挙動の変化はない。既存の CLI・skill・`KajiConfig` の既定値は変えない。
- **Python の規約**: `docs/reference/python/` に従う。外部入力（config・event ファイル・draft・状態ファイル）は Pydantic で検証する。

### 変更スコープ

| 種別 | パス |
|---|---|
| 新規 | `kaji_harness/watch/`（`__init__.py` / `__main__.py`（loop の entrypoint）/ `constants.py` / `preflight.py` / `models.py` / `labels.py` / `facts.py` / `detect.py` / `store.py` / `notify.py` / `herdr.py` / `loop.py`） |
| 新規 | `kaji_harness/commands/watch.py`、`kaji_harness/scripts/watch_labels.py`、`kaji_harness/scripts/run_watched.py` |
| 変更 | `kaji_harness/config.py`（`WatchConfig`）、`kaji_harness/commands/parser.py` / `main.py`（`watch` の登録）、`kaji_harness/runstate/`（`query.py` の新設、`schema.list_series_ids` の追加、`facts._attempt_dir` の公開化）、`kaji_harness/commands/status.py`（`query.collect_status` の呼び出しへの置き換え。出力は不変）、`kaji_harness/interactive_terminal_herdr.py`（読み取り・タブ操作の公開関数の切り出し） |
| 新規 skill | `.claude/skills/watch-triage/SKILL.md`、`.claude/skills/run-watched/SKILL.md` |
| 変更 skill | `.claude/skills/issue-close/SKILL.md`（`watch_labels` の呼び出しを 2 箇所） |
| 設定 | `.github/labels.yml`（`state:*` 6 個・`wf:*` 4 個）、`.kaji/config.toml`（kaji の repo 自身にも `[watch]` を宣言するかは後述） |

## 方針

### データフロー（1 周）

```
facts.collect()
  ├─ provider.search_issues_all(labels=[state:planned|running|aborted], state="all")  → 対象の Issue
  ├─ provider.search_issues_all(labels=[state:done], state="open")                    → reopen された done（M6）
  │    + 状態ファイルの pending_labels / pending_reports / 未起動の event の Issue
  ├─ runstate.query.collect_status(issues)       → IssueStatus（#540 schema v1）
  ├─ runstate.query.list_series()                → Issue → 最新の series の member（anchor）
  └─ herdr.list_tabs / read_pane_agent_status    → triage タブ・step の pane の状態（Herdr の外の --dry-run では省略）
labels.compute_targets(facts, state)            → 純粋関数。Issue ごとの目標・保留の理由
detect.detect_events(facts, targets, ledger, now) → 純粋関数。未処理の event のリスト（M5: ラベルの遷移に依存しない）
store.save_intents(pending_labels, pending_reports, new events=detected)   → 1 回の原子的な保存（M4: ラベル操作より前）
label ops: 付ける → 外す → pending_labels から消して保存
launch: detected / launching の event ごとに、照合 → 起動 → launched を保存（M4）
reports: aborted に揃った Issue ごとに、marker の既存確認 → コメント → 保存 → 通知（M4）
empty_polls 更新 → 2 周続けて 0 件で、pending と未起動の event がなければ watch_exit を通知して終了
```

### 目標ラベルの計算（`labels.compute_targets`。純粋関数）

書き換えの対象は、今のラベルが `planned` / `running` / `aborted` / `done` のうち 1 個だけの Issue（と、自分の `pending_labels` にある Issue）に限る。上から順に、最初に当てはまった行を採る。

**reopen された `state:done`（M6）**: open で今のラベルが `state:done` の Issue は、次の場合だけ下の表で再計算する。(a) `any_run_alive == true` または `launching == true`、(b) watch がその Issue を open の `done` として初めて見た周の `latest_run.run_id`（状態ファイルの `reopen_baselines`）より新しい run がある。どちらでもなければ `done` のまま書かない（reopen しただけでは planned / aborted と断定しない）。(b) の anchor は `reopen_baselines` の値とする。

| 事実 | 目標 |
|---|---|
| closed かつ `state_reason == completed` | `state:done` |
| closed かつそれ以外（not planned 等） | `planned` / `running` / `aborted` を外す（`state:*` なし） |
| open かつ（`any_run_alive == true` または `launching == true`） | `state:running` |
| open かつ `any_run_alive == null`（判定できない） | 書かない（`hold_reason: unknown_liveness`） |
| open かつ anchor 以降の run がない | `state:planned` |
| open かつ anchor 以降の最新の run が `termination == ended` で `end_status ∈ {ABORT, ERROR}`、または `termination == vanished` | `state:aborted` |
| open かつ anchor 以降の最新の run が `ended` / `COMPLETE`（部分実行。`--before close` 等） | `state:aborted`（event は起こさない） |
| open かつ `termination ∈ {unknown, null}` | 書かない（`hold_reason: unknown_liveness`） |

**anchor**（どの run から数えるか）: kamo2 の時刻比較（GitHub の `labeled` の時刻と run の時刻）は、時計のずれの補正（60 秒の窓）を必要とした。本設計では、時刻ではなく run_id の比較にする。

1. Issue を member に持つ series のうち、`updated_at` が最新のものの member を採る。member の `run_id` があれば、`run_id >= member.run_id` の run を数える。member が `pending`（`run_id` なし）なら、run は 0 件として扱う（生きていれば上の行で `running` になる）。
2. どの series の member でもない Issue は、状態ファイルの `baseline_run_id` を使う。これは、watch がその Issue を `state:planned` で初めて見た周の `latest_run.run_id`（なければ空）で、それより大きい run を数える。

run_id は、同じ host の時計による `YYMMDDHHMMSS(-NNN)` の文字列比較で決まる（#540 の `RunStatus` の定義）。GitHub の時計は使わない。

**#1909 と同じ形の状況**（series の子が run dir を作る前の窓）: #540 の launch record が `launching: true` を返すので、`running` になる。kamo2 がプロセス表を読み直して補っていた部分は不要になる。

**#1910 と同じ形の状況**（付与の直後に、ラベル別の一覧へ反映されない）: `EMPTY_POLLS_TO_EXIT = 2` に加えて、`pending_labels` が残っている間は終了しない。`/run-watched` が付けた直後の Issue は 1 周目で見えないことがあるが、2 周目までに見えれば継続する。

### 付け替えの冪等性

- 状態ファイル `<artifacts>/_watch/state.json`（Pydantic で検証。壊れていたら `.broken-<ts>` に退避し、空から始めて警告する）には、`pending_labels: {issue: target}`、`pending_reports: {issue: event_key}`、`baselines`、`pane_idle_counts`、`empty_polls`、`triage_tabs` を持つ。
- 付け替えの手順: `pending_labels` に記録して保存する → 目標を付ける → ほかの `planned` / `running` / `aborted` / `done` を外す → 記録を消して保存する。途中で落ちた場合も、次の周で同じ目標を再計算して付け直す（付ける・外す操作は冪等）。
- 書き込みのたびに、`labels.jsonl` に `judge={host}:{pid}` を付けて 1 行を追記する（別の監視との競合を調べるため）。

### 検知（`detect.detect_events`。純粋関数）

| kind | 条件 | event key |
|---|---|---|
| `aborted` | 目標が `state:aborted` で、その根拠が anchor 以降の最新の run の失敗（`ended` かつ `end_status ∈ {ABORT, ERROR}`、または `vanished`）であり、その run の key `<issue>:aborted:<run_id>` が ledger（events と records）にまだない。ラベルの遷移は条件にしない（M5）。`COMPLETE` で open の場合は目標が `aborted` でも event にしない | `<issue>:aborted:<run_id>` |
| `pane_idle` | `latest_run.termination == running`、`latest_run.pane` がある（headless なら null なので対象外。D4）、step の pane の `agent_status` が `working` 以外（`idle` / `done` / `blocked` / `unknown`）の周が、同じ `(run_id, step, attempt)` で `PANE_IDLE_POLLS` 回続いた | `<issue>:pane_idle:<run_id>:<step>:<attempt>:<episode>`（`working` に戻るたびに `episode` を増やす） |
| `log_stall` | `latest_run.termination == running` で、進み具合（`run_id`・`last_step.id`・`last_step.attempt`・`termination`）が `LOG_STALL_SECONDS` の間変わらない | `<issue>:log_stall:<run_id>:<step>:<attempt>:<recheck>` |

- **M5 の例**: run A の失敗で既に `aborted` の Issue を承認後に retry し、次の周までに run B も失敗した場合、目標は前後とも `aborted` だが、run B の key `101:aborted:<B>` は ledger にないので event になる。watch を止めている間に新しい run が失敗した場合も同じく、起動後の最初の周で event になる。
- **event の存在と起動済みを分ける（M4）**: event は `launch_status` を持つ（`detected` → `launching` → `launched`、または `launch_failed`）。同じ key で 2 度起動しないための判定は `launched` に対して行い、ファイルの存在では判定しない。手順は次のとおり。
  1. 検知した event は、ラベル操作より前に `detected` として event ファイルと状態ファイルに保存する（処理意図の永続化）。ラベル操作の途中で落ちても、次の周に `detected` から再開する。
  2. 起動の直前に `launching`（`launch_attempts += 1`、タブの label `watch-triage-<issue>`）を保存する。
  3. `herdr tab create` → `herdr pane run`。成功したら tab_id・pane_id と `launched` を保存する。
  4. 再起動時、または前の周に `launching` のまま残った event は、まず Herdr を照合する。label `watch-triage-<issue>` のタブがあり、その pane の前景に claude がいる場合は、`launched` として記録する。タブはあるが pane がシェルだけの場合（`pane run` の前に落ちた）は、そのタブを閉じてから起動し直す（このタブは watch が作ったもので、label で所有を確認する）。タブがなければ、そのまま起動し直す。
  5. 起動の失敗（Herdr のエラー）は警告して `detected` に戻し、次の周にやり直す。`launch_attempts` が 3 に達したら `launch_failed` にして、それ以上自動では起動しない。`status` に `launch_failed` と event ファイルのパスを表示する（保守者は `/watch-triage <event-file>` を手で起動できる）。
- `launched` の event は、同じ key では再び起動しない。
- 抑止 1: その Issue の `watch-triage-<issue>` タブが開いている間は起動しない。
- 抑止 2: `handoff` を記録した Issue は、進み具合（目標ラベル・`run_id`・`last_step`・`episode`）が変わるまで、event の種類によらず起動しない。タブを閉じても、監視を起動し直しても解除しない（`records.jsonl` から再構成する）。
- `still-working` の記録の後は、`STILL_WORKING_SNOOZE_SECONDS` の間、同じ attempt・episode では起動しない。その後も条件が続いていれば、key の末尾に `:r<n>` を付けて再確認する。`not-stalled` の後は、記録から `LOG_STALL_SECONDS` 後に `recheck + 1` で再確認する。
- `log_stall` は、kamo2 の「`run.log` の更新時刻」ではなく、#540 の文書に現れる進み具合の不変時間で判定する。`run.log` は step の実行中には行が増えないので、kamo2 の判定も実質的には step の境界で進んでいた。dev.yaml の step timeout の最大値は 3600 秒（implement）で、90 分はそれより長い。`kaji status` の契約に新しいフィールドを足さずに済む。

### `aborted` の報告

- `aborted` の event を検知した周に、ラベル操作より前に `pending_reports[key]` を保存する（M4。検知は M5 のとおり事実から行うので、記録が失われても次の周に再検知できる）。
- ラベルが `aborted` に揃った周に、次の順で報告する。
  1. **marker の既存確認**: `list_issue_comments_all` で、1 行目が `<!-- kaji-watch: aborted key=<key> -->` のコメントを探す。あれば投稿済みとして、その URL を採り、2 を飛ばす（前回、コメントの成功後・保存前に落ちた場合や、応答を失った場合の重複を防ぐ）。
  2. コメントを投稿する（1 行目は上記の marker。run_id・end_status・failed_step・failure.cause を書く）。例外（応答の喪失を含む）の場合は、結果不明として `pending_reports` を残し、次の周に 1 からやり直す。
  3. `reports[key] = {comment_url, notified: true}` を保存し、`pending_reports` から消す。
  4. 通知（kind `aborted`）を送る。通知は 3 の保存の後に 1 回だけ送る（at-most-once。D5 の非再送）。3 と 4 の間で落ちた場合、その通知は送られないが、コメントは残る。
- 目標が `aborted` でなくなったら、`pending_reports` を捨てる。

### 終了

終了判定の件数は「open で、今のラベルまたは目標が `planned` / `running` / `aborted` の Issue」の数とする。reopen された `done` で再計算の対象にならないものは数えない（M6: 取得の経路と終了判定の件数を分ける）。この件数が 2 周続けて 0 件になり、`pending_labels`・`pending_reports`・`detected` / `launching` の event がすべて空なら、`watch_exit` を通知して exit 0 で終える。事実の収集に失敗した周は `empty_polls` を 0 に戻し、警告して次の周へ進む（GitHub が一時的に落ちても監視は終わらない）。

### `herdr` の公開関数

`interactive_terminal_herdr.py` から、読み取りとタブの操作を薄い公開関数として切り出す。既存の private 関数の挙動は変えない。watch は `kaji_harness.watch.herdr` を経由して呼び、テストでは runner を差し替える。

### kaji の repo 自身の `[watch]`

kaji の repo の `.kaji/config.toml` には `[watch]` を**宣言しない**。宣言は opt-in の運用判断であり、kaji の repo でラベルが GitHub に作られるのは merge の後（labels-sync）になる。宣言してしまうと、merge から labels-sync までの間、`issue-close` が exit 2 で止まりうる。事後確認（release 後の dogfood）の段階で、保守者が宣言する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|---|---|---|---|
| 置き場所 | 監視とラベルの同期は CLI、判断は skill | 保守者の決定（2026-11-08、EPIC #535。Issue「重要判断」1 行目） | CLI は `kaji watch`、loop の本体は内部の entrypoint、判断は `/watch-triage`・`/run-watched` |
| ラベル名 | kamo2 と同じ名前で opt-in、`kaji:` の接頭辞なし | 保守者の決定（2026-11-08。Issue「重要判断」2 行目） | `state:*` 6 個・`wf:*` 4 個の色・説明・付ける主体と外す主体 |
| opt-in の判定 | tracked の `[watch]` の table。overlay は無視。宣言済みでラベルがなければエラー | D1 | `WatchConfig`・`_parse_watch`・exit 2・`watch_labels` の skip / exit 2 |
| 定義するラベル | 6 個の `state:*` と `wf:*` だけ | D2 | kaji の repo の `wf:*` を 4 個に決めた（Issue から指定する運用のある workflow だけ） |
| CLI の形 | 5 つのサブコマンド、exit 0 / 2 / 3、`stop` なし | D3 | 各サブコマンドの前提と動作、`record` の任意の引数（`--comment-url` / `--summary`）、`OUTCOME` の enum、loop を内部 entrypoint に置くこと |
| Herdr と headless | 監視は Herdr 必須、run は headless 可で `pane_idle` だけ行わない | D4 | `latest_run.pane == null` で `pane_idle` を対象外にする |
| 通知 | `notify_command` に JSON を標準入力で渡す | D5 | 通知の JSON（schema v1・3 kind）、timeout 30 秒、ログに stderr を出さない |
| triage の起動 | 権限確認なし。禁止リストを契約テストで固定 | D6 | 禁止リスト 10 項目、マーカー区間の一致テスト |
| series YAML の扱い | YAML だけを main に commit / push | D7 | `execute` の段 4 の前提（main checkout・clean な index）と exit |
| #422 との分担 | 独立に共存 | A1（AI の仮定。検査先: issue-review-design） | `pane_idle` は汎用の検知として残す |
| `/series-create` との関係 | 併存し、生成器を共有 | A2（AI の仮定。検査先: issue-review-design） | `kaji_harness.series.generator` を共有する |
| 決定的な処理の置き場所 | `kaji_harness/scripts/` のモジュール | A3（AI の仮定。検査先: issue-review-design） | `run_watched.py`・`watch_labels.py` |
| 周期としきい値 | kamo2 と同じ既定値の定数 | A4（AI の仮定。検査先: issue-review-design） | `STILL_WORKING_SNOOZE_SECONDS = 1200` を追加（kamo2 の 20 分） |
| 内部の形式 | 状態・event・記録は artifacts の下の内部の形式 | A5（AI の仮定。検査先: issue-review-design） | `<artifacts>/_watch/` の配置。event ファイルは triage の入力なので schema_version を持たせる |
| `schema_version` | #540 の D2 と同じ規則 | A6（AI の仮定。検査先: issue-review-design、i-dev-final-check） | `status` / `events` / 通知の項目 |
| triage の agent | `claude` に固定 | A7（AI の仮定。検査先: issue-review-design） | 起動の argv |
| `issue-close` の宣言の確認 | kaji の読み取りコマンドで確かめる | A8（AI の仮定。検査先: issue-review-design） | 確認とラベル操作を `watch_labels` script にまとめ、skill は TOML を読まない |
| triage skill の名前 | `/watch-triage`（Issue の仮称は `/lane-triage`） | **AI の仮定**（本設計で追加）。根拠: kaji には「レーン」の概念がなく（`/plan-run` 相当は EPIC の範囲外）、`kaji watch` と対にした方が発見しやすい。skill 名は後から改名できる（two-way door）。検査先: issue-review-design | タブの label `watch-triage-<issue>`、コメントのマーカー |
| `log_stall` の判定元 | #540 の文書の進み具合の不変時間 | **AI の仮定**（本設計で追加）。根拠: 完了条件「#540 の API を通す」に合わせ、`kaji status` の契約を広げない。内部の判定なので後から変えられる。検査先: issue-review-design | 進み具合の 4 要素 |
| anchor | 時刻ではなく run_id の比較（series の member / `baseline_run_id`） | **AI の仮定**（本設計で追加）。根拠: kamo2 の時計のずれの補正（apokamo/kamo2#2183）を不要にする。内部の判定なので後から変えられる。検査先: issue-review-design | 方針の「anchor」 |
| `mark-done` が外すラベル | ほかのすべての `state:*`（`hold` / `needs-maintainer` を含む） | Issue 完了条件「state:done を付け（他の state:* は外す）」（review-design の M1 で、初版の弱化を是正した） | 契約表の「外す主体」に `issue-close`（close 時）を追加 |
| 事実の取得の境界 | `runstate.query`（既存の収集器の再利用だけ）を新設し、`kaji status` の CLI も同じ経路を使う | #543 完了条件「事実の取得は #540 の API を通す」。query の新設は AI の詳細化（内部の module 境界で、公開の schema は変えない。検査先: issue-verify-design） | `collect_status` / `list_series` / `locate_step_artifacts` |
| series YAML だけを公開する保証 | HEAD と remote の一致を前提とし、`<sha>:refs/heads/<branch>` で 1 commit だけを push | D7「YAML だけを main に commit / push」（review-design の M8 で、index の検査だけでは足りないことが再現された） | 段 0・段 4 の照合と再開 |
| kaji の repo での `[watch]` の宣言 | 本 Issue では宣言しない | **AI の仮定**。根拠: labels-sync より前に `issue-close` が止まる窓を作らないため。宣言は 1 行で足せる。検査先: issue-review-design、事後確認 | — |

source of truth の扱い: Issue 本文の `## 決定事項`（D1〜D7・A1〜A8）と、#540 の決定事項 D1〜D10 / `docs/cli-guides/run-status.md` を正とし、これらを上書き・弱化していない。one-way door の未決は見つからなかった。公開の契約（CLI・JSON）の形は D3・D5・A6 の範囲内で詳細化した。

## テスト戦略

### 変更タイプ

- 実行時コード変更（新しい CLI・内部 package・script・config）、skill の追加と変更、`.github/labels.yml` の変更

### 実行時コード変更の場合

#### Small テスト

- `labels.compute_targets`: 方針の表の全行（closed で completed / not planned、alive、`launching` のみ true、`any_run_alive == null`、anchor の前の run だけ、`ABORT` / `ERROR` / `vanished` / `COMPLETE` で open、`termination == unknown`）。対象外のラベル（`hold` / `needs-maintainer` / 2 個以上）では書かないこと。
- #1909 と同じ形: series の member が `running` で run dir がなく、`launching: true` → `running`（`aborted` にしない）。
- #1910 と同じ形: 1 周目に 0 件、2 周目に 1 件 → 終了しない。`pending_labels` が残っている間は 0 件が続いても終了しない。
- anchor: series の member の `run_id` より前の run は数えない。member が `pending` なら `planned` にする。series の外の Issue は `baseline_run_id` を使う。
- `detect.detect_events`: 3 つの kind の成立と不成立、event key の書式、`episode` の増加、headless（`pane == null`）では `pane_idle` を出さないこと、タブが開いている間・`handoff` の後の抑止（進み具合が変わると解除）、`still-working` / `not-stalled` の再確認のタイミング。
- `WatchConfig` の検証: table でない、`notify_command` が空・非文字列・未知の key、overlay の `[watch]` を無視すること。
- 通知・`status` / `events` の JSON: 必須のフィールドと `schema_version == 1`。
- `run_watched` の分類と承認の要否: 除外の理由ごとの分類、承認の 3 条件、workflow の解決（stem・ファイル名・パス・`wf:*`・曖昧・解決不能・食い違い）、series-id の書式。
- `OUTCOME` と kind の組み合わせの検証。

#### Medium テスト

- loop の 1 周（`once`）: tmp の artifacts に、runstate のテストが使う fixture の作り方で run を作り、fake の provider と fake の Herdr runner を注入する。差分だけ `edit_issue` を呼ぶこと、2 周目には書かないこと（収束）、`--dry-run` で provider・状態ファイル・Herdr・通知のどれにも書かないこと。
- 付け替えの途中で落ちる場合: 「付ける」の直後に例外を起こし、次の周で `pending_labels` から直ること。状態ファイルが壊れていれば退避して続けること。
- `aborted` の報告が 1 回だけであること（2 周目・再起動後にもコメントしない）。コメントが失敗したら次の周でやり直すこと。
- `notify_command`: tmp の script（標準入力を file に書く）で JSON が届くこと。失敗・timeout は警告して続行すること。argv とログに通知の内容と stderr が出ないこと。
- lock: `once` が lock の保持中に exit 2 になること。`start` が lock の保持中に exit 0（何もしない）になること。
- CLI の exit code: `[watch]` がない → 2、ラベルが足りない → 2、Herdr の外の `start` → 2、`local` provider → 2、予期しない例外 → 3。
- `watch_labels`: skip / exit 2 / `mark-follow-up`。`mark-done` は、`state:hold` 付き・`state:needs-maintainer` 付き（follow-up の完了）・2 個以上付きの Issue を close した場合に、結果が `state:done` の 1 個だけになること（M1）。
- 前提の fail-fast（M7）: Herdr の外で `once`（`--dry-run` なし）を呼ぶと exit 2 になり、GitHub・状態ファイル・event・Herdr のどれにも書かないこと。`--dry-run` は Herdr の外でも動き、`herdr: unavailable` を出すこと。`run_watched execute` が `[watch]` なし・ラベル不足・Herdr の外の場合は、git（commit なし）・GitHub（ラベルなし）・Herdr のどれにも副作用を残さず exit 2 になること。`status` / `events` / `record` も、宣言済みでラベルが足りなければ exit 2 になること。
- aborted の検知（M5）: run A の失敗で `aborted` の Issue に対して、poll の間に retry した run B も失敗した場合に、`<issue>:aborted:<B>` の event が出ること。watch の停止中に新しい run が失敗した場合も、起動後の最初の周で出ること。`COMPLETE` で open の場合は出ないこと（S と M の両方）。
- 永続化の順序（M4）: 次の各境界で例外を起こし、再起動後の挙動を確かめる。
  - ラベル付け替えの直後: 次の周に `detected` の event から起動されること。
  - `launching` の保存後・`tab create` の前: 起動し直されること。
  - `tab create` の後・`pane run` の前: シェルだけのタブを閉じてから起動し直されること。
  - `pane run` の後・`launched` の保存前: 照合で `launched` になり、二重に起動しないこと。
  - Herdr の起動が 3 回失敗した場合: `launch_failed` になること。
  - コメントの成功後・保存前、およびコメントの応答喪失: marker の既存確認で、二重に投稿しないこと。
  - 保存後・通知前: 通知は送られず、コメントは 1 件であること。
- reopen された done（M6）: `state:done` の Issue を reopen し、run を起動すると `running` に戻ること。run のない reopen では `done` のまま書かないこと。いずれの場合も、終了判定の件数に正しく反映されること。
- `run_watched execute`（M3・M8）: tmp の git repo（bare の remote 付き）で確かめる。`git` は実物、`gh` と `herdr` は fake を使う。
  - 中断と再開: YAML の作成直後、commit の直後、push の直後、各 Issue のラベル付与の直後、タブの作成直後、`pane run` の直後のそれぞれで停止させ、同じ draft で再実行する。済んだ段は外部の照合で飛ばされ、空の commit・二重の push・二重のラベル・二重のタブ・二重の起動が起きないこと。
  - push の失敗後: remote が parent のままなら push だけがやり直されること。remote が進んでいれば exit 3 で止まり、rebase・force push をしないこと。
  - 状態の再検査: 段の間で、Issue が close される・`state:hold` が付く場合は、残りの副作用を行わずに exit 2 になること。自分が付けた `state:planned` は相違とみなさず、再実行が続行すること。
  - 既存の YAML: sha256 が一致しない既存の series YAML は上書きせず、exit 2 になること。
  - 無関係な ahead commit: main に無関係な未 push の commit がある場合は、段 0 で exit 2 になり、YAML の作成・commit・push のどれも行わないこと（M8。レビューで再現された状況）。push の後、remote の差分が YAML の 1 ファイルだけであること。
  - 検証の失敗: ラベル・タブを変えないこと。
- **#540 の API だけを使う契約テスト（S1 で範囲を限定）**: 対象は、run と series の内部事実を取得する境界だけとする。
  - `kaji_harness/watch/` の全モジュールについて、AST で次を検査する。runstate から import するのは `kaji_harness.runstate.query` だけで、`runstate.facts` / `runlog` / `process` / `launch` / `schema` と `kaji_harness.series.state` を import しない。文字列リテラル `run.log` / `session-state.json` / `process.json` / `pane-metadata.json` / `_series` が現れない。
  - 自分の永続化（`store.py` による `_watch/` の下の state・event・record の読み書き）は許可し、`open` / `Path.read_text` を一律には禁止しない。
  - `query.collect_status` の結果が、同じ fixture に対する `kaji status --json` の文書と一致すること（CLI と同じ経路であることの検査）。`locate_step_artifacts` が既存の attempt dir を返し、ないものには null を返すこと。
- **skill の契約テスト**:
  - `.claude/skills/watch-triage/SKILL.md` と本設計書の `watch-triage-forbidden` マーカー区間の表から ID の集合を抜き出し、一致することを確かめる（D6）。
  - watch-triage に「agent の質問に答えない」の規則があること。
  - run-watched に「series YAML / draft を手で書かない」「`kaji run` / `run-series` を直接起動しない」の規則があること。
  - issue-close が `watch_labels mark-done` / `mark-follow-up` を呼ぶこと。
- **labels.yml の検証**: `state:*` 6 個と `wf:*` 4 個が定義されていること、`state:ready` / `state:needs-verify` / `state:needs-grill` / `prio:*` がないこと、名前が重複しないこと、色が 6 桁の hex であること、`wf:<name>` が `.kaji/wf/**/*.yaml` の `name:` にちょうど 1 個ずつ一致すること。

#### Large テスト

- `large_local`: 実の `kaji` CLI を subprocess で起動する。tmp の repo（`[watch]` を宣言した config と、fake の `gh` / `herdr` を PATH の先頭に置く）で、`kaji watch once --dry-run` と `kaji watch status --json` が schema v1 の JSON を出し、exit 0 になること、`[watch]` がない repo で exit 2 になることを確かめる。CLI の配線（parser → command → package）を通しで検証するため。
- 実際の GitHub API・実際の Herdr に疎通するテスト（`large_forge`）は追加しない。`testing-convention.md` §「正当化できる理由」に沿った根拠は次のとおり。
  1. **物理的に作成不可**: 契約の `state:*` ラベルは、merge 後に labels-sync が作るまで GitHub に存在しない。そのため、実装の段階で実の repo に対して付け替えを検証できない。また、恒久テストとして実の Issue のラベルを書き換えると、CI ごとに共有の repo の状態を汚す。
  2. **既存ゲートで捕捉できる**: GitHub への書き込みは、既存の `GitHubProvider.edit_issue` / `comment_issue` / `search_issues_all` の組み立てだけで行い、新しい API は呼ばない。kaji 側の新しい結合点（どの Issue に、どの順で、何を付け外すか）は、Medium の fake provider で全分岐を検証する。`gh issue edit --add-label` の挙動自体は gh の契約である。
  3. Herdr のタブの起動・`kaji run-series` の実起動・triage の claude の起動は、実 GitHub と実 Herdr の両方を必要とする。これらは Issue の `### ワークフロー完了後の確認項目`（release 後の dogfood）として分離済みである。

### 長時間 acceptance の配置

該当なし。実装 step で行う検証は pytest（S / M / `large_local`）と `make check` だけで、nested の `kaji run`・外部 agent を含む dogfood は行わない。Herdr 上の dogfood（`/run-watched` からの起動・判断用のタブの起動）は、Issue の `### ワークフロー完了後の確認項目` に分離済みで、時期は「#543 を含む kaji の release が公開済み」と記載されている。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|---|---|---|
| `docs/adr/` | なし | 新しいライブラリ・技術選定はない（`fcntl` と subprocess は既存の作法） |
| `docs/ARCHITECTURE.md` | あり | `kaji_harness/watch/` package と、runstate との依存の向きを追記する |
| `docs/dev/labels.md` | あり | `state:*` / `wf:*` の節、管理対象の数（29 → 39） |
| `docs/rfc/github-labels-standardization.md` | あり | `state:*` の契約（意味・付ける主体と外す主体・排他・人が付けてよいもの）、`wf:*` の規則 |
| `docs/guides/git-commit-flow.md` | あり | main 直コミットの例外に「`.kaji/series/<id>.yaml` の新規追加（`/run-watched`）」を追加する（D7） |
| `docs/reference/configuration.md` | あり | `[watch]` の table・`notify_command`・overlay の扱い |
| `docs/cli-guides/` | あり | 新規 `watch.md`（+ `.ja.md`）: CLI・exit code・JSON の schema・運用（起動・判断・起動の入口）・Discord に送る例のスクリプト。`run-status.md` に、監視が利用者であることへのリンクを追加する |
| `docs/dev/workflow_guide.md` | あり | `/run-watched`・`/watch-triage` の位置づけ |
| `CLAUDE.md` / `AGENTS.md` | あり（軽微） | `CLAUDE.md` の Development Skills の表に `/run-watched`・`/watch-triage` を追加する |
| `README.md` / `llms.txt` | 要確認 | CLI 一覧があれば `kaji watch` を追加する（final-check で確認） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|---|---|---|
| Issue #543 本文・決定事項 | https://github.com/apokamo/kaji/issues/543 | D1〜D7・A1〜A8、完了条件。本設計の要件の正本 |
| #540 の状態 API | `docs/cli-guides/run-status.md`、`kaji_harness/commands/status.py`（`collect_issue_facts` → `issue_to_json` → `status_document` の順で文書を作る）、`kaji_harness/runstate/schema.py`（`status_document` は受け取った文書を包むだけ / `series_to_json`）、`kaji_harness/runstate/__init__.py`（facade は循環を避けて facts / schema を再 export しない） | 「Monitoring tools call it instead of reading kaji's internal files」。`any_run_alive` / `launching` / `termination` / `pane` / schema の互換規則 |
| #540 の安全ゲート付きのやり直し | `docs/cli-guides/failure-recovery.md` § `kaji recover --retry-failed-step` | triage が使う唯一のやり直し。exit 2 / 3 / 4、`retry-gate:` 行 |
| `[incident]` の作法 | `kaji_harness/config.py`（`IncidentConfig` / `_parse_incident`）、`docs/reference/configuration.md` | tracked の config だけを読み、kaji はラベルを作らない。D1 がこれに倣う |
| 終了コード | `kaji_harness/commands/exit_codes.py` | `EXIT_INVALID_INPUT = 2` / `EXIT_RUNTIME_ERROR = 3` |
| GitHub provider | `kaji_harness/providers/github.py`（`search_issues_all` / `edit_issue` / `list_labels`） | ラベルによる全件の取得、`--add-label` / `--remove-label` |
| Herdr の操作 | `kaji_harness/interactive_terminal_herdr.py`（`inspect_agent_pane` / `_run_herdr_json`）、`.claude/skills/herdr-kaji-launch/SKILL.md` | 所有の確認（tokens）、`HERDR_ENV` / `HERDR_PANE_ID` の確認、split / run の作法 |
| series の生成器 | `kaji_harness/scripts/series_generate.py`、`kaji_harness/series/generator.py`、`.claude/skills/series-create/SKILL.md` | `--id` / `--member ISSUE=WORKFLOW` / `--output`。A2 で共有する |
| 利用側の監視（参照実装） | `~/dev/kamo2/docs/operations/kaji/lane-watch.md`、`~/dev/kamo2/scripts/ops/lane_watch/{facts,events,triage}.py`（apokamo/kamo2。保守者のローカル checkout） | ラベルの計算表、event key の書式、抑止の規則、`agent_status` を `herdr pane list` から読む（`facts.py:765`）、`herdr tab create --label … --no-focus`（`triage.py:75`）、`claude --dangerously-skip-permissions`（`triage.py:50`） |
| 利用側の triage の禁止リスト | `~/dev/kamo2/.claude/skills/lane-triage/SKILL.md` § 絶対規則 2 | 「merge・force push・rebase・Issue / PR の close・ラベルの変更・Issue 本文の変更・kaji の state / series state の削除や書き換え・series YAML の変更・コードの修正・モデル / エージェントの切り替え」。本設計の 10 項目の出典 |
| 重要判断の分類 | `.claude/skills/_shared/critical-decision-checklist.md` | triage の報告に含める 3 分類 |
| main 直コミットの例外 | `docs/guides/git-commit-flow.md` § main 直コミットの例外 | 現状は docs と設定の minor 修正だけ。D7 で series YAML の新規追加を足す |
| テスト規約 | `docs/dev/testing-convention.md` | S / M / L の定義、`large_local` / `large_forge`、省略の 4 条件 |
| workflow の timeout | `docs/dev/workflow-authoring.md` § timeout と attempt deadline、`.kaji/wf/official/dev.yaml` | implement の 3600 秒。`LOG_STALL_SECONDS` がこれより長いことの根拠 |
