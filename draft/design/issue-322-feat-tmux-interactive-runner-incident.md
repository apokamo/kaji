# [設計] tmux セッション外での interactive runner 起動失敗を incident 自動起票対象から除外する

Issue: #322

## 概要

interactive terminal runner を選択した状態で tmux セッション外から `kaji run` を起動したときの前提エラーに、専用例外型 `TmuxSessionRequiredError` を与える。failure triage はこの例外型（`run.log` の構造化 field `failure_event.exception_type`）から新 cause `user_precondition_error` を一意に導出し、incident の新規起票・再発追記・ローカル occurrence 追記だけを抑止する。console エラー・run artifact・発生元 Issue への triage コメントは従来どおり維持する。

## 背景・目的

### 現状の問題

`.kaji/config.local.toml` で `agent_runner = "interactive_terminal"` を選んだまま tmux 外から `kaji run` を起動すると、runner は `_resolve_target_pane()` で `$TMUX` 未設定を検出し `CLINotFoundError` で fail-fast する（`kaji_harness/interactive_terminal.py:483-487`）。原因と対処がエラー文に含まれており、ユーザーはその場で tmux 内再実行または headless 切替に移れる。

一方 #304 の第1層は triage 対象の全失敗を例外なく incident に記録する契約であるため、この即時対応可能な操作ミスも incident Issue として自動起票される。run `260714000453` では以下が実際に起きた（一次証跡は § 参照情報）。

- `run.log`: `failure_event kind=dispatch_exception exception_type=CLINotFoundError` → `workflow_end status=ERROR`
- `<artifacts_dir>/incidents/occurrences.jsonl`: 署名 `cause=dispatch_failure / exception_type=CLINotFoundError / hash=d7b6c1e…` の occurrence が 1 行追記
- GitHub: 同署名の identity marker を持つ incident Issue #316 が自動起票

障害調査を要さない既知の前提違反が incident 一覧に混ざると、人手による確認・分類・クローズが必要になり、内部不具合・上流障害の信号が薄まる。

> run artifact（`.kaji-artifacts/`）は `.gitignore` 対象で worktree には存在しないため、レビュー時に参照すべき行は § 参照情報 § 証跡抜粋に全文引用してある。

### ユースケース

- **interactive runner を使うユーザーとして**、tmux 外で誤って起動したときは、その場のエラーを見て tmux 内で再実行するか headless に切り替えたい。incident Issue が増えてほしくない。
- **incident を管理する maintainer として**、調査を要さない既知のユーザー前提エラーを incident へ昇格させず、調査対象の失敗に集中したい。
- **運用証跡を確認する maintainer として**、incident を抑止した run でも、抑止した事実と理由を run artifact（`run.log` / `recovery.json`）から判別したい。

### 代替案と不採否

Issue 本文 § 代替案と採否のとおり（#316 を製品バグ扱い / failure triage 無効化 / `CLINotFoundError` 全体除外 / エラーメッセージ文字列一致 / headless 自動 fallback）はいずれも不採用。本設計はそれらを取らず、「例外型という構造化情報を鍵に、incident 記録だけを抑止する」経路を採る。

判定鍵の候補比較:

| 判定鍵 | 採否 | 理由 |
|--------|------|------|
| 例外型名（`failure_event.exception_type`） | **採用** | `run.log` に既に存在する構造化 field。runner が `type(exc).__name__` で決定論的に書く（`runner.py:903-914`）。message 非依存 |
| `failure_event` への新 field 追加（例 `precondition="tmux_session_required"`） | 不採用 | schema 追加が必要で、既存 run.log との互換分岐が増える。例外型名で同じ識別性が既に得られる |
| classification に直交 flag（`incident_eligible: bool`）を追加 | 不採用 | cause 軸と policy 軸で情報源が二重化する。triage コメント上の cause は `dispatch_failure` のままとなり、人間に「dispatch が壊れた」と誤読させる |

## インターフェース

### 入力

| 層 | 入力 | 型・値 |
|----|------|--------|
| 例外送出（`interactive_terminal._resolve_target_pane`） | 環境変数 `TMUX` | 未設定または空文字のとき `TmuxSessionRequiredError` を送出。`TMUX_PANE` 欠落・tmux 未インストール・version 不足は従来どおり `CLINotFoundError` |
| 分類（`recovery.classify.classify_failure`） | `FailureSnapshot.failure_event.exception_type` | `str \| None`。`"TmuxSessionRequiredError"` のとき新 cause を返す |
| 抑止（`recovery.handler.RecoveryHandler._record_incident`） | `FailureClassification.cause` | `INCIDENT_EXEMPT_CAUSES`（`frozenset[str]`）に含まれるとき incident 記録を行わない |

### 出力

| 出力先 | 内容 |
|--------|------|
| 例外 | `TmuxSessionRequiredError(CLINotFoundError)`。message は現行文言を一字も変えない（"interactive terminal runner requires tmux. Run \`kaji run\` inside tmux or use agent_runner='headless'."） |
| `run.log` | 既存 `failure_event` の `exception_type` が `"TmuxSessionRequiredError"` になる。新 event `incident_suppressed`（field: `cause` / `exception_type` / `failed_step` / `reason`）を 1 件追記 |
| `recovery.json` | 追加フィールド `incident_suppressed: bool`（既定 `false`）・`incident_suppression_reason: str \| None`（既定 `null`）。`incident_ref` / `incident_action` は `null` のまま |
| Issue（発生元） | triage コメントを従来どおり 1 件投稿。cause 表示が `user_precondition_error` になり、固定説明文で「incident 起票の対象外」であることを示す |
| incident Issue / occurrences.jsonl | **何も書かない**（新規起票なし・occurrence コメントなし・`occurrences.jsonl` へ追記なし） |
| exit code / console | 変更なし（`EXIT_RUNTIME_ERROR = 3`、エラー文言は現行のまま） |

### 使用例

```python
# 1. 送出（interactive_terminal._resolve_target_pane 内。$TMUX 未設定時のみ）
raise TmuxSessionRequiredError(
    "interactive terminal runner requires tmux. Run `kaji run` inside tmux "
    "or use agent_runner='headless'."
)

# 2. 分類（recovery/classify.py。runner が run.log へ書いた型名だけを見る）
_USER_PRECONDITION_EXCEPTIONS = frozenset({"TmuxSessionRequiredError"})

def _classify_dispatch(snapshot, exception_type):
    if exception_type in _USER_PRECONDITION_EXCEPTIONS:
        return FailureClassification(
            cause="user_precondition_error",
            synthetic=True,
            source="config",
            recoverability_hint="no",
        )
    ...  # 既存分岐（StepTimeoutError / CLIExecutionError / …）は不変

# 3. 抑止（recovery/handler.py _record_incident の先頭。append_occurrence より前）
if classification.cause in INCIDENT_EXEMPT_CAUSES:
    reason = INCIDENT_SUPPRESSION_REASONS[classification.cause]
    self._run_logger.log_incident_suppressed(
        cause=classification.cause,
        exception_type=snapshot.failure_event.exception_type if snapshot.failure_event else None,
        failed_step=snapshot.failed_step,
        reason=reason,
    )
    return replace(decision, incident_suppressed=True, incident_suppression_reason=reason)
```

`run.log` に追記される event（例）:

```json
{"ts": "...", "event": "incident_suppressed", "cause": "user_precondition_error",
 "exception_type": "TmuxSessionRequiredError", "failed_step": "review-ready",
 "reason": "known user precondition error (interactive terminal runner requires a tmux session); excluded from incident recording"}
```

### エラー・境界

| ケース | 挙動 |
|--------|------|
| `$TMUX` 未設定（本 Issue の対象） | `TmuxSessionRequiredError` → cause `user_precondition_error` → incident 抑止。triage・artifact・console は維持 |
| `$TMUX` あり / `TMUX_PANE` 未設定 | `CLINotFoundError`（既存）→ cause `dispatch_failure` → incident 記録は従来どおり |
| tmux 未インストール | `CLINotFoundError`（既存）→ 同上 |
| tmux < 3.1 | `CLINotFoundError`（既存）→ 同上 |
| その他の `CLINotFoundError`（agent CLI 不在等） | 変更なし。incident 記録を維持 |
| headless runner / tmux 内の正常な interactive runner | 到達しない。挙動不変 |
| `kaji recover` による同一 run への再 triage | artifact（`run.log`）から同じ cause を再導出するため、再実行しても occurrence を書かない（冪等） |
| `log_incident_suppressed` の書き込み失敗 | 既存 `_record_incident` の fail-open（`except Exception` → `incident_recording_failed` を記録し triage 続行）に載る |

## 制約・前提条件

- **例外型名が契約面**: classifier は live class ではなく `run.log` に記録された型名文字列で判定する（既存 `_DISPATCH_EXCEPTIONS` / `_DEFINITION_EXCEPTIONS` と同じ設計）。したがって `TmuxSessionRequiredError` のクラス名変更は artifact 互換の破壊になる。名前は契約として固定する。
- **`CLINotFoundError` のサブクラスであること**が必須。runner の dispatch `except` タプル（`runner.py:872-880`）、cli 層の送出契約、exit code マッピングはすべて `CLINotFoundError` を前提にしており、サブクラス化により fail-fast・retry・終了コードの挙動が保存される。リポジトリ内に `type(exc) is CLINotFoundError` 形式の完全一致比較は存在しない（grep 済み）ため、サブクラス導入で分岐が変わる箇所はない。
- **抑止は `append_occurrence` より前**に置く。`occurrences.jsonl` は backfill の入力（`incident.backfill_entries`）でもあるため、occurrence を 1 行でも残すと後から incident を再生成しうる。
- **cause は signature の構成要素**（`signature.compute_signature`）だが、抑止経路では signature を計算しないため影響しない。既存 incident #316（`cause=dispatch_failure` の marker を持つ）はそのまま残る。その処遇（クローズ等）は人間の運用判断であり本 Issue の scope 外。
- **`_CAUSE_DESCRIPTIONS` は cause の全数辞書**（`report.py:35-69`、`render_triage_comment` が `[cause]` で直接引く）。新 cause の説明文を同時に追加しないと triage コメント生成が `KeyError` で落ち、完了条件「triage コメント維持」を壊す。追加は必須。
- **除外集合は 1 要素に固定する**。`INCIDENT_EXEMPT_CAUSES = {"user_precondition_error"}`、`_USER_PRECONDITION_EXCEPTIONS = {"TmuxSessionRequiredError"}` のいずれも本 Issue では追加しない。他のユーザー操作ミス・設定ミス（`config_or_definition_error` 等）の一般化は Issue の scope 外であり、追加は別 Issue で判断する。
- `recovery.json` の 2 フィールド追加は additive・optional。`RecoveryDecision.from_dict` は `.get()` で既定値を補うため、`RECOVERY_SCHEMA_VERSION` は 1 のまま据え置く（#304 の `incident_ref` 追加と同じ扱い）。
- **観測メモ（設計判断には影響しない）**: run `260714000453` の `run.log` には `incident_recorded` event と 3 件目の `recovery_decision` が無いにもかかわらず、occurrence 追記と incident Issue #316 の起票は完了している。triage 中の外部中断（`KeyboardInterrupt` は `except Exception` を通過する）で説明可能な範囲であり、incident 記録経路が実行された事実は occurrences.jsonl と #316 の identity marker で確定している。本設計は `incident_recorded` event の有無に依存しない。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/errors.py` | `TmuxSessionRequiredError(CLINotFoundError)` を追加 |
| `kaji_harness/interactive_terminal.py` | `_resolve_target_pane()` の `$TMUX` 未設定分岐のみ新例外を送出。docstring の `Raises` を更新。message は不変 |
| `kaji_harness/recovery/models.py` | `FailureCause` に `user_precondition_error` を追加（`FAILURE_CAUSES` も同期）。`INCIDENT_EXEMPT_CAUSES` / `INCIDENT_SUPPRESSION_REASONS` を追加。`RecoveryDecision` に `incident_suppressed` / `incident_suppression_reason` を追加（`to_dict` / `from_dict` 同期） |
| `kaji_harness/recovery/classify.py` | `_USER_PRECONDITION_EXCEPTIONS` を追加し、`_classify_dispatch` の先頭で分岐 |
| `kaji_harness/recovery/handler.py` | `_record_incident` の先頭に抑止ガード |
| `kaji_harness/recovery/report.py` | `_CAUSE_DESCRIPTIONS` に新 cause の固定説明文を追加 |
| `kaji_harness/logger.py` | `log_incident_suppressed()` を追加（event `incident_suppressed`） |
| `tests/` | § テスト戦略のとおり |
| `docs/` | § 影響ドキュメントのとおり |

変更しないもの: runner の dispatch 経路 / fail-fast 契約 / headless fallback（導入しない）/ retry 方針 / exit code / incident ラベル体系 / `plan_recovery` の decision 導出ロジック（新 cause は `_COMMENT_ONLY_CAUSES` に入れないため、従来どおり `not_resumable` に落ちる）。

## 方針

データフローは既存の 3 層をそのまま使い、各層に 1 箇所ずつ最小の分岐を足す。

```
[1] interactive_terminal._resolve_target_pane()
      $TMUX 未設定 → raise TmuxSessionRequiredError(<現行 message>)
        ↓ runner の except（CLINotFoundError を捕捉。サブクラスなので不変）
[2] runner: log_failure_event(kind="dispatch_exception",
                              exception_type=type(exc).__name__)  ← "TmuxSessionRequiredError"
      + result.json / step_end / workflow_end(status=ERROR) は従来どおり
        ↓ RecoveryHandler.run()
[3] classify_failure(snapshot)
      exception_type == "TmuxSessionRequiredError"
        → cause=user_precondition_error / source=config / hint=no
        ↓
    plan_recovery(...) → decision=not_resumable（従来と同じ。cause 説明のみ変わる）
        ↓
    _post_triage_comment(...)   ← 維持（完了条件 2）
        ↓
    _record_incident(...)
      cause ∈ INCIDENT_EXEMPT_CAUSES
        → log_incident_suppressed(...) して return
          （append_occurrence / search_issues_all / create_issue のいずれにも到達しない）
        ↓
    _record(decision) → recovery.json に incident_suppressed=true を保存
```

判定鍵は `exception_type` の 1 トークンであり、raw エラーメッセージの完全一致・部分一致は一切使わない。tmux は session 内で環境変数 `TMUX` を設定する（§ 参照情報）ため、「tmux セッション外」は `$TMUX` の有無という安定した構造化事実として観測できる。その観測結果を例外型に写し、以降の層は型名だけを見る。

## テスト戦略

### 変更タイプ

実行時コード変更（例外送出・分類・triage 副作用の抑止）。恒久回帰テストを追加する。

### Small テスト

外部依存なし・pure 関数と環境変数 patch のみ。

1. **送出点の一意性**（`tests/test_interactive_terminal.py`）
   - `$TMUX` 未設定 → `TmuxSessionRequiredError` が送出され、かつ `isinstance(exc, CLINotFoundError)` が真（既存の捕捉契約が壊れていないこと）。message が現行文言と一致すること。
   - `$TMUX` あり・`TMUX_PANE` 未設定 → `CLINotFoundError` であって `TmuxSessionRequiredError` ではないこと（境界の反証）。
   - tmux 未インストール（`shutil.which` → `None`）/ tmux < 3.1 → `CLINotFoundError` であって新型ではないこと。
2. **分類**（`tests/test_recovery_classify.py`）
   - `failure_event(kind="dispatch_exception", exception_type="TmuxSessionRequiredError")` → `cause=user_precondition_error` / `source=config` / `recoverability_hint=no`。
   - 回帰: `exception_type="CLINotFoundError"` → 従来どおり `dispatch_failure`（除外が波及していないこと）。
3. **decision 導出**（`tests/test_recovery_plan.py`）
   - `cause=user_precondition_error` → `decision=not_resumable` / `recoverable=False`（自動再開しない。従来挙動と同一）。
4. **triage コメント生成**（`tests/test_recovery_report.py`）
   - 新 cause で `render_triage_comment` が `KeyError` を出さず、固定説明文（incident 対象外である旨）を含むこと。
5. **モデル**（`tests/test_recovery_models.py`）
   - `FailureClassification(cause="user_precondition_error", ...)` が構築でき、`FAILURE_CAUSES` と `FailureCause` が同期していること。
   - `RecoveryDecision` の `incident_suppressed` / `incident_suppression_reason` が `to_dict` → `from_dict` で往復すること。既存 JSON（両 field 欠落）から既定値で読めること。

### Medium テスト

ファイル I/O（run artifact 生成・読み取り）と provider spy を伴う結合。

6. **#316 再現ケースの triage 結合**（`tests/test_recovery_incident_handler.py`）
   tmpdir に run artifact（`run.log` = `workflow_start` / `step_start` / `failure_event(exception_type="TmuxSessionRequiredError")` / `step_end` / `workflow_end(status=ERROR)`、`steps/<step>/attempt-001/result.json`、`session-state.json`）を組み、`IncidentSearchCapable` な spy provider で `RecoveryHandler.run()` を実行して次を検証する。
   - triage コメントが 1 件投稿される（`comment_issue` 呼び出し 1 回）。
   - `search_issues_all` / `create_issue` / `list_issue_comments_all` が **一度も呼ばれない**。
   - `<artifacts_dir>/incidents/occurrences.jsonl` が **生成されない / 追記されない**。
   - `run.log` に `incident_suppressed` event が 1 件あり、`cause` / `exception_type` / `failed_step` / `reason` を含む。
   - `recovery.json` が `incident_suppressed=true` / `incident_ref=null` / `decision=not_resumable`。
7. **除外境界の回帰**（同ファイル）
   - 同じ fixture の `exception_type` を `"CLINotFoundError"` に差し替えると、occurrence が 1 行追記され incident 起票経路（`create_issue`）が呼ばれること。既存の incident 記録契約が維持されていることを、除外実装と同一テスト面で固定する。
8. **送出点から artifact への到達**（`tests/test_runner_interactive_dispatch.py`）
   - `agent_runner=interactive_terminal` かつ `$TMUX` 未設定で dispatch させ、生成された `run.log` の `failure_event.exception_type` が `"TmuxSessionRequiredError"` であること。分類が型名文字列に依存する以上、「送出した型名が artifact に正しく載る」ことを固定しないと、classify 側の Small テストが実運用と乖離しうる。

### Large テスト

**追加しない。** 理由（`docs/dev/testing-convention.md` の「省略してよい理由」に対応）:

- 本変更の効果は「provider へ一切到達しないこと」であり、実 GitHub API 疎通では *呼ばれないこと* を証明できない。抑止の観測点は spy provider による呼び出し不在の assertion（Medium 6）が最も直接的で、Large にしても回帰検出情報は増えない。
- 非除外パス（incident 起票が起きる側）の実 API 疎通は既存の `tests/test_recovery_incident_large_local.py` / `tests/test_providers_github_incident.py` が既にカバーしており、本変更はその経路のコードを変えない。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/cli-guides/failure-recovery.ja.md` | **あり** | `incidents/occurrences.jsonl` の「全 provider・全失敗で必ず 1 行追記」（L117）に除外規則の例外を明記。`run.log` の event 一覧（L116）に `incident_suppressed` を追加。`recovery.json` の説明（L114）に新 2 field を追加 |
| `docs/cli-guides/failure-recovery.md` | **あり** | 英語版を同内容で同期（記述の二言語対応を維持） |
| `docs/dev/workflow_guide.md` | **あり** | 第1層の記述（L179-195 付近）に「既知のユーザー前提エラー（`user_precondition_error`）は incident 記録の対象外。triage・artifact は維持」を追記 |
| `docs/cli-guides/interactive-terminal-runner.ja.md` / `.md` | **あり** | tmux 前提・fail-fast の契約は不変。「tmux 外での起動失敗は incident Issue として起票されない」旨を 1 行追記する（利用者が最初に読むガイドで失敗時の扱いを明示するため） |
| `docs/dev/incident-labels.md` | なし | 新ラベルを増やさない。起票しない失敗はラベル体系に載らない |
| `docs/adr/` | なし | 新技術選定なし。ADR 007（tmux 単一 backend）の前提は変更しない |
| `docs/reference/python/` | なし | 規約変更なし |
| `docs/dev/testing-convention.md` | なし | テスト規約変更なし |
| `AGENTS.md` / `CLAUDE.md` | なし | 開発規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 発生 run の `run.log`（一次証跡） | `/home/aki/dev/kaji/main/.kaji-artifacts/314/runs/260714000453/run.log`（gitignore 対象。全文は § 証跡抜粋） | `{"event": "failure_event", "kind": "dispatch_exception", "step_id": "review-ready", "exception_type": "CLINotFoundError", "synthetic": true}` / `{"event": "workflow_end", "status": "ERROR", "error": "CLINotFoundError: interactive terminal runner requires tmux. …"}` → 判定鍵に使える構造化 field が既に存在することの実証 |
| ローカル occurrence 記録 | `/home/aki/dev/kaji/main/.kaji-artifacts/incidents/occurrences.jsonl`（gitignore 対象。全文は § 証跡抜粋） | 当該 run が `{"signature": {"cause": "dispatch_failure", "exception_type": "CLINotFoundError", "fingerprint_hash": "d7b6c1e…"}, "run_id": "260714000453", "source_issue": "314"}` を追記済み → 抑止を `append_occurrence` より前に置く必要があることの実証 |
| 自動起票された incident | Issue #316 本文の identity marker | `<!-- kaji-incident: schema=1 cause=dispatch_failure exception=CLINotFoundError hash=d7b6c1e… -->` → 同一 hash で occurrence と対応。第1層が本失敗を incident に昇格させた事実 |
| 例外送出点 | `kaji_harness/interactive_terminal.py:482-491` | `_resolve_target_pane()`: `if not os.environ.get("TMUX"): raise CLINotFoundError("interactive terminal runner requires tmux. …")`、`TMUX_PANE` 欠落は別 `raise` → `$TMUX` 分岐だけを新型に差し替えれば、他の前提エラーに波及しない |
| runner の失敗記録契約 | `kaji_harness/runner.py:872-914` | `except (StepTimeoutError, CLIExecutionError, CLINotFoundError, …)` で捕捉し、`log_failure_event(kind=…, exception_type=type(exc).__name__)` を書く → サブクラスは同じ except で捕捉され、型名が artifact に載る |
| 分類の型名依存 | `kaji_harness/recovery/classify.py:19-31,72-91` | `_DISPATCH_EXCEPTIONS` / `_DEFINITION_EXCEPTIONS` は例外型名の文字列集合。「reason 文字列マッチには依存しない」という #288 の設計方針と一致する判定鍵 |
| incident 記録の現行契約 | `kaji_harness/recovery/handler.py:456-540` | `_record_incident`: `append_occurrence(self.artifacts_dir, record)  # 常に実行` → 全失敗を例外なく記録する現行契約の所在。ここが唯一の抑止点 |
| triage コメントの cause 全数辞書 | `kaji_harness/recovery/report.py:35-69,184` | `lines.append(_CAUSE_DESCRIPTIONS[classification.cause])` → cause 追加時に説明文を足さないと `KeyError` になる（triage コメント維持の制約） |
| recovery 契約ドキュメント | `docs/cli-guides/failure-recovery.ja.md:114-122` | 「`incidents/occurrences.jsonl` … 全 provider・全失敗で必ず 1 行追記」→ 本変更で明示的な例外を持つ契約に更新する |
| tmux の環境変数仕様 | https://man.openbsd.org/tmux#ENVIRONMENT | tmux は session 内のプロセスに `TMUX` を設定する（`TMUX` はサーバの socket path / pid / session id を保持）。「tmux セッション外」を `$TMUX` の不在で判定できる公式根拠 |

### 証跡抜粋（gitignore 対象 artifact のため全文引用）

`.kaji-artifacts/` は `.gitignore:49` で除外されており worktree には存在しない。レビュワーが検証すべき一次情報を以下に転記する（取得元は上表の絶対パス）。

**run `260714000453` の `run.log`（全 7 行。`ts` を除き原文ママ）**

```jsonl
{"event": "workflow_start", "issue": "314", "workflow": "docs-fable", "schema_version": 1}
{"event": "step_start", "step_id": "review-ready", "agent": "codex", "model": "gpt-5.6-sol", "effort": "medium", "session_id": null, "attempt": 1, "dispatch": "agent"}
{"event": "failure_event", "kind": "dispatch_exception", "step_id": "review-ready", "exception_type": "CLINotFoundError", "cycle_name": null, "synthetic": true}
{"event": "step_end", "step_id": "review-ready", "verdict": {"status": "ABORT", "reason": "step aborted without a usable verdict", "evidence": "interactive terminal runner requires tmux. Run `kaji run` inside tmux or use agent_runner='headless'.", "suggestion": "Inspect attempt-001/result.json and console.log; re-run after addressing the abort cause."}, "duration_ms": 528, "cost": null, "attempt": 1, "exit_code": null, "signal": null, "dispatch": "agent"}
{"event": "workflow_end", "status": "ERROR", "cycle_counts": {}, "total_duration_ms": 528, "total_cost": null, "error": "CLINotFoundError: interactive terminal runner requires tmux. Run `kaji run` inside tmux or use agent_runner='headless'."}
{"event": "recovery_decision", "run_id": "260714000453", "decision": "not_resumable", "recoverable": false, "cause": "dispatch_failure", "synthetic": true, "failed_step": "review-ready", "resume_from": null, "recovery_root_run_id": "260714000453", "recovery_parent_run_id": null, "reason": "cause dispatch_failure is not an auto-resume candidate"}
{"event": "recovery_decision", "run_id": "260714000453", "decision": "not_resumable", "recoverable": false, "cause": "dispatch_failure", "synthetic": true, "failed_step": "review-ready", "resume_from": null, "recovery_root_run_id": "260714000453", "recovery_parent_run_id": null, "reason": "cause dispatch_failure is not an auto-resume candidate"}
```

**`incidents/occurrences.jsonl`（当該 run が追記した 1 行）**

```jsonl
{"schema_version": 1, "signature": {"schema_version": 1, "cause": "dispatch_failure", "exception_type": "CLINotFoundError", "fingerprint": "CLINotFoundError: interactive terminal runner requires tmux. Run `kaji run` inside tmux or use agent_runner='headless'.", "fingerprint_hash": "d7b6c1ecd57db0f730316cf705304375b143c8b6b79394e2e5f9b1aa781ef4cb"}, "run_id": "260714000453", "source_issue": "314", "failed_step": "review-ready", "workflow_path": "/home/aki/dev/kaji/main/.kaji/wf/docs-fable.yaml", "recorded_at": "2026-07-13T15:04:55.102417+00:00"}
```

**Issue #316 の identity marker（本文 1 行目。`kaji issue view 316` で再取得可能）**

```text
<!-- kaji-incident: schema=1 cause=dispatch_failure exception=CLINotFoundError hash=d7b6c1ecd57db0f730316cf705304375b143c8b6b79394e2e5f9b1aa781ef4cb -->
```

occurrence の `fingerprint_hash` と #316 の marker の `hash` が一致する。これが「この失敗が第1層により incident へ昇格した」ことの決定的証跡である。
