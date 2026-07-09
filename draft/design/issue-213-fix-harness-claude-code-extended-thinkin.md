# [設計] Claude Code Extended Thinking 400 エラーの transient retry 化

Issue: #213

## 概要

`kaji run` の agent CLI 実行経路で、Claude Code の Extended Thinking 会話状態不整合に由来する Anthropic API 400 を transient error として扱う。Claude の terminal `result` event に含まれる API error detail を `CLIExecutionError` まで伝搬し、その full detail を `_is_transient()` が判定することで、既存の backoff retry 機構に乗せる。

## 背景・目的

### Observed Behavior (OB)

Issue #213 に記載された実ログでは、`design` step の Claude Code 実行中に Anthropic API が 400 を返し、step が retry されずに `ERROR` 終了している。

```text
API Error: 400 messages.3.content.71: `thinking` or `redacted_thinking` blocks in the latest assistant message cannot be modified. These blocks must remain as they were in the original response.
Error: Step 'design' CLI exited with code 1: terminal failure
```

実ログは main worktree の `/home/aki/dev/kaji/main/.kaji-artifacts/192/runs/2605282259/design/console.log` に残っている。Anthropic 公式の Errors / Extended thinking ドキュメントも、最新 assistant message 内の `thinking` / `redacted_thinking` block が編集・削除・再構築された場合に 400 `invalid_request_error` が返る、と説明している。

現行コードでは `ClaudeAdapter.is_terminal_failure()` が `result.is_error=true` を失敗として扱い、`CLIExecutionError` を送出する。しかし `_TRANSIENT_PATTERNS` は `at capacity` / `rate limit` / `overloaded` / `try again` のみで、Extended Thinking block 改変エラーを transient と判定しない。

さらに `_is_transient()` は `str(error)` を小文字化して判定している一方、`CLIExecutionError.__init__()` は exception message に `stderr[:200]` だけを埋め込む。今回のような長い API error では、判定したい phrase が 200 文字より後ろに現れると検出漏れする。

レビューで確認した実 artifact では、`run.log` の workflow error は `CLIExecutionError: Step 'design' CLI exited with code 1: terminal failure` であり、API error 文言を含まない。`design/stderr.log` も存在しない。API error 全文は `design/stdout.log` の Claude stream JSON にあり、特に terminal event `{"type":"result","is_error":true,"result":"API Error: 400 ... thinking ... redacted_thinking ..."}` に保持されている。したがって、単に `_is_transient()` を `error.stderr` に切り替えるだけでは足りず、Claude terminal failure detail の抽出経路を追加する必要がある。

### Expected Behavior (EB)

Claude Code 側の会話状態不整合が原因の Extended Thinking block 改変 400 は、ユーザー入力や kaji の workflow 定義が恒久的に壊れている状態ではなく、新規 agent session で再試行すれば回復しうる一時的失敗として扱う。

`execute_cli()` は既に `_is_transient()` が `True` を返す `CLIExecutionError` に対し `_MAX_RETRIES=3` の backoff retry を行う。したがって、今回の変更では Claude adapter が terminal `result.result` の API error を `CLIResult.error_messages` へ渡し、`CLIExecutionError.stderr` に full detail として保持させる。そのうえで `_is_transient()` が `CLIExecutionError.stderr` の全文を判定対象にし、公式エラー文言に対応する十分に具体的な pattern を追加することで、既存 retry ループを再利用する。

## 再現手順

1. `provider.type='github'` で、Claude Code を `agent=claude`, `model=opus`, `effort=high` 以上として起動する workflow step を実行する。現行の代表例は `.kaji/wf/dev.yaml` の `design` step と `.kaji/wf/dev-thorough.yaml` の `design` step。
2. Extended Thinking と tool use を伴う長い multi-turn 実行中に、Claude Code が最新 assistant message の `thinking` / `redacted_thinking` block を改変した request を Anthropic API に送る。
3. Anthropic API から 400 が返り、Claude Code が `stdout.log` に `type:"result", is_error:true, result:"API Error: 400 ... thinking ... redacted_thinking ... cannot be modified"` を出力する。
4. 現行 `stream_and_log()` は `type:"result"` の `result` field を `error_messages` に収集しないため、`_execute_cli_once()` は `CLIExecutionError` detail に `"terminal failure"` を渡す。
5. 現行 `_is_transient()` は `str(error)` の切り詰め済み文字列を判定し、かつ API error 文言も届いていないため、`execute_cli()` は retry せずに `CLIExecutionError` を上位へ伝播する。

実世界障害ログが存在するため、実装前に同じ外部 API 障害を合成再現することは必須にしない。恒久回帰テストは `_is_transient()` の判定契約を直接固定する。

## 根本原因

1. `kaji_harness/cli.py` の `_TRANSIENT_PATTERNS` が、Anthropic 公式エラー文言および実ログに出ている Extended Thinking block 改変エラーを含んでいない。
2. `kaji_harness/cli.py` の `_is_transient()` が `str(error)` を判定対象にしているため、`kaji_harness/errors.py` の `CLIExecutionError` が保持している full `stderr` ではなく、200 文字で切り詰められた exception message に依存している。
3. `stream_and_log()` は `type:"error"` と `type:"turn.failed"` の message だけを `CLIResult.error_messages` に収集する。Claude Code の今回の failure は terminal `type:"result"` event の `result` field に API error detail を持つため、`_execute_cli_once()` の terminal failure detail が `result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"` の最後まで落ち、API error 文言が `CLIExecutionError.stderr` に伝搬しない。

`git blame -L 33,39 -- kaji_harness/cli.py` では、現行の transient pattern と `_is_transient()` 判定は `37d1c81e4fb077130ea132ae57bb935e792dcbc4` (`fix: implement Codex robustness improvements for #122`) 由来である。これは当時の Codex capacity / rate limit 系の transient retry を対象にした実装で、Claude Code の Extended Thinking 400 は同じ分類に含まれていなかった。

同根調査として、retry 可否を判定する箇所は `execute_cli()` 内の `_is_transient()` 呼び出し 1 箇所だけである。ただし retry 判定に必要な detail は `stream_and_log()` が組み立てる `CLIResult.error_messages` を経由するため、adapter と stream collection の責務境界も修正対象に含める。`ScriptExecutionError` や deterministic `exec_script` 経路には agent CLI retry 判定が存在しないため、本 Issue の修正対象外とする。

## インターフェース

### 入力

- 内部関数 `_is_transient(error: CLIExecutionError) -> bool`
- `CLIEventAdapter.extract_error_message(event: dict[str, Any]) -> str | None`: CLI 固有 event から failure detail を取り出す adapter method
- `CLIResult.error_messages`: stream event 由来の failure detail list
- `CLIExecutionError.stderr`: agent CLI 失敗時に保持される full detail
- `_TRANSIENT_PATTERNS`: transient として扱う小文字 pattern の内部定義

### 出力

- Claude terminal `result` event の `result` field が `CLIResult.error_messages` を経由して `CLIExecutionError.stderr` に渡る
- `_is_transient()` が Extended Thinking block 改変 400 に対して `True` を返す
- `execute_cli()` が既存 retry loop により同エラーを retry 対象にする
- public CLI 引数、workflow YAML schema、Issue / PR provider API は変更しない

### 使用例

```python
event = {
    "type": "result",
    "is_error": True,
    "result": (
        "API Error: 400 messages.3.content.71: "
        "`thinking` or `redacted_thinking` blocks in the latest assistant message "
        "cannot be modified."
    ),
}
assert ClaudeAdapter().extract_error_message(event) == event["result"]

err = CLIExecutionError(
    "design",
    1,
    (
        "API Error: 400 messages.3.content.71: "
        "`thinking` or `redacted_thinking` blocks in the latest assistant message "
        "cannot be modified."
    ),
)
assert _is_transient(err) is True
```

## 制約・前提条件

- 既存の `_MAX_RETRIES` / `_BASE_DELAY` / retry loop は変更しない。
- `session_id=None` の step は retry ごとに fresh agent invocation になるため、本 Issue の主対象である `design` step の回復可能性と整合する。`session_id` を明示している step の session 継続仕様はこの Issue では変更しない。
- Pattern は `"thinking"` 単体のような広すぎる文字列にしない。Anthropic 公式エラーと実ログに共通する phrase に限定し、無関係な thinking 表示や通常の model output を retry 対象にしない。
- `CLIExecutionError.__str__()` の user-facing 200 文字切り詰めは維持する。変更するのは transient 判定の入力だけにする。
- 実世界障害ログは main worktree の `/home/aki/dev/kaji/main/.kaji-artifacts/192/...` 配下の過去 run artifact であり、恒久テスト fixture としてコピーしない。テストでは必要最小限の Claude stream JSON event を inline fixture として使う。
- Claude Code stream JSON の synthetic assistant error text も同じ API error を含むが、責務境界は terminal `result` event の `result` field とする。terminal event は `is_terminal_failure()` の判定対象と同じ event であり、failure detail の source of truth として扱いやすいため。

## 方針

1. `kaji_harness/adapters.py` の `CLIEventAdapter` に `extract_error_message(event: dict[str, Any]) -> str | None` を追加する。
   - `ClaudeAdapter`: `type:"result"` かつ `is_error is True` または `subtype:"error"` のとき、`result` field が non-empty string なら返す。必要に応じて `type:"error"` の `message` も返す。
   - `CodexAdapter`: 既存の `type:"error"` の `message` と `type:"turn.failed"` の `error.message` を返す。`treats_stream_error_as_failure()=False` の contract は変えず、観測性と `turn.failed` detail のために収集は続ける。
   - `GeminiAdapter`: 現行挙動を壊さないため、既知の `message` / `error` 文字列があれば返し、無ければ `None` とする。
2. `stream_and_log()` の hard-coded `event_type == "error"` / `turn.failed` collection を `adapter.extract_error_message(event)` 呼び出しに置き換え、返値があれば `error_messages` に append する。
3. `kaji_harness/cli.py` の `_TRANSIENT_PATTERNS` に、Anthropic の Extended Thinking block 改変エラーに十分具体的に一致する pattern を追加する。
   - 候補: ``"`thinking` or `redacted_thinking` blocks in the latest assistant message cannot be modified"``
   - 必要なら backtick なし variant を加えるが、まずは実ログ・公式 docs と一致する backtick あり phrase を優先する。
4. `_is_transient()` の判定対象を `str(error)` から `error.stderr` の全文へ切り替える。
   - `CLIExecutionError.stderr` が空の場合だけ defensive fallback として `str(error)` を使う。
   - lower-case matching の既存挙動は維持する。
5. 既存の retry loop / logging / terminal failure 判定は変えない。
6. `tests/test_codex_robustness.py` に focused regression test を追加する。
   - `ClaudeAdapter.extract_error_message()` が terminal `result` event から API error を抽出すること。
   - Extended Thinking block 改変 400 が transient と判定されること。
   - 200 文字より後ろに pattern が現れても full `stderr` 判定で transient と判定されること。
   - 実際の Claude failure event shape を使った `execute_cli()` retry が 2 attempt 目で成功すること。

## テスト戦略

### 変更タイプ

- 実行時コード変更

### 実行時コード変更の場合

#### Small テスト

- `tests/test_codex_robustness.py::TestIsTransient` に、Anthropic API 400 の `thinking` / `redacted_thinking` block 改変エラーを `CLIExecutionError.stderr` に持つケースを追加する。
- `CLIExecutionError.stderr` の先頭 200 文字以内に transient pattern が出ない長文ケースを追加し、`str(error)` には pattern が含まれないことと `_is_transient(error) is True` の両方を確認する。
- `ClaudeAdapter.extract_error_message()` に、`{"type":"result","is_error":true,"result":"API Error: 400 ... thinking ..."}` 形式の event を渡し、API error detail が返ることを確認する。
- `CodexAdapter` の既存 `type:"error"` / `turn.failed` detail 収集が `extract_error_message()` 経由でも維持されることを、既存 test の更新または追加で確認する。
- 既存の `at capacity` / `rate limit` / `overloaded` / `try again` / permanent error の判定が変わらないことを既存テストで確認する。

#### Medium テスト

- `tests/test_codex_robustness.py` に Claude stream JSON の failure→success 2 attempt test を追加する。1 attempt 目は `type:"result", is_error:true, result:"API Error: 400 ... thinking ... redacted_thinking ..."` を出す mock script、2 attempt 目は Claude success `result` event を出す mock script とし、`execute_cli()` が retry して 2 attempt 目の result を返すことを確認する。
- 既存 `TestExecuteCLIRetry` は `_is_transient()` が `True` を返す `CLIExecutionError` で retry する contract を保持するため、今回の Claude event-shape test と併せて retry loop と detail propagation の両方を担保する。
- 変更後の影響範囲確認として、少なくとも `pytest tests/test_codex_robustness.py -q` を実行する。

#### Large テスト

- 新規 Large test は追加しない。Anthropic API 400 は Claude Code 内部の multi-turn 会話状態不整合に依存し、CI で安定再現できる外部 API test にできない。
- `docs/dev/testing-convention.md` の分類上、外部 API 疎通を伴う再現は Large だが、Issue 本文と `.kaji-artifacts/192/runs/2605282259/design/console.log` に OB を直接示す実世界障害ログがあるため、bug design guide の escape clause により実装前 Red の代替証跡として扱う。

### 品質ゲート

- コミット前: `source .venv/bin/activate && make check`
- 開発中の focused check: `pytest tests/test_codex_robustness.py::TestIsTransient -q`
- Event-shape focused check: `pytest tests/test_codex_robustness.py -k "Claude" -q`
- 影響モジュール確認: `pytest tests/test_codex_robustness.py -q`

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定や architecture decision は発生しない |
| docs/ARCHITECTURE.md | なし | agent CLI 実行経路の責務分割は変えない |
| docs/dev/ | なし | workflow 手順や skill lifecycle は変えない |
| docs/reference/ | なし | Python 規約・公開 API 仕様の変更ではない |
| docs/cli-guides/ | なし | CLI 引数やユーザー操作手順は変えない |
| AGENTS.md / CLAUDE.md | なし | agent 作業規約の変更ではない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #213 本文 | `kaji issue view 213 --json body,labels,comments` | OB / EB / 完了条件の正本。Claude Code Extended Thinking 400 が retry されない問題、`stderr` 全文判定の必要性、追加テスト条件が明記されている |
| 実世界障害ログ（console） | `/home/aki/dev/kaji/main/.kaji-artifacts/192/runs/2605282259/design/console.log` | 2026-05-28 の `design` step で、Anthropic API 400 と `thinking` / `redacted_thinking` block 改変エラーが実際に出力された証跡 |
| 実世界障害ログ（stdout） | `/home/aki/dev/kaji/main/.kaji-artifacts/192/runs/2605282259/design/stdout.log` | API error 全文が Claude stream JSON の synthetic assistant text と terminal `type:"result", is_error:true, result:"API Error: 400 ..."` に存在する証跡 |
| 実世界障害ログ（run） | `/home/aki/dev/kaji/main/.kaji-artifacts/192/runs/2605282259/run.log` | workflow error は `CLIExecutionError: Step 'design' CLI exited with code 1: terminal failure` であり、現行 detail には API error が伝搬していない証跡 |
| Anthropic Errors: Thinking blocks cannot be modified | `https://docs.anthropic.com/en/api/errors#thinking-blocks-cannot-be-modified` | 最新 assistant message の `thinking` / `redacted_thinking` block が編集・削除・再構築されると 400 `invalid_request_error` が返る、という API 側仕様 |
| Anthropic Extended Thinking: Preserving thinking blocks | `https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks` | tool use を伴う multi-turn conversation では thinking block を元のまま API に戻す必要があり、変更すると 400 になる根拠 |
| transient 判定の現行実装 | `kaji_harness/cli.py` | `_TRANSIENT_PATTERNS` と `_is_transient()`、および `execute_cli()` の retry loop の正本 |
| CLI event adapter の現行実装 | `kaji_harness/adapters.py` | `ClaudeAdapter.is_terminal_failure()` は `result.is_error=true` を failure と判定するが、`result.result` を error detail として抽出しない現行責務境界 |
| CLIExecutionError の detail 保持 | `kaji_harness/errors.py` | `CLIExecutionError.stderr` は full detail を保持するが、exception message は `stderr[:200]` に切り詰める |
| 既存 transient / retry tests | `tests/test_codex_robustness.py` | `_is_transient()` と `execute_cli()` retry の既存検証先。新規 regression test の追加場所 |
| テスト規約 | `docs/dev/testing-convention.md` | Small / Medium / Large の分類、実行時コード変更では回帰テストが必要、Large 外部 API 再現の扱いを判断する根拠 |

## 完了条件の段階確認

- [x] 設計書で根本原因を特定: `_TRANSIENT_PATTERNS` の不足、`_is_transient()` の `str(error)` 依存、Claude terminal `result.result` が `CLIExecutionError` detail に伝搬していない点を根本原因として記載した。
- [x] パターン追加または別回復経路: `ClaudeAdapter.extract_error_message()` で terminal `result.result` を収集し、`_TRANSIENT_PATTERNS` に公式エラー phrase を追加して既存 retry loop を使う方針を記載した。
- [x] 全文 detail 判定: `_is_transient()` が `CLIExecutionError.stderr` の全文を見る方針と、その `stderr` に API error を入れる detail propagation 方針を記載した。
- [x] 追加 pattern の unit test: `TestIsTransient` と `ClaudeAdapter.extract_error_message()` の focused regression test を追加する方針を記載した。
- [x] 200 文字より後ろの pattern test: 長文 `stderr` case を追加する方針を記載した。
- [x] 実際の Claude failure event shape の test: terminal `type:"result", is_error:true, result:"API Error: 400 ..."` を使った `execute_cli()` retry test 方針を記載した。
- [x] 影響モジュール test: `pytest tests/test_codex_robustness.py -q` を品質ゲートとして記載した。
- [x] `make check`: コミット前品質ゲートとして記載した。
