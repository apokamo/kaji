---
id: local-p1-22
title: step runner が claude の result event 後 stdout 閉鎖待ちで step timeout する
state: closed
slug: stream-terminal-event-break
labels:
- type:bug
created_at: '2026-05-10T11:04:43Z'
closed_at: '2026-05-10T11:52:08Z'
closed_by: p1
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-fix-local-p1-22`
> **Branch**: `fix/local-p1-22`

## 設計書

<details>
<summary>クリックして展開</summary>

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

各 adapter での判定（success terminal / failure terminal / 非 terminal を一次情報で確定）:

| Adapter | success terminal | failure terminal | 非 terminal（intermediate） | 根拠（一次情報） |
|---------|-----------------|-----------------|----------------------------|-----------------|
| `ClaudeAdapter` | `type == "result"` (1 回限り) | 同上（`is_error: true` でも `result` は出る。session 終端マーカーは 1 種類） | `system` / `assistant` / `user` 等 | `docs/cli-guides/claude-code-cli-guide.md:467-474` および観測 artifact `.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/stdout.log` で `type:"result"`/`terminal_reason:"completed"` が session 末尾に 1 度だけ出ることを確認 |
| `CodexAdapter` | `type == "turn.completed"` | `type == "turn.failed"` | `thread.started` / `turn.started` / `item.started` / `item.completed` / `error` | `docs/cli-guides/codex-cli-session-guide.md:243-252` のイベントタイプ表で `turn.completed` / `turn.failed` をそれぞれ「ターン終了」「ターン失敗」と定義。`codex exec --json` は単一ターン実行であり、turn 終了 = session 終了。`error` は intermediate（`tests/test_codex_robustness.py:315-324` の挙動から `error` 単独では session が終わらず後続に `turn.failed` を伴う観測あり） |
| `GeminiAdapter` | `type == "result"` (1 回限り) | 同上（`status` フィールドで成功/失敗を区別） | `init` / `message` | `docs/cli-guides/gemini-cli-session-guide.md:623-631`（stream-json で `{type: "result", status, stats}` が session 終端）と既存 `kaji_harness/adapters.py:144-151` の docstring |

設計判断:

- **success/failure を区別せず両方とも terminal として break する**。理由: `is_terminal_event` は「session が論理的に終わったか」だけを返す責務であり、成功/失敗判定は `process.returncode` および `error_messages` で行う既存経路が独立に存在する（`kaji_harness/cli.py:135-137`）。
- **Codex の `error` event は terminal にしない**。理由: 既存テスト `tests/test_codex_robustness.py:272-324` が示す通り、`error` は `turn.failed` の前に流れる intermediate 性質。`error` で break すると後続の `turn.failed` の `error.message` が `error_messages` に集約されず、`CLIExecutionError` の詳細が薄くなる。

### `stream_and_log` と `_execute_cli_once`（変更）

**timer race の解消**: 現行 `kaji_harness/cli.py:117-131` では `timer = Timer(timeout, _kill_process, ...)` が armed のまま `stream_and_log` を呼んでおり、timer cancel は finally でしか起きない。terminal event 観測後に `process.terminate() → wait(5) → kill()` を行うと、timer の grace 5s と timeout がオーバーラップする時点（terminal event が timeout 直前に届いたケース）で timer が発火し、`timed_out` が立ってしまい「論理終端を見たのに `StepTimeoutError`」になる race が残る。

**解消設計**: `stream_and_log` が `terminal_seen: bool` を返し、`_execute_cli_once` 側で **break 検知後は timer を先に cancel してから後始末する**。これにより grace wait 中の timer 発火を構造的に排除する。

`CLIResult` モデル拡張（`kaji_harness/models.py`）:

```python
@dataclass
class CLIResult:
    full_output: str
    session_id: str | None
    cost: CostInfo | None
    stderr: str
    error_messages: list[str]
    terminal_seen: bool = False   # ← 新規（後方互換のためデフォルト False）
```

`stream_and_log`:

```python
def stream_and_log(...) -> CLIResult:
    terminal_seen = False
    ...
    for line in process.stdout:
        ...
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            ...
            continue
        # 既存の extract_session_id / extract_text / extract_cost / error event 収集
        ...
        if adapter.is_terminal_event(event):
            terminal_seen = True
            break
    # 後始末は呼び出し側 (_execute_cli_once) に委譲し、stderr 読み出しのみここで行う
    stderr = process.stderr.read() if process.stderr else ""
    if stderr:
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    return CLIResult(..., terminal_seen=terminal_seen)
```

`_execute_cli_once`（順序が race 防止の核心）:

```python
timed_out = threading.Event()
timer = threading.Timer(timeout, _kill_process, args=[process, timed_out])
timer.start()
try:
    log_dir.mkdir(parents=True, exist_ok=True)
    result = stream_and_log(process, adapter, step.id, log_dir, verbose)
    if result.terminal_seen:
        # 1) terminal event を見た → 即座に timer を disarm（race 防止の本丸）
        timer.cancel()
        # 2) プロセスが自発 exit していなければ、timeout に依存しない短い grace wait
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    else:
        # 通常経路: EOF まで読んだ → process.wait() で残骸回収（timer は最終ガード）
        process.wait()
finally:
    timer.cancel()

if timed_out.is_set():
    raise StepTimeoutError(step.id, timeout)
if process.returncode != 0:
    ...
```

**race 防止の保証**:
- terminal event を観測した瞬間に `timer.cancel()` が走る → grace 5s wait 中に `_kill_process` が呼ばれない → `timed_out` は立たない。
- `Timer.cancel()` は既に発火してしまった timer を止めない可能性があるが、その場合は `_kill_process` 内の `terminate()` が我々の `terminate()` と冪等に重なるだけで、`timed_out.is_set()` 判定がもはや正しい意味（= ユーザ視点でもタイムアウト）を持たない（terminal event が見えていれば成功扱いにすべき）。これを避けるため、`timed_out.is_set()` の評価を `terminal_seen` で短絡する追加ガードを入れる:

```python
if timed_out.is_set() and not result.terminal_seen:
    raise StepTimeoutError(step.id, timeout)
```

これで以下のいずれかが成り立つ:
1. terminal event 観測前に timer 発火 → `terminal_seen=False`, `timed_out=True` → `StepTimeoutError`（既存挙動）
2. terminal event 観測 → `terminal_seen=True` → `timer.cancel()` 成功 or 既に発火していても `StepTimeoutError` を抑制（terminal event が真実の終端）
3. 通常 EOF → `terminal_seen=False`, `timed_out=False` → 正常完了

後方互換性: terminal event を出さない CLI / 出る前に死ぬ CLI / `JSONDecodeError` だけが流れる経路は `terminal_seen=False` のままなので EOF or `_kill_process` timer による既存挙動。`CLIResult.terminal_seen` のデフォルト値 False により、既存テストや CLIResult 利用箇所への影響はない。

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/adapters.py` | `CLIEventAdapter` Protocol に `is_terminal_event` を追加、3 adapter に実装を追加（Codex は `turn.completed` / `turn.failed` の両方） |
| `kaji_harness/models.py` | `CLIResult` に `terminal_seen: bool = False` を追加（後方互換のためデフォルト False） |
| `kaji_harness/cli.py` | `stream_and_log` で terminal event 観測時に `terminal_seen=True` を返し、後始末は呼び出し側に委譲。`_execute_cli_once` で timer cancel → terminate → wait(5) → kill 順で race を解消、`timed_out and not terminal_seen` 短絡ガードを追加 |
| `tests/test_adapters.py` | 各 adapter の `is_terminal_event` 単体テスト（Small）。Codex は success/failure 両 terminal をカバー |
| `tests/test_cli_streaming_integration.py` | 再現テスト + race regression テスト + Codex failure terminal テスト（Medium） |

影響するコマンド: `kaji run`（全 workflow / 全 step）。

スコープ外（Issue 本文より）:
- claude CLI 側の background bash による fd leak 修正
- timeout 値の調整
- Codex / Gemini 実環境での同種 leak の網羅検証

## 方針（修正アプローチ）

最小侵襲で「terminal event を観測したら積極的に break + terminate、ただし timer race を構造的に排除する」を実装する。

1. **adapters.py**: `CLIEventAdapter` Protocol に `is_terminal_event` を追加。`ClaudeAdapter` / `GeminiAdapter` は `type == "result"`、`CodexAdapter` は `type in ("turn.completed", "turn.failed")` を返す。3 クラス全てで実装することで Protocol の structural typing を満たす。
2. **models.py**: `CLIResult` に `terminal_seen: bool = False` を追加。デフォルト False により既存テスト・既存利用箇所は影響を受けない。
3. **cli.py / stream_and_log**: event デコード後に `is_terminal_event` を見て break。後始末（terminate / wait / kill）は **呼び出し側に委譲**し、ここでは `terminal_seen` フラグを `CLIResult` に乗せて返すだけにする。
4. **cli.py / _execute_cli_once（race 解消の本丸）**:
   - `result.terminal_seen` が True の場合、**最初に `timer.cancel()` を呼ぶ**。これにより grace wait 中の `_kill_process` 発火を構造的に排除する。
   - その後 `process.poll() is None` なら `terminate → wait(5) → kill` で fd を閉じる。
   - 通常 EOF（`terminal_seen=False`）なら従来通り `process.wait()`（timer が最終ガード）。
   - 末尾の `timed_out` 判定は `not result.terminal_seen` で短絡し、cancel 失敗の極端ケースでも terminal event が真実の終端として尊重されるようにする。
5. **冪等性**: `Popen.terminate` は既に終了しているプロセスへの SIGTERM が no-op なため、`stream_and_log` 経路でも `_kill_process` 経路でも安全に重なる。

リファクタは混ぜない（`stream_and_log` の責務再設計は別 Issue で扱う）。

## テスト戦略

### 変更タイプ

実行時コード変更（subprocess 制御 + adapter Protocol 拡張）。

### Small テスト（`tests/test_adapters.py`）

- `ClaudeAdapter.is_terminal_event`:
  - `{"type": "result", "subtype": "success", ...}` で True
  - `{"type": "result", "subtype": "error", "is_error": true, ...}` で True（success/failure 共に session 終端）
  - `{"type": "assistant", ...}` / `{"type": "system", "subtype": "init"}` / `{"type": "user", ...}` で False
  - 空 dict / `type` 欠落で False
- `CodexAdapter.is_terminal_event`:
  - `{"type": "turn.completed", ...}` で True（success terminal）
  - `{"type": "turn.failed", "error": {...}}` で True（failure terminal）
  - `{"type": "error", "message": "..."}` で **False**（intermediate。後続に `turn.failed` を伴うため break しない）
  - `{"type": "thread.started", ...}` / `{"type": "turn.started"}` / `{"type": "item.completed", ...}` で False
- `GeminiAdapter.is_terminal_event`:
  - `{"type": "result", "status": "success", ...}` で True
  - `{"type": "result", "status": "error", ...}` で True
  - `{"type": "init", ...}` / `{"type": "message", ...}` で False

### Medium テスト（`tests/test_cli_streaming_integration.py`）

bug 設計ガイド準拠の **再現テスト（修正前 Red）** を含み、加えて timer race と Codex failure path の regression を検証:

1. **再現テスト（fd leak）— 必須**: bash スクリプトで以下を出力する fake CLI を用意:
   ```
   echo '{"type":"system","subtype":"init","session_id":"sess-leak"}'
   echo '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
   echo '{"type":"result","subtype":"success","total_cost_usd":0.01}'
   exec sleep 30   # stdout fd を保持したまま 30s 待つ
   ```
   `execute_cli` を `default_timeout=3` で呼び、3 秒以内に成功で戻ることを assert。修正前は `StepTimeoutError`、修正後は `CLIResult.session_id == "sess-leak"` で完了。
2. **timer race regression（重要）**: fake CLI が `result` を出した直後に `sleep 4`（grace wait 5s と timeout の境界をまたぐ条件）。`default_timeout=1`（terminal event は 0.5s 後、grace 4s）で実行し、`StepTimeoutError` ではなく成功で完了することを確認。これは `terminal_seen=True` 観測時の `timer.cancel()` が正しく機能していることを保証する。
3. **Codex failure terminal**: fake CLI が `{"type":"turn.failed","error":{"message":"capacity"}}` を出した後 stdout fd を保持して sleep。修正後は terminal event として break、`error_messages` に "capacity" を含み、`exit_code=1` 経由で `CLIExecutionError` が発火することを確認。
4. **Codex error は terminal 扱いにしない regression**: fake CLI が `{"type":"error",...}` の後に `{"type":"turn.failed",...}` を出すケース（既存 `tests/test_codex_robustness.py:315-324` と同形）で、`error` で break せず `turn.failed` まで読み切り `error_messages` に両方集約されることを確認。
5. **正常終了 regression**: 既存 `test_claude_streaming_extracts_session_and_text` 等が従来通り PASS（CLIResult.terminal_seen=True が新たに付くだけで、ほかは不変）。
6. **terminal event なし regression**: terminal event を出さず exit する fake CLI（CLI クラッシュ模擬）が EOF 経路で従来通り完了することを確認。
7. **timer 最終ガード regression**: terminal event なし & stdout 閉じない fake CLI を `default_timeout=2` で呼び、`StepTimeoutError` が発火することを確認（最終ガードが効いている）。

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

### Agent 別の正本（仕様ソース）

| Agent | 仕様ドキュメント | 確認ポイント |
|-------|----------------|------------|
| Claude Code | `docs/cli-guides/claude-code-cli-guide.md:467-474` | stream-json の最終結果が `type: "result"` であり、`subtype: "success" | "error"` と `terminal_reason` を持つ |
| Codex | `docs/cli-guides/codex-cli-session-guide.md:243-252` | イベントタイプ表で `turn.completed`（成功）、`turn.failed`（失敗）、`error`（エラーイベント = intermediate）が定義されている |
| Gemini | `docs/cli-guides/gemini-cli-session-guide.md:623-631` | stream-json で `{type: "result", status, stats}` が session 終端 |

### 観測 artifact / 既存実装

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 観測 artifact (stdout.log) | `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/stdout.log` | `result` イベントが `terminal_reason: completed`, `duration_ms: 418868` を持ち session 終了マーカーとして機能。後続イベントなし |
| 観測 artifact (console.log) | `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/console.log` | `[tool] Bash $ ... while kill -0 ...; sleep 5` の痕跡から claude が background bash + polling を実行したセッションだったことを確認 |
| 既存実装 | `kaji_harness/cli.py:117-138` (`_execute_cli_once`) | timer は finally でしか cancel されず、現状のままだと grace wait 中に `_kill_process` が発火する race を抱える |
| 既存実装 | `kaji_harness/cli.py:141-216` (`stream_and_log`) | `for line in process.stdout` が EOF まで blocking する構造を確認 |
| 既存実装 | `kaji_harness/cli.py:219-226` (`_kill_process`) | terminate → wait(5) → kill の手順を流用可能 |
| 既存実装 | `kaji_harness/adapters.py:13-18` (`CLIEventAdapter` Protocol) | extract_* 群と並べて `is_terminal_event` を追加するのが責務分離上自然 |
| 既存実装 | `kaji_harness/adapters.py:99-104` (`ClaudeAdapter.extract_cost`) | `result` event を既に session 終了相当とみなして cost 抽出 |
| 既存実装 | `kaji_harness/adapters.py:133-141` (`CodexAdapter.extract_cost`) | `turn.completed` の `usage` を抽出しており、turn.completed が session 末尾と一致する観測 |
| 既存実装 | `kaji_harness/adapters.py:144-151` (GeminiAdapter docstring) | stream-json の `result` event が session 終端である旨を明記 |
| 既存テスト | `tests/test_codex_robustness.py:272-324` | `error` 単独 or `error` → `turn.failed` の連続パターンが既存挙動として観測されており、Codex で `error` を terminal にしないと「`error` 後に来る `turn.failed` の `error.message` を `error_messages` に集約する」既存契約を破らない根拠 |
| 関連 Issue | `local-pc5090-21` | 本問題が観測された dev workflow 実行 |
| testing-convention | `docs/dev/testing-convention.md` | 4 条件と Small/Medium/Large 判定基準 |

</details>

## 概要

`kaji_harness/cli.py:stream_and_log` が Claude Code (stream-json) の `result` イベント受信後も stdout EOF を待ち続けるため、claude セッションが正常完了していてもステップが `default_timeout`（既定 1800s）まで blocking する。adapter に terminal event 検知 IF を追加し、stream loop 側で受信時に break + `process.terminate()` させることで解消する（案 B）。

## 目的

### Observed Behavior（OB）

Issue `local-pc5090-21` の dev workflow を `kaji run` で実行中、`final-check` ステップが `Step 'final-check' timed out after 1800s` で終了した。一方、artifact の `stdout.log` 末尾の `result` イベントは Claude セッションが 7 分で正常完了していたことを示している。

artifact: `.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/`

```
$ ls -la .kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/
-rw-r--r-- 1 aki aki   6743  5月 10 17:17 console.log
-rw-r--r-- 1 aki aki 290036  5月 10 17:42 stdout.log
```

`stdout.log` の最終 `result` イベント（要約）:

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 418868,
  "terminal_reason": "completed",
  "stop_reason": "end_turn",
  "session_id": "8540bd90-dece-407c-8fc9-b2d8bf8a82ea"
}
```

時系列:

| 時刻 | イベント |
|---|---|
| 17:10 | claude サブプロセス起動（`final-check` ステップ開始） |
| 17:17 | `result` event 出力（`duration_ms: 418868` ≈ 7 分、`terminal_reason: completed`）。`console.log` 最終書き込み |
| 17:17〜17:42 | stdout が閉じない 25 分間（`for line in process.stdout` が blocking） |
| 17:42 | `_kill_process` timer 発火 → SIGTERM → fd 閉鎖 → `StepTimeoutError` |

console.log には以下の痕跡があり、claude が `run_in_background: true` で `make check` を起動して polling loop で完了を待ったセッションだったことが分かる:

```
[tool] Bash $ cd /home/aki/dev/kaji/kaji-feat-local-pc5090-21 && source .venv/bin/activate && …
[tool] ScheduleWakeup
[tool] Bash $ while kill -0 $(pgrep -f "make check" 2>/dev/null) 2>/dev/null; do sleep 5; done…
```

推定原因（kaji 視点で対処すべき部分）: `for line in process.stdout` (`kaji_harness/cli.py:159`) は EOF まで blocking する設計であり、Claude セッションの完了シグナルを能動的に検知しない。child bash が stdout fd を保持したままだと、claude メインセッションが完了していても EOF が返らず、kaji 側のタイマーまで離脱できない。

### Expected Behavior（EB）

stream-json で `result`（terminal event）を受信した時点で、`stream_and_log` は for loop を break し、`process.terminate()` を発行して短い grace period (例: 5s) 内に EOF を取りに行く。grace period 内に exit しなければ kill する。これにより:

- 健全なセッションは `result` 受信直後に kaji 側でステップ完了として扱える
- child process leak のような claude CLI 側の挙動に kaji が引きずられない
- 既存のタイムアウト機構は最終ガードとして温存される

根拠:

- `kaji_harness/adapters.py` には既に `extract_session_id` / `extract_text` / `extract_cost` という event 内容を adapter で抽出する責務分離があり、`is_terminal_event(event)` を追加するのは設計上自然な拡張
- Claude Code stream-json の契約上、`result` イベントは session 終了マーカーであり、その後に意味のあるイベントは流れない
- `kaji_harness/cli.py:117-138` の `_execute_cli_once` には既に `_kill_process` ベースの強制終了経路があるため、break 後の terminate は既存パターンに沿う

### 再現手順（Steps to Reproduce）

確実な再現には claude 側で stdout fd を leak する条件が必要だが、観測された artifact から固定再現は可能:

1. 前提: kaji main repo (`/home/aki/dev/kaji/main`) に `.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/stdout.log` が存在
2. 単体テストレベル: `stream_and_log` に「`result` event を出した後 `process.stdout` を close せずに sleep する fake subprocess」を流し込み、現状実装が timeout まで blocking することを確認
3. 観測: 修正前は `_kill_process` 経由でしか終了しない / 修正後は `result` 受信直後に break して `process.terminate()` で正常終了する

実環境での再現条件（参考、強制再現は不要）:
- claude 側で `run_in_background: true` の Bash を起動し、`make check` のような長時間プロセスを polling で待つようなセッション
- 当該セッション終了時点で background bash の子孫プロセスが stdout fd を保持

## 完了条件

- [x] 設計書で根本原因（stdout EOF を待つ blocking read）と修正方針（terminal event 検知 → break → terminate）が特定されている
- [x] `CLIEventAdapter` に `is_terminal_event(event: dict[str, Any]) -> bool` を追加し、Claude / Codex / Gemini 各 adapter で適切なイベント種別（claude: `type == "result"` 等）を判定している
- [x] `stream_and_log` が terminal event 観測後に for loop を break し、`process.terminate()` → 短い grace period の `wait(timeout=...)` → 必要なら `kill()` の順で後始末する
- [x] 再現テストが 1 本以上追加され、修正前は FAIL（`StepTimeoutError` または timeout）、修正後は PASS することを確認
- [x] 既存の正常終了経路（subprocess が自発的に EOF を返すケース）でも従来と等価な挙動になることをテストで確認（regression なし）
- [x] 既存の timeout（`step.timeout` / `default_timeout`）経路はそのまま残り、terminal event を見ない adapter（あるいは未対応 event）でも従来通り timeout で守られている
- [x] `make check` 通過
- [x] 影響モジュール（`tests/test_cli.py` 等の cli / adapter 関連テスト）が green

## 影響範囲（初期評価）

- 主修正: `kaji_harness/cli.py`（`stream_and_log` の break 条件と後始末）
- 主修正: `kaji_harness/adapters.py`（`CLIEventAdapter` ABC + `ClaudeAdapter` / `CodexAdapter` / `GeminiAdapter` の `is_terminal_event` 実装）
- テスト: `tests/` 配下の cli / adapters 関連、新規 terminal event テスト
- 影響するコマンド: `kaji run`（全 workflow 全 step）
- 深刻度: 軽微〜中（成果物には影響しないが、stdout fd を保持する子孫プロセスが発生したセッションごとに `default_timeout` 分の wall clock を浪費。今回は 1 step で 25 分 / 1 セッション）
- 回避策: ステップ単位で `step.timeout` を短く設定すれば最大ロスは抑制できるが、根本解決ではなく、健全な長時間ステップを誤って打ち切るリスクと表裏

## スコープ外

- claude CLI 側の background bash による stdout fd leak バグ修正（kaji の責務外、上流で別途対処）
- timeout 値の調整（症状ではなく原因に対処する Issue のため）
- Codex / Gemini で同種 leak が発生した場合の adapter 実装の網羅検証（terminal event 検知 IF を入れること自体は本 Issue で扱い、各 adapter の event 種別調査は実装フェーズで行う）

## 参考

- 観測 artifact: `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/{console.log,stdout.log}`
- 関連実装:
  - `kaji_harness/cli.py:117-138`（`_execute_cli_once`）
  - `kaji_harness/cli.py:141-216`（`stream_and_log`）
  - `kaji_harness/cli.py:219-226`（`_kill_process`）
  - `kaji_harness/adapters.py:1-176`（`CLIEventAdapter` と各 adapter 登録）
- 関連 Issue: `local-pc5090-21`（本問題に遭遇した dev workflow 実行）
- Claude Code stream-json 仕様: `result` event が `terminal_reason` を持つ session 終了マーカーである旨は artifact 側の event ペイロードで確認済み
