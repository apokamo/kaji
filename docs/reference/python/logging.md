# ロギング規約

kaji における実行ログの規約。`kaji_harness/logger.py` の `RunLogger` JSONL 契約を文書化する。

> このドキュメントは Python 標準 `logging` モジュールの使い方ガイドではない。
> `RunLogger` は JSONL を直接書き出す専用実装であり、標準 `logging` を使用しない。

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
| `agent` | `str` |
| `model` | `str \| null` |
| `effort` | `str \| null` |
| `session_id` | `str \| null` |

```json
{"ts": "2025-04-22T01:00:01+00:00", "event": "step_start", "step_id": "implement", "agent": "claude", "model": "claude-sonnet-4-6", "effort": null, "session_id": null}
```

#### `step_end`

ステップ終了時に記録（正常終了・タイムアウト・エラーを問わず）。

| フィールド | 型 |
|-----------|-----|
| `step_id` | `str` |
| `verdict` | `dict` |
| `duration_ms` | `int` |
| `cost` | `dict \| null` |

`verdict` の構造:

```json
{"status": "PASS", "reason": "...", "evidence": "...", "suggestion": "..."}
```

`cost` の構造（利用可能な場合のみ）:

```json
{"usd": 0.012, "input_tokens": 1500, "output_tokens": 800}
```

```json
{"ts": "2025-04-22T01:05:00+00:00", "event": "step_end", "step_id": "implement", "verdict": {"status": "PASS", "reason": "実装完了", "evidence": "pytest 全パス", "suggestion": ""}, "duration_ms": 240000, "cost": {"usd": 0.015, "input_tokens": 2000, "output_tokens": 1200}}
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
| `log_step_start(step_id, agent, model, effort, session_id)` | CLI 実行前 |
| `log_step_end(step_id, verdict, duration_ms, cost)` | CLI 終了・verdict 解析後 |
| `log_cycle_iteration(cycle_name, iteration, max_iter)` | サイクル内の各反復開始時 |
| `log_workflow_end(status, cycle_counts, total_duration_ms, total_cost, error)` | ワークフロー終了時（正常・異常問わず） |

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

## 禁止事項

### 標準 logging の直接使用

```python
# ❌ 禁止
import logging
logging.info("step started")

# ✅ RunLogger を使う
self.logger.log_step_start(step.id, step.agent, step.model, step.effort, session_id)
```

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
- [ ] 標準 `logging` モジュールを直接使用していないか
- [ ] 機密情報がフィールドに含まれていないか

## 関連ドキュメント

- `kaji_harness/logger.py` — RunLogger の実装（フィールド仕様のソースオブトゥルース）
- [エラーハンドリング](./error-handling.md) — エラー発生時の RunLogger 呼び出しパターン
- [Python スタイル規約](./python-style.md) — 全般的なコーディング規約
