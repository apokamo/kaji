# [設計] Herdr pane 早期終了の CLIExecutionError メッセージに端末ログ末尾の失敗原因行を残す

Issue: #527

## 概要

Herdr pane が `verdict.yaml` を書かずにシェルへ戻った（`no_pattern`）とき、`CLIExecutionError` のメッセージは
`stderr[:200]` で切られ、端末ログ末尾の失敗原因行が `result.json.error`・`run.log`・incident から失われる。
また runner の banner しか残らないため、原因の違う失敗が同じ `fingerprint_hash` に集約される。
`no_pattern` 経路に限り、メッセージを「端末ログ末尾窓から選んだ失敗原因行を、fingerprint と同じ正規化に
1 回通したもの」に替え、`CLIExecutionError` はこの経路で渡された要約を切らずにメッセージへ入れる。
headless 経路・AI formatter は変更しない（調査結果のみ記録）。

## 背景・目的

### Observed Behavior（OB）

利用側 incident apokamo/kamo2#2018（run `261031204817`、発生元 apokamo/kamo2#2017、kaji 0.24.0、
Herdr 0.9.5）の attempt artifact を一次情報として確認した（ローカル:
`/home/aki/dev/kamo2/.kaji/artifacts/2017/runs/261031204817/steps/review-ready/attempt-001/`）。

- `terminal.log`（1188 bytes）/ `pane-metadata.json` の `terminal_diagnostic`（`kind = "no_pattern"`、
  `clean_tail` 1126 文字）は、wrapper banner → Node の uncaught exception → `Node.js v26.10.0` で終わる:

  ```text
  kaji interactive terminal runner
  agent: codex
  workdir: /home/aki/dev/kamo2
  prompt_path: /home/aki/dev/kamo2/.kaji/artifacts/2017/runs/261031204817/steps/review-ready/attempt-001/prompt.txt
  verdict_path: /home/aki/dev/kamo2/.kaji/artifacts/2017/runs/261031204817/steps/review-ready/attempt-001/verdict.yaml
  model: gpt-6.1-sol
  effort: high
  execution_policy: auto

  The agent must read prompt_path and write a pure YAML verdict to verdict_path.

  file:///home/aki/.nvm/versions/node/v26.10.0/lib/node_modules/@openai/codex/bin/codex.js:108
    throw new Error(
          ^

  Error: Missing optional dependency @openai/codex-linux-x64. Reinstall Codex: npm install -g @openai/codex@latest
      at findCodexExecutable (file:///home/aki/.nvm/versions/node/v26.10.0/lib/node_modules/@openai/codex/bin/codex.js:108:9)
      ... (stack frame 4 行)

  Node.js v26.10.0
  ```

- 同 attempt の `result.json.error`（全文。banner の途中 `prompt` で切れている）:

  ```text
  CLIExecutionError: Step 'review-ready' CLI exited with code 1: Herdr pane returned to its shell before writing verdict.yaml; no known provider error pattern in transcript; log tail:
  kaji interactive terminal runner
  agent: codex
  workdir: /home/aki/dev/kamo2
  prompt
  ```

- 設計時の試作（現行 main の `normalize_error_text` + sha256 = `compute_signature` と同じ算出）で、この
  `error` の hash は `67a9b260bc3dd2b4…`（apokamo/kamo2#2018 と一致）。banner の後を `Killed` だけに
  差し替えた transcript も同じ `67a9b260bc3dd2b4…` になった（別原因の集約を再現）。

### Expected Behavior（EB）

Issue 本文 EB 1–3 と `## 決定事項`（EB 4 追加）を正とする。要約:

1. `CLIExecutionError` のメッセージ（`result.json.error` / `run.log` / incident 根拠欄）に端末ログ末尾の失敗原因の
   情報が入る。上の例では `Error: Missing optional dependency @openai/codex-linux-x64 ...` の行。
2. 失敗原因の行が違う失敗は、違う `fingerprint_hash` になる。
3. メッセージ長の上限は保つ（全文は `terminal.log` / `pane-metadata.json` に残る）。
4. 同じ原因で、実行ごとの値（run_id・path・timestamp・model 等）だけが違う失敗は、従来どおり同じ
   `fingerprint_hash` になる。

根拠: #516（`ScriptExecutionError` の同じ欠陥）の EB と設計書、`kaji_harness/recovery/signature.py` の
module docstring（正規化は occurrence 固有値だけを除き、原因の識別に使う値は残す）。

### 再現手順（Steps to Reproduce）

1. **実ログ**（実装前 Red 証跡の代替。Issue 完了条件 2 が明示的に許可）: 上記 OB の
   `pane-metadata.json` の `clean_tail` 末尾と `result.json.error` の差。
2. **合成再現（メッセージ）**: OB と同じ形（banner + Node の uncaught exception + `Node.js vX` 行）の
   `terminal.log` を用意し、`_recorded_exit_detail(terminal_log, prefix="Herdr pane returned to its shell before writing verdict.yaml")`
   を `CLIExecutionError(step_id, 1, detail)` に渡す。`str(exc)` に `Missing optional dependency` が含まれない（現行 main）。
3. **合成再現（fingerprint）**: 2 と同じ banner を持ち、banner より後だけが `Error: ...` / `Killed` と違う
   2 つの transcript から runner と同じ形（`attempt_error = f"CLIExecutionError: {exc}"`）の
   `FailureSnapshot` を作り `compute_signature` を呼ぶ。`fingerprint_hash` が一致する（現行 main）。

## 根本原因（Root Cause）

### なぜ壊れているか

1. **`CLIExecutionError` が先頭 200 文字だけを残す。** `kaji_harness/errors.py:150` は
   `f"Step '{step_id}' CLI exited with code {returncode}: {stderr[:200]}"`。
2. **Herdr の `no_pattern` detail は先頭に固定文と banner を置く。** `kaji_harness/interactive_terminal_common.py:139`
   は `f"{prefix}; no known provider error pattern in transcript; log tail:\n{clean_tail}"`。前置き（prefix 61 文字 +
   固定文）が 119 文字あり、`stderr[:200]` で残る `clean_tail` は先頭約 80 文字、すなわち wrapper banner
   （`kaji_harness/assets/interactive-terminal/wrapper.sh:20-41` が agent 起動前に必ず出力する）だけになる。
   失敗原因は transcript の**末尾**に出るので、残す位置（先頭）と原因の位置（末尾）が逆になっている。
3. **fingerprint がこのメッセージから作られる。** runner は `result.json.error` を
   `f"{type(exc).__name__}: {exc}"` で書き（`kaji_harness/runner.py:774`）、`recovery/snapshot.py` がそれを
   `attempt_error` とし、`signature.py` の `_canonical_input` → `normalize_error_text` → sha256 で hash にする。
   banner の可変値（`prompt_path` / `verdict_path` の run_id・絶対 path）は `<PATH>` に正規化されるので、
   起動直後の失敗（`clean_tail` が banner から始まる、端末ログ 2000 文字未満）は原因によらず同じ hash になる。

### いつから壊れているか

- `stderr[:200]` は `CLIExecutionError` 導入時から headless 経路用に存在する。
- Herdr backend（Issue #396）が早期終了 detail をこの例外に載せ、#296 で `no_pattern` 分岐に
  `log tail` を付けた時点で「banner だけが残る」形になった。incident fingerprint（Issue #304）が
  `attempt_error` を canonical input にしたことで、表示の欠落が「別原因の集約」に広がった。

### 同じパターンの他の箇所（調査結果）

`## 決定事項` の人間決定（修正範囲）により、以下はコードを変更せず調査結果を記録する。必要なら別 Issue にする
（Issue 化の要否は人間判断。完了報告で提案する）。

| 箇所 | 内容 | 本 Issue での扱いと理由 |
|------|------|------------------------|
| `kaji_harness/cli.py:331,337` headless 経路の `CLIExecutionError` | `detail = result.stderr or "\n".join(result.error_messages[-3:])` を `CLIExecutionError(step.id, rc, detail)` に渡し、`errors.py:150` で `[:200]` に切られる。同じ欠陥（原因が末尾にある stderr の先頭だけが残る）を持つ | **変更しない**。メッセージは `recovery/classify.py:101` の `is_transient_error_text(snapshot.attempt_error)`（run 単位の自動回復判定）に直接入る。抜粋を変えると先頭 200 文字にあった transient 語が消えたり末尾の語が入ったりして回復の可否が変わりうる。実障害の一次情報も無い。attempt 内 retry（`cli.py:127` `_is_transient`）は `error.stderr` 全文を見るので影響を受けない |
| `kaji_harness/verdict.py:555` AI formatter の `VerdictParseError` | `f"AI formatter CLI exited with code {result.returncode}: {result.stderr[:300]}"` | **変更しない**。例外型も fingerprint への経路（verdict 解析失敗）も別で、影響が小さい |
| Herdr の他の `CLIExecutionError`（`interactive_terminal_herdr.py:530-1148` の herdr コマンド失敗等） | 渡す文言は短い固定文 + herdr の応答で、`[:200]` で原因が欠ける既知の実例は無い | **変更しない**（`summary` を渡さないので挙動は不変） |

参考（下流の先頭切り詰め。本設計で「原因行をメッセージの前寄りに置く」理由）:

- `kaji_harness/runner.py:756` — ABORT verdict の `evidence=str(exc)[:500]`
- `kaji_harness/recovery/report.py:20,96-102` — incident 根拠欄の `sanitize_evidence`（`EVIDENCE_LIMIT = 500`）
- `kaji_harness/recovery/signature.py:29,141` — 正規化後の fingerprint を先頭 `FINGERPRINT_LIMIT = 2000` 文字に切る

## インターフェース

### 入力

| 対象 | 変更前 | 変更後 |
|------|--------|--------|
| `CLIExecutionError.__init__` | `(step_id, returncode, stderr, *, session_resolution=None)` | `(step_id, returncode, stderr, *, session_resolution=None, summary: str \| None = None)` |
| `TerminalDiagnostic`（`interactive_terminal_common`） | `kind / matched_pattern / clean_excerpt / clean_tail / sensitive_marker` | `cause_summary: str \| None = None` を追加（`kind == "no_pattern"` のときだけ非 `None`） |
| Herdr 早期終了の送出（`interactive_terminal_herdr.py:351`） | `CLIExecutionError(step.id, 1, _recorded_exit_detail(...), session_resolution=...)` | `no_pattern` のときだけ `summary=<detail>` を渡す。他の kind では渡さない |

`summary` は keyword-only・既定 `None`。`None` のときの挙動（`stderr[:200]`）は現行と同一で、headless 経路と
Herdr の他の送出箇所はこれを使い続ける。`summary` を必須にしない理由は § 重要判断 provenance（修正範囲の人間決定）。

### 出力

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 型名 `CLIExecutionError`・属性 `step_id` / `returncode` / `stderr` / `session_resolution` | — | 変更しない（型名は `classify.py` が読む artifact 契約） |
| `str(exc)`（`summary` なし） | `Step '<id>' CLI exited with code <rc>: <stderr[:200]>` | 同じ |
| `str(exc)`（`summary` あり） | — | `Step '<id>' CLI exited with code <rc>: <summary>`（切らない） |
| Herdr `no_pattern` の detail（= `stderr` 属性 = `summary`） | `<prefix>; no known provider error pattern in transcript; log tail:\n<clean_tail>` | `<prefix>; no known provider error pattern in transcript; <cause_summary>` + 既存の recorder 付記（`; transcript may be incomplete (recorder state: <state>)`、該当時のみ） |
| `provider_error` / `no_log` / `empty` の detail とメッセージ | — | 変更しない |
| `pane-metadata.json` の `terminal_diagnostic` | — | 変更しない（`clean_tail` は従来どおり記録。`cause_summary` は書かない） |

`cause_summary` の規則（`interactive_terminal_common.py` の private helper として実装。`extract_terminal_diagnostic`
が `no_pattern` のときに ANSI/制御除去済みの `clean` から算出する）:

1. **対象窓**: `clean` の末尾 `_TERMINAL_LOG_TAIL_CHARS`（2000）文字（= `clean_tail` と同じ窓）を
   `str.splitlines()` で行に分ける。窓が行の途中から始まる（`clean` が窓より長く、窓の直前の文字が改行でない）
   場合、先頭の断片行は候補から除く。
2. **banner の除外**: 窓内に wrapper banner の終端行（`The agent must read prompt_path and write a pure YAML verdict to verdict_path.`。
   module 定数として持つ）があれば、その最後の出現行までを候補から除く。
3. **失敗原因（優先）**: 候補を後ろから見て、エラー行パターン `_ERROR_LINE_RE` に一致する最初の行を原因行とし、
   ラベルを `last error line` とする。要約の元になる行の並び `Ls` は原則この 1 行（`Ls = [原因行]`）。パターン（行頭の空白は許容、行頭 anchor）:
   - `(<識別子>)?Error|Exception` の直後に（`[...]` を挟んでもよい）`:`（例: `Error:` / `TypeError:` /
     `OSError: [Errno 30]` / `ModuleNotFoundError:` / `java.io.IOException:`）
   - 大小文字無視の `error` / `fatal` / `fatal error` / `panic` の直後に（`[...]` を挟んでもよい）`:`
     （例: Rust/clap の `error: ...`、`error[E0433]:`、git の `fatal:`、Go の `panic:`、Node heap OOM の `FATAL ERROR:`）
   - `thread '<name>' panicked at ` および thread ID 付きの `thread '<name>' (<数字>) panicked at `
     （Rust の panic。codex は Rust native binary。thread ID 付きは Rust 公式 Book の現行例の形式）
   - **Rust panic の本文の連結（review-design R1）**: 原因行が panic 見出しの場合、現行の Rust（1.73 以降）は原因本文を
     見出しの**次行以降**に出す（`thread 'main' panicked at src/main.rs:2:5:` の次行が `failed to open config`）。
     このため見出しの直後から、`note: ` / `stack backtrace:` で始まる行、正規化で空になる行（空行等）、候補の終わり、
     のいずれかに達するまでの行を本文として `Ls` に加える（`Ls = [見出し, 本文行...]`）。旧形式
     （`thread 'main' panicked at 'msg', src/main.rs:2:5` の 1 行）は次行が `note:` なので見出し 1 行のまま。
4. **最終出力行（fallback）**: 3 に一致する行が無ければ、候補を後ろから見て `normalize_error_text(行)` が空でない
   最初の行 1 行を `Ls` とし、ラベルを `last output line` とする（#516 の規則と同じ）。
5. 候補が尽きたら `cause_summary = "no agent output line in transcript tail"`。
6. **要約前の実行固有値の除去（review-design R2）**: `Ls` の各行に、正規化の前に次の 2 つの置換だけを行う（`P_f`）。
   どちらも Herdr `no_pattern` の要約生成の中だけで行い、`signature.py` の正規化は変えない。
   - **file URI の path**: `file://` に続く絶対 path（`file://(?:/[\w.\-@+%~]+)+/?`）を `file://<PATH>` に置換する。
     `normalize_error_text` の `_ABS_PATH_RE` は lookbehind `(?<![\w/])` のため `file:///home/...` の path を置換しない
     （`signature.py:61`）。置換しないと、同じ原因でも worktree・一時ディレクトリ・home・install path の違いで hash が
     分かれる（EB 4 違反）。path 直後の `:108:9` のような行・列番号は残す（原因箇所の識別に使う値）。
   - **Rust panic の thread ID**: 見出しの `thread '<name>' (<数字>) panicked at` を `thread '<name>' panicked at` にする。
     thread ID は実行ごとの値で、3 桁以下だと `_LONG_NUM_RE`（4 桁以上）で置換されない。
7. 要約 `S = normalize_error_text(" | ".join(P_f(行) for 行 in Ls))`（#516 と同じく常に 1 回正規化。path・timestamp・
   run_id 等を placeholder 化、redaction、空白正規化、`FINGERPRINT_LIMIT` で有界）。`cause_summary = f"{ラベル}: {S}"`。
   行の連結に空白ではなく ` | ` を使うのは、行をまたいで transient pattern（例: `try` / `again` の 2 行）が成立しない
   ようにするため（pattern に `|` は含まれない）。
8. **transient 不変の guard**: `find_transient_pattern(S)` が一致した場合に限り、`S` の代わりに
   `G = " | ".join(P_f(行) for 行 in Ls)`（**規則 6 の `P_f` は適用済み**・`normalize_error_text` は通さない。窓内の行なので
   有界）を使う。guard で省くのは空白正規化を含む `normalize_error_text` だけで、`P_f` は省かない
   （review-code cycle 1 の指摘 R1: raw の `Ls` に戻すと file URI の path と 3 桁以下の thread ID が復活し、後段の
   `compute_signature` の正規化では除去できないため EB 4 が崩れる）。理由と証明は § 制約・前提条件。

### 使用例

```python
# OB の transcript の場合
str(exc)
# "Step 'review-ready' CLI exited with code 1: Herdr pane returned to its shell before writing "
# "verdict.yaml; no known provider error pattern in transcript; last error line: Error: Missing "
# "optional dependency @openai/codex-linux-x64. Reinstall Codex: npm install -g @openai/codex@latest"

# banner の後に `Killed` だけが出た場合
# "...; no known provider error pattern in transcript; last output line: Killed"

# Python traceback で終わる場合
# "...; last error line: ModuleNotFoundError: No module named 'foo'"

# Rust panic（現行形式。本文は次行）の場合
# "...; last error line: thread 'main' panicked at src/main.rs:2:5: | failed to open config"

# file URI を含む原因行の場合（path は実行ごとの値として除く）
# "...; last error line: Error: cannot load module file://<PATH>"

# 原因行の空白正規化で transient pattern が成立する場合（guard。P_f は適用、正規化だけ省く。\t は実際の TAB）
# "...; last error line: Error: cannot load module file://<PATH>; please try\tagain"

# agent が何も出さずに終わった場合
# "...; no known provider error pattern in transcript; no agent output line in transcript tail"

# headless 経路（summary なし）は従来どおり
CLIExecutionError("implement", 1, long_stderr)  # str(exc) は long_stderr[:200]
```

## 制約・前提条件

- **修正範囲は Herdr の `no_pattern` 経路だけ**（`## 決定事項` 人間決定）。`provider_error` / `no_log` / `empty`
  分岐の detail、recorder state の付記文言、headless 経路、AI formatter は変えない。
- **transient 判定（`classify.py:101`）の結果を変えない。** 証明:
  - 変更が及ぶのは `no_pattern` 経路だけで、`no_pattern` は `find_transient_pattern(clean)` が `None`（transcript 全文に
    transient pattern が無い）のときにだけ選ばれる（`interactive_terminal_common.py:87-91`）。
  - `_TRANSIENT_PATTERNS`（`cli.py:36-43`）は英字・空白・バッククォート・`_` だけからなる literal で、`:` も `;` も含まない。
    メッセージ中の要約の前は固定文 `...; last error line: ` / `last output line: ` で `: ` で終わり、後ろは `; transcript ...`
    または文字列末尾なので、要約と固定文をまたぐ一致は起きない。固定文自体（prefix・ラベル・`no known provider error
    pattern in transcript`）も pattern を含まない（現行メッセージにも同じ固定文があり非 transient）。
  - 複数行（Rust panic の本文）は ` | ` で連結するので、行境界をまたぐ一致は起きない。`P_f` は `file://<PATH>` と
    thread ID の除去で、`<...>` の挿入と数字・括弧の削除だけなので pattern を作れない（pattern は英字・空白・バッククォート・`_` のみ）。
    thread ID 除去は `' (123) panicked` → `' panicked` で、英字同士を新たに隣接させない。
  - `S` が一致を新たに作りうる段は空白正規化（`\s+` → 1 空白。例: `try\tagain` /
    `try  again` / NBSP → `try again`）だけである（redaction と placeholder 置換は `<...>` / `***` / `Bearer ***` を挿入するだけで
    pattern を作れない。traceback フレーム行除去は行全体を消す。ANSI は `_strip_ansi` で除去済み）。この場合は guard で
    `G = " | ".join(P_f(行) for 行 in Ls)` を使う。各行は `clean` の部分文字列（transient pattern を含まない）で、上記のとおり
    `P_f` は pattern を作れず、区切り ` | ` をまたぐ一致も無いので `find_transient_pattern(G)` は `None`。
  - したがって `is_transient_error_text(attempt_error)` は修正前後とも `False`。safety gate（`handler.py:151`）は
    candidate の場合にしか評価されない（`handler.py:278` が先に `not candidate` を返す）ので、要約に sensitive 語が
    入っても回復判定は変わらない。
- **layer 規約（ADR 009）**: `errors.py`（foundation）は kaji_harness 内部を import しない（現状維持。渡された
  `summary` を入れるだけ）。正規化は application 層の `interactive_terminal_common.py` から `kaji_harness.recovery`
  facade の `normalize_error_text`（`__all__` 済み）を module 先頭で import する。`tests/test_layer_imports.py:35,43`
  で両者とも `application` なので許可される。循環 import は無い（試作: `import kaji_harness.recovery` 後の
  `sys.modules` に `interactive_terminal*` / `runner` が無いことを確認）。`find_transient_pattern` は既に
  `.cli` から import 済み。
- **原因行はメッセージの前寄りに置く。** 要約の前は prefix（61 文字）+ 固定文で約 125 文字。incident 根拠欄
  （`steps/<step>/result.json error=CLIExecutionError: Step '<id>' CLI exited with code 1: ` の後）でも 500 文字の
  窓に OB の原因行（111 文字）全体が入る。
- **要約は常に `N(L)`（長さで raw / 正規化を切り替えない。唯一の例外である transient guard の分岐は次項）。** #516 の design review で示されたとおり、raw を文字数で
  切ると可変値の長さで hash が分裂し、長さで表現を切り替えると正規化の文脈依存・非冪等性で分裂する。`N(L)` が等しい
  （= 同じ原因）なら fingerprint 算出への入力文字列 `P + N(L)` 自体が同一なので、hash は決定性だけで一致する。
- **transient guard の分岐でも EB 4 を保つ。** 証明:
  - guard を使うかどうかは `S`（EB 4 の差では変わらない値）だけで決まるので、同じ原因の 2 つの失敗は必ず同じ分岐に入る。
  - guard 分岐のメッセージは `P + G`。fingerprint は `compute_signature` が `normalize_error_text(P + G)` から作る。
    実行ごとの値のうち `normalize_error_text` が除去できるもの（`<PATH>` 化される絶対 path・run_id・UUID・timestamp・
    4 桁以上の数値等）はこの段で除去され、除去できないもの（file URI の path・3 桁以下の thread ID）は `G` の時点で `P_f`
    が除去済み。したがって同じ原因なら `normalize_error_text(P + G)` が一致し hash も一致する。raw の文字数切り詰めは
    しないので（`G` は窓内の行で有界、切り詰めは正規化後の `FINGERPRINT_LIMIT` だけ）、長さの違いによる分裂も起きない。
  - 不変条件として、`P_f` は「`normalize_error_text` が除去できない実行固有値を補う前処理」であり、要約のどの分岐でも
    省略しない。
- **抜粋は行単位・末尾窓内で決める。** 末尾 N 文字の生の窓をそのまま使うと、窓の開始位置が path 長などでずれて
  EB 4 を壊す。行単位なら path 長は行の中だけの差になり正規化で消える。窓を `clean_tail` と同じ末尾 2000 文字に
  限るのは、長い session の途中で agent が表示した古い `error:` 行を最後の原因と誤認しにくくするためと、raw の `L`
  を有界にするため。
- **banner は候補から外す。** banner には `model:` / `effort:` のように正規化で消えない実行ごとの値がある（EB 4）。
  また agent が何も出さずに終わったとき、banner 終端行を「原因」と表示しない。banner 終端行の文言は wrapper.sh と
  定数の二重管理になるため、wrapper.sh がその行を出力することを Small テストで固定する（契約テスト）。
- **最終行方式（#516）をそのまま使わない。** Node の uncaught exception は常に `Node.js vX` で終わり、Rust の panic は
  `note: run with RUST_BACKTRACE=1 ...` で終わる。最終行だと agent（codex / claude は Node / Rust）のクラッシュが
  原因によらず同じ hash になる（`## 決定事項` AI の仮定 1 行目の根拠）。
- `Last <N> chars:` の語は使わない（`signature.py` の `_TAIL_RE` がそれ以降を `<TAIL>` に潰すため）。
- `SIGNATURE_SCHEMA_VERSION` と `signature.py` は変更しない（`## 決定事項` 人間決定。算出方法は不変で入力だけが変わる）。
- 外部依存の追加なし。

### 採用しなかった案

| 案 | 判定 | 理由 |
|----|------|------|
| `CLIExecutionError` の `[:200]` を全経路で末尾寄り・長めに変える | 不採用 | headless 経路の回復判定が変わる。修正範囲の人間決定に反する |
| `summary` を必須引数にする（#516 と同形） | 不採用 | 送出箇所が headless・Herdr で 30 以上あり、決定済みの修正範囲を超えて全送出箇所を変更することになる。`None` は「現行の挙動」を意味するだけで、互換レイヤ（旧形式の読み替え）ではない |
| detail を短くして `[:200]` に収める | 不採用 | 前置きで 119 文字使うため要約に残るのは約 80 文字。OB の原因行（111 文字）でも欠け、80 文字より後ろだけが違う原因が同じ hash になる（EB 2） |
| 最終の空でない行（#516 方式） | 不採用 | 上記。`Node.js vX` / RUST_BACKTRACE の note が原因行になる |
| banner 後の末尾 K 行を連結 | 不採用 | K の根拠が無く、Node の stack frame（`file:///` path は `_ABS_PATH_RE` の lookbehind で正規化されない）や実行ごとの値を含みやすく EB 4 を壊しやすい |

### 既知の限界

- エラー行パターンに一致しない形で原因を出して終わる agent（例: OOM kill で何も出さずに終わる、独自書式の
  エラー）では、最終出力行（または `no agent output line in transcript tail`）が要約になる。長い TUI session の末尾行は
  実行ごとに変わりうるので、その場合 hash は実行ごとに分かれうる。ただし現行も、端末ログが 2000 文字を超えると
  `clean_tail` の先頭（= メッセージに残る約 80 文字）が TUI 出力の途中になり実行ごとに変わっているので、退行ではない。
- 窓内に agent が通常作業中に表示した `error:` 形式の行が残っていて、その後の終了原因がパターンに一致しない場合、
  その行が `last error line` として表示される。全文は `terminal.log` / `pane-metadata.json` で読む。
- `file://` 以外の URI scheme（`http://host/...` 等）に含まれる path、`P_f` の文字クラスに無い文字（空白等）を含む
  file URI の path は除去しない。原因行に実行ごとの値として現れる既知の実例は無い。発生した場合は `P_f` の置換対象を
  足す（Herdr `no_pattern` の要約生成の中だけで閉じた変更）。
- Rust panic の本文は `note:` / `stack backtrace:` / 空行 / 候補の終わりで止める。本文が空行を含む複数段落の場合は
  最初の段落だけが要約に入る（全文は `terminal.log`）。
- transient guard の分岐（原因行の空白正規化で transient pattern が成立する場合のみ）では、メッセージ上の要約は
  `P_f` 適用済みだが正規化前の行になる。このため `result.json.error` 等の表示では絶対 path・timestamp 等が placeholder
  にならず実値のまま、空白も TAB 等のまま残る（fingerprint は後段で正規化されるので EB 4 は保たれる。§ 制約・前提条件）。
  credential の redaction も要約段では行わないが、incident 根拠欄は従来どおり `sanitize_evidence` で mask され、
  `stderr` 属性・`terminal.log` には従来から raw の transcript がある。
- メッセージ上の path・timestamp 等は `<PATH>` / `<TS>` 等の placeholder になる（実値は `terminal.log` で読む）。
- 修正後、Herdr `no_pattern` 経路の fingerprint は変わり、既存 incident（apokamo/kamo2#2018 等）とは一致せず新しい
  incident が 1 回起票される（`## 決定事項` で許容済み）。

## 方針

`kaji_harness/errors.py`・`kaji_harness/interactive_terminal_common.py`・`kaji_harness/interactive_terminal_herdr.py` を変更する。

```python
# kaji_harness/errors.py（foundation。kaji_harness 内部を import しない）
class CLIExecutionError(HarnessError):
    def __init__(self, step_id, returncode, stderr, *, session_resolution=None, summary: str | None = None):
        ...  # 既存属性は不変
        self.summary = summary
        detail = summary if summary is not None else stderr[:200]
        super().__init__(f"Step '{step_id}' CLI exited with code {returncode}: {detail}")


# kaji_harness/interactive_terminal_common.py（application）
from .recovery import normalize_error_text

_WRAPPER_BANNER_END = "The agent must read prompt_path and write a pure YAML verdict to verdict_path."
_ERROR_LINE_RE = re.compile(...)  # § インターフェース 規則 3

def _transcript_cause_summary(clean: str) -> str:
    window = clean[-_TERMINAL_LOG_TAIL_CHARS:]
    lines = window.splitlines()
    if len(clean) > len(window) and clean[-len(window) - 1] != "\n":
        lines = lines[1:]                         # 先頭の断片行を除く
    banner_end = <lines 中で strip() == _WRAPPER_BANNER_END の最後の index>
    candidates = lines[banner_end + 1:] if 見つかった else lines
    for label, idx in (後ろから _ERROR_LINE_RE 一致 → "last error line",
                       後ろから normalize_error_text(line) が空でない → "last output line"):
        ls = [candidates[idx]]
        if _RUST_PANIC_RE.match(candidates[idx]):      # R1: 本文は次行以降
            ls += <idx+1 から note:/stack backtrace:/正規化で空の行/終端 までの行>
        stripped = [_strip_run_specific(x) for x in ls]                 # R2: file URI path・thread ID
        summary = normalize_error_text(" | ".join(stripped))
        if find_transient_pattern(summary) is not None:
            summary = " | ".join(stripped)        # transient 不変の guard（P_f は維持し、正規化だけ省く）
        return f"{label}: {summary}"
    return "no agent output line in transcript tail"

# extract_terminal_diagnostic: no_pattern のとき cause_summary=_transcript_cause_summary(clean)
# _terminal_exit_detail: no_pattern 分岐を
#   f"{prefix}; no known provider error pattern in transcript; {diagnostic.cause_summary}"
# に変える（他分岐は不変）


# kaji_harness/interactive_terminal_herdr.py
# _recorded_exit_detail は 1 回読んだ diagnostic から detail（従来どおり recorder 付記込み）と kind を得られる形に
# 内部整理し、送出箇所は kind == "no_pattern" のときだけ summary=detail を渡す:
raise CLIExecutionError(step.id, 1, detail, session_resolution=resolved,
                        summary=detail if kind == "no_pattern" else None)
```

- 関数分割・戻り値の形（tuple / 小さな dataclass 等）は implement に委ねる。観測可能な契約は § インターフェース の表。
- `CLIExecutionError` の docstring に、`summary` は Herdr 早期終了（`no_pattern`）で原因行を含む要約を切らずに
  載せるためのもので、`None` の経路は従来どおり `stderr[:200]` であることを書く。
- `TerminalDiagnostic` の docstring に `cause_summary` の意味（`no_pattern` 専用、メッセージ用・正規化済み）を足す。
  `clean_excerpt` / `clean_tail` と違いメッセージに載るが、`no_pattern` では transient / sensitive の分類入力にならない
  ことも書く。
- 既存テスト `tests/test_interactive_terminal_common.py:580` は旧 `log tail` 形式のメッセージを手で組み立てているので、
  新形式（`_terminal_exit_detail` の戻り値）に合わせる。検証内容（非 transient）は変えない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 修正の範囲 | Herdr の `no_pattern` 経路（`interactive_terminal_herdr.py:351` → `_recorded_exit_detail` → `_terminal_exit_detail`）だけを直す。headless（`cli.py:331,337`）と AI formatter（`verdict.py:555`）は調査結果の記録のみ | 人間決定: Issue 本文 `## 決定事項` > 人間決定「修正の範囲」（grill-me 質問 1、2026-11-01、`## grill-me provenance` コメント） | `CLIExecutionError` に keyword-only `summary`（既定 `None` = 現行挙動）を追加し、Herdr `no_pattern` の送出だけが渡す。provider_error / no_log / empty と Herdr の他の送出は `summary` を渡さず不変。調査結果は § 同じパターンの他の箇所 |
| fingerprint の互換性 | 互換処理をせず、Herdr `no_pattern` の新 hash による 1 回の再起票を許容。`SIGNATURE_SCHEMA_VERSION` は上げない。既存 incident は操作しない | 人間決定: `## 決定事項` > 人間決定「fingerprint の互換性」（grill-me 質問 2）。既存方針 `signature.py:24-26`（#303 決定 E）、ADR 008 | `signature.py` を変更しない（入力だけが変わる）ことを方針に固定 |
| EB 4 / 再現テスト観点 (c) | 同じ原因で実行ごとの値だけが違う失敗は同じ hash | 人間決定: `## 決定事項` > 人間決定「EB・完了条件の追加」（grill-me 質問 3） | banner（`model:` 等の非正規化値を含む）を候補から除外し、原因行は `normalize_error_text` に 1 回通した `N(L)` を使う。Small テストで run_id・path 長・model・effort・timestamp 違いの hash 一致を検証 |
| 抜粋の作り方 | 末尾 2000 文字窓の完全な行から、banner 後のエラー行パターン一致の最後の行（無ければ最終出力行）を選び `N(L)` にする | AI の仮定（two-way door。`## 決定事項` > AI の仮定 1 行目が設計に委任）。根拠: OB の実 transcript は最終行が `Node.js v26.10.0` で原因行ではない（試作で最終行方式は Node / Rust のクラッシュを集約することを確認）、banner は `model:` 等の可変値を含む、#516 の review で raw 文字数の切り詰めは hash を分裂させると実証済み。試作: OB の transcript で要約に原因行全体が入り hash `c3d3edbaa3adc68c…`、`Killed` 版は `975c83f6c57ec8a6…`（別 hash）、run_id・model・workdir（長さ違い）を変えた版は同じ `c3d3edbaa3adc68c…`。後段の検査先: issue-review-design、Small テスト (a)(b)(c)。review-design cycle 1 の指摘 R1（Rust panic の本文が次行にあり見出しだけでは別原因が同 hash、thread ID 付き形式が非一致）・R2（file URI の path が正規化されず同原因が別 hash になり EB 4 を弱める）を受けて規則を修正。修正後の試作: panic 本文 `failed to open config` / `failed to connect socket` は `c99fe127f7e2d8f8…` / `ac5e3f981aee7eb4…`（別）、thread ID `(6018279)` / `(1234567)` は同じ `f64d941342fe877b…`、`file:///home/aki/a/module.js` / `file:///tmp/other/longer/module.js` は同じ `132f780eaf70a928…`、全ケース `is_transient_error_text=False` | エラー行パターンは Node / Python / Rust / Go / git / clap の代表形に限定し、Python の traceback フレーム（正規化で空）・Node の `at ...` 行は一致しない形にする。窓の先頭断片行を除外。banner 終端行は module 定数 + wrapper.sh との契約テスト。Rust panic は見出し + 次行以降の本文を ` | ` で連結。要約前に file URI の path と panic の thread ID を除去（`signature.py` は不変で、EB 4 を既知の限界で弱めない） |
| transient 判定の不変性 | 修正前後で `is_transient_error_text(attempt_error)` は `no_pattern` 経路で常に `False`。かつ guard の分岐でも EB 4（人間決定）を弱めない | AI の仮定（`## 決定事項` > AI の仮定 2 行目）。根拠と証明は § 制約・前提条件（`no_pattern` の前提、pattern と区切り文字、空白正規化だけが一致を作りうること、guard 分岐の EB 4 証明）。review-code cycle 1 の指摘 R1（guard が raw 行に戻すため file URI path / thread ID だけが違う同原因が別 hash `6822b335…` / `e85ef09c…`、`aaff4acb…` / `4f6917da…` になる）を受けて guard を修正。修正後の試作（実装 commit `9573555` の guard 1 行だけを差し替え、テスト module の `_summary` / `_hash` / `_attempt_error` で算出）: URI path 違い（`please try\tagain` 付き）は同じ `f8ebe5f7e0b7a5bf…`、thread ID `(123)` / `(456)` は同じ `8eb26a550e829847…`、原因文違いは別 `a29b1362f4e0cb40…`、絶対 path・timestamp 違いは同じ `7d1377d08f42e0fc…`、guard 非発動の URI 違いは従来どおり同じ `9ea2dbc077d20ca1…`、全ケース `is_transient_error_text=False`。後段の検査先: issue-review-design、既存 transient テスト + 空白境界テスト + guard × 実行固有値の組合せテスト | 空白正規化で pattern が成立する場合に限り、`P_f` 適用済み・正規化前の `G` を使う guard（`P_f` は全分岐で省略しない）。旧設計の「guard 分岐では hash が分かれうる」既知の限界は削除 |
| 他分岐・recorder 付記の不変 | provider_error / no_log / empty の detail とメッセージ、recorder state 付記の文言は変えない | AI の仮定（`## 決定事項` > AI の仮定 3 行目）。根拠: Issue 完了条件 3、#296 の safety gate 契約。後段の検査先: issue-review-code、既存テスト | `summary` を渡すのは `no_pattern` だけ。付記は従来どおり detail の末尾に付き、`no_pattern` では今回からメッセージにも残る（以前は 2000 文字の log tail の後ろにあり `[:200]` で必ず落ちていた。文言は不変） |
| docs 更新 | `docs/cli-guides/interactive-terminal-runner.md` の Troubleshooting 行を更新する | AI の仮定（`## 決定事項` > AI の仮定 4 行目が設計に委任）。根拠: 同 docs の行 `Herdr pane returned to its shell before writing verdict.yaml` が「Check the tail of `terminal.log` attached to the error」と、エラーに log tail が付く現行挙動を書いている。後段の検査先: i-dev-final-check | エラーには原因行の要約が付き、全文の末尾は `pane-metadata.json` の `terminal_diagnostic.clean_tail` / `terminal.log` で読む、と書き換える |

one-way door の未決はない。公開契約（`kaji` console entry point）・永続化形式（`result.json.error` は従来どおり文字列）・
型名は変えない。`CLIExecutionError` の引数追加は repo 内部の例外 API への後方互換な追加で、既存呼び出しは不変。
fingerprint の変化と再起票は人間決定で許容済み。

## テスト戦略

### 変更タイプ

実行時コード変更（Herdr 早期終了時の例外メッセージの組み立て。`result.json` / `run.log` / incident の内容と fingerprint に影響）。

### 実行時コード変更の場合

#### Small テスト

再現テスト（bug 固有ルール）。実装前 Red は § 再現手順 1 の実ログで代替する（Issue 完了条件 2 が明示的に許可）。
いずれも修正後に PASS すること。transcript は文字列（`extract_terminal_diagnostic` / `_transcript_cause_summary` を直接呼ぶ）
または `tmp_path` の `terminal.log`（`_terminal_exit_detail` / `_recorded_exit_detail` 経由。既存の同ファイルの
テストと同じく small）で与える。fingerprint は runner と同じ形の `attempt_error = f"CLIExecutionError: {exc}"`
で `FailureSnapshot` を作り `compute_signature` を呼ぶ。

- **(a) 原因行がメッセージに入る（完了条件 2a / EB 1）**: OB と同じ形（banner + Node の uncaught exception +
  stack frame + `Node.js v26.10.0`、合計 2000 文字未満）の transcript から作った `CLIExecutionError` の `str(exc)` に
  `Error: Missing optional dependency @openai/codex-linux-x64` が含まれ、banner の行（`kaji interactive terminal runner`）
  と `Node.js v` は含まれない。ラベルは `last error line`。
- **(b) 別原因は別 hash（完了条件 2b / EB 2）**: banner が同じで、banner 後だけが `Error: Missing ...` / `Killed` /
  `Error: other` と違う transcript の `fingerprint_hash` がすべて異なる。
- **(c) 同じ原因・実行ごとの値違いは同 hash（決定事項 (c) / EB 4）**: 原因行が同じで、banner の `prompt_path` /
  `verdict_path` の run_id、`workdir`（長さの違う path）、`model:`、`effort:`、原因行内の絶対 path（長さ違い）・
  ISO timestamp が違う 2 つの transcript の `fingerprint_hash` が一致する。窓より長い transcript（先頭に 2000 文字超の
  出力があり窓の開始位置が変わる）でも一致する。
- **エラー行パターンの代表形**: Python traceback（最終行 `ModuleNotFoundError: ...`、フレーム行は選ばれない）、
  Rust panic（見出し `thread 'main' panicked at ...:` と次行の本文が要約に入り、後続の `note: run with RUST_BACKTRACE=1` は
  入らない。thread ID 付き `thread 'main' (6018279) panicked at ...:` も一致する。旧 1 行形式は見出し 1 行）、
  `error: ...` / `error[E0433]: ...` / `fatal: ...` / `FATAL ERROR: ...` / `panic: ...`、Node の `at ...(...)` 行と
  `Node.js vX` 行は一致しない。複数一致時は最後の行。
- **Rust panic の本文で別原因を分離（review-design R1）**: 見出しが同じで次行の本文だけが `failed to open config` /
  `failed to connect socket` と違う 2 つの transcript の `fingerprint_hash` が異なる。本文の要約に原因文が含まれる。
  thread ID だけが違う（`(6018279)` / `(123)`）2 つの transcript は同じ hash。本文の連結は `note:` / `stack backtrace:` /
  空行で止まる。
- **file URI の path 違いで分裂しない（review-design R2 / EB 4）**: 原因行の `file:///home/aki/a/module.js` と
  `file:///tmp/other/longer/module.js`（path・長さ違い）だけが違う 2 つの transcript の `fingerprint_hash` が一致し、要約は
  `file://<PATH>` を含む。path 後の行・列番号（`:3:4`）が違う場合や、エラー文が違う場合は hash が異なる。
- **fallback と境界**: パターン一致が無い場合は最終出力行（`last output line: Killed`）。banner だけで終わる
  transcript は `no agent output line in transcript tail`。banner 終端行が無い transcript（wrapper の `cd` 失敗など banner
  前の出力）は全行が候補。窓が行の途中から始まる場合に先頭断片行を選ばない。末尾の空行・空白行は飛ばす。
  1 行が 10 万文字でも要約は有界（`FINGERPRINT_LIMIT` 以下）でメッセージに改行が入らない。
- **transient 判定の不変（完了条件 3）**: `no_pattern` の各 transcript（上記すべて）で、`is_transient_error_text(attempt_error)`
  が `False`。空白正規化の境界: `please try\tagain later` / `rate  limit` / `try again` を原因行に持つ transcript は
  `no_pattern` で、要約は `P_f` 適用済み・正規化前の行（guard の `G`）になり、`is_transient_error_text(attempt_error)` は `False`。区切り境界: 原因行が
  `again` で始まる・`try` で終わる場合も `False`。Rust panic の本文が `please try` / `again` の 2 行に分かれる場合も
  ` | ` 連結のため `False`。既存の `tests/test_interactive_terminal_common.py` の transient /
  sensitive テスト（provider_error 経路）が変更なしで通る。
- **guard 分岐でも実行固有値で分裂しない（review-code R1 / EB 4）**: guard が発動する（原因行に `please try\tagain` を含む）
  transcript で、(i) 原因行の file URI の path・長さだけが違う（`file:///home/aki/a/module.js` / `file:///tmp/other/longer/module.js`）
  2 つ、(ii) Rust panic 見出しの thread ID だけが違う（`(123)` / `(456)`、本文が `please try\tagain`）2 つ、(iii) 原因行の
  絶対 path・ISO timestamp だけが違う 2 つ、のそれぞれで `fingerprint_hash` が一致する。要約は (i) で `file://<PATH>` を含み
  `file:///` を含まない、(ii) で thread ID（`(123)`）を含まない。いずれも `is_transient_error_text(attempt_error)` は `False`。
  対照として、guard 発動下で原因文だけが違う 2 つ（`cannot load module` / `cannot open socket`）は hash が異なる。
- **他分岐の不変**: `provider_error`（sensitive marker 併記あり / なし）・`no_log`・`empty` の `_terminal_exit_detail` の
  戻り値と、Herdr 送出時の `str(exc)` が修正前と同じ（`summary` を渡さない）。recorder state 付記は `no_pattern` の
  detail 末尾にも従来の文言で付き、メッセージにも残る。
- **`CLIExecutionError`**: `summary=None` では `str(exc)` が `stderr[:200]`（headless の従来挙動）。`summary` ありでは
  `str(exc)` が `summary` 全体を末尾に持ち、`stderr` / `step_id` / `returncode` / `session_resolution` 属性は不変。
- **banner 契約**: `kaji_harness/assets/interactive-terminal/wrapper.sh` が `_WRAPPER_BANNER_END` と同じ文言を
  `echo` する行を含む。
- **layer 規約の回帰**: 既存の `tests/test_layer_imports.py` / `tests/test_private_imports.py` が違反 0 件のまま通る
  （`make check` で実行。新規テストは追加しない）。
- **incident 根拠欄**: (a) の `attempt_error` を `sanitize_evidence(f"steps/review-ready/result.json error={attempt_error}")`
  に通しても `Missing optional dependency` が残る。

配置: 抜粋・メッセージ・fingerprint の観点は `tests/test_interactive_terminal_common.py`、`CLIExecutionError` の組み立て（`summary` 有無）は
専用の既存テスト module が無いため新規 `tests/test_errors.py` に置く、Herdr 送出箇所で `no_pattern` のときだけ `summary` が渡ることは
`tests/test_interactive_terminal_herdr.py` の既存の早期終了テスト（`execute_interactive_terminal_herdr` を patch で駆動、
`match="returned to its shell"`）に `no_pattern` の transcript を与えるケースを追加する。いずれも `@pytest.mark.small`
（既存の同種テストと同じ扱い）。

#### Medium テスト

runner を通した記録（完了条件「`result.json.error` / `run.log` に原因行」）を確認する。

- `WorkflowRunner` で Herdr 経路の dispatch を `execute_interactive_terminal_herdr` 相当の patch で
  `CLIExecutionError(..., summary=<no_pattern detail>)` を送出させ（既存 `tests/test_verdict_artifact_runner.py:806-826`
  と同じ方式）、attempt の `result.json` の `error` と `run.log` の `workflow_end` の error に原因行
  （`Missing optional dependency`）が含まれ、200 文字で切れていないことを確認する。runner 自体は変更しないが、
  `str(exc)` がそのまま永続化される配線を固定する。

#### Large テスト

追加しない。理由: 変更は例外メッセージの組み立てだけで、実 Herdr・実 agent CLI・ネットワークは関わらない。
実 Herdr / 実 agent を起動する acceptance は壊れた codex の install を再現する必要があり、決定論的に再現できない
（`docs/dev/testing-convention.md` の基準: 外部依存の挙動ではなく kaji 内部の文字列組み立てが検証対象で、
Small / Medium で入力から永続化まで固定できる）。実世界の挙動は OB の実ログ（apokamo/kamo2#2018）で代替する。

### 長時間 acceptance の配置

該当なし。nested workflow・外部 agent を含む dogfood・繰り返しの acceptance は無い。検証は Small / Medium の pytest と
`make check` だけで、implement step の timeout の 25% に達しない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。ADR 008（互換レイヤなし）・ADR 009（layer 規約）に従うだけ |
| docs/ARCHITECTURE.md | なし | 層の割り当て・module 構成は不変（application → application の import が 1 本増えるだけ） |
| docs/dev/ | なし | `CLIExecutionError` のメッセージ形式を記述した箇所なし |
| docs/reference/ | なし | 規約変更なし |
| docs/cli-guides/interactive-terminal-runner.md | **あり** | Troubleshooting の `Herdr pane returned to its shell before writing verdict.yaml` 行が「エラーに付いた `terminal.log` の tail を見る」と現行挙動を書いている。エラーには原因行の要約（`last error line:` / `last output line:`）が付き、末尾全体は `pane-metadata.json` の `terminal_diagnostic.clean_tail` / `terminal.log` で読む、に更新する |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（本 PR では編集しない） | `/release` で生成。例外メッセージは公開契約ではない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 失敗 attempt の artifact | `/home/aki/dev/kamo2/.kaji/artifacts/2017/runs/261031204817/steps/review-ready/attempt-001/{result.json,pane-metadata.json,terminal.log}` | `terminal_diagnostic.kind = no_pattern`、`clean_tail` 1126 文字で原因行 `Error: Missing optional dependency @openai/codex-linux-x64 ...` を含み最終行は `Node.js v26.10.0`。`result.json.error` は banner の `prompt` で切れる |
| incident | https://github.com/apokamo/kamo2/issues/2018 | 本文・根拠欄・fingerprint（`67a9b260bc3dd2b4…`）が上記 `error` から作られ原因行を含まない |
| Issue #527 本文・`## 決定事項`・grill-me provenance・readiness review | https://github.com/apokamo/kaji/issues/527 | OB / EB 1–4 / 再現手順 / 修正範囲・互換性・観点 (c) の人間決定 / AI の仮定と検査先 |
| `CLIExecutionError` | `kaji_harness/errors.py:129-150` | `f"Step '{step_id}' CLI exited with code {returncode}: {stderr[:200]}"` |
| 早期終了の診断 | `kaji_harness/interactive_terminal_common.py:30,74-101,114-142` | `_TERMINAL_LOG_TAIL_CHARS = 2000`。`find_transient_pattern(clean)` が無ければ `no_pattern`。`no_pattern` の detail は `prefix; no known provider error pattern in transcript; log tail:\n<clean_tail>` |
| Herdr 送出箇所 | `kaji_harness/interactive_terminal_herdr.py:319-359,1197-1203,1268-1274` | `confirmed_shell_only` 3 回で `CLIExecutionError(step.id, 1, _recorded_exit_detail(...))`。recorder state 付記。`pane-metadata.json` に `terminal_diagnostic`（`clean_tail` 含む）を記録 |
| wrapper banner | `kaji_harness/assets/interactive-terminal/wrapper.sh:20-41` | agent 起動前に banner（`prompt_path` / `verdict_path` / `model` / `effort` 等）と終端行 `The agent must read prompt_path and write a pure YAML verdict to verdict_path.` を echo |
| transient pattern | `kaji_harness/cli.py:36-64,111-129` | `_TRANSIENT_PATTERNS` は小文字化した部分文字列一致。`_is_transient` は `error.stderr` 全文（headless の attempt 内 retry） |
| 回復判定 | `kaji_harness/recovery/classify.py:100-108`、`kaji_harness/recovery/handler.py:136-159,278,310` | `CLIExecutionError` は `is_transient_error_text(attempt_error)` で candidate 判定。safety gate は candidate の後にだけ評価 |
| Rust の panic 出力形式 | https://doc.rust-lang.org/book/ch09-01-unrecoverable-errors-with-panic.html | panic の出力例は見出し `thread 'main' panicked at src/main.rs:2:5:` の次行に原因本文（`crash and burn`）、その後に `note: run with RUST_BACKTRACE=1 ...`。thread ID を含む見出し形式も掲載（review-design R1 の根拠） |
| 署名の算出 | `kaji_harness/recovery/signature.py:24-29,61,110-176` | `attempt_error` を `normalize_error_text`（redaction → ANSI / frame 除去 → `_TAIL_RE` → 置換 → 空白正規化 → 2000 文字）して sha256。`_ABS_PATH_RE` の lookbehind で `file:///` path は置換されない。schema version に migration なし（#303 決定 E） |
| redaction | `kaji_harness/recovery/report.py:25-33,81-102` | `_CREDENTIAL_RULES` は伏字の挿入のみ。`sanitize_evidence` は mask → 先頭 500 文字 |
| result.json.error | `kaji_harness/runner.py:756,774` | `error=f"{type(exc).__name__}: {exc}"`、ABORT evidence は `str(exc)[:500]` |
| layer fitness test | `tests/test_layer_imports.py:19-47` | `errors`=foundation、`interactive_terminal_common` / `recovery`=application |
| 前例 #516 | `draft/design/issue-516-fix-errors-scriptexecutionerror-stderr-2.md`、`kaji_harness/script_exec.py:55-76` | 要約を `normalize_error_text(原因行)` にし長さで表現を切り替えない理由（review での反例）、`CLIExecutionError` を対象外とした理由 |
| 後方互換方針 | `docs/adr/008-no-backward-compat-layer.md` | 互換レイヤを書かない（fingerprint の旧 hash を引き継がない根拠） |
| モジュール境界 | `docs/adr/009-module-boundary-private-import.md` | foundation は kaji_harness 内部依存ゼロ。public 名の import は可 |
| テストサイズ | `docs/dev/testing-convention.md` | Small / Medium / Large の定義と省略理由の基準 |
