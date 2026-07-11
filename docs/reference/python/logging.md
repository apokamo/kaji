# ロギング規約

kaji における実行ログの規約。`kaji_harness/logger.py` の `RunLogger` JSONL 契約を文書化する。

> このドキュメントは Python 標準 `logging` モジュールの使い方ガイドではない。
> `RunLogger` は JSONL を直接書き出す専用実装であり、標準 `logging` を使用しない。

> [!NOTE]
> kaji には責務の異なる **2 系統** のログ層がある。本ドキュメントは前者（機械可読
> ログ）の契約を定める。
>
> | 層 | 実装 | 出力 | 用途 |
> |----|------|------|------|
> | 機械可読ログ | `RunLogger`（`logger.py`） | `run.log`（JSONL） | プログラムが解析する実行記録 |
> | 起動コンソール progress | stdlib `logging`（`console_log.py` / `kaji.*` 名前空間） | stdout（`INFO` 以下）/ stderr（`WARNING` 以上） | `kaji run` 起動コンソールで人間が時系列に進行を追う表示 |
>
> 後者は Issue #235 で導入。起動コンソール向けの **人間可読 progress** であり、
> `RunLogger` の JSONL 契約には一切影響しない。詳細は § 起動コンソール progress logging。

## RunLogger の概要

`RunLogger` は `kaji_harness/logger.py` に定義された dataclass。ワークフロー実行中のイベントを JSONL 形式でファイルに記録する。

```python
from kaji_harness.logger import RunLogger
from pathlib import Path

logger = RunLogger(log_path=Path(".kaji/run.jsonl"))
```

各イベントは JSON オブジェクト 1 行として書き込まれ、即時 `flush()` される。プロセスが途中終了してもログが失われない。

## JSONL フォーマット

### 全イベント共通フィールド

すべてのイベントに必ず含まれるフィールド:

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `ts` | `str` | UTC タイムスタンプ（ISO 8601 形式: `"2025-04-22T00:00:00.000000+00:00"`） |
| `event` | `str` | イベント名（下記参照） |

### イベント別フィールド

#### `workflow_start`

ワークフロー実行開始時に記録。

| フィールド | 型 |
|-----------|-----|
| `issue` | `int` |
| `workflow` | `str` |

```json
{"ts": "2025-04-22T01:00:00+00:00", "event": "workflow_start", "issue": 141, "workflow": "feature_development.yaml"}
```

#### `step_start`

ステップ実行開始時に記録。

| フィールド | 型 |
|-----------|-----|
| `step_id` | `str` |
| `agent` | `str \| null` |
| `model` | `str \| null` |
| `effort` | `str \| null` |
| `session_id` | `str \| null` |
| `attempt` | `int \| null` |
| `dispatch` | `str` |

`dispatch` は dispatch 経路の識別子。`"agent"` は LLM 経由（既存）、`"exec_script"` は
skill frontmatter `exec_script` による subprocess dispatch（Issue #204）、`"exec"` は
workflow.yaml の `exec:` step による subprocess dispatch（Issue #205）。決定論経路
（`"exec"` / `"exec_script"`）では `agent` / `model` / `effort` は常に null（LLM 起動なし）。

`attempt` は `attempt-NNN` の 1 始まり整数（Issue #222）。dispatch される step では常に整数。
同一 step が cycle / retry / resume で再 dispatch されると `attempt` が増え、step retry の
時系列を `run.log` から復元できる。

```json
{"ts": "2025-04-22T01:00:01+00:00", "event": "step_start", "step_id": "implement", "agent": "claude", "model": "claude-sonnet-4-6", "effort": null, "session_id": null, "attempt": 1, "dispatch": "agent"}
{"ts": "2025-04-22T01:00:01+00:00", "event": "step_start", "step_id": "poll-review", "agent": null, "model": null, "effort": null, "session_id": null, "attempt": 1, "dispatch": "exec_script"}
{"ts": "2025-04-22T01:00:01+00:00", "event": "step_start", "step_id": "collect-metrics", "agent": null, "model": null, "effort": null, "session_id": null, "attempt": 1, "dispatch": "exec"}
```

#### `step_end`

ステップ終了時に記録（正常終了・タイムアウト・エラーを問わず）。

| フィールド | 型 |
|-----------|-----|
| `step_id` | `str` |
| `verdict` | `dict` |
| `duration_ms` | `int` |
| `cost` | `dict \| null` |
| `attempt` | `int \| null` |
| `exit_code` | `int \| null` |
| `signal` | `str \| null` |
| `dispatch` | `str` |

`dispatch` は `step_start` と同じ意味（`"agent"` / `"exec_script"` / `"exec"`）。決定論経路
（`"exec"` / `"exec_script"`）では `cost` も常に null（LLM 課金なし）。

`attempt` / `exit_code` / `signal` は Issue #222 で追加。`exit_code` は subprocess の
`returncode`（取得不能なら null）、`signal` はそこから導出した signal 名（clean exit /
signal 由来でなければ null）。`step_end` は異常終了（timeout / CLI / script の失敗）でも
発火し、その場合 `verdict.status` は合成 `"ABORT"`、`exit_code` / `signal` は
best-effort で記録される。cycle 上限 exhaust の合成 verdict では dispatch を伴わないため
`attempt` / `exit_code` / `signal` は null。同じ終了情報は attempt 配下の
`result.json`（[`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) § 実行アーティファクトの layout）にも構造化保存される。

`verdict` の構造:

```json
{"status": "PASS", "reason": "...", "evidence": "...", "suggestion": "..."}
```

`cost` の構造（利用可能な場合のみ）:

```json
{"usd": 0.012, "input_tokens": 1500, "output_tokens": 800}
```

```json
{"ts": "2025-04-22T01:05:00+00:00", "event": "step_end", "step_id": "implement", "verdict": {"status": "PASS", "reason": "実装完了", "evidence": "pytest 全パス", "suggestion": ""}, "duration_ms": 240000, "cost": {"usd": 0.015, "input_tokens": 2000, "output_tokens": 1200}, "attempt": 1, "exit_code": 0, "signal": null, "dispatch": "agent"}
```

#### `cycle_iteration`

サイクル内の各イテレーション開始時に記録。

| フィールド | 型 |
|-----------|-----|
| `cycle_name` | `str` |
| `iteration` | `int` |
| `max_iterations` | `int` |

```json
{"ts": "2025-04-22T01:10:00+00:00", "event": "cycle_iteration", "cycle_name": "review_fix_loop", "iteration": 2, "max_iterations": 5}
```

#### `verdict_source`

各 step の verdict 解決経路を記録（Issue #220）。`resolve_verdict()` 直後に呼ぶ。

| フィールド | 型 | 備考 |
|-----------|-----|------|
| `step_id` | `str` | |
| `source` | `str` | `"artifact"` / `"comment"` / `"stdout"` |
| `attempt` | `str` | `attempt-NNN` ディレクトリ名 |

```json
{"ts": "2025-04-22T01:05:00+00:00", "event": "verdict_source", "step_id": "implement", "source": "artifact", "attempt": "attempt-001"}
```

#### `verdict_sanitization`

verdict parse 境界（`_parse_yaml_fields`）で正規化した YAML 禁止制御文字を記録する（Issue #298）。
`resolve_verdict()` の戻り値 `findings` が非空の場合のみ発火する。

| フィールド | 型 | 備考 |
|-----------|-----|------|
| `step_id` | `str` | |
| `attempt` | `str` | `attempt-NNN` ディレクトリ名 |
| `count` | `int` | 検出した禁止制御文字の件数 |
| `findings` | `list[dict]` | `{"codepoint": "U+XXXX", "position": int}` の列。検出順。生の禁止文字は含まない |

```json
{"ts": "2025-04-22T01:05:00+00:00", "event": "verdict_sanitization", "step_id": "implement", "attempt": "attempt-001", "count": 1, "findings": [{"codepoint": "U+001B", "position": 41}]}
```

診断表記規約: コードポイントは常に `U+XXXX`（4桁大文字16進）形式。生の制御文字を event / ログへ
再出力しない。

#### `workflow_end`

ワークフロー終了時に記録。

| フィールド | 型 | 備考 |
|-----------|-----|------|
| `status` | `str` | `"completed"` / `"error"` 等 |
| `cycle_counts` | `dict[str, int]` | サイクル名 → 実行回数 |
| `total_duration_ms` | `int` | |
| `total_cost` | `float \| null` | USD。利用不可の場合は null |
| `error` | `str` | エラー発生時のみ存在 |

```json
{"ts": "2025-04-22T01:30:00+00:00", "event": "workflow_end", "status": "completed", "cycle_counts": {"review_fix_loop": 2}, "total_duration_ms": 1800000, "total_cost": 0.085}
```

## RunLogger の呼び出し場所

各メソッドを呼ぶタイミングを明記する。

| メソッド | いつ呼ぶか |
|---------|-----------|
| `log_workflow_start(issue, workflow)` | ワークフロー開始直後 |
| `log_step_start(step_id, agent, model, effort, session_id, *, attempt=None, dispatch="agent")` | CLI / subprocess 実行前。決定論経路では `dispatch="exec_script"`（Issue #204）/ `dispatch="exec"`（exec-step・Issue #205）+ `agent=model=effort=None`。`attempt` は attempt-NNN の整数（Issue #222） |
| `log_verdict_source(step_id, source, attempt)` | `resolve_verdict()` 直後（verdict 解決経路の記録、Issue #220） |
| `log_verdict_sanitization(step_id, attempt, findings)` | `resolve_verdict()` の `findings` が非空の場合のみ、`log_verdict_source` と同じ場所で呼ぶ（YAML 禁止制御文字の正規化を永続記録、Issue #298） |
| `log_step_end(step_id, verdict, duration_ms, cost, *, attempt=None, exit_code=None, signal=None, dispatch="agent")` | CLI / subprocess 終了・verdict 解析後（異常終了でも合成 ABORT で発火、Issue #222） |
| `log_cycle_iteration(cycle_name, iteration, max_iter)` | サイクル内の各反復開始時 |
| `log_workflow_end(status, cycle_counts, total_duration_ms, total_cost, error)` | ワークフロー終了時（正常・異常問わず） |

> **step log の出力先（Issue #220）**: `stdout.log` / `console.log` / `stderr.log` / `run.log` 以外の step 単位ログ（`prompt.txt` 等）と verdict は、従来の `runs/<run_id>/<step_id>/` ではなく `runs/<run_id>/steps/<step_id>/attempt-NNN/` 配下に出力される。`run.log` は従来どおり `runs/<run_id>/` 直下。詳細は [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) § 実行アーティファクトの layout。

> **`console.log` の可読性正規化（Issue #137）**: `console.log` は人間が読むための表示用アーティファクトであり、adapter は tool result text に含まれる `\uXXXX` エスケープ（Codex の `mcp_tool_call` 二重エンコード等）を人間可読な文字へ正規化して書き出し得る。一方 `stdout.log` は CLI が出力した raw JSONL をそのまま保持するため、正規化前の生イベントの検証可能性は失われない。

```python
# 典型的な呼び出し順序
logger.log_workflow_start(issue=141, workflow="feature_development.yaml")
logger.log_step_start("design", "claude", "claude-sonnet-4-6", None, None)
# ... CLI 実行 ...
logger.log_step_end("design", verdict, duration_ms=60000, cost=cost_info)
logger.log_cycle_iteration("review_fix_loop", iteration=1, max_iter=5)
logger.log_step_start("review", "claude", None, None, session_id)
# ... CLI 実行 ...
logger.log_step_end("review", verdict, duration_ms=30000, cost=None)
logger.log_workflow_end("completed", {"review_fix_loop": 1}, 90000, 0.025)
```

## 新規イベントを追加する場合のガイドライン

`RunLogger` に新しいイベントを追加する場合は以下のルールに従う。

### イベント名の命名規則

`{noun}_{verb_past}` または `{noun}_{state}` の snake_case。

```python
# ✅ 良い例
"step_skipped"
"cycle_exhausted"
"budget_exceeded"

# ❌ 避けるべき
"skipStep"        # camelCase
"step-skip"       # kebab-case
"skip"            # 短すぎる・名詞なし
```

### フィールド設計のルール

1. **共通フィールド（`ts` / `event`）は `_write()` が自動付与するため、イベント固有フィールドだけを `**kwargs` に渡す**
2. **フィールド名は snake_case**
3. **None を許容するフィールドは `str | None` 等で型を明示する**
4. **金額は `float`（USD）、期間は `int`（ミリ秒）で統一する**

```python
def log_budget_exceeded(self, step_id: str, budget_usd: float, spent_usd: float) -> None:
    """予算超過イベントを記録。"""
    self._write(
        "budget_exceeded",
        step_id=step_id,
        budget_usd=budget_usd,
        spent_usd=spent_usd,
    )
```

### 後方互換性の維持

既存フィールドの削除・型変更は禁止。新フィールドの追加は `| None` として optional にする。

## 起動コンソール progress logging（Issue #235）

`run.log`（JSONL）とは別系統の、`kaji run` 起動コンソール向け人間可読表示層。
`kaji_harness/console_log.py` の `configure_console_logging()` が stdlib `logging` の
二ハンドラを `kaji` ルート logger に設定する。

- **routing**: `INFO` 以下 → stdout、`WARNING` 以上 → stderr。
- **formatter**: `[%(asctime)s] [kaji] %(message)s`（local time。`script_exec` の
  exec 中継行 `[ts] [step_id] ...` と同一タイムラインに並ぶよう local time にそろえる）。
- **logger 名前空間**: 各モジュールは `logging.getLogger("kaji.<module>")` を使い、
  `kaji` ルートのハンドラへ伝播させる。`RunLogger`（`kaji_harness.*`）とは別ツリー。
- **`--log-level`**: `kaji run --log-level {DEBUG,INFO,WARNING,ERROR}`（default `INFO`）で
  閾値を制御。`--quiet`（agent/exec stdout streaming 抑制）とは独立。

この層は「人間が起動コンソールで進行を追う」用途に限定し、プログラムが解析する記録は
引き続き `RunLogger`（JSONL）を正本とする。

## 禁止事項

### `RunLogger` 文脈での標準 logging 直接使用

`run.log`（JSONL 機械可読ログ）に書くべきイベントを、標準 `logging` で出してはならない。
機械可読ログは必ず `RunLogger` のメソッド経由にする。

```python
# ❌ 禁止: run.log に残すべきイベントを stdlib logging で出す
import logging
logging.info("step started")

# ✅ RunLogger を使う
self.logger.log_step_start(step.id, step.agent, step.model, step.effort, session_id)
```

> 起動コンソール向けの **人間可読 progress** を `kaji.*` 名前空間の stdlib `logging` で
> 出すのは別系統として許容される（§ 起動コンソール progress logging）。この禁止事項は
> あくまで「JSONL 機械可読ログを stdlib logging で代替するな」という趣旨に限定される。

### 文字列フォーマットでのイベント記述

```python
# ❌ 禁止: JSON に埋め込む場合に構造が失われる
self._write("step_end", message=f"step {step_id} ended with {status}")

# ✅ 各値を独立したフィールドとして渡す
self._write("step_end", step_id=step_id, verdict=asdict(verdict), ...)
```

### 機密情報の記録

API キー・トークン・認証情報をフィールドとして記録しない。

```python
# ❌ 禁止
self._write("step_start", api_key=config.api_key)

# ✅ ID や存在フラグのみ
self._write("step_start", agent=step.agent, model=step.model, ...)
```

## チェックリスト

### 実装時チェック

- [ ] `RunLogger` のメソッドを正しいタイミングで呼んでいるか
- [ ] `log_workflow_end` が正常・異常終了どちらのパスでも呼ばれているか（`try/finally` 等）
- [ ] 新規イベントのフィールド名が snake_case か
- [ ] `run.log`（JSONL）に残すべきイベントを標準 `logging` で代替していないか（起動コンソール progress は別系統として許容）
- [ ] 機密情報がフィールドに含まれていないか

## 関連ドキュメント

- `kaji_harness/logger.py` — RunLogger の実装（フィールド仕様のソースオブトゥルース）
- [エラーハンドリング](./error-handling.md) — エラー発生時の RunLogger 呼び出しパターン
- [Python スタイル規約](./python-style.md) — 全般的なコーディング規約
