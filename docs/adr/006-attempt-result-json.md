# ADR 006: attempt 終了情報の構造化保存（`result.json`）と異常終了の best-effort 記録

## ステータス

承認 (2026-06-04)

## コンテキスト

ADR 005 (Issue #220) は artifact-primary verdict 解決と `runs/<run_id>/steps/<step_id>/attempt-NNN/` の attempt layout を導入し、`console.log` / `stdout.log` / `prompt.txt` / `verdict.yaml` のログ上書きを解消した。しかし以下のギャップが残っていた（Issue #222）。

- attempt ごとの **終了情報（`status` / `exit_code` / `signal` / 時刻 / `duration_ms` / `session_id`）の構造化保存が存在しない**（`find .kaji-artifacts -name result.json` → 0 件、コード側にも書き出し点が無い）。
- `run.log` の `step_start` / `step_end` に **attempt 識別子が無く**、step retry の時系列を復元できない（attempt を持つのは `verdict_source` のみ）。
- 143 / SIGTERM / timeout / interruption のような **異常終了の exit_code / signal が成果物に残らない**。`CLIResult` が `process.returncode` を捨てており、dispatch 例外は `step_end` / `record_step` に到達しないまま run 全体が `EXIT_RUNTIME_ERROR` で停止する。
- `progress.md` で failed / aborted attempt の存在が分からない。

abort attempt の終了コード・signal が構造化されないため、143 / timeout の原因調査・費用説明・再発防止が困難だった。

## 決定

attempt 終了情報を attempt 単位の構造化ファイル `result.json` として保存し、`run.log` の step イベントに attempt 識別子と exit_code / signal を付与し、`progress.md` に failed / aborted attempt を可視化する。異常終了でも best-effort で残す。

- 新規 `kaji_harness/result.py` に `AttemptResult` dataclass / `write_result_json` / `derive_signal` を置く（`verdict.py` の `write_verdict_yaml` と並列の構造）。`result.json` は `runs/<run_id>/steps/<step_id>/attempt-NNN/result.json` に保存する。
- `CLIResult` に `exit_code` / `signal` を追加し、`cli.py` / `script_exec.py` が `process.returncode` と導出 signal を運ぶ。`StepTimeoutError` は kill 後の returncode を運ぶ。`derive_signal` は `signal.Signals` で `>128`（shell 慣例 128+N）/ 負値（POSIX signal 終了）の両方を名前に解決する。
- `logger.log_step_start` に `attempt`、`log_step_end` に `attempt` / `exit_code` / `signal` を追加する（既存フィールドは不変。追加のみで後方互換）。`step_end` は異常終了経路でも合成 `Verdict(status="ABORT", ...)` で発火する。
- runner の dispatch 区間を try/except で囲み、`StepTimeoutError` / `CLIExecutionError` / `ScriptExecutionError` を捕捉して best-effort で `result.json` + `step_end` + `record_step` を記録した上で **元例外を優先 re-raise** する。crash semantics（`EXIT_RUNTIME_ERROR`）を維持し、失敗を silent に retry 化しない。`result.json` 書き出しの `OSError` は元処理を妨げない範囲で握る。
- `StepRecord` に optional `attempt` / `exit_code` / `signal` を追加し、`progress.md` に `(attempt N): STATUS — reason (exit X, SIGNAL)` を描画する。

「同一 run 内で失敗 attempt の後に PASS する retry」は RETRY 系 verdict による cycle（ADR 005 / runner の cycle 機構）で実現する。`ABORT` は workflow 契約上の終端であり自動 retry させない。exit_code / signal の記録は成否と直交する横断的関心事として扱う。

## 影響

- attempt 配下に `result.json` が追加される（純粋に追加。`docs/ARCHITECTURE.md` § 実行アーティファクトの layout）。
- `run.log` の `step_start` / `step_end` に `attempt` / `exit_code` / `signal` が追加される（`docs/reference/python/logging.md`）。既存 consumer は未知キーを無視できる。`verdict_source` の既存 `attempt`（`"attempt-001"` 文字列）は #220 互換のため不変で、新規フィールドは `int`。
- `session-state.json` の `step_history` に `attempt` / `exit_code` / `signal` が追加される。旧 state（新キー無し）の load は optional default で壊れない。
- CLI 引数 / workflow YAML / `verdict.yaml` 形式 / attempt layout / exit code 契約は不変。
- **migration 不要**: `result.json` は純粋追加で、verdict 解決（`resolve_verdict`）は `result.json` に依存しない。旧 run に無くても影響しない（ADR 005 の「migration 必須化はしない」方針と整合）。

## 代替案と却下理由

| 代替案 | 却下理由 |
|--------|----------|
| `step_attempt_start` / `step_attempt_end` を新 event として追加する | 既存 `step_start` / `step_end` consumer と二重管理になる。既存 event へのフィールド追加で後方互換に attempt 時系列を表現できる |
| Issue 本文 EB のとおり `ABORT` attempt の後に `retry` で PASS させる | `ABORT` は workflow 契約上「終端（人間介入が必要）」であり、自動 retry させると意味と既存契約が壊れる。失敗 attempt → retry → PASS は RETRY-cycle が正規経路 |
| 異常終了時に `result.json` を書けなければ run を失敗させる | best-effort 記録の失敗で元例外を握り潰すと crash の真因が失われる。元例外を優先 re-raise し、`result.json` 書き出し失敗は WARN に留める |
| `exit_code` を常に shell 慣例の正値（143）へ正規化する | timeout-kill 経路では POSIX の負値（-15）が真実。`derive_signal` が正値 / 負値の双方を名前へ解決するため、raw returncode を保存する方が情報損失が無い |
