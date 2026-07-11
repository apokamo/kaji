# [設計] interactive terminal の model capacity エラーを復旧候補として扱う

Issue: #296

## 概要

`kaji run` の interactive terminal 経路で pane が verdict 未書き込みのまま終了したとき、`_terminal_exit_detail` が terminal transcript の **末尾 2000 文字だけ** を診断情報にする。TUI redraw の ANSI 制御文字で埋まった末尾には provider の "Selected model is at capacity" 本文が含まれず、`CLIExecutionError` → `result.json.error` → failure triage の `is_transient_error_text` 判定に capacity 文言が届かない。結果として transient candidate が `recoverability_hint: no` に誤分類される。本設計は「transcript 全体から既存 transient pattern を走査して provider エラー行を構造化診断として残す」ことでこの取りこぼしを塞ぐ。

## 背景・目的

### Observed Behavior（OB）

2026-07-11 JST、`uv run kaji run .kaji/wf/dev-thorough.yaml 137` の `start` step（`agent: codex`, gpt-5.6-luna）で pane が verdict を書かずに終了した。実 artifact（`.kaji-artifacts/137/runs/260711002521/`）で以下を確認済み:

- `steps/start/attempt-001/terminal.log` は **298,389 バイト**。`Selected model is at capacity. Please try a different model.` は **111 行目**（ファイル先頭付近）に存在する。末尾には `Shutting down...` と TUI redraw の ANSI escape、`Token usage: total=32,386 ...` テレメトリが並ぶ。
- `steps/start/attempt-001/result.json` の `error`:
  ```
  CLIExecutionError: Step 'start' CLI exited with code 1: tmux pane exited before writing verdict.yaml; log tail:
  ;65;49mi[38;2;62;64;82;49mn[38;2;101;105;127;49mg[39m ... （以降 ANSI escape のみ）
  ```
  末尾 2000 文字は ANSI escape のみで、capacity 本文を含まない。
- `recovery.json`:
  ```yaml
  classification: {cause: dispatch_failure, recoverability_hint: no}
  decision: not_resumable
  reason: "cause dispatch_failure is not an auto-resume candidate"
  auto_recovery_attempted: false
  ```
  evidence にも ANSI escape だらけの log tail のみが残り、capacity 本文が失われている。

### Expected Behavior（EB）

"Selected model is at capacity" は provider 側の一時的 capacity 不足であり、設定不備・認証失敗・agent の正規 ABORT ではない。既に `kaji_harness/cli.py` は `"at capacity"` を transient pattern として持ち、`kaji_harness/recovery/classify.py:_classify_dispatch` は `is_transient_error_text(snapshot.attempt_error)` を候補判定に使う。したがって interactive terminal 経路でも **同じ情報源** で判定できるべきである。具体的に:

1. pane 終了時、transcript の途中にある provider エラーを失わず、構造化診断として attempt artifact / `CLIExecutionError` に残す。
2. capacity 文言検出時、failure triage は `dispatch_failure` かつ `recoverability_hint: candidate` と判定する。
3. `--auto-recover` 指定 run では、safety gate を通過する場合に限り、既存仕様どおり固定 10 分後（`RECOVERY_WAIT_SECONDS = 600`）の recovery child run を 1 回だけ予約する。
4. `--auto-recover` 未指定なら自動再開せず、Issue コメントと `recovery.json` に「transient candidate だが auto recovery は opt-in で無効」と明示する。
5. provider-side capacity failure / kaji の diagnostic extraction failure / auto recovery disabled を triage report から区別できる。

### 再現手順（Steps to Reproduce）

1. tmux interactive terminal が利用可能な環境で、Codex step（`agent: codex`, interactive dispatch）を使う。実行環境は Codex CLI 0.144.1、モデル gpt-5.6-luna。
2. `uv run kaji run .kaji/wf/dev-thorough.yaml 137` を実行し、`start` step の pane で provider が capacity エラーを返す状況を再現する。
3. artifact を確認する:
   ```console
   rg -n -i "at capacity|Selected model|Shutting down" .kaji-artifacts/137/runs/260711002521/steps/start/attempt-001/terminal.log
   cat .kaji-artifacts/137/runs/260711002521/recovery.json
   ```
4. terminal log には capacity 文言が存在する一方、`recovery.json` は `recoverability_hint: no` / `decision: not_resumable` になる。

> **実装前 Red 証跡（escape clause）**: 上記の実 artifact（terminal.log / result.json / recovery.json）は OB を直接示す実世界障害ログである。恒久回帰テストは、この OB に対応する EB（capacity 検出 → candidate）を fixture で検証する。実ログを実装前 Red 証跡の代替とし、恒久回帰テスト自体（修正後 Green）は省略しない。

## 根本原因（Root Cause）

`kaji_harness/interactive_terminal.py:_terminal_exit_detail`（95-102 行）:

```python
text = terminal_log.read_text(encoding="utf-8", errors="replace").strip()
...
return f"tmux pane exited before writing verdict.yaml; log tail:\n{text[-_TERMINAL_LOG_TAIL_CHARS:]}"
```

- **なぜ間違っているか**: interactive agent は TUI であり、transcript は画面 redraw の ANSI escape で高頻度に肥大する（本件で 298KB）。provider の 1 行エラーは transcript の中盤〜先頭に出て、`kill-pane` 直前の `Shutting down...` / redraw / `Token usage` テレメトリに押し流される。`_TERMINAL_LOG_TAIL_CHARS = 2000` の末尾窓は、この構造では **エラー本文をほぼ確実に取りこぼす**。結果、`CLIExecutionError.stderr`（= `_terminal_exit_detail` の戻り）→ `result.json.error` → `snapshot.attempt_error` に capacity 文言が乗らず、`is_transient_error_text` が `False` を返す。
- **いつから壊れているか**: `_terminal_exit_detail` の tail-only 実装（interactive terminal runner 初出、ADR 007 v2 / Issue #230 系）に内在。failure triage（Issue #288, PR #288/#295）は `attempt_error` を信頼して transient 判定するため、tail-only の欠落がそのまま誤分類として顕在化した。
- **classify.py は既に正しい**: `_classify_dispatch` は `is_transient_error_text(snapshot.attempt_error)` で candidate を判定する。`attempt_error` に capacity 文言が届けば候補判定は **自動的に成立する**。したがって修正の主対象は classify.py ではなく **上流の診断抽出**（`attempt_error` を忠実にする）である。Issue が挙げた `classify.py:_classify_dispatch` は「変更不要（既に transient 判定の単一情報源を参照済み）」であることを本設計で確定させる。
- **headless 経路との差分**: headless runner（`cli.py:execute_cli`）は CLI の stderr/stdout をそのまま `CLIExecutionError.stderr` にし、attempt-level retry も同一 pattern list で判定する。capacity 文言が構造上 stderr に載るため取りこぼさない。interactive 経路のみ「artifact-primary（verdict.yaml トリガ、stdout 非パース）」設計ゆえ、transcript からの抽出が別途必要になる。これが interactive/headless の経路差分の核心。
- **同根の他の壊れ箇所**: `_terminal_exit_detail` は (a) pipe-pane 失敗時（298-303 行）、(b) pane 死亡時（355 行）、(c) `_wrapper_path` 系の pane 死亡診断で共有される。tail-only の欠陥は全経路に波及するため、抽出関数の修正で 3 経路すべてを同時に是正する。

### sensitive gate との相互作用（設計の中核的制約）

`recovery/handler.py:_safety_gates` は candidate 判定後に `_sensitive_failure_text(snapshot.failure_error_text)` を評価する。パターンに `re.compile(r"(?i)\btoken\b")` があり、capacity shutdown が出す **`Token usage:`（単数 "Token"）は `\btoken\b` に一致する**。もしこの文言が classification / sensitive gate の読む text に混入すると、gate が誤発火して auto recovery（EB 3）を阻害する。

> 既存テスト `tests/test_recovery_plan.py::test_rate_limit_token_quota_text_is_not_treated_as_credential_leak` は `"...30000 input tokens per minute"`（複数形 "tokens"）が `\btoken\b` に **一致しない**ことを前提に candidate 予約を守っている。しかし `"Token usage:"` は単数形で一致してしまう。

**実データによる制約の確定（行単位抽出が不可能な理由）**: 実 artifact の terminal.log 111 行目を ANSI/制御除去すると、単一物理行に以下が連結されている:

```
⚠ Selected model is at capacity. Please try a different model. › Shutting down... <制御>Token usage: total=32,386 input=31,159 ...
```

`at capacity`（idx 20）と `Token usage`（idx 95）は **同一物理行に 75 文字差で共存**する。したがって「一致行全体を焦点化行にする」抽出では `Token usage` を必ず巻き込み、`\btoken\b` gate を回避できない（初版設計の致命的欠陥。review-design 指摘 1）。

**採用する焦点化契約（canonical pattern literal のみ / window 調整に依存しない）**: classification と sensitive gate が読む text（`CLIExecutionError.stderr` → `attempt_error` / `workflow_end_error`）には、**transcript の部分文字列を一切載せず、一致した canonical transient pattern の literal のみ**を載せる。すなわち provider_error 時のメッセージは:

```
tmux pane exited before writing verdict.yaml; transient provider error detected (pattern: 'at capacity')
```

- **transient 検出の保証**: メッセージが pattern literal `at capacity` を含むため `is_transient_error_text(attempt_error)` は構造的に `True`（pattern そのものを含む）。sample tuning された window 幅に依存しない。
- **sensitive-safe の保証**: `_TRANSIENT_PATTERNS` の全 literal（`at capacity` / `rate limit` / `overloaded` / `try again` / thinking-block 2 種）は、`_SENSITIVE_FAILURE_PATTERNS`（credential / permission denied / unauthorized / authentication failed / `\btoken\b` / 401 / 403）の **いずれにも一致しない**。よって canonical literal のみを載せる限り gate 誤発火は構造的に起きない（1 サンプルへの依存なし。恒久的保証）。
- **人間向けコンテキストの保全**: `Token usage` を含む ANSI 除去済み excerpt / raw tail は、classifier・sensitive gate が **読まない** `pane-metadata.json`（`terminal_diagnostic` キー）にのみ保存する。`recovery/snapshot.py` の入力は run.log / result.json / session-state.json / recovery-chain.json / git のみで pane-metadata.json を含まないため、ここに `Token usage` があっても gate に到達しない。

sensitive gate 自体の pattern（`\btoken\b`）は変更しない（スコープ外・回帰リスク）。焦点化契約側で誤発火を構造的に排除する。

## インターフェース

bug 修正のため公開 CLI/IF は不変。内部関数のみ変更・追加する。

### 追加: transient pattern 取得 IF（cli.py、単一情報源の非破壊拡張）

`is_transient_error_text` は `bool` のみを返すため、`matched_pattern` を二重実装なしに得る IF が無い（review-design 指摘 2）。これを解消するため、pattern list を唯一の情報源とする取得関数を追加する:

```python
def find_transient_pattern(text: str | None) -> str | None:
    """`_TRANSIENT_PATTERNS` のうち最初に (大小無視で) 一致した literal を返す。無ければ None。"""

def is_transient_error_text(text: str | None) -> bool:
    return find_transient_pattern(text) is not None  # 既存挙動を保ったまま委譲へ書き換え
```

- pattern list（`_TRANSIENT_PATTERNS`）は 1 箇所のまま。`is_transient_error_text` の外部契約（bool 返却）は不変で後方互換。`recovery/classify.py` / `cli.py` の既存呼び出しは影響を受けない。
- `matched_pattern` は `find_transient_pattern` の戻り値をそのまま使う（別 list を持たない）。

### 追加: 構造化診断（純データ + 純関数）

```python
@dataclass(frozen=True)
class TerminalDiagnostic:
    """pane 早期終了時の transcript 診断結果。"""
    kind: str                    # "provider_error" | "no_pattern" | "no_log" | "empty"
    matched_pattern: str | None  # 一致した transient pattern literal（provider_error のみ非 None）
    clean_excerpt: str | None    # 一致 pattern 周辺の ANSI 除去済み抜粋（pane-metadata.json 専用の人間向け。gate 非入力）
    clean_tail: str              # ANSI/制御除去済み末尾（人間向けコンテキスト。pane-metadata.json 専用）

def extract_terminal_diagnostic(text: str) -> TerminalDiagnostic: ...       # 純関数（Small）
def read_terminal_diagnostic(terminal_log: Path) -> TerminalDiagnostic: ... # 薄い I/O ラッパ
```

- 走査は ANSI/制御除去 → 全文に対し `find_transient_pattern(clean_text)` を適用（単一情報源。二重実装しない）。一致すれば `kind="provider_error"`, `matched_pattern=<literal>`。
- `clean_excerpt` / `clean_tail` は **人間向けの参考情報のみ**で、classification / sensitive gate の入力には一切使わない（下記メッセージ契約参照）。`Token usage` 等のノイズを含みうるが、gate が読まない `pane-metadata.json` 専用のため安全。
- `kind` は 4 値で provider-side capacity / no_pattern / extraction failure（no_log / empty）を **明示的に区別**し、EB 5 の distinguishability を語彙で保証する。

### 変更: `_terminal_exit_detail(terminal_log: Path) -> str`

戻り値（= `CLIExecutionError.stderr` → `result.json.error` → `attempt_error` / `workflow_end_error`）を `kind` で分岐。**provider_error では transcript 部分文字列を載せず、canonical pattern literal のみを載せる**（前掲「焦点化契約」）:

- `provider_error`: `"tmux pane exited before writing verdict.yaml; transient provider error detected (pattern: '<matched_pattern>')"`
  - pattern literal を含むため `is_transient_error_text` が候補判定。全 literal は `_SENSITIVE_FAILURE_PATTERNS` 非一致のため sensitive gate 誤発火なし（`Token usage` は載らない）。
- `no_pattern`: `"tmux pane exited before writing verdict.yaml; no known provider error pattern in transcript; log tail:\n<clean_tail>"`（非候補。仮に tail に `Token usage` があっても、非候補は plan_recovery で gate 到達前に返るため無害）。
- `no_log` / `empty`: `"tmux pane exited before writing verdict.yaml; diagnostic unavailable: <reason>"`（extraction failure を明示。非候補）。

### 変更: pane-metadata.json への診断添付

pane 死亡診断を書く経路（`_write_pane_metadata` 相当）で、`TerminalDiagnostic` の全フィールド（`kind` / `matched_pattern` / `clean_excerpt` / `clean_tail`）を `terminal_diagnostic` キーとして `pane-metadata.json` に書き込む。`pane-metadata.json` は classifier/sensitive gate の入力ではない（`recovery/snapshot.py` は run.log / result.json / session-state.json / recovery-chain.json / git のみ読む）ため、`Token usage` を含む excerpt/tail を安全に保全でき、EB 1 の「構造化診断を artifact に残す」を満たす。

### 使用例

```python
diag = read_terminal_diagnostic(attempt_dir / "terminal.log")
# diag.kind == "provider_error", diag.matched_pattern == "at capacity"
# _terminal_exit_detail は pattern literal のみを載せる（transcript 部分文字列なし）
raise CLIExecutionError(step.id, 1, _terminal_exit_detail(terminal_log))
# → "…; transient provider error detected (pattern: 'at capacity')"
#   is_transient_error_text(msg) == True かつ _sensitive_failure_text(msg) == False
```

### 後方互換

- 公開 IF・成功経路（verdict 検出）は不変。変わるのは pane 早期終了時の `CLIExecutionError` メッセージ文面と `pane-metadata.json` の追加キーのみ。
- `classify.py` / `handler.py` / `report.py` は変更なし（`attempt_error` が忠実になれば既存ロジックが要件を満たす）。

## 制約・前提条件

- transient pattern の正本は `kaji_harness/cli.py:_TRANSIENT_PATTERNS` / `is_transient_error_text` 単一。interactive 経路で別 list を持たない。
- `RECOVERY_WAIT_SECONDS = 600`（固定 10 分）、`RECOVERY_BUDGET = 1`（chain 単位）は既存値を変更しない（EB 3/6）。
- sensitive gate の pattern（`\btoken\b` 等）は変更しない。診断の焦点化で誤発火を回避する。
- `start` step の id は `.kaji/wf/dev-thorough.yaml` で `start`。`NON_RESUMABLE_STEPS = {"issue-start", "i-pr", "issue-close"}` に `start` は含まれないため、candidate 化後の `start` は `non_resumable_step` gate で止まらず、他 gate（worktree/branch/provider/sensitive/newer run）通過時に resume 予約される。
- ANSI 除去は端末制御除去に留め、C1/DEL の JSON エスケープ規約（adapters.py, Issue #137）とは目的が別（こちらは可読診断のための除去）。

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/cli.py` | `find_transient_pattern` 追加、`is_transient_error_text` を委譲へ書き換え（外部契約不変） |
| `kaji_harness/interactive_terminal.py` | `TerminalDiagnostic` / `extract_terminal_diagnostic` / `read_terminal_diagnostic` 追加、`_terminal_exit_detail` 書き換え、pane 死亡診断書き込みへ `terminal_diagnostic` 添付 |
| `tests/test_interactive_terminal.py` | 診断抽出・焦点化契約・pane exit 区別のテスト追加 |
| `tests/test_recovery_handler.py`（または新規） | capacity fixture 経由の handler 副作用・budget guard・extraction failure 区別 |
| `tests/test_cli.py` 相当 | `find_transient_pattern` の Small テスト |
| `docs/ARCHITECTURE.md` / `docs/cli-guides/failure-recovery.md` / `docs/reference/configuration.md`（+ `.ja.md`） | 挙動反映 |

`classify.py` / `recovery/handler.py` / `recovery/report.py` / `recovery/snapshot.py` は **無改修**（`attempt_error` 忠実化で既存ロジックが要件を満たす）。sensitive gate pattern も不変。

## 方針

1. `cli.py` に `find_transient_pattern(text) -> str | None` を追加し、`is_transient_error_text` をそれへ委譲（外部契約不変）。
2. `extract_terminal_diagnostic(text)` を純関数で実装:
   - ANSI CSI/OSC escape と C0/C1 制御文字を除去（focused な除去関数を用意）。
   - 除去済み全文に `find_transient_pattern` を適用。一致すれば `kind="provider_error"`, `matched_pattern=<literal>`, `clean_excerpt`（一致箇所周辺の参考抜粋）。
   - 一致なしなら `kind="no_pattern"` + `clean_tail`（除去済み末尾）。
3. `read_terminal_diagnostic(path)` は no_log / empty を判定してから `extract_terminal_diagnostic` に委譲。
4. `_terminal_exit_detail` を `read_terminal_diagnostic` ベースへ書き換え、`kind` で分岐。provider_error は **canonical pattern literal のみ** をメッセージに載せる（transcript 部分文字列は載せない）。
5. pane 死亡診断書き込みで `TerminalDiagnostic` 全フィールドを `pane-metadata.json` に添付。
6. classify.py / handler.py / report.py は無改修。`attempt_error` が忠実になることで既存ロジックが要件を満たすことを、Small〜Medium テストで end-to-end に固定する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

実行時コード変更（診断抽出ロジックの新規追加 + `CLIExecutionError` メッセージ生成の変更）。

### Small テスト（純ロジック・外部依存なし）

- `find_transient_pattern`（cli.py 新 IF）:
  - `at capacity` / `rate limit` / `overloaded` を含む text → 対応 literal を返す。
  - transient 語なし → `None`。`is_transient_error_text` が委譲後も従来どおり bool を返す後方互換。
- `extract_terminal_diagnostic`:
  - **回帰の主眼（実データ同一行の再現）**: 実 artifact 111 行目相当の fixture 文字列（`⚠ Selected model is at capacity. Please try a different model. › Shutting down… <制御>Token usage: total=…` を ANSI/制御込みで再構成し、かつ末尾 2000 文字より前に配置）→ `kind="provider_error"`, `matched_pattern="at capacity"`。修正前の tail-only 相当（末尾 2000 文字のみに `find_transient_pattern`）では `None` になることを対比 assert で示す。
  - `kind` 分岐: `no_pattern`（transient 語なし）/ `empty`（空文字）。
  - transient pattern 一貫性: `rate limit` / `overloaded` でも `provider_error`（cli.py 単一情報源の再利用確認）。
  - ANSI 除去: `clean_excerpt` / `clean_tail` に escape 断片が残らないこと。
- **焦点化契約（sensitive-safe）の直接検証**:
  - 実データ同一行 fixture に対する `_terminal_exit_detail(provider_error)` メッセージが (a) `at capacity` を含み `is_transient_error_text(msg) is True`、(b) `Token usage` を **含まず** `_sensitive_failure_text(msg) is False`（`recovery/handler._sensitive_failure_text` を直接呼ぶ）。これが同一物理行問題への回帰ガードの核心。
  - `_TRANSIENT_PATTERNS` の全 literal が `_SENSITIVE_FAILURE_PATTERNS` のいずれにも一致しないことを網羅 assert（canonical-only 契約の恒久的健全性を固定。将来 pattern 追加時に破れれば即 fail）。
- **false-positive 境界（review-design 指摘 3、`try again` の transcript 全体走査）**:
  - **negative**: transient 語を一切含まない通常完了 transcript（"let me proceed" 等の benign prose）→ `kind="no_pattern"`、`_terminal_exit_detail` メッセージが `is_transient_error_text` に非一致（誤 candidate 化しない）。
  - **characterization**: `try again` を含む transcript → 仕様どおり `provider_error`（pattern: `try again`）になることを固定。この走査は **verdict 未書き込みの異常 pane death 経路でのみ** 実行され、budget=1 + 固定 10 分 gate で blast radius が 1 回の再実行に限定されること、および transient pattern の単一情報源維持のため generic pattern も共有する設計判断をテスト docstring と設計本文に明記する（headless は stderr のみ走査するのに対し interactive は stdout 非パースで transcript が唯一の情報源、という経路差分に起因）。
- `read_terminal_diagnostic`: 存在しない path → `no_log`、空ファイル → `empty`。
- `classify._classify_dispatch`: `attempt_error` に canonical-only メッセージ → `recoverability_hint="candidate"`（既存ロジックが忠実 `attempt_error` で成立することの固定）。

### Medium テスト（ファイル I/O・artifact 結合・handler 副作用）

fixture terminal.log（ANSI + 中盤に実データ同一行の capacity+Token usage 連結 + 末尾 `Token usage`）を attempt dir に置き、result.json / run.log / session-state.json を伴う失敗 run を組む。

- **classify → plan_recovery（EB 2/4/6）**:
  - `auto_recover=False` → `decision="comment_only"`, `recoverable=True`, reason に auto recovery disabled、resume_command 提示（EB 4）。
  - `auto_recover=True` → `decision="resume"`, `resume_scheduled_at == now + 600s`、`resume_from="start"`、sensitive gate（`Token usage` 由来 `\btoken\b`）が **発火せず** 予約成立（EB 3/6）。
- **RecoveryHandler 副作用（review-design 指摘 4。`tests/test_recovery_handler.py` の `_FakeProvider` + injectable `child_launcher` / `sleep` パターンを踏襲。すべて `RecoveryHandler.run()` を実行した handler-level assert）**:
  - **`auto_recover=False`（EB 4、cycle 2 追加）**: capacity fixture で `RecoveryHandler.run()` を実行し、(a) triage コメントが **ちょうど 1 回** 投稿される（`provider.comments` に auto recovery disabled 相当の reason と `resume_command` 提示行が載る。follow-up コメントは無いので計 1 コメント）、(b) `child_launcher` が **0 回**（呼ばれたら記録する spy で `calls == []` を assert）、(c) `recovery.json` が永続化され `decision="comment_only"` / `recoverable=true` / `auto_recovery_attempted=false` / `auto_recovery_attempt_no=0` / `resume_scheduled_at=null` / `resume_command` 非 null であること（`read_recovery_json` で読み戻して assert）。これにより「候補判定だが opt-in 無効で自動再開しない」を handler 副作用レベルで固定する。
  - `auto_recover=True`: triage コメントが child 起動 **前** に 1 回投稿される（provider.comments に `resume_scheduled_at` 行）。`child_launcher` が **ちょうど 1 回** 呼ばれ、child argv に `--from start` / `--recovery-root` / `--recovery-parent` が載る。`recovery.json` に `auto_recovery_attempted=true` / `auto_recovery_attempt_no=1` / `recovery_child_run_id` が書き戻される。child 終了後の follow-up コメントが投稿される（計 2 コメント）。
  - **budget guard（二重消費なし）**: 同一 run に対し handler を 2 度実行 → 2 度目は `snapshot.budget_consumed`（既存 `recovery.json` / child run dir 検出）により `decision="exhausted"`、`child_launcher` は追加で呼ばれない。
  - **extraction failure の区別（EB 5）**: terminal.log 不在の失敗 run → `attempt_error` が `diagnostic unavailable: …` → `recoverability_hint="no"` / `not_resumable`、triage report で capacity candidate / auto-disabled と語彙上区別できることを assert。
- **`execute_interactive_terminal` の pane exit 区別（完了条件、fake-tmux `subprocess.run` パターン踏襲。3 ケースを status 値まで観測可能にする、cycle 2 強化）**:
  - fake-tmux は `display-message` 応答で `pane_dead` と **`pane_dead_status` を独立に** 差し替える（既存 `_PANE_METADATA.replace` を `pane_dead` だけでなく `pane_dead_status` にも適用し、ケースごとに異なる status を返す）。
  - **ケース A** pane_dead=1 / `pane_dead_status=0` / verdict 不在 → `CLIExecutionError` が送出され、書き出された `pane-metadata.json` の `pane_dead_status == "0"` を assert。
  - **ケース B** pane_dead=1 / `pane_dead_status=137`（非 0） / verdict 不在 → `CLIExecutionError` が送出され、`pane-metadata.json` の `pane_dead_status == "137"` を assert。A と B で **status 値が実際に異なる**ことを比較 assert し、0/非 0 の差が観測可能であることを固定する（前回は両者 CLIExecutionError の共通 assert のみで status 差が観測できなかった）。
  - **ケース C** verdict 存在 → 正常終了（`CLIResult`、例外不発）。
  - A/B いずれも `pane-metadata.json` に `terminal_diagnostic` キー（`kind` 等）が記録されることも併せて assert（診断の観測可能性）。

### Large テスト

- 不要。理由（`docs/dev/testing-convention.md` の 4 条件）: 実 provider の capacity 応答は非決定的で再現不能。本件の振る舞い（transcript → 診断 → 分類 → 予約）はすべて fixture + fake-tmux で決定論的に再現でき、Small/Medium で回帰シグナルを完全に確保できる。実 tmux/実 Codex への疎通は既存 `test_recovery_e2e_large_local.py` 系の資産で別途担保され、本修正の回帰検出情報を増やさない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規アーキテクチャ決定なし（既存 transient 単一情報源・recovery 設計を踏襲） |
| docs/ARCHITECTURE.md | あり | interactive terminal の pane 早期終了時に transcript 全体から provider エラーを構造化抽出する診断経路を追記 |
| docs/dev/ | なし | workflow / 開発手順は不変 |
| docs/reference/ | なし | Python 規約・API 契約変更なし |
| docs/cli-guides/failure-recovery.md | あり | interactive terminal の transient failure（capacity）が candidate 化し、`--auto-recover` opt-in で 10 分後 1 回予約 / 未指定時は comment_only になる挙動、extraction failure との区別を追記 |
| docs/reference/configuration.md | あり | auto recovery opt-in の挙動が interactive 経路にも及ぶ旨を反映（完了条件の「configuration reference」。`.ja.md` も対で更新） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 実 artifact result.json | `.kaji-artifacts/137/runs/260711002521/steps/start/attempt-001/result.json` | `error` に "log tail:" 以降 ANSI escape のみで capacity 本文欠落。tail-only 欠陥の直接証拠 |
| 実 artifact terminal.log | `.kaji-artifacts/137/runs/260711002521/steps/start/attempt-001/terminal.log` | 298,389 バイト、capacity 文言が 111 行目（先頭付近）、末尾に `Shutting down...` / `Token usage:` |
| terminal.log 111 行目の ANSI 除去実測 | 同上（本 fix で計測） | 除去後: `⚠ Selected model is at capacity.（idx20）… › Shutting down…（idx65）… Token usage:（idx95）`。capacity と Token usage が同一物理行に 75 文字差で共存 → 行単位抽出不可、canonical-only 契約が必要（review-design 指摘 1 の根拠） |
| 実 artifact recovery.json | `.kaji-artifacts/137/runs/260711002521/recovery.json` | `recoverability_hint: no` / `not_resumable` の誤分類、evidence も ANSI escape のみ |
| transient pattern 正本 | `kaji_harness/cli.py:33-59`（`_TRANSIENT_PATTERNS` / `is_transient_error_text`） | `"at capacity"` / `"rate limit"` / `"overloaded"` を含む単一情報源。interactive 経路も再利用 |
| interactive pane 診断 | `kaji_harness/interactive_terminal.py:95-102, 289-355` | `_terminal_exit_detail` の tail-only（`text[-2000:]`）と pipe-pane 失敗 / pane 死亡経路 |
| 分類ロジック | `kaji_harness/recovery/classify.py:72-91` | `_classify_dispatch` が `is_transient_error_text(snapshot.attempt_error)` で candidate 判定（無改修で成立） |
| recovery 判定・gate | `kaji_harness/recovery/handler.py:62-70, 109-130, 142-277` | sensitive gate `\btoken\b`、`RECOVERY_WAIT_SECONDS=600`、comment_only/resume 分岐 |
| recovery 定数 | `kaji_harness/recovery/models.py:19-30` | `RECOVERY_BUDGET=1`, `RECOVERY_WAIT_SECONDS=600`, `NON_RESUMABLE_STEPS`（`start` 非含有） |
| snapshot 収集 | `kaji_harness/recovery/snapshot.py:314-316, 296-374` | `attempt_error = result.json.error`、sensitive gate 入力に pane-metadata.json は含まれない |
| sensitive gate 既存テスト | `tests/test_recovery_plan.py:206-212` | `"tokens per minute"`（複数形）が `\btoken\b` 非一致で candidate を守る前提。`"Token usage"` 単数形は一致し得るため焦点化が必要 |
| OpenAI Codex usage limits | https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan | capacity/usage limit が provider 側一時制約であることの一次情報 |
