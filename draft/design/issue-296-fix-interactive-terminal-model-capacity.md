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

### sensitive gate との相互作用（設計上の要注意点）

`recovery/handler.py:_safety_gates` は candidate 判定後に `_sensitive_failure_text(snapshot.failure_error_text)` を評価する。パターンに `re.compile(r"(?i)\btoken\b")` があり、capacity shutdown が末尾に出す **`Token usage:`（単数 "Token"）は `\btoken\b` に一致する**。もし診断メッセージに末尾テレメトリをそのまま含めると、この gate が誤発火して auto recovery（EB 3）を阻害する。

> 既存テスト `tests/test_recovery_plan.py::test_rate_limit_token_quota_text_is_not_treated_as_credential_leak` は `"...30000 input tokens per minute"`（複数形 "tokens"）が `\btoken\b` に **一致しない**ことを前提に candidate 予約を守っている。しかし `"Token usage:"` は単数形で一致してしまう。

このため本設計は「診断抽出は provider エラー行に**焦点化**し、`CLIExecutionError` メッセージ（= classification / sensitive gate が読む `attempt_error` / `workflow_end_error`）には焦点化行のみを載せる。ノイズを含む raw/clean tail は sensitive gate が読まない `pane-metadata.json` にのみ書く」という分離を採用する（下記「方針」参照）。sensitive gate 自体の pattern 変更は行わない（スコープ外・回帰リスク）。

## インターフェース

bug 修正のため公開 CLI/IF は不変。内部関数のみ変更・追加する。

### 追加: 構造化診断（純データ + 純関数）

```python
@dataclass(frozen=True)
class TerminalDiagnostic:
    """pane 早期終了時の transcript 診断結果。"""
    kind: str                       # "provider_error" | "no_pattern" | "no_log" | "empty"
    provider_error_line: str | None # ANSI 除去済みの一致行（"provider_error" のみ非 None）
    matched_pattern: str | None     # 一致した transient pattern（cli.py の単一情報源）
    clean_tail: str                 # ANSI/制御除去済み末尾（人間向けコンテキスト）

def extract_terminal_diagnostic(text: str) -> TerminalDiagnostic: ...      # 純関数（Small）
def read_terminal_diagnostic(terminal_log: Path) -> TerminalDiagnostic: ...# 薄い I/O ラッパ
```

- pattern 走査は `kaji_harness.cli.is_transient_error_text` を **再利用**する（`recovery/classify.py` と同じ単一情報源。二重実装しない）。ANSI 除去後に行単位で走査し、最初に `is_transient_error_text(line)` を満たす行を `provider_error_line` にする。
- `kind` は 4 値で provider-side capacity / no_pattern / extraction failure（no_log / empty）を **明示的に区別**し、EB 5 の distinguishability を語彙で保証する。

### 変更: `_terminal_exit_detail(terminal_log: Path) -> str`

戻り値（= `CLIExecutionError.stderr`）を `read_terminal_diagnostic` の `kind` で分岐:

- `provider_error`: `"tmux pane exited before writing verdict.yaml; provider error detected: <provider_error_line>"`
  - capacity 本文（"at capacity" 部分文字列）を含み、`Token usage` テレメトリは含めない → `is_transient_error_text` が候補判定し、sensitive gate は誤発火しない。
- `no_pattern`: `"tmux pane exited before writing verdict.yaml; no known provider error pattern; log tail:\n<clean_tail>"`（人間向けに ANSI 除去済み tail を提示。非候補なので gate 到達しない）。
- `no_log` / `empty`: `"tmux pane exited before writing verdict.yaml; diagnostic unavailable: <reason>"`（extraction failure を明示。非候補）。

### 変更: pane-metadata.json への診断添付

pane 死亡診断を書く経路（`_write_pane_metadata` 相当）で、`TerminalDiagnostic` の全フィールド（`kind` / `provider_error_line` / `matched_pattern` / `clean_tail`）を `terminal_diagnostic` キーとして `pane-metadata.json` に書き込む。`pane-metadata.json` は classifier/sensitive gate の入力ではない（`recovery/snapshot.py` は run.log / result.json / session-state.json / recovery-chain.json / git のみ読む）ため、`Token usage` を含む raw tail を安全に保全でき、EB 1 の「構造化診断を artifact に残す」を満たす。

### 使用例

```python
diag = read_terminal_diagnostic(attempt_dir / "terminal.log")
if diag.kind == "provider_error":
    # CLIExecutionError.stderr に焦点化行が乗り、triage が candidate 判定
    raise CLIExecutionError(step.id, 1, _terminal_exit_detail(terminal_log))
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

## 方針

1. `extract_terminal_diagnostic(text)` を純関数で実装:
   - ANSI CSI/OSC escape と C0/C1 制御文字を除去して行分割（既存汎用 helper は無いので focused な除去関数を用意）。
   - 行ごとに `is_transient_error_text(line)` を評価し、最初の一致行を `provider_error_line`、その一致 pattern を `matched_pattern` に採る。
   - 一致なしなら `kind="no_pattern"` + `clean_tail`（除去済み末尾）。
2. `read_terminal_diagnostic(path)` は no_log / empty を判定してから `extract_terminal_diagnostic` に委譲。
3. `_terminal_exit_detail` を `read_terminal_diagnostic` ベースへ書き換え、`kind` で焦点化メッセージを生成。
4. pane 死亡診断書き込みで `TerminalDiagnostic` を `pane-metadata.json` に添付。
5. classify.py / handler.py / report.py は無改修。テストで「`attempt_error` に capacity → candidate → (auto_recover False) comment_only / (True) resume 予約」を end-to-end に固定する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

実行時コード変更（診断抽出ロジックの新規追加 + `CLIExecutionError` メッセージ生成の変更）。

### Small テスト（純ロジック・外部依存なし）

- `extract_terminal_diagnostic`:
  - **回帰の主眼**: ANSI escape / TUI redraw を含み、capacity 文言が末尾 2000 文字より **前** にある fixture 文字列 → `kind="provider_error"`, `provider_error_line` に "at capacity" を含む, `is_transient_error_text(provider_error_line)` が `True`。修正前の tail-only 相当（末尾 2000 文字のみ）では検出できないことを対比 assert で示す。
  - `kind` 分岐: `no_pattern`（transient 語なし）/ `empty`（空文字）。
  - 既存 transient pattern 一貫性: `rate limit` / `overloaded` を含む行でも `provider_error` になること（cli.py 単一情報源の再利用確認）。
  - ANSI 除去: `provider_error_line` / `clean_tail` に escape 断片が残らないこと。
- `read_terminal_diagnostic`: 存在しない path → `no_log`、空ファイル → `empty`。
- `_terminal_exit_detail`: 各 `kind` に対する焦点化メッセージ生成。特に `provider_error` では メッセージに "at capacity" が含まれ、`Token usage`（`\btoken\b` 誤発火源）が **含まれない** ことを assert（sensitive gate 回帰の予防）。
- `classify._classify_dispatch`: `attempt_error` に capacity 焦点化メッセージ → `recoverability_hint="candidate"`（既存ロジックが忠実 `attempt_error` で成立することの固定）。

### Medium テスト（ファイル I/O・artifact 結合）

- fixture terminal.log（ANSI + 中盤 capacity 行 + 末尾 `Token usage`）を attempt dir に配置し、result.json / run.log を伴う失敗 run を組んで `collect_snapshot → classify_failure → plan_recovery` を通す:
  - `auto_recover=False` → `decision="comment_only"`, `recoverable=True`, reason に auto recovery disabled、resume_command 提示（EB 4）。
  - `auto_recover=True` → `decision="resume"`, `resume_scheduled_at` が `now + 600s`、`resume_from="start"`、sensitive gate（`Token usage` 由来 `\btoken\b`）が発火せず予約が成立（EB 3/6）。budget 二重消費なし（既存 budget guard の確認）。
- `execute_interactive_terminal` の pane 早期終了区別（既存の fake-tmux `subprocess.run` パターンを踏襲）:
  - pane_dead=1 / status=0 / verdict 不在 → `CLIExecutionError`。
  - pane_dead=1 / status 非 0 / verdict 不在 → `CLIExecutionError`。
  - verdict 存在 → 正常終了（`CLIResult`、`CLIExecutionError` 不発）。
  これで「pane exit 0 / 非 0 / verdict 正常」の 3 区別（完了条件）を固定する。

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
| 実 artifact recovery.json | `.kaji-artifacts/137/runs/260711002521/recovery.json` | `recoverability_hint: no` / `not_resumable` の誤分類、evidence も ANSI escape のみ |
| transient pattern 正本 | `kaji_harness/cli.py:33-59`（`_TRANSIENT_PATTERNS` / `is_transient_error_text`） | `"at capacity"` / `"rate limit"` / `"overloaded"` を含む単一情報源。interactive 経路も再利用 |
| interactive pane 診断 | `kaji_harness/interactive_terminal.py:95-102, 289-355` | `_terminal_exit_detail` の tail-only（`text[-2000:]`）と pipe-pane 失敗 / pane 死亡経路 |
| 分類ロジック | `kaji_harness/recovery/classify.py:72-91` | `_classify_dispatch` が `is_transient_error_text(snapshot.attempt_error)` で candidate 判定（無改修で成立） |
| recovery 判定・gate | `kaji_harness/recovery/handler.py:62-70, 109-130, 142-277` | sensitive gate `\btoken\b`、`RECOVERY_WAIT_SECONDS=600`、comment_only/resume 分岐 |
| recovery 定数 | `kaji_harness/recovery/models.py:19-30` | `RECOVERY_BUDGET=1`, `RECOVERY_WAIT_SECONDS=600`, `NON_RESUMABLE_STEPS`（`start` 非含有） |
| snapshot 収集 | `kaji_harness/recovery/snapshot.py:314-316, 296-374` | `attempt_error = result.json.error`、sensitive gate 入力に pane-metadata.json は含まれない |
| sensitive gate 既存テスト | `tests/test_recovery_plan.py:206-212` | `"tokens per minute"`（複数形）が `\btoken\b` 非一致で candidate を守る前提。`"Token usage"` 単数形は一致し得るため焦点化が必要 |
| OpenAI Codex usage limits | https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan | capacity/usage limit が provider 側一時制約であることの一次情報 |
