# [設計] 第1層: インシデント検知・集約の自動化と incident ラベル体系

Issue: #304

## 概要

kaji ワークフローの失敗時に、既存 failure triage（`kaji_harness/recovery/`）の直後で障害を
**識別署名 `(failure_cause, exception_type, 正規化エラー指紋)`** で照合し、新規ならインシデント
イシューをテンプレート起票、既存一致なら occurrence コメントを追記して再発回数を marker 件数から
導出する「検知・集約層（第1層）」を追加する。あわせて `incident` ラベル 2 軸（status /
classification）を新設する。**完全純コード（LLM を含めない）・fail-open・at-least-once**。

設計上の全決定は EPIC #303 本文「設計方針（合意済み決定事項）」を正本とする。本設計書は
その決定（決定 D / E / F、照合規則、ラベル 2 軸）を実装可能な契約に具体化したものであり、
矛盾時は #303 が優先する。

## 背景・目的

### ユーザーストーリー

- **kaji 運用者として**、同一障害が再発してもインシデントイシューが乱立せず 1 本に集約され
  再発回数が自動で数えられているために、手動で過去の失敗を突き合わせる作業から解放されたい。
- **kaji 運用者として**、エラー発生時に障害の証拠（redaction 済みログ抜粋・識別署名・発生 run
  情報）が自動で収集・添付されているために、原因調査をゼロから証拠集めせず始めたい。

### 現状の問題（一次情報）

同一障害が `#296` → `#298`(2回目) → `#298`(3回目) と再発した際、重複排除と回数把握を人間が
手動で行った（集約先: `#301`）。3 再発は issue も step も別（#296/`pr-fix`、#298/`implement`×2）
だが、run artifact 上の識別子は 3 件とも同一である:

| run | failure_event | exception_type | エラー冒頭（安定部分） |
|-----|--------------|----------------|------------------------|
| `.kaji-artifacts/296/runs/260711151512/` | `verdict_exception` | `VerdictNotFound` | `No verdict delimiter found in output. Step 3 (AI formatter) skipped to prevent fabrication. Last 500 chars: ...` |
| `.kaji-artifacts/298/runs/260712010554/` | `verdict_exception` | `VerdictNotFound` | 同上 |
| `.kaji-artifacts/298/runs/260712015008/` | `verdict_exception` | `VerdictNotFound` | 同上 |

3 件の差分は `Last 500 chars:` 以降の occurrence 固有 transcript tail のみ。すなわち
「occurrence 固有部分を除去した正規化指紋」で 3 件は同値になる（署名 fixture の正例）。

### 代替案と不採用理由

- **LLM による意味的重複判定**: 第1層が最も働くべき瞬間は LLM 側が壊れている時（#301 の
  3 件はすべて LLM 経路の障害）。LLM 関与は LLM 障害時にこそ重複排除を劣化させるため不採用
  （#303 決定 F）。意味的類似の指摘は第2層（#305）が担う。
- **書き込み時の exactly-once 補償（remote 照会 lock / create 後再走査）**: GitHub に
  uniqueness constraint も atomic upsert も無く保証不能。単一運用者環境で同時起票競合の
  確率は極小であり、「弱い保証（at-least-once）＋安い修復（読み取り時 dedupe）」に倒す
  （#303 決定 F）。
- **時間窓カウンタ / 新規判定機構**: 全失敗を例外なく記録する方針のため不要（#303 決定）。

## インターフェース

### 変更スコープ（モジュール一覧）

| 対象 | 変更 |
|------|------|
| `kaji_harness/recovery/signature.py` | **新規**。識別署名の正規化・算出・あいまい類似（すべて純関数） |
| `kaji_harness/recovery/incident.py` | **新規**。marker 生成/厳格 parse、照合判定（純関数）、テンプレート描画（純関数）、ローカル occurrence 記録、起票/追記 orchestrator |
| `kaji_harness/recovery/models.py` | `RecoveryDecision` に `incident_ref: str \| None` を追加（additive。`RECOVERY_SCHEMA_VERSION` は 1 のまま） |
| `kaji_harness/recovery/handler.py` | triage コメント投稿後に incident 記録を呼ぶ統合（fail-open）、auto-resume 成功時の transient 即クローズ |
| `kaji_harness/recovery/__init__.py` | 新規公開シンボルの re-export |
| `kaji_harness/providers/base.py` | `IncidentSearchCapable` protocol（`search_issues_all`）を追加 |
| `kaji_harness/providers/github.py` | `search_issues_all()`（全件 pagination の incident 検索）を追加 |
| `kaji_harness/logger.py` | `incident_recorded` / `incident_recording_failed` の run.log event を追加 |
| `.github/labels.yml` | incident 2 軸ラベル 8 件を追加 |
| `tests/` | S / M / L（後述） |
| `docs/dev/workflow_guide.md` / `docs/dev/incident-labels.md`（新規） / ほか | 影響ドキュメント参照 |

### 入力

第1層の入力は既存 triage が既に持つ値のみ。新規の外部入力・config 項目は追加しない。

- `FailureSnapshot`（`recovery/snapshot.py`）: `attempt_error` / `workflow_end_error` /
  `run_id` / `failed_step` / `evidence` 等
- `FailureClassification`（`recovery/classify.py`）: `cause`
- `RecoveryDecision`: 再入ガード（`incident_ref`）の読み出し元
- provider（`IssueProvider`）と `artifacts_dir`（ローカル occurrence 記録の置き場所）

### 出力（副作用）

1. **ローカル occurrence 記録**（全 provider・全失敗で必ず）:
   `<artifacts_dir>/incidents/occurrences.jsonl` に 1 行 append
2. **GitHub provider のみ**: インシデントイシュー新規起票 or 既存イシューへの occurrence
   コメント追記（下記照合規則）
3. `recovery.json` の `incident_ref` 更新、`run.log` への `incident_recorded` /
   `incident_recording_failed` event
4. auto-resume が成功（child run `COMPLETE`）し、かつ **この run が起票した** インシデントに
   対する `incident:cause:transient` 付与＋クローズ

### 識別署名（`recovery/signature.py`、純関数）

```python
SIGNATURE_SCHEMA_VERSION = 1
FINGERPRINT_LIMIT = 2000          # 正規化後 canonical text の長さ上限（文字）
SIMILARITY_THRESHOLD = 0.8        # あいまい照合の閾値（助言専用）

@dataclass(frozen=True)
class IncidentSignature:
    schema_version: int           # SIGNATURE_SCHEMA_VERSION
    cause: str                    # FailureClassification.cause
    exception_type: str           # 不明時は "-"
    fingerprint: str              # 正規化・redaction 済み canonical text（人間のデバッグ用）
    fingerprint_hash: str         # sha256 hexdigest（機械可読 marker 用）

def compute_signature(
    snapshot: FailureSnapshot, classification: FailureClassification
) -> IncidentSignature: ...

def normalize_error_text(text: str) -> str: ...   # 正規化パイプライン単体（fixture で固定）

def similarity(a: str, b: str) -> float: ...       # difflib.SequenceMatcher(None, a, b).ratio()
```

**canonical input の優先順位**（#303 決定 E）: `attempt_error` を主とし、空のときのみ
`workflow_end_error` を使う。**連結はしない**（`workflow_end_error` は attempt_error の
wrapper 再掲になりがちで、連結すると wrapper 文言の揺れが指紋を割る）。双方空なら
fingerprint は固定文字列 `"<no-error-text>"`（空指紋でも署名は成立し、
`(cause, exception_type)` のみで照合される）。

**正規化パイプライン**（この順で適用。各段の具体 regex は fixture ベース S テストで固定する）:

1. **redaction**: 既存 `report.mask_secrets()` を必ず先に適用する
   （**hash は redaction 後の text から生成**。署名 marker 経由で secrets が漏れない）
2. ANSI エスケープシーケンス除去
3. traceback のフレーム行（`File "...", line N, in ...`）を除去（例外メッセージ行は残す）
4. **保持 allowlist の保護**: 原因を識別する数値を sentinel 化して 5. の置換から守る
   - 文脈付き数値: `HTTP <n>` / `status [code] <n>` / `exit [code] <n>` / `errno <n>` /
     `code <n>`
   - 単独の既知 HTTP status（`400/401/403/404/408/409/422/429/500/502/503/504/529`）
5. **occurrence 固有値のプレースホルダ置換**（除去 pattern リスト）:
   - run_id（`YYMMDDHHMMSS[-NNN]`）→ `<RUN_ID>`、ISO 8601 時刻 → `<TS>`
   - 絶対パス → `<PATH>`、`#<数字>`（issue 参照）→ `<ISSUE>`、`port <n>` → `port <N>`
   - UUID / 8 桁以上の hex 列 → `<HEX>`、上記以外の 4 桁以上の数字列 → `<N>`
   - **可変 payload の tail**: `Last <n> chars:` 以降を `<TAIL>` に置換
     （#301 の 3 再発を同値にする要の規則。kaji 自身の例外メッセージ構造に対応する
     除去 pattern として fixture で固定する）
6. allowlist sentinel の復元
7. 空白正規化（改行含む連続 whitespace → 単一スペース、前後 strip）
8. `FINGERPRINT_LIMIT` で切り詰め

**署名の同値判定** = `schema_version` / `cause` / `exception_type` / `fingerprint_hash` の
4 値完全一致。schema version 不一致は「一致なし＝新規起票」に倒れる（migration 機構は
作らない。旧インシデントへのリンクは人間が 1 回張れば済む — #303 決定 E）。

**あいまい照合（助言専用）**: 完全一致しなかった候補のうち同一 `exception_type` **または**
同一 `cause` のものに対し `similarity(fingerprint, candidate_fingerprint)` を取り、
`SIMILARITY_THRESHOLD` 以上を score 降順で最大 5 件列挙する。**起票・カウントの判断には
一切使わない**。resolved 済み候補も列挙対象（リグレッション示唆として価値がある）。
候補側 fingerprint はインシデント本文の fenced block（後述）から取得し、block が無い /
読めない候補はあいまい照合の対象から除外する。

### 機械可読 marker（`recovery/incident.py`）

いずれも HTML コメント 1 行・行頭配置。厳格 parse（正規表現完全一致）で読み、
**読めない・破損した marker は「一致なし」に倒す**（誤マージより重複起票の方が修復が安い）。

- **identity marker**（インシデントイシュー本文の 1 行目。照合キー）:

  ```
  <!-- kaji-incident: schema=1 cause=<cause> exception=<exception_type> hash=<sha256hex> -->
  ```

- **occurrence marker**（occurrence コメントの行頭。1 コメントに複数行可 = backfill 用）:

  ```
  <!-- kaji-incident-occurrence: schema=1 hash=<sha256hex> run_id=<run_id> source_issue=<issue_id> -->
  ```

- **fingerprint block**（インシデントイシュー本文内。あいまい照合と人間のデバッグ用）:

  ````
  ```kaji-fingerprint
  <正規化済み canonical text>
  ```
  ````

**再発回数の導出**: 対象イシューの全コメント中、hash が一致する valid occurrence marker の
**ユニーク `run_id` 件数**。可変カウンタは持たない（crash window で同一 run のコメントが
二重投稿されてもカウントが汚れない — at-least-once ＋ 読み取り時 dedupe、#303 決定 F）。

### 照合規則（`plan_incident_action()`、純関数）

候補 = `incident` ラベルで絞った **state=all の全件**（pagination 必須）。identity marker を
厳格 parse し、署名同値の候補を state / ラベルで分岐する:

| 一致candidateの状態 | アクション |
|---|---|
| open | `recur`: occurrence コメント追記 |
| closed かつ `incident:cause:transient` あり | `recur`: occurrence コメント追記（**reopen しない**） |
| closed かつ `incident:cause:transient` なし（人間 resolve 済み） | `create_regression`: 新規起票し本文から旧イシューへリンク（リグレッション検知） |
| 一致なし | `create`: 新規起票 |

- 分岐は上から評価する（open 一致が最優先）。同分岐内に複数一致した場合は **issue 番号最小
  （最古）を追記先に選び**、残りを run.log に記録する（決定論を保つ）。
- `create_regression` の新規イシューは通常の `create` と同型＋「関連」節に旧イシュー参照を持つ。

### イシュー・コメントのテンプレート（純関数、LLM なし）

`report.py` と同じ方針: 自由記述は固定文面、可変部は構造化フィールドの埋め込みのみ。
本文・タイトルに auto-close hazard pattern（`Fixes #N` 等）を含めない
（`docs/dev/shared_skill_rules.md` § auto close keyword 回避規約）。

- **タイトル**: `incident: <cause> / <exception_type> — <fingerprint 先頭 64 字>`
- **本文**: identity marker（1 行目）→ 概要テーブル（cause / exception_type / schema /
  hash / 初回 run_id / 発生元 issue / failed_step / workflow）→ fingerprint block →
  根拠（`sanitize_evidence()` 済みの snapshot evidence ＋ attempt_error 抜粋 ≤500 字）→
  関連の可能性（あいまい候補、score 付き）→ ラベル運用ガイドへのリンク
- **occurrence コメント**: occurrence marker（行頭、backfill 分を含め 1 run_id 1 行）→
  再発情報テーブル（今回 run_id / 発生元 issue / failed_step / 導出した再発回数 N）→
  根拠抜粋 → あいまい候補（あれば）
- **起票時ラベル**: `incident` ＋ `incident:investigating`（status 軸の初期値）

### ローカル occurrence 記録（fail-open の受け皿）

`<artifacts_dir>/incidents/occurrences.jsonl`（append-only、1 行 1 JSON）:

```json
{"schema_version": 1, "signature": {"schema_version": 1, "cause": "...", "exception_type": "...",
 "fingerprint": "...", "fingerprint_hash": "..."}, "run_id": "...", "source_issue": "...",
 "failed_step": "...", "workflow_path": "...", "recorded_at": "<UTC ISO 8601>"}
```

- **全 provider・全失敗で必ず append する**（GitHub 起票の成否と無関係）。非 GitHub provider
  では issue 起票は no-op とし、この記録のみ行う（v1 の provider 契約）。
- 読み取り時、parse できない行は skip する（fail-open）。posted フラグの更新等の
  **書き換えは行わない**（remote marker が「投稿済み集合」の正本）。
- **backfill**: occurrence コメント投稿時、同一署名のローカル記録のうち remote の occurrence
  marker に存在しない `run_id` を同じコメントに marker 行として同梱する。これにより
  「起票失敗 → ローカル記録 → 次回失敗時の照合で拾う」が専用 flush キューなしで成立する。

### provider 拡張（v1: GitHub のみ）

```python
# providers/base.py
@runtime_checkable
class IncidentSearchCapable(Protocol):
    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        """label で絞った Issue を全件 pagination で返す（limit デフォルト依存禁止）。"""

# providers/github.py — GitHubProvider に追加
def search_issues_all(self, *, labels, state="all") -> list[Issue]: ...
```

- 実装は `gh api --paginate repos/{repo}/issues?labels=...&state=...`（GitHub REST
  `GET /repos/{owner}/{repo}/issues`）。このエンドポイントは PR も返すため
  `pull_request` キーを持つ要素を除外する。`gh issue list --limit` は上限依存があるため
  使わない（全件 pagination の契約 — #303 決定 F）。
- incident 記録は `isinstance(provider, IncidentSearchCapable)` かつ `not is_readonly` の
  場合のみ remote 起票・追記に進む。それ以外はローカル記録のみで `skipped_provider`。
- 既存 `edit_issue(add_labels=...)` / `close_issue(reason="completed")` / `comment_issue` /
  `create_issue` をそのまま使う（新規 write API は追加しない）。

### handler 統合と使用例

```python
# handler.run() 内（擬似コード）— triage コメント投稿後、stderr サマリ前に挿入
decision = self._post_triage_comment(decision)
decision = self._record_incident(decision)      # 新規。例外を外に漏らさない（fail-open）
self._record(decision)
self.stderr.write(render_stderr_summary(decision))

# _record_incident の中身（擬似コード）
def _record_incident(self, decision):
    try:
        if prior_recovery_json_has_incident_ref(self.run_dir):   # 再入ガード（同型: triage_comment_ref）
            return decision
        sig = compute_signature(snapshot, decision.classification)
        append_occurrence(self.artifacts_dir, sig, run_id=..., ...)   # 常に実行
        if not isinstance(self.provider, IncidentSearchCapable) or self.provider.is_readonly:
            return decision
        candidates = self.provider.search_issues_all(labels=["incident"], state="all")
        action = plan_incident_action(sig, parse_candidates(candidates))   # 純関数
        outcome = execute_incident_action(self.provider, action, ...)     # create / comment
        self._run_logger.log_incident_recorded(outcome)
        return replace(decision, incident_ref=outcome.incident_ref)
    except Exception as exc:   # fail-open: triage / recovery 判断を一切阻害しない
        self._run_logger.log_incident_recording_failed(exc)
        self.stderr.write(f"WARNING: incident recording failed: {exc}\n")
        return decision
```

**transient 即クローズ経路**: `_resume()` で child run の final_status が `COMPLETE` になった
とき、**この run の incident アクションが `create` / `create_regression`（= 自分が起票した）
だった場合のみ**、`incident:cause:transient` を付与し `incident:investigating` を外して
`close_issue(reason="completed")` する（fail-open）。既存イシューへの `recur` だった場合は
何もしない（集約先の履歴が transient とは限らないため）。閉じた transient インシデントは
照合規則により以後も closed のまま occurrence が追記され、頻発パターンの昇格判断材料になる。

### エラーハンドリング（fail-open 契約）

- `_record_incident` は **いかなる例外も外に漏らさない**。失敗時は run.log
  （`incident_recording_failed`）と stderr WARNING に記録して triage を続行する。
- 起票失敗時の部分回復はしない（ラベルなし再試行等はしない）。ローカル記録が残るため、
  次回の同一署名失敗時に「remote 一致なし → 新規起票 ＋ backfill」で自然に回復する。
- occurrence marker の parse 失敗・fingerprint block 欠落は候補 skip（新規起票側に倒す）。

## 制約・前提条件

- **完全純コード**: 第1層のどの経路にも LLM を含めない（#303 決定 F）
- **fail-open**: 起票・照合の失敗は既存 triage コメント生成・recovery 判断・exit code を
  一切変えない（既存 `_run_failure_triage` の best-effort 契約を保つ）
- **既存 recovery の設計原則の延長**: pure data（snapshot）→ pure classify / plan →
  副作用は handler、という層構造を踏襲する。署名算出・照合判定・テンプレート描画は
  `classify_failure()` と同様に純関数とし S テストで固定する
- **決定 E の署名キー**: `step_id` / issue 番号 / workflow 名は証拠として記録するが
  署名キーに入れない（#301 の 3 再発が 3 本に分裂するため）
- **新規 config / CLI フラグは追加しない**: 既存 `[execution] failure_triage` の配下で動く
  （#288 の「運用安全弁は config 化しない」前提を踏襲）。無効化は triage ごと
  `--no-failure-triage` で行う
- **ラベルは宣言的管理**: `.github/labels.yml` ＋ `labels-sync.yml` が正本。runtime での
  ラベル自動作成はしない。ラベル不在で起票が失敗しても fail-open ＋ ローカル記録で回復する
  （merge 後の labels-sync（main push トリガ）で解消する運用）
- **recovery.json の互換**: `incident_ref` は additive・optional。`from_dict` は欠落時
  `None`（旧 artifact を読める）。`RECOVERY_SCHEMA_VERSION` は 1 のまま
- 依存追加なし（`difflib` / `hashlib` / `re` は標準ライブラリ）

## 方針（Minimal How / データフロー）

```
kaji run 失敗
  └─ RecoveryHandler.run()
       ├─ collect_snapshot → classify_failure → plan_recovery   （既存）
       ├─ recovery.json / run.log / bug issue / triage コメント （既存）
       ├─ ★ _record_incident                                    （新規・fail-open）
       │    ├─ compute_signature（純関数: 正規化→redaction 済み hash）
       │    ├─ append_occurrence（ローカル jsonl、全 provider）
       │    ├─ GitHub のみ: search_issues_all（incident ラベル・全件 pagination）
       │    │    → identity marker 厳格 parse → plan_incident_action（純関数）
       │    ├─ create（新規/リグレッション）or comment（occurrence + backfill markers）
       │    └─ recovery.json.incident_ref 更新・run.log event
       └─ decision: resume → child run 起動 → COMPLETE かつ自分が起票
            → ★ transient 即クローズ（cause:transient 付与＋close）
```

新設モジュールの責務（名前と責務のみ）:

- `recovery/signature.py`: `IncidentSignature` / `compute_signature` / `normalize_error_text`
  / `similarity` — 全て純関数
- `recovery/incident.py`: marker の render/parse、`plan_incident_action`（純関数）、
  イシュー本文・occurrence コメントの render（純関数）、`append_occurrence` /
  `read_occurrences`（I/O 境界）、`execute_incident_action`（provider 呼び出し）

## incident ラベル 2 軸（`.github/labels.yml` へ追加）

| ラベル | 軸 | 付与者 |
|--------|-----|--------|
| `incident` | 種別（検索キー） | 第1層（起票時に必ず） |
| `incident:investigating` | status | 第1層（起票時の初期値）。以降の遷移は人間 |
| `incident:mitigated` | status | 人間 |
| `incident:resolved` | status | 人間 |
| `incident:cause:internal` | classification | 人間（第2層の調査結論を受けて） |
| `incident:cause:upstream` | classification | 人間（同上） |
| `incident:cause:environment` | classification | 人間（同上） |
| `incident:cause:transient` | classification | **第1層が自動付与**（auto-resume 自己回復の即クローズ時） |

遷移の機械強制はしない。2 軸の意味と遷移意図の 1 テーブルは新規運用ガイド
`docs/dev/incident-labels.md` に記載する（配置決定: `docs/dev/labels.md`（既存ラベル運用
ガイド）と同階層に置き、相互リンクする。labels.yml のヘッダコメントの管理対象数も更新する）。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

実行時コード変更（`kaji_harness/recovery/` / `providers/` / `logger.py`）＋ metadata
（`.github/labels.yml`）＋ docs。

### Small テスト（純関数・モック完結）

- **署名正規化 fixture**（`tests/fixtures/incident/`。完了条件の指定項目）:
  - 正例: #301 の 3 再発の実エラーテキスト（`.kaji-artifacts/296/runs/260711151512/` /
    `298/runs/260712010554/` / `298/runs/260712015008/` の run.log 由来）→ 3 件が同一
    `fingerprint_hash` になる
  - 負例: 認証エラー（`401 unauthorized`）と rate limit（`429`）が **別署名に分離**される
    （数値 allowlist が識別的数値を保持する検証）
  - occurrence 固有値の除去: run_id / ISO 時刻 / 絶対パス / `#issue` / `Last N chars:` tail
    が置換され、run が違っても同値になる
  - redaction: token を含むエラーテキストの fingerprint / hash に secrets が残らない
    （`mask_secrets` が hash 生成前に適用される）
  - 空エラーテキスト → `"<no-error-text>"` 指紋で署名が成立する
- **marker の render / 厳格 parse**: round-trip、破損 marker（欠損フィールド・不正 hash 長・
  2 行目以降の引用）が `None` に倒れる
- **`plan_incident_action` の分岐**: open 一致 / closed+transient 一致 / closed 人間 resolve
  一致（→ regression 新規）/ 一致なし / 複数一致時の最小 issue 番号選択 / schema version
  不一致 → 新規 / 読めない identity marker の候補 skip
- **再発回数導出**: 同一 run_id の重複 marker がカウントを増やさない（ユニーク件数）
- **あいまい照合**: 閾値 0.8 の境界、同一 exception_type / cause フィルタ、score 降順、
  起票判断に影響しないこと（助言専用）
- **テンプレート描画**: identity / occurrence marker が行頭 1 行目に来る、auto-close hazard
  pattern（`(Clos|Fix|Resolv|Implement)...#N`）を含まない（既存 report テストと同型の regex 検証）
- `labels.yml` の機械的妥当性は既存 `test_labels_yml.py` が担保（追加分も自動で対象になる）

### Medium テスト（ファイル I/O・内部結合。FakeProvider 使用）

- **occurrence store**: append / read の round-trip、破損行 skip、ディレクトリ自動作成
- **handler 統合（新規起票経路)**: 失敗 run artifact ＋ FakeProvider → incident 起票・
  ラベル `incident`+`incident:investigating`・`recovery.json.incident_ref` 更新・
  run.log `incident_recorded` を検証
- **再発経路**: 既存 incident（identity marker 持ち）を FakeProvider に置き、occurrence
  コメント追記・回数導出・reopen しないことを検証
- **crash window の再実行テスト**（完了条件の指定項目）: remote への occurrence 投稿成功後、
  `recovery.json` 保存前に中断したと仮定した状態（remote に marker あり・local の
  `incident_ref` なし）で handler を再実行 → occurrence コメントは再投稿されうるが
  ユニーク run_id 件数（再発回数）が汚れないこと
- **再入ガード**: `recovery.json.incident_ref` が既にある run への handler 再入
  （`kaji recover` 相当）で remote 投稿がスキップされること
- **fail-open**: FakeProvider が例外を投げても triage コメント・decision・exit code が
  不変で、ローカル occurrence 記録と `incident_recording_failed` event が残ること
- **backfill**: 起票失敗 → ローカル記録のみ → 次回失敗（同一署名）で新規起票と同時に
  過去 run_id の marker が同梱されること
- **transient 即クローズ**: `create` 後に child `COMPLETE` → `cause:transient` 付与＋
  close 呼び出し。`recur` 後の child `COMPLETE` → close されないこと
- **非 GitHub provider（LocalProvider / provider=None）**: 起票 no-op・ローカル記録のみ

### Large テスト

- **large_local（subprocess あり・ネットワークなし）**:
  - PATH 上の stub `gh` 実行ファイルを使い、`GitHubProvider.search_issues_all` の
    `gh api --paginate` 呼び出し契約（引数・PR 除外・JSON parse）を実プロセス境界で検証
  - 既存 `test_recovery_e2e_large_local.py` の系で、失敗 run E2E 後に
    `<artifacts_dir>/incidents/occurrences.jsonl` が生成されることを検証
- **large_forge（実 GitHub API 疎通）は追加しない**。理由: このテストは production repo に
  実インシデントイシュー・ラベルを作成する破壊的副作用を持ち、CI で再現可能な隔離対象
  リポジトリを現状持たない（物理的に安全に作成不可）。remote 契約は stub `gh` の
  large_local と FakeProvider の Medium で二重に固定しており、既存 recovery テスト構成
  （`test_provider_guard_large_local.py` の「large_forge は別途」方針）とも整合する。

### 変更固有検証

- `.github/labels.yml` 追加分: `pytest tests/test_labels_yml.py` ＋ merge 後の
  labels-sync workflow 実行結果の目視確認（恒久テスト化しない: 同期は GitHub Actions の
  責務で、既存ゲート `test_labels_yml.py` が機械的妥当性を捕捉済み）
- docs: `make verify-docs`

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/dev/workflow_guide.md` | **あり** | § failure triage に第1層（インシデント検知・集約）の動作・triage との関係を追記（完了条件の指定項目） |
| `docs/dev/incident-labels.md` | **あり（新規）** | incident ラベル 2 軸の意味と遷移意図の 1 テーブル、自動付与の範囲、照合規則との関係 |
| `docs/dev/labels.md` | あり | 管理対象ラベル数の更新と incident-labels.md への相互リンク |
| `docs/cli-guides/failure-recovery.ja.md` | あり | triage が残すものに incident 記録（`incidents/occurrences.jsonl` / `incident_ref`）を追記 |
| `docs/adr/` | なし | 新規技術選定なし（標準ライブラリのみ。設計正本は EPIC #303 本文） |
| `docs/ARCHITECTURE.md` | なし | 既存 recovery 層内の拡張でアーキテクチャ構成変更なし |
| `docs/reference/` | なし | コーディング規約・API 規約への影響なし |
| `AGENTS.md` / `CLAUDE.md` | なし | プロジェクト規約の変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| EPIC #303 本文「設計方針（合意済み決定事項）」 | GitHub Issue #303（`kaji issue view 303`） | 決定 E（署名 3 つ組・数値 allowlist・redaction 後 hash・schema version）、決定 F（純コード・fail-open・at-least-once・marker 件数導出・全件 pagination・v1 GitHub のみ）、照合規則、ラベル 2 軸。本設計書の正本 |
| #301（手動集約の実施記録） | GitHub Issue #301 | 3 再発を 1 本に手動集約した実例。署名キーに step/issue を入れない根拠（#296/`pr-fix`、#298/`implement`×2） |
| 発生 run artifact | `.kaji-artifacts/296/runs/260711151512/` / `.kaji-artifacts/298/runs/260712010554/` / `.kaji-artifacts/298/runs/260712015008/` | 3 件とも `failure_event.kind=verdict_exception` / `exception_type=VerdictNotFound`、エラー本文は `No verdict delimiter found ... Last 500 chars:` 以降のみ相違（正規化 fixture の正例。本設計書 § 背景に転記済み） |
| 既存 recovery 実装 | `kaji_harness/recovery/`（`classify.py` / `snapshot.py` / `handler.py` / `report.py` / `models.py`） | pure classify → 副作用は handler の層構造、`sanitize_evidence` / `mask_secrets`、`triage_comment_ref` 再入ガード、best-effort 契約（`cli_main._run_failure_triage`）。本機能はこの延長として設計 |
| GitHub REST API: List repository issues | https://docs.github.com/en/rest/issues/issues#list-repository-issues | 「GitHub's REST API considers every pull request an issue」— PR が混入するため `pull_request` キーで除外する設計根拠。`labels` / `state` / pagination パラメータの仕様 |
| gh CLI: `gh api --paginate` | https://cli.github.com/manual/gh_api | `--paginate` が「Make additional HTTP requests to fetch all pages of results」— `gh issue list --limit` のデフォルト依存を避け全件取得する手段 |
| Python `difflib.SequenceMatcher` | https://docs.python.org/3/library/difflib.html | `ratio()` が 0..1 の類似度を返す（「a measure of the sequences' similarity as a float in the range [0, 1]」）。あいまい照合（助言専用・閾値 0.8）の実装基盤 |
| Python `hashlib` | https://docs.python.org/3/library/hashlib.html | sha256 hexdigest による fingerprint hash 生成 |
| Sentry: Fingerprints / grouping | https://docs.sentry.io/concepts/data-management/event-grouping/ | 「Events with the same fingerprint are grouped into a single issue」— 可変部を正規化した指紋でイベントを 1 イシューに集約するクラッシュ集約系の先行事例（正規化指紋の考え方の出典） |
| 上流不具合 | https://github.com/anthropics/claude-code/issues/59864 | #301 の真因（`claude -p` の background lifecycle 問題）。第1層が「LLM 障害時にも動く純コード」であるべき根拠の実例 |
| GitHub labels 標準化 | `docs/rfc/github-labels-standardization.md` / `.github/labels.yml` / `docs/dev/labels.md` | ラベルは labels.yml 宣言 ＋ labels-sync 同期が正本という既存運用。incident ラベルも同機構に載せる根拠 |
