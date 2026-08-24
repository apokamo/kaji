# [設計] ABORT 系 cause を incident 記録の対象外にする（署名退化による誤集約の解消）

Issue: #405

## 概要

failure triage 第1層で `agent_declared_abort` / `cycle_exhausted` の識別署名が
`<no-error-text>` に退化し、無関係な安全停止が同一 incident イシューへ集約される問題を、
両 cause を `INCIDENT_EXEMPT_CAUSES` へ追加して incident 記録の対象外にすることで解消する。
併せて、既に汚染された `occurrences.jsonl` を掃除し、除外規則を docs へ反映する。

## 背景・目的

### Observed Behavior（OB）

#### OB-1: 異なる step の agent ABORT が同一署名に退化する（決定論的）

`compute_signature()` の canonical input は `attempt_error` / `workflow_end_error` のみ
（`kaji_harness/recovery/signature.py:144-150,167-168`）。agent ABORT は例外ではないため
両方 null であり、`fingerprint` が定数 `_NO_ERROR_TEXT = "<no-error-text>"` に退化する。

本設計の作成時に main（`221997d`）で再実行した実測値:

```
design: <no-error-text> 4ee617eee91581490c4cc7ee12e93589d7f688ce6a775e4f771b5253a7e9dc24
close : <no-error-text> 4ee617eee91581490c4cc7ee12e93589d7f688ce6a775e4f771b5253a7e9dc24
matches: True
cycle : <no-error-text> 4ee617eee91581490c4cc7ee12e93589d7f688ce6a775e4f771b5253a7e9dc24 matches_design: False
```

`cycle_exhausted` は `cause` が署名キーに含まれるため `agent_declared_abort` とは別バケット
になるが、cause 内部では全件が同一 hash へ衝突する（同じ退化が独立に起きている）。

#### OB-2: 実運用データで 8 occurrence が 1 署名へ集約されている

`/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl` の実測（2026-08-25 再測定、15 行）:

| run_id | source issue | failed_step | cause | hash 先頭 |
|---|---|---|---|---|
| 260714000453 | 314 | review-ready | dispatch_failure | d7b6c1ec |
| 260715021013 | 328 | pr | dispatch_failure | 6dd57a74 |
| 260716025951 | 346 | review-design | agent_declared_abort | 4ee617ee |
| 260717214040 | 357 | review-ready | agent_declared_abort | 4ee617ee |
| 260717220728 | 357 | review-design | agent_declared_abort | 4ee617ee |
| 260723023106 | 331 | close | agent_declared_abort | 4ee617ee |
| 260723170525 | 368 | doc-update | agent_declared_abort | 4ee617ee |
| 260723171857 | 368 | close | agent_declared_abort | 4ee617ee |
| 260730022435 | 391 | design | agent_declared_abort | 4ee617ee |
| 260730041135 | 391 | design | agent_declared_abort | 4ee617ee |
| 260730222002 | 391 | implement | dispatch_failure | 5a0e69f4 |
| 260731015910 | 391 | implement | dispatch_failure | 5a0e69f4 |
| 260821001117 | 396 | review-ready | dispatch_failure | 82b0c1d7 |
| 260822045729 | 405 | fix-design | dispatch_failure | f173c145 |
| 260824014705 | 405 | fix-design | cycle_exhausted | 4ee617ee |

初稿時の 13 行から、#405 の設計修正 workflow 中に `dispatch_failure` と `cycle_exhausted` が
1 行ずつ増えた。現在の内訳は `dispatch_failure` 6 件 / `agent_declared_abort` 8 件 /
`cycle_exhausted` 1 件。5 issue・6 step にまたがる別物の agent ABORT 8 件が
1 署名（`4ee617ee…`）に集約され、incident イシュー #350 / #359 / #367 / #369 / #392 を生んだ。

#### OB-3: 偽のリグレッション判定が連鎖する

`docs/dev/incident-labels.md:51` の照合規則「closed かつ `incident:cause:transient` なし
（人間 resolve 済み）→ 新規起票し旧イシューへリンク」に一致するため、#359 / #367 / #369 / #392
のすべてが本文に「過去に人間が resolve 済みの同一署名イシュー `#350` のリグレッションの
可能性がある」を持つ。いずれも #350 とは無関係であり判定は誤り。人間が resolve するたびに
次の無関係な ABORT が新たな「リグレッション」を生む構造になっている。
加えて #392 は既起票済み 7 run を occurrence として backfill し、再発回数 `N=8` も二重計上している。

### Expected Behavior（EB）

- **EB-1**: `agent_declared_abort` は incident 記録（新規起票 / 再発追記 /
  `occurrences.jsonl` 追記）の対象外になる。triage コメント・run artifact・console 表示は維持
  （triage コメントは投稿と構成が維持され、cause 説明 1 行のみ改訂する。契約の正本は § 方針 2）。
  - 根拠: `kaji_harness/recovery/report.py:48-51` が当該 cause を
    「agent が正規の ABORT verdict を返した。安全停止・手動確認要求であり、自動再開の対象に
    しない」と定義する。契約上の正常終端であり障害ではない。
  - 根拠: ABORT verdict の reason / evidence は source Issue へ `kaji-verdict` marker 付き
    コメントとして投稿され、直後に triage コメントが続く。exit code 1 と stderr サマリも出るため、
    incident イシューが無くても信号は失われない。
- **EB-2**: `cycle_exhausted` も同様に対象外になる。
  - 根拠: `report.py:44-47` が「cycle が `max_iterations` に到達した。これは安全弁の正常作動」
    と定義する。
  - 根拠: `report.py:142-148` が triage コメントに `--reset-cycle` の具体的次アクションを既に
    出力しており、incident イシューは追加情報を持たない。
- **EB-3**: `set(INCIDENT_SUPPRESSION_REASONS) == set(INCIDENT_EXEMPT_CAUSES)` の既存不変条件が
  保たれる（`tests/test_recovery_models.py:103`）。
- **EB-4**: 既存の非 exempt cause の署名・hash は変化しない。`signature.py` は無変更。
- **EB-5**: `occurrences.jsonl` から `agent_declared_abort` の 8 行と、初稿後に記録された
  `cycle_exhausted` の 1 行を削除し、将来の backfill で両 cause の incident が再生成されない。
  - 根拠: `handler.py:522-524` のコメント「`append_occurrence` より前に抜ける
    （`occurrences.jsonl` は backfill の入力でもあるため、1 行でも残すと後から incident を
    再生成しうる）」。

## 再現手順（Steps to Reproduce）

1. 前提: `main`（`221997d` 以降）、`.venv` 有効化。
2. 実行:

```bash
source .venv/bin/activate && python3 - <<'PY'
from pathlib import Path
from kaji_harness.recovery.signature import compute_signature
from kaji_harness.recovery.snapshot import FailureSnapshot, FailureEvent
from kaji_harness.recovery.models import FailureClassification

def sig(step, cause="agent_declared_abort", kind="agent_abort"):
    snap = FailureSnapshot(
        run_id="x", run_dir=Path("."), run_log_schema_version=1,
        workflow_end_status="ABORT", workflow_end_error=None,
        failure_event=FailureEvent(kind=kind, step_id=step, synthetic=False),
        failed_step=step, attempt_error=None, attempt_result_present=True,
    )
    cls = FailureClassification(cause=cause, synthetic=False, source="agent",
                                recoverability_hint="no")
    return compute_signature(snap, cls)

a, b = sig("design"), sig("close")
c = sig("implement", cause="cycle_exhausted", kind="cycle_exhausted")
print("design:", a.fingerprint, a.fingerprint_hash)
print("close :", b.fingerprint, b.fingerprint_hash)
print("matches:", a.matches(b))
print("cycle :", c.fingerprint, c.fingerprint_hash, "matches_design:", c.matches(a))
PY
```

3. 観測: § OB-1 に掲載した 4 行がそのまま出力される。`design` と `close` の `fingerprint` が
   ともに `<no-error-text>`、`fingerprint_hash` がともに `4ee617ee…`、`matches: True`。step が
   異なるにもかかわらず同一 incident として照合される。`cycle` 行は同一 hash だが
   `matches_design: False`（`cause` が署名キーに含まれるため別バケット）。

この再現は artifact も provider も要さない純関数レベルで決定論的に成立するため、実ログ代替
（`_shared/design-by-type/bug.md` § escape clause）は使わず、実装前 Red 証跡を取得する。

## 根本原因（Root Cause）

### なぜ間違っているか

`_canonical_input()`（`signature.py:144-150`）は「エラー文字列がある失敗」だけを想定した設計に
なっている。`normalize_error_text()` の正規化パイプラインは occurrence 固有値を除去して
「同一障害の再発を束ねる」ために作られており、入力が空の場合の `_NO_ERROR_TEXT` は
**例外的フォールバック**であって識別子ではない。

ところが `agent_declared_abort` / `cycle_exhausted` は例外を伴わない終端であり、
`attempt_error` / `workflow_end_error` がともに null になる。この 2 cause では
フォールバックが常態化し、「識別署名」が cause ごとの定数へ縮退する。結果として
「同一署名 = 同一障害」という第1層の前提（`draft/design/issue-304-1-incident.md`）が破れ、
別物の安全停止がすべて 1 バケットへ集約される。

### いつから壊れているか

第1層（Issue #304）で `signature.py` が導入された時点から。ABORT 系 cause は
`_COMMENT_ONLY_CAUSES`（`handler.py:96-104`）で自動再開の対象外にはなっていたが、
incident 記録経路からは除外されておらず、退化した署名がそのまま照合に使われていた。

### 同根の他の壊れ箇所（調査結果）

`attempt_error` / `workflow_end_error` がともに null になりうる cause を全列挙した結果、
`_NO_ERROR_TEXT` へ退化しうるのは次の 3 つ。

| cause | 退化するか | 実 occurrence | 本 Issue での扱い |
|---|---|---|---|
| `agent_declared_abort` | する | 8 件 | 除外する（EB-1） |
| `cycle_exhausted` | する | 1 件 | 除外する（EB-2） |
| `ambiguous_worktree_abort` | する | 0 件 | **対象外・現状維持**（下記） |

その他の cause（`dispatch_failure` / `verdict_resolution_failure` / `runtime_error` /
`config_or_definition_error` / `unknown_external_error` / `kaji_bug_suspected` /
`user_precondition_error` / `user_interrupted`）は例外を伴うため canonical input が非空になる。
実データ上も `dispatch_failure` の 6 件は `d7b6c1ec` / `6dd57a74` / `5a0e69f4`（×2）/
`82b0c1d7` / `f173c145` の 5 種に正しく分かれており、退化していない。

#### `ambiguous_worktree_abort` を対象外とする技術的根拠

当初案（`_canonical_input()` の fallback に abort reason を足す）は成立しない。

abort reason は `kaji_harness/runner.py:852` の `Verdict.reason` として
`f"multiple worktrees match issue {run_ctx.canonical_id}"` と組まれる。`canonical_id` は
`391` のような裸の数字であり、`normalize_error_text()` の `_ISSUE_REF_RE`（`#\d+`,
`signature.py:62`）にも `_LONG_NUM_RE`（`\d{4,}`, `signature.py:68`）にもマッチしない。
したがって reason を canonical input に足すと **issue 番号が fingerprint に生で残り、Issue ごとに
署名が分裂する** — `draft/design/issue-304-1-incident.md:381` の決定 E「`step_id` / issue 番号 /
workflow 名は署名キーに入れない」に正面から違反する。

マスクするには共有 normalizer に規則を追加することになり、既存 incident イシューの
`fingerprint_hash` 安定性に影響しうる（#303 決定 E により署名 schema の migration 機構は存在せず、
不一致は「一致なし＝新規起票」に倒れる）。本 Issue の scope で安全に実施できる変更ではない。

なお `ambiguous_worktree_abort` は occurrence 0 件（未発火）であり、かつ全発生が
「同一 Issue に複数 worktree が存在する運用不整合」という単一の現象クラスに属するため、
単一バケットへの集約自体は現時点で実害を持たない。two-way door として先送りし、実際に発生して
集約が実害を生んだ時点で再判断する。

## インターフェース

bug 修正であり、公開 IF（CLI 引数・exit code・artifact schema）は一切変更しない。
変更するのは module 内定数と固定文面のみ。

### 入力

| 対象 | 変更前 | 変更後 |
|---|---|---|
| `INCIDENT_EXEMPT_CAUSES`（`recovery/models.py:87`） | `{"user_precondition_error", "user_interrupted"}` | `{"user_precondition_error", "user_interrupted", "agent_declared_abort", "cycle_exhausted"}` |
| `INCIDENT_SUPPRESSION_REASONS`（`recovery/models.py:90-98`） | 2 key | 4 key（追加 2 key の固定文は下記） |
| `_CAUSE_DESCRIPTIONS`（`recovery/report.py:44-51`） | 当該 2 cause の説明文に incident 言及なし | 「incident 起票の対象外」を明記した文面へ改訂 |

追加する固定文（既存 2 件の書式に合わせ、小文字始まり + `; excluded from incident recording` で終える）:

```python
"agent_declared_abort": (
    "agent returned a legitimate ABORT verdict (safe stop / manual confirmation "
    "requested); excluded from incident recording"
),
"cycle_exhausted": (
    "cycle reached max_iterations (safety valve worked as designed); "
    "excluded from incident recording"
),
```

`_CAUSE_DESCRIPTIONS` の改訂文（既存 exempt 2 cause が `"incident 起票の対象外"` を含む慣習に
揃える。`tests/test_recovery_report.py:170,190` が同じ文言を assert しているため、新規 2 cause も
同一 assert で検査できる）:

```python
"cycle_exhausted": (
    "cycle が `max_iterations` に到達した。これは安全弁の正常作動であり、"
    "自動再開の対象にしない。障害ではないため incident 起票の対象外とする。"
),
"agent_declared_abort": (
    "agent が正規の ABORT verdict を返した。安全停止・手動確認要求であり、"
    "自動再開の対象にしない。障害ではないため incident 起票の対象外とする。"
),
```

### 出力

| 出力先 | 変更 |
|---|---|
| `run.log` | 当該 2 cause の run で `incident_recorded` の代わりに `incident_suppressed` event（`cause` / `exception_type` / `failed_step` / `reason`）が 1 件出る |
| `recovery.json` | `incident_suppressed=true` / `incident_suppression_reason=<固定文>`、`incident_ref` / `incident_action` は `null` |
| `<artifacts_dir>/incidents/occurrences.jsonl` | 当該 2 cause の行を追記しない |
| GitHub | 当該 2 cause で incident イシューの起票・追記・検索を一切行わない |
| triage コメント | **投稿・構成は不変**。変わるのは `### 原因（機械判定）` 直下の cause 説明 1 行のみ（§ 方針 2 の契約表を参照） |
| stderr サマリ / exit code / `decision` 値 | **完全に不変**（`agent_declared_abort` は `comment_only`、`cycle_exhausted` は `not_resumable` のまま） |

### 使用例

コード変更は定数のみのため利用者側の呼び出しは変わらない。振る舞いの差分は次で確認できる。

```bash
# 修正後: agent ABORT で終わった run を triage しても incident は記録されない
kaji recover <issue> --run-id <run_id>
jq 'select(.event=="incident_suppressed")' .kaji-artifacts/<issue>/runs/<run_id>/run.log
jq -r '.incident_suppressed, .incident_suppression_reason' \
  .kaji-artifacts/<issue>/runs/<run_id>/recovery.json
```

## 制約・前提条件

- `kaji_harness/recovery/signature.py` / `incident.py` / `handler.py` は**無変更**。
  既存 `fingerprint_hash` の安定性を壊さないため（#303 決定 E に migration 機構は無く、
  不一致は「一致なし＝新規起票」に倒れる）。
- `handler.py:527-538` の除外分岐は既存実装をそのまま使う。除外判定は
  `classification.cause in INCIDENT_EXEMPT_CAUSES` の 1 箇所に集約されており、
  `append_occurrence` より前・再入ガードより前に位置する（`handler.py:520-524` のコメント）。
  したがって集合へ 2 要素を足すだけで EB-1 / EB-2 / EB-5 の全経路が閉じる。
- `INCIDENT_SUPPRESSION_REASONS` は `handler.py:528` が `[cause]` で引くため、
  `INCIDENT_EXEMPT_CAUSES` へ要素を足したら必ず対応する固定文を足す（`KeyError` 回避）。
  これが EB-3 の不変条件の実効的な意味。
- `.kaji-artifacts/` は `.gitignore:49` で除外されている。**`occurrences.jsonl` の掃除は
  PR diff に現れないローカルデータ操作**であり、レビューは実行前後の実測値（件数・cause 分布）
  でのみ検証できる。
- `_COMMENT_ONLY_CAUSES`（`handler.py:96-104`）は変更しない。recovery decision の値は
  本修正の対象外であり、`agent_declared_abort` = `comment_only`、
  `cycle_exhausted` = `not_resumable` は現状のまま維持する。
- `append_occurrence()`（`incident.py:339-345`）へ file lock を導入することは本 Issue の
  scope 外（`incident.py` 無変更の制約）。並行 append に対する保全は掃除手順側で行う
  （§ 方針 3）。writer 側の排他が必要になるのは「複数の `kaji run` を常時並行させる運用」へ
  移行した場合であり、現行の単一オペレータ運用では顕在化しない。必要になった時点で別 Issue
  として判断する。
- 既存の incident イシュー #350 / #359 / #367 / #369 / #392 は削除・改変しない
  （処遇は人間が行う。#392 の扱いは Issue の「ワークフロー完了後の確認項目」）。
  修正後は `_record_incident` が検索前に return するため、これらが再照合されることはない。

## 方針

### 1. `recovery/models.py`: 除外集合と抑止理由の追加

`INCIDENT_EXEMPT_CAUSES` に 2 要素、`INCIDENT_SUPPRESSION_REASONS` に対応する 2 固定文を追加する。
既存コメント（「他のユーザー操作ミス・設定ミスの一般化は scope 外」）は、除外集合が
「ユーザー起因」だけでなく「契約上の正常終端」も含むようになるため、意味が変わった旨を反映して
改訂する。

### 2. `recovery/report.py`: cause 説明文の改訂

`_CAUSE_DESCRIPTIONS` の当該 2 cause に「障害ではないため incident 起票の対象外とする」を足す。

**triage コメントの変更契約（本設計が定める正本）**: `render_triage_comment()` は
`_CAUSE_DESCRIPTIONS[classification.cause]` を `### 原因（機械判定）` の直下へ 1 行そのまま
出力する（`report.py:192-193`）。したがって本変更は **triage コメント本文を 1 箇所だけ変える**。

| triage コメントの構成要素 | 本変更での扱い |
|---|---|
| コメントの投稿有無・投稿タイミング・`Comment.ref` の扱い | 不変 |
| 見出し `## Workflow failure triage` と項目表（`report.py:189-190`） | 不変 |
| `### 原因（機械判定）` 直下の cause 説明 1 行 | **変更**（当該 2 cause のみ。他 10 cause は不変） |
| `判定理由`（`decision.reason`）・`## 判断根拠`（`decision.evidence`） | 不変（`decision` 値自体を変えないため文言も変わらない） |
| 次アクション行（`_next_action_lines()`。`cycle_exhausted` の `--reset-cycle` 行を含む） | 不変 |
| `## 元 run の artifact` / `## 自動再開の実施有無` | 不変 |

「triage コメントは維持される」という EB-1 / EB-2 の主張は、**incident 記録を抑止しても triage
コメントの投稿と情報量が失われない**という意味であり、cause 説明 1 行の改訂と両立する。
テスト期待もこの定義に揃える（§ テスト戦略 Small 3 / Medium 5・6）。

### 3. `occurrences.jsonl` の掃除（ローカルデータ操作）

対象は `/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl`（`kaji run` が
main worktree から起動される際の `artifacts_dir`）。

#### 書き込み側の性質（保全設計の前提）

`append_occurrence()` は **呼び出しのたびに `open(path, "a")` で path を開き直し、1 行 write して
close する**（`incident.py:339-345`）。lock は取らない。ここから 2 つの性質が導かれる。

- **性質 A**: 同一ファイルへの並行 append は `O_APPEND` により互いに壊さない。
- **性質 B**: path を別 inode へ差し替えた後、**新規の `open(path, "a")` は新しい inode を開く**。
  旧 inode へ write しうるのは、差し替え前に `open()` を終えた fd を持つ process だけである。
  ただしその write が **いつ起きるか**は保証されない（`open()` と `write()` の間で任意に遅れうる）。
  したがって「旧 inode への write はもう来ない」と言うには、**そのような fd が存在しないこと**を
  確認する必要がある。

ここから 2 つの要件が分かれる。

| 要件 | 内容 | 本設計での保証手段 |
|---|---|---|
| 無損失（データ） | 並行 append されたデータを 1 行も消さない | 旧 inode を hard link で保持し、`mv` で unlink しない |
| 回収の完了性 | 「もう追加は来ない」と確定した上で回収・検証を終える | 旧 inode を開いている fd が 0 件であることを直接確認する（性質 B より、0 件なら以後到達不能） |

前々案は「`mv` 直前に再ハッシュして競合を検出する」方式だったが、**再ハッシュから `mv` までの
間に旧 inode へ届いた append を検出できず、`mv` が旧 inode を unlink するため復元もできない**。
前案は hard link で無損失（データ）を解決したが、**完了性**を「差し替え直前の 1 write だけが
対象」と誤って仮定しており、pre-swap fd を保持した writer の遅延 write で破れた
（§ 実測による検証 2）。`pgrep` による process 有無の確認は一時点の観測にすぎず、完了性の根拠に
ならない。writer 側へ lock を入れるのは scope 外（`incident.py` 無変更）であるため、
**無損失は hard link、完了性は fd 到達不能性の確認**という 2 段構えで保証する方式へ改める。

#### 方式: 旧 inode を hard link で保全し、到達不能を確認してから回収を確定する

1. 掃除の前に、原本へ **2 本目の hard link**（`$OLD`）を張る。以後 `$OLD` は原本 inode を
   参照し続け、`mv` で path のリンクが差し替わっても inode は生存する。
2. `$OLD` を入力に filter して tmp を作り、`mv` で path を新 inode へ差し替える（唯一の
   atomic 操作）。**unlink される inode は存在しない**ため、この時点で失われるデータはない。
3. 差し替え後、旧 inode へ write できるのは **swap 前に `open()` を終えた fd を持つ process
   だけ**である（性質 B。新規の `open(path, "a")` は新 inode を開く）。したがって
   **旧 inode を開いている fd が 0 件**であることを確認できれば、以後その inode への write は
   **不可能**であり、その時点の回収結果が最終になる。
4. fd の有無は `/proc/[0-9]*/fd/*` の実体を `stat -Lc '%d:%i'` で照合して数える。0 件になるまで
   待ち（上限付き）、0 件を確認してから最終回収と検証を行う。
5. 回収は `$OLD` と現ファイルの差集合を **append（`>>` = `O_APPEND`）で戻す**操作であり、
   writer と同じ操作なので競合しない。

> **前案からの変更点**: 前案は「差し替え直前に旧 inode へ届いた 1 write だけが回収対象」と述べ、
> 「どの時点の append も step 3 が回収する」と主張していた。これは誤りである。swap 前に
> `open()` を済ませた writer が、回収ループと最終検証の**後**に `write()` すると、その行は
> `$OLD` に残るが現 path には現れず、検証済みの `LOST=0` が事後に破れる（レビューで
> 決定論的な反例が提示され、本設計フェーズでも再現した。§ 実測による検証）。
> hard link により**データ自体は失われない**が、「回収の完了性」は別に保証する必要がある。
> 本案はそれを **fd 到達不能性の確認**（step 3-4）で与える。
>
> `pgrep` による process 有無の確認は一時点の観測にすぎず、検査後に起動した writer を排他
> できないため完了性の根拠にならない。一方 fd 照合は「この inode に到達しうる経路が現在
> 存在しない」ことの直接確認であり、**新規 `open()` は新 inode を開く**（性質 B）ため、
> 0 件確認以降に旧 inode へ到達する経路は生じない。両者は観測対象が異なる。

#### 手順

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/aki/dev/kaji/main
F=.kaji-artifacts/incidents/occurrences.jsonl
FILTER='select(
    .signature.cause != "agent_declared_abort"
    and .signature.cause != "cycle_exhausted"
)'

# 1. 原本 inode への 2 本目のリンクを張る（上書きしない。既存なら停止）。
OLD="$F.pre405-$(date -u +%Y%m%dT%H%M%SZ)"
if [ -e "$OLD" ]; then echo "ABORT: $OLD already exists" >&2; exit 1; fi
ln "$F" "$OLD"                       # cp ではなく ln。以後 $OLD == 原本 inode
F_ID=$(stat -Lc '%d:%i' "$F")
OLD_ID=$(stat -Lc '%d:%i' "$OLD")
if [ "$F_ID" != "$OLD_ID" ]; then
    echo "ABORT: hard link does not preserve the original inode" >&2; exit 1
fi
echo "preserved original inode: F=$F_ID OLD=$OLD_ID path=$OLD"

BEFORE_N=$(wc -l < "$OLD")
KEEP_N=$(jq -c "$FILTER" "$OLD" | wc -l)
DROP_N=$((BEFORE_N - KEEP_N))

# 2. filter → atomic swap。jq が失敗した時点では path は未変更なので原状のまま停止する。
if ! jq -c "$FILTER" "$OLD" > "$F.tmp"; then
    rm -f "$F.tmp"; echo "ABORT: jq failed (partial line?); nothing changed" >&2; exit 1
fi
mv "$F.tmp" "$F"                     # 旧 inode は $OLD が保持しているので unlink されない

# 3. 旧 inode への到達不能を確認する。swap 前に open() を終えた fd だけが旧 inode へ
#    write しうる（新規 open(path,"a") は新 inode を開く）。0 件になれば以後 write は不可能。
holders() {
    local fd id
    for fd in /proc/[0-9]*/fd/*; do
        id=$(stat -Lc '%d:%i' "$fd" 2>/dev/null) || continue
        [ "$id" = "$OLD_ID" ] && echo "$fd"
    done
    true                             # 該当なしでも 0 を返す（set -e 対策）
}
SETTLED=0
for _ in $(seq 1 120); do            # 上限 60 秒（0.5s × 120）
    if [ "$(holders | wc -l)" -eq 0 ]; then SETTLED=1; break; fi
    sleep 0.5
done
if [ "$SETTLED" -ne 1 ]; then
    echo "FAIL: processes still hold the old inode ($OLD_ID):" >&2; holders >&2
    comm -23 <(sort "$OLD") <(sort "$F") >> "$F"      # append-only rollback
    exit 1
fi

# 4. 最終回収（append-only。到達不能確認後なので、この結果が最終になる）。
#    jq は必ず実ファイルへ materialize してから使う。process substitution 内で jq が失敗
#    （torn write による壊れた行など）しても終了コードが伝播せず、切り詰めた入力で
#    「LOST=0」と誤判定するため。
# 作業用 keep list は backup glob（*.pre405-*）と紛れない名前にし、終了時に必ず消す。
KEEP="$F.keep.$$"
trap 'rm -f "$KEEP"' EXIT
keep_list() {                        # $OLD の非 ABORT 行を $KEEP へ書き出す
    if ! jq -c "$FILTER" "$OLD" > "$KEEP"; then
        echo "FAIL: jq could not parse $OLD (torn write?); rolling back" >&2
        comm -23 <(sort "$OLD") <(sort "$F") >> "$F"
        exit 1
    fi
}
for _ in 1 2 3; do
    keep_list
    MISSING=$(comm -23 <(sort "$KEEP") <(sort "$F"))
    # `[ -z ... ] && break` は set -e 下で非空時に exit 1 になるため if で書く。
    if [ -z "$MISSING" ]; then break; fi
    printf '%s\n' "$MISSING" >> "$F"
    echo "reconciled $(printf '%s\n' "$MISSING" | wc -l) line(s) that landed on the old inode"
done

# 5. 検証（すべて失敗時に停止する。echo だけで通過させない）。
keep_list
LOST=$(comm -23 <(sort "$KEEP") <(sort "$F") | wc -l)
if [ "$LOST" -ne 0 ]; then
    echo "FAIL: $LOST line(s) lost; rolling back" >&2
    comm -23 <(sort "$OLD") <(sort "$F") >> "$F"      # append-only rollback
    exit 1
fi
EXEMPT_LEFT=$(jq -r 'select(
    .signature.cause == "agent_declared_abort"
    or .signature.cause == "cycle_exhausted"
) | .signature.cause' "$F" | wc -l)
if [ "$EXEMPT_LEFT" -ne 0 ]; then
    echo "FAIL: $EXEMPT_LEFT newly exempt occurrence row(s) remain; rolling back" >&2
    comm -23 <(sort "$OLD") <(sort "$F") >> "$F"
    exit 1
fi
AFTER_N=$(wc -l < "$F")
if [ "$AFTER_N" -lt "$KEEP_N" ]; then
    echo "FAIL: line count $AFTER_N < expected $KEEP_N; rolling back" >&2
    comm -23 <(sort "$OLD") <(sort "$F") >> "$F"
    exit 1
fi
# 到達不能が最後まで保たれたことを再確認する（0 件でなければ検証は最終ではない）。
if [ "$(holders | wc -l)" -ne 0 ]; then
    echo "FAIL: old inode reopened during verification; rolling back" >&2
    comm -23 <(sort "$OLD") <(sort "$F") >> "$F"
    exit 1
fi
echo "OK: BEFORE_N=$BEFORE_N DROP_N=$DROP_N AFTER_N=$AFTER_N LOST=0 OLD=$OLD"
jq -r '.signature.cause' "$F" | sort | uniq -c        # 現在の期待: dispatch_failure 6 件のみ
```

#### この手順が満たす性質

- **無損失（データ）**: `mv` は path のリンクを差し替えるだけで、原本 inode は `$OLD` が保持する。
  どの時点の並行 append も `$OLD` か `$F` のどちらかに必ず存在し、失われない。
- **回収の完了性（到達不能性による保証）**: 旧 inode へ write しうるのは swap 前に `open()` を
  終えた fd だけであり（性質 B）、その fd が 0 件であることを step 3 で直接確認する。0 件確認後は
  旧 inode への write が不可能なので、step 4 の回収結果と step 5 の `LOST=0` は**事後に破れない**。
  step 5 末尾で 0 件を再確認し、検証期間中の再 open も排除する。
- **`pgrep` を完了性の根拠にしない**: process 有無の観測は一時点の情報で、検査後の writer 起動を
  排他できない。本手順は fd 照合（到達経路の直接確認）に置き換え、`pgrep` は必須手順から外した。
- **`jq` 失敗を握り潰さない**: `comm <(jq …)` のような process substitution は内側の終了コードを
  伝播しないため、壊れた行で `jq` が失敗しても切り詰めた入力のまま `LOST=0` と誤判定しうる
  （本設計フェーズの検証中に実際に踏んだ。§ 実測による検証 4）。回収・検証で使う非 ABORT 行は
  `keep_list()` で実ファイル `$KEEP`（`trap … EXIT` で必ず削除。backup glob と紛れない名前）へ
  materialize し、`jq` の失敗をその場で `exit 1` +
  rollback に落とす。
- **停止と復旧**: `set -euo pipefail` を置き、fd 未解放（step 3 の上限超過）、`jq` の parse 失敗、
  step 5 の 4 検査は、いずれも `exit 1` で停止する。停止前に **append-only の rollback**
  （`comm -23 <(sort "$OLD") <(sort "$F") >> "$F"`）で原本の全行を戻す。
  `cp "$OLD" "$F"` による上書き復旧は、swap 後に新 inode へ届いた正常な append を消すため
  採用しない。
- **上書きしない backup**: `$OLD` は UTC タイムスタンプ付きで、既存なら `exit 1`。手順を
  再実行しても実行時の原本を潰さない。`.kaji-artifacts/` 配下（`.gitignore:49`）に残るため
  repo は汚さない。
- **行順の扱い**: step 4 で戻した行はファイル末尾に付く。`read_occurrences()` の結果は
  backfill entry の **列挙順**にのみ影響し、run_id による重複排除（`incident.py:514,534-536`）
  と署名同値判定・再発回数には影響しない。各行は `recorded_at` を保持するため時系列は復元できる。
- **冪等性**: filter は冪等であり、掃除後に新しい ABORT 行が混入しても手順全体を再実行すれば
  同じ状態へ収束する（`$OLD` は新しい名前で追加される）。
- **順序の制約**: 掃除と修正 merge の間に main で newly exempt cause の run が起きると
  再び行が増えうる。
  実装フェーズで掃除して証跡化し、merge 後の事後確認（Issue の「ワークフロー完了後の確認項目」）
  で cause 分布を再測定して `agent_declared_abort` / `cycle_exhausted` が 0 件であることを確かめる。
  増えていた場合は
  本手順を再実行する。
- 初稿時に `cycle_exhausted` の occurrence は 0 件だったが、#405 の設計修正 workflow が
  cycle 上限へ到達したことで 1 件記録された。両 cause は修正後に同じ exempt 集合へ入るため、
  filter は `agent_declared_abort` と `cycle_exhausted` の両方を落とし、backfill の入力を残さない。

#### 実測による検証（本設計フェーズで実施済み）

いずれも **`#### 手順` の code block をそのまま抽出し**（`cd` 先だけ検証用ディレクトリへ置換）、
`occurrences.jsonl` の複製に対して verbatim 実行した結果である（2026-08-24）。

**検証 1: 並行 writer（毎回 `open`→write→`close`）下での実行**

`append_occurrence()` と同型の writer を 20ms 間隔で 60 回並走させた。

| 観測項目 | 結果 |
|---|---|
| 終了コード / 出力 | `0` / `OK: BEFORE_N=22 DROP_N=8 AFTER_N=14 LOST=0` |
| `BEFORE_N=22` の内訳 | 原本 13 行 + swap 前に届いた並行 append 9 行 |
| writer 終了後の `$F` / `LOST` | 66 行 / **0**（swap 後の append はすべて新 inode = 性質 B の実証） |
| `$F` / `$OLD` の `agent_declared_abort` | **0** / **8**（原本 inode が保全されている） |

**検証 2: 前案の反例（pre-swap fd を保持し、回収後に遅延 write）**

レビューで提示された反例を再現した。writer は swap 前に `open()` した fd を保持したまま 4 秒
待ち、回収ループが終わる時刻に 1 行 `write()` してから `close()` する。

| 段階 | 前案（fd gate なし） | 本案（fd gate あり） |
|---|---|---|
| 回収直後の判定 | `LOST=0` で終了 | fd holder **1 件**を検出（`/proc/998695/fd/3`、`device:inode = 2128:3448823`）→ 待機 |
| 遅延 `write()` 到達後 | `LOST=1`（検証済みの結論が事後に破れる） | holder **0 件**に遷移 → 到達不能が確定 |
| 最終回収 | — | `reconciled 1 line(s) that landed on the old inode` |
| script 終了コード / 出力 | — | `0` / `OK: BEFORE_N=14 DROP_N=8 AFTER_N=7 LOST=0` |
| 遅延行（`run_id=LATE`）の所在 | `$OLD` のみ（現 path に無い） | **現 path に存在**（`grep -c LATE = 1`） |
| `$F` の `agent_declared_abort` | — | **0** |

前案の主張「どの時点の append も回収する」が破れる条件を実際に踏み、fd gate を挟むことで
解消されることを確認した。

**検証 3: rollback**

rollback 行（`comm -23 <(sort "$OLD") <(sort "$F") >> "$F"`）を単独実行した。

| 観測項目 | rollback 前 | rollback 後 |
|---|---|---|
| `$F` の行数 | 66 | 88 |
| `$F` の `agent_declared_abort` | 0 | **8**（原本の 8 行が戻る） |
| 並行 append 由来の行（`run_id` が `RACE*`） | 60 | **60**（1 行も失われない） |
| `$OLD` にあって `$F` に無い行 | — | **0** |

`cp "$OLD" "$F"` による上書き復旧ではこの 60 行が消えるため、append-only rollback を採用した
判断が実測で裏付けられている。

**検証 4: torn write（壊れた行）で停止・復旧すること**

検証 2 と同じ構成で、遅延 write の内容を不正な JSON（`{"broken": ,,,}`）にした。
この検証は、初期実装で `comm <(jq …)` の process substitution が `jq` の失敗を握り潰し、
切り詰めた入力のまま `LOST=0` と誤判定していたのを発見して `keep_list()` を導入した経緯を持つ。

| 観測項目 | 結果 |
|---|---|
| script 終了コード | **1**（`set -euo pipefail` + 明示 `exit 1`） |
| 出力 | `jq: parse error: Expected value before ','` に続き `FAIL: jq could not parse …pre405-…(torn write?); rolling back` |
| rollback 後の `$F` の `agent_declared_abort` | **8**（原本の全行が append-only rollback で戻った） |
| 作業用 keep list の残骸（`$F.keep.*`） | **0 件**（`trap … EXIT` で削除） |

### 4. docs 更新

Issue が明示する `docs/dev/incident-labels.md` に加え、**現行 docs が「除外は 2 cause」と
明記している箇所**を同時に更新する。放置すると docs が事実と食い違うため、docs 整合の
最小必要範囲として含める（振る舞いの scope は広げない）。

| ドキュメント | 更新内容 |
|---|---|
| `docs/dev/incident-labels.md` | 新節「第1層が incident 記録しない cause」を追加し、4 cause と各除外理由・除外が照合規則に与える帰結（退化署名が照合母集団に入らない）を記載 |
| `docs/cli-guides/failure-recovery.ja.md:125-160` | 「incident 記録の対象外（Issue #322 / #403）」節の cause 表に 2 行追加、Issue 番号参照に #405 を追記 |
| `docs/cli-guides/failure-recovery.md:148-166` | 同上（英語版） |
| `docs/dev/workflow_guide.md:216-227` | § 第1層の「記録の対象外」箇条書きを 2 ケース → 4 ケースへ更新 |

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| source of truth | incident イシュー #392 の 2 コメント（調査結果 / PR #404 影響確認）を一次情報とする | 人間決定（2026-08-22、#392 のやり取り）。Issue #405 § 重要判断に明記 | 本設計は当該コメントの結論を実装可能な粒度へ分解しただけで、優先順位を変更していない |
| 対応方針 | 案 A（`INCIDENT_EXEMPT_CAUSES` へ追加）を採用。案 B（fingerprint に step / 正規化 reason / abort category を含める）は不採用 | 人間決定「案A採用です」（2026-08-22）。#392 調査結果コメント § 5 | 変更点を `models.py` の 2 定数 + `report.py` の 2 文面に限定し、`signature.py` / `handler.py` / `incident.py` を無変更にすることで案 B の副作用（既存 hash 不安定化）を構造的に排除 |
| scope（対象 cause） | `agent_declared_abort` と `cycle_exhausted` の 2 cause | 人間決定（2026-08-22、選択肢提示に対する回答） | 退化しうる cause を全列挙（3 件）し、3 件目 `ambiguous_worktree_abort` を除外対象に含めないことを § 根本原因の表で明示 |
| `ambiguous_worktree_abort` の扱い | 本 Issue の対象外・現状維持 | AI が技術検証で反証し人間へ報告済み（2026-08-22）。two-way door として先送り | 反証の根拠（`runner.py:852` の reason 文字列と `_ISSUE_REF_RE` / `_LONG_NUM_RE` の非マッチ、#304 設計 L381 の決定 E 違反）を § 根本原因へ転記。再判断の条件（実発生 + 集約による実害）を明記 |
| 一方向性の評価 | 本変更は two-way door | AI の評価（Issue § 重要判断に記載、人間が受領） | 取り消しは frozenset から 2 要素を戻すだけで済み、run artifact の記録形式・署名 schema・既存 hash を変えないことを § 制約で確認 |
| docs 更新範囲を 4 ファイルへ拡大 | Issue が挙げる `incident-labels.md` に加え、除外 cause を「2 つ」と明記している 3 ファイルも更新 | **AI の仮定**。根拠は grep 実測で当該 3 ファイルが cause 数を明記していること（`failure-recovery.ja.md:145-160` / `failure-recovery.md:151-166` / `workflow_guide.md:216-227`）。後段の検査先は review-design と `/i-dev-final-check` の docs 整合確認 | 振る舞いの scope は広げず、記述の事実整合のみを対象とする |
| 抑止理由・説明文の具体的文面 | 既存 2 件の書式（英語小文字始まり + `; excluded from incident recording` / 和文に「incident 起票の対象外」）を踏襲 | **AI の仮定**。根拠は `models.py:90-98` と `report.py:72-76` の既存慣習、および `tests/test_recovery_report.py:170,190` が「incident 起票の対象外」を assert していること。後段の検査先は review-code | 文面を § インターフェースに確定値として記載し、実装時の裁量を残さない |
| `occurrences.jsonl` 掃除のタイミング・並行 append 保全・復旧 | 実装フェーズで原本 inode を hard link に保全してから filter + atomic swap し、旧 inode の fd holder が 0 件になった後に append-only で最終回収・検証する。merge 後の事後確認でも cause 分布を再測定する | **AI の仮定**。根拠は #392 の「実装順序の制約」コメント、`handler.py:522-524` の backfill 依存、`incident.py:339-345` が呼び出しごとに `open(path, "a")` して lock なしで append すること、および pre-swap fd の遅延 write が fd gate なしでは回収後に到達する反例（§ 実測による検証 2）。後段の検査先は verify-design / review-code と Issue の「ワークフロー完了後の確認項目」 | 無損失はタイムスタンプ付き非上書き hard link（`*.pre405-*`）、回収の完了性は旧 inode の fd holder 1→0 遷移と到達不能確認で保証する。到達不能後の append-only 回収、`LOST=0`、cause 分布、torn write 時の停止、append-only rollback を § 方針 3 の実行手順と検証項目に固定 |
| writer 側（`append_occurrence`）の排他 | 導入しない（`incident.py` 無変更を維持） | **AI の仮定**。根拠は Issue #405 § 影響範囲の「`incident.py` は無変更」指定と、現行が単一オペレータ運用であること。後段の検査先は review-code | 掃除側の hard-link 保全 + fd 到達不能 gate + append-only 回収で pre-swap fd の遅延 write を保全する。writer 側排他が必要になる条件（複数 run の常時並行運用）は § 制約に明記 |

## テスト戦略

### 変更タイプ

実行時コード変更（定数集合の変更により handler の分岐が変わる）+ docs 更新。

### Small テスト

対象: `tests/test_recovery_models.py` / `tests/test_recovery_report.py` /
`tests/test_recovery_signature.py`

1. **除外集合の固定**（既存 `test_incident_exempt_causes_is_limited_to_known_non_incident_causes`
   を更新）: `INCIDENT_EXEMPT_CAUSES == frozenset({"user_precondition_error", "user_interrupted",
   "agent_declared_abort", "cycle_exhausted"})`、`INCIDENT_EXEMPT_CAUSES <= FAILURE_CAUSES`。
   → 修正前 Red（現在は 2 要素）。
2. **不変条件の維持**（EB-3）: `set(INCIDENT_SUPPRESSION_REASONS) == set(INCIDENT_EXEMPT_CAUSES)`
   と、追加 2 key の固定文が非空であること。→ 修正前 Red（key 不足）。
3. **triage コメント文面**（§ 方針 2 の契約に対応）: `agent_declared_abort` /
   `cycle_exhausted` の triage 本文に `_CAUSE_DESCRIPTIONS[cause]` が含まれ、その文面が
   「incident 起票の対象外」を含むこと（既存の `user_precondition_error` /
   `user_interrupted` 検査と同型）。併せて `### 原因（機械判定）` 以外の構成要素
   （項目表・`## 判断根拠`・次アクション行）が従来どおり出力されることを検査する。
   → 修正前 Red。
4. **署名 hash の不変性**（EB-4）: Issue #405 の完了条件は非 exempt cause として
   **`dispatch_failure` と `verdict_resolution_failure` の両方**の `fingerprint_hash` 不変を
   要求している。両 cause の golden 値を実データから pin し、`compute_signature()` の
   `fingerprint_hash` が次を返すことを検査する。

   | # | cause | 入力（実データ） | 期待 `fingerprint_hash` | 出所 |
   |---|---|---|---|---|
   | 4-a | `dispatch_failure` | `StepTimeoutError: Step 'implement' timed out after 3600s` | `5a0e69f403a1e37cb09cc23e5a40ee64a77501a9655ce37264d4d044dbf0a046` | `occurrences.jsonl` の run 260730222002 / 260731015910（#391 implement） |
   | 4-b | `dispatch_failure` | `StepTimeoutError: Step 'pr' timed out after 1800s` | `6dd57a743123862400b6b3294be7648c11432f79b681d9e445a64bcab88ddaa3` | 同 run 260715021013（#328 pr） |
   | 4-c | `dispatch_failure` | `CLINotFoundError: interactive terminal runner requires tmux. Run \`kaji run\` inside tmux or use agent_runner='headless'.` | `d7b6c1ecd57db0f730316cf705304375b143c8b6b79394e2e5f9b1aa781ef4cb` | 同 run 260714000453（#314 review-ready） |
   | 4-d | `verdict_resolution_failure` | `tests/fixtures/incident/verdict_notfound_run{1,2,3}.txt`（#301 の 3 再発の実ログ。repo 内に既存） | `35856983d74433e1b1db9a4089da1de1fbf3d1e736ab130150121d10b33d0fc4`（3 件とも同値） | 既存 fixture。`tests/test_recovery_signature.py:52` の `test_three_recurrences_share_one_fingerprint_hash` が同じ入力を使う |

   **検査方法**: 4-a〜4-c は `_snapshot(attempt_error=<生文字列>, exception_type=<型名>)` +
   `_classification("dispatch_failure")` を `compute_signature()` に通し、
   `fingerprint_hash` を期待値と `==` で比較する。4-d は既存の `_load()` / `_snapshot()` /
   `_classification()`（default が `verdict_resolution_failure`。
   `tests/test_recovery_signature.py:34-46`）をそのまま再利用し、3 fixture すべての
   `fingerprint_hash` が上記 1 値に等しいことを検査する。既存テストは「3 件が同値」だけを
   検査して**値そのものを固定していない**ため、正規化規則が変わっても検出できない。本テストが
   その穴を塞ぐ（既存テストの置き換えではなく追加）。

   **完了条件との対応**: 4-a〜4-c が `dispatch_failure`、4-d が `verdict_resolution_failure` を
   カバーし、Issue の「S: 非 exempt cause（`dispatch_failure` / `verdict_resolution_failure`）の
   `fingerprint_hash` が本修正前後で不変であること」を満たす。

   4 件とも本設計フェーズで main（`221997d`）にて実測済み。4-a〜4-c は `occurrences.jsonl` の
   記録値と一致し、4-d は 3 fixture が同一 hash を返すことを確認した。
   これは **invariant guard であり回帰テストではない**ため、修正前後どちらでも Green になる。
   意図は「`signature.py` を将来触ったときに既存 incident イシューとの照合が静かに壊れることを
   検出する」ことであり、Red→Green 遷移を求めない理由をテスト docstring に明記する。

### Medium テスト

対象: `tests/test_recovery_incident_handler.py`（既存 `_IncidentProvider` / `_git_repo` /
`_seed_state` fixture を再利用）

5. **agent ABORT の run で incident が抑止される**: `run.log` に
   `failure_event kind=agent_abort` + `workflow_end status=ABORT` を持つ run を組み、
   `RecoveryHandler.run()` を実行して次を検査する。
   - triage コメントは 1 件投稿され、本文が § 方針 2 の契約表どおりであること。すなわち
     `## Workflow failure triage` 見出し・項目表・`## 判断根拠`・次アクション行は従来どおりで、
     `### 原因（機械判定）` 直下に改訂後の `_CAUSE_DESCRIPTIONS["agent_declared_abort"]` が
     出ること（改訂前の文面が残っていないことも併せて検査する）
   - occurrence コメントは 0 件、`provider.searches` / `provider.created` /
     `provider.comment_lists` がすべて空（起票経路に到達しない）
   - `occurrences_path(artifacts_dir)` が存在しない
   - `run.log` に `incident_suppressed` が 1 件（`cause == "agent_declared_abort"` /
     `failed_step` / `reason` 非空）、`incident_recorded` は 0 件
   - `recovery.json` が `incident_suppressed=true` / `incident_suppression_reason=<固定文>` /
     `incident_ref is None` / `incident_action is None` / `decision == "comment_only"`
   → 修正前 Red（現在は incident 起票 + occurrence 追記が起きる）。
6. **cycle exhaust の run で incident が抑止される**: `failure_event kind=cycle_exhausted` の run で
   5 と同型の検査。`decision == "not_resumable"` と、triage 本文に改訂後の
   `_CAUSE_DESCRIPTIONS["cycle_exhausted"]` および `--reset-cycle` の次アクション行
   （`report.py:142-148`）が**両方**残ることを追加で固定する。→ 修正前 Red。
7. **stderr サマリの維持**: 5 / 6 の run で stderr サマリに `--- failure triage ---` 以下の行が
   従来どおり出力されること（抑止対象が incident 記録のみであることの確認）。
8. **除外境界の回帰（既存テスト）**: `test_cli_not_found_dispatch_still_records_incident` が
   引き続き Green であること（`dispatch_failure` は起票・occurrence 追記を継続）。

### Large テスト

不要。理由は `docs/dev/testing-convention.md` § 省略してよい理由の 4 条件に照らして次のとおり。

1. 本変更は module 内定数のみで、外部 API / E2E 経路に新規ロジックを追加しない。
2. 想定される不具合パターン（除外漏れ / 起票継続 / occurrence 追記）は Medium で
   FakeProvider 経由に完全に写せる（実 GitHub 疎通は分岐条件に寄与しない）。
3. 実 API 疎通を足しても新しい回帰シグナルは増えず、`large_forge` の実行コストと
   incident イシューの実起票という副作用のみが増える。
4. 代替として、merge 後に実 run 1 回で incident が起票されないことを確認する項目を
   Issue の「ワークフロー完了後の確認項目」に既に持っている。

### 変更固有検証（恒久テスト化しない）

- `occurrences.jsonl` の掃除: § 方針 3 の手順（hard-link 保全 → filter + atomic swap →
  fd 到達不能 gate → append-only 回収 → 検証）をそのまま実行し、次を Issue コメントへ証跡として
  貼る。
  - 作成した原本 inode の hard link 名（`occurrences.jsonl.pre405-<UTC>`）と、swap 前に `$F` と
    `$OLD` の `device:inode` が一致すること
  - pre-swap fd 遅延 write の決定論的検証で、旧 inode の holder が **1 件 → 0 件**へ遷移し、
    遅延行を append-only で回収したこと
  - 実データ掃除の `OK: BEFORE_N=<実行時件数> DROP_N=<両 exempt cause の件数>
    AFTER_N=<保持件数以上> LOST=0 OLD=<path>` と、検証末尾でも holder が 0 件であること。
    2026-08-25 時点の期待値は `BEFORE_N=15 DROP_N=9 AFTER_N=6`
  - `jq -r '.signature.cause' "$F" | sort | uniq -c` が非 exempt cause のみであること。
    2026-08-25 時点の期待値は `dispatch_failure 6` のみ
  - rollback 検証で原本の全行と並行 append 行が残ること、および torn-write 検証が exit 1 で停止し、
    append-only rollback 後に検証 fixture の agent ABORT 8 行が戻り、`$F.keep.*` が残らないこと

  ローカル運用データであり repo にコミットされないため恒久テストにはしない。
- `make check`（`ruff` / `mypy` / `pytest` 全件）を実装後に実行する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定を伴わない。既存決定（#303 決定 E / F）の範囲内 |
| docs/ARCHITECTURE.md | なし | recovery layer の構成・責務分割は不変 |
| docs/dev/incident-labels.md | **あり** | 新節「第1層が incident 記録しない cause」を追加（4 cause と除外理由）。Issue の完了条件 |
| docs/dev/workflow_guide.md | **あり** | § 第1層の「記録の対象外」が 2 ケース固定で書かれている（L216-227） |
| docs/cli-guides/failure-recovery.ja.md | **あり** | § incident 記録の対象外の cause 表が 2 行（L145-160） |
| docs/cli-guides/failure-recovery.md | **あり** | 同上の英語版（L151-166） |
| docs/reference/ | なし | API 仕様・規約の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #405 本文 | https://github.com/apokamo/kaji/issues/405 | OB-1〜3 / EB-1〜5 / 完了条件 / 重要判断表（人間決定の出典） |
| #392 調査結果コメント | https://github.com/apokamo/kaji/issues/392#issuecomment-5373586286 | 原因・被害・対応策 A/B/C の比較。案 B が churn を悪化させる根拠（8 occurrence → 8 イシュー、step_id のみでは 8 → 6）と、abort category 判定が #303 決定 F に抵触する根拠 |
| #392 PR #404 影響確認コメント | https://github.com/apokamo/kaji/issues/392#issuecomment-5374554607 | `signature.py` 無変更の実査、案 A の前提強化、実装順序の制約 |
| 第1層 設計正本 | `draft/design/issue-304-1-incident.md`（特に L381） | 決定 E: 「`step_id` / issue 番号 / workflow 名は署名キーに入れない」。`ambiguous_worktree_abort` 案の反証根拠 |
| 署名実装 | `kaji_harness/recovery/signature.py:35,62,68,144-150,163-176` | `_NO_ERROR_TEXT` フォールバック、`_ISSUE_REF_RE` / `_LONG_NUM_RE` の対象、canonical input が 2 フィールドのみであること |
| 除外分岐の実装 | `kaji_harness/recovery/handler.py:520-538` | 「`append_occurrence` より前に抜ける（`occurrences.jsonl` は backfill の入力でもあるため、1 行でも残すと後から incident を再生成しうる）」= EB-5 の根拠 |
| cause 説明の正本 | `kaji_harness/recovery/report.py:44-51` | 両 cause を「安全弁の正常作動」「安全停止・手動確認要求」と定義 = EB-1 / EB-2 の根拠 |
| 既存不変条件 | `tests/test_recovery_models.py:101-105` | `set(INCIDENT_SUPPRESSION_REASONS) == set(INCIDENT_EXEMPT_CAUSES)` = EB-3 |
| 実運用 occurrence データ | `/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl`（`.gitignore:49` により非追跡） | OB-2 の 15 行の内訳と、EB-4 golden hash の出所。本設計 § OB-2 に全行を転記済み（レビュワーが repo 内で内容を参照できるようにするため） |
| 除外機構の前例 | `draft/design/issue-322-feat-tmux-interactive-runner-incident.md` / `draft/design/issue-403-fix-interactive-workflow-codex-recovery.md:480,527` | `INCIDENT_EXEMPT_CAUSES` へ cause を足す際の変更点一式（`FAILURE_CAUSES` / `INCIDENT_SUPPRESSION_REASONS` / `_CAUSE_DESCRIPTIONS` / `_COMMENT_ONLY_CAUSES`）。今回は cause 自体が既存のため前 2 者と説明文のみが対象 |
| ラベル運用と照合規則 | `docs/dev/incident-labels.md:43-58` | 「closed かつ transient なし → 新規起票し旧イシューへリンク」= OB-3 の偽リグレッションの機序 |
| テスト規約 | `docs/dev/testing-convention.md` | Large 省略の 4 条件、bug の再現テスト必須ルール |
