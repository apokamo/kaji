# [設計] step runner の terminal event 検知による blocking read 解消

Issue: local-p1-22

## 概要

`kaji_harness/cli.py:stream_and_log` が CLI サブプロセスの stdout EOF まで blocking read する設計のため、Claude セッションが `result`（terminal event）で正常完了済みでも子孫プロセスの fd leak 等で stdout が閉じないと `default_timeout` まで待ち続ける。`CLIEventAdapter` に `is_terminal_event(event) -> bool` を追加し、stream loop 側で受信時に break + `process.terminate()` させて解消する。

## 背景・目的

### Observed Behavior (OB)

`kaji run` で dev workflow (Issue `local-pc5090-21`) を実行中、`final-check` ステップが `Step 'final-check' timed out after 1800s` で終了した。
artifact `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/` を確認すると:

- `stdout.log` 末尾に `{"type":"result","subtype":"success","is_error":false,"duration_ms":418868,"terminal_reason":"completed","stop_reason":"end_turn",...}` が記録されており、Claude セッション自体は 7 分（≈ 418 秒）で正常終了している。
- `console.log` の最終書き込み時刻は 17:17、kaji の `StepTimeoutError` 発火は 17:42（= 17:10 開始 + 1800s timeout）。
- `console.log` に `[tool] Bash $ … && source .venv/bin/activate && …` と `[tool] Bash $ while kill -0 $(pgrep -f "make check" 2>/dev/null) 2>/dev/null; do sleep 5; done…` の痕跡があり、Claude が長時間プロセス (`make check`) を polling で待ったセッションだった。

つまり Claude のメインセッションが終了しても子孫プロセス（または background bash）が stdout fd を保持したため、`for line in process.stdout` (`kaji_harness/cli.py:159`) の iterator が EOF を返さず、kaji 側の `_kill_process` timer (`kaji_harness/cli.py:124`) が発火するまで block していた。

### Expected Behavior (EB)

stream-json で terminal event（Claude では `type == "result"`、Gemini では `type == "result"`、Codex では turn 完了相当）を受信した時点で、`stream_and_log` は for loop を break し、`process.terminate()` を発行、短い grace period (例: 5s) 内で `process.wait()` し、超過したら `kill()` する。

これにより:

- 健全なセッションは terminal event 受信直後に kaji 側でステップ完了として扱える。
- child process / fd leak のような CLI 側の挙動に kaji 全体が引きずられない。
- 既存の `step.timeout` / `default_timeout` は最終ガードとして温存される。

根拠（一次情報）:

- 観測 artifact `stdout.log` の `result` イベントが `terminal_reason: completed` を持つこと（後続に意味のあるイベントが流れない）。
- `kaji_harness/adapters.py` の `CLIEventAdapter` Protocol には既に `extract_session_id` / `extract_text` / `extract_cost` という event 内容判定の責務があり、`is_terminal_event` 追加は責務分離の延長線上。
- `kaji_harness/cli.py:_kill_process` は既に terminate → 5s wait → kill の手順で実装済みであり、これを「terminal event 検知時の正常後始末」にも流用できる。

### 再現手順（Steps to Reproduce）

実環境での fd leak 強制再現は claude CLI 側挙動に依存するため、テストレベルで以下の最小再現を採用する:

1. fake subprocess（または bash スクリプト）を用意し、以下を出力させる:
   - `{"type":"system","subtype":"init","session_id":"sess-leak"}`
   - `{"type":"assistant", ... }`
   - `{"type":"result","subtype":"success","is_error":false,...}` ← terminal event
   - その後 stdout を close せず長時間 sleep（例: 30s）
2. 短い `default_timeout`（例: 3s）で `execute_cli` を呼ぶ。
3. **修正前**: `StepTimeoutError` で失敗（terminal event を見ず timeout まで blocking）。
4. **修正後**: terminal event 受信直後に break + terminate し、3s 以内に成功で完了する。

実環境での再現条件（参考、強制再現は不要）:
- `local-pc5090-21` の `final-check` artifact を再実行（claude セッション内で `run_in_background: true` の long-running Bash + polling）。

## 根本原因（Root Cause）

`kaji_harness/cli.py:159` の `for line in process.stdout:` は Python の line-buffered iterator であり、stdout fd が close されるまで blocking する。Popen で起動した子プロセスがさらに孫プロセスを fork し、孫が親の stdout fd を継承したまま生存している場合、メインプロセスが exit しても fd は close されない。

stream-json プロトコルでは「session の論理的終了」と「stdout fd の物理的 close」は独立した概念であり、kaji 側は **論理的終了（terminal event）を観測した時点で iteration を打ち切り、物理的 close を能動的に促す（terminate → wait → kill）** べきだが、現行実装は後者だけに依存している。

いつから壊れているか: stream-json 対応導入時から。Claude / Codex / Gemini のいずれも terminal event を持つ仕様だが、kaji は EOF だけを終了シグナルとして使ってきた。

同じ原因で他に壊れる箇所:
- 全 step / 全 adapter（claude / codex / gemini）で同じ blocking read を経由するため、同種の fd leak は全 step で再現しうる。
- ただし claude CLI で `run_in_background` Bash + polling が頻発するため、現状実観測できているのは claude のみ。

## インターフェース

### CLIEventAdapter Protocol（変更）

`kaji_harness/adapters.py`:

```python
class CLIEventAdapter(Protocol):
    def extract_session_id(self, event: dict[str, Any]) -> str | None: ...
    def extract_text(self, event: dict[str, Any]) -> str | None: ...
    def extract_cost(self, event: dict[str, Any]) -> CostInfo | None: ...
    def is_terminal_event(self, event: dict[str, Any]) -> bool: ...   # ← 新規
```

各 adapter での判定（暫定。最終的な event 種別は実装フェーズで一次情報を再確認する）:

| Adapter | terminal 判定 | 根拠 |
|---------|--------------|------|
| `ClaudeAdapter` | `event.get("type") == "result"` | 観測 artifact `stdout.log` で `result` イベントが session 終了マーカーとして 1 度だけ出ることを確認 |
| `CodexAdapter` | `event.get("type") == "turn.completed"` を terminal とみなす（実装時に Codex の `--json` 仕様で session 終端を持つ別 event があれば差し替える） | 既存 `extract_cost` も `turn.completed` を採用 |
| `GeminiAdapter` | `event.get("type") == "result"` | `kaji_harness/adapters.py:144-151` の docstring に明記済み（stream-json: `result: {type: "result", status, stats:...}` が session 終端） |

### `stream_and_log`（変更）

戻り値 `CLIResult` の構造は不変。break 動作のみ追加:

```python
for line in process.stdout:
    f_raw.write(line); f_raw.flush()
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        ...
        continue
    # 既存の extract_session_id / extract_text / extract_cost / error event 処理
    ...
    if adapter.is_terminal_event(event):
        break
# break 抜け or 正常 EOF 後、後始末を行う:
if process.poll() is None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
```

`_execute_cli_once` の `process.wait()` (`kaji_harness/cli.py:129`) は冗長になるため整理する（`stream_and_log` 側で wait まで完結させる）。timer.cancel() の経路は維持。

後方互換性: terminal event を出さない CLI / 出る前に死ぬ CLI / `JSONDecodeError` だけが流れる経路は従来通り EOF or `_kill_process` timer で守られる。

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/adapters.py` | `CLIEventAdapter` Protocol に `is_terminal_event` を追加、3 adapter に実装を追加 |
| `kaji_harness/cli.py` | `stream_and_log` に terminal event break + terminate 後始末を追加。`_execute_cli_once` の wait 経路を整理 |
| `tests/test_adapters.py` | 各 adapter の `is_terminal_event` 単体テスト（Small） |
| `tests/test_cli_streaming_integration.py` | 再現テスト（terminal event 後 stdout を close しない fake CLI で timeout せず終了することを検証）（Medium） |

影響するコマンド: `kaji run`（全 workflow / 全 step）。

スコープ外（Issue 本文より）:
- claude CLI 側の background bash による fd leak 修正
- timeout 値の調整
- Codex / Gemini 実環境での同種 leak の網羅検証

## 方針（修正アプローチ）

最小侵襲で「terminal event を観測したら積極的に break + terminate」を実装する。

1. **adapters.py**: Protocol 拡張 + 3 実装追加。`Protocol` への method 追加は既存の adapter 登録（`ADAPTERS = {...}`）を破壊しないが、3 クラス全てに `is_terminal_event` を実装することで Protocol の structural typing を満たす。
2. **cli.py**: `stream_and_log` 内で event デコード後に `is_terminal_event` を見て break。break 経路でも EOF 経路でも、関数末尾で `process.poll() is None` のときに `terminate → wait(5) → kill` を行う。`_execute_cli_once` から `process.wait()` 重複呼び出しを除去（`stream_and_log` が wait まで完結）。timer は `_kill_process` 用に残す。
3. **timed_out との衝突回避**: `terminate()` を `stream_and_log` でも `_kill_process` でも呼ぶ可能性があるが、`Popen.terminate` は冪等（既に終了しているプロセスへの SIGTERM は no-op に近い）。`timed_out.is_set()` の判定は `_execute_cli_once` 末尾で従来通り行う。

リファクタは混ぜない（`stream_and_log` の責務再設計は別 Issue で扱う）。

## テスト戦略

### 変更タイプ

実行時コード変更（subprocess 制御 + adapter Protocol 拡張）。

### Small テスト（`tests/test_adapters.py`）

- `ClaudeAdapter.is_terminal_event`:
  - `{"type": "result", ...}` で True
  - `{"type": "assistant", ...}` / `{"type": "system", ...}` で False
  - 空 dict / `type` 欠落で False
- `CodexAdapter.is_terminal_event`:
  - `{"type": "turn.completed", ...}` で True（暫定。Codex 仕様再確認は実装フェーズ）
  - `{"type": "thread.started", ...}` / `{"type": "item.completed", ...}` で False
- `GeminiAdapter.is_terminal_event`:
  - `{"type": "result", ...}` で True
  - `{"type": "init", ...}` / `{"type": "message", ...}` で False

### Medium テスト（`tests/test_cli_streaming_integration.py`）

bug 設計ガイド準拠の **再現テスト（修正前 Red）** を 1 本以上含む:

- **再現テスト（必須）**: bash スクリプトで以下を出力する fake CLI を用意:
  ```
  echo '{"type":"system","subtype":"init","session_id":"sess-leak"}'
  echo '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
  echo '{"type":"result","subtype":"success","total_cost_usd":0.01}'
  exec sleep 30   # stdout fd を保持したまま 30s 待つ
  ```
  `execute_cli` を `default_timeout=3` で呼び、3 秒以内に成功で戻ることを assert。修正前は `StepTimeoutError`、修正後は `CLIResult.session_id == "sess-leak"` で完了。
- **正常終了 regression**: 既存 `test_claude_streaming_extracts_session_and_text` 等の「`result` 後すぐに exit する」ケースが従来通り PASS することを確認（regression なし）。
- **terminal event なし regression**: terminal event を出さないまま exit するパターン（CLI クラッシュなど）でも従来通り EOF 経路で正常完了することを確認。
- **timer 経路 regression**: terminal event を出さず stdout も閉じない fake CLI を `default_timeout=2` で呼び、`StepTimeoutError` が従来通り発火することを確認（最終ガードが効いている）。

### Large テスト

不要。実 CLI への疎通検証は本修正で導入される判定ロジックのカバレッジを増やさず、Medium の fake subprocess で十分（4 条件のうち 1, 3 を満たす: 独自ロジックは Medium で完全カバー、Large 追加で増える回帰検出情報がほとんどない）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | アーキテクチャレベルの方針変更ではなく既存層内の bug fix |
| `docs/ARCHITECTURE.md` | なし | 同上 |
| `docs/dev/` | なし | ワークフロー手順は不変 |
| `docs/reference/python/` | なし | コード規約は不変 |
| `docs/cli-guides/` | 軽微（要確認） | `kaji run` の timeout 挙動の説明があれば「terminal event 受信時は timeout を待たず break する」旨を追記する可能性あり。実装フェーズで確認 |
| `CLAUDE.md` | なし | 規約変更なし |
| `CHANGELOG.md` | あり（軽微） | bugfix 記載 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 観測 artifact (stdout.log) | `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/stdout.log` | `result` イベントが `terminal_reason: completed`, `duration_ms: 418868` を持ち session 終了マーカーとして機能。後続イベントなし |
| 観測 artifact (console.log) | `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/console.log` | `[tool] Bash $ ... while kill -0 ...; sleep 5` の痕跡から claude が background bash + polling を実行したセッションだったことを確認 |
| 既存実装 | `kaji_harness/cli.py:141-216` (`stream_and_log`) | `for line in process.stdout` が EOF まで blocking する構造を確認 |
| 既存実装 | `kaji_harness/cli.py:219-226` (`_kill_process`) | terminate → wait(5) → kill の手順を流用可能 |
| 既存実装 | `kaji_harness/adapters.py:13-18` (`CLIEventAdapter` Protocol) | extract_* 群と並べて `is_terminal_event` を追加するのが責務分離上自然 |
| 既存実装 | `kaji_harness/adapters.py:99-104` (`ClaudeAdapter.extract_cost`) | `result` event を既に session 終了相当とみなして cost 抽出している（terminal event 判定の根拠） |
| 既存実装 | `kaji_harness/adapters.py:144-151` (GeminiAdapter docstring) | stream-json の `result` event が session 終端である旨を明記 |
| 関連 Issue | `local-pc5090-21` | 本問題が観測された dev workflow 実行 |
| testing-convention | `docs/dev/testing-convention.md` | 4 条件と Small/Medium/Large 判定基準 |
