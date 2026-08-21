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

`/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl` の実測（13 行）:

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

実障害は `dispatch_failure` 5 件のみ。5 issue・6 step にまたがる別物の ABORT 8 件が
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
  `occurrences.jsonl` 追記）の対象外になる。triage コメント・run artifact・console 表示は維持。
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
- **EB-5**: `occurrences.jsonl` から `agent_declared_abort` の 8 行が削除され、将来の backfill で
  incident が再生成されない。
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
print("cycle :", c.fingerprint, c.fingerprint_hash)
PY
```

3. 観測: `design` と `close` の `fingerprint` がともに `<no-error-text>`、`fingerprint_hash` が
   ともに `4ee617ee…`、`matches: True`。step が異なるにもかかわらず同一 incident として照合される。

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
| `cycle_exhausted` | する | 0 件 | 除外する（EB-2） |
| `ambiguous_worktree_abort` | する | 0 件 | **対象外・現状維持**（下記） |

その他の cause（`dispatch_failure` / `verdict_resolution_failure` / `runtime_error` /
`config_or_definition_error` / `unknown_external_error` / `kaji_bug_suspected` /
`user_precondition_error` / `user_interrupted`）は例外を伴うため canonical input が非空になる。
実データ上も `dispatch_failure` の 5 件は `d7b6c1ec` / `6dd57a74` / `5a0e69f4`（×2）/ `82b0c1d7`
の 4 種に正しく分かれており、退化していない。

#### `ambiguous_worktree_abort` を対象外とする技術的根拠

当初案（`_canonical_input()` の fallback に abort reason を足す）は成立しない。

abort reason は `kaji_harness/runner.py:851` の `Verdict.reason` として
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
| triage コメント / stderr サマリ / exit code / `decision` 値 | **不変**（`agent_declared_abort` は `comment_only`、`cycle_exhausted` は `not_resumable` のまま） |

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
triage コメントに `_CAUSE_DESCRIPTIONS[cause]` がそのまま出る（`report.py:193`）ため、
利用者は incident が起票されない理由を triage コメント上で確認できる。

### 3. `occurrences.jsonl` の掃除（ローカルデータ操作）

対象は `/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl`（`kaji run` が
main worktree から起動される際の `artifacts_dir`）。

```bash
cd /home/aki/dev/kaji/main
F=.kaji-artifacts/incidents/occurrences.jsonl
cp "$F" "$F.bak-405"                                   # 復旧用（repo 外に出さず gitignore 下に置く）
jq -c 'select(.signature.cause != "agent_declared_abort")' "$F" > "$F.tmp" && mv "$F.tmp" "$F"
jq -r '.signature.cause' "$F" | sort | uniq -c          # 期待: dispatch_failure 5 件のみ
```

- `jq` の select filter は**冪等**であり、掃除後に新しい ABORT 行が混入しても同じコマンドで
  再収束する。
- **順序の制約**: 掃除と修正 merge の間に main で agent ABORT run が起きると再び 1 行増えうる。
  実装フェーズで掃除して before/after を証跡化し、merge 後の事後確認（Issue の
  「ワークフロー完了後の確認項目」）で cause 分布を再測定し、`agent_declared_abort` が 0 件で
  あることを確かめる。増えていた場合は同じ冪等コマンドを再実行する。
- `cycle_exhausted` の occurrence は現時点で 0 件のため掃除対象に含まれない（filter は
  `agent_declared_abort` のみを落とす）。

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
| `ambiguous_worktree_abort` の扱い | 本 Issue の対象外・現状維持 | AI が技術検証で反証し人間へ報告済み（2026-08-22）。two-way door として先送り | 反証の根拠（`runner.py:851` の reason 文字列と `_ISSUE_REF_RE` / `_LONG_NUM_RE` の非マッチ、#304 設計 L381 の決定 E 違反）を § 根本原因へ転記。再判断の条件（実発生 + 集約による実害）を明記 |
| 一方向性の評価 | 本変更は two-way door | AI の評価（Issue § 重要判断に記載、人間が受領） | 取り消しは frozenset から 2 要素を戻すだけで済み、run artifact の記録形式・署名 schema・既存 hash を変えないことを § 制約で確認 |
| docs 更新範囲を 4 ファイルへ拡大 | Issue が挙げる `incident-labels.md` に加え、除外 cause を「2 つ」と明記している 3 ファイルも更新 | **AI の仮定**。根拠は grep 実測で当該 3 ファイルが cause 数を明記していること（`failure-recovery.ja.md:145-160` / `failure-recovery.md:151-166` / `workflow_guide.md:216-227`）。後段の検査先は review-design と `/i-dev-final-check` の docs 整合確認 | 振る舞いの scope は広げず、記述の事実整合のみを対象とする |
| 抑止理由・説明文の具体的文面 | 既存 2 件の書式（英語小文字始まり + `; excluded from incident recording` / 和文に「incident 起票の対象外」）を踏襲 | **AI の仮定**。根拠は `models.py:90-98` と `report.py:72-76` の既存慣習、および `tests/test_recovery_report.py:170,190` が「incident 起票の対象外」を assert していること。後段の検査先は review-code | 文面を § インターフェースに確定値として記載し、実装時の裁量を残さない |
| `occurrences.jsonl` 掃除の実施タイミングと冪等性 | 実装フェーズで掃除し、merge 後の事後確認で再測定する | **AI の仮定**。根拠は #392 の「実装順序の制約」コメントと `handler.py:522-524` の backfill 依存。後段の検査先は Issue の「ワークフロー完了後の確認項目」 | `jq select` による冪等 filter と before/after 証跡の取り方を § 方針 3 に固定 |

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
3. **triage コメント文面**: `agent_declared_abort` / `cycle_exhausted` の triage 本文に
   `_CAUSE_DESCRIPTIONS[cause]` が含まれ、その文面が「incident 起票の対象外」を含むこと
   （既存の `user_precondition_error` / `user_interrupted` 検査と同型）。→ 修正前 Red。
4. **署名 hash の不変性**（EB-4）: 実運用 occurrence から採取した実データを golden 値として
   pin する。`normalize_error_text()` + sha256 が次を返すことを検査する。

   | 生エラー文字列 | 期待 `fingerprint_hash` | 出所 |
   |---|---|---|
   | `StepTimeoutError: Step 'implement' timed out after 3600s` | `5a0e69f403a1e37cb09cc23e5a40ee64a77501a9655ce37264d4d044dbf0a046` | occurrences.jsonl の run 260730222002 / 260731015910（#391 implement） |
   | `StepTimeoutError: Step 'pr' timed out after 1800s` | `6dd57a743123862400b6b3294be7648c11432f79b681d9e445a64bcab88ddaa3` | 同 run 260715021013（#328 pr） |
   | `CLINotFoundError: interactive terminal runner requires tmux. Run \`kaji run\` inside tmux or use agent_runner='headless'.` | `d7b6c1ecd57db0f730316cf705304375b143c8b6b79394e2e5f9b1aa781ef4cb` | 同 run 260714000453（#314 review-ready） |

   3 件とも本設計作成時に main（`221997d`）で実測し、記録済み hash と一致することを確認済み。
   これは **invariant guard であり回帰テストではない**ため、修正前後どちらでも Green になる。
   意図は「`signature.py` を将来触ったときに既存 incident イシューとの照合が静かに壊れることを
   検出する」ことであり、Red→Green 遷移を求めない理由をテスト docstring に明記する。

### Medium テスト

対象: `tests/test_recovery_incident_handler.py`（既存 `_IncidentProvider` / `_git_repo` /
`_seed_state` fixture を再利用）

5. **agent ABORT の run で incident が抑止される**: `run.log` に
   `failure_event kind=agent_abort` + `workflow_end status=ABORT` を持つ run を組み、
   `RecoveryHandler.run()` を実行して次を検査する。
   - triage コメントは 1 件投稿される（本文は従来どおり）
   - occurrence コメントは 0 件、`provider.searches` / `provider.created` /
     `provider.comment_lists` がすべて空（起票経路に到達しない）
   - `occurrences_path(artifacts_dir)` が存在しない
   - `run.log` に `incident_suppressed` が 1 件（`cause == "agent_declared_abort"` /
     `failed_step` / `reason` 非空）、`incident_recorded` は 0 件
   - `recovery.json` が `incident_suppressed=true` / `incident_suppression_reason=<固定文>` /
     `incident_ref is None` / `incident_action is None` / `decision == "comment_only"`
   → 修正前 Red（現在は incident 起票 + occurrence 追記が起きる）。
6. **cycle exhaust の run で incident が抑止される**: `failure_event kind=cycle_exhausted` の run で
   5 と同型の検査。`decision == "not_resumable"` を追加で固定する。→ 修正前 Red。
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

- `occurrences.jsonl` の掃除: 実行前後で
  `jq -r '.signature.cause' … | sort | uniq -c` を取り、13 行 → 5 行、
  `agent_declared_abort` 8 件 → 0 件を Issue コメントへ証跡として貼る。
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
| 実運用 occurrence データ | `/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl`（`.gitignore:49` により非追跡） | OB-2 の 13 行の内訳と、EB-4 golden hash の出所。本設計 § OB-2 に全行を転記済み（レビュワーが repo 内で内容を参照できるようにするため） |
| 除外機構の前例 | `draft/design/issue-322-feat-tmux-interactive-runner-incident.md` / `draft/design/issue-403-fix-interactive-workflow-codex-recovery.md:480,527` | `INCIDENT_EXEMPT_CAUSES` へ cause を足す際の変更点一式（`FAILURE_CAUSES` / `INCIDENT_SUPPRESSION_REASONS` / `_CAUSE_DESCRIPTIONS` / `_COMMENT_ONLY_CAUSES`）。今回は cause 自体が既存のため前 2 者と説明文のみが対象 |
| ラベル運用と照合規則 | `docs/dev/incident-labels.md:43-58` | 「closed かつ transient なし → 新規起票し旧イシューへリンク」= OB-3 の偽リグレッションの機序 |
| テスト規約 | `docs/dev/testing-convention.md` | Large 省略の 4 条件、bug の再現テスト必須ルール |
