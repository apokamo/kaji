# [設計] 起動コンソールへの harness progress と review-poll heartbeat 表示

Issue: #235

## 概要

`kaji run` の起動コンソールに、ハーネス自身の進行状況（workflow / step / verdict / transition）を
Python 標準 `logging` 経由で日時付き `[kaji]` 行として出力し、あわせて `review-poll` の deterministic
polling ループに `POLL_INTERVAL_SEC`（10s）ごとの heartbeat を stdout へ flush 出力する。

## 背景・目的

### ユースケース

- **`interactive_terminal` 利用者として**、agent の作業は pane 側に見えるが起動コンソールでは
  ハーネスが何をしているか分からない。**起動コンソールだけを見て** harness がいま workflow の
  どの step を起動・遷移したかを時系列で追跡したい。
- **`review-poll` 運用者として**、最大 30 分 polling しうる `review-poll` step が「待機中 /
  停止中 / エラー」のどれなのかを、`run.log` を別途開かずに **起動コンソールの heartbeat で** 即座に
  切り分けたい。

### 代替案と不採用理由

- **独自 `ConsoleReporter` 出力制御層を新設する案**: 不採用。Issue 方針が「stdlib logging の
  level / handler / formatter で制御し、独自出力制御層は作らない」と明示。stdlib logging の
  二ハンドラ（stdout / stderr）で要件を満たせるため、新抽象は過剰。
- **`RunLogger`（JSONL）に人間可読出力も兼ねさせる案**: 不採用。`run.log` は機械可読契約
  （`docs/reference/python/logging.md`）であり、人間向け表示と責務が異なる。JSONL 契約は不変に保つ。

## インターフェース

### 入力

| 項目 | 型 / 値 | 説明 |
|------|---------|------|
| `--log-level`（新規 CLI option, `kaji run`） | `DEBUG` / `INFO` / `WARNING` / `ERROR`、default `INFO` | 起動コンソール progress の閾値 |
| `--quiet`（既存） | flag | agent / exec の **stdout streaming** を抑制（既存 `verbose=not args.quiet` 意味を維持）。harness progress とは独立 |
| `POLL_INTERVAL_SEC`（既存定数） | `10`（秒） | heartbeat 出力間隔。polling 間隔と同一 |

### 出力（副作用）

1. **harness progress（stdlib logging）**: `INFO` 以下 → **stdout**、`WARNING` 以上 → **stderr**。
   formatter は以下（local time。`script_exec` の relay 行 `[ts] [step_id] ...` が `datetime.now()`
   ＝ local time を使うため、両者を同一タイムラインに並べるには console も local time にそろえる）。

   ```text
   # INFO 以下（stdout handler）
   [2026-06-07T12:34:56] [kaji] workflow start: feature-development issue #235
   # WARNING 以上（stderr handler）
   [2026-06-07T12:34:56] [kaji] WARNING: resolve_pr_context for branch 'feat/...' failed: ...
   ```

2. **review-poll heartbeat（subprocess stdout）**: `codex_review_poll` が polling 毎に 1 行 stdout へ
   `print(..., flush=True)`。これは exec step として `script_exec._run_argv` が
   `[{_now_stamp()}] [{step.id}] {line}` 形式で relay するため、起動コンソールには
   `[2026-06-07T13:00:01] [review-poll] polling PR #176 head=abc1234 elapsed=0s remaining=1800s`
   のように表示される（`[review-poll]` prefix・日時は relay 側が付与、heartbeat 行自体は素の本文）。

3. `RunLogger` の `run.log`（JSONL）への出力契約は **不変**。

### 使用例

```bash
# 既定（INFO）: harness progress が stdout に出る
kaji run .kaji/wf/feature-development.yaml 235

# harness progress を抑え、警告/エラーのみ表示
kaji run .kaji/wf/feature-development.yaml 235 --log-level WARNING

# agent/exec の stdout streaming だけ止める（harness progress は INFO のまま出る）
kaji run .kaji/wf/feature-development.yaml 235 --quiet
```

### エラー

- `--log-level` に未定義値 → argparse `choices` で fail-fast（exit 2 相当、既存定義エラー経路）。
- **heartbeat emitter の失敗隔離**: heartbeat 出力中の `sys.stdout.write` / `flush` が
  `BrokenPipeError` または `OSError` を送出しても、polling 判定ロジックには一切影響させない
  （観測のみの副作用。verdict 判定は不変）。
  - **捕捉責務の所在**: 二段で containment する。
    1. **`_default_emit(line)`（出力実体）**: `sys.stdout.write` / `flush` を `try/except
       (BrokenPipeError, OSError)` で囲み、捕捉したら **黙って return**（heartbeat は best-effort）。
       pipe 切断（reader 側の `script_exec` が先に閉じた等）を polling 失敗に昇格させない。
    2. **`run_polling`（呼び出し側）**: 注入された `emit_progress` が任意の例外を投げても
       polling state を変えないよう、`emit_progress(...)` 呼び出しを `try/except Exception`
       で囲み、例外を捨てる。これにより custom emitter（テストの fake 含む）が壊れても
       state machine の終了判定は不変に保たれる。
  - **根拠**: 組み込み `print` / stream の `flush=True` は flush を強制するが、write/flush 失敗を
    自動で無害化する契約ではない（後述 Primary Sources）。「観測のみの副作用」を満たすには
    上記の明示的 containment が必要。

## 制約・前提条件

- **依存追加なし**: Python 標準 `logging`（`StreamHandler` / `Filter` / `Formatter`）のみ使用。
- **`RunLogger` JSONL 契約の非破壊**: `logger.py` のフィールド・イベント名は変更しない。
- **verdict marker 非汚染**: heartbeat 行は `---VERDICT---` / `---END_VERDICT---` および
  `---` 始まりの marker 類似文字列を含めない（`verdict.py` の抽出正規表現を壊さない）。
- **flush 必須**: subprocess の stdout は tty でないため Python は block-buffer する。各 heartbeat 行は
  `flush=True` で即時送出しないと `script_exec` の pipe で終了まで溜まる。
- **`--quiet` 連動**: heartbeat は exec stdout streaming に乗るため、`--quiet`（`verbose=False`）時は
  `script_exec` 側で relay されない。これは「agent/exec streaming 抑制」という `--quiet` 既存意味と整合。
- **polling 判定ロジック不変**: `classify` / `run_polling` の状態機械の終了判定（PASS / RETRY /
  BACK_FALLBACK / ABORT）は変更しない。heartbeat は副作用追加のみ。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/cli_main.py` | `--log-level` option 追加、`configure_console_logging(level)` 呼び出し（`cmd_run` 冒頭） |
| `kaji_harness/console_log.py`（新規・小） | `configure_console_logging()`：stdout（`<= INFO`）/ stderr（`>= WARNING`）の二ハンドラ + 二 formatter をルート `kaji` logger に設定。冪等 |
| `kaji_harness/runner.py` | workflow start / step start / verdict detected / step end / transition / cycle / barrier / abort / workflow end の各点で `logging.getLogger("kaji.runner").info()/warning()` を発火（既存 `RunLogger` 呼び出しと並置） |
| `kaji_harness/script_exec.py` | `exec start` progress を **`_run_argv`（`exec` / `exec_script` 共有コア）** から発火。実 argv を知るのは runner ではなくこの層のため（後述 § 2 の argv 表示参照） |
| `kaji_harness/interactive_terminal.py` | pane launch 成功直後に pane launched progress を `logging.getLogger("kaji.interactive_terminal").info()` |
| `kaji_harness/scripts/codex_review_poll.py` | `run_polling` に progress emit を追加（injectable callback、default は stdout flush print）。`format_heartbeat()` 純粋関数を追加 |
| `tests/` | console routing / formatter / heartbeat content / verdict 非破壊 / flush / **emitter 失敗隔離** / runner progress のテスト追加 + 既存 `tests/test_cli_timestamp.py::test_quiet_flag_suppresses_timestamp_output` の期待値更新（`[kaji]` progress 行は残る前提に絞る） |

kaji は Python 単一スタック。backend/frontend の Scope 分岐は無い。

## 方針（Minimal How）

### 1. console logging 設定（`console_log.py`）

stdout/stderr split は logging の標準パターン（stdout handler に `levelno < WARNING` の Filter、
stderr handler に `setLevel(WARNING)`）。

```python
ISO_LOCAL = "%Y-%m-%dT%H:%M:%S"  # local time。script_exec relay と整合

def configure_console_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("kaji")
    root.setLevel(level)
    root.propagate = False
    # 冪等化: 既存 kaji handler を除去してから張り直す（再呼び出し / テスト対策）
    for h in [h for h in root.handlers if getattr(h, "_kaji", False)]:
        root.removeHandler(h)

    out = logging.StreamHandler(sys.stdout)
    out.addFilter(lambda r: r.levelno < logging.WARNING)
    out.setFormatter(logging.Formatter("[%(asctime)s] [kaji] %(message)s", ISO_LOCAL))
    out._kaji = True  # type: ignore[attr-defined]

    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(logging.Formatter("[%(asctime)s] [kaji] %(levelname)s: %(message)s", ISO_LOCAL))
    err._kaji = True  # type: ignore[attr-defined]

    root.addHandler(out)
    root.addHandler(err)
```

- 各モジュールは `logging.getLogger("kaji.<module>")` を使い、`kaji` ルートのハンドラに伝播させる。
- `runner.py` 既存の `_logger = logging.getLogger(__name__)`（`kaji_harness.runner`）は **別系統**。
  console progress 用には `kaji.*` 名前空間の logger を新規に用いる（混線回避）。

### 2. runner progress 発火点

既存 `RunLogger` 呼び出しの隣で、人間可読メッセージを `logging` に出す。表示対象は Issue「表示対象」節を網羅:

| 進行 | 例（message 部のみ。formatter が `[ts] [kaji]` を付与） |
|------|--------|
| workflow start | `workflow start: <workflow.name> issue <issue_ref>` |
| step start | `step start: <step_id> attempt-NNN dispatch=<agent\|exec\|exec_script> [agent=.. model=.. effort=..]`（runner.py） |
| exec start | `exec start: <argv を join、長大なら省略>`（**script_exec.py の `_run_argv`**。理由は下記） |
| pane launched（interactive_terminal） | `pane launched: <step_id> pane=<pane_id> verdict=<verdict_path>` |
| verdict detected | `verdict detected: <step_id> source=<artifact\|comment\|stdout> status=<status>` |
| step end | `step end: <step_id> status=<status> duration=<ms>ms next=<next_step_id\|end>` |
| cycle iteration / exhaust | `cycle iteration: <name> <i>/<max>` / `cycle exhausted: <name>` |
| `--before` barrier | `barrier hit: <step>` / `barrier missed: <step>`（missed は warning） |
| abort / error | `WARNING: ...` / `ERROR: ...`（既存 `sys.stderr.write` 群を logging へ寄せる or 併置） |
| workflow end | `workflow end: status=<status> duration=<ms>ms` |

> next step id は `current_step.on.get(verdict.status)` 確定後に出す。step_end の next を表示するため、
> transition 解決後に step end を出す順序とする（or step end と transition を 2 行に分ける）。

#### `exec start` の argv 表示位置（`exec` / `exec_script` 共通化）

`exec start` だけは runner.py ではなく **`script_exec.py` の `_run_argv`** から発火する。理由:

- 実 argv を組み立てるのは `_run_argv` の呼び出し元であり、runner.py は知らない:
  - `execute_exec` → `command_label=" ".join(argv)`（workflow.yaml の任意 argv そのまま）
  - `execute_script` → `args=[sys.executable, "-m", module]` / `command_label=module`（runner からは module 名のみ）
- もし runner.py で表示すると `exec_script` は module 名しか出せず、`exec` と表示粒度が食い違う。
- `_run_argv` は両 dispatch の唯一の共有コアで、既に `command_label`（`exec`=full argv / `exec_script`=module）と
  実 `args` を保持する。ここで `exec start: <argv>` を 1 箇所だけ発火すれば、両 dispatch で
  **同じ作り方の argv 文字列**（`" ".join(args)`。長大時は末尾省略）を一貫表示でき、実装重複も避けられる。
- relay 行 `[ts] [step_id] ...` と同じ `_now_stamp()`（local time）コンテキストに並ぶため、
  console formatter の local time とも整合する。

### 3. review-poll heartbeat

`run_polling` のループ末尾（`sleep` 直前）で progress を 1 回 emit。injectable callback で testability を確保。

```python
def format_heartbeat(*, elapsed_sec, pr_number, head_sha, state, remaining_sec) -> str:
    # marker 非汚染: "---" を含めない素のテキスト
    return (f"polling PR #{pr_number} head={head_sha[:7]} "
            f"state={state} elapsed={int(elapsed_sec)}s remaining={max(0, int(remaining_sec))}s")

def run_polling(..., emit_progress=_default_emit):  # _default_emit = print(..., flush=True)
    ...
    while True:
        ...                                  # 既存判定（不変）
        emit_progress(format_heartbeat(
            elapsed_sec=now() - start,
            pr_number=pr_number, head_sha=head_sha,
            state=state, remaining_sec=no_reaction_timeout_sec - (now() - start)
            # in_progress 中は in_progress_timeout 基準の残を表示する分岐を持たせる
        ))
        sleep(poll_interval_sec)
```

- `_default_emit(line)` は `sys.stdout.write(line + "\n"); sys.stdout.flush()` を行い、
  `BrokenPipeError` / `OSError` を捕捉して return する（best-effort。失敗を例外として伝播させない）:

  ```python
  def _default_emit(line: str) -> None:
      try:
          sys.stdout.write(line + "\n")
          sys.stdout.flush()
      except (BrokenPipeError, OSError):
          return  # heartbeat は観測のみ。pipe 切断を polling 失敗に昇格させない
  ```

- `run_polling` 側でも、注入 emitter が壊れても state machine を守るため emit 呼び出しを隔離する:

  ```python
  try:
      emit_progress(format_heartbeat(...))
  except Exception:
      pass  # emitter 例外は polling 判定に影響させない（観測のみの副作用）
  ```

- terminal state（`done_pass` / `done_retry`）で return する経路では heartbeat を出さず即 return（verdict 直前に
  不要な progress を混ぜない）。`---VERDICT---` block は従来どおり `emit_verdict` が最後に 1 度出す。

## テスト戦略

> **CRITICAL**: 実行時コード変更（CLI option / logging 副作用 / polling ループ副作用）。
> Small / Medium で検証観点を定義する。Large は新規追加しない（理由を明記）。

### 変更タイプ

実行時コード変更（起動コンソール出力・subprocess stdout 副作用・CLI option 追加）。

### Small テスト

- **console routing**: `configure_console_logging()` 後、`StringIO` を差した stdout/stderr 相当で、
  `INFO`/`DEBUG` レコードが stdout 側のみ、`WARNING`/`ERROR` が stderr 側のみに出ることを検証。
- **formatter 整形**: INFO 行が `^\[<ISO local>\] \[kaji\] <msg>$`、WARNING 行が
  `\[kaji\] WARNING: <msg>` 形式であることを正規表現で検証。`--log-level WARNING` 時に INFO が出ないこと。
- **冪等性**: `configure_console_logging()` 二度呼びでハンドラが重複登録されない（行が二重化しない）。
- **`format_heartbeat()` 純粋関数**: 必須要素（PR 番号・head[:7]・elapsed・remaining・state）を含み、
  かつ `---VERDICT---` / `---END_VERDICT---` / `---` を **含まない** ことを assert。
- **`--log-level` parse**: default `INFO`、未定義値で argparse エラー（`choices`）。

### Medium テスト

- **`run_polling` heartbeat**: `now` / `sleep` / `emit_progress`（収集リスト）を注入し、poll 毎に 1 回
  heartbeat が呼ばれること、elapsed / remaining が単調に推移すること、終了判定（PollResult.state）が
  従来と同一であることを検証（既存の `run_polling` medium テスト資産を流用）。
- **verdict parse 非破壊**: heartbeat を複数行混ぜた `codex_review_poll.main()` の合成 stdout を
  `parse_verdict_block()`（現行 `kaji_harness/verdict.py:544` に実在）/ `resolve_verdict()`（同 `:608`）に渡し、
  抽出される `status` が heartbeat 無しの場合と一致することを検証
  （完了条件「heartbeat が `---VERDICT---` parse を破壊しない」をテストで固定）。
- **flush 検証**: `write`/`flush` 呼び出しを記録する fake stream を `_default_emit` に差し、各 heartbeat で
  `flush` が呼ばれることを assert（subprocess pipe で終了まで溜まらないことの単位化）。
- **emitter 失敗隔離（Must Fix 対応）**: 二観点を固定する。
  1. **`_default_emit` の例外吸収**: `sys.stdout` を `write`/`flush` で `BrokenPipeError`（および
     `OSError`）を投げる fake stream に差し替え、`_default_emit("line")` が **例外を送出せず return** すること。
  2. **`run_polling` の state 不変**: 必ず例外を投げる `emit_progress`（`raise BrokenPipeError` / 任意 `Exception`）を
     注入して `run_polling` を回し、heartbeat 無し（`emit_progress` 省略時）と **同一の `PollResult.state`**
     （done_pass / done_retry / done_fallback / done_abort）になることを、既存の medium 状態機械シナリオで検証。
     → 「heartbeat は観測のみの副作用」を回帰テストで固定する。
- **runner progress**: 既存 runner テスト（exec-step 最小 workflow）を `capsys` で回し、`[kaji] workflow
  start` / `step start` / `exec start` / `verdict detected` / `step end ... next=...` / `workflow end` が
  stdout に、WARNING 系が stderr に出ることを検証。`RunLogger` の `run.log` JSONL が不変であることも併検証。
- **既存 `--quiet` テストの整合更新（Should Fix 対応）**:
  `tests/test_cli_timestamp.py::test_quiet_flag_suppresses_timestamp_output`（現状 `:347-390`）は
  「`--quiet` 時に `^\[<ISO>\]` で始まる timestamp 行が stdout に **一切** 出ない」ことを期待している。
  本設計では `--quiet`（agent/exec streaming 抑制）でも harness progress（`[ts] [kaji] ...`）は
  `--log-level`（default INFO）で出るため、この期待は成立しなくなる。**期待値を「抑制対象は
  agent/exec relay 行（`[ts] [step_id] ...`、`[kaji]` 以外の prefix）のみで、`[ts] [kaji] ...` の
  harness progress 行は残る」へ更新する**（regex を `\[kaji\]` を除外する形に絞る、または relay prefix を
  明示的に対象化する）。この更新は本設計に伴う必須変更として `tests/` 変更スコープに含める。

### Large テスト

- **追加しない**。理由（`docs/dev/testing-convention.md` の 4 条件）:
  1. polling の API 経路・判定ロジックに新規ロジックを追加しない（heartbeat は観測副作用のみ）。
  2. 想定不具合（marker 汚染 / flush 漏れ / routing 誤り）は上記 Small/Medium と既存品質ゲートで捕捉済み。
  3. 実 GitHub 疎通を足しても、logging/heartbeat に関する新規回帰シグナルはほぼ増えない
     （実 API 疎通は既存 review-poll Large テストが既にカバー）。
  4. 以上をレビュー可能な形で説明できる。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/reference/python/logging.md | **あり** | 「禁止事項: 標準 logging の直接使用」が現状 blanket 記述。これを **RunLogger / run.log（JSONL 機械可読ログ）文脈に scope 限定** し、別途「起動コンソール向け console progress logging（stdlib logging, `kaji.*` 名前空間）」層の存在と方針（stdout/stderr split・formatter）を追記する必要がある |
| docs/cli-guides/ | **あり** | `kaji run` の `--log-level` option 追記。`interactive-terminal-runner.md` に起動コンソール progress（pane launched 等）への言及余地 |
| docs/ARCHITECTURE.md | **あり（軽微）** | 起動コンソール observability 層（人間向け console logging）と `run.log`（機械可読 JSONL）の責務分離を 1 段落で補足 |
| docs/adr/ | なし | 新ライブラリ採用なし（stdlib logging）。`ConsoleReporter` 不採用は Issue 方針で確定済みのため ADR 不要 |
| docs/dev/ | なし | workflow / 開発手順の変更なし |
| CLAUDE.md | なし | 規約・必読文書の変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python `logging` HOWTO | https://docs.python.org/3/howto/logging.html | 「Logging Levels」「Handlers send the log records to the appropriate destination」。複数 Handler で出力先を分離し、Handler ごとに level / Filter / Formatter を独立設定できる → stdout(`<WARNING`)/stderr(`>=WARNING`) 二分割の根拠 |
| `logging.Formatter` (datefmt) | https://docs.python.org/3/library/logging.html#logging.Formatter | `datefmt` で `asctime` の書式を指定可能。`%Y-%m-%dT%H:%M:%S` で `2026-06-07T12:34:56`（local time）形式を再現 |
| `logging.Filter` | https://docs.python.org/3/library/logging.html#filter-objects | Handler に Filter を付け `record.levelno < WARNING` で INFO 以下のみ stdout に通す根拠 |
| 組み込み `print`（flush） | https://docs.python.org/3/library/functions.html#print | `flush` 引数で「stream は強制的に flush される」。pipe（非 tty）出力時の block buffering を回避し heartbeat を即時送出する根拠 |
| 現行 relay 実装 | `kaji_harness/script_exec.py:146` | `print(f"[{_now_stamp()}] [{step.id}] {stripped}")`。`_now_stamp()` は `datetime.now().isoformat(timespec="seconds")`（local time）→ console formatter を local time にそろえる根拠 |
| RunLogger JSONL 契約 | `docs/reference/python/logging.md` / `kaji_harness/logger.py` | JSONL 機械可読ログの source of truth。console logging はこの契約に影響しない別系統であることの裏付け |
