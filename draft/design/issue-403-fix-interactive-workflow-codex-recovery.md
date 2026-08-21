# [設計] interactive workflow の異常終了記録（timeout session ID 欠落 / 割込みの COMPLETE 誤記録）

Issue: #403

## 概要

`interactive_terminal` runner の timeout / pane-dead 経路で、当該 attempt に一意対応する
agent session ID を `result.json.session_id` に保存し、`WorkflowRunner.run()` の
`KeyboardInterrupt` を `workflow_end status=ERROR` + `failure_event kind=interrupted` として
記録する。両者は「verdict を得ないまま run が終わる」同一境界の記録漏れであり、recovery /
incident triage が再開候補を判断する証跡を失わせている。

## 背景・目的

### Observed Behavior（OB）

実障害 3 run（Issue #393 の[原因調査コメント](https://github.com/apokamo/kaji/issues/393#issuecomment-5372780233)）:

| run / attempt | 観測 |
|---|---|
| `260730222002` / `implement/attempt-002` | runner が `6002075ms` で timeout。pane は `pane_dead=0` / `node`。Codex session `019fb36d-1346-7773-b7c9-b18a9da494d2` は timeout 約 32 秒前まで進行。`result.json.session_id=null` |
| `260731015910` / `implement/attempt-001` | runner が `6002101ms` で timeout。pane は `pane_dead=0` / `node`。Codex session `019fb3f7-3aa7-7cc2-9e4b-88d7c104620c` は timeout 約 21 秒前まで進行。`result.json.session_id=null` |
| `260731015744` | Codex session は `turn_aborted(reason=interrupted)`。`result.json` / `verdict.yaml` 不在。`run.log` 末尾は `workflow_end status=COMPLETE` |

```text
run 260730222002: StepTimeoutError: Step 'implement' timed out after 6000s; result.json.session_id=null
run 260731015910: StepTimeoutError: Step 'implement' timed out after 6000s; result.json.session_id=null
run 260731015744: turn_aborted(reason=interrupted); workflow_end status=COMPLETE
```

帰結: 中断 run が `COMPLETE` と記録されるため `select_target_run_dir`（`ERROR` / `ABORT` のみ受理）
が triage 対象として選べず、timeout run は `session_id=null` のため再開候補の診断情報を持たない。
運用者は Codex session store と run artifact を手で突合するしかない。

### Expected Behavior（EB）

- timeout / pane-dead で終わった attempt でも、当該 attempt と**検証可能に**対応付く session ID が
  存在すれば `result.json.session_id` に残る。検証できない ID は推測して保存せず `null` にする。
  - 根拠: [`docs/adr/006-attempt-result-json.md`](../../docs/adr/006-attempt-result-json.md) —
    「143 / SIGTERM / timeout / interruption のような**異常終了の exit_code / signal が成果物に残らない**」
    ことを解決課題とし、`result.json` は異常終了でも best-effort で終了情報を残す artifact と定義する。
    `session_id` は同 ADR が列挙する保存対象フィールドの 1 つ。
- workflow 開始後の `KeyboardInterrupt` は `workflow_end status=ERROR` + 中断 evidence を記録し、
  例外自体は握り潰さず再送出する。
  - 根拠: [`docs/cli-guides/failure-recovery.ja.md`](../../docs/cli-guides/failure-recovery.ja.md)
    § いつ走るか / § `kaji recover` — triage の入力は `ERROR` / 対象 `ABORT` の終端 artifact であり、
    `workflow_end` を欠く run・`COMPLETE` の run は拒否される。
- 通常完了（`COMPLETE`）と agent の正規 `ABORT` の既存挙動、公開 CLI の exit code / signal 伝播は不変。

### 目的（この修正で得られるもの）

長時間 workflow が異常終了したとき、`result.json` と `run.log` だけで「どの session を人手で
resume すればよいか」「その run は正常完了ではなく中断だったか」を判別できる。自動 resume は
追加しない（診断と人手判断の材料までが scope）。

## 再現手順（steps-to-reproduce）

### A. timeout で session ID が失われる

1. 前提: `agent_runner = "interactive_terminal"`、`step.agent = "codex"`、`CODEX_HOME/sessions/`
   配下に当該 attempt の `prompt.txt` / `verdict.yaml` path を本文に含む rollout `*.jsonl` が
   1 件だけ存在し、pane は生存（`#{pane_dead}=0`）したまま `verdict.yaml` を書かない。
2. 操作: monotonic deadline を経過させ、`execute_interactive_terminal` の timeout 分岐
   （`kaji_harness/interactive_terminal.py:477-487`）に到達させる。
3. 観測（OB）: `StepTimeoutError` が送出され、`steps/<step>/attempt-NNN/result.json` の
   `session_id` が `null`。実障害は上記 2 run で確認済み。

### B. 割込みが COMPLETE と記録される

1. 前提: `WorkflowRunner.run()` が run directory を採番し `workflow_start` を書き終えている。
2. 操作: メインループの step dispatch から `KeyboardInterrupt` を送出する。
3. 観測（OB）: `KeyboardInterrupt` は呼出元へ伝播するが、`run.log` の末尾は
   `workflow_end status=COMPLETE`。実障害は run `260731015744` で確認済み。

## 根本原因（Root Cause）

### 原因 A: 異常終了経路に session 解決が存在しない

- **何が間違っているか**: `execute_interactive_terminal` は session 解決を
  「verdict 検出分岐の中」にしか置いていない（`interactive_terminal.py:439-458`）。timeout 分岐
  （`:477-487`）は `_write_pane_metadata` → `_kill_pane` → `raise StepTimeoutError` のみ、
  pane-dead 分岐（`:461-474`）は `_write_pane_metadata` → `raise CLIExecutionError` のみで、
  `_extract_codex_session_id` を呼ばない。
- **なぜそうなっているか**: session ID は当初「成功した attempt を後続 `resume:` step へ引き継ぐ
  入力」としてのみ設計された（`state.save_session_id` は成功経路のみ）。異常終了は fail-loud に
  倒す方針で、診断情報としての session 保全は要件になっていなかった。runner 側も
  `_record_dispatch_failure` に渡すのは「dispatch 前に判明していた resume session ID」だけで
  （`runner.py:330` の `result_session_id or session_id`）、例外経路で新たに判明する情報を
  受け取る口が無い。
- **いつから**: `9c31e6a`（2026-06-05, Issue #224「add interactive_terminal runner」）の初版から。
  `git blame -L 439,458 kaji_harness/interactive_terminal.py` の session 解決行と
  `-L 477,487` の timeout 行が同一導入 commit で、最初から verdict 経路のみに配置されている。
- **同じ原因で他に壊れている箇所**（完了条件「同根経路の調査」）:

| 経路 | 調査結果 | 本 Issue での扱い |
|---|---|---|
| timeout × codex（`:487`） | session store に rollout が存在しても未照合 | **修正する** |
| timeout × claude（`:487`） | kaji が採番済みの `launch_session_id`（`:380`）すら捨てている | **修正する** |
| pane-dead 早期終了（`:474`） | 同上。`CLIExecutionError` は session を運ばない | **修正する** |
| pipe-pane 設定失敗（`:416`） | pane 起動直後の setup 失敗。agent がまだ session を作っていない | 対象外（下記「制約」） |
| tmux 検証失敗（`:519` / `:522` / `:585` / `:588` / `:650` / `:713`） | pane 生成前 / 制御コマンド失敗。attempt に紐づく session が存在しない | 対象外 |
| headless runner の timeout（`kaji_harness/cli.py:273`） | `execute_cli` は session ID を CLI の stdout から解決するため、timeout では取得元自体が存在しない。kaji が採番する ID も無い | 変更しない（検証可能な ID が原理的に無い。設計上の記録のみ） |
| `script_exec.py:182` の timeout | exec / exec_script step。agent session の概念が無い | 対象外 |
| `kaji_harness/recovery/` | `session_id` の参照は 0 件（`grep -rn "session_id" kaji_harness/recovery/`）。report / snapshot での提示は新規作業 | **追加する** |

### 原因 B: `KeyboardInterrupt` が `BaseException` であることを終端処理が考慮していない

- **何が間違っているか**: `runner.py:956` が `end_status = "COMPLETE"` で初期化し、`:1133` の
  `except Exception` だけが `ERROR` へ書き換える。`KeyboardInterrupt` は `BaseException` の
  直接の子で `Exception` ではないため except を素通りし、`:1138` の `finally` が初期値
  `COMPLETE` のまま `log_workflow_end` を実行する。
- **なぜそうなっているか**: 「例外 = `Exception`」という前提のもとで `finally` に終端記録を置き、
  「成功が既定・例外が上書き」という初期値設計を採ったため、`BaseException` 経路で初期値が
  そのまま真実として記録される。fail-safe の向きが逆（未知の終了を成功に倒している）。
- **いつから**: `94df778`（2026-03-10, Issue #57）。`end_status = "COMPLETE"` 初期化と
  `except Exception` / `finally` の三点セットが同一 commit で導入されている。
- **同じ原因で他に壊れている箇所**:

| 箇所 | 調査結果 | 本 Issue での扱い |
|---|---|---|
| `commands/run.py:207` の `except Exception` | `KeyboardInterrupt` はプロセス外へ抜け、`_run_failure_triage` は起動しない | **変更しない**（既存の signal 伝播契約。Issue 本文「スコープ外（維持）」で人間決定済み） |
| `recovery/handler.py:633` | recovery wait 中の `KeyboardInterrupt` は既に捕捉し `cancelled_interrupted` を記録済み | 変更しない（既に正しい） |
| `interactive_terminal` の polling ループ | `KeyboardInterrupt` 時に pane snapshot を残さないため、孤児 pane の pane_id が artifact に残らない | **pane metadata の snapshot のみ追加**（kill はしない） |

## インターフェース

bug 修正のため公開 IF は原則維持する。変更するのは以下だけで、いずれも既存呼び出し側に対して
後方互換（新規パラメータはすべて keyword-only + default `None`）。

### 入力（変更点）

| 対象 | 変更前 | 変更後 | 互換性 |
|---|---|---|---|
| `errors.SessionResolution` | （存在しない） | 新規 frozen dataclass。`session_id: str \| None` の 1 フィールドのみ | 追加のみ |
| `errors.StepTimeoutError.__init__` | `(step_id, timeout, returncode=None)` | `(step_id, timeout, returncode=None, *, session_resolution=None)` | 既存 3 呼び出し（`cli.py:273` / `interactive_terminal.py:487` / `script_exec.py:182`）は無変更で動作 |
| `errors.CLIExecutionError.__init__` | `(step_id, returncode, stderr)` | `(step_id, returncode, stderr, *, session_resolution=None)` | 既存 10 呼び出しは無変更で動作。`TmuxSessionRequiredError` は `CLINotFoundError` 系で無関係 |
| `logger.RunLogger.log_failure_event` | signature 変更なし | `kind` の値集合に `"interrupted"` を追加（docstring の列挙も更新） | JSONL の field 集合は不変 |

例外に情報を載せる方式自体は、Issue #222 が `StepTimeoutError.returncode` で確立した
「異常終了時に runner が `result.json` へ写すための情報を例外に運ばせる」既存パターンの踏襲であり、
`_record_dispatch_failure` の `getattr(exc, "returncode", None)` と同じ読み取り方で扱う。

**`str | None` ではなく `SessionResolution | None` を運ぶ理由**（`session_id` を直接載せると
成立しない要件）: 異常終了経路では 2 つの `None` を区別しなければならない。

| 状態 | 意味 | runner の扱い |
|---|---|---|
| `session_resolution is None` | この経路は session 解決を**試みていない**（headless `cli.py` / `script_exec.py` / pane 生成前の tmux 制御失敗） | 既存 fallback `result_session_id or session_id` を維持（挙動不変） |
| `SessionResolution(session_id="…")` | 当該 attempt の session として**検証済み** | その値を `result.json.session_id` に書く |
| `SessionResolution(session_id=None)` | 解決を試みた結果、一意に対応付く session が**無かった** | `result.json.session_id` を `null` にする。**resume 入力への fallback を明示的に抑止する** |

3 番目の状態が本設計の要点である。Codex の `resume:` step が異常終了したとき、resume 入力
（親 session ID）は Codex が新規 rollout を作る性質上「当該 attempt で進行していた session」では
ないため、これを保存すると Issue #403 完了条件「session ID が存在しない、複数候補、marker 不一致、
読取失敗の場合は ID を推測せず fail-safe に `null` とする」に反し、運用者を誤った再開先へ誘導する。
`str | None` 1 本では「未試行」と「試行して null」が同じ値になり、この抑止を表現できない。

```python
@dataclass(frozen=True)
class SessionResolution:
    """異常終了経路で確定した session ID の解決結果。

    ``session_id is None`` は「解決を試みたが、当該 attempt に一意対応する session が
    無かった」ことを表す確定値であり、呼び出し側の推測（resume 入力等）で埋めてはならない。
    解決自体を試みていない経路は、例外にこのオブジェクトを載せない。
    """

    session_id: str | None
```

### 出力（変更点）

| artifact | 変更 | schema |
|---|---|---|
| `steps/<step>/attempt-NNN/result.json` | 異常終了時に `session_id` が `null` 以外になり得る。逆に Codex の `resume:` step が異常終了し新 rollout を一意特定できない場合は、従来 resume 入力の親 ID が書かれていた位置が `null` になる（fail-safe 側への意図した変更） | **キー集合は不変**（`AttemptResult.session_id` は既存フィールド） |
| `run.log` `failure_event` | `kind="interrupted"`（`step_id` / `exception_type="KeyboardInterrupt"` / `synthetic=true` 付き）を新規に emit | **field 集合は不変**。既存 field の新しい値のみ。`workflow_start.schema_version` は 1 のまま |
| `run.log` `workflow_end` | 割込み時 `status="ERROR"` / `error="KeyboardInterrupt: <説明>"` | 不変（既存 field の値） |
| `steps/<step>/attempt-NNN/pane-metadata.json` | 割込み時にも書き出す（pane は kill しない） | 不変（既存キーのみ） |
| `recovery.json` | **変更なし**（`RecoveryDecision` へのフィールド追加をしない）。session ID / 孤児 pane は既存 `evidence: list[str]` に行として載る | 不変 |

### 使用例

```python
# 1) timeout 経路: 解決結果を確定値として例外に載せる（interactive_terminal 内部）
#    resolved.session_id が None でも「解決して null」という確定値として運ばれる
resolved = _resolve_abnormal_exit_session(step.agent, ..., pane_alive=True)
raise StepTimeoutError(step.id, timeout, session_resolution=resolved)

# 2) runner: 確定値があればそれで上書きし、無い経路だけ既存 fallback を残す
#    （_record_dispatch_failure 内部。resolution.session_id is None なら null を書く）
resolution = getattr(exc, "session_resolution", None)
if resolution is not None:
    session_id = resolution.session_id

# 3) 割込み: run レベルのみ記録し、例外はそのまま再送出する（WorkflowRunner.run 内部）
except KeyboardInterrupt as exc:
    end_status = "ERROR"
    end_error = f"{type(exc).__name__}: workflow interrupted by user"
    logger.log_failure_event(
        kind="interrupted",
        step_id=in_flight_step_id,      # dispatch 中の step。未 dispatch なら None
        exception_type="KeyboardInterrupt",
        synthetic=True,
    )
    raise
```

運用者から見える差分（人間向けの使用例）:

```console
$ kaji run .kaji/wf/official/dev.yaml 403     # ← 実行中に Ctrl-C
^C
$ tail -1 .kaji-artifacts/403/runs/<run_id>/run.log
{"event":"workflow_end","status":"ERROR",...,"error":"KeyboardInterrupt: workflow interrupted by user"}
$ kaji recover .kaji/wf/official/dev.yaml 403  # ← 中断 run が triage 対象として選べる
```

## 制約・前提条件

1. **公開契約の不変**: `kaji run` の exit code map（`0/1/2/3`）と signal 伝播は変更しない。
   `KeyboardInterrupt` は `cmd_run` を素通りして exit 130 のまま（Issue 本文「スコープ外（維持）」）。
2. **artifact schema の不変**: `result.json` / `recovery.json` / `run.log` の field 集合を増やさない。
   追加するのは既存 field の値（`failure_event.kind`、`FailureCause`）と、既存 `evidence` 配列の行のみ。
3. **auto-recovery の不変**: safety gate、`RECOVERY_BUDGET`、`RECOVERY_WAIT_SECONDS`、
   `_safety_gates` の判定は変更しない。自動 resume 経路は追加しない。
4. **session state を汚さない**: 異常終了で解決した session ID を `SessionState.save_session_id`
   へ保存しない。保存すると後続 `resume:` step の入力が変わり「診断のみ」という scope を超える。
   保存先は `result.json`（診断 artifact）に限定する。
5. **verdict 検出経路の不変**: `_extract_codex_session_id` / `_wait_for_pane_exit_or_session_id` /
   `_SESSION_ID_GRACE_SECONDS` を用いる既存の verdict 経路（`:439-458`）は一切変更しない。
   newest-match-wins を維持する（変更すると `resume:` step が `MissingResumeSessionError` で退行する）。
6. **推測しない**: 一意に検証できない候補は `null` にする。「複数候補のうち最新」「pane が死んだ
   直後だからおそらくこれ」といった推定は行わない。**resume 入力（親 session ID）による代替も
   推測に含める**: Codex の異常終了で新 rollout を一意特定できない場合、runner の既存 fallback
   `result_session_id or session_id` が親 ID を書き込むことを `SessionResolution(None)` で抑止する。
7. **終端記録の保護範囲**: `log_workflow_start()` の emit 完了直後から run 終了までのすべての処理を
   `KeyboardInterrupt` 終端記録の保護範囲に入れる。「窓が短い」「現状より悪化しない」を理由に
   未保護区間を残さない。
8. **中断時に pane を kill しない**: 中断直前の agent 状態を人間が目視・回収できる状態を保つ。
9. **対象外の raise site**: pane 生成前 / pipe-pane setup 失敗の `CLIExecutionError` は
   session 解決の対象にしない（agent プロセスがまだ session を作っていないため、
   照合しても常に `null` になるか、無関係な session に一致するリスクだけが残る）。これらは
   `session_resolution` を載せない = 「未試行」として既存 fallback を維持する。
10. **Antigravity**: 公開 session ID を持たない（`docs/cli-guides/interactive-terminal-runner.ja.md`
    § session 継続）。異常終了でも常に `SessionResolution(None)`。

## 方針

### 1. 異常終了経路の session 解決（`interactive_terminal.py`）

異常終了専用の private helper を 1 本追加し、timeout / pane-dead の 2 箇所から呼ぶ。

```python
def _resolve_abnormal_exit_session(
    agent: str, *, prompt_path: Path, verdict_path: Path,
    resume_session_id: str | None, launch_session_id: str, pane_alive: bool,
) -> SessionResolution:
    """verdict を得ずに終わった attempt の session を解決する。

    戻り値は常に確定値。``SessionResolution(None)`` は「一意対応する session は無い」
    という結論であり、呼び出し側が resume 入力で埋め直してはならない。
    """
    if agent == "claude":
        # `--resume <id>` は同一 session を継続するため、resume 入力そのものが
        # 当該 attempt の session ID。pane の生死に依らず検証済み。
        if resume_session_id:
            return SessionResolution(resume_session_id)
        # fresh は `--session-id <uuid>` で session を新規作成する。pane が全 timeout 区間を
        # 生存した = wrapper の起動が受理された証拠。pane-dead は起動失敗を含むため採用しない。
        return SessionResolution(launch_session_id or None if pane_alive else None)
    if agent != "codex":
        return SessionResolution(None)
    # codex は resume でも新規 rollout を作るため、resume 入力は当該 attempt の session では
    # ない。store 照合で一意特定できなければ None（親 ID への fallback は行わない）。
    return SessionResolution(
        _unique_codex_session_id_from_store(
            prompt_path=prompt_path, verdict_path=verdict_path
        )
    )
```

agent × resume 状態 × 経路ごとの結論:

| agent | resume 入力 | 経路 | 解決結果 | 根拠 |
|---|---|---|---|---|
| codex | なし | timeout / pane-dead | store 一意一致 1 件 → その UUID / それ以外 → `None` | marker + mtime で当該 attempt の rollout を検証 |
| codex | あり | timeout / pane-dead | 同上（**親 ID へ fallback しない**） | `codex resume` は履歴を引き継ぐ**新規** rollout を生成するため、親 ID は当該 attempt で進行していた session ではない（Issue #403 `## 決定事項` と grill-me provenance の確認事項） |
| claude | なし | timeout | `launch_session_id` | pane が全区間生存 = `claude --session-id <uuid>`（`wrapper.sh:72`）が受理された |
| claude | なし | pane-dead | `None` | 起動失敗を含み、UUID に対応する session が実在しない可能性がある |
| claude | あり | timeout / pane-dead | resume 入力の ID | `claude --resume <id>`（`wrapper.sh:69`）は**同一** session を継続する。その session は先行 attempt が作成し `SessionState` に記録済みで実在が保証される |
| antigravity | — | timeout / pane-dead | `None` | 公開 session ID を持たない |

`_unique_codex_session_id_from_store` の規則（既存 `_extract_codex_session_id_from_store` は変更せず、
別関数として追加する）:

1. marker = 当該 attempt の `prompt_path` / `verdict_path` の絶対 path 文字列。
2. 走査対象 = `CODEX_HOME/sessions/**/*.jsonl`（既存 `_codex_home()` を再利用）のうち、
   **mtime が `prompt_path` の mtime 以上**のもの。上限は既存 `_CODEX_SESSION_SCAN_LIMIT`（100）。
3. ファイル名が `_CODEX_SESSION_FILE_RE` に一致し、本文に marker を含むものを**全件数える**。
4. 一致が**ちょうど 1 件**ならその UUID を返す。0 件 / 2 件以上 / 読取失敗 / `prompt_path` の
   stat 失敗はすべて `None`。

mtime 下限フィルタを置く理由は 2 つある。(a) `resume:` step では Codex が履歴を引き継ぐ**新規**
rollout を作るため、親 rollout も marker を含み一致が 2 件以上になるが、親は当該 attempt 開始前に
最終更新が止まっているので除外され、一意性判定が実務で機能する。(b) 100 件全走査の読み取りコストを
「attempt 開始後に更新されたファイル」に限定できる（実運用では 1〜3 件）。`prompt.txt` は
runner が dispatch 直前に書き出す（`runner.py` の `(attempt_dir / "prompt.txt").write_text(...)`）
ため attempt 開始時刻の代理として正確であり、marker を含む rollout が prompt より古いことは
論理的に起こり得ない。

`terminal.log` の `codex resume <uuid>` 正規表現は**異常終了経路では使わない**。理由:
(a) timeout 時は pane が生存しており（実障害 2 run とも `pane_dead=0`）、Codex が終了時に出す
resume 行はまだ存在しない、(b) 実障害の `terminal.log` は 51MB / 52MB で、全文 read + 正規表現は
新たな数百 MB のメモリ確保を招く、(c) store 照合（marker + mtime）の方が「この attempt の session か」
を直接検証できる。結果として異常終了経路は `terminal.log` を読まない（pane-dead 経路の
`_terminal_exit_detail` による全文走査は Issue #296 の契約どおり不変）。

grace wait は**置かない**。verdict 経路の `_SESSION_ID_GRACE_SECONDS` は「Codex の終了間際の
resume 行を待つ」ためのものだが、timeout 時点で既に time budget を超過しており、rollout file は
セッション進行中に逐次追記される（実障害 run で timeout の 21〜32 秒前まで追記が確認できている）
ため待つ意味がない。解決は `_kill_pane` の**前**に 1 回だけ行い、kill 後の再走査もしない。

呼び出し位置:

```python
# timeout 分岐（現 :477-487）
_write_pane_metadata(...)
resolved = _resolve_abnormal_exit_session(step.agent, ..., pane_alive=True)
_kill_pane(tmux, pane_id)
raise StepTimeoutError(step.id, timeout, session_resolution=resolved)

# pane-dead 分岐（現 :461-474）
_write_pane_metadata(..., terminal_log=terminal_log)
resolved = _resolve_abnormal_exit_session(step.agent, ..., pane_alive=False)
raise CLIExecutionError(
    step.id, 1, _terminal_exit_detail(terminal_log), session_resolution=resolved
)
```

この 2 経路は resume 入力の有無に関わらず必ず `SessionResolution` を載せる。したがって
runner 側の既存 fallback（`result_session_id or session_id`）はこの 2 経路では働かず、
Codex resume の異常終了で親 session ID が `result.json` に残ることは構造的に起きない。

**失われる情報とその回収手段**: Codex resume の異常終了で `session_id` が `null` になると、
親 session ID は当該 attempt の `result.json` からは辿れなくなる。ただし親 ID は
(a) 同一 run の session 生成元 step の `result.json.session_id`、(b) `session-state.json` の
`session_ids`、から従来どおり参照できる。誤った再開先を提示するリスクの方が、同じ run 内で
別 artifact から辿れる情報の重複を失うコストより大きいため、fail-safe `null` を採る
（Issue #403 完了条件「ID を推測せず fail-safe に `null` とする」）。

### 2. runner 側の写し取り（`runner.py`）

`_record_dispatch_failure` に `getattr(exc, "session_resolution", None)` の読み取りを 1 箇所
追加する（`returncode` と同じ形）。

```python
resolution = getattr(exc, "session_resolution", None)
if resolution is not None:
    session_id = resolution.session_id     # None なら null をそのまま書く
```

`resolution is None`（= 解決を試みていない経路。headless / exec / pane 生成前の tmux 制御失敗）の
ときだけ、`execute()` が渡した既存値 `result_session_id or session_id`（`:330`）が使われる。
`execute()` 側の呼び出しは変更しない。`state.save_session_id` は呼ばない（制約 4）。

### 3. 割込みの終端整合（`runner.py` / `interactive_terminal.py`）

#### 3-1. 終端記録の保護範囲を `workflow_start` emit 直後まで広げる

現行の `try` はメインループだけを覆っており（`runner.py:987`）、`log_workflow_start`
（`:951`）から `try` 突入までの区間 — ローカル変数初期化 / `ambiguous_abort` の早期 return /
`_apply_cycle_reset` / `_StepExecutor` 構築 — が保護されていない。Issue #403 の EB は
「workflow 開始後の `KeyboardInterrupt`」を `ERROR` として記録することを求めているため、
この区間も保護範囲に入れる。

構造は次のとおり（`workflow_start` の採時と全ローカル初期化を `log_workflow_start()` の**前**へ
移し、`try:` を `log_workflow_start()` の**直後**に置く）:

```python
total_cost = 0.0
end_status = "COMPLETE"
end_error: str | None = None
last_verdict: Verdict | None = None
barrier_hit = False
step_dispatched = False
in_flight_step_id: str | None = None      # 追加: dispatch 中の step
workflow_end_logged = False               # 追加: 二重 workflow_end の抑止
workflow_start = time.monotonic()

logger = RunLogger(log_path=run_dir / "run.log")
logger.log_workflow_start(run_ctx.canonical_id, self.workflow.name)
try:
    _console.info("workflow start: ...")
    if ambiguous_abort is not None:
        self._emit_ambiguous_worktree_abort(ambiguous_abort, state, logger, workflow_start)
        workflow_end_logged = True        # 当該 helper が自前で workflow_end を書く
        return state
    self._apply_cycle_reset(cycle_reset_target, state, logger)
    executor = _StepExecutor(...)
    while current_step and current_step.id != "end":
        ...
        in_flight_step_id = current_step.id
        outcome = executor.execute(...)
        in_flight_step_id = None
        ...
    if last_verdict and last_verdict.status == "ABORT":
        end_status = "ABORT"
except KeyboardInterrupt as exc:
    ...
except Exception as exc:
    ...
finally:
    if not workflow_end_logged:
        logger.log_workflow_end(end_status, ...)
```

`workflow_end_logged` が必要な理由: `_emit_ambiguous_worktree_abort`（`:877-906`）は自前で
`log_workflow_end("ABORT", ...)` を書く。これを `try` の中へ移すと `return` 時にも `finally` が
走り、`ABORT` の直後に初期値 `COMPLETE` の `workflow_end` が二重記録される。フラグで
`finally` を no-op にすることで、現行の「ABORT が 1 件だけ」という挙動を保存する。

#### 3-2. `except KeyboardInterrupt` の処理

`except Exception` の**前**に置く（型階層上は排他だが、`BaseException` を明示的に扱っていることを
読み手に示す配置）。処理は 3 つだけ:

1. `end_status = "ERROR"` / `end_error = "KeyboardInterrupt: workflow interrupted by user"`
   （既存 `except Exception` と同じ `"<Type>: <message>"` 形。`str(KeyboardInterrupt())` は
   空文字なので固定文を使う。`workflow_end_exception_type` は `"KeyboardInterrupt"` と解決され、
   `_DEFINITION_EXCEPTIONS` には含まれないため `config_or_definition_error` にはならない）
2. `logger.log_failure_event(kind="interrupted", step_id=in_flight_step_id,
   exception_type="KeyboardInterrupt", synthetic=True)`
3. `_console.warning(...)` の後に `raise`（再送出）

`step_id` は `current_step.id` ではなく `in_flight_step_id` を使う。dispatch 中に割り込まれた
場合のみ step 名が入り、main-loop 突入前 / step 間の遷移解決中に割り込まれた場合は `None` に
なる。これは (a) 実行されていない step を「失敗した step」と誤読させない、(b) 孤児 pane は
dispatch 中の step にしか存在しないため § 方針 5 の pane 対応付けと一致する、という 2 点による。
`failure_event.step_id` は既存の nullable field（`cycle_name` と同様）であり、`None` でも
`_detect_contradiction` は `interrupted` を `_ATTEMPT_BACKED_KINDS` に含めないため矛盾検出に
落ちない。

進行中 attempt の `result.json` は**作らない**。Ctrl-C は runner プロセスにしか届かず tmux pane 内の
agent は生存しうるため、exit_code / session を best-effort で埋めると誤情報になる。
`_record_dispatch_failure` は呼ばない。

#### 3-3. 保護範囲拡大に伴う既存挙動への影響（意図した変更）

`try` を広げた結果、`ambiguous_abort` 処理 / `_apply_cycle_reset` / `_StepExecutor` 構築で
`Exception` が出た場合も `except Exception` → `finally` を通り、`workflow_end status=ERROR` が
記録されるようになる。現行はこの区間の例外が `workflow_end` を残さないまま伝播し、
`select_target_run_dir` が「実行中（`workflow_end` event なし）」として triage を拒否していた。
本変更はこれを triage 可能にする方向の変更であり、`cmd_run` の exit code map
（`HarnessError`→3 等）には影響しない。制約 1〜3（公開 CLI / artifact schema / safety gate）を
侵さないことは変わらない。

#### 3-4. 残る窓（原理的に除去できない区間）

`log_workflow_start()` の呼び出しが戻ってから `try` に入るまでの 1 バイトコード境界だけが
未保護として残る。これは Python の signal 配送モデル上、`try` の位置をどこに置いても
消せない（消すには `signal.signal` でカスタム SIGINT ハンドラを導入する必要があり、
Issue #403 が「維持」と定めた signal 伝播契約の変更にあたるため採らない）。
`RunLogger` 構築中および `log_workflow_start` 自体の実行中に届いた割込みは
`workflow_start` すら記録されないため、Issue の「workflow 開始後」の対象外である。

#### 3-5. `interactive_terminal` 側（孤児 pane の記録）

polling ループを `try` で包み、`KeyboardInterrupt` 時に `_write_pane_metadata` を best-effort
（`OSError` / `subprocess.SubprocessError` を握る）で実行してそのまま `raise` する。
`_kill_pane` は呼ばない。これで孤児 pane の `pane_id` / `pane_pid` / `pane_dead` が
`steps/<step>/attempt-NNN/pane-metadata.json` に残り、path が step と attempt を、
`failure_event.step_id` が run レベルの step を示す。metadata write 中の例外で
`KeyboardInterrupt` が別の例外に置き換わらないよう、握る例外型を限定したうえで `raise` を必ず
実行する。

### 4. 分類と incident 抑止（`recovery/`）

| ファイル | 変更 |
|---|---|
| `recovery/models.py` | `FailureCause` Literal / `FAILURE_CAUSES` に `"user_interrupted"` を追加。`INCIDENT_EXEMPT_CAUSES` に追加。`INCIDENT_SUPPRESSION_REASONS["user_interrupted"]` に固定文を追加（handler が `[cause]` で引くため必須） |
| `recovery/classify.py` | `classify_failure` の `match event.kind` に `case "interrupted"` を追加し、`FailureClassification(cause="user_interrupted", synthetic=True, source="external", recoverability_hint="no")` を返す |
| `recovery/handler.py` | `_COMMENT_ONLY_CAUSES` に `"user_interrupted"` を追加（decision は `comment_only`。`not_resumable` は Issue #349 が「副作用 skill のため resume を封じる」意味で使う語であり、手動再開が正当な中断 run に付けると誤読される）。`recoverable=False` は変わらず、auto-resume 経路には入らない |
| `recovery/report.py` | `_CAUSE_DESCRIPTIONS["user_interrupted"]` に固定説明文を追加 |
| `recovery/snapshot.py` | 下記 2 行の evidence 追加 |

`_detect_contradiction` の `_ATTEMPT_BACKED_KINDS` に `"interrupted"` を**追加しない**。
中断では意図的に `result.json` を作らないため、追加すると `kaji_bug_suspected` として
bug issue を誤起票する。この非対称性はコメントで明示する。

`source="external"` を選ぶ理由: `FailureSource` は `runner` / `agent` / `external` / `config` の
4 値で、Ctrl-C は kaji のプロセス境界の外から届く signal であり、harness のバグでも agent の
判断でも config 不備でもない。`source` は直列化と report 表示にのみ使われ、判定ロジックの入力に
なっていない（`grep -rn "\.source\b" kaji_harness/` の結果は `models.py` の検証と `to_dict` のみ）。

### 5. recovery snapshot での提示（`recovery/snapshot.py`）

`FailureSnapshot`（非永続の in-memory dataclass）に 2 フィールドを追加し、既存 `evidence` タプルへ
行を足す。`RecoveryDecision` / `recovery.json` のフィールドは増やさない（制約 2）。

| フィールド | 取得元 | evidence 行 | 出す条件 |
|---|---|---|---|
| `attempt_session_id: str \| None` | 既存 `_latest_attempt_result()` が読む `result.json` の `session_id` | `steps/<step>/result.json session_id=<id>` | 非 `None` のときだけ |
| `orphan_pane_id: str \| None` | `steps/<step>/attempt-*/pane-metadata.json`（最新 attempt）の `pane_id` | `steps/<step>/attempt-NNN/pane-metadata.json: orphan pane pane_id=<id> (not killed)` | `failure_event.kind == "interrupted"` かつ最新 attempt が **`result.json` を持たない**（進行中）かつ読めたときだけ |

最新 attempt に `result.json` がある場合は「完了済み attempt」であり pane は cleanup 済みなので
孤児として採用しない。`in_flight_step_id` は `_StepExecutor.execute()` の *前* に設定されるため、
新 attempt directory の作成前に割り込むと最新 attempt が同 step の直前の完了済み attempt になり、
この判別がないと殺し済み pane を孤児と誤報する。割込み時は進行中 attempt の `result.json` を
作らない（§ 方針 3-2）ので、`result.json` の実在で進行中 / 完了済みを判別できる。

`pane-metadata.json` の読み取りは silent best-effort とし、失敗を `artifact_read_errors` に
**入れない**（入れると `_detect_contradiction` が `kaji_bug_suspected` に倒れ、pane metadata が
無い headless run で bug issue を誤起票する）。evidence は既存どおり `sanitize_evidence()` を通す。

これにより triage コメント / stderr サマリの根拠一覧に「どの session を resume できるか」
「どの pane が孤児として残っているか」が出る。`signature.py` の fingerprint は
`failure_error_text`（`attempt_error` + `workflow_end_error`）由来なので、evidence を増やしても
incident の署名 hash は変わらず既存 dedup は壊れない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|---|---|---|---|
| Issue の分割 | timeout の情報欠落と割込みの状態誤記録を 1 Issue で扱う | Issue #403 本文 `## 重要判断`（起票前の人間決定、2026-08-22） | 「verdict を得ずに run が終わる」共通境界として根本原因 A / B に分解し、修正箇所と再現テストを個別に定義 |
| session 情報の用途 | 診断と明示的な人手 recovery 判断まで。自動 resume は対象外 | Issue #403 本文 `## 重要判断`（人間決定、2026-08-22） | 保存先を `result.json.session_id` に限定し、`SessionState.save_session_id` を呼ばないことを制約 4 として固定 |
| 割込みの終端 | `COMPLETE` ではなく `ERROR` として artifact 化し、`KeyboardInterrupt` は再送出 | Issue #403 本文 `## 重要判断` + `docs/cli-guides/failure-recovery.ja.md` の既存契約 | `except KeyboardInterrupt` を `run()` のメインループ try に置き、`end_status` / `end_error` / `failure_event` の 3 点だけを更新して `raise`。`cmd_run` は無変更 |
| 割込み run の分類と incident | `failure_event kind=interrupted`（step_id 付き）+ cause `user_interrupted` を `INCIDENT_EXEMPT_CAUSES` に入れる。incident 起票 / `occurrences.jsonl` 追記のみ抑止 | Issue #403 `## 決定事項`（grill-me 人間決定、2026-08-22）。識別子文字列は改名禁止と明記されている | 文字列は固定のまま、`FailureCause` / `FAILURE_CAUSES` / `INCIDENT_SUPPRESSION_REASONS` / `_CAUSE_DESCRIPTIONS` / `_COMMENT_ONLY_CAUSES` の追記点を特定。`_ATTEMPT_BACKED_KINDS` には追加しないことを決定 |
| 割込み証跡の粒度 | run レベル（`failure_event` + `workflow_end status=ERROR`）のみ。進行中 attempt の `result.json` は作らない | Issue #403 `## 決定事項`（grill-me 人間決定） | `_record_dispatch_failure` を呼ばないことを明示。pane snapshot は `result.json` ではないため許容範囲と整理 |
| 割込み時の pane 後始末 | kill せず、孤児 pane の pane_id / step を中断 evidence に記録 | Issue #403 `## 決定事項`（grill-me 人間決定） | polling ループの `except KeyboardInterrupt` で `_write_pane_metadata` のみ実行（`_kill_pane` なし）。pane_id は既存 `pane-metadata.json`、step は path と `failure_event.step_id` から辿れる形にし、snapshot evidence へ 1 行出す |
| session ID 保存の対象経路 | timeout（codex / claude）と pane-dead の 3 経路を同一境界として扱う | Issue #403 `## 決定事項`（grill-me 人間決定） | 3 経路に加え、対象外とする raise site（pipe-pane setup / tmux 制御失敗 / headless / exec）を根拠付きで列挙（制約 8・根本原因 A の表） |
| 複数候補時の fail-safe の適用範囲 | 一意でなければ `null`。適用は異常終了経路のみ。verdict 経路は newest-match-wins を維持 | Issue #403 `## 決定事項`（grill-me 人間決定）。理由も Issue に記載済み | 既存 `_extract_codex_session_id_from_store` を変更せず別関数を追加する構成に分解し、「経路ごとに規則が異なる理由」を § 方針 1 と制約 5 に明記。**fail-safe の適用対象に resume 入力への fallback を含める**ため、例外の運搬型を `str \| None` から `SessionResolution \| None` に変え、「未試行」と「試行して null」を区別できるようにした（review 指摘 1 への対応） |
| recovery での提示方法 | `FailureSnapshot` に保持し既存 `evidence` へ行追加。`RecoveryDecision` / `recovery.json` にフィールドを足さない | Issue #403 `## 決定事項`（grill-me 人間決定） | 追加フィールド 2 つと evidence 行の書式・出力条件を確定。`pane-metadata.json` 読取失敗を `artifact_read_errors` に入れない理由を明記 |
| Codex resume の異常終了で親 session ID を保存しない | 一意な新 rollout を特定できなければ `result.json.session_id` は `null`。resume 入力へ fallback しない | Issue #403 完了条件「session ID が存在しない、複数候補、marker 不一致、読取失敗の場合は ID を推測せず fail-safe に `null` とする」+ `issue-review-design` 指摘 1（https://github.com/apokamo/kaji/issues/403#issuecomment-5373730963 ） | `SessionResolution(None)` を確定値として運ぶ契約に分解し、runner の既存 fallback が働く条件を「例外が resolution を載せていない経路のみ」に限定。失われる親 ID の回収手段（session 生成元 step の `result.json` / `session-state.json`）を § 方針 1 に明記 |
| `workflow_start` emit 後の全区間を割込み保護範囲にする | `try` を `log_workflow_start()` 直後へ移し、`ambiguous_abort` / `_apply_cycle_reset` / executor 構築も覆う | Issue #403 EB「workflow 開始後の `KeyboardInterrupt` は `workflow_end status=ERROR` と error evidence を記録した上で再送出する」+ `issue-review-design` 指摘 2（https://github.com/apokamo/kaji/issues/403#issuecomment-5373730963 ） | `workflow_end_logged` フラグで `_emit_ambiguous_worktree_abort` の自前 `workflow_end` と二重記録しない構造に分解。原理的に除去できない 1 バイトコード境界のみを残余として明示（§ 方針 3-4） |
| 割込み時の `failure_event.step_id` | dispatch 中のみ step 名、未 dispatch なら `None` | AI の仮定。根拠: 実行されていない step を「失敗した step」と誤読させないため。孤児 pane は dispatch 中の step にしか存在せず § 方針 5 の対応付けと一致する。`step_id` は既存の nullable field で、`interrupted` は `_ATTEMPT_BACKED_KINDS` 外のため `None` でも矛盾検出に落ちない。検査先: `issue-review-design` | `in_flight_step_id` ローカル変数を dispatch 直前に設定し完了時に解除する形へ具体化 |
| claude の resume 入力は異常終了でも保存する | timeout / pane-dead いずれでも resume 入力の ID を `SessionResolution` に載せる | AI の仮定。根拠: `claude --resume <id>`（`wrapper.sh:69`）は**同一** session を継続するため、resume 入力そのものが当該 attempt の session ID。その session は先行 attempt が作成し `SessionState` に記録済みで実在が保証される（Codex の `resume` が新規 rollout を作るのとは性質が異なる）。検査先: `issue-review-design` | agent × resume × 経路の表（§ 方針 1）として明文化 |
| 保護範囲拡大による `Exception` 経路の副次変化 | pre-loop 区間の `Exception` も `workflow_end status=ERROR` を残すようになることを受け入れる | AI の仮定。根拠: 現行はこの区間の例外が `workflow_end` を欠き `select_target_run_dir` に「実行中」として triage を拒否されていた。ERROR 記録は triage 可能にする方向の変更で、exit code map / artifact field 集合を侵さない。検査先: `issue-review-design` / `issue-review-code` | § 方針 3-3 に影響範囲として明記し、通常 `COMPLETE` / 正規 `ABORT` / ambiguous ABORT の非退行を Medium 回帰観点に追加 |
| pane-dead × claude の `launch_session_id` 保存可否 | **保存しない**。timeout × claude では保存する | AI の仮定。根拠: pane 死亡は「wrapper が claude を起動できなかった / 起動直後に落ちた」を含み、その場合 kaji が採番した UUID に対応する session は実在しない。一方 timeout は pane が全 timeout 区間を生存した（実障害 2 run とも `pane_dead=0`）＝ `claude --session-id <uuid>`（`assets/interactive-terminal/wrapper.sh:72`）が受理された証拠になる。検査先: `issue-review-design` | `pane_alive` フラグで分岐する helper として実装。制約 6「推測しない」の具体化 |
| timeout 経路の grace wait | **置かない**。`_kill_pane` の前に 1 回だけ解決し、kill 後の再走査もしない | AI の仮定。根拠: timeout は既に time budget 超過であり、Codex の resume 行は pane 終了時にしか出ないため待っても得られない。rollout file はセッション進行中に逐次追記される（実障害 run で timeout の 21〜32 秒前まで追記を確認）。検査先: `issue-design`（本書で決定）→ `issue-review-code` | 呼び出し位置を `_write_pane_metadata` の後・`_kill_pane` の前に固定 |
| 巨大 `terminal.log`（51MB / 52MB）の読込コスト | 異常終了経路では `terminal.log` を**読まない**（store 照合のみ）。加えて store 走査を mtime 下限でフィルタする | AI の仮定。根拠: timeout 時は resume 行が未出力で読む価値がない、pane-dead 経路の全文走査は Issue #296 の別契約として不変、store 照合の方が attempt との対応を直接検証できる。検査先: `issue-review-code` | tail 読み / 上限バイト数という選択肢を採らず、読込自体を無くす形で解決。走査上限は既存 `_CODEX_SESSION_SCAN_LIMIT` を再利用 |
| `user_interrupted` の `source` 値 | `"external"` | AI の仮定。根拠: `FailureSource` は 4 値で user/operator が無く、Ctrl-C は harness の外から届く。`source` は直列化と表示のみに使われ判定入力ではない（`grep -rn "\.source\b" kaji_harness/`）。検査先: `issue-review-design` | 新しい `FailureSource` 値を追加しない（artifact schema 不変の制約 2 を優先） |
| `user_interrupted` の decision | `comment_only` | AI の仮定。根拠: `recoverability_hint="no"` の cause は `_COMMENT_ONLY_CAUSES` 所属で `comment_only`、非所属で `not_resumable` になる。`not_resumable` は Issue #349 が「副作用 skill のため手動 resume も封じる」意味で使う語で、手動再開が正当な中断 run には不適。どちらでも `recoverable=False` で auto-resume には入らない（safety gate / budget 不変）。検査先: `issue-review-design` | `_COMMENT_ONLY_CAUSES` への 1 行追加として実装 |
| headless runner の timeout | 変更しない | AI の仮定。根拠: `execute_cli` の session ID は CLI stdout 由来で timeout 時には取得元が存在せず、kaji 採番の ID も無いため「検証可能な ID」が原理的に存在しない。検査先: `issue-review-design`（scope 判断の妥当性） | 根本原因 A の同根経路表に調査結果として記録し、コード変更はしない |

## テスト戦略

### 変更タイプ

実行時コード変更（runner / interactive_terminal / recovery の振る舞い変更）。

### 実装前 Red の扱い（bug 固有ルール）

`design-by-type/bug.md` の escape clause を適用する。Issue #403 本文と #393 の調査コメントに、
OB を直接示す実障害ログ（3 run の timeout 値・Codex session ID・`result.json.session_id=null`・
`turn_aborted(reason=interrupted)`・`workflow_end status=COMPLETE`）が一次情報として存在する。
恒久回帰テストはこの OB に対応する EB（session ID が残る / `ERROR` として記録される）を検証する。
ただし実装は TDD で進め、以下の回帰テストを**修正前に Red**であることを確認してから通す
（実ログ代替は「実装前 Red 証跡の代替」であって恒久テスト省略の根拠ではない）。

### Small テスト

外部依存の無い純粋判定のみ。

- `classify_failure`: `failure_event kind="interrupted"` の snapshot → cause `user_interrupted` /
  `synthetic=True` / `source="external"` / `recoverability_hint="no"`。
- `classify_failure`: `kind="interrupted"` かつ `result.json` 不在でも `kaji_bug_suspected` に
  ならない（`_ATTEMPT_BACKED_KINDS` 非追加の回帰）。
- `models`: `"user_interrupted"` が `FAILURE_CAUSES` と `INCIDENT_EXEMPT_CAUSES` に含まれ、
  `set(INCIDENT_SUPPRESSION_REASONS) == set(INCIDENT_EXEMPT_CAUSES)` の既存不変条件を保つ
  （`tests/test_recovery_models.py:80,99,101` の集合等価アサーションを更新する必要がある）。
- `report`: `_CAUSE_DESCRIPTIONS["user_interrupted"]` が triage コメント本文に載る。
- `plan_recovery`（handler の純関数）: cause `user_interrupted` → `decision="comment_only"` /
  `recoverable=False`。既存 cause の decision が変わらないこと。
- `_resolve_abnormal_exit_session` の非 I/O 分岐: claude × resume 入力あり → resume ID、
  claude × fresh × `pane_alive=True` → `launch_session_id`、claude × fresh × `pane_alive=False` →
  `None`、antigravity → `None`。いずれも `SessionResolution` を返し「未試行（`None`）」とは
  区別できること。

### Medium テスト

ファイル I/O・fake tmux・fake session store を伴う結合。既存 fixture（`tests/test_interactive_terminal.py`
の `_make_fake_tmux` / `TestCodexSessionIdExtraction`、`tests/test_runner_interactive_dispatch.py`
の `_make_runner`）を再利用する。

session 解決:

- timeout × codex（fresh）: `CODEX_HOME` に marker 一致 rollout が 1 件（mtime > `prompt.txt`）→
  `StepTimeoutError.session_resolution.session_id` にその UUID が載り、runner 経由の
  `result.json.session_id` が一致する。
- timeout × codex（fresh）: marker 一致 rollout が 2 件 → `result.json.session_id is None`（fail-safe）。
- timeout × codex（fresh）: marker 一致 rollout が 0 件 / `CODEX_HOME/sessions` 不在 →
  `result.json.session_id is None`。
- timeout × codex: marker には一致するが mtime が `prompt.txt` より古い rollout のみ → `None`
  （resume 親 rollout を誤採用しない回帰）。
- **timeout × codex（`resume:` step、resume 入力あり）: 一意な新 rollout 1 件 → その新 UUID が
  `result.json.session_id` に入り、resume 入力の親 ID とは異なる。**
- **timeout × codex（`resume:` step）: 一致 0 件 → `result.json.session_id is None`。
  resume 入力の親 ID が書き込まれ**ない**ことを明示的に assert する（review 指摘 1 の回帰）。**
- **timeout × codex（`resume:` step）: 一致 2 件以上 → `result.json.session_id is None`。
  同上、親 ID へ fallback しない。**
- **pane-dead × codex（`resume:` step）: 一致 0 件 → `result.json.session_id is None`（親 ID なし）。**
- timeout × codex: 異常終了経路で `terminal.log` を読まない（巨大 log を置いても
  `codex resume <uuid>` を採用しない）。
- timeout × claude（fresh）: `launch_session_id` が `result.json.session_id` に載る。
- pane-dead × claude（fresh）: `launch_session_id` を載せない（`None`）。
- timeout / pane-dead × claude（`resume:` step）: resume 入力の ID が `result.json.session_id` に
  載る（`--resume` は同一 session 継続。既存挙動の維持）。
- pane-dead × codex（fresh）: marker 一致 1 件 → `CLIExecutionError.session_resolution` に載る。
- **`session_resolution` を載せない経路の非退行: headless（`cli.py`）/ `exec_script` の
  `StepTimeoutError` では既存 fallback が働き、resume 入力の session ID が
  `result.json.session_id` に残る（`getattr(exc, "session_resolution", None) is None` の分岐）。**
- verdict 検出経路の回帰: 既存 `TestCodexSessionIdExtraction` の 3 ケース
  （terminal.log 抽出 / store fallback / 無関係 rollout 無視）が現状の期待値のまま green。
- `resume:` step の回帰: 既存の resume 経路が `MissingResumeSessionError` を出さない。

割込み:

- `WorkflowRunner.run()` の step dispatch が `KeyboardInterrupt` を送出 →
  (a) `KeyboardInterrupt` が呼出元へ再送出される、(b) `run.log` 末尾が
  `workflow_end status=ERROR` かつ `error` が `KeyboardInterrupt:` で始まる、
  (c) `failure_event kind="interrupted"` が `step_id=<dispatch 中の step>` 付きで 1 件、
  (d) 進行中 attempt に `result.json` が**作られない**。
- **main-loop dispatch 前の割込み（`_apply_cycle_reset` から `KeyboardInterrupt`）→
  (a) 再送出される、(b) `workflow_end status=ERROR` が記録される、
  (c) `failure_event kind="interrupted"` が `step_id=None` で 1 件、
  (d) `result.json` は 1 件も作られない（review 指摘 2 の回帰）。**
- **`ambiguous_worktree` ABORT 経路の非退行: `workflow_end` が 1 件だけ（`status=ABORT`）で、
  保護範囲拡大による `COMPLETE` の二重記録が起きない（`workflow_end_logged` フラグの回帰）。**
- **pre-loop 区間で `Exception`（`_apply_cycle_reset` が送出）→ `workflow_end status=ERROR` が
  記録され、例外が伝播する（§ 方針 3-3 の意図した変更を固定する）。**
- `interactive_terminal` の polling 中に `KeyboardInterrupt` →
  `pane-metadata.json` が書かれ、`kill-pane` が呼ばれず（fake tmux の argv 記録で検証）、
  `KeyboardInterrupt` がそのまま伝播する。
- `_write_pane_metadata` が `OSError` を投げても `KeyboardInterrupt` が別例外に置き換わらない。

終了契約の回帰（既存挙動が変わらないこと）:

- 通常完了 → `workflow_end status=COMPLETE` のまま。
- agent の正規 `ABORT` → `workflow_end status=ABORT` / `failure_event kind="agent_abort"` /
  `synthetic=false` のまま。
- `Exception` 経路 → `status=ERROR` / `failure_event kind="dispatch_exception"` のまま。

recovery 提示:

- `collect_snapshot`: `result.json.session_id` がある run → evidence に
  `result.json session_id=` 行が含まれる。`session_id` が `null` の run → 当該行が出ない。
- `collect_snapshot`: `kind="interrupted"` の run で `pane-metadata.json` があれば
  `orphan pane pane_id=` 行が出る。無い / 壊れている場合は行が出ず、
  `artifact_read_errors` も増えない（`kaji_bug_suspected` に落ちない回帰）。
- `kaji recover` が中断 run（`status=ERROR`）を triage 対象として受理する
  （`select_target_run_dir` の回帰）。

### Large テスト

**追加しない。** 理由を `docs/dev/testing-convention.md` の 4 条件に沿って示す:

1. 独自ロジックの追加は session 一意判定・例外への値受け渡し・終端 status の分岐で、いずれも
   実 tmux / 実 agent CLI を必要としない。実 API 疎通も外部サービス結合も新規に増えない。
2. 想定される不具合パターン（pane lifecycle、実 tmux 上の kill/remain-on-exit、wrapper 契約）は
   既存の `tests/test_interactive_terminal.py::TestInteractiveTerminalEndToEnd`（`large_local` /
   実 tmux + fake agent bin）と `tests/test_recovery_e2e_large_local.py` が既に保護している。
3. 実 `claude` / `codex` の対話 CLI に対する `Ctrl-C` 送出と 100 分 timeout の再現は、
   `docs/cli-guides/interactive-terminal-runner.ja.md` § 手動検証手順が明記するとおり意図的に
   自動化しない領域（実 API 課金・対話 CLI の自動化困難）であり、Large を足しても回帰検出情報が
   増えない。
4. 既存 Large を壊さないことは `make check`（全テスト実行）で確認する。

### 変更固有の一時検証

- `source .venv/bin/activate && make check`（`ruff` / `mypy` / 全 `pytest`）。
- 実障害 artifact に対する事後照合（恒久化しない）: `.kaji-artifacts/391/runs/260730222002/` の
  `prompt.txt` path を marker に、`_unique_codex_session_id_from_store` 相当の判定が
  `019fb36d-1346-7773-b7c9-b18a9da494d2` を一意に返すか手元で確認する。Codex session store の
  現物は共有できないため恒久テストにはしない（テストは合成 fixture で行う）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|---|---|---|
| `docs/adr/` | なし | 新規の技術選定なし。incident 抑止は ADR ではなく Issue #322 が確立した既存方針の踏襲、例外への値受け渡しは Issue #222 / ADR 006 の既存パターンの踏襲 |
| `docs/ARCHITECTURE.md` | なし | recovery layer の package 構成・責務分界（runner は構造化記録のみ）は不変 |
| `docs/cli-guides/failure-recovery.ja.md` / `.md` | **あり** | § incident 記録の対象外 に `user_interrupted` を追加（該当ケース・判定入力が `failure_event.kind` であること・triage コメント / artifact / console は維持されること） |
| `docs/cli-guides/interactive-terminal-runner.ja.md` / `.md` | **あり** | § session 継続 に異常終了経路の解決規則（一意一致のみ採用 / verdict 経路との規則差 / claude は timeout のみ）を追記。手順 6 の timeout 記述の隣に「割込み時は kill しない」を追記 |
| `docs/dev/workflow_guide.md` | **あり** | § 第1層 の「記録の対象外（Issue #322）」に `user_interrupted` を追加 |
| `docs/dev/` その他 | なし | workflow 定義・skill lifecycle は不変 |
| `docs/reference/` | なし | config key・CLI 引数・公開 API に変更なし |
| `docs/cli-guides/local-mode.md` 等 provider 系 | なし | provider 非依存の変更 |
| `AGENTS.md` / `CLAUDE.md` | なし | 開発規約の変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|---|---|---|
| Issue #403 本文 `## 決定事項` | https://github.com/apokamo/kaji/issues/403 | grill-me の人間決定。`failure_event kind=interrupted` / cause `user_interrupted` の**文字列固定**、run レベルのみの証跡、pane を kill しない、対象 3 経路、一意性規則を異常終了経路に限定、`FailureSnapshot` + 既存 `evidence` での提示 |
| Issue #403 `## grill-me provenance` コメント | https://github.com/apokamo/kaji/issues/403#issuecomment-5373459783 | 決定理由と棄却案。特に「Codex の resume は履歴を引き継ぐ新規 rollout を作るため同一 marker が正当に複数一致しえ、verdict 経路へ一意性を強制すると既存 `resume:` step が `MissingResumeSessionError` で退行する」 |
| Issue #393 原因調査コメント | https://github.com/apokamo/kaji/issues/393#issuecomment-5372780233 | OB の一次情報。3 run の timeout 値・pane 生存・Codex session 進行・`session_id=null`・`workflow_end status=COMPLETE` |
| ADR 006 | `docs/adr/006-attempt-result-json.md` | 「143 / SIGTERM / timeout / interruption のような**異常終了の exit_code / signal が成果物に残らない**」を解決課題とし、`result.json` が `status` / `exit_code` / `signal` / 時刻 / `duration_ms` / `session_id` を保存する契約と定義する |
| failure triage CLI リファレンス | `docs/cli-guides/failure-recovery.ja.md` | 「`kaji run` の workflow process が `ERROR`、または triage 対象の `ABORT` で終了したときに、原因を分類して証跡を残す」「対象 run の終了 status が `ERROR` / `ABORT` 以外の場合も exit 2」。§ incident 記録の対象外（Issue #322）が `user_precondition_error` の前例 |
| interactive terminal runner ガイド | `docs/cli-guides/interactive-terminal-runner.ja.md` | § session 継続 の現行規則（claude は runner 採番 UUID を `--session-id`、codex は `terminal.log` → session store の mtime 降順走査、antigravity は常に `None`）。§ 手動検証手順「real `claude` / `codex` / `agy` のライブ疎通は**意図的に自動化しない**」 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の判定基準（外部 API → Large、ファイル I/O → Medium、純粋関数 → Small）と、恒久テストを追加しない場合の 4 条件 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | OB / EB / 再現手順の分離、根本原因の「なぜ」「いつから」「同根の他箇所」、再現テスト必須と実ログ escape clause |
| 重要判断チェックリスト | `.claude/skills/_shared/critical-decision-checklist.md` | 3 分類（決定済み方針の詳細化 / two-way door / one-way door）と provenance 4 列の記録形式 |
| 現行実装（本設計の対象） | `kaji_harness/interactive_terminal.py:380,439-458,461-474,477-487,839-905` | verdict 経路のみが session を解決し、timeout / pane-dead 経路は解決しない。`_extract_codex_session_id_from_store` は mtime 降順の**最初の**一致を採用し複数一致を検出しない |
| 現行実装（本設計の対象） | `kaji_harness/runner.py:330,546-603,956,1133-1148` | `_record_dispatch_failure` は `getattr(exc, "returncode", None)` で例外から値を取り出す既存パターンを持つ。`end_status="COMPLETE"` 初期化 + `except Exception` + `finally` で `log_workflow_end` |
| 現行実装（本設計の対象） | `kaji_harness/recovery/classify.py`, `models.py:83`, `handler.py:92-94,517-518`, `snapshot.py`, `signature.py` | `INCIDENT_EXEMPT_CAUSES` / `INCIDENT_SUPPRESSION_REASONS` の対応関係、`_COMMENT_ONLY_CAUSES` の効果、fingerprint が `failure_error_text` 由来で `evidence` に影響されないこと |
| wrapper 契約 | `kaji_harness/assets/interactive-terminal/wrapper.sh:68-92` | claude は `--session-id <launch_session_id>` / `--resume <resume_session_id>`、codex は `codex resume --cd ... <resume_session_id>`。claude の `launch_session_id` が実 session の識別子として CLI に渡ることの根拠 |
