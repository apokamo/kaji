# [設計] terminate-after-terminal の後始末で Claude Code の SIGTERM trap exit (143) を誤って失敗扱いしない

Issue: #25

## 概要

`kaji_harness/cli.py:_execute_cli_once` の terminal-event 後の後始末ブロックが、
terminal `result` event（`subtype:success`）を観測した後でも `process.returncode > 0`
を失敗根拠に採用するため、SIGTERM を trap して shell 慣例の exit 143 を返す
Claude Code CLI を成功ステップなのに `CLIExecutionError` として誤判定する。本修正は
terminal event 観測後の分岐から returncode 由来の失敗判定（`self_exit_failure`）を
取り除く。

## 背景・目的

### Observed Behavior (OB)

成功した `implement` ステップが `CLIExecutionError` で異常終了する。
実ログ `.kaji-artifacts/23/runs/2605142356/run.log`（Issue #25 本文より引用）:

```
{"ts": "2026-05-14T15:19:02.262369+00:00", "event": "workflow_end", "status": "ERROR",
 "error": "CLIExecutionError: Step 'implement' CLI exited with code 143: terminal failure"}
```

同 run の `stdout.log` 末尾には Claude 側の成功 result event が記録されている:

```json
{"type":"result","subtype":"success","is_error":false,"terminal_reason":"completed",
 "duration_ms":354173,"stop_reason":"end_turn"}
```

別プロジェクト（kamo2 gl:4）でも kaji 0.10.0 で同一不具合を再現。`design` /
`fix-ready` ステップが `CLI exited with code 143: terminal failure` で終了し、
`console.log` には `---VERDICT---` が `status: PASS` で完備していた（Issue #25
コメント 2026-05-17 より）。エラーメッセージ末尾の `terminal failure` は
`cli.py:160` の fallback 文字列であり、`result.stderr` / `result.error_messages`
がいずれも空であることを示す。したがって `cli.py:159` の OR 条件で True に
なり得るのは **`self_exit_failure` のみ**であることが確定している。

### Expected Behavior (EB)

terminal `result` event を観測した後は、その event を真実とする。kaji 自身が
後始末で `process.terminate()` を撃ったプロセスの returncode は、CLI 実装の
SIGTERM ハンドリング方式（`-15` / `143` / `137` のいずれか）に依らず失敗根拠から
除外する。失敗判定は terminal event 自体の failure シグナル
（`adapter.is_terminal_failure`）と stream 中の `error` イベント集約
（`error_messages`）のみで行う。

EB の一次情報的裏付け:

- `cli.py:156` のコメント「我々が後始末で SIGTERM した結果の returncode（< 0）は
  失敗根拠にしない（terminal event を真実とする）」— bc34906 が宣言した設計意図
  そのもの。本修正は実装をこのコメント意図に一致させる。
- Python `subprocess`: SIGTERM で kill されたプロセスの `returncode` は負値
  （`-15`）になる（[公式ドキュメント](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.returncode)）。
  bc34906 のコメントはこの慣例（`< 0`）のみを前提にしていた。
- Shell の終了ステータス慣例: シグナル `N` で終了したプロセスを shell が
  reap すると `128 + N` を返す（[Bash Reference Manual, Exit Status](https://www.gnu.org/software/bash/manual/bash.html#Exit-Status)）。
  Claude Code CLI は SIGTERM を **trap して** この慣例で `128 + 15 = 143`
  （正の整数）を返すため、bc34906 が前提にした「`< 0`」と一致しなかった。

### なぜ修正が必要か

`self_exit_failure = process.returncode is not None and process.returncode > 0`
（`cli.py:157`）は「正の returncode = CLI が自発的に異常終了した」と仮定する。
しかし terminal event 観測後に kaji が撃った `terminate()` に対し、Claude Code が
SIGTERM を trap して `143`（正）で exit すると、この仮定が崩れ、成功ステップが
例外化される。bc34906 (2026-05-10) で `terminate-after-terminal` 経路が導入されて
以降のリグレッションであり、それ以前は本経路自体が存在しなかった。

## 再現手順

誤判定のトリガは特定経路ではなく **「terminal `result` event 観測後、kaji が
`process.poll()` を呼んだ時点で Claude Code プロセスがまだ生存している」**という
レース条件である。

1. Claude Code が terminal `result` event（`subtype:success`）を stdout に書き出す
2. kaji が当該行を読み終え stream loop を break（`cli.py:238`）、`timer.cancel()`
   後に `process.poll()` を呼ぶ（`cli.py:132`）
3. このとき Claude Code が自身の cleanup（stdio MCP サーバーの切断 reap、
   background subprocess の reap 等）を終えておらず `process.poll() is None`
   → kaji が `process.terminate()` を撃つ（`cli.py:133`）
4. Claude Code が SIGTERM を trap し exit 143 → `process.returncode = 143`
5. `cli.py:157` で `self_exit_failure = (143 > 0) = True`
6. `cli.py:159` の `if result.terminal_failure or self_exit_failure or
   result.error_messages:` が True → `CLIExecutionError` を raise

ステップ 3 でプロセスが生存する要因（レースで kaji が勝つ要因）は複数あり、
いずれでも発生する:

- **stdio MCP サーバーの切断 cleanup 遅延**: serena 等の stdio MCP サーバーへ
  shutdown を送り reap する cleanup が `result` event 送出後に走る。kamo2 の
  `fix-ready` ステップ（実時間約 64 秒、`error` イベント皆無、MCP 4 件接続）でも
  発生しており、ステップ長や tool エラーの有無に依らない。
- **background subprocess を抱えたまま `end_turn`**: deferred tool を先行ロード
  せず呼び `InputValidationError` を踏んだケース等。

対照的に codex ステップは terminal event 後に自発的に stdout を閉じ exit するため
`poll()` 時点で既に終了済み → `terminate()` がスキップされ発生しない。failure が
claude ステップに限定される観測事実とも整合する。

## 根本原因（Root Cause）

| 項目 | 内容 |
|------|------|
| 問題箇所 | `kaji_harness/cli.py:157` `self_exit_failure = process.returncode is not None and process.returncode > 0` と `cli.py:159` の OR 条件 |
| なぜ間違いか | `cli.py:156` のコメントは「SIGTERM-killed → returncode < 0」という Python subprocess 慣例のみを前提に防御を書いた。実際の Claude Code CLI は SIGTERM を trap し shell 慣例の正値 143 で exit するため、`returncode > 0` 判定が成立してしまい、防御が機能しない |
| いつから | bc34906 (2026-05-10) `fix(cli): break stream loop on terminal event to avoid stdout EOF wait`。`terminate-after-terminal` 経路と `self_exit_failure` 判定を同時に導入 |
| 同根の他箇所 | `_execute_cli_once` の terminal-event 後の後始末ブロックに限定。非 terminal 経路（`cli.py:164` `if process.returncode != 0:`）は terminate を撃たないため同根の欠陥なし。`_kill_process`（timeout 経路）は `timed_out` フラグで別判定されており影響なし |

## インターフェース

bug 修正のため公開インターフェースは不変。`_execute_cli_once` / `execute_cli` /
`stream_and_log` のシグネチャ・戻り値型（`CLIResult`）はいずれも変更しない。

### 振る舞いの変更（変更前 / 変更後）

`result.terminal_seen is True` の分岐における失敗判定:

| 条件 | 変更前 | 変更後 |
|------|--------|--------|
| terminal success event + `returncode` が `143` / `137` / `-15` 等（kaji が terminate） | `CLIExecutionError`（誤判定） | `CLIResult` を返す（PASS） |
| terminal success event + 自発 exit で `returncode > 0`（kaji は terminate せず） | `CLIExecutionError` | `CLIResult` を返す（PASS） |
| terminal failure event（`subtype:error` / `is_error:true` 等） | `CLIExecutionError` | `CLIExecutionError`（不変） |
| stream 中に `error` / `turn.failed` イベントあり（`error_messages` 非空） | `CLIExecutionError` | `CLIExecutionError`（不変） |
| terminal event なし + `returncode != 0` | `CLIExecutionError` | `CLIExecutionError`（不変） |

後方互換性の評価: 呼び出し側（`dispatcher` 等）は `CLIResult` か例外を受けるだけで、
判定ロジックには依存しない。本来の terminal 失敗（`terminal_failure` /
`error_messages`）は引き続き例外化されるため、誤判定の解消のみが観測される変化。

> **設計判断（要レビュー）**: 上表 2 行目「terminal success event + 自発 exit
> `returncode > 0`」は、kaji が terminate していなくても `terminal_seen` 分岐では
> 失敗扱いしなくなる。これは Issue #25「設計方針メモ」の case ①
> （「`result` が `success` なら cleanup ノイズで失敗扱いすべきでない」）に沿った
> **意図的な振る舞い変更**である。詳細は「方針」§ 既存テストへの影響を参照。

## 制約・前提条件

- 修正対象は `kaji_harness/cli.py` の terminal-event 後の後始末ブロック
  （`cli.py:152-167` 近傍）に限定する（Issue #25 スコープ境界）。
- スコープ外: adapter 側の terminal event 判定（`is_terminal_event` /
  `is_terminal_failure`）、Claude Code CLI 側の挙動変更、deferred tool 利用方針。
- bc34906 が `terminate-after-terminal` を導入した目的（stdout EOF の無制限待ち
  回避）を退行させない。stream loop の break と `timer.cancel()` は維持する。
- `terminal_failure` と `error_messages` による失敗判定は従来どおり保持する
  （Issue #25 完了条件）。

## 方針

### 採用案: 案 B（`terminal_seen` 分岐から returncode 由来判定を除外）

Issue #25「設計方針メモ」および #25 コメントで提示された案 A / B / C のうち、
**案 B** を採用する。

`result.terminal_seen is True` の分岐では、失敗判定材料を
`result.terminal_failure`（terminal event 自体の failure シグナル）と
`result.error_messages`（stream 中の error イベント集約）の 2 つに限定し、
`process.returncode` 由来の `self_exit_failure` を判定材料から完全に外す。
`self_exit_failure` 変数は terminal-seen 分岐でのみ使用されているため、変数定義
（`cli.py:157`）ごと削除する。非 terminal 経路（`cli.py:164`）は
`process.returncode` を直接参照しており変数に依存しないため不変。

擬似コード（変更後の `_execute_cli_once` 末尾）:

```python
# 失敗判定:
#  - terminal event を観測したら、その event を真実とする。kaji が後始末で
#    撃った terminate の returncode は、CLI の SIGTERM ハンドリング方式
#    （-15 / 143 / 137 等）に依らず失敗根拠にしない。
#  - 失敗は terminal event 自体の failure シグナル（adapter.is_terminal_failure）
#    か、stream 中の error イベント集約（error_messages）でのみ判定する。
if result.terminal_seen:
    if result.terminal_failure or result.error_messages:
        detail = result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"
        rc = process.returncode if process.returncode is not None else -1
        raise CLIExecutionError(step.id, rc, detail)
    return result
# terminal event なし: 従来どおり returncode で判定
if process.returncode != 0:
    detail = result.stderr or "\n".join(result.error_messages[-3:])
    raise CLIExecutionError(step.id, process.returncode, detail)
return result
```

`cli.py:152-156` のコメント（失敗判定の優先順位 1〜3）も、項目 2「process が
自発的に正の終了コードで exit」が terminal 分岐から消えるため、上記擬似コードの
コメントに置き換える。

### 案 A を採らない理由

案 A（`kaji_terminated` フラグを導入し、kaji が terminate したケースのみ
`self_exit_failure` から除外）は案 B の上位互換に見えるが、案 B より追加で捕捉する
のは「terminal event 観測済み・`poll()` 時点で既に死亡・`returncode > 0`」の
ケースのみ。このケースは ① terminal event が `success` なら cleanup ノイズで
失敗扱いすべきでない、② terminal event が `error` なら `terminal_failure` が既に
捕捉する、のいずれかに帰着し、案 A の追加分岐は実在の失敗を 1 件も捕捉しない。
分岐が増えるだけのため不採用（Issue #25 設計方針メモと同結論）。

### 案 C（terminate 前の grace wait）を本 Issue では採らない理由

案 C（`process.poll() is None` 直後に `process.wait(timeout=N)` の有界 grace を
入れ、正常系で SIGTERM を撃たない）は #25 コメント・設計方針メモで「併用推奨」と
されている。本 Issue では**採用せず、別 Issue に切り出す**。理由:

- 誤判定そのものは案 B 単独で完全に解消する（Issue #25 設計方針メモ明記）。
  案 C は誤判定修正に不要。
- 案 C が解決するのは MCP サーバーの graceful shutdown（orphan 子プロセス /
  異常終了ログの抑制）という**別の品質課題**であり、本 Issue の bug（誤判定）
  とは関心が異なる。bug 修正に品質改善を混在させない（最小侵襲）。
- 案 C は grace 時間 `N` という調整パラメータを新設し、生存中プロセスを持つ
  全 claude ステップの終了タイミング挙動を変える。`N` が MCP cleanup を実際に
  カバーするかは計測を要し、独立の検証スコープを持つ。
- Issue #25 自身が関連課題（deferred tool 利用方針、ToolSearch 先行ロード明文化）
  を別 Issue に分離する方針を取っており、案 C の分離はこれと整合する。

→ 案 C は本 Issue 完了後にフォローアップ Issue として起票することを推奨する
（「影響範囲外の発見」ではなく、設計判断として明示的に分離）。

### 既存テストへの影響（重要）

bc34906 で追加された既存テスト
**`tests/test_cli_streaming_integration.py::TestTerminalEventBreak::test_claude_success_terminal_with_self_exit_nonzero_raises`**
（`test_cli_streaming_integration.py:634`）は、

> 「`result` が success でも CLI が自発的に exit 1 した場合は `CLIExecutionError`」

を assert しており、案 B 採用後はこの期待が成立しない（terminal success 観測後は
`returncode` を見ないため例外を投げない）。これは Issue #25 設計方針メモ case ①
が「誤った前提」と判定したケースそのものであり、本テストは bc34906 が埋め込んだ
バグの前提を固定化している。

→ 実装フェーズで本テストの**期待値を反転**する（success terminal + 自発
`exit > 0` → 例外ではなく `CLIResult` を返す）。テスト名・docstring も新挙動に
合わせて改名する。これはテスト退行ではなく、本設計に基づく**意図的な期待値変更**
である。review-code フェーズでこの変更が設計通りであることを確認できるよう、
本節に根拠を残す。

他の既存テストは不変で PASS する見込み:

- `test_claude_failure_terminal_raises_cli_execution_error`: `subtype:error` で
  `terminal_failure=True` → 引き続き例外。
- `test_codex_turn_failed_is_terminal` / `test_codex_error_event_does_not_break_early`:
  `error_messages` 非空 → 引き続き例外。
- `test_nonzero_exit_raises_cli_execution_error`: terminal event なし経路 → 不変。
- `test_claude_terminal_event_breaks_before_eof` / `test_terminal_event_observed_does_not_raise_timeout`:
  success terminal → 不変（むしろ案 B で意図がより明確になる）。

## テスト戦略

### 変更タイプ

実行時コード変更（subprocess ライフサイクル後始末ロジックの振る舞い変更）。
恒久回帰テストを追加する。

### bug 固有: 再現テスト（regression test、省略不可）

OB を assert する再現テストを実装フェーズで先行作成（Red）→ 修正後 PASS（Green）
へ遷移させる。再現テストは bc34906 直後の現行 `cli.py` に対して **FAIL** する
（現行コードでは `self_exit_failure = (143 > 0) = True` → 例外）。これにより
リグレッション検知能力を担保する（Issue #25 完了条件「上記テストが bc34906 直後の
cli.py に対しては失敗することを確認」）。

### Small テスト

不要。失敗判定ロジックは `_execute_cli_once` 内のインライン分岐であり、
`process.returncode` / `result.terminal_seen` / `result.terminal_failure` という
subprocess・stream 結合状態に依存する。純粋関数として切り出せる単位がなく、
切り出しは「最小侵襲」方針に反する。検証は Medium で行う（`docs/dev/testing-convention.md`
の判定基準: subprocess 結合あり → Medium）。

### Medium テスト

`tests/test_cli_streaming_integration.py` の `TestTerminalEventBreak`（mock CLI
shell script + 実 `subprocess.Popen`）と同方式で以下を検証する。検証観点:

1. **誤判定の解消（returncode バリアント網羅）**: terminal `result` event
   （`subtype:success`）を出力後、`process.poll()` 時点でプロセスが生存し
   `terminate()` が呼ばれるケースで、戻り値が
   - `143`（SIGTERM trap → `128+15`）
   - `137`（SIGKILL fallback → `128+9`）
   - `-15`（SIGTERM 既定、trap なし）

   のいずれでも `CLIExecutionError` を投げず `CLIResult` を返し
   `terminal_seen is True` であること。mock script は SIGTERM の trap 有無で
   終了コードを作り分ける（`trap 'exit 143' TERM` + `sleep & wait` で `143` /
   `trap 'exit 137' TERM` で `137` / trap なし `exec sleep` で `-15`）。

2. **本来の terminal 失敗が引き続き例外化されること**: terminal `result` event が
   `subtype:error` / `is_error:true` の場合、terminate 後 returncode に関わらず
   `CLIExecutionError`（`terminal_failure` 経路の保持確認）。

3. **error_messages 経路の保持**: terminal `result` が `success` でも stream 中に
   `error` イベントがあれば `CLIExecutionError`（`error_messages` 経路の保持確認）。

4. **非 terminal 経路の不変**: terminal event を出さず `exit 1` する CLI は従来
   どおり `CLIExecutionError`。

5. **既存テストの期待値反転**: `test_claude_success_terminal_with_self_exit_nonzero_raises`
   を「success terminal + 自発 `exit 1` → `CLIResult` を返す」に改修
   （「方針」§ 既存テストへの影響）。

### Large テスト

不要。本 bug は subprocess の終了シグナル処理に閉じており、mock CLI shell script
（terminal event 出力 + SIGTERM trap）で根本原因の経路を決定論的に再現できる。
既存 `TestTerminalEventBreak` も同方式で terminal-event 後始末を検証済み。実
`claude` CLI 疎通は本質的にレース条件のため決定論的な回帰シグナルを増やさず、
`docs/dev/testing-convention.md` の「物理的に再現可能な手段が Medium で足りる」
ケースに該当する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規アーキテクチャ決定なし。bc34906 の terminate-after-terminal 設計を変えず、後始末ブロックのバグを修正するのみ |
| docs/ARCHITECTURE.md | なし | アーキテクチャ構造の変更なし |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし |
| docs/reference/ | なし | 公開 API・規約の変更なし。`_execute_cli_once` は内部関数でシグネチャ不変 |
| docs/cli-guides/ | なし | CLI 仕様（`kaji run` 等のユーザ向け挙動）の変更なし。誤判定の解消は内部修正 |
| CLAUDE.md | なし | 規約変更なし |

> terminal-event 後始末の挙動は `draft/design/issue-local-p1-22-stream-terminal-event-break.md`
> （bc34906 の設計書、worktree-local）に記述があるが、これは過去 Issue のドラフト
> 設計書であり恒久 docs ではない。本 Issue の設計書（本ファイル）が #25 の
> `i-dev-final-check` 時に Issue 本文へアーカイブされることで履歴は保たれる。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 修正対象コード | `kaji_harness/cli.py:152-167`（worktree 内） | `self_exit_failure = process.returncode is not None and process.returncode > 0` と OR 条件 `if result.terminal_failure or self_exit_failure or result.error_messages:` が誤判定の発生箇所 |
| 起因コミット | `git show bc34906`（in-repo） | `fix(cli): break stream loop on terminal event to avoid stdout EOF wait` — `terminate-after-terminal` 経路と `self_exit_failure` 判定、`is_terminal_event` を同時導入。コミットメッセージ「Success/failure after terminal event is decided by error_messages (not signal-induced returncode)」が宣言した意図に実装が一致していなかった |
| Python subprocess 仕様 | https://docs.python.org/3/library/subprocess.html#subprocess.Popen.returncode | 「A negative value -N indicates that the child was terminated by signal N」— SIGTERM kill 時の returncode は `-15`。bc34906 のコメントが前提にした慣例 |
| Bash 終了ステータス慣例 | https://www.gnu.org/software/bash/manual/bash.html#Exit-Status | 「a command which terminates due to receipt of a signal … the exit status is greater than 128」— シグナル `N` で終了したプロセスを shell 慣例で reap すると `128+N`。SIGTERM trap → `143`、SIGKILL → `137` の根拠 |
| 観測ログ（Issue 本文に引用済） | Issue #25 本文「OB（観測された挙動）」 | `run.log` の `workflow_end status:ERROR error:"CLIExecutionError: Step 'implement' CLI exited with code 143: terminal failure"` と `stdout.log` 末尾の `result subtype:success is_error:false`。`.kaji-artifacts/` は worktree 外のため Issue 本文の引用を一次情報とする |
| 既存テスト | `tests/test_cli_streaming_integration.py:407-679`（`TestTerminalEventBreak`） | mock CLI shell script + 実 `subprocess.Popen` による terminal-event 後始末の検証パターン。本 Issue のテストもこの方式を踏襲。`test_claude_success_terminal_with_self_exit_nonzero_raises`（:634）は期待値反転の対象 |
