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

### 共通: run の生存記録（新規 artifact）

`kaji run` は run dir 採番直後（`allocate_run_dir` の直後、`run.log` の `workflow_start` より前）に
`runs/<run_id>/process.json` を atomic write する。

| 項目 | 型 | 意味 |
|------|----|------|
| `schema_version` | int | 1 |
| `pid` | int | `kaji run` プロセスの pid |
| `pid_start_ticks` | int \| null | `/proc/<pid>/stat` 第 22 欄（取得不能な OS では null） |
| `boot_id` | str \| null | `/proc/sys/kernel/random/boot_id`（取得不能なら null） |
| `hostname` | str | `socket.gethostname()` |
| `started_at` | str | UTC ISO 8601 |

生存判定 `probe_liveness(record) -> Liveness(alive: bool | None, basis: str)`:

| 条件（上から評価） | `alive` | `basis` |
|--------------------|---------|---------|
| 記録なし（旧 run / 書き込み前） | null | `no_record` |
| 記録が壊れている | null | `record_unreadable`（`read_errors` にも追記） |
| `hostname` が現ホストと異なる | null | `other_host` |
| `boot_id` が両方非 null かつ不一致 | false | `rebooted` |
| `os.kill(pid, 0)` が `ESRCH` | false | `pid_absent` |
| pid 存在 + `pid_start_ticks` 一致 | true | `pid_verified` |
| pid 存在 + `pid_start_ticks` 不一致 | false | `pid_reused` |
| pid 存在 + 開始時刻を検証できない（macOS 等） | null | `unverifiable` |

`process.json` は自動復旧 handler（`workflow_end` 後の triage・10 分待機・child 起動）を含む
`kaji run` プロセス全体の生存を表す。

### 1. `kaji status`（新規サブコマンド、読み取り専用）

```
kaji status [--workdir DIR] [--json] <issue> [<issue> ...]
kaji status [--workdir DIR] [--json] --series <series_id>
```

| 入力 | 型 | 必須 | 説明 |
|------|----|------|------|
| `<issue>` | str（`153` / `gh:153`） | `--series` と排他でどちらか必須 | `normalize_id` で正規化（provider 呼び出しなし） |
| `--series` | str（series id） | 同上 | `<artifacts>/_series/<id>/state.json` を読む |
| `--json` | flag | 任意 | JSON 出力。省略時は人向けの表（表の形式は契約にしない） |
| `--workdir` | path | 任意 | 既存サブコマンドと同じ config 探索起点 |

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
| `id` | str | — |
| `status` | enum `running` / `stopped` / `completed` | state.json が読めない |
| `stop_reason` | str \| null | 停止理由なし、または読めない |
| `updated_at` | str \| null | 読めない |
| `members[]` | object: `issue`(int) / `workflow`(str) / `status`(enum `pending` / `running` / `interrupted` / `completed` / `failed`) / `child_pid` / `run_id` / `exit_code` / `gate` / `started_at` / `finished_at` | 各値は `MemberState` の値をそのまま出す（null は state 上の未設定） |
| `read_errors[]` | ReadError | — |

`IssueStatus`:

| 項目 | 型 | null の意味 |
|------|----|-------------|
| `issue` | str | — |
| `latest_run` | RunStatus \| null | run が 1 件もない |
| `any_run_alive` | bool \| null | Issue のいずれかの run の `process.alive` が true なら true。全 run が false なら false。true がなく null が含まれれば null |
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
| `failed_step` | str \| null | 失敗 step がない（running / COMPLETE）または判定不能 |
| `failure` | `{"cause": str, "source": str, "recoverability_hint": str}` \| null | `termination` が `ended`（ERROR/ABORT）または `vanished` 以外 |
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

**終了コード（D3）**: 0 = 出力完了（run の失敗・artifact 破損を含む）。2 = 引数の誤り
（issue と `--series` の両方/どちらもなし、不正な issue id、series state が存在しない）・config なし。
3 = 予期しないエラー。

**外部書き込みなし**: ファイル・GitHub・Herdr・git index への書き込みを行わない。provider を構築せず、
git / Herdr のコマンドも呼ばない。

### 2. `kaji recover --retry-failed-step`

```
kaji recover <workflow> <issue> --retry-failed-step [--run-id <id>] [--workdir DIR]
```

| 入力 | 説明 |
|------|------|
| `--retry-failed-step` | 新規 flag。`--auto-recover` / `--no-auto-recover` と排他（argparse の mutually exclusive group） |
| `--run-id` | 省略時は Issue の最新 run を対象にする |

処理: `RetryPlanner.plan()` でゲートを判定 → 通過なら待機なしで child run を前面起動
（`kaji run <workflow> <issue> --from <resume_from> --recovery-root <root> --recovery-parent <run_id>`）。

**終了コード（D5）**:

| 状況 | exit |
|------|------|
| workflow 不在・定義エラー・config なし・provider 解決不能・`--run-id` の run dir 不在・引数排他違反 | 既存 2 |
| 予期しない内部エラー（artifact 書き込み失敗等） | 既存 3 |
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
  {"requested_at": "...", "trigger": "retry_failed_step" /* or "series_resume" */,
   "outcome": "launched" /* "refused" | "launch_failed" */, "gates": [{"name": "run_alive", "detail": "pid=..."}],
   "resume_from": "implement", "discarded_resume_session": false,
   "child_run_id": "261112...", "child_exit_code": 0}
]
```

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

`--dry-run` / `validate-series` の出力・終了コードは変えない（A8）。

### 安全ゲート一覧（公開契約、A6）

`RetryPlanner` が評価し、拒否ゲートをすべて列挙する（最初の 1 件で打ち切らない）。名前は既存
`_safety_gates` / `_non_resumable_skill_hits` / `FailureCause` の識別子を基にする。

| # | name | 拒否条件 | 由来 |
|---|------|---------|------|
| 1 | `no_target_run` | `--run-id` 省略時に run が 1 件もない | 新規 |
| 2 | `newer_run_detected` | 対象 run より新しい run がある（指定 run が最新でない） | 既存 |
| 3 | `run_alive` | 対象 run の `process.alive == true` | 新規 |
| 4 | `liveness_unknown` | 対象 run の `process.alive == null`（A1: 安全側） | 新規 |
| 5 | `issue_run_alive` | 同じ Issue の他の run の `process.alive == true` | 新規 |
| 6 | `run_not_failed` | `termination == ended` かつ `end_status` が ERROR / ABORT 以外 | 新規（既存 target の拒否を移設） |
| 7 | `artifact_unreadable` | run.log / result.json / session-state.json が読めない | 既存 |
| 8 | `workflow_mismatch` | `workflow_start.workflow` と指定 workflow の `name` が異なる | 新規 |
| 9 | `unknown_failed_step` | 失敗 step を特定できない、または workflow に存在しない | 既存 |
| 10 | `non_resumable_skill` | 失敗 step / 実再開先が `NON_RESUMABLE_SKILLS` | 既存 |
| 11 | `agent_declared_abort` / `cycle_exhausted` / `user_interrupted` / `ambiguous_worktree_abort` | triage の cause がこれら | `FailureCause` |
| 12 | `sensitive_failure_pattern` | 失敗のエラー文字列が sensitive pattern に一致 | 既存 |
| 13 | `worktree_unavailable` / `branch_mismatch` | worktree がない / branch が state と不一致 | 既存 |
| 14 | `provider_unavailable` | provider を解決できない（retry / series では provider 解決失敗が起動前の exit 2 / 3 になるため通常は発火しない。triage と共有する `_safety_gates` の一部として残す） | 既存 |
| 15 | `agent_pane_alive` | 失敗 step の最新 attempt の Herdr pane が kaji 所有で、前景に agent プロセスが残る | 新規（A7） |
| 16 | `agent_pane_unverifiable` | pane-metadata はあるが Herdr を照会できない / liveness が `unknown` | 新規（A7、安全側） |

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
| 生存判定不能 | status は `alive: null`、retry / series は `liveness_unknown` で拒否（A1） |
| Herdr 照会失敗 | status は Herdr を呼ばない。retry / series は `agent_pane_unverifiable` で拒否 |
| child 起動の OSError | retry: exit 3（`retries[].outcome="launch_failed"`）。series: 既存どおり member `failed` + `SeriesRuntimeError`（exit 3） |

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
- `process.json` 書き込み失敗は run を止めない（warning のみ）。その run は `liveness_unknown` 扱いになる。
- 依存追加なし（psutil 等は使わない）。

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/runstate/__init__.py`（新規） | 公開 API の再 export |
| `kaji_harness/runstate/process.py`（新規） | `ProcessRecord` / `write_process_record` / `read_process_record` / `probe_liveness` |
| `kaji_harness/runstate/facts.py`（新規） | `RunFacts` / `IssueFacts` / `collect_run_facts` / `collect_issue_facts` / `latest_run_dir`（生存・最新 run・失敗 step・termination の唯一の実装） |
| `kaji_harness/runstate/schema.py`（新規） | `STATUS_SCHEMA_VERSION = 1`、facts → JSON dict の変換、ReadError |
| `kaji_harness/recovery/gates.py`（新規） | `handler.py` の `_resume_point` / `_safety_gates` / `_non_resumable_skill_hits` を移設し `GateHit(name, detail)` を返す形にする。`RetryPlanner` / `RetryPlan` |
| `kaji_harness/recovery/handler.py` | gates を import して利用（triage の挙動は不変）。`_child_argv` 相当の argv 構築を gates と共有 |
| `kaji_harness/recovery/snapshot.py` | 失敗 step の導出に「`workflow_end` なし・最後の `step_start` に対応する `step_end` なし」を追加（`allow_unterminated` 指定時のみ）。`chain_index` / `trigger` の読み取り |
| `kaji_harness/recovery/models.py` | `RecoveryDecision.retries`（optional）、`write_recovery_chain` に `chain_index` / `trigger` |
| `kaji_harness/runner.py` | run dir 採番直後に `process.json` を書く。chain 書き込みに `chain_index` / `trigger` |
| `kaji_harness/commands/status.py`（新規）/ `parser.py` / `main.py` | `kaji status` |
| `kaji_harness/commands/recover.py` / `parser.py` | `--retry-failed-step`、exit 4 |
| `kaji_harness/commands/exit_codes.py` | `EXIT_RETRY_REFUSED = 4` |
| `kaji_harness/series/runner.py` | `--resume` 時の failed / interrupted member 処理、`_execute_member` に `from_step` / chain 引数 |
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
- `failure` は `collect_snapshot(..., probe_git=False)` → `classify_failure` で得る（classify は git を入力に
  しないことを確認済み）。status は git を呼ばない。
- `RetryPlanner.plan(target_run_id | None)`:
  1. `collect_issue_facts` で全 run の facts と最新 run を得る（ゲート 1〜6）
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

- #1909: series は child 起動後に `child_pid` を保存するため、直後の窓では state.json が古い status のまま。
  kaji status は series の `child_pid` ではなく run 自身の `process.json` で生存を判定するので、この窓でも
  `running` を返す。series 外の `kaji run --from` も同じ判定になる。
- #1910: 最新 run は run_id の順序で決め、呼び出し側の時計や series `started_at`（UTC）と run_id（ローカル時刻）を
  比較しない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 置き場所 | 事実とゲート判定は kaji 本体の CLI で公開 | 保守者の決定（Issue 本文「重要判断」置き場所、2026-11-08 EPIC #535） | `runstate/` を唯一の判定実装とし 3 入口が共有 |
| status CLI の形 | `kaji status <issue>...` / `--series`、既定は表、`--json` | 保守者の決定 D1（Issue「決定事項」） | 表は非契約、JSON のみ契約と明記 |
| schema 互換性 | 整数 `schema_version`、追加は互換、削除・型/null 意味/enum 変更で上げる | 保守者の決定 D2 | v1 の項目表と null の意味を定義 |
| status の失敗表現・終了コード | 項目ごと null + `read_errors`、exit 0 / 2 / 3 | 保守者の決定 D3 | `ReadError` 形式、series state 不在を exit 2 とした（下の AI 仮定 A10） |
| retry の CLI | `kaji recover ... --retry-failed-step [--run-id]`、`--auto-recover` と排他、待機なし前面起動 | 保守者の決定 D4 | argparse 排他 group、`RetryPlanner` |
| retry の終了コード | 入力/config 2・3、拒否 4、起動後は child の code | 保守者の決定 D5 | `EXIT_RETRY_REFUSED=4`、`retry-gate:` 行の正規表現 |
| budget | retry には適用しない。最新 run ゲートで同一 run 1 回 | 保守者の決定 D6 | `chain_index` / `trigger` を chain に追加 |
| 外部書き込み | recovery.json と run.log のみ | 保守者の決定 D7 | `retries` 配列（additive）、run.log event 3 種 |
| series `--resume` 既定 | failed/interrupted は失敗 step から。旧挙動なし。CHANGELOG に非互換 | 保守者の決定 D8 | member × Issue 状態の挙動表 |
| closed/not_planned | 失敗扱いで stopped、新 status なし | 保守者の決定 D9 | stop_reason 文言に `mismatch:closed/<reason>` |
| series ゲート停止の exit | exit 1、`stop_reason` にゲート名 | 保守者の決定 D10 | `gates=` 形式の正規表現 |
| 生存記録の方式（A1） | `process.json`（pid + `/proc` 開始 ticks + boot_id + hostname）、不明は拒否 | AI の仮定 A1（Issue 記載）。根拠: 現状 pid 記録なし、`/proc` 開始時刻で pid 再利用を検知可能。検査先: issue-review-design | basis 値の表、書き込みタイミング |
| `workflow_end` なし run の対象化（A2） | `vanished`（alive=false）なら retry / resume 対象 | AI の仮定 A2。検査先: issue-review-design / issue-review-code | 失敗 step = 最後の未終了 `step_start` |
| enum / 項目名（A3） | `termination`: running/ended/vanished/unknown、その他項目名 | AI の仮定 A3。検査先: issue-review-design | 決定表 |
| GitHub 状態を status に含めない（A4） | 含めない。Herdr も status では呼ばない | AI の仮定 A4。検査先: issue-review-design | pane は metadata のみ |
| agent 名・session id（A5） | `last_step.agent` / `session_id` を出す。ログパスは出さない | AI の仮定 A5。検査先: issue-review-design | `step_start` event の値を正とする |
| ゲート名（A6） | 既存識別子を基に 16 項目を公開 | AI の仮定 A6。検査先: issue-review-design / i-dev-final-check | 一覧表と評価順 |
| 旧 pane ゲート（A7） | 失敗 step の最新 attempt の kaji 所有 pane に agent が残れば拒否。#532 の holder close とは別 | AI の仮定 A7。検査先: issue-review-design | Herdr 照会不能も拒否（安全側） |
| dry-run / validate-series（A8） | 変えない | AI の仮定 A8。検査先: issue-review-code | 既存テスト維持 |
| A9（設計で追加）: retry child の自動 triage | child は chain の一員なので、その終了時 triage は既存 budget 規則（`is_recovery_child` → `exhausted`）に従う | AI の仮定。根拠: Issue 完了条件「child は既存 recovery chain に記録」と D7「child の triage は通常の kaji run と同じ設定」。budget 規則自体は変えない。検査先: issue-review-design | chain_index で回数を読める |
| A10（設計で追加）: series state 不在 | `kaji status --series` で state.json 不在は exit 2、破損は exit 0 + `read_errors` | AI の仮定。根拠: D3「引数の誤り = 2」。存在しない id は引数の誤り。検査先: issue-review-design | — |
| A11（設計で追加）: run のない failed member | `no_target_run` で停止（最初からやり直さない） | AI の仮定。根拠: D8「旧挙動を残さない」、安全側。検査先: issue-review-design | — |
| A12（設計で追加）: `workflow_mismatch` ゲート | 対象 run の workflow 名と指定 workflow が違えば拒否 | AI の仮定。根拠: 別 workflow の同名 step から再開する誤りを防ぐ。検査先: issue-review-design | — |
| A13（設計で追加）: series の `interrupted` member と `user_interrupted` ゲート | series resume にも同じゲートを適用するため、直近 run の cause が `user_interrupted`（Ctrl-C）の member は resume で停止する | AI の仮定。根拠: Issue 完了条件「`failed` / `interrupted` の member を resume したとき、上の安全ゲートを通したうえで」と、ゲート一覧の「中断（`KeyboardInterrupt`）」の字義どおりの適用。series 側だけ緩める例外は Issue に根拠がないため作らない。緩める変更は後から互換に足せる（拒否 → 通過）。検査先: issue-review-design | docs に「Ctrl-C で止めた member は `user_interrupted` で停止する」と明記 |

one-way door の未決: なし。公開契約（CLI 形・schema 互換性・終了コード・`--resume` の非互換変更・closed 扱い）は
D1〜D10 で保守者が決定済み。本設計の追加分（A9〜A12、項目名）は release 前なら安く直せる two-way door。

## テスト戦略

### 変更タイプ

- 実行時コード変更

### 実行時コード変更の場合

#### Small テスト

- `probe_liveness` の basis 表の全行（`os.kill` / `/proc` 読み取り / hostname / boot_id を注入）。pid 再利用（開始 ticks 不一致）が false になること
- `termination` 決定表の全 6 行（run.log 可読/不可読 × alive true/false/null × workflow_end 有無）。null 1 つに「unknown」と「破損」がまとまらないこと
- 最新 run 選択: 同秒 suffix（`-002`）、series `started_at`（UTC）と run_id（ローカル時刻）がずれていても最新 run が変わらないこと（#1910 形）
- `RetryPlanner` のゲート 16 種それぞれについて「そのゲートだけが拒否する」最小入力で拒否されること、全通過で `resume_from` が失敗 step（`resume:` を持つ step では session 生成元 step）になること
- `recoverability_hint="no"` の cause（例: `unknown_external_error`）でも拒否しないこと、budget 消費済み run でも拒否しないこと（D4 / D6）
- JSON 変換: 全項目の型、`schema_version == 1`、member status enum が `MemberState` の `Literal` と一致すること（schema の enum と実装の乖離を検出）
- `retry-gate:` 行と `stop_reason` の `gates=` 形式が契約の正規表現に一致すること
- 既存 `plan_recovery` のテスト（`tests/test_recovery_plan.py`）が無変更で通ること（gates 移設の回帰）

#### Medium テスト

- `collect_issue_facts` を tmp の artifacts（run.log / process.json / result.json / pane-metadata.json / session-state.json / recovery-chain.json）で検証。各ファイルの不在・不正 JSON・必須 key 欠落で該当項目だけ null と `read_errors`、他項目は出ること
- #1909 形: series state の member が `pending` / `failed` のまま、run dir に生存 pid（テスト中に起動した `sleep` 子プロセス）の `process.json` と `workflow_end` なし run.log → status は `running`、retry / series resume は `run_alive` で拒否。プロセス終了後は `vanished` になり retry 可能
- series 外の `kaji run --from` 相当（recovery-chain なしの新 run）が生存中のとき、旧 run への retry が `newer_run_detected` / `issue_run_alive` で拒否されること
- `kaji status` の読み取り専用性: 実行前後で artifacts / worktree のファイルツリー（パス・mtime・内容 hash）が不変、`get_provider` / git subprocess / Herdr 呼び出しの注入点が一度も呼ばれないこと
- `kaji recover --retry-failed-step`（`cmd_recover` を `child_launcher` 注入で実行）: 拒否時 exit 4・child 未起動・stderr のゲート行・`recovery.json.retries[-1].outcome == "refused"`。通過時 argv が `--from <step> --recovery-root <root> --recovery-parent <run_id>`、child の exit code（0 / 1 / 3）をそのまま返す、既存 triage 記録が保持され `retries` が追記されること。provider の書き込み系メソッドが呼ばれないこと（D7）
- `--retry-failed-step` と `--auto-recover` の同時指定が exit 2
- `runner` が run dir 採番直後に `process.json` を書き、child の `recovery-chain.json` に `chain_index` / `trigger` が入ること
- series runner（既存 `tests/test_series_*` の fake provider / launcher 注入）: failed member + open Issue → `--from` 付き起動、closed/completed → `completed` で次へ、closed/not_planned → stopped + `mismatch:closed/not_planned`、ゲート拒否 → member 未起動・stopped・`gates=`、`completed` member の巻き戻し検知が従来どおり abort、`pending` member は `--from` なしで起動
- Herdr ゲート: Herdr 照会関数を fake にし、active / confirmed_shell_only / unknown / 照会失敗 / pane 不在 / 非所有 pane の各ケース
- artifact 破損・欠落の 3 入口固定（完了条件「共通」2 件目）: 同じ壊れた fixture（run.log 不正 JSON 行のみ、result.json 欠落、session-state.json 不正）に対し、status は exit 0 + 該当項目 null + `read_errors`、retry は exit 4 + `artifact_unreadable`、series resume は stopped + `gates=artifact_unreadable` になること

#### Large テスト

- `large_local`: 既存 `tests/test_recovery_e2e_large_local.py` の方式（実 `kaji run` subprocess + `exec:` step workflow + PATH 上の fake `gh`）で、2 番目の exec step が初回だけ失敗する workflow を実行 → `kaji status --json` が `ended` / `ERROR` / `failed_step` を返す → `kaji recover --retry-failed-step` が child を `--from` で起動し exit 0、child の `recovery-chain.json` に chain が記録される。続けて同じ run への 2 回目の retry が exit 4（`newer_run_detected`）
- `large_local`: `kaji status` を実 subprocess で起動し、壊れた run.log を含む artifacts で exit 0 と `read_errors`、不正引数で exit 2
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
| Herdr 照会 | `kaji_harness/interactive_terminal_herdr.py` `_get_herdr_pane` / `_get_herdr_process_info` / `_classify_herdr_process_liveness`、`pane-metadata.json`（`pane_id` / `origin_pane` / `kaji_run`） | agent pane の生存を active / confirmed_shell_only / unknown で分類済み |
| Linux `/proc/<pid>/stat` | https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html | 第 22 欄 `starttime`: boot 後のプロセス開始時刻（clock ticks）。pid 再利用の検知に使う |
| Linux `boot_id` | https://man7.org/linux/man-pages/man5/proc_sys_kernel.5.html | `/proc/sys/kernel/random/boot_id` は boot ごとに変わる ID |
| `kill(2)` | https://man7.org/linux/man-pages/man2/kill.2.html | signal 0 は存在確認のみ。`ESRCH` = プロセスなし、`EPERM` = 存在するが権限なし |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 定義と省略条件 |
