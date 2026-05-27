# [設計] Codex の terminal-success 後の非致命的 error event を失敗判定から外す

Issue: #196

## 概要

`kaji_harness/cli.py:_execute_cli_once` の terminal-seen 分岐における失敗判定を、
**adapter 単位で stream-level `error` event の致死性を決める contract** に置き換える。

- Codex: stream-level `{"type":"error"}` を recoverable とみなし、失敗判定に使わない（`turn.failed` のみ失敗）
- Claude / Gemini: 既存契約を維持。`error_messages` 経路の失敗判定は継続

これにより Issue #196 OB（Codex の `Reconnecting...` event 混入による誤失敗化）を
Codex に閉じた最小スコープで修正する。Claude / Gemini の挙動は一次情報の裏付けが
得られるまで現状維持とし、別 Issue で検討する余地を残す。

`error_messages` の収集ロジック (`stream_and_log`) と `CLIExecutionError` detail
フォールバック材料としての利用は全 adapter で維持する。

## 背景・目的

### Observed Behavior (OB)

`kaji run` の verify-design step が以下の流れで実行されたとき、Codex プロセスが
exit code 0 で `---VERDICT--- status: PASS` を出して正常終了したにも関わらず、
kaji harness が `CLIExecutionError` を raise してワークフローを中断する。

実 run artifact: `.kaji-artifacts/191/runs/2605262236/verify-design/`

`stdout.log` は同一 step 内の **複数 codex invocation** の append ログである。
失敗対象は **2 つ目以降の invocation**（先行 invocation の `turn.completed` で
`stream_and_log` は一度 break するが、その後の codex retry 内部処理で同一プロセスから
追加 event が流れている運用ログ。実際の harness 失敗ポイントは下記）。

失敗 invocation の JSONL シーケンス（`stdout.log:35-71`）:

1. `{"type":"thread.started","thread_id":"..."}` （セッション初期化）
2. （中略：ツール呼び出し等）
3. `{"type":"error","message":"Reconnecting... 2/5 (timeout waiting for child process to exit)"}` （`stdout.log:37`、resume 中の再接続通知 / recoverable）
4. （中略）
5. `{"type":"turn.completed","usage":{...}}` （`stdout.log:71`、終局 / 成功）

`stderr.log` は `codex_core::tools::router` の `os error 2` 1 行のみ（`exec_command` spawn 失敗。Codex 内部で復帰し継続）。`console.log` は末尾に `---VERDICT--- status: PASS` を出力。

実 raise される `CLIExecutionError` メッセージ:

```
Error: Step 'verify-design' CLI exited with code 0: 2026-05-26T14:06:09.419201Z ERROR
codex_core::tools::router: error=exec_command failed for `/bin/bash -lc "pwd && git status ..."`:
CreateProcess { message: "Rejected(\"Failed to create unified exec process: No such file or directory (os error 2)\")" }
```

`exited with code 0` の文字列が「process は 0 で終了したのに失敗扱い」していることを示す。

### Expected Behavior (EB)

Codex プロセスについては、`thread.started → ... → error(Reconnecting) → ... → turn.completed`
（exit 0）の場合、kaji harness は step を成功扱いし `CLIExecutionError` を raise しない。

Claude / Gemini については、本 Issue では契約変更しない（後述 § スコープ参照）。

根拠（一次情報）:

- **`kaji_harness/adapters.py:156-160` の `CodexAdapter.is_terminal_failure` 契約**:
  `event.get("type") == "turn.failed"` のみを失敗とする。`turn.completed` で終わった
  セッションは契約上「成功」
- **Codex `{"type":"error"}` event の運用観測**: `.kaji-artifacts/191/runs/2605262236/verify-design/stdout.log:37`
  に表れる `Reconnecting... 2/5` メッセージは Codex 内部 retry の通知であり、
  その後同一 thread の `turn.completed` で正常復帰している。fatal な失敗は
  Codex 側で `turn.failed` event として発火する契約（adapters.py:156-160）が存在する
  ため、stream-level `error` event を fatal と扱う必要はない
- **`kaji_harness/cli.py:152-159` の設計コメント**: 「terminal event を観測したら、
  その event を真実とする」原則を述べているが、後段で `error_messages` を
  失敗判定に組み込んでおり、Codex についてはこの原則と矛盾している

### Root Cause

問題のロジック: `kaji_harness/cli.py:160-165`

```python
if result.terminal_seen:
    if result.terminal_failure or result.error_messages:
        detail = result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"
        rc = process.returncode if process.returncode is not None else -1
        raise CLIExecutionError(step.id, rc, detail)
    return result
```

`result.terminal_failure or result.error_messages` の **後半が adapter 横断で適用** されている点が誤り。

`error_messages` は `stream_and_log` (`cli.py:227-236`) で `type == "error"` および
`type == "turn.failed"` イベントから集約される（`turn.failed` 経路は
`terminal_failure=True` でも別途検出されるため二重判定）。
Codex における `type == "error"` event は recoverable な通知を含むため、これを
adapter の特性を問わず一律「失敗根拠」にすると、Codex の terminal-success step
が誤って失敗化する。

#### いつから壊れているか

- `37d1c81` (Apr 5 2026, `fix: implement Codex robustness improvements for #122`) で
  Bug 3 として `error_messages` 収集と「stderr 空のときの detail フォールバック」が導入。
  この時点では集約のみで失敗判定には未使用
- `fe8df5f` (May 17 2026, `fix(cli): exclude terminate-after-terminal returncode from failure judgment`)
  で SIGTERM/exit 143 起因の誤判定を直す際、失敗判定を `terminal_failure or error_messages`
  に集約した。**ここで `error_messages` が失敗判定に昇格** し、本 Issue の OB が再現可能になった

#### 他に壊れている箇所がないか

- **非 terminal 経路** (`cli.py:166-169` の `if process.returncode != 0:`) は
  `error_messages` を detail フォールバックとしてのみ使い、失敗判定の根拠にしていない
  → 影響なし
- **Claude adapter**: 既存テスト `test_claude_success_terminal_with_error_event_still_raises`
  (`tests/test_cli_streaming_integration.py:788-817`, commit `fe8df5f`) が現状契約を
  明示的に固定している。Claude 公式 stream-json の `type:"error"` event スキーマ
  および recoverable / fatal の区別について一次情報を確認できていないため、
  本 Issue では契約変更しない（後述 § スコープ）
- **Gemini adapter**: stream-json は `type` ∈ {`init`, `message`, `result`} で、
  `type == "error"` event の発火条件について一次情報（公式仕様 / source）が
  得られていない。本 Issue では契約変更しない
- **`turn.failed` イベント**: Codex の `terminal_failure=True` 経路は引き続き
  raise する（後述）

### 目的

実害: Codex の verify / review 系 step が成功条件を満たしているのに誤って失敗扱いに
なり、後続 step が動かなくなる。回避策（`kaji run --from <next-step>` 手動スキップ）は
verdict を手動確認する必要があり、自動化が崩れる。

## 再現手順（Steps to Reproduce）

### 最小再現（恒久テストで使う形）

**単一 invocation 単一 terminal の前提を満たす入力にする** (MF-1 対応)。
`stream_and_log` は最初の terminal event で break するため、`turn.completed` を
複数置く構成では `error` event を取り逃がす。下記順序であれば、`turn.completed`
到達時点で `error_messages` に reconnection メッセージが積まれている状態となり、
現行コードでは失敗判定が走る。

1. 前提: pytest fixture で mock CLI スクリプトを用意し、以下の JSONL を順に stdout に出力させる
   - `{"type": "thread.started", "thread_id": "thr-x"}` （Codex `extract_session_id` 契約に整合: adapters.py:123-126）
   - `{"type": "error", "message": "Reconnecting... 2/5"}` （非致命的 error event）
   - `{"type": "turn.completed", "usage": {...}}` （terminal / 成功）
2. 操作: mock CLI を exit code 0 で終了させ、`execute_cli` を `CodexAdapter` で呼ぶ
3. 観測:
   - **修正前**: `result.error_messages == ["Reconnecting... 2/5"]`、`terminal_seen=True`、`terminal_failure=False` だが `error_messages` 経路で `CLIExecutionError` raise（FAIL）
   - **修正後**: 正常 return、`error_messages` は維持（観測性のため）、raise されない（PASS）

### 実 artifact による補助確認

`.kaji-artifacts/191/runs/2605262236/verify-design/stdout.log:35-71` の失敗 invocation
区間を抽出し、上記最小再現と等価な順序であることを設計検証段階で目視確認する
（CI 恒久テストではなく、設計妥当性確認のための補助手段）。

## インターフェース

### Adapter contract の追加

`CLIEventAdapter` (`kaji_harness/adapters.py:13-20`) に **default メソッド** を追加:

```python
class CLIEventAdapter(Protocol):
    ...既存メソッド...
    def treats_stream_error_as_failure(self) -> bool:
        """Stream-level `type:"error"` event を terminal-seen 分岐の失敗根拠とするか。

        - True (Claude / Gemini): 既存契約。terminal が success でも
          `error_messages` が non-empty なら `CLIExecutionError` を raise する
        - False (Codex): `error` event は recoverable 通知（Reconnecting 等）を
          含むため失敗根拠としない。`turn.failed` のみで失敗判定する

        `error_messages` の収集自体および `CLIExecutionError` detail のフォールバック
        利用は本フラグに依らず常に行う（観測性維持）。
        """
        ...
```

実装値:

| Adapter | 戻り値 | 根拠 |
|---------|--------|------|
| `ClaudeAdapter` | `True` | 既存挙動を保持。`test_claude_success_terminal_with_error_event_still_raises` の contract を維持 |
| `CodexAdapter` | `False` | Issue #196 OB および adapters.py:156-160 の `turn.failed`-only 失敗契約に整合 |
| `GeminiAdapter` | `True` | 既存挙動を保持（`type:"error"` event の発火有無に関する一次情報が無いため safe default） |

Protocol へのメソッド追加だが、`CLIEventAdapter` は kaji 内部 Protocol で外部公開
されていない（`docs/cli-guides/` などで公開 IF として記載されていない）ため
**internal API 変更** として扱う。

### `_execute_cli_once` の失敗判定変更

```python
# kaji_harness/cli.py:160-165
if result.terminal_seen:
    fail = result.terminal_failure
    if not fail and adapter.treats_stream_error_as_failure():
        fail = bool(result.error_messages)
    if fail:
        detail = result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"
        rc = process.returncode if process.returncode is not None else -1
        raise CLIExecutionError(step.id, rc, detail)
    return result
# 非 terminal 経路は変更なし
```

### 不変項目

- `execute_cli()` のシグネチャ・戻り値型
- `CLIResult` の field 構造（`error_messages` 含む）
- `CLIExecutionError` の detail フォーマット `stderr or error_messages[-3:] or "terminal failure"`
- 非 terminal 経路 (`not terminal_seen and returncode != 0`)
- `stream_and_log` の `error_messages` 収集ロジック（全 adapter 共通で収集継続）
- Claude / Gemini の terminal-seen 分岐失敗判定（既存契約維持）

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/adapters.py` | `CLIEventAdapter` Protocol に `treats_stream_error_as_failure() -> bool` を追加。`ClaudeAdapter` / `GeminiAdapter` は `True`、`CodexAdapter` は `False` を返す実装を追加 |
| `kaji_harness/cli.py:160-165` | `_execute_cli_once` の失敗判定を adapter delegation 形式に変更（§ インターフェース 参照） |
| `kaji_harness/cli.py:152-159` | 設計コメントを「`error_messages` 経路の扱いは adapter delegation で決まる」「Codex のみ recoverable 扱い」と書き換え（Issue #196 を参照） |
| `tests/test_codex_robustness.py` | **新規** Codex regression テスト（最小再現の JSONL シーケンス。修正前 FAIL → 修正後 PASS）を追加 |
| `tests/test_codex_robustness.py::TestStreamAndLogErrorMessages::test_error_messages_in_cli_execution_error` | jsonl が `{"type":"error",...}` + `{"type":"turn.failed",...}` を含み exit 1 で `turn.failed` 経路により raise される既存挙動を維持（assertion 不変）。docstring に「Codex で `treats_stream_error_as_failure()=False` でも `turn.failed` 経路は引き続き raise」を追記 |
| `tests/test_codex_robustness.py::TestStreamAndLogErrorMessages::test_error_event_collected` / `test_turn_failed_event_collected` | 不変（`error_messages` 収集ロジック自体は変更なし） |
| `tests/test_cli_streaming_integration.py::test_claude_success_terminal_with_error_event_still_raises` | **不変**（Claude は `treats_stream_error_as_failure()=True` のため既存契約継続） |
| `tests/test_cli_streaming_integration.py::test_no_terminal_event_nonzero_exit_raises` | 不変（非 terminal 経路は変更なし） |
| 新規 unit テスト（小） | `ClaudeAdapter().treats_stream_error_as_failure() is True` / `CodexAdapter()...is False` / `GeminiAdapter()...is True` の Small テスト |

スコープ外（別 Issue 候補。本 Issue では扱わない）:

- **Claude / Gemini の stream-level `error` event 契約見直し**: 公式 stream-json
  仕様での `error` event 意味論（recoverable / fatal の区別）に関する一次情報を
  収集してから別 Issue で検討する。本 Issue では既存契約を維持
- **ワークフローが `kaji-chore-191` を worktree として注入したが実体は `kaji-refactor-191` だった件**:
  Issue #191 の relabel に伴う worktree 名と branch 名の不整合。別レイヤの問題
- **`error_messages` の収集対象を recoverable / fatal で分類する機能拡張**: Codex は
  recoverable error を区別する公開仕様を持たない（`type:"error"` event は message
  文字列のみ）ため、event-level 分類は現時点で実装不可

## 方針（修正アプローチ）

### コア変更（最小侵襲、adapter 単位スコープ）

§ インターフェース の通り、`CLIEventAdapter` に default メソッドを追加し、
`_execute_cli_once` で adapter delegation する。条件分岐は 1 行追加のみ。

### コメントの更新（cli.py:152-159 付近）

- 旧: 「失敗は terminal event 自体の failure シグナル（`adapter.is_terminal_failure`）か、
  stream 中の error イベント集約（`error_messages`）でのみ判定する」
- 新: 「失敗は (1) terminal event 自体の failure シグナル（`adapter.is_terminal_failure`）
  または (2) adapter が `treats_stream_error_as_failure()=True` を返す場合の
  `error_messages` non-empty で判定する。Codex は (2) を false としており、
  stream-level `error` event は recoverable 通知（reconnection 等）として扱う
  （Issue #196）。`error_messages` は detail メッセージのフォールバック材料としては
  全 adapter で利用する」

### 観測性の維持

- `stream_and_log` の `error_messages` 収集 (`cli.py:227-236`) は変更なし
- `CLIExecutionError` detail のフォールバック順序 `stderr or error_messages[-3:] or "terminal failure"` は不変
- ログ書き出し（`stdout.log` / `stderr.log` / `console.log`）は不変

## テスト戦略

### 変更タイプ

実行時コード変更（cli.py の失敗判定ロジックと adapters.py の Protocol 拡張）

### Small テスト

**新規追加（1 ファイル / 3 case）**: adapter ごとに `treats_stream_error_as_failure()`
の戻り値を assert する。Protocol contract の宣言的検証で、外部依存なし。

- `ClaudeAdapter().treats_stream_error_as_failure() is True`
- `CodexAdapter().treats_stream_error_as_failure() is False`
- `GeminiAdapter().treats_stream_error_as_failure() is True`

これは `docs/dev/testing-convention.md` の Small テスト基準（純粋関数 / 外部依存なし）に該当。

失敗判定本体のロジック（`_execute_cli_once`）は subprocess streaming を伴うため
Medium で検証する（後述）。

### Medium テスト

`tests/test_codex_robustness.py` および `tests/test_cli_streaming_integration.py` に
追加・修正する。すべて mock CLI スクリプトで subprocess を立ち上げる Medium サイズ。

1. **新規 regression（Codex）**: 最小再現シーケンス
   `thread.started` → `error(Reconnecting... 2/5)` → `turn.completed`、exit 0
   - **修正前**: `CLIExecutionError` raise（FAIL）
   - **修正後**: 正常 return、`result.terminal_seen=True`、`result.terminal_failure=False`、
     `result.error_messages == ["Reconnecting... 2/5"]`（観測性維持）
   - 配置: `tests/test_codex_robustness.py::TestStreamAndLogErrorMessages` 直下に
     新規メソッド `test_codex_recoverable_error_then_terminal_success_returns` を追加
2. **既存維持（Codex `turn.failed` 経路）**: `tests/test_codex_robustness.py::test_error_messages_in_cli_execution_error`
   - jsonl: `{"type":"error",...}` + `{"type":"turn.failed",...}`、exit 1
   - `turn.failed` で `terminal_failure=True` → 引き続き raise。`error_messages` が
     detail にフォールバックされ "at capacity" 文字列を含むことを assert（既存どおり）
   - docstring を更新: 「Codex `treats_stream_error_as_failure()=False` でも `turn.failed`
     経路は引き続き raise」を明示
3. **既存維持（Claude 契約不変）**: `tests/test_cli_streaming_integration.py::test_claude_success_terminal_with_error_event_still_raises`
   - jsonl: `system/init` + `error` + `result(is_error:false, subtype:success)`、exit 0
   - `treats_stream_error_as_failure()=True` で従来どおり raise（assertion 不変）
   - docstring を補強: 「Claude は本 Issue #196 のスコープ外。一次情報の裏付けが
     得られるまで既存契約を維持」
4. **既存維持（収集ロジック）**: `tests/test_codex_robustness.py::TestStreamAndLogErrorMessages::test_error_event_collected` / `test_turn_failed_event_collected`
   - `result.error_messages` 配列に error event message が積まれることを verify。不変
5. **既存維持（非 terminal 経路）**: `tests/test_cli_streaming_integration.py::test_no_terminal_event_nonzero_exit_raises`
   - terminal event を出さず exit 1 する CLI は引き続き raise。不変

### Large テスト

不要。本修正は CLI subprocess streaming と adapter 契約の結合層に閉じており、
外部 API や実 CLI（`codex exec` 実コマンド）まで含む E2E 検証を新規追加しても
回帰検出情報量が増えない。`docs/dev/testing-convention.md` の 4 条件のうち
「新規テストを追加しても回帰検出情報がほとんど増えない」「想定される不具合パターンが
既存テストまたは既存品質ゲートで捕捉済み」（Small + Medium でカバー）に該当。

実 artifact (`.kaji-artifacts/191/runs/2605262236/`) による手動確認は設計検証段階で
1 度行えば足り、CI 恒久テストに昇格させる必要はない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | アーキテクチャ選定の変更ではない。adapter 契約の小幅拡張に伴うバグ修正 |
| docs/ARCHITECTURE.md | なし | 公開アーキテクチャの記述は不変 |
| docs/dev/workflow_guide.md / development_workflow.md | なし | ワークフロー手順は不変。step 失敗判定の internal なセマンティクス変更のみ |
| docs/reference/python/ | なし | コーディング規約・naming・error-handling・logging いずれにも影響なし |
| docs/cli-guides/ | なし | CLI 公開仕様（`kaji run` 等）の挙動は変わらない（むしろバグった挙動を正常化） |
| CLAUDE.md | なし | プロジェクト全体規約に変更なし |
| `kaji_harness/cli.py` 内コメント | あり | terminal_seen 分岐コメントを adapter delegation 方針に書き換え（既出: § 方針） |
| `kaji_harness/adapters.py` Protocol docstring | あり | 新規メソッド `treats_stream_error_as_failure` の docstring を追加（既出: § インターフェース） |

CHANGELOG: 本プロジェクトでは bug fix の CHANGELOG 運用は未確立のため記載不要
（`/release` skill 起動時にまとめて反映される運用と理解）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 実 run artifact (壊れた挙動) | `/home/aki/dev/kaji/main/.kaji-artifacts/191/runs/2605262236/verify-design/{stdout.log,stderr.log,console.log}` | OB の唯一の一次根拠。失敗 invocation 区間は `stdout.log:35-71`: `thread.started` → ... → `error: Reconnecting... 2/5` (`:37`) → ... → `turn.completed` (`:71`)、exit 0、`---VERDICT--- status: PASS`。これが kaji 側で誤って例外化された |
| `kaji_harness/cli.py:128-169` | リポジトリ内パス | 失敗判定ロジックの現状実装。`stream_and_log()` 呼出 → terminal-seen 分岐 → `terminal_failure or error_messages` 条件が本 Issue の根本原因 |
| `kaji_harness/cli.py:227-241` | リポジトリ内パス | `stream_and_log` の event ループ。`type == "error"` を `error_messages` に集約し、`is_terminal_event(event)` で **最初の** terminal event を見たら break する設計。最小再現テスト設計の制約根拠（terminal を 2 つ並べると最初で break するため `error` を取り逃がす） |
| `kaji_harness/adapters.py:108-117` | リポジトリ内パス | `ClaudeAdapter.is_terminal_event/is_terminal_failure` 契約。terminal は `type:"result"`、failure は `is_error:true` または `subtype:"error"`。stream-level `type:"error"` の意味論は明示されておらず、本 Issue のスコープ外とする根拠 |
| `kaji_harness/adapters.py:156-160` | リポジトリ内パス | `CodexAdapter.is_terminal_event/is_terminal_failure` 契約。terminal は `turn.completed` / `turn.failed`、failure は `turn.failed` のみ。「fatal は `turn.failed` で表現される」一次根拠 |
| `kaji_harness/adapters.py:193-201` | リポジトリ内パス | `GeminiAdapter.is_terminal_event/is_terminal_failure` 契約。terminal は `type:"result"`、failure は `status != "success"`。`type:"error"` event の発火条件は不明（一次情報なし） |
| `kaji_harness/adapters.py:123-126` | リポジトリ内パス | `CodexAdapter.extract_session_id` は `type == "thread.started"` の `thread_id` を返す。最小再現テストの session id event 名根拠（誤って `session.created` を使わない） |
| `tests/test_cli_streaming_integration.py:788-817` | リポジトリ内パス | `test_claude_success_terminal_with_error_event_still_raises`。Claude の既存 failure 契約 (`error_messages` 経路) を明示的に固定している。本 Issue では Claude を変更しないため、このテストは不変 |
| `tests/test_codex_robustness.py:270-343` | リポジトリ内パス | `TestStreamAndLogErrorMessages`（収集ロジック verify）と `test_error_messages_in_cli_execution_error`（`turn.failed` 経路で raise）。本 Issue の新規 Codex regression を同クラス直下に追加する配置根拠 |
| `commit fe8df5f` | `git show fe8df5f` (本リポジトリ) | terminal_seen 分岐の失敗判定を `terminal_failure or error_messages` に集約した変更点。本 Issue の regression introduction commit |
| `commit 37d1c81` | `git show 37d1c81` (本リポジトリ) | `error_messages` 収集ロジックを Bug 3 として追加した由来 commit。`error_messages` は本来「detail フォールバック材料」として導入されており、失敗判定の根拠にする設計ではなかったことの裏付け |
| `docs/dev/testing-convention.md` | リポジトリ内パス | テストサイズ判定基準（外部依存なし → Small / ファイル I/O・サブプロセス → Medium / 外部 API → Large）と、新規恒久テスト要否の 4 条件。Small テスト（adapter contract）と Medium テスト（subprocess 失敗判定）の使い分け根拠、および Large 不要判定の根拠 |
| `.claude/skills/_shared/design-by-type/bug.md` | リポジトリ内パス | bug 設計の必須セクション（OB / EB / 再現手順 / Root Cause / 再現テスト必須）。本設計書の構成根拠 |
