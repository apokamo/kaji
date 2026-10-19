# [設計] ScriptExecutionError のメッセージに stderr 末尾の失敗原因行を残す

Issue: #516

## 概要

`ScriptExecutionError` が例外メッセージに stderr の**先頭 200 文字**を入れているため、
`exec_script` / `exec:` step の失敗原因（stderr 末尾の例外行）が `result.json.error`・`run.log`・
incident から失われる。また incident の fingerprint が原因と無関係な先頭部分から作られる。
メッセージに入れる部分を「stderr の最後の非空行（失敗原因行）」に替え、長さの上限は保つ。

## 背景・目的

### Observed Behavior（OB）

Incident #499（run `261018124412`、発生元 #489）の `baseline` step（`exec_script`:
`kaji_harness.scripts.baseline_precheck`）の失敗を一次情報として確認した。

- `.kaji-artifacts/489/runs/261018124412/steps/baseline/attempt-001/stderr.log`（410,463 bytes）の
  最後の行:

  ```text
  OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'
  ```

- 同じ attempt の `result.json` の `error`（全文。pytest の起動 banner で切れている）:

  ```text
  ScriptExecutionError: Step 'baseline' deterministic command 'kaji_harness.scripts.baseline_precheck' exited with code 1: ============================= test session starts ==============================\nplatform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /home/aki/dev/kaji/kaji-fix-489/.venv/bin/python\ncachedi
  ```

- incident #499 の本文と fingerprint も、この `error` から作られているため原因行を含まない。

readiness review（Issue コメント）で独立に再現済み: 共通の長い pytest 出力の末尾だけを
`OSError ...` / `RuntimeError ...` に変えた 2 つの stderr で、どちらもメッセージから原因行が欠け、
`compute_signature` の hash が `7fbc19f1…a65e` で一致した。

### Expected Behavior（EB）

Issue 本文「Expected Behavior」を正とする。要約:

1. `ScriptExecutionError` のメッセージ（`result.json.error` / `run.log` / incident 根拠欄に写るもの）に、
   失敗原因を示す stderr 末尾の情報が入る。#499 の例では `OSError: [Errno 30] Read-only file system` の行。
2. 失敗原因の行が異なる失敗は、異なる `fingerprint_hash` になる。
3. 同じ原因で、実行ごとの値（path・timestamp 等）だけが違う失敗は、従来どおり同じ hash になる。
4. メッセージ長は有界。全文は従来どおり `stderr.log` に残る。
5. `execute_script`（`exec_script`）と `execute_exec`（`exec:`）の両経路で同じ契約。

根拠: `kaji_harness/recovery/signature.py` の module docstring は、正規化で「occurrence 固有値」
だけを除き、原因の識別に使う値は残すことを設計目的にしている。先頭切り詰めはこの前提を壊す。

### 再現手順（Steps to Reproduce）

1. **実ログ**（実装前 Red 証跡の代替）: 上記 OB の run `261018124412` の `stderr.log` 末尾と
   `result.json.error` の差。
2. **合成再現（メッセージ）**: 200 文字を超える pytest 風の出力の後に
   `OSError: [Errno 30] Read-only file system: '/x'` で終わる stderr で
   `ScriptExecutionError("baseline", "kaji_harness.scripts.baseline_precheck", 1, stderr)` を作る。
   `str(exc)` に `Read-only file system` が含まれない（現行 main で観測）。
3. **合成再現（fingerprint）**: 2 と同じ長い先頭部分を持ち、最後の行だけが
   `OSError: ...` / `RuntimeError: ...` と異なる 2 つの stderr から、runner と同じ形
   （`attempt_error = f"ScriptExecutionError: {exc}"`）の `FailureSnapshot` を作り、
   `compute_signature` を呼ぶ。2 つの `fingerprint_hash` が一致する（現行 main で観測）。

## 根本原因（Root Cause）

### なぜ壊れているか

原因は 3 つの性質の組み合わせで、メッセージの抜粋方法（2）が直すべき箇所である。

1. **stderr の先頭は原因と無関係な定型文になりやすい。** `kaji_harness/scripts/baseline_precheck.py:115-118`
   は pytest の stdout と stderr をどちらも自分の stderr に書く。そのため stderr の先頭は必ず
   pytest の起動 banner になる。Python の traceback も、原因（最終例外行）は**最後**に出る。
2. **`ScriptExecutionError` が先頭を残す。** `kaji_harness/errors.py:185-188` は
   `f"...exited with code {returncode}: {stderr[:200]}"` でメッセージを作る。失敗原因が出る位置
   （末尾）と、残す位置（先頭）が逆になっている。
3. **fingerprint がこのメッセージから作られる。** runner は `result.json.error` を
   `f"{type(exc).__name__}: {exc}"` で書き（`kaji_harness/runner.py:774`）、
   `kaji_harness/recovery/snapshot.py:379` がそれを `attempt_error` として読み、
   `signature.py` の `_canonical_input` → `normalize_error_text` → sha256 で hash にする。
   正規化は絶対 path を `<PATH>` にするので、先頭の banner は Python / pytest の version が
   同じ限り常に同じ文字列になる。その結果、`baseline_precheck` の非ゼロ終了は原因によらず
   同じ fingerprint になる。

### いつから壊れているか

`stderr[:200]` は `9cb6f49`（`feat: add exec_script frontmatter ...`、Issue #204）で導入され、
`5806f33`（`feat: add exec step type ...`、Issue #205）で `exec:` 経路と共有されてから変わっていない。
incident の fingerprint 機構（Issue #304）は後から入り、このメッセージを canonical input に
したことで、表示の問題が「別原因の集約」に広がった。

### 同じパターンの他の箇所（調査結果）

`kaji_harness/` で、外部プロセスの stderr を**先頭から**切って例外メッセージに入れている箇所:

| 箇所 | 内容 | 本 Issue での扱い |
|------|------|------------------|
| `kaji_harness/errors.py:150` `CLIExecutionError` | `f"Step '{step_id}' CLI exited with code {returncode}: {stderr[:200]}"`。入力は `cli.py:331,337` の `result.stderr`（無ければ `error_messages[-3:]`）と interactive terminal（Herdr）の失敗文言 | **対象外**（下記） |
| `kaji_harness/verdict.py:555` AI formatter の `VerdictParseError` | `f"AI formatter CLI exited with code {result.returncode}: {result.stderr[:300]}"` | **対象外**（下記） |

対象外にした理由:

- Issue のスコープは `ScriptExecutionError` の抜粋方法であり、完了条件はこの 2 箇所について
  「調査結果を設計書に記載」までを求めている。
- `CLIExecutionError` は `classify.py:101` の `is_transient_error_text(snapshot.attempt_error)` で
  transient 判定（自動回復の可否）に使われている。抜粋方法を変えると回復判定が変わりうるため、
  同じ変更の中で扱うと影響が広がる。agent CLI の stderr の構造（最後の行が原因とは限らない）も
  別途確認が必要である。
- `VerdictParseError` は incident の fingerprint に入る経路が別（verdict 解析失敗）で、影響が小さい。

これらは本 Issue の PR 後、必要なら別 Issue として扱う（Issue 化の要否は人間判断。完了報告で提案する）。

参考: 先頭切り詰めではないが、下流にも**先頭から**切る処理がある。これが本設計の
「原因行をメッセージの先頭側に置く」制約の理由である（§ 制約・前提条件）。

- `kaji_harness/runner.py:756` — ABORT verdict の `evidence=str(exc)[:500]`
- `kaji_harness/recovery/report.py:20,89-102` — incident 根拠欄の `sanitize_evidence`（`EVIDENCE_LIMIT = 500` で先頭を残す）
- `kaji_harness/recovery/signature.py:29,151` — 正規化後の fingerprint を先頭 `FINGERPRINT_LIMIT = 2000` 文字に切る

## インターフェース

### 入力

`ScriptExecutionError(step_id: str, command_label: str, returncode: int, stderr: str)` — 変更しない。

### 出力

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 型名 `ScriptExecutionError` | — | 変更しない（artifact 上の互換契約） |
| 属性 `step_id` / `command_label` / `returncode` / `stderr` | — | 変更しない（`stderr` は全文のまま） |
| `str(exc)` | `Step '<id>' deterministic command '<label>' exited with code <rc>: <stderr の先頭 200 文字>` | `Step '<id>' deterministic command '<label>' exited with code <rc>: <stderr の最後の非空行（上限あり）>` |

メッセージ本文の抜粋規則（`errors.py` 内の private helper として実装）:

1. stderr を `str.splitlines()` で行に分け、各行の末尾空白を除く。`\r` だけで区切られた
   進捗表示も行として分かれる。
2. 空でない最後の行を「原因行」とする。
3. 空でない行が無い（空文字・空白のみ）場合は `(no stderr)` とする
   （`script_exec.py:186` が空 stderr に渡す既存の表記と同じ）。
4. 原因行が上限 `_STDERR_SUMMARY_LIMIT`（**8192 文字**）を超える場合だけ、先頭 8192 文字に `…` を付ける。
   上限は「メッセージを有界にする安全弁」であり、通常の原因行を切るための値ではない
   （切る位置が可変値の長さで動くと fingerprint が分裂するため。§ 制約・前提条件）。
5. メッセージは 1 行のまま（改行を含めない）。

### 使用例

```python
stderr = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.12.3, pytest-9.0.2 ...\n"
    "...（410KB の pytest 出力と traceback）...\n"
    "OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'\n"
)
exc = ScriptExecutionError("baseline", "kaji_harness.scripts.baseline_precheck", 1, stderr)
str(exc)
# "Step 'baseline' deterministic command 'kaji_harness.scripts.baseline_precheck' exited with "
# "code 1: OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'"
exc.stderr == stderr  # True（全文は属性と stderr.log に残る）
```

## 制約・前提条件

- **原因行はメッセージの先頭側に置く。** 下流の 3 箇所（runner の ABORT evidence 500 文字、
  incident 根拠欄 500 文字、fingerprint 2000 文字）はどれも先頭を残して切る。incident 根拠欄は
  `steps/<step>/result.json error=ScriptExecutionError: Step ... exited with code N: ` の接頭辞
  （#499 の例で約 150 文字）の後に原因行が続く。原因行が長い場合（長い path を含む等）は
  根拠欄と ABORT evidence で原因行の末尾が切れるが、例外の種類とメッセージの書き出しは先頭側に
  あるので残る。`result.json.error` には原因行の全体が残る。
- **原因行を正規化前の文字数で切らない（可変値の長さで署名を変えない）。** fingerprint は
  `normalize_error_text` が path・timestamp 等を `<PATH>` / `<TS>` に置換した**後**の text から作られ、
  その後に `FINGERPRINT_LIMIT = 2000` 文字で切られる。この切り詰めは正規化後なので、可変値の
  長さに依存しない。一方 `errors.py` が正規化**前**の原因行を短い上限で切ると、切る位置が
  path の長さで動き、同じ原因でも hash が分裂する（design review の指摘: path 20 文字と
  333 文字の同じ EROFS 行で、300 文字上限では hash が分裂した）。そのため `errors.py` の上限は
  通常の原因行に届かない大きさにし、原因行を丸ごとメッセージに入れる。上限 8192 は
  Linux の `PATH_MAX`（4096、終端 NUL 込み）の 2 倍で、最大長の path を 1 つ含み、かつ
  path 以外の部分が約 4096 文字以下の原因行なら切られない。この範囲では、原因行全体が
  正規化に渡るので、path 等の可変値の長さは hash に影響しない。
- **抜粋は行単位で決める（文字数の窓で決めない）。** 「末尾 N 文字」の窓は、worktree 名などの
  path の長さで窓の開始位置がずれ、同じ原因でも fingerprint が変わる。行単位なら path の長さは
  行の中だけの差になり、正規化（`<PATH>`）で消える。
- **原因行より前の行はメッセージに入れない。** 前の行には pytest の実行時間
  （例: `in 312.45s`）のような、正規化で消えない実行ごとの値が入りうる。入れると EB 3 を壊す。
  前の文脈（traceback のフレーム）は `stderr.log` で読む。
- **既存の正規化に頼る範囲を明確にする。** 原因行に入る実行ごとの値は、絶対 path・timestamp・
  run_id・UUID・長い hex/数値など、`normalize_error_text` が既に置換するものを前提にする。
  `signature.py` は変更しない。
- メッセージ文言に `Last <N> chars:` を含めない（`signature.py` の `_TAIL_RE` がそれ以降を
  `<TAIL>` に潰すため）。本設計の形式はこの語を使わない。
- 依存の追加なし。`errors.py` は標準ライブラリだけで実装する。

### 既知の限界

- 最後の非空行が原因を表さない stderr（失敗の後に無関係な警告を出して終わる command 等）では、
  原因行の代わりにその行が残る。現行の banner よりは原因に近く、全文は `stderr.log` に残る。
- 原因行が 8192 文字を超える場合（path 以外の部分が約 4096 文字を超える、または長い path を
  複数含む等）は、切る位置が可変値の長さで動き、同じ原因でも hash が変わりうる。メッセージを
  有界にするには、どこかで正規化前の text を切る必要があり、この性質を任意の長さで保証することは
  できない。上限を十分大きく取ることで、実際の例外行がこの範囲に入らないようにしている。
- 原因行が長いと `result.json.error` も最大で約 8.4k 文字になる。現行の 200 文字より大きいが、
  有界であり、410KB の全文は従来どおり `stderr.log` にある。
- 同じ最終例外行を別の呼び出し元から出した失敗は、同じ fingerprint になる。これは
  「同じ原因の再発」として扱う（incident 機構の集約目的に沿う）。

## 方針

`kaji_harness/errors.py` だけを変更する。

```python
_STDERR_SUMMARY_LIMIT = 8192  # 安全弁。通常の原因行は切らない

def _stderr_summary(stderr: str) -> str:
    """stderr の最後の非空行を、失敗原因行として上限付きで返す。"""
    for line in reversed(stderr.splitlines()):
        line = line.rstrip()
        if line:
            if len(line) > _STDERR_SUMMARY_LIMIT:
                return line[:_STDERR_SUMMARY_LIMIT] + "…"
            return line
    return "(no stderr)"

class ScriptExecutionError(HarnessError):
    def __init__(self, step_id, command_label, returncode, stderr):
        ...  # 属性は変更しない
        super().__init__(
            f"Step '{step_id}' deterministic command '{command_label}' exited with "
            f"code {returncode}: {_stderr_summary(stderr)}"
        )
```

- `ScriptExecutionError` の docstring に、メッセージには stderr の最後の非空行だけを入れること、
  全文は `stderr` 属性と `stderr.log` にあること、その理由（下流の先頭切り詰めと fingerprint）を書く。
- `script_exec.py` は変更しない。両経路とも同じ `_run_subprocess`（`script_exec.py:185-187`）から
  `ScriptExecutionError` を送出しているため、例外側の変更だけで EB 5 を満たす。
- `signature.py` / `SIGNATURE_SCHEMA_VERSION` は変更しない。署名の算出方法は変わらず、
  入力（メッセージ）だけが変わるため。
- 同じパターンの `CLIExecutionError` / AI formatter は変更しない（§ 根本原因「同じパターンの他の箇所」）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既存 incident の fingerprint 互換性 | 互換処理をしない。既存の `ScriptExecutionError` 系 incident（open は #499 のみ）は新しい hash と一致せず、同じ原因でも新規起票されうることを許容する | Issue 本文「重要判断」1 項目目（AI の仮定・two-way door と明記、review-design で再確認）。既存方針として `kaji_harness/recovery/signature.py` の `SIGNATURE_SCHEMA_VERSION` コメント（「migration 機構は作らない — #303 決定 E」、不一致は新規起票に倒す）と ADR 008（`docs/adr/008-no-backward-compat-layer.md` 決定 1: 互換レイヤを書かない）。readiness review コメントも同じ整理で PASS | `SIGNATURE_SCHEMA_VERSION` は上げない（算出方法は不変で入力だけが変わるため）。#499 の処遇（close 等）は既存どおり人間判断とし、本 Issue では操作しない |
| `ScriptExecutionError` の型名と属性 | 変えない。変えるのはメッセージ本文の抜粋方法だけ | Issue 本文「重要判断」2 項目目。`kaji_harness/errors.py` の既存 docstring（型名は artifact 互換の契約、#205 の共有契約） | `stderr` 属性は全文を保持し続けることを使用例とテストで固定 |
| stderr から残す部分の選び方 | 最後の非空行（上限 8192 文字、超過時は先頭 8192 文字 + `…`）。原因行より前の行は入れない | AI の仮定。根拠: (1) Python traceback と #499 の実ログで最終例外行が原因を表す、(2) 下流 3 箇所が先頭を残して切るので原因行を先頭側に置く必要がある、(3) 文字数の窓や前の行を入れると path 長・実行時間で hash が変わり EB 3 を壊す。Issue 完了条件 1 は選び方を設計で特定するよう求めている（候補: 末尾 N 文字 / 先頭＋末尾 / 最終例外行の抽出）。後段の検査先: review-design、Small テスト（同一原因で path 違いの hash 一致） | 候補のうち「末尾 N 文字」と「先頭＋末尾」は不採用（理由は § 制約・前提条件）。「最終例外行の抽出」は Python 以外の `exec:` command にも効くよう、例外行の正規表現ではなく「最後の非空行」に一般化 |
| メッセージ上限 | 原因行 8192 文字（安全弁）。通常の原因行は丸ごと入れる | AI の仮定（初版の 300 文字から design review の指摘で変更）。根拠: 正規化前に短く切ると path 長で hash が分裂し EB 3 を壊す。fingerprint 側の切り詰め（正規化後 2000 文字）は長さに依存しない。8192 = Linux `PATH_MAX` 4096 の 2 倍。後段の検査先: verify-design、Small テスト（短い path と長い path の同一 hash） | 定数 `_STDERR_SUMMARY_LIMIT` として `errors.py` に置く。incident 根拠欄（500 文字）で長い原因行の末尾が切れることは許容（全体は `result.json.error` に残る） |
| `CLIExecutionError` 等の同じパターン | 本 Issue では直さず、調査結果を記録する | Issue 完了条件 5（「調査結果が設計書に記載されている」）。直すかどうかは Issue 本文で決まっていない | transient 判定（`classify.py:101`）への影響を理由に分離。別 Issue 化は完了報告で提案（人間判断） |

one-way door の未決はない。fingerprint 互換性は Issue 本文で two-way door と明示され、
既存方針（#303 決定 E・ADR 008）と矛盾しない。既存記録の削除・書き換えはしない。

## テスト戦略

### 変更タイプ

実行時コード変更（例外メッセージの組み立て。`result.json` / `run.log` / incident の内容と fingerprint に影響）。

### 実行時コード変更の場合

#### Small テスト

再現テスト（bug 固有ルール）。実装前 Red は § 再現手順 1 の実ログで代替する（Issue 完了条件 2 が明示的に許可）。
いずれも修正後に PASS すること。

- **再現手順 2 に対応（メッセージ）**: 200 文字を超える pytest 風出力の後に
  `OSError: [Errno 30] Read-only file system: '/x'` で終わる stderr から作った例外の `str(exc)` に
  `Read-only file system` が含まれる。先頭の banner（`test session starts`）は含まれない。
- **再現手順 3 に対応（原因の分離）**: 長い共通の先頭部分を持ち、最後の行だけが `OSError ...` /
  `RuntimeError ...` と異なる 2 つの stderr から、runner と同じ形の `attempt_error`
  （`f"ScriptExecutionError: {exc}"`）で `FailureSnapshot` を作り `compute_signature` を呼ぶと、
  `fingerprint_hash` が異なる。
- **同じ原因の集約（完了条件 3）**: 原因行の中の絶対 path（`/home/a/kaji-fix-489/...` と
  `/home/b/kaji-fix-1234/...` のように長さも違う）と、先頭部分の timestamp・run_id・実行時間の行が
  異なり、最後の行の原因が同じ 2 つの stderr は、同じ `fingerprint_hash` になる。
- **長い path でも集約が分裂しない（design review の指摘に対応）**: 同じ EROFS 例外行で、path が
  20 文字程度のもの（原因行約 65 文字）と、300 文字を超えるもの（原因行約 380 文字）、さらに
  4000 文字程度のもの（原因行約 4050 文字。`PATH_MAX` 級）が、すべて同じ `fingerprint_hash` になる。
  初版の 300 文字上限ではこの観点が FAIL する。
- **incident 根拠欄**: 上記の `attempt_error` を `sanitize_evidence(f"steps/baseline/result.json error={attempt_error}")`
  に通しても `Read-only file system` が残る（下流の 500 文字切り詰めで原因行が落ちないこと）。
- **抜粋規則の境界**: 末尾の空行・空白行は飛ばす。`\r\n` 改行と `\r` 区切りの進捗表示で最後の
  区切りを原因行にする。空文字・空白のみは `(no stderr)`。8192 文字ちょうどの原因行は切られず、
  8193 文字以上は `8192 文字 + …` で切られる（メッセージ長が有界）。メッセージに改行が入らない。
- **属性の不変**: `stderr` 属性は全文と一致、`step_id` / `command_label` / `returncode` も不変。
  既存の `tests/test_exec_step_parser.py::TestScriptExecutionErrorGeneralization` は変更せずに通る
  （1 行 stderr はそのまま全体がメッセージに入る）。

配置: メッセージ系は `tests/test_exec_step_parser.py` の `TestScriptExecutionErrorGeneralization`
に追加、fingerprint 系は `tests/test_recovery_signature.py` に追加する（どちらも `@pytest.mark.small`）。

#### Medium テスト

完了条件 4（両経路で原因行が `result.json.error` に残る）を、runner を通して検証する。
実 subprocess（`sys.executable`）を使うのは既存の `tests/test_exec_step_dispatch.py::TestExecuteExecSubprocess`
（`@pytest.mark.medium`）と同じ扱い。

- **`exec:` 経路**: `Step(exec=[sys.executable, "-c", <script>])` を `WorkflowRunner` で実行する。
  script は 200 文字を超える出力を stderr に書いた後、最後に `OSError: [Errno 30] Read-only file system: '/x'`
  を書いて exit 1 する。`ScriptExecutionError` が送出され、`result.json` の `error` に
  `Read-only file system` が含まれ、`dispatch == "exec"`。
- **`exec_script` 経路**: 同じ stderr を出して exit 1 する fixture module
  （`tests/fixtures/scripts/` に追加）を `exec_script` に持つ skill の step を、runner で実行する
  （`load_skill_metadata` / `validate_skill_exists` は既存 runner テストと同じく patch）。
  `result.json` の `error` に原因行が含まれ、`dispatch == "exec_script"`。
- `run.log` の `workflow_end` の error にも原因行が含まれることを、どちらか一方のテストで確認する。

#### Large テスト

追加しない。理由: 変更は例外メッセージの組み立てだけで、外部 API・ネットワーク・実 agent CLI は
関わらない。実 subprocess を通した両経路の記録は Medium テストで確認する。既存の
`tests/test_exec_script_subprocess_large.py`（`large_local`）は例外の型だけを検証しており、
本変更で壊れない（回帰は既存テストの実行で確認する）。

### 長時間 acceptance の配置

該当なし。nested workflow・外部 agent・繰り返しの acceptance は無い。検証は Small / Medium の
pytest と `make check` だけで、implement step の timeout の 25% に達しない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。ADR 008 の方針に従うだけ |
| docs/ARCHITECTURE.md | なし | 構造の変更なし |
| docs/dev/ | なし | `docs/dev/skill-authoring.md:185` と `docs/dev/workflow-authoring.md:193` は「非ゼロ終了は `ScriptExecutionError`」と型だけを書いており、メッセージ形式に触れていない |
| docs/reference/ | なし | `docs/reference/python/python-style.md:39` の `stderr[:200]` は「長い文字列を括弧で連結する」書式の例で、切り詰め方の規約ではない |
| docs/cli-guides/ | なし | CLI の仕様変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（本 PR では編集しない） | CHANGELOG は `/release` で生成する。例外メッセージは公開契約ではないため BREAKING には当たらない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 失敗 attempt の stderr | `.kaji-artifacts/489/runs/261018124412/steps/baseline/attempt-001/stderr.log`（main checkout） | 410,463 bytes。最後の行が `OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'` |
| 失敗 attempt の result.json | `.kaji-artifacts/489/runs/261018124412/steps/baseline/attempt-001/result.json` | `error` が pytest banner の先頭 200 文字で終わり、原因行を含まない |
| `ScriptExecutionError` | `kaji_harness/errors.py:170-188` | `f"...exited with code {returncode}: {stderr[:200]}"`。docstring に #205 の両経路共有契約 |
| `CLIExecutionError` | `kaji_harness/errors.py:129-150` | 同じ `stderr[:200]` パターン（調査結果） |
| 共有送出経路 | `kaji_harness/script_exec.py:174-187` | stderr 全文を `stderr.log` に書き、非ゼロ終了で `ScriptExecutionError(step.id, command_label, returncode, stderr or "(no stderr)")` を送出 |
| baseline_precheck の出力 | `kaji_harness/scripts/baseline_precheck.py:115-118` | pytest の stdout と stderr を両方とも自分の stderr に書く |
| result.json.error の組み立て | `kaji_harness/runner.py:774`、`kaji_harness/runner.py:756` | `error=f"{type(exc).__name__}: {exc}"`。ABORT verdict の `evidence=str(exc)[:500]` |
| snapshot の attempt_error | `kaji_harness/recovery/snapshot.py:379,423` | `result.json` の `error` を `attempt_error` にし、`sanitize_evidence` で根拠欄に転記 |
| 根拠欄の切り詰め | `kaji_harness/recovery/report.py:20,89-102` | `EVIDENCE_LIMIT = 500`、mask → 先頭を残して truncate |
| 署名の算出 | `kaji_harness/recovery/signature.py` | `_canonical_input` は `attempt_error` 優先。正規化で path・timestamp 等を置換し、先頭 `FINGERPRINT_LIMIT = 2000` 文字を sha256。`_TAIL_RE` は `Last N chars:` 以降を `<TAIL>` に置換。`SIGNATURE_SCHEMA_VERSION` のコメントに「migration 機構は作らない — #303 決定 E」 |
| transient 判定 | `kaji_harness/recovery/classify.py:101` | `CLIExecutionError` の `attempt_error` を `is_transient_error_text` で判定（`CLIExecutionError` を対象外にした理由） |
| AI formatter のエラー | `kaji_harness/verdict.py:553-556` | `result.stderr[:300]`（同じパターン、調査結果） |
| 後方互換方針 | `docs/adr/008-no-backward-compat-layer.md` | 決定 1「後方互換レイヤを書かない」 |
| テストサイズ | `docs/dev/testing-convention.md`、`tests/test_exec_step_dispatch.py:65-66` | 実 `sys.executable` subprocess を使う runner/exec テストは既存で `medium` |
| Issue #516 本文・readiness review コメント | https://github.com/apokamo/kaji/issues/516 | OB / EB / 再現手順 / 重要判断（fingerprint 互換性を two-way door として許容）。readiness review で合成再現の hash 一致を独立確認 |
| Incident #499 | https://github.com/apokamo/kaji/issues/499 | 本 Issue の発生源。本文と fingerprint に pytest banner だけが残っている |
