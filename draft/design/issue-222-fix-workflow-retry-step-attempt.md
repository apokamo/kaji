# [設計] step attempt の終了情報（result.json）構造化保存と run.log / progress.md の attempt 可視化

Issue: #222

## 概要

kaji workflow の各 step attempt について、終了情報（`status` / `exit_code` /
`signal` / 時刻 / `duration_ms` / `session_id`）を attempt 単位の構造化ファイル
`result.json` として保存し、`run.log` の step イベントに attempt 識別子と
exit_code / signal を付与し、`progress.md` に failed / aborted attempt を可視化する。
143 / SIGTERM / timeout / interruption のような異常終了でも best-effort で残す。

## 背景・目的

### Observed Behavior（OB） — 一次証跡

すべて main（`a28a0b8` 時点）と実 run の観測に基づく。#220 (#221, `a28a0b8`) が
attempt-NNN layout を導入済みで、`console.log` / `stdout.log` / `prompt.txt` /
`verdict.yaml` の **ログ上書き自体は解消済み**である（`kaji_harness/runner.py:45-69`
`allocate_attempt_dir`）。本 Issue の残課題は #220 が手をつけていない以下のギャップ。

**1. 終了情報の構造化ファイル（result.json）が存在しない**

- 全 artifact 横断: `find .kaji-artifacts -name result.json | wc -l` → `0`
- コード側にも書き出し実装が無い: `grep -rn "result.json\|result_json" kaji_harness/ --include=*.py` → 0 件
- 結果として attempt 単位で `status` / `exit_code` / `signal` / `duration_ms` /
  `session_id` を復元できない。

**2. `run.log` の step イベントに attempt 識別子が無い**

- 実 run `.kaji-artifacts/222/runs/2606042200/run.log` の `step_start` / `step_end`
  には `attempt` キーが無い。`attempt` を持つのは `verdict_source` のみ
  （`kaji_harness/logger.py:79-85` の `log_verdict_source`）。
- そのため run.log だけでは「どの attempt がどの終了状態だったか」の時系列を復元できない。

**3. abort（143 / SIGTERM / timeout）の終了情報が保存されない**

- `kaji_harness/cli.py:154-180` は、agent が terminal event を出した後に kaji が
  後始末 `terminate()` した際の `143`（128+15）/ `137` を **意図的に失敗根拠から
  除外**している（CLI が SIGTERM を trap し shell 慣例の正値で exit するため）。
  判定ロジックとしては妥当だが、この `exit_code` / `signal` を **artifact に
  構造化保存する処理が無い**（`CLIResult` 自体が `process.returncode` を捨てている。
  `kaji_harness/models.py:28-39`）。
- timeout / 真のプロセス失敗（`StepTimeoutError` / `CLIExecutionError`）は
  runner の `except Exception`（`runner.py:658-661`）で `end_status="ERROR"` の上
  re-raise され、**result.json も step_end も残らないまま** run 全体が
  `EXIT_RUNTIME_ERROR` で停止する（`step_end` のログは `runner.py:595` で、
  例外発生時には到達しない）。

**4. progress.md / summary で failed / aborted attempt の存在が分からない**

- `kaji_harness/state.py:150-163` の `_write_progress_md` は `step_history` を
  `[x]` / `[ ]` で列挙するのみ。exit_code / signal / attempt 番号を持たず、
  異常終了 attempt はそもそも `step_history` に記録されない（`record_step` は
  `step_end` の後の `runner.py:602` で呼ばれ、例外経路では到達しない）。

### Expected Behavior（EB）

同一 workflow run 内で同じ step を複数回実行したとき、各 attempt の終了情報が
attempt 単位で構造化保存され、run.log と progress.md から時系列を復元できる。

```text
.kaji-artifacts/<issue>/runs/<run_id>/steps/<step>/
  attempt-001/
    console.log
    stdout.log
    prompt.txt
    verdict.yaml
    result.json   # ← 本 Issue で追加
  attempt-002/
    ...
```

`result.json`（attempt 単位の終了情報。pure JSON）:

```json
{
  "step_id": "design",
  "attempt": 1,
  "status": "RETRY",
  "exit_code": 143,
  "signal": "SIGTERM",
  "started_at": "2026-06-04T22:00:00.000000+00:00",
  "ended_at": "2026-06-04T22:01:47.000000+00:00",
  "duration_ms": 107335,
  "session_id": "abc123",
  "dispatch": "agent",
  "error": null
}
```

`run.log` の step イベントに attempt を識別できる情報を含める（既存
`step_start` / `step_end` への **フィールド追加**。後述「方針」で event 追加では
なくフィールド追加を選ぶ理由を述べる）:

```json
{"event":"step_start","step_id":"design","attempt":1,"agent":"claude", ...}
{"event":"step_end","step_id":"design","attempt":1,"verdict":{"status":"RETRY",...},"exit_code":143,"signal":"SIGTERM","duration_ms":107335, ...}
{"event":"step_start","step_id":"design","attempt":2, ...}
{"event":"step_end","step_id":"design","attempt":2,"verdict":{"status":"PASS",...},"exit_code":0,"signal":null, ...}
```

### EB に対する設計上の補正（assumption challenge）

Issue 本文 EB の例は attempt-001 を `status: "ABORT"` とし、その直後に
attempt-002 が PASS する流れを示すが、これは **そのままでは実現不能**で、
設計として以下に補正する。理由を明示する:

- `ABORT` は workflow YAML 契約上「終端（人間介入が必要）」を意味し、`on:` で
  retry に戻す設計は既存ワークフローに存在しない（`docs/dev/workflow-authoring.md`
  の verdict 定義）。`ABORT` verdict を自動 retry させると ABORT 本来の意味と
  既存契約が壊れる。
- 「同一 run 内で失敗 attempt の後に PASS する retry」は、**RETRY 系 verdict に
  よる cycle**（`runner.py:610-616`）で既に成立する正規経路である。terminal event を
  出した agent が `RETRY` verdict を返し、kaji が後始末 `terminate()` した結果
  `exit_code=143` になる、というのが OB#3 で観測される「143 を伴う非 PASS
  attempt」の実体である。
- したがって本設計は「**exit_code / signal の記録は成否と直交する横断的関心事**」
  として扱う。retry は RETRY-cycle で実現し、ABORT / timeout / crash は
  **best-effort で記録した上で run を停止**する（exit code 契約 `EXIT_RUNTIME_ERROR`
  を維持し、失敗を silent に retry 化しない）。

## 再現手順（steps-to-reproduce）

1. main（`a28a0b8` 以降）で kaji workflow の任意 step を実行する。
2. 同一 workflow run 内で同じ step を retry し（review cycle 等で `RETRY` →
   loop back）、2 回目を PASS させる。検証では `execute_cli` を mock して
   attempt-001 が `exit_code=143` の RETRY、attempt-002 が PASS を返す。
3. `.kaji-artifacts/<issue>/runs/<run_id>/steps/<step>/attempt-*/` を確認する。
4. **OB**: attempt 配下に `result.json` が無く、`run.log` の `step_start` /
   `step_end` に attempt / exit_code / signal が無いため、abort attempt の
   exit_code(143) / signal を artifact から確認できない。timeout / 真の失敗では
   そもそも attempt 単位の終了情報が一切残らず run が `EXIT_RUNTIME_ERROR` で止まる。

## 根本原因（Root Cause）

| 症状 | 根本原因 | 位置 |
|------|----------|------|
| result.json 不在 | attempt 終了情報を書き出す処理自体が未実装 | `runner.py`（書き出し点なし） |
| exit_code / signal が成果物に残らない | `CLIResult` が `process.returncode` を保持せず捨てている。正常 terminate(143) も例外（CLIExecutionError.returncode）も artifact 化されない | `kaji_harness/models.py:28-39` / `kaji_harness/cli.py:167-180` |
| run.log に attempt 識別子が無い | `log_step_start` / `log_step_end` の signature に `attempt` が無い。#220 では `verdict_source` のみ attempt 付与 | `kaji_harness/logger.py:41-77` |
| 異常終了が一切残らない | dispatch（`execute_cli` / `execute_script`）が raise すると `step_end` / `record_step` / result 書き出しに到達せず、runner の `except Exception` で re-raise される | `runner.py:548-602` / `runner.py:658-661` |
| progress に failed/aborted が出ない | `_write_progress_md` が `step_history` のみを参照し、`StepRecord` が attempt / exit_code を持たない。異常終了 attempt は `step_history` に入らない | `kaji_harness/state.py:115-163` |

**いつから**: result.json / attempt 識別子は #220 (`a28a0b8`) でも対象外。本 Issue が
その残ギャップを埋める（#220 は layout 分離と verdict 解決順までを範囲とした）。

**同根の他箇所**: exec_script 経路（`script_exec.py:140-151`）も同じく終了情報を
artifact 化しない。本設計は agent / exec_script の両 dispatch を対象にする。

## インターフェース

bug 修正だが、新規 artifact（`result.json`）と run.log フィールド追加を含むため、
入出力契約を明示する。**既存の公開 IF（CLI 引数 / verdict.yaml / attempt layout）は
不変**。

### 入力

- 変更なし。`kaji run <workflow> <issue>` の引数・workflow YAML は不変。
- 内部的に subprocess の `process.returncode`（agent / exec_script）と、
  dispatch 直前に記録済みの `attempt_started_at`（`runner.py:521,546`）を入力として使う。

### 出力

#### 1. `result.json`（新規 artifact / attempt 単位）

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `step_id` | `str` | step ID |
| `attempt` | `int` | 1 始まりの attempt 番号（`attempt-NNN` の NNN） |
| `status` | `str` | 正常終了は解決済み verdict.status。異常終了は `"ABORT"` |
| `exit_code` | `int \| null` | subprocess の `returncode`。取得不能なら `null` |
| `signal` | `str \| null` | `exit_code` から導出した signal 名（`SIGTERM` 等）。clean exit は `null` |
| `started_at` | `str` | dispatch 直前の UTC ISO 8601 |
| `ended_at` | `str` | 終了時刻の UTC ISO 8601 |
| `duration_ms` | `int` | `ended_at - started_at` の ms |
| `session_id` | `str \| null` | agent session id（exec_script / 未取得は `null`） |
| `dispatch` | `str` | `"agent"` / `"exec_script"` |
| `error` | `str \| null` | 異常終了時の例外クラス名 + 短いメッセージ（secret 非含）。正常時 `null` |

- **保存先**: `runs/<run_id>/steps/<step_id>/attempt-NNN/result.json`。
- **保存タイミング**: 正常終了（verdict 解決後）と異常終了（dispatch 例外捕捉時）の
  両方。異常終了は best-effort（書き出し自体が失敗しても run の終了処理を阻害しない）。
- **読み手の契約**: `result.json` は **欠落しうる**（旧 run / best-effort 書き出し前に
  プロセスが死んだ attempt）。読み手は存在しない場合を許容する。

#### 2. `run.log` の step イベント拡張（フィールド追加 / JSONL）

| イベント | 追加フィールド | 型 |
|---------|---------------|-----|
| `step_start` | `attempt` | `int` |
| `step_end` | `attempt` / `exit_code` / `signal` | `int` / `int\|null` / `str\|null` |

- 既存フィールドは不変。追加のみ（後方互換。既存 consumer は未知キーを無視できる）。
- `step_end` は **異常終了経路でも発火**するよう変更する（従来は正常経路のみ）。
  異常終了時の `verdict` は合成 `Verdict(status="ABORT", ...)`。
- `verdict_source` の既存 `attempt`（`"attempt-001"` 文字列）は #220 consumer /
  既存テスト互換のため **変更しない**。新規フィールドは `int` で統一する
  （文字列・整数の混在は run.log 内で発生するが、既存契約を壊さない選択を優先）。

#### 3. `progress.md` の attempt 可視化

- `StepRecord` に `attempt` / `exit_code` / `signal`（いずれも optional, default
  `None`）を追加し、`record_step` で記録、`_write_progress_md` で描画する。
- 異常終了 attempt も `record_step(ABORT)` するため progress.md に行が現れる。

例:

```markdown
# Progress: Issue #222
- [x] design (attempt 1): PASS — 設計書作成完了
- [ ] implement (attempt 1): RETRY — テスト未達 (exit 143, SIGTERM)
- [x] implement (attempt 2): PASS — 実装完了
```

### 使用例

```python
# 内部 helper（新規モジュール kaji_harness/result.py 想定）
from kaji_harness.result import AttemptResult, write_result_json, derive_signal

derive_signal(143)   # -> "SIGTERM"
derive_signal(137)   # -> "SIGKILL"
derive_signal(0)     # -> None
derive_signal(-15)   # -> "SIGTERM"（POSIX: signal 終了は負値）
derive_signal(None)  # -> None

write_result_json(attempt_dir, AttemptResult(
    step_id="implement", attempt=1, status="RETRY",
    exit_code=143, signal="SIGTERM",
    started_at=started, ended_at=ended, duration_ms=107335,
    session_id="abc", dispatch="agent", error=None,
))
```

## 制約・前提条件

- **#220 の「上書き禁止」を維持**: retry / rerun は前回 attempt の artifact を
  上書きしない。`result.json` は attempt-NNN/ 配下なので構造的に上書きされない。
- **公開 IF 不変**: CLI 引数 / workflow YAML / `verdict.yaml` 形式 / attempt layout
  は変えない。`run.log` は追加フィールドのみ。exit code 契約（`EXIT_OK` /
  `EXIT_ABORT` / `EXIT_RUNTIME_ERROR`）は維持し、失敗を silent に retry 化しない。
- **privacy**: `result.json` に保存するのは終了メタデータのみ。token / secret /
  private prompt は含めない（既存ログポリシー準拠。Issue「設計で固定したい方針」）。
  `error` は例外クラス名 + 短い stderr 抜粋に留め、prompt 本文を入れない。
- **best-effort の意味**: 異常終了時の `result.json` / `step_end` 書き出しが失敗
  しても、その失敗で元の例外を握り潰さない（元例外を優先 re-raise する。
  `docs/reference/python/error-handling.md` § 握り潰し禁止）。
- **後方互換 / migration**: `result.json` は純粋に追加。既存 attempt-NNN layout を
  読む処理（`resolve_verdict` / verdict.yaml 読み取り）は `result.json` に依存
  しないため、旧 run に `result.json` が無くても影響なし。**migration は不要**
  （ADR 005 の「migration 必須化はしない」方針と整合）。`StepRecord` の新フィールドは
  optional default で、旧 `session-state.json` の load（`state.py:69` の
  `StepRecord(**r)`）が壊れない。

## 方針（修正アプローチ）

最小侵襲を優先し、責務を 3 レイヤに分けて局所化する。

### A. exit_code / signal の捕捉（cli.py / script_exec.py / models.py）

- `CLIResult` に `exit_code: int | None = None` / `signal: str | None = None` を追加。
- `cli.py:_execute_cli_once` の正常 return 直前で `process.returncode` を
  `exit_code` に格納し `signal` を導出（terminate 後の 143 もここで残る）。
- 異常終了の運搬: `CLIExecutionError` は既に `returncode` を持つ。`StepTimeoutError`
  に kill 由来の signal 情報（`SIGTERM` / `SIGKILL`）を持たせるか、runner 側で
  timeout を SIGTERM 相当として扱う（実装時に最小の方を選ぶ）。`script_exec` も
  同様に `ScriptExecutionError.returncode` を運ぶ（既存）。
- `derive_signal(exit_code)` は標準 `signal.Signals` で number→name を引く
  （`>128` は `n-128`、負値は `-n`、それ以外は `None`、未知番号は `None`）。

### B. result.json と run.log attempt 識別子（runner.py / logger.py / result.py）

- 新規 `kaji_harness/result.py`: `AttemptResult` dataclass + `write_result_json` +
  `derive_signal`（`verdict.py` の `write_verdict_yaml` と並列の構造）。
- `logger.log_step_start` に `attempt: int` を追加、`log_step_end` に `attempt` /
  `exit_code` / `signal` を追加。
- `runner.py` のメインループ dispatch 区間を try/except で囲む（疑似コード）:

```python
attempt_dir = allocate_attempt_dir(run_dir, current_step.id)
attempt_no = int(attempt_dir.name.split("-")[1])
logger.log_step_start(current_step.id, ..., attempt=attempt_no)
attempt_started_at = datetime.now(UTC)
try:
    result = execute_cli(...)        # or execute_script(...)
    verdict, verdict_source = resolve_verdict(...)
    exit_code, signal = result.exit_code, result.signal
    status = verdict.status
    error = None
except (StepTimeoutError, CLIExecutionError, ScriptExecutionError) as exc:
    # best-effort 記録 → 元例外を優先 re-raise（crash semantics 維持）
    exit_code = getattr(exc, "returncode", None)
    signal = derive_signal(exit_code) or _signal_from(exc)
    verdict = Verdict(status="ABORT", reason="step aborted", evidence=str(exc), suggestion="...")
    status, error = "ABORT", f"{type(exc).__name__}: {exc}"
    _record_attempt_end(...)   # result.json + step_end + record_step を best-effort
    raise
ended = datetime.now(UTC)
_record_attempt_end(attempt_dir, ..., status, exit_code, signal, started, ended, ...)
# 正常経路では従来どおり verdict に基づき次 step を決定（RETRY-cycle 等）
```

- `_record_attempt_end` は `write_result_json` + `logger.log_step_end(..., attempt,
  exit_code, signal)` + `state.record_step(...)` をまとめる。best-effort の書き出し
  失敗（OSError 等）は元処理を妨げない範囲で握る。

### C. progress.md 可視化（state.py）

- `StepRecord` に optional `attempt` / `exit_code` / `signal` を追加。
- `record_step` の引数を拡張（既存呼び出しは default で互換）。
- `_write_progress_md` で `(attempt N): STATUS — reason (exit X, SIGNAL)` を描画。

リファクタ（既存ロジックの作り替え）は混在させない。`resolve_verdict` /
attempt 採番 / cli の失敗判定ロジックには手を入れない。

## テスト戦略

> **CRITICAL**: 実行時の振る舞いを変えるコード変更のため、Small / Medium の
> 検証観点を定義する。Large は不要（実 agent / 外部 API 疎通を新たに必要としない）。
> bug 固有ルールに従い、再現テスト（修正前 Red → 修正後 Green）を必ず定義する。

### 変更タイプ

実行時の振る舞いを変えるコード変更（artifact 生成 + log schema 拡張 + state 拡張）。

### Small テスト

- `derive_signal`: `143→SIGTERM` / `137→SIGKILL` / `0→None` / `-15→SIGTERM` /
  `None→None` / 未知番号→`None` の境界。
- `write_result_json` + `AttemptResult`: 全フィールドが JSON round-trip する。
  `null` 許容フィールド（`exit_code` / `signal` / `session_id` / `error`）の表現。
- `CLIResult` に `exit_code` / `signal` フィールドが付与され default が後方互換
  （既存 `CLIResult(full_output=...)` 構築が壊れない）。
- `StepRecord` に optional フィールドを追加しても旧 `session-state.json`
  （新キー無し）の load が壊れない（`StepRecord(**r)` 互換）。
- `_write_progress_md`: attempt / exit_code / signal を持つ `StepRecord` が
  `(attempt N): STATUS — reason (exit X, SIGNAL)` 形式で描画される。

### Medium テスト

`WorkflowRunner.run()` を mock CLI + 実 filesystem（既存
`tests/test_verdict_artifact_runner.py` の `_make_runner` 系を踏襲）で回す。

- **正常 single-attempt**: PASS step で attempt-001/`result.json` が
  `status=PASS` / `exit_code`（mock 値）/ `signal` 導出 / `started_at` <=
  `ended_at` / `session_id` を持つ。`run.log` の `step_start` / `step_end` に
  `attempt=1` が付き、`step_end` に `exit_code` / `signal` が入る。
- **回帰テスト（Issue 完了条件: 143 失敗 attempt → retry → PASS）**:
  review cycle workflow を使い、`execute_cli` mock が
  attempt-001 で `CLIResult(exit_code=143, signal="SIGTERM", <RETRY verdict>)`、
  retry 後 attempt-002 で `CLIResult(<PASS verdict>)` を返す。検証:
  - attempt-001/`result.json` が `status=RETRY` / `exit_code=143` /
    `signal=SIGTERM` を持ち、attempt-002 実行後も **上書きされず残る**。
  - attempt-002/`result.json` が `status=PASS`。
  - `run.log` に `step_start(attempt=1)` → `step_end(attempt=1, exit_code=143)`
    → `step_start(attempt=2)` → `step_end(attempt=2)` の時系列が並ぶ。
  - `progress.md` に failed attempt（attempt 1, RETRY, exit 143）と最終 PASS の
    両方が現れる。
- **異常終了 best-effort（完了条件: timeout / SIGTERM の記録）**: `execute_cli`
  mock が `StepTimeoutError` / `CLIExecutionError(returncode=143)` を raise する。
  検証:
  - attempt-001/`result.json` が best-effort で書かれ、`status=ABORT` /
    `exit_code`（取得可能なら 143）/ `signal` / `error`（例外クラス名）を持つ。
  - `step_end(attempt=1)` が run.log に発火する。
  - `record_step(ABORT)` により progress.md に aborted attempt が現れる。
  - 元例外は re-raise され、run は従来どおり `EXIT_RUNTIME_ERROR`
    （`cmd_run` の `except HarnessError`）で停止する（crash semantics 不変）。

### Large テスト

- 不要。新規 artifact 生成・log schema 拡張・state 拡張はいずれも mock CLI +
  実 filesystem（Medium）で完結し、実 agent 起動 / 外部 API 疎通を新たに必要と
  しない。`docs/dev/testing-convention.md` の判定基準（外部 API / 実サービス疎通
  なし）により Large 対象外。既存の `test_verdict_artifact_e2e_large_local.py` 等が
  実 CLI 経路の回帰を別途担保している。

### 実装前 Red 証跡

bug.md の escape clause に従い、Issue 本文 OB の一次証跡
（`find .kaji-artifacts -name result.json` = 0 / `grep result.json kaji_harness/`
= 0 / run.log に attempt 無し）が「result.json 不在・attempt 識別子不在」という
OB を直接示す。上記回帰テスト（修正後 Green）は省略不可。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | result.json による attempt 終了情報の構造化保存と abort best-effort 記録は、ADR 005（artifact-primary verdict / attempt layout）を拡張する設計判断。ADR 005 への追記、または短い後続 ADR を検討する |
| docs/ARCHITECTURE.md | あり | § 実行アーティファクトの layout（`ARCHITECTURE.md:291-310`）の attempt-NNN/ 内容に `result.json` を追加。run.log step イベントの attempt 付与を反映 |
| docs/dev/ | なし | workflow / 開発手順自体は不変 |
| docs/reference/ | あり | `docs/reference/python/logging.md` の `step_start` / `step_end` フィールド表に `attempt` / `exit_code` / `signal` を追記。result.json は run.log ではないが、関連 artifact として言及を検討 |
| docs/cli-guides/ | なし | CLI 引数・サブコマンド仕様は不変 |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| kaji runner（attempt 採番 / dispatch / 例外経路） | `kaji_harness/runner.py:45-69, 465-602, 658-661` | `allocate_attempt_dir` が attempt-NNN を採番。dispatch 例外は `except Exception` で re-raise され step_end / record_step に到達しない（result.json 書き出し点が無いことの根拠） |
| kaji CLI（143/137 の失敗判定除外） | `kaji_harness/cli.py:152-180` | terminal event 観測後の terminate(143/137) を失敗根拠にしない。`process.returncode` を CLIResult に残さない（exit_code 喪失の根拠） |
| kaji models（CLIResult） | `kaji_harness/models.py:28-39` | `CLIResult` に exit_code / signal フィールドが無い（捕捉先が無いことの根拠） |
| kaji logger（step イベント / verdict_source） | `kaji_harness/logger.py:41-85` | `log_step_start` / `log_step_end` に attempt 引数が無い。attempt を持つのは `log_verdict_source` のみ |
| kaji state（progress.md / StepRecord） | `kaji_harness/state.py:31-41, 115-163` | `_write_progress_md` が step_history のみ参照、`StepRecord` に attempt/exit_code 無し |
| kaji script_exec（exec_script 経路） | `kaji_harness/script_exec.py:140-151` | exec_script も終了情報を artifact 化しない（同根箇所） |
| Issue #220 設計 / ADR 005 | `draft/design/issue-220-feat-issue-comment-verdict.md` / `docs/adr/005-artifact-primary-verdict.md` | attempt-NNN layout と「migration 必須化はしない」方針。本設計はこの延長で result.json を追加（後方互換方針の根拠） |
| run.log schema 文書 | `docs/reference/python/logging.md:47-156` | `step_start` / `step_end` / `verdict_source` の現行フィールド契約（追加フィールドの整合先） |
| Python `signal.Signals` | https://docs.python.org/3/library/signal.html | signal 番号→名前の標準マッピング（`derive_signal` の根拠） |
| Python `Popen.returncode`（負値 = signal 終了） | https://docs.python.org/3/library/subprocess.html#subprocess.Popen.returncode | "A negative value -N indicates that the child was terminated by signal N (POSIX only)."（signal 導出規則の根拠） |
