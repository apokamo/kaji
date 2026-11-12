# [設計] run 状態の読み取り・安全ゲート付き失敗 step やり直し・series resume の公開 API

Issue: #540

## 概要

kaji の外のツールが内部ファイルを読まずに済むよう、(1) run / series の状態を返す読み取り専用 CLI
`kaji status`、(2) 安全ゲートを kaji 本体が判定して失敗 step から 1 回やり直す
`kaji recover --retry-failed-step`、(3) 失敗 member を失敗 step から再開する `kaji run-series --resume`
を追加する。3 つは「run の生存・最新の run・失敗 step・安全ゲート」を判定する共通モジュール
`kaji_harness/runstate/` を唯一の実装として使う。

## 背景・目的

### 現状（main `158f21c` で確認）

- 状態を読む公開 CLI がない。利用側（apokamo/kamo2 `scripts/ops/lane_watch/`）は `run.log` /
  `session-state.json` / `pane-metadata.json` / `recovery.json` / `result.json` / `_series/*/state.json`
  を直接読み、生存判定・最新 run・非再開 skill・sensitive pattern を再実装している。
  apokamo/kamo2#1909（series 子の起動直後を kill と誤認）/ #1910（時計のずれで run を見落とす）はこの窓から生じた。
- kaji は run の pid を記録していない。`kaji_harness/recovery/target.py` の `select_target_run_dir` は
  `workflow_end` のない run を「in progress」として拒否するため、OOM kill 等で消えた run を安全にやり直す入口がない。
- ゲート判定（`kaji_harness/recovery/handler.py` の `_resume_point` / `_safety_gates` /
  `_non_resumable_skill_hits`）は triage の `decision: resume` 経路からしか使えない。
- `kaji_harness/series/runner.py` の `_execute_member` は `--from` なしで起動するため、`failed` member の
  resume は最初の step からやり直す。`run()` は series 外で close された `failed` / `interrupted` member で
  `member N is already closed before its resumed run` として abort する。

### ユースケース

- **監視ツールの作者**として、kaji の内部形式に依存せず Issue / series ごとの事実を 1 回で得るために、
  `kaji status --json 540 541` / `kaji status --json --series lane-a` を呼びたい。
- **保守者**として、止まった Issue を人が読める表で確認するために `kaji status 540` を使いたい。
- **保守者・判断用 skill（#543）**として、triage が `resume` と判定しない失敗（長い usage limit、OOM kill、
  provider の一時エラー）をやり直すために、`kaji recover <wf> 540 --retry-failed-step` を呼び、
  ゲートの判定を kaji に任せたい。拒否時は exit 4 とゲート名で「保守者に回す」を機械的に判断したい。
- **保守者**として、series 途中の member が環境要因で失敗したとき、`kaji run-series <yaml> --resume`
  1 回で失敗 step から再開し、残り member まで流したい。series 外で直して close した member も完了として進めたい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 内部ファイルの形式を docs で「安定」と宣言し利用側に読ませ続ける | 判定ロジック（生存・最新 run・ゲート）の再実装が利用側に残り、#1909 / #1910 型の窓が消えない |
| やり直しを別サブコマンドにする | 保守者決定 D4（`kaji recover` の拡張）。`--auto-recover` と排他の option で意味を分ける |
| 生存判定を `/proc` 走査（kamo2 `facts.py` 方式）で行う | コマンドライン一致に依存し脆い。run 自身が pid と開始時刻を記録すれば pid 再利用も検知できる |

## インターフェース

### 共通: 生存記録と起動の排他（新規 artifact）

#### 生存記録（ProcessRecord）

同じ形式の記録を 2 種類の場所に書く。

| 項目 | 型 | 意味 |
|------|----|------|
| `schema_version` | int | 1 |
| `role` | enum `run` / `launcher` | `run` = `kaji run` 自身、`launcher` = child の `kaji run` を起動する親（retry / series） |
| `pid` | int | 記録したプロセスの pid |
| `pid_start_ticks` | int \| null | `/proc/<pid>/stat` 第 22 欄（取得不能な OS では null） |
| `boot_id` | str \| null | `/proc/sys/kernel/random/boot_id`（取得不能なら null） |
| `hostname` | str | `socket.gethostname()` |
| `started_at` | str | UTC ISO 8601 |
| `run_id` | str \| null | `role=run` で run dir 採番後に設定。`role=launcher` は child の run_id が判明したら設定（不明なら null） |

1. **launch record**: `<artifacts>/<issue>/launches/<role>-<pid>-<pid_start_ticks or 0>.json`
   - `kaji run` は **config 探索と `resolve_artifacts_dir` の直後、provider による Issue 解決・`session-state.json`
     読み込み・run dir 採番より前**に `role=run`・`run_id=null` で書く。`<issue>` は `normalize_id(<issue 引数>)`
     （GitHub provider では canonical id と一致する。provider 解決後に canonical id が異なれば canonical 側にも同じ記録を書く）。
     run dir 採番後に `run_id` を設定して atomic に書き直す。正常終了時（`finally`）に自分の記録を削除する（best-effort）。
     kill された場合は記録が残るが、生存判定で false になるため無害。
   - retry / series の launcher は child を `Popen` する**前**に `role=launcher` で書き、child 終了後に削除する（best-effort）。
     これで series の `Popen` → `child_pid` 保存 → child の Python 起動 → child の launch record 書き込みの区間も、
     親の記録で「Issue に生きたプロセスがいる」と判定できる。
   - **作成成功を続行の前提とする**（下の「記録の書き込み失敗の規則」）。`kaji run` は provider 解決前の launch record を
     書けなければ provider 解決へ進まず終了する。launcher は launcher record を書けなければ child を起動しない。
     これにより「生きているのに列挙対象の記録が 1 件もないプロセス」は、記録を書く前の Python 起動区間を除いて存在しない。
2. **process.json**: `runs/<run_id>/process.json`（`role=run`）。run dir 採番直後に書き、削除しない（run ごとの恒久記録）。

`process.json` と launch record は、自動復旧 handler（`workflow_end` 後の triage・10 分待機・child 起動）を
含む `kaji run` プロセス全体の生存を表す。

#### Issue 単位の起動の排他（LaunchLock）

`<artifacts>/<issue>/launches/.lock` を `fcntl.flock(LOCK_EX | LOCK_NB)` で取る。retry と series resume の
planner は **ゲート判定 → launcher の launch record 書き込み** をこの lock の中で行い、その後 lock を解放してから
child を起動する。

- lock を取れない（他の retry / series resume が判定中） → ゲート `concurrent_launch` で拒否
- lock 解放後に来た 2 件目の planner は、1 件目の launcher launch record（生存）を見て `issue_run_alive` で拒否する
- これで同じ run に対する retry / retry、retry / series resume の同時要求のどちらか一方だけが起動する（D6 の「同じ run は 1 回」）
- `kaji run` 自体（手動の `kaji run --from` を含む）は lock を取らない。手動 `kaji run` には従来どおりゲートがなく、
  launch record を書く前の Python 起動中の数百 ms に planner が判定した場合はそれを検出できない（既存挙動からの後退ではない）

**D7 との整合**: launch record と lock file は全 `kaji run` が書く生存・排他の協調用ローカル artifact であり、
GitHub への書き込みを伴わない。retry の結果の記録は D7 どおり対象 run の `recovery.json` と `run.log` だけに残す。

#### 記録の書き込み失敗の規則

「列挙対象を作る書き込み」は失敗したら先へ進まない（fail-closed）。「既に列挙対象がある状態での追記」は
warning で続行する。後者でも、生きている間は既存の launch record が生存を示し、死後は `no_record` として
`liveness_unknown` / `issue_liveness_unknown`（または `finished_by_log`）で判定されるため、見落としは生じない。

| 書き込み | 時点 | 失敗時 | 理由 |
|----------|------|--------|------|
| `kaji run` の launch record（`run_id=null`） | config 探索・artifacts 解決の直後、provider 解決より前 | **provider 解決・state 読み込み・run dir 採番に進まず exit 3**（`Error: cannot write launch record: ...`）。Issue コメント等の副作用なし | 失敗して続行すると、run dir ができるまで生存が列挙できない（R1 の反例） |
| canonical id 側の launch record（canonical id が `normalize_id` と異なる場合のみ。GitHub では発生しない） | provider 解決後、run dir 採番前 | run dir 採番に進まず exit 3 | 同上 |
| launch record への `run_id` 設定 | run dir 採番後 | warning で続行 | `run_id=null` の記録が生きている間 `launching: true` と `issue_run_alive` を返し続ける（安全側） |
| `process.json` | run dir 採番後 | warning で続行 | 生存中は launch record が示す。死後は `no_record` → null（`finished_by_log` を除く）で拒否 |
| 自分の launch record の削除 | 終了時 | warning のみ | 残った記録はプロセス終了後に生存判定で false |
| LaunchLock の lock file を開けない（`EWOULDBLOCK` 以外の `OSError`） | planner の手順 1 | ゲート判定に進まず retry は exit 3、series は stopped + exit 3。child を起動しない | 排他なしで判定すると二重起動を防げない |
| launcher record | planner の手順 3（lock 内） | lock を解放し、child を起動せず retry は exit 3、series は stopped + exit 3 | 記録なしで lock を解放して起動すると、後続の planner が同じ run を通過できる |
| `recovery.json` / `run.log` への `outcome=launched` | planner の手順 5（起動直前） | launcher record を削除し、child を起動せず exit 3（series は stopped + exit 3） | D5 / D7 の記録を残せないまま起動しない |
| child 終了後の結果記録・launcher record の削除 | 手順 6 | warning で続行し、retry は child の exit code を返す（D5） | child は既に動いた。残った launcher record は launcher 終了後に false |

起動前に失敗した場合（exit 3）は、`recovery.json` / `run.log` への `outcome=launch_failed` の記録を best-effort で試みる。

#### 生存判定 `probe_liveness(record) -> Liveness(alive: bool | None, basis: str)`

| 条件（上から評価） | `alive` | `basis` |
|--------------------|---------|---------|
| 記録なし（旧 run / 書き込み前） | null | `no_record` |
| 記録が壊れている | null | `record_unreadable`（`read_errors` にも追記） |
| `hostname` が現ホストと異なる | null | `other_host` |
| `boot_id` が両方非 null かつ不一致 | false | `rebooted` |
| `os.kill(pid, 0)` が `ESRCH` | false | `pid_absent` |
| `os.kill(pid, 0)` が成功または `EPERM`（存在するが権限なし）で、`/proc` はあるが `/proc/<pid>/stat` が probe 中に消えた（`ENOENT`） | false | `pid_absent` |
| 同上で `/proc/<pid>/stat` 第 3 欄（state）が `Z` / `X` | false | `zombie` |
| 同上で `pid_start_ticks` 一致 | true | `pid_verified` |
| 同上で `pid_start_ticks` 不一致 | false | `pid_reused` |
| 同上で開始時刻を検証できない（`/proc` なし・記録側 ticks が null） | null | `unverifiable` |

**run.log による補完 `finished_by_log`**: 上の表の結果が `no_record` / `other_host` / `unverifiable`（null）で、
対象が run（launch record ではなく run dir を持つもの）のとき、run.log が次の終端条件を満たせば
`alive: false`、`basis: finished_by_log` とする。run.log に `workflow_end` があり、かつ次のいずれかを満たす。
この後の `kaji run` プロセスは Issue コメント投稿などだけを行い、worktree の操作や新しい run の起動をしない。

- `workflow_end.status == "COMPLETE"`（handler は動かない）
- `recovery_decision` event があり、その `decision != "resume"`
- `recovery_attempt_end` event がある（resume の child が終わった）

上記に当たらない run（`workflow_end` なし、または ERROR / ABORT で handler の終端 event なし）は null のまま。
`pid_verified` で true の run には適用しない（終端 event 後も comment 投稿中は true を返し、`run_alive` で短時間拒否する）。

### 1. `kaji status`（新規サブコマンド、読み取り専用）

```
kaji status [--workdir DIR] [--json] <issue> [<issue> ...]
kaji status [--workdir DIR] [--json] --series <series_id>
```

| 入力 | 型 | 必須 | 説明 |
|------|----|------|------|
| `<issue>` | str（`153` / `gh:153`） | `--series` と排他でどちらか必須 | `normalize_id` で正規化（provider 呼び出しなし） |
| `--series` | str（series id。`^[a-z0-9][a-z0-9-]{0,63}$`） | 同上 | `<artifacts>/_series/<id>/state.json` を読む。形式違反は引数の誤り（exit 2） |
| `--json` | flag | 任意 | JSON 出力。省略時は人向けの表（表の形式は契約にしない） |
| `--workdir` | path | 任意 | 既存サブコマンドと同じ config 探索起点 |

**artifact の場所**: `kaji run` / `kaji recover` / `run-series` と同じ `resolve_artifacts_dir(config)` を使う。
相対 `paths.artifacts_dir` の場合、これは `git worktree list --porcelain`（読み取り専用の git コマンド）で
main worktree を解決し、feature worktree から実行しても run が書いた場所を読む。解決できない場合の
legacy fallback（`config.artifacts_dir`）も既存と同じ。status が呼ぶ git コマンドはこの 1 種類だけで、
`git status` 等の index を更新しうるコマンドは呼ばない。

**出力 JSON（`schema_version: 1`）**:

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-11-12T04:05:09+00:00",
  "series": null,                 // --series 時のみ object（下記）
  "issues": [ /* IssueStatus */ ]  // 引数順。--series 時は member 順
}
```

`series`（`--series` 時）:

| 項目 | 型 | null の意味 |
|------|----|-------------|
| `id` | str | —（CLI 引数の値） |
| `status` | enum `running` / `stopped` / `completed` \| null | state.json が欠落・構文破損、または値が enum 外 |
| `stop_reason` | str \| null | 停止理由なし、または読めない |
| `updated_at` | str \| null | 読めない |
| `members[]` | object の配列（下記） | 配列自体は null にしない。読めなければ `[]` |
| `read_errors[]` | ReadError | — |

`members[]` の各要素: `issue`(int \| null) / `workflow`(str \| null) / `status`(enum `pending` / `running` /
`interrupted` / `completed` / `failed` \| null) / `child_pid` / `run_id` / `exit_code` / `gate` / `started_at` /
`finished_at`。値は `MemberState` の各フィールドをフィールド単位で検証して出す。検証に通らないフィールドだけ
null にして `read_errors` に記録する（`MemberState` 上の未設定 None もそのまま null）。

state.json の状態ごとの出力（D3: どれも exit 0）:

| state.json | `series.status` 等 | `members` | `issues` | `read_errors` |
|------------|--------------------|-----------|----------|---------------|
| 正常 | 値 | 全 member | member 順に IssueStatus | `[]` |
| 欠落（`_series/<id>/` ごと無い場合を含む） | null | `[]` | `[]` | `{"path": "_series/<id>/state.json", "error": "missing"}` |
| JSON 構文破損・top-level が object でない | null | `[]` | `[]` | `error: "invalid"` |
| `members` が配列でない | 他の top-level は個別検証 | `[]` | `[]` | `members` の `invalid` |
| 一部 member / フィールドが不正 | 個別検証 | フィールド単位で null | `issue` が正の int の member だけ | フィールドごとの `invalid` |

`IssueStatus`:

| 項目 | 型 | null の意味 |
|------|----|-------------|
| `issue` | str | — |
| `latest_run` | RunStatus \| null | run が 1 件もない |
| `any_run_alive` | bool \| null | Issue の全 run の `process.json` と全 launch record を生存判定し、1 件でも true なら true。全件 false（または記録が 1 件もない）なら false。true がなく null が 1 件でもあれば null（判定不能） |
| `launching` | bool \| null | 生存中の launch record のうち、`run_id` が null（run dir 採番前の `kaji run`、または child の run_id が未判明の launcher）が 1 件でもあれば true。生存中の launch record がなければ false。判定不能な launch record があり true がなければ null |
| `read_errors[]` | ReadError（`{"path": "<artifacts 相対>", "error": "missing" \| "unreadable" \| "invalid", "message": str}`） | — |

`RunStatus`（最新 run = Issue の `runs/` 直下の run_id 辞書順最大。既存 `select_target_run_dir` /
`select_newer_run_ids` と同じ順序）:

| 項目 | 型 | null の意味 |
|------|----|-------------|
| `run_id` | str | — |
| `workflow` | str \| null | `workflow_start` が読めない |
| `started_at` | str \| null | `workflow_start.ts` が読めない |
| `process` | `{"pid": int \| null, "alive": bool \| null, "basis": str}` | `alive: null` は「判定不能」（`basis` で理由） |
| `termination` | enum `running` / `ended` / `vanished` / `unknown` \| null | `run.log` が読めず、かつ `alive` が true でない（理由は `read_errors`） |
| `end_status` | str \| null | `termination != "ended"`（値は `workflow_end.status`: `COMPLETE` / `ABORT` / `ERROR`） |
| `ended_at` | str \| null | 同上 |
| `last_step` | `{"id": str, "attempt": int \| null, "agent": str \| null, "session_id": str \| null}` \| null | step が 1 度も dispatch されていない |

`last_step` は run.log の最後の `step_start`（`id` / `attempt` / `agent` はその event の値）。`session_id` は
**その run のその attempt の artifact だけ**から次の優先順で取る（Issue 単位の `session-state.json` の
`sessions` は別 run / 別 attempt の古い ID を含みうるため使わない）:

1. `steps/<id>/attempt-<NNN>/result.json` の `session_id`（attempt 完了時、または異常終了時の診断用 ID。runner は dispatch の戻り値をここに書く）
2. 上がない、または null で、`step_start.session_id` が非 null（= `resume:` を持つ step。runner は resume 元の session を渡して同じ session を続ける）→ その値
3. それ以外は null =「この attempt の session ID はまだ記録されていない」（fresh step の dispatch 中、または agent が ID を返さなかった）
| `failed_step` | str \| null | 失敗 step がない（running / COMPLETE）または判定不能 |
| `failure` | `{"cause": str, "source": str, "recoverability_hint": str}` \| null | `termination` が `ended`（ERROR/ABORT）または `vanished` 以外。`vanished` では新 cause `process_vanished`（source `external`、hint `unknown`） |
| `worktree_dir` / `branch_name` | str \| null | `session-state.json` に値がない、または読めない |
| `pane` | `{"backend": "herdr", "pane_id": str, "origin_pane": str \| null}` \| null | `last_step` の最新 attempt に `pane-metadata.json` がない |
| `recovery` | `{"root_run_id": str, "parent_run_id": str, "chain_index": int}` \| null | recovery child ではない |

`termination` の決定表（完了条件「実行中 / 終了の記録あり / 終了の記録なし・プロセスなし」と破損の区別）:

| `workflow_end` | `process.alive` | `termination` |
|----------------|-----------------|---------------|
| あり | 任意（true は終了後の triage / 待機中） | `ended` |
| なし（run.log 可読） | true | `running` |
| なし（run.log 可読） | false | `vanished`（kill / OOM 等の推定） |
| なし（run.log 可読） | null | `unknown` |
| run.log 不可読 | true | `running` |
| run.log 不可読 | false / null | null（`read_errors` に記録） |

#### 必須 artifact 表（status・triage 外の retry / series resume・分類で共通の解釈）

「存在すべきなのに無い／壊れている」だけを `read_errors`（status）と `artifact_unreadable` ゲート（retry /
series）にする。「未完了なので未作成」は欠落として扱わない。判定は `runstate.facts` に 1 箇所で実装し、
`collect_snapshot(..., allow_unterminated=True)` も同じ関数を使う。

| artifact | 存在すべき条件 | 欠落の扱い | 破損（読めない・JSON 不正・必須 key 欠落）の扱い |
|----------|----------------|-----------|-----------------------------------------------|
| `runs/<id>/run.log` | run dir があれば常に（`workflow_start` を含む） | `missing` → 破損と同じ | `artifact_unreadable` |
| `runs/<id>/process.json` | なし（旧 run には無い） | `no_record`（生存判定で扱う。read error にしない） | `record_unreadable`（read error） |
| `steps/<s>/attempt-<n>/result.json` | run.log にその step・attempt の `step_end` がある attempt だけ | `step_end` あり → `artifact_unreadable`。`step_end` なし（実行中・kill された in-flight attempt）→ 正常 | `artifact_unreadable`。必須 key は `AttemptResult` 導入時（#222）からの 10 項目（`step_id` / `attempt` / `status` / `exit_code` / `signal` / `started_at` / `ended_at` / `duration_ms` / `session_id` / `dispatch`）で型も検証する（`runstate.runlog.ResultRecord`）。後から追加された `error` / `synthetic` / `tmp_dir` は省略可（旧形式互換）だが、存在する場合の型不正は `artifact_unreadable`。失敗分類用の snapshot も型不正の値を `None` に倒して `artifact_type_errors` に記録し、facts が blocking read error にする（分類・ゲート評価へ型不正の値を渡さない。評価中の例外も `artifact_unreadable` に倒す） |
| `steps/<s>/attempt-<n>/pane-metadata.json` | なし（Herdr backend のみ） | 正常（`pane: null`） | read error。retry では `agent_pane_unverifiable`。所有確認に使う `pane_id` / `origin_pane` / `kaji_run` のいずれかが欠落・空・非文字列なら破損扱い |
| `<issue>/session-state.json` | なし（初回 step で kill された run では未作成のことがある） | 正常（worktree / branch は null。retry では `worktree_unavailable`） | `artifact_unreadable`（`worktree_dir` / `branch_name` / `last_completed_step` の型不正も含む） |
| `runs/<id>/recovery-chain.json` | なし（root run には無い） | 正常（root） | read error（status）。retry では `artifact_unreadable` |
| `runs/<id>/recovery.json` | なし | 正常 | `artifact_unreadable`（retry は記録を追記できないため） |

**未終了 step の導出**（`termination == vanished` の run の `failed_step`）: run.log の最後の `step_start` のうち、
同じ `step_id` と `attempt` の `step_end` がその後に無いもの。無ければ（全 step が `step_end` 済みで次の step の
`step_start` 前に消えた）`unknown_failed_step`。

**分類**: `allow_unterminated=True` の snapshot は「`workflow_end` event missing」を read error にせず、
`classify_failure` は `workflow_end` も `failure_event` も無い snapshot を新 cause `process_vanished`
（`FailureCause` に追加、source `external`、hint `unknown`）に分類する。

**既存 triage との境界**: `kaji recover`（`--retry-failed-step` なし）と `kaji run` 終了時の handler は
`allow_unterminated=False`（既定）のまま呼ぶため、`select_target_run_dir` の未終了 run 拒否・
「`workflow_end` event missing」の read error・分類順はすべて現状どおり。`process_vanished` はこの経路では出ない。

**終了コード（D3）**: 0 = 出力完了（run の失敗・artifact の破損と欠落を含む。series の state.json 欠落も 0）。
2 = 引数の誤り（issue と `--series` の両方/どちらもなし、`normalize_id` が拒否する issue id、series id の形式違反）・
config なし。3 = 予期しないエラー。

**外部書き込みなし**: ファイル（artifacts・worktree・git index）・GitHub・Herdr への書き込みを行わない。
provider を構築せず、Herdr のコマンドを呼ばない。git は artifact の場所の解決に使う
`git worktree list --porcelain` だけを呼ぶ。launch lock も取らない（lock file を作らない）。

### 2. `kaji recover --retry-failed-step`

```
kaji recover <workflow> <issue> --retry-failed-step [--run-id <id>] [--workdir DIR]
```

| 入力 | 説明 |
|------|------|
| `--retry-failed-step` | 新規 flag。`--auto-recover` / `--no-auto-recover` と排他（argparse の mutually exclusive group） |
| `--run-id` | 省略時は Issue の最新 run を対象にする |

処理（retry と series resume で共通の `RetryPlanner`）:

1. Issue の LaunchLock を取る（取れなければ `concurrent_launch`）
2. ゲートを判定する（下の一覧。拒否ゲートはすべて列挙）
3. 通過なら `role=launcher` の launch record を書く
4. LaunchLock を解放する
5. `recovery.json` / `run.log` に `outcome=launched` を記録し、待機なしで child run を前面起動する
   （`kaji run <workflow> <issue> --from <resume_from> --recovery-root <root> --recovery-parent <run_id>`）。
   child の run dir が見つかれば launcher record の `run_id` を設定する
6. child 終了後に結果を記録し、launcher record を削除する

拒否時は 3〜6 を行わず（child を起動しない）、`outcome=refused` を記録して lock を解放する。
手順 1・3・5 の書き込み失敗は「記録の書き込み失敗の規則」に従い、child を起動せず exit 3 とする。

**終了コード（D5）**:

| 状況 | exit |
|------|------|
| workflow 不在・定義エラー・config なし・provider 解決不能・`--run-id` の run dir 不在・引数排他違反 | 既存 2 |
| 起動前の書き込み失敗（LaunchLock の lock file・launcher record・起動直前の `recovery.json` / `run.log`）。child を起動していない | 既存 3 |
| child 起動の `OSError`、その他の予期しない内部エラー | 既存 3 |
| ゲートで拒否（child を起動していない） | **新規 4**（`EXIT_RETRY_REFUSED`） |
| child run を起動した | child の exit code をそのまま返す |

**stderr（拒否時、機械可読）**:

```
Error: retry refused by safety gate(s): run_alive, branch_mismatch
retry-gate: run_alive pid=12345 basis=pid_verified
retry-gate: branch_mismatch worktree=feat/9 state=feat/540
```

契約: `^retry-gate: (?P<name>[a-z_]+)(?: (?P<detail>.*))?$` の行が拒否ゲート 1 件ずつ（順序は下表の評価順）。

**recovery.json への記録（D5 / D6 / D7）**: 対象 run の `recovery.json` に additive optional な
`retries` 配列を追記する（`RECOVERY_SCHEMA_VERSION` は据え置き。既存 triage 記録は上書きしない）。
`recovery.json` が存在しない run では、同じ snapshot から `plan_recovery(auto_recover=False)` で得た
decision を基に作成し `retries` を付ける。

```jsonc
"retries": [
  {"request_id": "<uuid hex>", "requested_at": "...", "trigger": "retry_failed_step" /* or "series_resume" */,
   "outcome": "launched" /* "refused" | "launch_failed" */, "gates": [{"name": "run_alive", "detail": "pid=..."}],
   "resume_from": "implement", "discarded_resume_session": false,
   "child_run_id": "261112...", "child_exit_code": 0}
]
```

`request_id` は要求ごとの識別子。child 終了・起動失敗の更新は同じ `request_id` の entry だけを置き換え
（無ければ追記）、その間に他の要求が追記した entry は保持する。read-modify-write は run dir の
`.recovery.lock`（`fcntl.flock`、ブロッキング）で排他する。

`run.log` には `retry_requested` / `retry_refused` / `retry_child_end` event を追記する。GitHub へは
書き込まない（Issue コメント・bug Issue・incident なし）。child 自身の終了時 triage は通常の `kaji run` と
同じ設定で動く。

**chain の回数（D6）**: child の `recovery-chain.json` に optional `chain_index`（親の `chain_index` + 1、
親が root なら 1）と `trigger`（`retry_failed_step` / `series_resume` / 既存 auto recovery は `auto_recover`）を追加する。

### 3. `kaji run-series --resume`（既定挙動の変更、D8〜D10）

引数は不変。`--resume` 時の member 処理を次のとおり変える（`pending` member の処理は不変）。

| member status | Issue 状態 | 挙動 |
|---------------|-----------|------|
| `completed` | — | 既存の巻き戻し検知（`_reconcile_resume`）を維持 |
| `failed` / `interrupted` | closed / completed | `completed` に更新（`gate=closed_completed`）して次へ |
| `failed` / `interrupted` | closed / その他（not_planned・duplicate） | `stopped`。`stop_reason`=`member N is already closed before its resumed run: mismatch:closed/not_planned`（D9）、exit 1 |
| `failed` / `interrupted` | open | `RetryPlanner`（trigger=`series_resume`）でゲート判定。通過 → `--from <resume_from>` と recovery chain 引数付きで起動。拒否 → member は起動せず `stopped`、exit 1（D10） |

ゲート拒否時の `stop_reason` 形式（機械可読）:
`member <issue> resume refused by safety gate: gates=<name>[,<name>...]`
（契約: `gates=(?P<gates>[a-z_]+(?:,[a-z_]+)*)$`）。stderr には `retry-gate:` 行も出す。

series runner は `pending` member の通常起動も含むすべての member 起動で、`Popen` の前に `role=launcher` の
launch record を書き、child 終了後に削除する（`child_pid` の保存前・child の launch record 書き込み前の区間を塞ぐ）。
failed / interrupted member の resume は retry と同じ `RetryPlanner` の手順 1〜6（LaunchLock を含む）を使う。
launcher record（通常起動・resume とも）、LaunchLock、起動直前の記録の書き込みに失敗した場合は `Popen` せず、
member の status を変えずに series を `stopped`（`stop_reason`=`member <issue> could not start: <書き込み失敗の内容>`）とし、
既存の `SeriesRuntimeError` として exit 3 を返す（`Popen` 自体の `OSError` は既存どおり member `failed` + exit 3）。

`--dry-run` / `validate-series` の出力・終了コードは変えない（A8）。

### 安全ゲート一覧（公開契約、A6）

`RetryPlanner` が評価し、拒否ゲートをすべて列挙する（`concurrent_launch` のときは他を評価せずそれだけを返す）（最初の 1 件で打ち切らない）。名前は既存
`_safety_gates` / `_non_resumable_skill_hits` / `FailureCause` の識別子を基にする。

| # | name | 拒否条件 | 由来 |
|---|------|---------|------|
| 0 | `concurrent_launch` | Issue の LaunchLock を取れない（他の retry / series resume が判定中） | 新規（R1） |
| 1 | `no_target_run` | `--run-id` 省略時に run が 1 件もない | 新規 |
| 2 | `newer_run_detected` | 対象 run より新しい run がある（指定 run が最新でない） | 既存 |
| 3 | `run_alive` | 対象 run の `process.json` または `run_id` が対象 run の launch record の `alive == true` | 新規 |
| 4 | `liveness_unknown` | 対象 run の `process.alive == null`（A1: 安全側） | 新規 |
| 5 | `issue_run_alive` | 対象 run 以外の run の `process.json`、または対象 run 以外を指す launch record（`run_id` null を含む）の `alive == true` | 新規 |
| 5b | `issue_liveness_unknown` | 5 の対象のいずれかが `alive == null`（true は無い）。= `any_run_alive` が null のときの対象 run 以外の分 | 新規（R2） |
| 6 | `run_not_failed` | `termination == ended` かつ `end_status` が ERROR / ABORT 以外 | 新規（既存 target の拒否を移設） |
| 7 | `artifact_unreadable` | 「必須 artifact 表」で破損・欠落を read error とする artifact に当たる | 既存 |
| 8 | `workflow_mismatch` | `workflow_start.workflow` と指定 workflow の `name` が異なる | 新規 |
| 9 | `unknown_failed_step` | 失敗 step を特定できない、または workflow に存在しない | 既存 |
| 10 | `non_resumable_skill` | 失敗 step / 実再開先が `NON_RESUMABLE_SKILLS` | 既存 |
| 11 | `agent_declared_abort` / `cycle_exhausted` / `user_interrupted` / `ambiguous_worktree_abort` | triage の cause がこれら | `FailureCause` |
| 12 | `sensitive_failure_pattern` | 失敗のエラー文字列が sensitive pattern に一致 | 既存 |
| 13 | `worktree_unavailable` / `branch_mismatch` | worktree がない / branch が state と不一致 | 既存 |
| 14 | `provider_unavailable` | provider を解決できない（retry / series では provider 解決失敗が起動前の exit 2 / 3 になるため通常は発火しない。triage と共有する `_safety_gates` の一部として残す） | 既存 |
| 15 | `agent_pane_alive` | 失敗 step の最新 attempt の Herdr pane が kaji 所有で、前景に agent プロセスが残る | 新規（A7） |
| 16 | `agent_pane_unverifiable` | pane-metadata はあるが Herdr を照会できない / liveness が `unknown` / 所有確認の情報（metadata 側 `origin_pane`・`kaji_run`、照会結果側 `pane_id`・`tokens.kaji_origin`・`tokens.kaji_run`）が欠落・型不正。`not_owned`（通過）は両側が有効な文字列で不一致と確認できた場合だけ | 新規（A7、安全側） |

triage の `recoverability_hint` では拒否しない（D4）。`kaji_bug_suspected` / `dispatch_failure` 等の
その他の cause は通過させる。budget（`RECOVERY_BUDGET`）は適用しない（D6）。

### 使用例

```bash
# 監視: 2 Issue の事実を 1 回で取得
kaji status --json 540 541 | jq '.issues[] | {issue, alive: .latest_run.process.alive, t: .latest_run.termination}'

# 保守者: 失敗 step から 1 回やり直す
kaji recover .kaji/wf/official/dev.yaml 540 --retry-failed-step
case $? in
  4) echo "gate refused → 保守者へ" ;;   # stderr の retry-gate: 行で理由を読む
  0) echo "child completed" ;;
  *) echo "child failed or input error" ;;
esac

# series: 失敗 member を失敗 step から再開し残りまで流す
kaji run-series .kaji/series/lane-a.yaml --resume
kaji status --json --series lane-a | jq '.series.stop_reason'
```

### エラー

| 失敗 | 挙動 |
|------|------|
| artifact 破損（status） | 項目ごとに null + `read_errors`、exit 0（D3） |
| artifact 破損（retry / series） | ゲート `artifact_unreadable` で拒否（retry exit 4 / series exit 1） |
| 生存判定不能 | status は `alive: null`（Issue 全体は `any_run_alive: null`）、retry / series は対象 run なら `liveness_unknown`、他の run・launch record なら `issue_liveness_unknown` で拒否（A1） |
| 同時要求 | 判定中の lock 競合は `concurrent_launch`、lock 解放後は先行 launcher の記録で `issue_run_alive` |
| Herdr 照会失敗 | status は Herdr を呼ばない。retry / series は `agent_pane_unverifiable` で拒否 |
| child 起動の OSError | retry: exit 3（`retries[].outcome="launch_failed"`）。series: 既存どおり member `failed` + `SeriesRuntimeError`（exit 3） |
| 生存記録・lock の書き込み失敗 | 「記録の書き込み失敗の規則」の表のとおり。列挙対象を作る書き込みの失敗は先へ進まない（`kaji run` は provider 解決前に exit 3、retry / series は child 未起動で exit 3） |

## 制約・前提条件

- 生存判定は Linux の `/proc` を前提に pid 再利用まで検知する。`/proc` がない OS では pid が存在すると
  `alive: null` になり、retry / series resume は拒否される（安全側）。
- 「最新の run」は run_id（ローカル時刻 `%y%m%d%H%M%S` + 同秒 suffix）の辞書順。kaji は呼び出し側の時計と
  比較しないため #1910 型（呼び出し側と run 側の時計比較）は起きない。同一ホストで時計が巻き戻った場合の
  順序逆転は既存 `select_target_run_dir` と同じ制約として残す。
- `session-state.json` は Issue 単位（run 単位ではない）。`worktree_dir` / `branch_name` は Issue の最新 state を出す。
- 既存の triage 経路（`kaji recover` の `--retry-failed-step` なし、`kaji run` 終了時の handler）の判定順・
  decision・Issue コメント・`select_target_run_dir` の挙動は変えない（`_safety_gates` の戻り値型の内部変更のみ）。
- `SeriesState` / `MemberState` の永続 schema と `Literal` は変えない（D9: 新 status なし）。
- 記録の書き込み失敗は「記録の書き込み失敗の規則」に従う。列挙対象を作る書き込み（`kaji run` の provider 解決前の launch record、launcher record、LaunchLock）は失敗したら先へ進まない（exit 3、child 未起動）。既に launch record がある状態での追記（`run_id` 設定・`process.json`・削除）は warning で続行する。
- このため artifacts dir に書けない環境では、`kaji run` は provider 解決前に exit 3 で止まる（従来も run dir 採番で失敗していた条件。失敗の時点が provider 解決より前に移る）。
- launch record は正常終了時に削除し、kill で残った記録は生存判定で false になる。別ホストで書かれた記録（artifacts を共有する構成）は `other_host`（null）となり、run は終端 event があれば `finished_by_log` で false になるが、kill で残った別ホストの launch record や未終端の run は retry / resume を拒否し続ける。artifacts をホスト間で共有する構成は対象外とし docs に明記する。
- LaunchLock（`flock`）はローカルファイルシステムを前提とする（NFS 上の `flock` の挙動は保証しない）。
- 依存追加なし（psutil 等は使わない）。

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/runstate/__init__.py`（新規） | 公開 API の再 export |
| `kaji_harness/runstate/process.py`（新規） | `ProcessRecord` / `write_process_record` / `read_process_record` / `probe_liveness`（`finished_by_log` / `zombie` を含む） |
| `kaji_harness/runstate/launch.py`（新規） | launch record の書き込み・`run_id` 設定・削除・列挙、`LaunchLock`（`fcntl.flock`） |
| `kaji_harness/runstate/facts.py`（新規） | `RunFacts` / `IssueFacts` / `collect_run_facts` / `collect_issue_facts` / `latest_run_dir`（生存・最新 run・失敗 step・未終了 step・termination・必須 artifact 判定・session_id 解決の唯一の実装） |
| `kaji_harness/runstate/schema.py`（新規） | `STATUS_SCHEMA_VERSION = 1`、facts → JSON dict の変換、ReadError |
| `kaji_harness/recovery/gates.py`（新規） | `handler.py` の `_resume_point` / `_safety_gates` / `_non_resumable_skill_hits` を移設し `GateHit(name, detail)` を返す形にする。`RetryPlanner` / `RetryPlan` |
| `kaji_harness/recovery/handler.py` | gates を import して利用（triage の挙動は不変）。`_child_argv` 相当の argv 構築を gates と共有 |
| `kaji_harness/recovery/snapshot.py` | `allow_unterminated` / `probe_git` 引数（既定は現状どおり）。未終了 step と必須 artifact の判定は `runstate.facts` を呼ぶ。`chain_index` / `trigger` の読み取り |
| `kaji_harness/recovery/classify.py` | `workflow_end` も `failure_event` も無い snapshot → `process_vanished`（`allow_unterminated` の経路でのみ到達） |
| `kaji_harness/recovery/models.py` | `FailureCause` / `FAILURE_CAUSES` に `process_vanished`、`RecoveryDecision.retries`（optional）、`write_recovery_chain` に `chain_index` / `trigger` |
| `kaji_harness/commands/run.py` | config 探索と artifacts 解決の直後（provider 解決より前）に `role=run` の launch record を書き（失敗したら exit 3 で provider 解決へ進まない）、終了時に削除 |
| `kaji_harness/runner.py` | run dir 採番直後に `process.json` を書き、launch record に `run_id` を設定。chain 書き込みに `chain_index` / `trigger` |
| `kaji_harness/commands/status.py`（新規）/ `parser.py` / `main.py` | `kaji status` |
| `kaji_harness/commands/recover.py` / `parser.py` | `--retry-failed-step`、exit 4 |
| `kaji_harness/commands/exit_codes.py` | `EXIT_RETRY_REFUSED = 4` |
| `kaji_harness/series/runner.py` | `--resume` 時の failed / interrupted member 処理、`_execute_member` に `from_step` / chain 引数、全 member 起動の前後で launcher launch record |
| `kaji_harness/interactive_terminal_herdr.py` | ゲート用の read-only 照会関数（pane get + process-info + 所有 token 確認）を公開関数として切り出す |

## 方針

### データフロー

```
           ┌──────────────────────── runstate.facts (唯一の判定実装) ─────────────────────────┐
artifacts ─┤ latest_run_dir / collect_run_facts(run_dir) → RunFacts(termination, liveness,       │
           │ failed_step, last_step, failure, worktree/branch, pane, recovery, read_errors)      │
           └──────────────┬──────────────────────────┬───────────────────────────┬─────────────┘
                          │                          │                           │
                 kaji status (JSON/表)      recovery.gates.RetryPlanner   series runner --resume
                                            (+ snapshot/classify +        (RetryPlanner, trigger=
                                             git + Herdr read-only)        series_resume)
                                                     │                           │
                                         kaji recover --retry-failed-step  _execute_member(--from …)
```

- `collect_run_facts` は `process.json` → `probe_liveness`、`run.log` → `workflow_start` / `step_start` /
  `step_end` / `workflow_end` / `failure_event`、最新 attempt の `result.json` / `pane-metadata.json`、
  `recovery-chain.json`、Issue の `session-state.json` を読む。例外を投げず読めない項目を `read_errors` に積む。
- `collect_issue_facts` は全 run の `process.json` と `launches/` の全 launch record を生存判定し、`any_run_alive` /
  `launching` を求める。
- `failure` は `collect_snapshot(..., probe_git=False, allow_unterminated=<termination == vanished>)` →
  `classify_failure` で得る（classify は git を入力にしないことを確認済み）。status が呼ぶ git は artifact の場所の
  解決に使う `git worktree list --porcelain` だけ。
- `RetryPlanner.plan(target_run_id | None)`（LaunchLock の中で実行）:
  1. `collect_issue_facts` で全 run・全 launch record の facts と最新 run を得る（ゲート 1〜6、5b）
  2. 対象 run の `collect_snapshot(allow_unterminated=<termination == vanished>, probe_git=True)` →
     `classify_failure` → `_resume_point` → ゲート 7〜14
  3. Herdr pane 照会（ゲート 15〜16）
  4. `RetryPlan(gates, resume_from, discarded_resume_session, argv, root_run_id, chain_index)` を返す
- `kaji recover --retry-failed-step`: plan → `recovery.json` / `run.log` に記録 → 拒否なら exit 4、通過なら
  `child_launcher(argv, workdir)` を前面で実行 → 結果を記録 → child の exit code を返す。待機なし。
- series runner: `--resume` の failed / interrupted member で Issue 状態を確認 → planner → `_execute_member(state,
  index, extra_argv=["--from", resume_from, "--recovery-root", ..., "--recovery-parent", ...])`。retry 記録は
  対象 run の `recovery.json` に trigger=`series_resume` で残す。以降の exit / gate 判定は既存どおり。

### 実行中判定の #1909 / #1910 への対応

- #1909: series は child 起動後に `child_pid` を保存し、child の `kaji run` は provider 解決と state 読み込みの後に
  run dir を作る。この区間を 2 つの launch record で塞ぐ。(1) series runner（launcher）が `Popen` 前に書く記録、
  (2) child の `kaji run` が provider 解決より前に書く記録。kaji status は series の `child_pid` ではなくこれらと
  `process.json` で生存を判定するので、run dir が無い区間でも `any_run_alive: true` / `launching: true` を返す。
  series 外の `kaji run --from` も (2) により同じ判定になる。
- #1910: 最新 run は run_id の順序で決め、呼び出し側の時計や series `started_at`（UTC）と run_id（ローカル時刻）を
  比較しない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 置き場所 | 事実とゲート判定は kaji 本体の CLI で公開 | 保守者の決定（Issue 本文「重要判断」置き場所、2026-11-08 EPIC #535） | `runstate/` を唯一の判定実装とし 3 入口が共有 |
| status CLI の形 | `kaji status <issue>...` / `--series`、既定は表、`--json` | 保守者の決定 D1（Issue「決定事項」） | 表は非契約、JSON のみ契約と明記 |
| schema 互換性 | 整数 `schema_version`、追加は互換、削除・型/null 意味/enum 変更で上げる | 保守者の決定 D2 | v1 の項目表と null の意味を定義 |
| status の失敗表現・終了コード | 項目ごと null + `read_errors`、exit 0 / 2 / 3 | 保守者の決定 D3 | `ReadError` 形式。series state.json の欠落・破損・一部不正も exit 0 + フィールド単位の null（A10 を D3 に合わせて改訂）。exit 2 は series id の形式違反のみ |
| retry の CLI | `kaji recover ... --retry-failed-step [--run-id]`、`--auto-recover` と排他、待機なし前面起動 | 保守者の決定 D4 | argparse 排他 group、`RetryPlanner` |
| retry の終了コード | 入力/config 2・3、拒否 4、起動後は child の code | 保守者の決定 D5 | `EXIT_RETRY_REFUSED=4`、`retry-gate:` 行の正規表現 |
| budget | retry には適用しない。最新 run ゲートで同一 run 1 回 | 保守者の決定 D6 | `chain_index` / `trigger` を chain に追加 |
| 外部書き込み | recovery.json と run.log のみ | 保守者の決定 D7 | `retries` 配列（additive）、run.log event 3 種 |
| series `--resume` 既定 | failed/interrupted は失敗 step から。旧挙動なし。CHANGELOG に非互換 | 保守者の決定 D8 | member × Issue 状態の挙動表 |
| closed/not_planned | 失敗扱いで stopped、新 status なし | 保守者の決定 D9 | stop_reason 文言に `mismatch:closed/<reason>` |
| series ゲート停止の exit | exit 1、`stop_reason` にゲート名 | 保守者の決定 D10 | `gates=` 形式の正規表現 |
| 生存記録の方式（A1） | `process.json` + launch record（pid + `/proc` 開始 ticks + boot_id + hostname）、不明は拒否（対象 run は `liveness_unknown`、他の run・launch record は `issue_liveness_unknown`） | AI の仮定 A1（Issue 記載）。根拠: 現状 pid 記録なし、`/proc` 開始時刻で pid 再利用を検知可能。検査先: issue-review-design | basis 値の表（zombie・EPERM・probe 中の消滅を含む）、provider 解決前の書き込みタイミング |
| `workflow_end` なし run の対象化（A2） | `vanished`（alive=false）なら retry / resume 対象 | AI の仮定 A2。検査先: issue-review-design / issue-review-code | 失敗 step = 最後の未終了 `step_start` |
| enum / 項目名（A3） | `termination`: running/ended/vanished/unknown、その他項目名 | AI の仮定 A3。検査先: issue-review-design | 決定表 |
| GitHub 状態を status に含めない（A4） | 含めない。Herdr も status では呼ばない | AI の仮定 A4。検査先: issue-review-design | pane は metadata のみ |
| agent 名・session id（A5） | `last_step.agent` / `session_id` を出す。ログパスは出さない | AI の仮定 A5。検査先: issue-review-design | session_id は当該 attempt の `result.json` → resume step の `step_start.session_id` → null の順。Issue 単位の `session-state.json` は使わない |
| ゲート名（A6） | 既存識別子を基に一覧表の全項目（0〜16、5b）を公開 | AI の仮定 A6。検査先: issue-review-design / i-dev-final-check | 一覧表と評価順 |
| 旧 pane ゲート（A7） | 失敗 step の最新 attempt の kaji 所有 pane に agent が残れば拒否。#532 の holder close とは別 | AI の仮定 A7。検査先: issue-review-design | Herdr 照会不能も拒否（安全側） |
| dry-run / validate-series（A8） | 変えない | AI の仮定 A8。検査先: issue-review-code | 既存テスト維持 |
| A9（設計で追加）: retry child の自動 triage | child は chain の一員なので、その終了時 triage は既存 budget 規則（`is_recovery_child` → `exhausted`）に従う | AI の仮定。根拠: Issue 完了条件「child は既存 recovery chain に記録」と D7「child の triage は通常の kaji run と同じ設定」。budget 規則自体は変えない。検査先: issue-review-design | chain_index で回数を読める |
| A10（設計で追加・改訂）: series state の欠落と引数の誤りの境界 | series id の形式違反だけ exit 2。形式が正しければ state.json の欠落（dir ごとの欠落を含む）・破損・一部不正はすべて exit 0 + null / `[]` + `read_errors` | D3 の適用（保守者の決定「artifact が壊れている・ないときは読めない項目だけ null、exit 0」）。形式違反の判定は既存 `SeriesConfig.id` の正規表現。検査先: issue-review-design | state.json の状態ごとの出力表 |
| A11（設計で追加）: run のない failed member | `no_target_run` で停止（最初からやり直さない） | AI の仮定。根拠: D8「旧挙動を残さない」、安全側。検査先: issue-review-design | — |
| A12（設計で追加）: `workflow_mismatch` ゲート | 対象 run の workflow 名と指定 workflow が違えば拒否 | AI の仮定。根拠: 別 workflow の同名 step から再開する誤りを防ぐ。検査先: issue-review-design | — |
| A14（設計で追加）: 起動前区間と同時要求の閉じ方 | launch record（`kaji run` は provider 解決前、launcher は `Popen` 前）と Issue 単位の LaunchLock（`flock`）。lock 競合は `concurrent_launch` | AI の仮定。根拠: `runner.py` は provider 解決・state 読み込みの後に run dir を作るため run dir 内の記録だけでは起動直後を観測できない（review-design R1 の実測）。`SeriesLock` は series 単位で recover と排他しない。D7 の「記録」は retry の結果記録で、launch record / lock は全 `kaji run` が書く協調用ローカル artifact（GitHub 書き込みなし）と解釈。検査先: issue-review-design | 手順 1〜6、記録の書き込み失敗の規則（列挙対象を作る書き込みは成功を続行の前提とする）、手動 `kaji run` は lock を取らない（従来どおりゲートなし） |
| A15（設計で追加）: 記録で判定できない run の生存 | 記録なし（旧 run）・別ホスト・検証不能で null になる run のうち、run.log 上で以降の動作が終わったもの（COMPLETE、非 resume の `recovery_decision`、`recovery_attempt_end`）は `finished_by_log`（false）。それ以外は null で、retry / resume を拒否する | AI の仮定。根拠: 旧 run の handler はこれらの event の後に worktree 操作や run 起動をしない（`handler.py` の `_resume` は `recovery_attempt_end` 後に結果コメントだけを投稿）。閾値を使わない決定的な条件。検査先: issue-review-design | 未終了の旧 run が残る Issue は、旧 run が終わるか再起動（`rebooted`）までゲートで止まる。その間は従来どおり手動 `kaji run --from` が使える |
| A16（設計で追加）: vanished run の分類 | 新 cause `process_vanished`（source `external`、hint `unknown`）。triage 外の経路でのみ出る | AI の仮定。根拠: 既存 `FailureCause` に kill / OOM 推定の値がなく、`unknown_external_error` 等で代用すると status の `failure.cause` が誤読される。v1 schema の初版に含めるので D2 の enum 変更に当たらない。検査先: issue-review-design | 必須 artifact 表、`allow_unterminated` |
| A13（設計で追加）: series の `interrupted` member と `user_interrupted` ゲート | series resume にも同じゲートを適用するため、直近 run の cause が `user_interrupted`（Ctrl-C）の member は resume で停止する | AI の仮定。根拠: Issue 完了条件「`failed` / `interrupted` の member を resume したとき、上の安全ゲートを通したうえで」と、ゲート一覧の「中断（`KeyboardInterrupt`）」の字義どおりの適用。series 側だけ緩める例外は Issue に根拠がないため作らない。緩める変更は後から互換に足せる（拒否 → 通過）。検査先: issue-review-design | docs に「Ctrl-C で止めた member は `user_interrupted` で停止する」と明記 |

one-way door の未決: なし。公開契約（CLI 形・schema 互換性・終了コード・`--resume` の非互換変更・closed 扱い）は
D1〜D10 で保守者が決定済み。本設計の追加分（A9〜A16、項目名）は release 前なら安く直せる two-way door。

## テスト戦略

### 変更タイプ

- 実行時コード変更

### 実行時コード変更の場合

#### Small テスト

- `probe_liveness` の basis 表の全行（`os.kill` / `/proc` 読み取り / hostname / boot_id を注入）。pid 再利用（開始 ticks 不一致）・zombie（state `Z` / `X`）・probe 中の消滅（`/proc/<pid>/stat` の `ENOENT`）が false、`EPERM` は存在扱いで ticks 照合に進むこと
- `finished_by_log` の 3 条件（COMPLETE / 非 resume の `recovery_decision` / `recovery_attempt_end`）で false（`no_record` / `other_host` / `unverifiable` のそれぞれから）、`workflow_end` なし・ERROR で handler 終端 event なし・`recovery_decision=resume` だけの run は null のまま、`pid_verified` の true には適用されないこと
- 未終了 step の導出: `step_start` だけの attempt、同 step の attempt 2 が未終了、全 step が `step_end` 済み（→ `unknown_failed_step`）
- `session_id` の解決順（R6）: fresh 成功（result.json の ID）、fresh 失敗で診断用 ID が result.json に残る、resume step 実行中（result.json なし・`step_start.session_id` あり）、fresh 実行中（両方なし → null）、Issue 単位 `session-state.json` に同 step の別 ID が残っていても使わないこと
- `classify_failure` が `allow_unterminated` の snapshot を `process_vanished` に分類し、既定（`allow_unterminated=False`）では従来どおり「`workflow_end` event missing」の read error になること
- `termination` 決定表の全 6 行（run.log 可読/不可読 × alive true/false/null × workflow_end 有無）。null 1 つに「unknown」と「破損」がまとまらないこと
- 最新 run 選択: 同秒 suffix（`-002`）、series `started_at`（UTC）と run_id（ローカル時刻）がずれていても最新 run が変わらないこと（#1910 形）
- `RetryPlanner` のゲート一覧の全ゲート（`concurrent_launch` / `issue_liveness_unknown` を含む）それぞれについて「そのゲートだけが拒否する」最小入力で拒否されること、全通過で `resume_from` が失敗 step（`resume:` を持つ step では session 生成元 step）になること
- `recoverability_hint="no"` の cause（例: `unknown_external_error`）でも拒否しないこと、budget 消費済み run でも拒否しないこと（D4 / D6）
- JSON 変換: 全項目の型、`schema_version == 1`、member status enum が `MemberState` の `Literal` と一致すること（schema の enum と実装の乖離を検出）
- `retry-gate:` 行と `stop_reason` の `gates=` 形式が契約の正規表現に一致すること
- 既存 `plan_recovery` のテスト（`tests/test_recovery_plan.py`）が無変更で通ること（gates 移設の回帰）

#### Medium テスト

- `collect_issue_facts` を tmp の artifacts（run.log / process.json / result.json / pane-metadata.json / session-state.json / recovery-chain.json）で検証。各ファイルの不在・不正 JSON・必須 key 欠落で該当項目だけ null と `read_errors`、他項目は出ること
- #1909 形: series state の member が `pending` / `failed` のまま、run dir に生存 pid（テスト中に起動した `sleep` 子プロセス）の `process.json` と `workflow_end` なし run.log → status は `running`、retry / series resume は `run_alive` で拒否。プロセス終了後は `vanished` になり retry 可能
- series 外の `kaji run --from` 相当（recovery-chain なしの新 run）が生存中のとき、旧 run への retry が `newer_run_detected` / `issue_run_alive` で拒否されること
- `kaji status` の読み取り専用性: 実行前後で artifacts / worktree / `.git`（index・worktrees 配下を含む）のファイルツリー（パス・mtime・内容 hash）が不変、`launches/.lock` を作らない、`get_provider` / Herdr 呼び出しの注入点が一度も呼ばれない、記録した git の argv が `worktree list --porcelain` だけであること
- R4: tmp の git repo + `git worktree add` の feature worktree、相対 `paths.artifacts_dir` の config で、main worktree と feature worktree の双方から `kaji status --json <issue>` / `--series <id>` を実行し、同じ main 側 artifacts を読んで同じ出力（`generated_at` を除く）になること。上の不変性も両方で確認する
- R1（起動前区間）: `cmd_run` の provider による Issue 解決を `threading.Event` で止める同期点を注入し、止まっている間に status が `any_run_alive: true` / `launching: true` / `latest_run: null`（run なし）を返し、retry が `issue_run_alive` で拒否されること。series runner の `member_launcher` を同期点で止め、`Popen` 前の launcher record により同じ結果になること（`child_pid` 未保存の区間）
- R1（同時要求）: `RetryPlanner` のゲート判定直後に同期点を置き、1 件目が lock を保持している間の 2 件目が `concurrent_launch`、1 件目が launcher record を書いて lock を解放した後の 3 件目が `issue_run_alive` になること。retry / retry と retry / series resume の両方の組合せで、child の起動が 1 回だけであること
- R1（記録の書き込み失敗の注入）: 書き込み関数に `OSError` を注入して次を固定する。(a) `kaji run` の provider 解決前の launch record 失敗 → exit 3、provider 解決の注入点が呼ばれない、run dir と `session-state.json` が作られない。(b) `run_id` 設定失敗・`process.json` 失敗 → run は続行し、実行中の status が `any_run_alive: true`、retry が `issue_run_alive` で拒否、終了後は `process.json` 欠落の run が `liveness_unknown`（終端条件なし）または `finished_by_log` になる。(c) retry の LaunchLock の lock file 失敗・launcher record 失敗・起動直前の `recovery.json` 記録失敗 → exit 3、`child_launcher` が呼ばれない、lock が解放されて次の planner が lock を取れる、launcher record が残らない。(d) series の通常起動と resume で launcher record 失敗 → `member_launcher` が呼ばれない、member の status が変わらない、series が `stopped` で exit 3。(e) child 終了後の記録失敗 → warning で child の exit code を返す
- R2（Issue 全体の不明）: 対象 run は vanished で、他の run の `process.json` が `other_host` / `record_unreadable`（いずれも run.log が終端条件を満たさない）、または記録なし・`workflow_end` なしの旧 run、または判定不能な launch record がある場合に、status は `any_run_alive: null`、retry は `issue_liveness_unknown`、series resume は stopped + `gates=issue_liveness_unknown` になること。他の run が `finished_by_log` だけなら通過すること
- R3（kill 相当）: 生存していない pid の `process.json`、run.log が `[workflow_start, step_start(implement, attempt 1)]`、`attempt-001/` に result.json なし、worktree / branch が一致する session-state.json の fixture で、status は `termination: vanished` / `failure.cause: process_vanished` / `read_errors: []`、retry と series resume は `--from implement` で起動すること。同じ fixture で `step_end` がある attempt の result.json を消すと `artifact_unreadable` になること。`--retry-failed-step` なしの `kaji recover` は同じ fixture で従来どおり exit 2（未終了 run の拒否）であること
- R5（series state）: 形式が正しい id で、state.json だけ欠落・`_series/<id>/` ごと欠落・JSON 構文破損・`members` が配列でない・1 member の `status` が enum 外、の各 fixture で「state.json の状態ごとの出力」表どおりの値と exit 0。形式違反の id（例 `Bad_ID`）は exit 2
- `kaji recover --retry-failed-step`（`cmd_recover` を `child_launcher` 注入で実行）: 拒否時 exit 4・child 未起動・stderr のゲート行・`recovery.json.retries[-1].outcome == "refused"`。通過時 argv が `--from <step> --recovery-root <root> --recovery-parent <run_id>`、child の exit code（0 / 1 / 3）をそのまま返す、既存 triage 記録が保持され `retries` が追記されること。provider の書き込み系メソッドが呼ばれないこと（D7）
- `--retry-failed-step` と `--auto-recover` の同時指定が exit 2
- `runner` が run dir 採番直後に `process.json` を書き、child の `recovery-chain.json` に `chain_index` / `trigger` が入ること
- series runner（既存 `tests/test_series_*` の fake provider / launcher 注入）: failed member + open Issue → `--from` 付き起動、closed/completed → `completed` で次へ、closed/not_planned → stopped + `mismatch:closed/not_planned`、ゲート拒否 → member 未起動・stopped・`gates=`、`completed` member の巻き戻し検知が従来どおり abort、`pending` member は `--from` なしで起動
- Herdr ゲート: Herdr 照会関数を fake にし、active / confirmed_shell_only / unknown / 照会失敗 / pane 不在 / 非所有 pane の各ケース
- artifact 破損・欠落の 3 入口固定（完了条件「共通」2 件目）: 同じ壊れた fixture（run.log 不正 JSON 行のみ、result.json 欠落、session-state.json 不正）に対し、status は exit 0 + 該当項目 null + `read_errors`、retry は exit 4 + `artifact_unreadable`、series resume は stopped + `gates=artifact_unreadable` になること

#### Large テスト

- `large_local`: 既存 `tests/test_recovery_e2e_large_local.py` の方式（実 `kaji run` subprocess + `exec:` step workflow + PATH 上の fake `gh`）で、2 番目の exec step が初回だけ失敗する workflow を実行 → `kaji status --json` が `ended` / `ERROR` / `failed_step` を返す → `kaji recover --retry-failed-step` が child を `--from` で起動し exit 0、child の `recovery-chain.json` に chain が記録される。続けて同じ run への 2 回目の retry が exit 4（`newer_run_detected`）
- `large_local`: `kaji status` を実 subprocess で起動し、壊れた run.log を含む artifacts で exit 0 と `read_errors`、不正引数で exit 2
- `large_local`（R1）: fake `gh` が flag file を待って Issue 解決で止まるようにし、実 `kaji run` を background で起動 → `kaji status --json` を poll して `any_run_alive: true` / `launching: true` を観測 → flag を置いて終了させ、終了後に launch record が削除されて `any_run_alive: false` になること
- `large_local`（R3）: `exec:` step が sleep する実 `kaji run` を起動し、step 実行中に `kaji run` のプロセスグループを SIGKILL → `kaji status --json` が `vanished` / `process_vanished` → `kaji recover --retry-failed-step` が child を `--from <その step>` で起動して exit 0（2 回目は flag file で成功させる）
- `large_local`（R4）: tmp repo の feature worktree と main worktree の双方から実 `kaji status --json` を起動し、同じ main 側 artifacts の結果になること
- `large_local`: 既存 `tests/test_series_cli_large_local.py` の方式で `run-series --dry-run` / `validate-series` の出力と終了コードが不変であること（A8 の回帰）
- `large_local`: 実 `kaji run-series` subprocess + `exec:` step のみの member workflow + PATH 上の fake `gh`（member Issue の状態を scripted に返す）で、1 番目の member の 2 番目の step が初回だけ失敗 → series が exit 1 で stopped → `--resume` で同 member が `--from <失敗 step>` で再開して完了し、2 番目の member まで completed になること。child run の `recovery-chain.json` に `trigger=series_resume` が残ること
- 実 GitHub / 実 Herdr / 実 agent を使う Large は追加しない。理由: retry / status は GitHub に書き込まず（D7 / A4）、series が使う `view_issue` の実 API 疎通は既存の provider テストが保護している。Herdr の照会は既存の read-only 関数（`_get_herdr_pane` / `_get_herdr_process_info` / `_classify_herdr_process_liveness`）を再利用し、新規ロジックは分類結果からゲートへの写像だけなので Medium の fake で全分岐を網羅できる（testing-convention「既存ゲートで不具合パターンを捕捉できる」）。実 agent を含む series resume の確認は Issue の「ワークフロー完了後の確認項目」（release 後の利用側確認）に分離済み

### 長時間 acceptance の配置

- 該当なし。追加する検証はすべて pytest 内で完結し、実 agent・nested full workflow・外部 agent dogfood を含まない。
  Large の `kaji run` は `exec:` step のみ（既存 E2E の実績で 1 ケース数秒）で、`implement` step の timeout の 25% を大きく下回る。
  release 後の利用側 series resume 確認は Issue 本文で workflow 完了後の確認項目に分離されている。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新ライブラリ・新技術選定なし。schema 互換性方針は D2 として Issue に記録済みで、正本は CLI ガイドに置く |
| docs/ARCHITECTURE.md | あり | `runstate/` パッケージと `recovery/gates.py` の責務、`process.json` artifact を追記 |
| docs/dev/ | あり | `docs/dev/workflow_guide.md` の series 記述（`--resume` の新挙動） |
| docs/reference/ | あり | `docs/reference/configuration*.md` の run-series / artifact 記述（`process.json`） |
| docs/cli-guides/ | あり | 新規 `run-status.md` / `run-status.ja.md`: `kaji status` の JSON schema v1・null の意味・`termination` 決定表・終了コード・D2 の互換性契約。`failure-recovery.md` / `.ja.md`: `--retry-failed-step`・ゲート一覧・exit 4・`retry-gate:` 行・`retries`。`github-mode.md` / `.ja.md`: run-series `--resume` の新挙動と `gates=` 形式の `stop_reason` |
| docs/README.md | あり | 新規 `run-status*.md` の索引追加 |
| CHANGELOG | あり | `run-series --resume` の非互換変更（D8）、`kaji status`、`--retry-failed-step` |
| AGENTS.md / CLAUDE.md | なし | repo 内 agent 規約の変更なし |
| README.md / llms.txt | あり（軽微） | サブコマンド一覧に `status` を追加（記載がある場合） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #540 本文・決定事項 D1〜D10 / A1〜A8 | https://github.com/apokamo/kaji/issues/540 | 公開契約（CLI 形・schema 互換・終了コード・budget・外部書き込み・`--resume` 既定・closed 扱い）の正本 |
| Issue #540 `## grill-me provenance` コメント | 同上（コメント） | D1〜D10 の採用理由と不採用案 |
| 既存 target 選択 | `kaji_harness/recovery/target.py` `select_target_run_dir` | `workflow_end` なしを「still in progress」として拒否、最新 = `sorted()[-1]` |
| 既存ゲート | `kaji_harness/recovery/handler.py` `_resume_point` / `_safety_gates` / `_non_resumable_skill_hits` / `plan_recovery` | ゲート名（`worktree_unavailable` 等）と判定順、`resume:` step の巻き戻し |
| recovery 契約 | `kaji_harness/recovery/models.py` | `RECOVERY_BUDGET=1`、`NON_RESUMABLE_SKILLS`、`FailureCause`、`write_recovery_chain` |
| snapshot | `kaji_harness/recovery/snapshot.py` `collect_snapshot` | `failed_step = failure_event.step_id`、`workflow_end` 欠落を read error 扱い、git 照会 |
| classify | `kaji_harness/recovery/classify.py` | cause の判定順。git state を入力にしない |
| series | `kaji_harness/series/runner.py` / `state.py` / `models.py` | `_execute_member` は `--from` なし、`_reconcile_resume`、`evaluate_member_gate`、`MemberState.status` / `SeriesState.status` の `Literal` |
| run dir / logger | `kaji_harness/runner.py` `allocate_run_dir`（`RUN_ID_FORMAT="%y%m%d%H%M%S"`）、`kaji_harness/logger.py`（`ts` は UTC、`step_start` に agent / session_id / attempt） | 最新 run の順序と status 項目の出所 |
| exit code | `kaji_harness/commands/exit_codes.py` | 既存 0 / 1 / 2 / 3。4 は未使用 |
| series の例外と exit code | `kaji_harness/errors.py`（`SeriesAbortedError` / `SeriesRuntimeError` はともに `HarnessError`）、`kaji_harness/commands/series.py` `cmd_run_series` | `SeriesAbortedError` は先に捕捉されて exit 1、`SeriesRuntimeError` は `HarnessError` として exit 3。series の書き込み失敗時の exit 3 の根拠 |
| Herdr 照会 | `kaji_harness/interactive_terminal_herdr.py` `_get_herdr_pane` / `_get_herdr_process_info` / `_classify_herdr_process_liveness`、`pane-metadata.json`（`pane_id` / `origin_pane` / `kaji_run`） | agent pane の生存を active / confirmed_shell_only / unknown で分類済み |
| Linux `/proc/<pid>/stat` | https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html | 第 22 欄 `starttime`: boot 後のプロセス開始時刻（clock ticks）。pid 再利用の検知に使う |
| Linux `boot_id` | https://man7.org/linux/man-pages/man4/random.4.html（/proc interfaces）、https://man7.org/linux/man-pages/man5/proc_sys_kernel.5.html | `/proc/sys/kernel/random/boot_id` は boot ごとに新しく生成される UUID（random(4) の /proc interfaces 節） |
| Linux `/proc/<pid>/stat` 第 3 欄 | https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html | `state`: `Z` = zombie、`X` = dead。kill(2) の signal 0 は zombie にも成功するため別途判定する |
| `flock(2)` / Python `fcntl.flock` | https://man7.org/linux/man-pages/man2/flock.2.html、https://docs.python.org/3/library/fcntl.html#fcntl.flock | `LOCK_EX \| LOCK_NB` は他が保持中なら `EWOULDBLOCK` で即失敗。プロセス終了で fd が閉じると解放される（kill されても lock が残らない） |
| `git worktree list --porcelain` | https://git-scm.com/docs/git-worktree#_list | worktree の一覧を出力するだけの読み取りコマンド。既存 `resolve_artifacts_dir` → `resolve_main_worktree` が使う |
| run の起動順序 | `kaji_harness/runner.py` `WorkflowRunner.run()`（`_resolve_run_issue_context` → `get_provider` → `_load_session_state` → `allocate_run_dir`） | run dir は provider 解決と state 読み込みの後に作られる（R1 の根拠） |
| session ID の流れ | `kaji_harness/runner.py`（`step.resume` のときだけ `state.get_session_id` を `step_start` に渡す、dispatch の戻り値 `result.session_id` を `save_session_id` と `result.json` に書く）、`logger.py` `log_step_end`（session_id を持たない） | R6 の解決順の根拠 |
| artifact の場所 | `kaji_harness/artifacts.py` `resolve_artifacts_dir` | 相対 `artifacts_dir` は main worktree 基準。feature worktree の `config.artifacts_dir` とは異なる（R4） |
| `kill(2)` | https://man7.org/linux/man-pages/man2/kill.2.html | signal 0 は存在確認のみ。`ESRCH` = プロセスなし、`EPERM` = 存在するが権限なし |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 定義と省略条件 |
