# [設計] workflow failure triage と 1 recovery chain 1 回限定の自動再開

Issue: #288

## 概要

`kaji run` が `ERROR` または triage 対象の `ABORT` で終了したとき、run artifact を根拠に原因を
機械分類して Issue コメント・`recovery.json`・`run.log`・stderr サマリに証跡を固定し、whitelist
条件を満たす場合のみ **1 recovery chain につき 1 回だけ**、固定 10 分ウェイト後に child run を
自動起動する failure triage / recovery handler を導入する。

## 背景・目的

### ユーザーストーリー

- workflow 運用者として、`ERROR` / 復旧可能性のある `ABORT` の発生時に、原因・証拠・再開可否・
  次アクションが Issue コメントと端末に自動で残ってほしい。失敗時の判断を artifact 探索から
  始めずに済むようにするため。
- workflow 運用者として、一時的な agent / CLI 異常で止まった run は、安全に再開できる場合だけ
  10 分待ってから自動再開してほしい。長い workflow の単発失敗で毎回介入せずに済み、かつ同じ
  一時障害で唯一の recovery budget を即時消費しないようにするため。
- maintainer として、同一 recovery chain で自動再開が 2 回以上起きないこと、および recovery の
  予定時刻・開始時刻・成否が `recovery.json` / `run.log` / Issue コメントから追跡できることを
  保証したい。誤分類のまま workflow が前進する事故を防ぎ、10 分ウェイトの効果を運用データで
  検証するため。
- maintainer として、kaji harness 側のバグが強く疑われる失敗は bug issue として起票されてほしい。
  運用中の異常を再現可能な開発タスクへ変換するため。

### 現状の問題と層構造

既存の失敗対応は attempt-level retry（`kaji_harness/cli.py` の `_MAX_RETRIES = 3` /
`_TRANSIENT_PATTERNS` / 指数バックオフ `_BASE_DELAY = 30.0`）のみで、それを使い切った後・
retry 対象外の失敗後の判断は人間依存である。本設計では層を次のように固定する
（docs/dev/workflow_guide.md にも明記する）。

| 層 | 対象 | 時間スケール | 実装 |
|----|------|-------------|------|
| **attempt retry**（既存） | 1 step dispatch 内の transient CLI failure | 数十秒〜数分、in-process | `cli.py` `execute_cli()` |
| **run recovery**（新規） | workflow process の `ERROR` / triage 対象 `ABORT` 終端 | 10 分ウェイト + 新規 `kaji run`、1 chain 1 回 | 本設計の recovery handler |

### 代替案と不採用理由

- **workflow YAML に recovery step を常時挿入する** — 全 workflow の step 列が複雑化し、
  `ERROR`（step 遷移外の例外終端）を YAML 遷移では捕捉できない。不採用（Issue 決定 16）。
- **LLM agent による原因調査** — agent の会話内記憶を根拠にした誤再開リスク
  （一次情報の tsuchi #38 事例）があり、初期実装では pure code に限定する（Issue 決定 6）。
- **`--reset-cycle` の自動付与** — cycle exhaust は `max_iterations` という安全弁の正常作動で
  あり、自動解除は無制限 retry 禁止の実質迂回になる。triage コメントの手動 next action 提示に
  留める（Issue 決定 14）。

## インターフェース

### 入力

#### CLI（`kaji run` への追加 flag）

| flag | 型 | default | 説明 |
|------|-----|---------|------|
| `--failure-triage` / `--no-failure-triage` | bool | config 値（下記） | failure triage（分類 + Issue コメント + `recovery.json` / `run.log` 記録 + stderr サマリ）の有効/無効 |
| `--auto-recover` / `--no-auto-recover` | bool | config 値（下記） | `decision: resume` 時の child run 自動起動の有効/無効。triage 無効時は無効 |
| `--recovery-root <run_id>` | str | なし | recovery chain の root run_id。**handler が child run 起動時に付与する内部伝播用**（手動指定も可） |
| `--recovery-parent <run_id>` | str | なし | 直接の親 run_id。同上。`--recovery-root` なしでの単独指定は `EXIT_DEFINITION_ERROR` |

override 適用は既存 `_apply_execution_overrides()`（`cli_main.py`）と同じ precedence
（CLI flag > `.kaji/config.local.toml` > `.kaji/config.toml`）。

#### CLI（新規 subcommand `kaji recover`）

```
kaji recover <workflow.yaml> <issue> [--run-id <run_id>] [--auto-recover] [--workdir <dir>]
```

- 失敗 run artifact に対して handler を手動起動する（テスト・再調査・opt-in 再開の入口）。
- `--run-id` 省略時は `<artifacts_dir>/<issue>/runs/` の最新 run を対象とする。
- 対象 run の `run.log` に `workflow_end` event（status `ERROR` / `ABORT`）が無い場合は
  `EXIT_INVALID_INPUT (2)` で停止する（実行中 run への誤介入防止）。
- `<workflow.yaml>` は resume command 構築に用いる（対象 run と同じ workflow を指定する責務は
  運用者側。`recovery.json` に workflow path を記録し、再入時に照合する）。

#### config（`[execution]` セクションへの追加 key）

| key | 型 | default | validation |
|-----|-----|---------|-----------|
| `failure_triage` | bool | `true` | 非 bool は `ConfigLoadError` |
| `auto_recover` | bool | `false` | 非 bool は `ConfigLoadError` |

- `auto_recover_max_attempts` は **config 化しない**。モジュール定数 `RECOVERY_BUDGET = 1` 固定
  （Issue 決定 1）。
- recovery wait も **config 化しない**。モジュール定数 `RECOVERY_WAIT_SECONDS = 600` 固定
  （Issue 決定 9）。テスト用に handler のコンストラクタ引数 `wait_seconds`（default =
  `RECOVERY_WAIT_SECONDS`）としてのみ注入可能にする（config / CLI へは露出しない）。
- default の根拠: triage は Issue コメントという可視証跡を残すだけで destructive 操作を含まず、
  第一責務（原因調査と証跡固定）の価値がそのまま default で立ち上がるため `true`。auto_recover
  は child run 起動という強い副作用を持つため **default 無効**（Issue 決定 17。default 有効化は
  設計レビューで別途リスク評価しない限り行わない）。

#### handler への入力（in-process）

- 対象 run の artifact 一式: `run.log`（`workflow_end` / `failure_event` / `step_end`）、
  最終 attempt の `result.json` / `verdict.yaml`、`session-state.json`
- `KajiConfig` / provider（`get_provider(config)`）/ workflow path / 起動時 CLI flag
- git state（worktree の現在 branch、`git status --porcelain` 要約）

### 出力

| 出力 | 内容 |
|------|------|
| `<run_dir>/recovery.json` | `RecoveryDecision` の直列化（下記スキーマ）。decision 更新のたびに上書き |
| `<run_dir>/recovery-chain.json` | child run 側が起動直後に書く chain identity `{root_run_id, parent_run_id}`（`--recovery-*` flag がある run のみ） |
| `run.log` 追記 event | `failure_event` / `recovery_decision` / `recovery_scheduled` / `recovery_attempt_start` / `recovery_attempt_end`（`RunLogger` に追加） |
| Issue コメント | 機械生成 triage report（下記テンプレート）。`provider.comment_issue()` 経由。戻り値 `Comment.ref`（下記「comment reference」参照）を stderr サマリと `recovery.json.triage_comment_ref` に記録。kaji-verdict マーカーは**付与しない**（step verdict ではないため。`issue-design` Step 1.6 の BACK 検出母集団を汚さない） |
| bug issue | 条件成立時のみ `provider.create_issue(title="bug: ...", labels=["type:bug"])`。作成した番号/URL を元 Issue コメントと `recovery.json.bug_issue` に記録 |
| stderr サマリ | 既存終端表示（`Error: ...` / `Workflow aborted: ...`）の**直後**に数行追加: `failed_step` / `classification` / `synthetic` / `decision` / `resume_scheduled_at`（resume 時）/ comment ref（取得不能時 `n/a`）/ 次アクション |
| child run | `decision: resume` かつ auto_recover 有効時のみ、10 分ウェイト後に subprocess として `kaji run <workflow> <issue> --from <step> --recovery-root <root> --recovery-parent <run_id>` を起動 |

#### comment reference（`Comment.ref` の追加）

現行の `Comment`（`providers/models.py`）は `author` / `body` / `created_at` / `seq` /
`machine_id` のみで投稿先参照を持たず、GitHub provider の `comment_issue()` は
`gh issue comment` の stdout（作成コメント URL）を捨てている。stderr サマリ・
`recovery.json` に載せる参照値を provider 中立に供給するため、以下を追加する:

- `Comment.ref: str = ""` — 投稿コメントへの provider 中立参照。default `""` の末尾追加
  なので既存生成箇所・既存テストは無変更で互換（frozen dataclass の既存 field 順不変）
- `GitHubProvider.comment_issue()` — `gh issue comment` 成功時の stdout 1 行目
  （作成コメント URL、例 `https://github.com/<owner>/<repo>/issues/288#issuecomment-<id>`）を
  strip して `ref` に格納。stdout が空・URL 形式でない場合は `ref=""`（fail させない）
- `LocalProvider.comment_issue()` — 作成した comment file の **repo-root 相対パス**
  （例 `.kaji/issues/local-pc1-3/comments/20260710T120000Z-pc1.md`）を `ref` に格納
- consumer（handler / stderr サマリ / triage コメント外部の表示）は `ref == ""` を `n/a` と
  表示する。`ref` の形式（URL か path か）には依存しない不透明文字列として扱う

#### exit code（既存 map `0=OK / 1=ABORT / 2=定義エラー / 3=ランタイムエラー` は不変）

- triage のみ（child run 未起動）: 元の失敗の exit code をそのまま返す（既存挙動不変）。
- child run を起動した場合: 親 `kaji run` プロセスは child の exit code を返す
  （語彙は同じ 0/1/2/3。wrapper の総合結果 = chain の最終結果として整合）。
- `kaji recover`: triage 完了（decision が何であれ）= `0`、対象 run 不在 / 進行中 run / flag
  不整合 = `2`、handler 内部エラー = `3`。

#### `recovery.json` スキーマ（`RecoveryDecision`）

```json
{
  "schema_version": 1,
  "run_id": "260710120000",
  "recoverable": true,
  "decision": "resume",
  "classification": {
    "cause": "verdict_resolution_failure",
    "synthetic": true,
    "source": "runner",
    "recoverability_hint": "candidate"
  },
  "failed_step": "review-code",
  "resume_from": "review-code",
  "resume_mode": "from",
  "resume_command": "kaji run .kaji/wf/dev.yaml 288 --from review-code --recovery-root 260710120000 --recovery-parent 260710120000",
  "reason": "VerdictNotFound after successful dispatch; step is re-entrant",
  "evidence": [
    "runs/260710120000/run.log workflow_end status=ERROR error=VerdictNotFound: ...",
    "steps/review-code/attempt-002/result.json error=VerdictNotFound: ...",
    "session-state.json last_completed_step=implement",
    "git: branch feat/288 matches state.branch_name; porcelain: 2 modified"
  ],
  "auto_recovery_attempted": false,
  "auto_recovery_attempt_no": 0,
  "recovery_parent_run_id": null,
  "recovery_root_run_id": "260710120000",
  "recovery_child_run_id": null,
  "recovery_child_final_status": null,
  "resume_scheduled_at": null,
  "resume_started_at": null,
  "discarded_resume_session": false,
  "triage_comment_ref": null,
  "bug_issue": null
}
```

- `decision` の値域: `resume` / `not_resumable` / `exhausted` / `comment_only` /
  `bug_issue_created` / `cancelled_newer_run_detected` / `cancelled_interrupted`
  （最後は 10 分ウェイト中の SIGINT / KeyboardInterrupt で自動再開を中止した状態。
  ウェイト中断を「resume 予定のまま」に見せない）。
- 時刻はすべて UTC ISO 8601。`resume_scheduled_at = decision 確定時刻 + 600s`。
- `triage_comment_ref` は投稿した triage コメントの `Comment.ref`（前掲）。投稿前・投稿失敗・
  `ref` 取得不能時は `null`。
- child run 終了後、親 run の `recovery.json` に `recovery_child_run_id` /
  `recovery_child_final_status`（`COMPLETE` / `ABORT` / `DEFINITION_ERROR` / `ERROR`。child の
  exit code から導出）を書き戻す。child run_id は「`resume_started_at` 以降に作成され、
  `recovery-chain.json.parent_run_id` が自 run_id である run dir」で特定する。

#### Issue コメントテンプレート（機械生成、LLM 不使用）

```markdown
## Workflow failure triage

| 項目 | 値 |
|------|----|
| workflow | `.kaji/wf/dev.yaml` |
| issue | `#288` |
| run_id | `<run_id>` |
| recovery_root_run_id | `<run_id>` |
| recovery_parent_run_id | `<run_id or n/a>` |
| failed_step | `<step>` |
| classification | `<cause>` |
| synthetic | `true/false` |
| decision | `<decision>` |
| auto_recovery | `attempted: true/false, attempt_no: 0/1` |
| resume_command | `<command or n/a>` |
| resume_scheduled_at | `<timestamp or n/a>` |
| discarded_resume_session | `true/false` |
| child_run_status | `<status or pending>` |

### 原因（機械判定）

（cause ごとの固定文面 + 構造化フィールドの埋め込みのみ。自由記述なし）

### 根拠

- `run.log`: ...
- `result.json`: ...
- `session-state.json`: ...
- git state: ...

### 次アクション

（cause 別の固定候補。cycle_exhausted なら `--reset-cycle` を含む手動コマンド例、
resume なら「10 分後に自動再開予定」等）
```

- `decision: resume` ではウェイト開始**前**にこのコメントを即時投稿する（Issue 決定 10）。
- コメント本文は auto-close hazard pattern（`Fixes #N` 等）を含まない固定テンプレートとする
  （docs/dev/shared_skill_rules.md § auto close keyword 回避規約）。
- 分類不能な外部エラーは解釈せず opaque なエラー文字列を引用し、`not_resumable` /
  `comment_only` に落とす（Issue 決定 7）。

### 使用例

```bash
# 1. 通常運用（triage default 有効、auto_recover default 無効）
kaji run .kaji/wf/dev.yaml 288
# → ERROR 終了時: Issue に triage コメント、recovery.json 保存、stderr にサマリ。exit 3

# 2. 自動再開を opt-in
kaji run .kaji/wf/dev.yaml 288 --auto-recover
# → decision: resume なら 10 分後に child run を自動起動。exit code は child のもの

# 3. 失敗 artifact から手動で handler を再実行（調査 / テスト）
kaji recover .kaji/wf/dev.yaml 288 --run-id 260710120000

# 4. handler が内部で起動する child run（運用者は通常直接叩かない）
kaji run .kaji/wf/dev.yaml 288 --from review-code \
  --recovery-root 260710120000 --recovery-parent 260710120000
```

### エラー

| 失敗 | 挙動 |
|------|------|
| run_dir 作成前の失敗（config / workflow validation / IssueContext 解決失敗） | triage 対象外。既存の stderr `Error:` 表示 + exit 2/3 のまま。artifact が第一根拠であり、根拠なしの Issue コメントは投稿しない（Issue 決定 4 の設計確定） |
| triage 中の provider 失敗（コメント投稿不可） | triage は best-effort: `recovery.json` / `run.log` / stderr サマリは残し、コメント失敗を stderr に WARN。元の exit code を変えない。自動再開は**しない**（safety: handler 自身が必要操作を完遂できない） |
| handler が必要 artifact を読めない | `decision: not_resumable`（分類 `kaji_bug_suspected` 候補）。自動再開しない |
| 10 分ウェイト中の SIGINT | child 未起動のまま `decision: cancelled_interrupted` に更新して終了 |
| `--recovery-parent` 単独指定 / root と parent の run dir 不在 | `EXIT_DEFINITION_ERROR (2)` |

## 制約・前提条件

- **前提**: #292（run_id 一意化: `allocate_run_dir` の `mkdir(exist_ok=False)` 採番）と #213
  （CLI transient 判定の stderr 充実）が main 反映済み。両方とも反映済みであることを確認した
  （`runner.py` `allocate_run_dir` / `cli.py` `_TRANSIENT_PATTERNS`）。
- 既存 attempt retry（`cli.py`）の transient 判定と二重実装しない。`_is_transient` 相当を
  公開 helper `is_transient_error_text(text: str) -> bool` として `cli.py` から抽出し、attempt
  retry と recovery classifier の両方が同一 pattern list を参照する。
- 既存 exit code map（`cli_main.py:50-56`）を変更しない。
- `ABORT` の意味論を変更しない（正規 verdict として維持）。verdict 語彙・workflow YAML の
  step 列・skill lifecycle の責務も変更しない。workflow YAML への `recovery:` metadata 追加は
  **初期実装では行わない**（未知 key 検証の追加コストに見合う要件がまだ無い。必要になった
  時点で parser / validator 更新とセットで別途設計する）。
- LLM による深掘り調査・destructive operation の自動実行・`--reset-cycle` 自動付与・無制限
  retry はスコープ外（Issue § スコープ境界）。
- `artifacts_dir` は `config.paths.artifacts_dir` の resolve 値を使う（固定文字列
  `.kaji-artifacts` を仮定しない）。
- run_id は `allocate_run_dir` の `YYMMDDHHMMSS[-NNN]` 形式で辞書順 = 時系列順が成立する
  （同一 artifacts_dir 内比較。世紀跨ぎ・システム時計の後退は考慮外と明記）。newer-run 検出は
  この辞書順比較で行う。
- handler は `interactive_terminal` / `headless` の両 agent_runner で dispatch 経路非依存に
  動作する（workflow 終端の status / artifact のみを見る）。
- 秘匿情報: triage コメント・bug issue 本文にはエラー文字列を引用するため、token 類が stderr に
  混入していた場合の転写リスクがある。引用は各根拠 500 文字上限で切り、`ghp_` /
  `github_pat_` / `Bearer ` 等の credential 形跡を伏字化する masking helper を通す。

## 変更スコープ

| モジュール | 変更 |
|-----------|------|
| `kaji_harness/recovery/`（新規 package） | `models.py`（`FailureClassification` / `RecoveryDecision`）、`classify.py`（純関数 classifier）、`snapshot.py`（artifact 収集）、`report.py`（コメント/サマリ生成の純関数）、`handler.py`（orchestrator: 投稿・ウェイト・child 起動・書き戻し） |
| `kaji_harness/runner.py` | `failure_event` の emit（emit 箇所は 4、kind は 5 種: dispatch/verdict 例外は同一 except 節から `dispatch_exception` / `verdict_exception` を出し分け、他は cycle exhaust・ambiguous worktree・agent ABORT の各 1 箇所）、`last_run_dir` 属性の公開、`--recovery-*` 受領時の `recovery-chain.json` 書き出し |
| `kaji_harness/cli.py` | `_is_transient` を公開 helper `is_transient_error_text` に抽出（挙動不変） |
| `kaji_harness/result.py` | `AttemptResult.synthetic: bool = False` を末尾追加（既存 result.json の後方互換は default で担保） |
| `kaji_harness/logger.py` | `log_failure_event` / `log_recovery_decision` / `log_recovery_scheduled` / `log_recovery_attempt_start` / `log_recovery_attempt_end` を追加 |
| `kaji_harness/config.py` | `ExecutionConfig.failure_triage` / `auto_recover` の追加 + validation |
| `kaji_harness/cli_main.py` | `cmd_run` 終端の handler 呼び出し（例外経路含む）、新 flag、`kaji recover` subcommand、stderr サマリ |
| `kaji_harness/state.py` | 変更なし（recovery 状態は run artifact 側に置く。Issue 単位 state を汚さない） |
| `kaji_harness/providers/models.py` | `Comment.ref: str = ""` を末尾追加（provider 中立の投稿コメント参照。default で後方互換） |
| `kaji_harness/providers/github.py` | `comment_issue()` が `gh issue comment` stdout の作成コメント URL を `Comment.ref` に格納（現状は捨てている） |
| `kaji_harness/providers/local.py` | `comment_issue()` が作成 comment file の repo-root 相対パスを `Comment.ref` に格納 |
| `kaji_harness/providers/base.py` | 変更なし（`comment_issue` / `create_issue` の signature 不変。`Comment` の field 追加のみで IF は不変） |
| `docs/` / `tests/` | 影響ドキュメント・テスト戦略の節を参照 |

## 方針

### 1. 実装形態: A/B 中間（runner は記録のみ、CLI 層 handler が orchestrate）

Issue § 9 の比較結果:

| 観点 | A. runner 内部 | B. 外部 CLI | 採用: A/B 中間 |
|------|---------------|------------|----------------|
| 失敗終端の捕捉精度 | 高いが捕捉点が分散（main loop re-raise / ambiguous ABORT / validation error） | run.log 依存 | runner は `failure_event` を構造化記録するだけ。捕捉点の分散は event emit で吸収 |
| 責務分離 / テスト容易性 | runner が肥大化 | 高い | classifier / report は純関数、handler は `kaji recover` で単体起動可能 |
| 10 分ウェイトの保持 | runner process が長生存 | wrapper 必要 | `cmd_run` の終端（runner.run 復帰後）で handler が sleep。runner 本体は不変 |
| run_dir 不在経路 | 扱える | `--run-id` 起動不能 | run_dir 不在 = triage 対象外と設計で確定（エラー表の通り） |

採用構成: **runner = failure event の構造化記録**（`run.log` へ `failure_event`、`result.json`
へ `synthetic`）、**`cmd_run` 終端 = handler 呼び出し**（成功復帰後の ABORT 判定箇所と
`HarnessError` catch 節の両方から、`runner.last_run_dir` が非 None の場合のみ）、
**`kaji recover` = 同一 handler 関数の手動入口**。handler がさらに `kaji run` を起動する
再入は、child プロセスに `--recovery-root` / `--recovery-parent` を渡すことで chain 側の
budget guard に閉じ込める（下記 4）。

### 2. failure classification（cause 軸 + 直交 `synthetic`）

`FailureClassification`（frozen dataclass）:

```python
cause: FailureCause          # 下表の Literal
synthetic: bool              # failure record が runner 生成か（cause と直交）
source: Literal["runner", "agent", "external", "config"]
recoverability_hint: Literal["candidate", "no", "unknown"]
```

runner が emit する `failure_event`（run.log JSONL）を分類の一次入力とし、**reason 文字列
マッチには依存しない**。emit 点と kind:

| emit 点（runner.py） | kind | synthetic |
|----------------------|------|-----------|
| dispatch/verdict 例外の except 節（既存 `_record_attempt_end` 直前） | `dispatch_exception` / `verdict_exception`（`exception_type` 名を同梱） | true |
| cycle 上限 exhaust の合成 verdict | `cycle_exhausted`（`cycle_name` 同梱） | true |
| ambiguous worktree の pre-loop ABORT | `ambiguous_worktree` | true |
| dispatch された attempt の verdict.status == ABORT | `agent_abort` | false |

classifier（`classify.py` の純関数 `classify_failure(snapshot: FailureSnapshot) ->
FailureClassification`）の決定規則:

| cause | 判定入力 | recoverability_hint |
|-------|---------|---------------------|
| `dispatch_failure` | `failure_event.kind == dispatch_exception`。`exception_type in {StepTimeoutError, CLIExecutionError, ScriptExecutionError, CLINotFoundError}` | `StepTimeoutError`、または `CLIExecutionError` かつ `is_transient_error_text(result.error)` → candidate。他は no |
| `verdict_resolution_failure` | `kind == verdict_exception` | `VerdictNotFound` / `VerdictParseError` → candidate。`InvalidVerdictValue`（プロンプト違反、決定論的再発）→ no |
| `cycle_exhausted` | `kind == cycle_exhausted` | no（`--reset-cycle` を手動 next action として提示） |
| `agent_declared_abort` | `kind == agent_abort` | no（decision は `comment_only`） |
| `ambiguous_worktree_abort` | `kind == ambiguous_worktree` | no |
| `config_or_definition_error` | ERROR + `workflow_end.error` の例外型 ∈ {WorkflowValidationError, MissingResumeSessionError, InvalidTransition, WorkdirNotFoundError} | no |
| `kaji_bug_suspected` | 決定論的矛盾チェック（例: `failure_event` があるのに対応 attempt の `result.json` 欠損 / `session-state.json` が読めない・スキーマ不整合） | no（bug issue 作成候補） |
| `runtime_error` | ERROR で上記いずれにも該当しない例外 | no（`comment_only`） |
| `unknown_external_error` | 外部 CLI / provider 由来と判別できるが pattern 不一致の opaque エラー | no（`comment_only`） |
| `external_upstream_anomaly` | **初期 classifier は emit しない予約値**（セッション異常の機械判定は pure code では不可能。将来の深掘り調査導入時に使用） | — |

`AttemptResult.synthetic` は except 経路の abort record で true、agent ABORT attempt で false を
書き、result.json 単体でも synthetic / agent-declared を区別可能にする（Issue 完了条件）。

### 3. recovery 判定モデル

`RecoveryDecision` は前掲スキーマの dataclass。判定フロー（handler 内、上から順に確定）:

1. `classification.recoverability_hint != candidate` → `not_resumable` / `comment_only`
   （cause 別の固定 mapping）／`kaji_bug_suspected` かつ bug issue 条件成立 → `bug_issue_created`
2. **budget guard**: 対象 run の起動 flag に `--recovery-root` があった（= 自身が recovery
   child）→ `exhausted`。2 回目以降の failure は原因分類が誤っている可能性が高いため停止する
3. **safety gate**（下記 6）のいずれかに抵触 → `not_resumable`（抵触 gate を evidence に記録）
4. auto_recover 無効（config / flag）→ `comment_only`（`resume_command` は提示するが起動しない）
5. すべて通過 → `resume`（`resume_scheduled_at` を確定し、コメント即時投稿 → 10 分ウェイト →
   起動直前再チェック → child 起動）

### 4. recovery chain identity と budget = 1

- chain identity は **CLI flag で伝播**する（state 経由 lookup は採らない: Issue 単位 state に
  run 単位情報を持ち込むと、後日の独立 run で budget が誤共有される）。
  - handler は child を `--recovery-root <root> --recovery-parent <own_run_id>` 付きで起動
  - child の runner は run_dir 作成直後に `recovery-chain.json` を書く
- **budget 判定は「自 run が recovery child か」だけで機械的に決まる**: root flag を持つ run の
  failure handler は無条件 `exhausted`。scan も counter も不要で、「別 run_id だからもう 1 回」
  という抜け道が構造的に消える。
- 手動起動の独立 run（flag なし）は新しい chain の root になり、budget は復活する（明記要件）。
- 親 run の `recovery.json` には `auto_recovery_attempted: true` / `auto_recovery_attempt_no: 1` /
  `recovery_child_run_id` / `recovery_child_final_status` が永続化される。

### 5. 再開点の決定（初期 hardcoded whitelist）

- 原則: `resume_from = failed_step`、`resume_mode = "from"`（`--from <failed_step>`）。
  対象 cause は candidate の 2 系（dispatch_failure の timeout/transient、
  verdict_resolution_failure の NotFound/ParseError）のみ。skill 側は BACK 再入・re-entry を
  前提に設計されており（`issue-design` Step 1.6 等）、同一 step 再実行は許容される。
- **`resume:` step の session 破棄**（Issue 決定 13）: failed_step が `resume:` を持つ場合、
  保存済み session id を引き継ぐ再開はしない。session を消して `--from <failed_step>` すると
  `MissingResumeSessionError` で即死するため、**`resume_from = step.resume`（session 生成元
  step）に巻き戻す**。`discarded_resume_session: true` を記録し、triage コメントに
  「session 引継ぎを破棄し生成元 step から再開」と明記する。
- **副作用 step の denylist**: `NON_RESUMABLE_STEPS = {"issue-start", "i-pr", "issue-close"}`
  （モジュール定数）。failed_step または巻き戻し先がこれに該当したら `not_resumable`
  （irreversible / 外部公開系の副作用 step は自動再開しない）。
- **cycle 内 step への `--from` 再開**は既存 `cycle_counts` を消費したまま行う（`--reset-cycle`
  は自動付与しない）。triage コメントの次アクションに「cycle 残量が不足する場合は手動で
  `--reset-cycle` を検討」と明記する。
- verdict 復元による「再開不要」判定（Issue § 4 優先順位 2）は初期実装では行わない
  （verdict 復元は `resolve_verdict` の comment fallback が既に担う領域で、handler 側での
  二重実装は誤復元リスクが上回る。whitelist を絞る方を優先）。

### 6. safety gate（自動再開しない条件の操作的定義）

以下のいずれかで `not_resumable`（gate 名を evidence に記録）:

1. `state.worktree_dir` が不在 / ディレクトリ実体なし / worktree の現在 branch
   （`git rev-parse --abbrev-ref HEAD`）≠ `state.branch_name`
2. provider context が解決できない（handler 起動時点の `get_provider` / `resolve_issue_context`
   失敗）
3. 失敗エラー文字列に auth / secret / permission 形跡（`credential` / `permission denied` /
   `401` / `403` / `token` 等の固定 pattern list `_SENSITIVE_FAILURE_PATTERNS`）
4. failed_step が `NON_RESUMABLE_STEPS`（irreversible operation 系）
5. budget guard（chain flag あり → `exhausted`）
6. handler が必要 artifact（run.log / result.json / session-state.json）を読めない
7. triage 時点で自 run より新しい run dir が既に存在する（別 run が同時実行中 /
   人間が既に再起動済み）

**未コミット変更の扱い**（操作的定義）: dirty worktree 自体は gate に**しない**。implement 系
step の失敗では未コミット変更が正常であり、一律禁止は最も失敗しやすい step 群で recovery を
無効化するため。初期実装は git state snapshot を持たない代わりに、(a) 再開対象 cause を
transient 系 2 種に絞る、(b) `git status --porcelain` の要約（件数と先頭数行）を
evidence に記録する、で保守性を担保する。snapshot 比較は Phase 2 運用データを見てからの
将来拡張とする。

**並行実行ガード**: lock file / state-level lease は採らない（比較: 既存の手動 `kaji run` は
lock を見ないため、handler 側だけ lock を持っても片側しか守れず、stale lock の解除運用が
新たな失敗モードになる）。代わりに **「recovery は常に新しい run に譲る」単方向 yield** で
競合を解消する:

- triage コメントに `resume_scheduled_at` を明示し、運用者が 10 分窓を Issue 上で把握できる
- ウェイト明けの child 起動**直前**に `runs/` を再走査し、自 run_id より辞書順で新しい run dir
  が存在すれば起動を中止して `decision: cancelled_newer_run_detected` に更新する
  （手動 `kaji run` は起動直後に run dir を作るため、この検査で確実に観測できる）
- 残余 race（再チェックと child 起動の間に手動 run が割り込む数百 ms）は許容し、設計上の既知
  制約として docs に明記する（child 側も同一 Issue の state を追記型で扱うため破壊はしない）

### 7. bug issue 作成条件

作成する（すべて満たす場合）:

- `cause == kaji_bug_suspected`（state / artifact / runner event の決定論的矛盾を検出）
- 矛盾の根拠 artifact path を列挙できる（推測段階では作らない）

作成しない: 外部依存の一時障害（Claude Code / Codex / Gemini / gh / network）、auth /
permission / quota、正当な agent ABORT、dirty worktree の意味が判定不能な場合。外部 agent /
upstream CLI 問題は bug issue にせず元 Issue コメントに調査結果として残す（Issue 決定 15）。

作成時は元 Issue コメントに bug issue 番号/URL・判断根拠・元 run の artifact path・自動再開の
実施有無を含め、`recovery.json.bug_issue` に `{id, url}` を記録する。

### 8. 終了時ターミナルサマリ

`cmd_run` の既存終端表示の直後に stderr で数行出力する（置き換えない）:

```
--- failure triage ---
failed_step:    review-code
classification: verdict_resolution_failure (synthetic=true)
decision:       comment_only
comment:        https://github.com/apokamo/kaji/issues/288#issuecomment-...
next action:    kaji run .kaji/wf/dev.yaml 288 --from review-code
```

`comment:` 行の値は `Comment.ref`（§ comment reference）をそのまま表示する。GitHub provider
では作成コメント URL、local provider では comment file の repo-root 相対パス、投稿失敗・
`ref` 取得不能時は `n/a`。初期実装は triage 対象 failure の要約に限定する。run を止めなかった途中異常（transient retry
で回復した失敗等）のサマリ包含は、`CLIResult` への retry 回数追加が必要になるためスコープ外
とし、必要なら別 Issue で扱う（Issue § 5 の設計論点への回答）。

### 9. 主要データフロー（resume 経路）

```
kaji run（親）
  runner.run() → 例外 or ABORT 終端（failure_event / result.json は記録済み）
  cmd_run 終端: handler 起動（failure_triage 有効時）
    snapshot 収集 → classify_failure() → decision 確定
    recovery.json 保存 + run.log recovery_decision
    Issue コメント即時投稿（resume_scheduled_at 明示）+ stderr サマリ
    decision == resume:
      run.log recovery_scheduled → 600s sleep
      newer-run 再チェック → (新 run あり → cancelled_newer_run_detected で終了)
      run.log recovery_attempt_start → subprocess: kaji run --from <step> --recovery-root --recovery-parent
        child runner: 起動直後に recovery-chain.json 書き出し（以後通常実行。
        child が再失敗しても child 側 handler は budget guard で exhausted 停止）
      child 終了 → 親 recovery.json に child_run_id / child_final_status 書き戻し
        + run.log recovery_attempt_end → 親 exit code = child exit code
```

### 10. Phase 分割（第一候補として採用）

| Phase | 内容 | 理由 |
|-------|------|------|
| Phase 1 | `failure_event` / `synthetic` の構造化記録、classifier、`recovery.json` / `run.log` 記録、Issue コメント、stderr サマリ、bug issue 作成、`kaji recover`（triage のみ） | 再開できないケースでも triage コメントに価値がある。副作用は Issue コメント / bug issue 起票のみでリスクが低い |
| Phase 2 | whitelist 自動再開、chain identity flag、budget guard、10 分ウェイト、起動直前再チェック、child status 書き戻し | 再入・並行実行・chain 管理にリスクが集中する。Phase 1 の運用データで whitelist / wait / default を検証してから有効化できる |

両 Phase とも本 Issue 内の実装順序（コミット段階）であり、別 Issue には分割しない。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

- 実行時コード変更（runner / cli_main / logger / result / config / 新規 recovery package）

### 実行時コード変更の場合

#### Small テスト

- `classify_failure()`: `failure_event` kind / 例外型 → cause・synthetic・source・hint の
  mapping 全分岐（分類表の行ごと）。`external_upstream_anomaly` を emit しないこと
- `is_transient_error_text()`: `cli.py` からの抽出後も既存 pattern の判定が不変であること
  （attempt retry との単一情報源化の回帰）
- `RecoveryDecision` / `FailureClassification` の直列化・schema_version・decision 値域の検証
- budget guard: `--recovery-root` あり → `exhausted`、なし → candidate 継続の純ロジック
- 再開点決定: 通常 step → `--from <failed_step>`、`resume:` step → 生成元 step へ巻き戻し +
  `discarded_resume_session=true`、`NON_RESUMABLE_STEPS` → `not_resumable`
- resume command 文字列の構築（workflow path / issue / flag の合成）
- newer-run 検出の run_id 辞書順比較（`-NNN` suffix 含む）
- コメント本文 / stderr サマリ生成の純関数: テンプレート充足、auto-close hazard pattern
  不含、credential masking、500 文字引用上限、`Comment.ref` 空文字 → `n/a` 表示の fallback
- `resume_scheduled_at = 決定時刻 + 600s` の算出
- `derive` 系: child exit code → `child_final_status` mapping（0/1/2/3）

#### Medium テスト

- runner の `failure_event` emit（emit 箇所 4 / kind 5 種の全組合せ: dispatch 例外 /
  verdict 例外 / cycle exhaust / ambiguous worktree / agent ABORT）が run.log に構造化記録
  されること（tmp fs + stub dispatch。テストケースは kind 5 種を網羅する）
- `AttemptResult.synthetic` が except 経路で true、agent ABORT attempt で false になること。
  旧形式 result.json（synthetic キーなし）の読み込み互換
- handler orchestration（provider mock + subprocess mock + `wait_seconds` 短縮注入）:
  - triage コメント投稿 → sleep → child 起動の順序（コメントがウェイト**前**であること）
  - 同一 chain で child 再失敗時に 2 回目の自動再開が起きないこと（budget guard）
  - ウェイト明け newer-run 検出 → child 未起動 + `cancelled_newer_run_detected` 更新
  - SIGINT（KeyboardInterrupt 注入）→ `cancelled_interrupted`
  - safety gate 各種（branch 不一致 / artifact 欠損 / sensitive pattern / denylist step）
  - child 終了後の `recovery.json` 書き戻し（child_run_id / child_final_status）
- `recovery.json` / `recovery-chain.json` の永続化と再読込、run.log の recovery event 5 種
- bug issue 作成経路: local provider（実 fs）+ GitHub provider mock で
  `create_issue(labels=["type:bug"])` と元 Issue への番号記録
- `Comment.ref` の格納: GitHub provider は `_run_gh` mock の stdout（コメント URL）が
  `ref` に入ること・stdout 空なら `ref=""` になること、local provider は作成 comment file の
  repo-root 相対パスが `ref` に入ること（実 fs）。既存呼び出し（`ref` 未参照）の互換
- config: `failure_triage` / `auto_recover` の型 validation・default・overlay / CLI flag
  precedence（`_apply_execution_overrides` 経由）
- `cmd_run` 終端: 失敗時に handler が呼ばれ、stderr サマリが出力されること（`capsys`）。
  triage のみでは exit code が既存 map から不変であること。run_dir 作成前の失敗では handler が
  呼ばれないこと
- `kaji recover`: 最新 run 解決、進行中 run（workflow_end なし）拒否 = exit 2

#### Large テスト

- `large_local`（subprocess、ネットワークなし、local provider）: 必ず失敗する exec step を持つ
  workflow を `kaji run`（triage 有効）で実行し、(a) `recovery.json` が保存され、(b)
  `.kaji/issues/<id>/comments/` に triage コメントが永続化され、(c) stderr に triage サマリが
  出ることを E2E で 1 ケース検証する。続けて `kaji recover --run-id <該当>` で handler を
  失敗 artifact から再起動できることを確認する（Issue 完了条件の E2E 1 ケース以上に対応）
- 実 GitHub API 疎通（`large_forge`）は追加しない: provider 境界の新規面は
  `gh issue comment` stdout の `ref` 捕捉のみで、stdout 形式は gh CLI の公開仕様
  （Primary Sources 参照）に基づき Medium の `_run_gh` mock で検証する。空 stdout 時も
  `ref=""` → `n/a` に落ちるだけで機能を壊さない。10 分実ウェイトの実時間検証は CI で
  再現不能なため、`wait_seconds` 注入 + Medium での順序検証で代替する
  （`docs/dev/testing-convention.md` の変更固有検証の考え方に基づく省略理由）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規ライブラリ / 外部技術選定なし。アーキテクチャ判断は本設計書と Issue に記録され、final-check で Issue 本文へ添付される |
| docs/ARCHITECTURE.md | あり（軽微） | recovery layer（attempt retry と run recovery の層構造）と `kaji_harness/recovery/` package の追記 |
| docs/dev/workflow_guide.md | あり | failure triage / 1 recovery chain 1 回限定 auto recovery / 固定 10 分ウェイト / 並行実行時は新しい run が優先される運用ルール、`kaji recover` の使い方 |
| docs/dev/workflow-authoring.md | なし | workflow YAML 文法に変更なし（`recovery:` metadata は不採用） |
| docs/reference/configuration.md | あり | `[execution] failure_triage` / `auto_recover` の型・default・validation・precedence を追記 |
| docs/dev/testing-convention.md | なし | 既存 S/M/L 規約の範囲内 |
| docs/reference/python/ | なし | 規約変更なし |
| docs/cli-guides/ | あり | `kaji run` 新 flag と `kaji recover` subcommand の CLI 仕様（github-mode / local-mode どちらにも共通の run 仕様として） |
| AGENTS.md / CLAUDE.md | なし | 開発規約・skill lifecycle に変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| runner 終端処理 | `kaji_harness/runner.py:988-1004` | `last_verdict.status == "ABORT"` で `end_status="ABORT"`、例外時は `end_status="ERROR"` / `end_error=f"{type(exc).__name__}: {exc}"` を `workflow_end` に記録して re-raise。handler の起動条件（ERROR/ABORT 終端）とちょうど対応する |
| synthetic ABORT の既存経路 | `kaji_harness/runner.py:826-878` | dispatch / verdict 例外の except 節で `Verdict(status="ABORT", reason="step aborted without a usable verdict", ...)` を `_record_attempt_end()` に渡して re-raise する。`failure_event` emit と `AttemptResult.synthetic` の挿入点 |
| run_id 一意化（前提 #292） | `kaji_harness/runner.py:61-80` | `allocate_run_dir` が `mkdir(exist_ok=False)` 成功を一意性判定に使い `YYMMDDHHMMSS[-NNN]` を採番。「run_dir が一意でなければ recovery chain の識別単位が成立しない」前提が main で充足済み |
| attempt retry（既存層） | `kaji_harness/cli.py:24-46, 84-111` | `_MAX_RETRIES = 3` / `_BASE_DELAY = 30.0` / `_TRANSIENT_PATTERNS`（#213 の thinking block pattern 含む）と `_is_transient()`。classifier はこれを公開 helper 化して再利用し二重実装しない |
| attempt 終了情報 | `kaji_harness/result.py:55-73` | `AttemptResult` は `status` / `exit_code` / `signal` / `session_id` / `error` を持ち、except 経路では `error=f"{type(exc).__name__}: {exc}"`。`synthetic` field 追加の土台 |
| Issue 単位 state | `kaji_harness/state.py:52-127` | `session-state.json` が `last_completed_step` / `last_transition_verdict` / `cycle_counts` / `worktree_dir` / `branch_name` を保持。safety gate の branch 照合と再開点判断の入力 |
| run logger | `kaji_harness/logger.py:26-151` | JSONL 追記 + 即時 flush の `_write()`。recovery event 5 種は同形式で追加でき、handler が別プロセス時点でも同一 `run.log` に追記可能 |
| exit code map | `kaji_harness/cli_main.py:50-56, 488-510` | `EXIT_OK=0 / EXIT_ABORT=1 / EXIT_DEFINITION_ERROR=2 / EXIT_RUNTIME_ERROR=3` と `cmd_run` の catch 節・`Workflow aborted:` 表示。handler 呼び出しの挿入点と「既存 map 不変」の正本 |
| CLI flag 群 | `kaji_harness/cli_main.py:145-208, 360-382` | `--from` / `--step` / `--before` / `--reset-cycle` の登録と `_apply_execution_overrides` の precedence。新 flag / `kaji recover` は同パターンに従う |
| provider interface | `kaji_harness/providers/base.py:29-62` | `comment_issue(issue_id, body)` / `create_issue(title, body, labels)` が github / local 両実装で利用可能。signature は不変のまま戻り値 `Comment` の field 追加で参照値を供給する |
| Comment model の現状 | `kaji_harness/providers/models.py:24-39` | `Comment` は `author` / `body` / `created_at` / `seq` / `machine_id` のみで投稿先参照を持たない。`ref: str = ""` の末尾追加が frozen dataclass の既存互換を壊さない根拠（既存 field はすべて default 付きで順序不変） |
| GitHub provider の comment_issue | `kaji_harness/providers/github.py:236-244` | `gh issue comment` 実行後に `Comment(author="", body=body, created_at="")` を返し stdout を捨てている。`ref` 捕捉の挿入点 |
| gh CLI のコメント URL 出力 | https://github.com/cli/cli/blob/trunk/pkg/cmd/pr/shared/commentable.go | `createComment` が `api.CommentCreate` 成功後に `fmt.Fprintln(opts.IO.Out, url)` で作成コメント URL を stdout へ出力する（`--quiet` 指定時を除く）。`gh issue comment` は本 shared package（`CommentableRun`）経由でこの経路を通る。GitHub 側 `Comment.ref` の取得可能性の根拠 |
| verdict marker 契約 | `kaji_harness/providers/markers.py:19-57` | kaji-verdict マーカーは step verdict 用の契約。triage コメントに付けないことで `issue-design` Step 1.6 の BACK 検出母集団を汚さない判断の根拠 |
| config 仕様の正本 | `docs/reference/configuration.md` § `[execution]` | 「This file is the Source of Truth for the config spec」。`failure_triage` / `auto_recover` 追加時に本 doc を先に更新する運用ルールの根拠 |
| 再開プリミティブ | `docs/dev/workflow_guide.md` | `--from` / `--before` / `--step` / `--reset-cycle` が既存の途中再開手段として定義済み。resume command はこの語彙のみで構成する |
| セッション異常の実例 | https://github.com/apokamo/tsuchi/issues/38#issuecomment-4915625532 | 実在しないユーザー発話を根拠に AI が動作した異常の後続対応。「当該セッションでの追加作業を避け、新セッションで workflow 起動すれば続行可能」= session 破棄再開（決定 13）の実証事例 |
| 同・調査記録 | https://github.com/apokamo/tsuchi/issues/38#issuecomment-4914605661 | 永続記録と AI コンテキストの乖離検証。「agent の会話内記憶だけを根拠にしない」（決定 5）と triage の artifact 主義の根拠 |
| subprocess 公式 | https://docs.python.org/3/library/subprocess.html | `Popen.returncode`: "A negative value -N indicates that the child was terminated by signal N (POSIX only)."。child run の exit code 回収と `child_final_status` 導出（`subprocess.run(...).returncode`）の根拠 |
