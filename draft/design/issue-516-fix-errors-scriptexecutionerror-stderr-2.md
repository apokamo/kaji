# [設計] ScriptExecutionError のメッセージに stderr 末尾の失敗原因行を残す

Issue: #516

## 概要

`ScriptExecutionError` が例外メッセージに stderr の**先頭 200 文字**を入れているため、
`exec_script` / `exec:` step の失敗原因（stderr 末尾の例外行）が `result.json.error`・`run.log`・
incident から失われる。また incident の fingerprint が原因と無関係な先頭部分から作られる。
メッセージに入れる部分を「stderr の最後の非空行（失敗原因行）」に替え、長さの上限は保つ。
原因行の抜粋は application 層の `script_exec.py`（例外の唯一の送出元）で行い、
foundation 層の `errors.py` は渡された要約をメッセージに入れるだけにする。

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
- `kaji_harness/recovery/signature.py:29,151` — 正規化後の fingerprint を先頭 `FINGERPRINT_LIMIT = 2000` 文字に切る。
  接頭辞（`ScriptExecutionError: Step '...' deterministic command '<command_label>' ...`）が長い場合
  （長い argv の `exec:` step 等）は、原因行が正規化後 2000 文字の窓に入らない場合がある。これは
  既存機構の制限で、本変更では変えない（§ 既知の限界）。

## インターフェース

### 入力

| 対象 | 変更前 | 変更後 |
|------|--------|--------|
| `ScriptExecutionError.__init__` | `(step_id: str, command_label: str, returncode: int, stderr: str)` | `(step_id: str, command_label: str, returncode: int, stderr: str, *, summary: str)` |
| `script_exec._run_argv` の送出 | `ScriptExecutionError(step.id, command_label, rc, stderr or "(no stderr)")` | `ScriptExecutionError(step.id, command_label, rc, stderr or "(no stderr)", summary=_stderr_summary(stderr))` |

`summary` は keyword-only の**必須**引数とする（既定値による fallback を持たない。ADR 008 決定 1）。
送出元が要約を必ず指定するので、「どの経路から作った例外か」でメッセージ形式が分かれない。

### 出力

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 型名 `ScriptExecutionError` | — | 変更しない（artifact 上の互換契約） |
| 属性 `step_id` / `command_label` / `returncode` / `stderr` | — | 変更しない（`stderr` は全文のまま） |
| 属性 `summary` | なし | 追加（メッセージに入れた要約。テストと調査用） |
| `str(exc)` | `Step '<id>' deterministic command '<label>' exited with code <rc>: <stderr の先頭 200 文字>` | `Step '<id>' deterministic command '<label>' exited with code <rc>: <summary>` |

`summary` の抜粋規則（`kaji_harness/script_exec.py` の private helper `_stderr_summary` として実装）:

1. stderr を `str.splitlines()` で行に分け、各行の末尾空白を除く。`\r` だけで区切られた
   進捗表示も行として分かれる。
2. 空でない最後の行を「原因行」とする。
3. 空でない行が無い（空文字・空白のみ）場合は `(no stderr)` とする
   （`script_exec.py:186` が空 stderr に渡す既存の表記と同じ）。
4. 原因行が `_STDERR_SUMMARY_LIMIT`（**2000 文字** = `FINGERPRINT_LIMIT`）以下なら、そのまま（raw）入れる。
5. 原因行が上限を超える場合は、raw の文字数では切らず、**`normalize_error_text(原因行)` の結果**を入れる。
   `normalize_error_text` は path・timestamp 等を `<PATH>` / `<TS>` 等に置換した**後**に
   `FINGERPRINT_LIMIT` 文字で切るので、結果は 2000 文字以下になり、かつ可変値の長さに依存しない
   （正規化 → 切り詰めの順序。§ 制約・前提条件）。この場合のメッセージには `<PATH>` 等の placeholder が
   現れ、それ自体が「正規化済み」の印になる。raw の全文は `stderr` 属性と `stderr.log` にある。
6. 要約は 1 行（改行を含めない）。

### 使用例

```python
# kaji_harness/script_exec.py（両経路が共有する _run_argv の送出箇所）
if process.returncode != 0:
    raise ScriptExecutionError(
        step.id,
        command_label,
        process.returncode,
        stderr or "(no stderr)",
        summary=_stderr_summary(stderr),
    )

# 410KB の pytest 出力の最後が
# "OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'"
# の場合:
str(exc)
# "Step 'baseline' deterministic command 'kaji_harness.scripts.baseline_precheck' exited with "
# "code 1: OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'"
exc.stderr  # 全文（stderr.log と同じ）
exc.summary  # "OSError: [Errno 30] Read-only file system: '/home/aki/dev/kaji/kaji-fix-489/.kaji-artifacts'"

# テスト等で直接作る場合も summary を指定する
ScriptExecutionError("s1", "lbl", 3, "the stderr tail", summary="the stderr tail")
```

## 制約・前提条件

- **layer 規約（ADR 009）に従い、正規化に依存する処理を foundation 層に置かない。**
  `tests/test_layer_imports.py` の `MODULE_LAYERS` で `kaji_harness.errors` は `foundation`、
  `kaji_harness.recovery` と `kaji_harness.script_exec` は `application`。`classify_layer_source` は
  foundation module から `kaji_harness.*` への runtime import を（foundation 同士も含め）すべて違反とし、
  関数内 import も runtime edge として検査する（`test_function_local_import_remains_runtime_dependency`）。
  ADR 009 決定 1 と `docs/reference/python/python-style.md` § モジュール境界は「循環回避の関数内
  deferred import は依存の向きが誤っている兆候」とする。第 3 版設計（`errors.py` 内で
  `recovery.signature` を関数内 import）は、implement step で合成ソースを `classify_layer_source` に
  通して `LayerViolation(source='kaji_harness.errors', target='kaji_harness.recovery.signature', ...)`
  となることが示され、差し戻された。そこで:
  - `errors.py` は kaji_harness 内部を import しない（現状維持）。メッセージは渡された `summary` を入れるだけ。
  - 抜粋（`_stderr_summary`）は application 層の `script_exec.py` に置き、`normalize_error_text` は
    `kaji_harness.recovery` の facade（`__all__` に既に載っている）から **module 先頭で** import する。
    application → application は `LAYER_RANK` 上許可される。`normalize_error_text` は public 名で、
    ADR 009 決定 2 の private import にも当たらない。
  - 循環 import は起きない: `kaji_harness.recovery` の import 連鎖（`classify` → `cli` 等、25 module）は
    `kaji_harness.script_exec` を読み込まない（試作で `import kaji_harness.recovery.signature` 後の
    `sys.modules` に `script_exec` が無いことを確認）。runner は既に `recovery.models` を module 先頭で
    import しており（`runner.py:45`）、`script_exec` の唯一の import 元は runner（`runner.py:47`）なので、
    読み込む module の集合も実質増えない。
  - 試作: `script_exec.py` 先頭に `from .recovery import normalize_error_text` を足した合成ソースを
    実ツリーの module 集合で `classify_layer_source` に通し、違反 0 件を確認した。
- **原因行はメッセージの先頭側に置く。** 下流の 3 箇所（runner の ABORT evidence 500 文字、
  incident 根拠欄 500 文字、fingerprint 2000 文字）はどれも先頭を残して切る。incident 根拠欄は
  `steps/<step>/result.json error=ScriptExecutionError: Step ... exited with code N: ` の接頭辞
  （#499 の例で約 150 文字）の後に原因行が続く。原因行が長い場合（長い path を含む等）は
  根拠欄と ABORT evidence で原因行の末尾が切れるが、例外の種類とメッセージの書き出しは先頭側に
  あるので残る。`result.json.error` には要約の全体が残る。
- **正規化してから切る（正規化前の文字数で切らない）。** fingerprint は `normalize_error_text` が
  path・timestamp 等を placeholder に置換した**後**の text を `FINGERPRINT_LIMIT = 2000` 文字で切って
  作られる。正規化**前**の原因行を文字数で切ると、上限をいくつにしても、切るかどうか・
  どこで切るかが可変値の長さで変わり、同じ原因でも hash が分裂する（design review の指摘 2 回:
  300 文字上限で path 20 / 333 文字、8192 文字上限で 3 path × 20 / 3006 文字の同じ EROFS 行が分裂）。
  そこで、上限を超える原因行は **同じ `normalize_error_text` に通した結果**を要約にする。
  切り詰めは常に正規化の後でだけ起きる。
- **不変条件（実装とテストで固定する）**: 任意の原因行 `L` と、fingerprint 算出時に前に付く固定の
  接頭辞 `P`（`ScriptExecutionError: Step '...' deterministic command '...' exited with code N: `）に
  ついて `normalize_error_text(P + L) == normalize_error_text(P + normalize_error_text(L))`。
  これが成り立てば、上限以下（raw）と上限超過（正規化済み）のどちらの表現になっても、同じ原因
  （正規化後が同じ）の fingerprint は一致する。成り立つ理由: 正規化は冪等（placeholder は再度の置換
  対象にならず、`mask_secrets` の伏字も同じ伏字に置換される）で、`P` は固定文字列で `: ` で終わるため
  `L` 側の置換と境界で干渉しない。正規化結果は 2000 文字で切られているが、`P` が前に付く分、外側の
  `[:2000]` の窓はその切れ目より手前で終わる。試作（本版で再実行）で、3 つの path を 20 / 333 / 3006 / 10000 文字に
  変えた原因行（134〜30,077 文字）がすべて同じ hash（先頭 `ddbe02653c7d`、verify-design の独立確認と同値）、
  行内 timestamp・run_id が違う原因行も同じ hash、`RuntimeError` の行とは別 hash になることを確認した。
- **上限 2000 の理由**: `FINGERPRINT_LIMIT` と同じ値にすると、raw で入れた原因行が fingerprint の窓
  （正規化後 2000 文字）をはみ出す量と、正規化表現に切り替わる境界がそろい、メッセージは最大で
  接頭辞 + 2000 文字程度に収まる（有界）。値は不変条件に影響しない（どの値でも hash は一致する）ため、
  可読性とメッセージ長の兼ね合いで選んだ。定数は `script_exec.py` に置き、`FINGERPRINT_LIMIT` を参照して
  値の重複を避ける（`kaji_harness.recovery` の facade は `FINGERPRINT_LIMIT` も `__all__` に載せている）。
- **抜粋は行単位で決める（文字数の窓で決めない）。** 「末尾 N 文字」の窓は、worktree 名などの
  path の長さで窓の開始位置がずれ、同じ原因でも fingerprint が変わる。行単位なら path の長さは
  行の中だけの差になり、正規化（`<PATH>`）で消える。
- **原因行より前の行は要約に入れない。** 前の行には pytest の実行時間
  （例: `in 312.45s`）のような、正規化で消えない実行ごとの値が入りうる。入れると EB 3 を壊す。
  前の文脈（traceback のフレーム）は `stderr.log` で読む。
- **既存の正規化に頼る範囲を明確にする。** 原因行に入る実行ごとの値は、絶対 path・timestamp・
  run_id・UUID・長い hex/数値など、`normalize_error_text` が既に置換するものを前提にする。
  `signature.py` は変更しない（`script_exec.py` から呼ぶだけ）。
- 要約に `Last <N> chars:` を付け足さない（`signature.py` の `_TAIL_RE` がそれ以降を
  `<TAIL>` に潰すため）。本設計の形式はこの語を使わない。
- 外部依存の追加なし。

### 採用しなかった配置（implement からの差し戻しで比較した候補）

| 候補 | 判定 | 理由 |
|------|------|------|
| (1) 正規化（`normalize_error_text` と依存する `mask_secrets` 等）を foundation 層の新 module へ移す | 不採用 | foundation は foundation 同士の import も禁止されている（`violates_foundation`）。`errors.py` から使うには正規化パイプラインを `errors.py` 自体に置くか、layer 規約（ADR 009 決定 1・`tests/test_layer_imports.py`）を変える必要がある。前者は例外定義 module に署名ロジックを混ぜ、後者は本 Issue の範囲を越える規約変更 |
| (2) 抜粋を application 層（`script_exec.py` の送出側）で行い、要約を例外に渡す | **採用** | 送出元は `_run_argv` の 1 箇所で、両経路（EB 5）が通る。正規化の定義は `signature.py` の 1 箇所のまま。layer 違反も循環も無い。代償は `ScriptExecutionError` に keyword-only 引数 `summary` が増えること（既存属性・型名は不変） |
| (3) 正規化に依存しない有界化 | 不採用 | raw の文字数で切る限り、上限の値によらず可変値の長さで hash が分裂する（design review で 2 回実証）。逆に可変値に依存しない固定表現（例: 例外型名だけ、`<too long>` 等）に置き換えると、長い原因行どうしの別原因が同じ hash になり EB 2 を壊す |

### 既知の限界

- 最後の非空行が原因を表さない stderr（失敗の後に無関係な警告を出して終わる command 等）では、
  原因行の代わりにその行が残る。現行の banner よりは原因に近く、全文は `stderr.log` に残る。
- 原因行が 2000 文字を超えると、メッセージ上の path 等は `<PATH>` 等の placeholder になり、実際の
  値は読めない（`stderr.log` で読む）。fingerprint の一意性と有界性を優先した結果で、通常の例外行
  （#499 で約 90 文字）はこの経路に入らない。
- fingerprint は正規化後の `attempt_error` 全体（接頭辞 + 要約）の先頭 2000 文字から作られる。
  `command_label` が長い（長い argv の `exec:` step 等）と接頭辞が窓の大半を占め、原因行の一部
  または全部が窓に入らない場合がある。その場合は原因の識別に使われない。これは `signature.py` の
  既存の性質で、本変更で変えない（機構の変更は本 Issue の範囲外）。
- 同じ最終例外行を別の呼び出し元から出した失敗は、同じ fingerprint になる。これは
  「同じ原因の再発」として扱う（incident 機構の集約目的に沿う）。

## 方針

`kaji_harness/errors.py` と `kaji_harness/script_exec.py` を変更する（`signature.py` は不変）。

```python
# kaji_harness/errors.py（foundation。kaji_harness 内部を import しない）
class ScriptExecutionError(HarnessError):
    def __init__(self, step_id, command_label, returncode, stderr, *, summary: str):
        ...  # 既存属性は変更しない
        self.summary = summary
        super().__init__(
            f"Step '{step_id}' deterministic command '{command_label}' exited with "
            f"code {returncode}: {summary}"
        )


# kaji_harness/script_exec.py（application）
from .recovery import FINGERPRINT_LIMIT, normalize_error_text

_STDERR_SUMMARY_LIMIT = FINGERPRINT_LIMIT

def _stderr_summary(stderr: str) -> str:
    """stderr の最後の非空行を、失敗原因行として有界に返す。

    上限を超える行は raw で切らず、fingerprint と同じ正規化を通した結果を返す
    （正規化 → 切り詰めの順序を保ち、可変値の長さで署名が変わらないようにする）。
    """
    for line in reversed(stderr.splitlines()):
        line = line.rstrip()
        if line:
            if len(line) <= _STDERR_SUMMARY_LIMIT:
                return line
            return normalize_error_text(line)
    return "(no stderr)"
```

- `ScriptExecutionError` の docstring に、メッセージには送出元が作った要約（stderr の最後の非空行）
  だけを入れること、全文は `stderr` 属性と `stderr.log` にあること、要約を送出元で作る理由
  （正規化は application 層にあり、foundation の `errors.py` からは使えない）を書く。
- `script_exec.py` の module docstring の不変条件（「exit code != 0 は `ScriptExecutionError` で fail-loud」）に、
  メッセージは stderr の最後の非空行を要約したものであることを 1 行足す。
- 両経路とも同じ `_run_argv` の送出箇所（`script_exec.py:185-187`）を通るため、1 箇所の変更で EB 5 を満たす。
- `signature.py` / `SIGNATURE_SCHEMA_VERSION` は変更しない。署名の算出方法は変わらず、
  入力（メッセージ）だけが変わるため。正規化の定義は `signature.py` の 1 箇所に保つ。
- 同じパターンの `CLIExecutionError` / AI formatter は変更しない（§ 根本原因「同じパターンの他の箇所」）。
- 既存テストの `ScriptExecutionError(...)` 直接生成（`tests/test_exec_step_parser.py:166,171,176`、
  `tests/test_exec_step_dispatch.py:360`、`tests/test_tmp_env_propagation.py:503`）に `summary=` を足す。
  検証内容（command_label・文言・属性の保持）は変えない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既存 incident の fingerprint 互換性 | 互換処理をしない。既存の `ScriptExecutionError` 系 incident（open は #499 のみ）は新しい hash と一致せず、同じ原因でも新規起票されうることを許容する | Issue 本文「重要判断」1 項目目（AI の仮定・two-way door と明記、review-design で再確認）。既存方針として `kaji_harness/recovery/signature.py` の `SIGNATURE_SCHEMA_VERSION` コメント（「migration 機構は作らない — #303 決定 E」、不一致は新規起票に倒す）と ADR 008（`docs/adr/008-no-backward-compat-layer.md` 決定 1: 互換レイヤを書かない）。readiness review コメントも同じ整理で PASS | `SIGNATURE_SCHEMA_VERSION` は上げない（算出方法は不変で入力だけが変わるため）。#499 の処遇（close 等）は既存どおり人間判断とし、本 Issue では操作しない |
| `ScriptExecutionError` の型名と属性 | 型名と既存属性（`step_id` / `command_label` / `returncode` / `stderr`）は変えない。変えるのはメッセージ本文の抜粋方法だけ | Issue 本文「重要判断」2 項目目。`kaji_harness/errors.py` の既存 docstring（型名は artifact 互換の契約、#205 の共有契約） | `stderr` 属性は全文を保持し続けることを使用例とテストで固定。抜粋方法の変更を layer 規約に沿って配線するため、keyword-only 必須引数と属性 `summary` を**追加**する（次行） |
| 抜粋の配置（layer） | 抜粋は application 層の `script_exec.py` で行い、`ScriptExecutionError` は keyword-only 必須引数 `summary` で受け取る。`errors.py` は kaji_harness 内部を import しない | AI の仮定（two-way door: 例外の生成元は repo 内の 1 箇所とテスト 5 箇所だけで、公開契約は `kaji` console entry point のみ — ADR 009 帰結）。契機は implement の差し戻しコメント（verdict marker `step=implement status=BACK`）。根拠: `tests/test_layer_imports.py`（`errors`=foundation、foundation からの `kaji_harness.*` runtime import は関数内も違反）、ADR 009 決定 1、`python-style.md` § モジュール境界。差し戻しが挙げた 3 候補を § 採用しなかった配置で比較。既存属性・型名は不変なので Issue「重要判断」2 項目目の範囲内と判断。後段の検査先: review-design、`make check`（`test_layer_imports` / `test_private_imports`）、Medium テスト（両経路） | `summary` に既定値を持たせない（fallback を作らない。ADR 008 決定 1）。`normalize_error_text` / `FINGERPRINT_LIMIT` は facade `kaji_harness.recovery` から module 先頭で import（関数内 import をしない）。循環なしと layer 違反 0 件を試作で確認 |
| stderr から残す部分の選び方 | 最後の非空行（2000 文字以下は raw、超過時は `normalize_error_text` の結果）。原因行より前の行は入れない | AI の仮定。根拠: (1) Python traceback と #499 の実ログで最終例外行が原因を表す、(2) 下流 3 箇所が先頭を残して切るので原因行を先頭側に置く必要がある、(3) 文字数の窓や前の行を入れると path 長・実行時間で hash が変わり EB 3 を壊す。Issue 完了条件 1 は選び方を設計で特定するよう求めている（候補: 末尾 N 文字 / 先頭＋末尾 / 最終例外行の抽出）。後段の検査先: review-design、Small テスト（同一原因で path 違いの hash 一致） | 候補のうち「末尾 N 文字」と「先頭＋末尾」は不採用（理由は § 制約・前提条件）。「最終例外行の抽出」は Python 以外の `exec:` command にも効くよう、例外行の正規表現ではなく「最後の非空行」に一般化 |
| 長い原因行の有界化（正規化と切り詰めの順序） | 2000 文字超の原因行は raw で切らず、`normalize_error_text` を通した結果（2000 文字以下）を入れる。切り詰めは正規化の後にだけ起きる | AI の仮定（初版 300 文字 raw 切り詰め → 第 2 版 8192 文字 raw 切り詰めを、design review の指摘 2 回で変更。第 3 版で確定し verify-design で承認。本版は配置のみ変更し規則は同じ）。根拠: raw の文字数で切る限り、上限の値によらず可変値の長さで hash が分裂する（review の試作で 2 回実証）。不変条件 `N(P+L) == N(P+N(L))` を試作で確認。後段の検査先: review-design、Small テスト（不変条件と長さの違う path・行内 timestamp の同一 hash） | 上限は `FINGERPRINT_LIMIT` と同じ 2000（定数を参照）。正規化の定義を複製しない。incident 根拠欄（500 文字）で長い原因行の末尾が切れることは許容（全体は `result.json.error` に残る） |
| `CLIExecutionError` 等の同じパターン | 本 Issue では直さず、調査結果を記録する | Issue 完了条件 5（「調査結果が設計書に記載されている」）。直すかどうかは Issue 本文で決まっていない | transient 判定（`classify.py:101`）への影響を理由に分離。別 Issue 化は完了報告で提案（人間判断） |

one-way door の未決はない。fingerprint 互換性は Issue 本文で two-way door と明示され、
既存方針（#303 決定 E・ADR 008）と矛盾しない。`summary` 引数の追加は repo 内部の例外 API の変更で、
公開契約（`kaji` console entry point）・永続化形式（`result.json` の `error` は従来どおり文字列）を変えない。
layer 規約（ADR 009）は変更せず従う。既存記録の削除・書き換えはしない。

## テスト戦略

### 変更タイプ

実行時コード変更（例外メッセージの組み立て。`result.json` / `run.log` / incident の内容と fingerprint に影響）。

### 実行時コード変更の場合

#### Small テスト

再現テスト（bug 固有ルール）。実装前 Red は § 再現手順 1 の実ログで代替する（Issue 完了条件 2 が明示的に許可）。
いずれも修正後に PASS すること。以下の「要約」は `_stderr_summary(stderr)`、「例外」は
`ScriptExecutionError(..., stderr, summary=_stderr_summary(stderr))`（送出箇所と同じ組み立て）を指す。

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
- **長い可変値でも集約が分裂しない（design review の指摘 2 回に対応）**: 同じ EROFS 例外行に path を
  3 つ含め、各 path を 20 / 333 / 3006 / 10000 文字に変えた原因行（上限 2000 の前後をまたぐ:
  約 130 / 1050 / 9100 / 30000 文字）が、すべて同じ `fingerprint_hash` になる。初版（300 文字）と
  第 2 版（8192 文字）の raw 切り詰めではこの観点が FAIL する。
- **原因行の中の timestamp・run_id**: 原因行の中の timestamp（`2026-10-18T03:55:46Z` と
  `2027-01-01T00:00:00Z`）や run_id だけが違う 2 つの stderr は、同じ `fingerprint_hash` になる
  （先頭部分だけでなく原因行の中の可変値も対象）。
- **不変条件**: 代表的な原因行（path・timestamp・run_id・`exit code N`・secret 形跡・前後の空白・
  `Last N chars:` を含むもの、2000 文字超のもの）について
  `normalize_error_text(P + L) == normalize_error_text(P + normalize_error_text(L))` が成り立つ
  （`P` は runner が付ける `ScriptExecutionError: ` を含む実際の接頭辞）。
- **長い原因の分離**: 2000 文字を超える原因行でも、例外の種類やメッセージが違えば（正規化後の
  先頭 2000 文字の範囲で違えば）hash が異なる。
- **incident 根拠欄**: 上記の `attempt_error` を `sanitize_evidence(f"steps/baseline/result.json error={attempt_error}")`
  に通しても `Read-only file system` が残る（下流の 500 文字切り詰めで原因行が落ちないこと）。
- **抜粋規則の境界**: 末尾の空行・空白行は飛ばす。`\r\n` 改行と `\r` 区切りの進捗表示で最後の
  区切りを原因行にする。空文字・空白のみは `(no stderr)`。2000 文字ちょうどの原因行は raw のまま、
  2001 文字以上は正規化表現（2000 文字以下、`<PATH>` 等を含む）になる。どちらも要約長は有界で、
  要約に改行が入らない。
- **例外の組み立て**: `str(exc)` は `summary` をそのまま末尾に持つ。`stderr` 属性は全文と一致、
  `step_id` / `command_label` / `returncode` も不変、`summary` 属性は渡した値。既存の
  `tests/test_exec_step_parser.py::TestScriptExecutionErrorGeneralization` は `summary=` を足すだけで
  検証内容を変えずに通る。
- **layer 規約の回帰**: 既存の `tests/test_layer_imports.py::TestRealTreeLayerDirection`（medium）と
  `tests/test_private_imports.py` が、変更後の実ツリーで違反 0 件のまま通る（`make check` で実行。
  新規テストは追加しない — 既存 fitness test がこの観点の正本）。

配置: 要約・fingerprint・根拠欄の観点は `tests/test_script_exec.py`（`_stderr_summary` を直接 import。
private helper を tests から読む前例は `tests/test_runner.py:22` の `_dispatch_kind` 等）、
例外の組み立ては `tests/test_exec_step_parser.py` の `TestScriptExecutionErrorGeneralization` に追加する
（どちらも `@pytest.mark.small`）。

#### Medium テスト

完了条件 4（両経路で原因行が `result.json.error` に残る）を、runner を通して検証する。
実 subprocess（`sys.executable`）を使うのは既存の `tests/test_exec_step_dispatch.py::TestExecuteExecSubprocess`
（`@pytest.mark.medium`）と同じ扱い。送出箇所で `summary` を組み立てる配線そのものも、この経路で確認される。

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
| docs/adr/ | なし | 新しい技術選定なし。ADR 008（互換レイヤなし）と ADR 009（layer 規約）に従うだけで、規約自体は変えない |
| docs/ARCHITECTURE.md | なし | 層の割り当て・module 構成の変更なし（application → application の import が 1 本増えるだけ） |
| docs/dev/ | なし | `docs/dev/skill-authoring.md:185` と `docs/dev/workflow-authoring.md:193` は「非ゼロ終了は `ScriptExecutionError`」と型だけを書いており、メッセージ形式・コンストラクタに触れていない |
| docs/reference/ | なし | `docs/reference/python/python-style.md:39` の `stderr[:200]` は「長い文字列を括弧で連結する」書式の例で、切り詰め方の規約ではない |
| docs/cli-guides/ | なし | CLI の仕様変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（本 PR では編集しない） | CHANGELOG は `/release` で生成する。例外メッセージ・内部例外のコンストラクタは公開契約ではない（ADR 009 帰結: 公開契約は `kaji` console entry point）ため BREAKING には当たらない |

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
| runner の import | `kaji_harness/runner.py:45,47` | `recovery.models` と `script_exec` を module 先頭で import（`script_exec` の唯一の import 元） |
| snapshot の attempt_error | `kaji_harness/recovery/snapshot.py:379,423` | `result.json` の `error` を `attempt_error` にし、`sanitize_evidence` で根拠欄に転記 |
| 根拠欄の切り詰め | `kaji_harness/recovery/report.py:20,89-102` | `EVIDENCE_LIMIT = 500`、mask → 先頭を残して truncate |
| 署名の算出 | `kaji_harness/recovery/signature.py` | `_canonical_input` は `attempt_error` 優先。正規化で path・timestamp 等を置換し、先頭 `FINGERPRINT_LIMIT = 2000` 文字を sha256。`_TAIL_RE` は `Last N chars:` 以降を `<TAIL>` に置換。`SIGNATURE_SCHEMA_VERSION` のコメントに「migration 機構は作らない — #303 決定 E」 |
| recovery facade | `kaji_harness/recovery/__init__.py:65-73,76` | `normalize_error_text` と `FINGERPRINT_LIMIT` を `__all__` で公開済み |
| layer fitness test | `tests/test_layer_imports.py:11-59,160-186,323` | `errors`=foundation、`recovery` / `script_exec`=application。foundation からの `kaji_harness.*` runtime import は `violates_foundation`。関数内 import も runtime edge（`test_function_local_import_remains_runtime_dependency`） |
| モジュール境界規約 | `docs/adr/009-module-boundary-private-import.md` 決定 1・2・帰結、`docs/reference/python/python-style.md` § モジュール境界と private import | foundation は kaji_harness 内部依存ゼロ。循環回避の関数内 deferred import は依存方向誤りの兆候。public 名の import は禁止対象外。公開契約は `kaji` console entry point |
| implement の差し戻し | https://github.com/apokamo/kaji/issues/516 （verdict marker `step=implement status=BACK` のコメント） | 第 3 版設計の `errors.py` 関数内 import が `LayerViolation(source='kaji_harness.errors', target='kaji_harness.recovery.signature', ...)` となり `make check` を通せないこと、配置の 3 候補 |
| transient 判定 | `kaji_harness/recovery/classify.py:101` | `CLIExecutionError` の `attempt_error` を `is_transient_error_text` で判定（`CLIExecutionError` を対象外にした理由） |
| AI formatter のエラー | `kaji_harness/verdict.py:553-556` | `result.stderr[:300]`（同じパターン、調査結果） |
| 後方互換方針 | `docs/adr/008-no-backward-compat-layer.md` | 決定 1「後方互換レイヤを書かない」（`summary` に fallback を持たせない根拠） |
| テストサイズ | `docs/dev/testing-convention.md`、`tests/test_exec_step_dispatch.py:65-66` | 実 `sys.executable` subprocess を使う runner/exec テストは既存で `medium` |
| Issue #516 本文・readiness review / design review コメント | https://github.com/apokamo/kaji/issues/516 | OB / EB / 再現手順 / 重要判断（fingerprint 互換性を two-way door として許容）。readiness review で合成再現の hash 一致を独立確認。verify-design（cycle 2）で抜粋規則（正規化後の有界化）を承認 |
| Incident #499 | https://github.com/apokamo/kaji/issues/499 | 本 Issue の発生源。本文と fingerprint に pytest banner だけが残っている |
