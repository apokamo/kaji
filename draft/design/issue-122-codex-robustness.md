# [設計] Codex エージェント実行の堅牢性一括改善

Issue: #122

## 概要

`CodexAdapter.extract_text()` の null 安全性と Unicode 処理、および `cli.py` のエラーメッセージ改善とモデルキャパシティエラーのリトライ機構を追加する。

## 背景・目的

Codex エージェント実行時に以下の 4 つの問題が発生し、ワークフローが不要に異常終了する:

| # | 問題 | 影響 |
|---|------|------|
| Bug 1 | `mcp_tool_call` の `result: null` で `AttributeError` crash | ワークフロー異常終了 |
| Bug 2 | MCP ツール結果の日本語が Unicode エスケープのまま記録 | console.log 文字化け |
| Bug 3 | agent CLI 失敗時のエラーメッセージに原因が含まれない | 原因特定に時間がかかる |
| Bug 4 | モデルキャパシティエラーでリトライなし即死 | 一時的エラーで進捗喪失 |

Bug 1・2 は同一コードパス（`adapters.py` L62-67）、Bug 3・4 は `cli.py` のエラーハンドリング。

## インターフェース

### 入力

各 Bug の修正は内部実装の変更であり、公開インターフェース（CLI 引数、ワークフロー YAML スキーマ、`config.toml` スキーマ）の変更はない。

- **Bug 4 のリトライ設定**: 今回の Issue ではリトライ回数・バックオフをモジュール定数（`_MAX_RETRIES = 3`, `_BASE_DELAY = 30.0`）として固定する。`config.toml` や `Step` モデルへの外部化は行わない。理由: 現時点ではリトライ回数をワークフローごとに変えたい要件がなく、固定値で十分。必要が出たら別 Issue で対応する。

### 出力

- **Bug 1**: `result: null` の場合、`extract_text()` は `None` を返す（クラッシュしない）
- **Bug 2**: console.log に日本語が正しく出力される
- **Bug 3**: `CLIExecutionError` のメッセージに stdout 末尾のエラー情報が含まれる
- **Bug 4**: 一時的エラー時にバックオフ付きリトライ後、最終的に失敗すれば `CLIExecutionError` を raise

### 使用例

```python
# Bug 1: result が null でもクラッシュしない
event = {
    "type": "item.completed",
    "item": {
        "type": "mcp_tool_call",
        "result": None,
        "error": {"message": "resources/read failed"},
        "status": "failed",
    },
}
adapter = CodexAdapter()
assert adapter.extract_text(event) is None  # AttributeError ではなく None

# Bug 3: エラーメッセージに stdout の error イベントが含まれる
# CLIExecutionError: Step 'review-design' CLI exited with code 1:
#   Selected model is at capacity. Please try a different model.

# Bug 4: 一時的エラーはリトライされる
# [INFO] Step 'review-design' CLI failed (attempt 1/3): model at capacity. Retrying in 30s...
```

## 制約・前提条件

- **後方互換性**: 既存ワークフロー YAML に変更不要であること
- **Codex JSONL エラーイベント仕様**: Codex CLI はエラー時に以下の 2 種類のイベントを stdout に出力する。両方を収集対象とする:
  - `{"type": "error", "message": "..."}` — エラー発生直後に出力される
  - `{"type": "turn.failed", "error": {"message": "..."}}` — ターン終了時に出力される（`error.message` にメッセージが格納）
  - Issue #122 の実例では capacity エラーで両方が出力されている。`type: "error"` のみ拾うと `turn.failed` 側の情報を取りこぼす
- **プロセスモデル**: `subprocess.Popen` で起動し、`stream_and_log()` が stdout を行単位で処理する。エラー判定は `process.returncode` ベース
- **Unicode**: Python の `json.loads()` は Unicode エスケープを自動デコードするが、`json.dumps()` のデフォルトは `ensure_ascii=True` で再エスケープする

## 方針

### Bug 1: `result: null` の null 安全性

**対象**: `kaji_harness/adapters.py` L62-67（`CodexAdapter.extract_text()` 内の `mcp_tool_call` 処理）

```python
# Before
result = item.get("result", {})
contents = result.get("content", [])

# After
result = item.get("result")
if not result:
    return None
contents = result.get("content", [])
```

`dict.get(key, default)` はキーが存在し値が `None` の場合、`default` ではなく `None` を返す。`or {}` パターンまたは明示的 None チェックで対処する。明示的 None チェックのほうが意図が明確。

### Bug 2: Unicode エスケープの解消

**対象**: `kaji_harness/cli.py` `stream_and_log()` 内の console.log 書き出し

`json.loads()` は Unicode エスケープをデコード済み文字列に変換するため、`extract_text()` が返す文字列は既にデコード済みのはず。問題は `json.dumps()` でログに再書き出しする箇所があれば `ensure_ascii=False` を指定する必要がある点。

調査の結果、`stream_and_log()` は `extract_text()` の戻り値をそのまま `f_con.write()` しているため、`json.loads()` → `extract_text()` → `write()` のパスでは Unicode エスケープは発生しない。

問題は `stdout.log` に書き出す `f_raw.write(line)` の `line` が Codex CLI から直接出力された生 JSONL であり、Codex CLI 側が `ensure_ascii=True` で出力している場合、`stdout.log` には Unicode エスケープが残る。これは kaji 側では制御できない。

**対策**: `console.log` 出力が正しくデコードされていることを確認するテストを追加する。`stdout.log` は生ログとしてそのまま保持する（Codex CLI の出力フォーマットは kaji の管轄外）。

### Bug 3: エラーメッセージへの stdout エラー情報の包含

**対象**: `kaji_harness/cli.py` L79-80（`execute_cli()` のエラー raise）, `kaji_harness/cli.py` L84-146（`stream_and_log()`）, `kaji_harness/errors.py` L54-61（`CLIExecutionError`）

`stream_and_log()` が返す `CLIResult` にはテキスト抽出済みの `full_output` があるが、エラーイベントのテキストは現在 `extract_text()` で抽出されない。

**方針**:

1. `stream_and_log()` 内で以下の 2 種類のエラーイベントから `message` を収集する:
   - `{"type": "error", "message": "..."}` → `event["message"]`
   - `{"type": "turn.failed", "error": {"message": "..."}}` → `event["error"]["message"]`
2. `CLIResult` に `error_messages: list[str]` フィールドを追加（`models.py`）
3. `CLIExecutionError` 生成時に、stderr が空なら `error_messages` の末尾を使用

```python
# stream_and_log() 内のエラーイベント収集
error_messages: list[str] = []

# JSON パース成功後
event_type = event.get("type")
if event_type == "error":
    msg = event.get("message", "")
    if msg:
        error_messages.append(msg)
elif event_type == "turn.failed":
    msg = (event.get("error") or {}).get("message", "")
    if msg:
        error_messages.append(msg)

# cli.py execute_cli() 内
if process.returncode != 0:
    detail = result.stderr or "\n".join(result.error_messages[-3:])
    raise CLIExecutionError(step.id, process.returncode, detail)
```

### Bug 4: 一時的エラーのリトライ

**対象**: `kaji_harness/cli.py` `execute_cli()` 関数（L44-81）

**方針**: `execute_cli()` にリトライループを追加する。

#### リトライ対象

- `CLIExecutionError` のうち、`error_messages`（Bug 3 で追加）に一時的エラーパターンを含むもの
- リトライ不可: `CLINotFoundError`, `StepTimeoutError`, パターン不一致の `CLIExecutionError`

```python
_TRANSIENT_PATTERNS = ["at capacity", "rate limit", "overloaded", "try again"]

def _is_transient(error: CLIExecutionError) -> bool:
    msg = str(error).lower()
    return any(p in msg for p in _TRANSIENT_PATTERNS)
```

#### リトライ設定

モジュール定数として固定する。外部設定（`config.toml`, `Step` モデル）への外部化は行わない:

```python
_MAX_RETRIES = 3        # 最大リトライ回数（初回を含まず）
_BASE_DELAY = 30.0      # 初回バックオフ（秒）
```

#### timeout とバックオフの関係

- **timeout は attempt 単位で適用する**。各リトライ試行は独立した subprocess 起動であり、`step.timeout` / `default_timeout` はその 1 回の subprocess の wall-clock 制限として機能する。
- **バックオフ sleep は timeout の外側**。attempt 間の sleep（30s, 60s, 120s）は subprocess が終了した後に発生するため、timeout と干渉しない。
- **execute_cli() 全体の wall-clock**: 最悪ケースは `(timeout + backoff) × (max_retries + 1)` となる。例: timeout=300s, max_retries=3 の場合、最大 `300×4 + 30+60+120 = 1410s ≈ 24分`。これはワークフロー全体の実行時間に対して許容範囲。
- **runner.py への影響なし**: runner は `execute_cli()` の呼び出しを 1 回行うだけで、リトライは `execute_cli()` 内に閉じ込める。

```python
# execute_cli() の疑似コード
for attempt in range(_MAX_RETRIES + 1):
    try:
        return _execute_cli_once(step, prompt, workdir, ...)  # 現行の execute_cli() 本体
    except CLIExecutionError as e:
        if attempt == _MAX_RETRIES or not _is_transient(e):
            raise
        delay = _BASE_DELAY * (2 ** attempt)
        logger.warning("Step '%s' transient error (attempt %d/%d): %s. Retrying in %.0fs...",
                       step.id, attempt + 1, _MAX_RETRIES + 1, e, delay)
        time.sleep(delay)
```

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 実行時コード変更では Small / Medium / Large の観点を定義し、
> docs-only / metadata-only / packaging-only 変更では変更固有検証と
> 恒久テストを追加しない理由を明記する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### 変更タイプ
- 実行時コード変更（`adapters.py`, `cli.py`, `errors.py`, `models.py`）

### Small テスト

- **Bug 1 null 安全性**: `CodexAdapter.extract_text()` に `result: null`, `result` キー欠損, `result: {}` を渡してクラッシュしないことを検証
- **Bug 2 Unicode**: `mcp_tool_call` の result.content に日本語テキストを含むイベントで、`extract_text()` がデコード済み文字列を返すことを検証
- **Bug 3 エラーメッセージ**: `CLIExecutionError` 生成時に stderr 空 + error_messages あり の場合、メッセージに error_messages が含まれることを検証。`type: "error"` と `type: "turn.failed"` の両イベントから message を抽出できることを検証
- **Bug 4 一時的判定**: `_is_transient()` がパターン一致/不一致を正しく判定することを検証

### Medium テスト

- **Bug 1+2 stream_and_log 結合**: `result: null` を含む JSONL ストリームを `stream_and_log()` に流し、クラッシュせず console.log に正しい出力が記録されることを検証（ファイル I/O を伴う）
- **Bug 3 execute_cli 結合**: モック subprocess が非ゼロ終了 + stderr 空 + stdout に error イベントを出力する場合、`CLIExecutionError` のメッセージにエラー内容が含まれることを検証
- **Bug 4 リトライ結合**: モック subprocess が 1 回目は capacity エラー、2 回目は成功を返す場合、`execute_cli()` がリトライして成功することを検証。全リトライ失敗時に最終エラーが raise されることも検証

### Large テスト

- 実 API 疎通は不要。Bug 1-4 はすべて kaji 内部のエラーハンドリングロジックであり、外部サービスとの結合点はモック subprocess で十分検証可能。

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存アーキテクチャの範囲内の修正 |
| docs/ARCHITECTURE.md | なし | モジュール構成の変更なし |
| docs/dev/ | なし | 開発ワークフローの変更なし |
| docs/cli-guides/ | なし | CLI の外部インターフェース変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python dict.get() 仕様 | https://docs.python.org/3/library/stdtypes.html#dict.get | `get(key, default)` は key が存在し value が None の場合、None を返す（default ではない）。Bug 1 の根本原因 |
| Python json.loads() Unicode 処理 | https://docs.python.org/3/library/json.html#json.loads | `json.loads()` は `\uXXXX` エスケープを自動的に Python str にデコードする。Bug 2 で console.log パスが正常な根拠 |
| Python json.dumps() ensure_ascii | https://docs.python.org/3/library/json.html#json.dumps | デフォルト `ensure_ascii=True` で非 ASCII 文字を `\uXXXX` にエスケープする。Bug 2 で stdout.log に残る原因 |
| Issue #122 本文 | `gh issue view 122` | Bug 1-4 の再現条件・実際のイベントログ・修正案を記載 |
| Codex JSONL stdout 仕様 | kaji_harness/adapters.py L47-78 | `CodexAdapter` クラス。`extract_text()` L55-68 が `mcp_tool_call` の `result` を処理するコードパス |
| CLI 実行・ストリーミング | kaji_harness/cli.py L44-81, L84-146 | `execute_cli()` と `stream_and_log()` の実装。Bug 3・4 の修正対象 |
| 既存リトライパターン | kaji_harness/verdict.py | verdict パースの 3-stage fallback パターン。Bug 4 のリトライ設計の参考 |
