# [設計] terminal success 後の非致命的 error event を失敗判定から外す

Issue: #196

## 概要

`kaji_harness/cli.py:_execute_cli_once` の terminal event 観測後の失敗判定から
`result.error_messages` を外し、`result.terminal_failure` のみを失敗の真実とする。
Codex の resume セッション等で挟まる非致命的な `{"type":"error","message":"Reconnecting..."}`
等のストリームイベントがあっても、最終 terminal event が `turn.completed` であれば
step を成功扱いする。`error_messages` の収集自体（`stream_and_log`）と
`CLIExecutionError` detail フォールバック材料としての利用は維持する。

## 背景・目的

### Observed Behavior (OB)

`kaji run` の verify-design step が以下の流れで実行されたとき、Codex プロセスが
exit code 0 で `---VERDICT--- status: PASS` を出して正常終了したにも関わらず、
kaji harness が `CLIExecutionError` を raise してワークフローを中断する。

実 run artifact: `.kaji-artifacts/191/runs/2605262236/verify-design/`

- `stdout.log` の JSONL 流入順:
  1. `{"type":"turn.completed","usage":{...}}` （1 ターン目完了）
  2. `{"type":"error","message":"Reconnecting... 2/5 (timeout waiting for child process to exit)"}` （resume 中の再接続通知 / recoverable）
  3. `{"type":"turn.completed","usage":{...}}` （2 ターン目完了 / 終局）
- `stderr.log`: `codex_core::tools::router` の `os error 2` 1 行のみ（worktree 不在による
  `exec_command` spawn 失敗。Codex は Issue 本文の Worktree NOTE から実体パスを解決して継続）
- `console.log`: 最後に `---VERDICT--- status: PASS` を含む verdict を出力

実 raise されるメッセージ:

```
Error: Step 'verify-design' CLI exited with code 0: 2026-05-26T14:06:09.419201Z ERROR
codex_core::tools::router: error=exec_command failed for `/bin/bash -lc "pwd && git status ..."`:
CreateProcess { message: "Rejected(\"Failed to create unified exec process: No such file or directory (os error 2)\")" }
```

`exited with code 0` の文字列が「process は 0 で終了したのに失敗扱い」していることを示している。

### Expected Behavior (EB)

Codex プロセスが exit code 0 で正常終了し、最終 terminal event が `turn.completed`
（`CodexAdapter.is_terminal_failure(event) == False`）であれば、kaji harness は
step を成功扱いし `CLIExecutionError` を raise しない。

根拠（一次情報）:

- **kaji_harness/cli.py:152-159 のコメント**（プロジェクト内で確立済みの原則）:
  「terminal event を観測したら、その event を真実とする」「失敗は terminal event 自体の
  failure シグナル（`adapter.is_terminal_failure`）か、stream 中の error イベント集約
  （`error_messages`）でのみ判定する」とあるが、後者の `error_messages` 経路が
  OB のケースを誤って失敗化している。原則「terminal event を真実とする」に基づけば、
  terminal success 後の error 集約は失敗根拠から外すべき
- **kaji_harness/adapters.py:156-160 の契約**: `CodexAdapter.is_terminal_failure` は
  `event.get("type") == "turn.failed"` のみを失敗とする。`turn.completed` で終わった
  セッションは契約上「成功」
- **Codex `{"type":"error"}` の意味論**: stream-level の error event は recoverable な
  通知（reconnection / 子プロセス spawn 失敗のリトライ等）を含み、その後の
  `turn.completed` で正常復帰しうる。OB の `Reconnecting... 2/5` がまさにこのケース

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

`result.terminal_failure or result.error_messages` の **後半が誤り**。
`error_messages` は `stream_and_log` (`cli.py:227-236`) で `type == "error"` および
`type == "turn.failed"` イベントから集約される（`turn.failed` 経路は
`terminal_failure=True` でも別途検出されるため二重判定）。`type == "error"` 単体は
recoverable / fatal の区別なく全て集約されているため、recoverable な error event
が 1 件混じるだけで terminal success step を失敗化する。

#### いつから壊れているか

- `37d1c81` (Apr 5 2026, `fix: implement Codex robustness improvements for #122`) で
  Bug 3 として `error_messages` 収集と「stderr 空のときの detail フォールバック」が導入。
  この時点では集約のみで失敗判定には未使用
- `fe8df5f` (May 17 2026, `fix(cli): exclude terminate-after-terminal returncode from failure judgment`)
  で SIGTERM/exit 143 起因の誤判定を直す際、失敗判定を `terminal_failure or error_messages`
  に集約した。**ここで `error_messages` が失敗判定に昇格** し、本 Issue の OB が再現可能になった
  （fe8df5f のコミットメッセージ自体「`error_messages` のみに限定」と書いているが、
  「`error_messages` が失敗判定に含まれる」状態にして fix としていた経緯）

#### 他に壊れている箇所がないか

- **非 terminal 経路** (`cli.py:166-169` の `if process.returncode != 0:`) は
  `error_messages` を detail フォールバックとしてのみ使い、失敗判定の根拠にしていない
  → 影響なし
- **Claude adapter**: `ClaudeAdapter.is_terminal_failure` は `is_error: true` を直接観測する
  契約。`type == "error"` 単体 event は別経路。本 Issue と同じ failure mode が起こりうる
  （`test_claude_success_terminal_with_error_event_still_raises` が現状の挙動を assertion
  している）が、修正方針は同じ（terminal success なら成功扱い）
- **Gemini adapter**: `error_messages` 収集対象 (`type == "error" | "turn.failed"`) と一致する
  event を出さない（adapters.py の `GeminiAdapter` は別契約）→ 影響なし
- **`turn.failed` イベント**: `terminal_failure=True` でも raise されるため、`error_messages`
  経路を外しても失敗検出は保たれる（`is_terminal_failure` が真を返すため）

### 目的

実害: verify / review 系 step が成功条件を満たしているのに誤って失敗扱いになり、
後続 step が動かなくなる。回避策（`kaji run --from <next-step>` 手動スキップ）は
verdict を手動確認する必要があり、自動化が崩れる。

## 再現手順（Steps to Reproduce）

### 最小再現（恒久テストで使う形）

1. 前提: pytest fixture で mock CLI スクリプトを用意し、以下の JSONL を順に stdout に出力させる
   - `{"type": "session.created", "session_id": "sess-x"}` (Codex)
   - `{"type": "turn.completed", "usage": {...}}` （1 ターン目完了）
   - `{"type": "error", "message": "Reconnecting... 2/5"}` （非致命的 error event）
   - `{"type": "turn.completed", "usage": {...}}` （2 ターン目完了 / 終局）
2. 操作: mock CLI を exit code 0 で終了させ、`execute_cli` を `CodexAdapter` で呼ぶ
3. 観測:
   - 修正前: `CLIExecutionError` が raise（`error_messages` 経路で失敗判定）
   - 修正後: `result.terminal_seen == True`, `result.terminal_failure == False` で正常 return

### 実 artifact による再現

`.kaji-artifacts/191/runs/2605262236/verify-design/stdout.log` を `stream_and_log` に流す
ことで、現存の壊れた挙動が再現できる。設計検証のための補助手段として用いる
（恒久テストは上記最小再現で書く）。

## インターフェース

bug 修正のため公開 IF は不変:

- `execute_cli()` のシグネチャ・戻り値・raise 条件のうち、戻り値 `CLIResult` の field 構造は不変
- `CLIExecutionError` の raise 条件のみ変更:
  - 変更前: `terminal_seen and (terminal_failure or error_messages)` → raise
  - 変更後: `terminal_seen and terminal_failure` → raise
- 非 terminal 経路（`not terminal_seen and returncode != 0`）は不変
- `CLIResult.error_messages` field は維持（観測性のため）。`CLIExecutionError` detail
  フォールバックの順序 `stderr or error_messages[-3:] or "terminal failure"` も不変

### 使用例（kaji 内部の呼び出し側に変化なし）

```python
# 呼び出し側コードは変更不要。失敗判定のセマンティクスのみが変わる。
result = execute_cli(step, prompt, workdir, session_id, log_dir, "auto", True, 1800)
# terminal_failure が True の場合のみ CLIExecutionError が raise される
# 非致命的 error event が混じった terminal success は raise されない
```

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/cli.py:160-165` | `_execute_cli_once` の失敗判定条件式から `or result.error_messages` を除去。detail 文字列フォールバックは現状維持 |
| `kaji_harness/cli.py:152-159` | コメントを新方針に合わせて更新（「stream 中の error イベント集約でも判定」記述を削除） |
| `tests/test_cli_streaming_integration.py` | `test_claude_success_terminal_with_error_event_still_raises` の assertion を反転（raise しないこと / `terminal_seen=True` で return すること） |
| `tests/test_codex_robustness.py` | `test_error_messages_in_cli_execution_error` の jsonl は `turn.failed` を含むため raise する。assertion 自体は維持。ただしテスト意図（「stderr 空でも error_messages が detail に出る」）の re-explanation を docstring に追記 |
| `tests/test_codex_robustness.py` 等 | `TestStreamingFailureDetection` の `error_messages` 収集自体を verify するテストは触らない（収集ロジック自体は不変） |
| 新規 regression test (codex / claude 各 1 本) | terminal success + 中間 error event → 例外化されないことを assert |

スコープ外（別 Issue 候補。本 Issue では扱わない）:

- ワークフローが `kaji-chore-191` を worktree として注入したが実体は `kaji-refactor-191` だった件
  （Issue #191 の `type:refactor → type:chore` relabel に伴う worktree 名と branch 名の不整合）。
  これは別レイヤの問題で、本修正の前後どちらでも独立に発生する
- `error_messages` の収集対象を recoverable / fatal で分類する機能拡張。
  Codex は recoverable error を区別する公開仕様を持たない（事実上、`type: "error"`
  単体のイベントスキーマは message 文字列のみで分類フィールドなし）。本 Issue では
  「terminal event を真実とする」原則に従い、error_messages 全体を失敗判定から外す
  方針を採る

## 方針（修正アプローチ）

### コア変更（最小侵襲）

```python
# kaji_harness/cli.py:160-165
if result.terminal_seen:
    if result.terminal_failure:  # ← `or result.error_messages` を削除
        detail = result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"
        rc = process.returncode if process.returncode is not None else -1
        raise CLIExecutionError(step.id, rc, detail)
    return result
# 非 terminal 経路は変更なし
if process.returncode != 0:
    detail = result.stderr or "\n".join(result.error_messages[-3:])
    raise CLIExecutionError(step.id, process.returncode, detail)
return result
```

### コメントの更新（cli.py:152-159 付近）

- 旧: 「失敗は terminal event 自体の failure シグナル（`adapter.is_terminal_failure`）か、
  stream 中の error イベント集約（`error_messages`）でのみ判定する」
- 新: 「失敗は terminal event 自体の failure シグナル（`adapter.is_terminal_failure`）
  のみで判定する。stream 中の `error` event は recoverable な通知（reconnection 等）
  を含むため失敗根拠としない（Issue #196）。`error_messages` は detail メッセージの
  フォールバック材料としてのみ利用する」

### 観測性の維持

- `stream_and_log` の `error_messages` 収集ロジック (`cli.py:227-236`) は変更しない。
  失敗判定からは外すが、`CLIExecutionError` detail としては引き続き有用（特に stderr 空の Codex で）
- ログ書き出し（`stdout.log` / `stderr.log` / `console.log`）も変更なし

## テスト戦略

### 変更タイプ

実行時コード変更（cli.py の失敗判定ロジック修正）

### Small テスト

不要。本修正は subprocess streaming と adapter 契約を跨いだロジックの結合検証が
必要であり、Small（純粋関数 / モック完結）では失敗判定の真の挙動を保証できない。
失敗判定ロジック単体を関数抽出して Small で test する案は、過剰な抽象化を生み
最小侵襲方針に反する。`docs/dev/testing-convention.md` の 4 条件のうち
「想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み」を
Medium テスト（subprocess 経由）が担保する。

### Medium テスト

以下を `tests/test_cli_streaming_integration.py` または `tests/test_codex_robustness.py`
（既存ファイルの近傍に置く）に追加・修正する。すべて mock CLI スクリプトで
subprocess を立ち上げ、`stream_and_log` / `execute_cli` を実コードで走らせる
Medium サイズ（既存テストと同サイズ感）。

1. **新規 regression（Codex）**: terminal `turn.completed` + 中間 `{"type":"error","message":"Reconnecting..."}` + 終局 `turn.completed`、exit 0
   - **修正前**: `CLIExecutionError` が raise（FAIL）
   - **修正後**: 正常 return、`result.terminal_seen=True`、`result.terminal_failure=False`、
     `result.error_messages` には reconnection message が含まれる（観測性維持）（PASS）
2. **新規 regression（Claude）**: terminal `result` (success) + 中間 `{"type":"error","message":"..."}`、exit 0
   - **修正前**: `CLIExecutionError` raise（既存 `test_claude_success_terminal_with_error_event_still_raises` が現状 assert している挙動）
   - **修正後**: 正常 return
3. **既存テスト反転**: `tests/test_cli_streaming_integration.py::TestCLIStreamingIntegration::test_claude_success_terminal_with_error_event_still_raises`
   - 名前と assertion を「`_no_longer_raises`」相当に変更し、上記 2 と統合または個別保持
4. **既存テスト維持（`turn.failed` 経路は raise）**: `tests/test_codex_robustness.py::test_error_messages_in_cli_execution_error`
   - jsonl が `{"type":"error",...}` + `{"type":"turn.failed",...}` を含み exit 1。
   - `turn.failed` で `terminal_failure=True` → 引き続き raise。`error_messages` が
     detail にフォールバックされ "at capacity" 文字列を含むことを assert（既存どおり）
   - docstring を更新し「`turn.failed` 経路では引き続き raise」「`error_messages`
     は detail フォールバックとしてのみ利用」を明示
5. **既存テスト維持（収集ロジック自体）**: `tests/test_codex_robustness.py::TestStreamingFailureDetection`
   - `result.error_messages` 配列に error event message が積まれることを verify するテストは触らない
6. **既存テスト維持（非 terminal 経路）**: terminal event を出さずに exit 1 する CLI
   が引き続き `CLIExecutionError` を raise することを既存テスト
   （`test_no_terminal_event_nonzero_exit_raises`）が verify。変更なし

### Large テスト

不要。本修正は CLI subprocess streaming と adapter 契約の結合層に閉じており、
外部 API や実 CLI（`codex exec` 実コマンド）まで含む E2E 検証を新規追加しても
回帰検出情報量が増えない。`docs/dev/testing-convention.md` の 4 条件のうち
「新規テストを追加しても回帰検出情報がほとんど増えない」「想定される不具合パターンが
既存テストまたは既存品質ゲートで捕捉済み」（Medium テストでカバー）に該当。

実 artifact (`.kaji-artifacts/191/runs/2605262236/`) による手動確認は設計検証段階で
1 度行えば足り、CI 恒久テストに昇格させる必要はない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | アーキテクチャ選定の変更ではない。バグ修正に伴うロジック調整 |
| docs/ARCHITECTURE.md | なし | 公開アーキテクチャの記述は不変 |
| docs/dev/workflow_guide.md / development_workflow.md | なし | ワークフロー手順は不変。step 失敗判定の internal なセマンティクス変更のみ |
| docs/reference/python/ | なし | コーディング規約・naming・error-handling・logging いずれにも影響なし |
| docs/cli-guides/ | なし | CLI 公開仕様（`kaji run` 等）の挙動は変わらない（むしろバグった挙動を正常化） |
| CLAUDE.md | なし | プロジェクト全体規約に変更なし |
| `kaji_harness/cli.py` 内コメント | あり | terminal_seen 分岐コメントを「`error_messages` を失敗根拠にしない」方針に書き換え（既出: § 方針） |

CHANGELOG: 本プロジェクトでは bug fix の CHANGELOG 運用は未確立のため記載不要
（`/release` skill 起動時にまとめて反映される運用と理解）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 実 run artifact (壊れた挙動) | `/home/aki/dev/kaji/main/.kaji-artifacts/191/runs/2605262236/verify-design/{stdout.log,stderr.log,console.log}` | OB の唯一の一次根拠。`stdout.log` に `turn.completed` → `error: Reconnecting... 2/5` → `turn.completed` の順で JSONL が並び、process は exit 0 で `---VERDICT--- status: PASS` を出して終了している。これが kaji 側で誤って例外化された |
| `kaji_harness/cli.py:152-169` | リポジトリ内パス | 失敗判定ロジックの現状実装。「terminal event を真実とする」コメントと、`terminal_failure or error_messages` 条件の矛盾が本 Issue の根本原因 |
| `kaji_harness/cli.py:227-236` | リポジトリ内パス | `error_messages` 収集ロジック。`type == "error"` 単体の event を recoverable / fatal の区別なく全て集約していることが確認できる |
| `kaji_harness/adapters.py:156-160` | リポジトリ内パス | `CodexAdapter.is_terminal_failure` の契約。`turn.failed` のみを失敗とする。`turn.completed` で終わったセッションが「成功」であることの一次根拠 |
| `tests/test_cli_streaming_integration.py:788-817` | リポジトリ内パス | `test_claude_success_terminal_with_error_event_still_raises` が現状の **誤った** 挙動を assert している。本修正で assertion を反転 |
| `tests/test_codex_robustness.py:315-343` | リポジトリ内パス | `test_error_messages_in_cli_execution_error` は `turn.failed` event を含むため引き続き raise。assertion 維持で良いことの根拠 |
| `commit fe8df5f` | `git show fe8df5f` (本リポジトリ) | terminal_seen 分岐の失敗判定を `terminal_failure or error_messages` に集約した変更点。本 Issue の regression introduction commit |
| `commit 37d1c81` | `git show 37d1c81` (本リポジトリ) | `error_messages` 収集ロジックを Bug 3 として追加した由来 commit。`error_messages` は本来「detail フォールバック材料」として導入されており、失敗判定の根拠にする設計ではなかったことの裏付け |
| `docs/dev/testing-convention.md` | リポジトリ内パス | テストサイズ判定基準（外部依存なし → Small / ファイル I/O・サブプロセス → Medium / 外部 API → Large）と、新規恒久テスト要否の 4 条件（独自ロジックの増減・既存品質ゲート捕捉・回帰検出情報量・未追加理由の説明）。本設計の Small / Large 不要判定の根拠 |
| `.claude/skills/_shared/design-by-type/bug.md` | リポジトリ内パス | bug 設計の必須セクション（OB / EB / 再現手順 / Root Cause / 再現テスト必須）。本設計書の構成根拠 |
