# [設計] verdict 不在の agent 出力に対する AI formatter による PASS 捏造の抑止

Issue: #193

## 概要

`kaji_harness/verdict.py` の 3-stage fallback parser において、agent が `---VERDICT---` ブロックを一切出力せずにセッション終了した場合に、Step 3 の AI formatter が agent の最終発話（進捗報告など）を素材に PASS verdict を捏造する failure mode を抑止する。verdict 不在は明示的に `VerdictNotFound` として扱う。

## 背景・目的

### Observed Behavior (OB)

Issue #184 の `.kaji/wf/full-cycle.yaml` 実行で、`implement` step の agent が verdict を一度も出力せずにセッション終了したにもかかわらず、harness は PASS verdict を `step_end` として記録した。

実装 agent の最終発話（`.kaji-artifacts/184/runs/2605260007/implement/stdout.log` 全 169 行 / assistant text 16 件 / 末尾行）:

```
line 168: baseline 改善確認OK。pytest 完了待ち。
```

stdout 全体に `---VERDICT---` / `---END_VERDICT---` ブロックは存在しない（`ScheduleWakeup` で再起動を期待しつつ pytest を待つ間にメインセッションが終了したケース）。

run.log の該当 `step_end` 記録:

```json
{"event": "step_end", "step_id": "implement", "verdict":
  {"status": "PASS",
   "reason": "Phase A-G の実装完了、品質ゲート（ruff/verify-docs）改善確認、
              pytest baseline clean から続行中",
   "evidence": "config.toml/CLAUDE.md/...修正済み、release-please 資産削除済み..."}}
```

reason 文字列は agent の中間進捗報告（pytest 完了前の状況）と一致しており、`kaji_harness/verdict.py:381` `create_verdict_formatter` が起動する AI formatter が agent 出力末尾の発話を素材に PASS verdict を生成したと推定される。

### Expected Behavior (EB)

agent が verdict ブロックを一切出力しないままセッション終了した場合、harness は PASS（および他の有効 status）verdict を生成すべきでない。verdict 不在は明示的に `VerdictNotFound` として扱い、`runner.run()` 内で `HarnessError` として伝播させ、`cmd_run` で `EXIT_RUNTIME_ERROR (= 3)` にマップする。

根拠:

- **harness 契約**: 「verdict による step 完了報告」が workflow harness の契約（[`docs/dev/workflow_guide.md`](../../docs/dev/workflow_guide.md) / [`docs/dev/skill-authoring.md`](../../docs/dev/skill-authoring.md) § verdict 出力規約）。verdict の無い「黙示的 PASS」は harness 設計外。
- **prompt 伝搬への影響**: `kaji_harness/prompt.py` で前段 verdict の `reason` / `evidence` が次 step に伝搬される（`previous_verdict` 注入）。捏造 verdict の自然言語が後段の判断材料になり、レビュー収束サイクル全体が破綻する。
- **FORMATTER_PROMPT の設計欠落**: `kaji_harness/verdict.py:40-55` の `FORMATTER_PROMPT` は `valid_statuses` に PASS / RETRY / BACK / ABORT を並列に渡す一方で、「verdict 不在」を表現する出力先がない。AI 側は何らかの status を返さざるを得ず、進捗報告から最も近い意味を「捏造」する圧力がかかる。

### 再現手順（steps-to-reproduce）

1. 前提: `feature-development.yaml` または `full-cycle.yaml` を `kaji run` で実行中、verdict を返さない agent セッションが発生する状況（例: `ScheduleWakeup` で再起動を期待しつつ pytest 待機中にメインセッションが終了する Issue #184 のケース）
2. agent stdout には `---VERDICT---` ブロックが存在せず、末尾は進捗報告のみ
3. `kaji_harness/runner.py:374-378` が `parse_verdict(full_output, valid_statuses, ai_formatter=formatter)` を呼ぶ
4. `parse_verdict` の Step 1 (`_extract_block_strict`) / Step 2a (`_extract_block_relaxed`) / Step 2b (`_parse_relaxed_fields`) はすべて失敗
5. Step 3 で `ai_formatter` が起動し、`FORMATTER_PROMPT`（`verdict.py:40-55`）が agent 出力末尾を整形対象として CLI 呼び出し
6. **観測値**: AI が agent の進捗報告を「PASS の verdict」と解釈し、整形済み verdict block を返す。`_parse_formatted_output` が成功し PASS が `step_end` として記録される
7. **期待値**: Step 3 を起動せず `VerdictNotFound` を raise する（or AI が「verdict 不在」を表現する sentinel を返し parser が同等の挙動をする）

## 根本原因（Root Cause）

### なぜ間違っているか

`kaji_harness/verdict.py:40-55` の `FORMATTER_PROMPT`:

```python
FORMATTER_PROMPT = Template(
    "以下の出力から VERDICT を抽出し、正確な YAML フォーマットで出力してください。\n"
    ...
    "## 出力フォーマット（厳密に従ってください）\n"
    "---VERDICT---\n"
    "status: <$valid_statuses_str のいずれか1つ>\n"
    ...
    "重要: status 行は必ず $valid_statuses_str のいずれかを出力してください。それ以外の値は使用禁止です。\n"
)
```

このプロンプトには 2 つの設計欠落がある:

1. **「抽出」表現の前提崩壊**: 「VERDICT を抽出」という表現は「verdict が存在する」前提に立っている。verdict が存在しないケースの挙動が定義されていない。
2. **逃げ場のない status 値域**: status は valid_statuses のいずれかを **必ず** 出力するよう要求している。「verdict 不在」を伝える出力チャネルがないため、AI は valid_statuses のいずれかを選ばざるを得ず、最終発話の文脈から PASS（または曖昧な場合は ABORT）を選ぶ。

また `parse_verdict` 側にも入力ゲートが無い:

```python
# kaji_harness/verdict.py:271-278
# Step 3: AI Formatter Retry
if ai_formatter is None:
    if block is None and relaxed_block is None:
        raise VerdictNotFound(f"No verdict block found. Last 500 chars: {output[-500:]}")
    raise VerdictParseError(
        "All parse attempts failed (Step 1-2). Provide ai_formatter for Step 3 retry."
    )

logger.info("Step 3 (AI formatter) invoked — Steps 1-2 exhausted")
```

`ai_formatter is None` の経路でのみ「verdict block が完全に無い」ことを検出して `VerdictNotFound` を raise している。`ai_formatter is not None` の経路（runner 実運用経路）では、verdict block 不在のままでも Step 3 を起動するため、AI 捏造に晒される。

### いつから壊れているか

3-stage fallback parser は Issue #77 で V5/V6 から復元された機構（commit 履歴は `draft/design/issue-77-verdict-resilient-parsing.md` 参照）。AI formatter による「verdict 不在の捏造リスク」は Issue #77 設計時点で議論されておらず、`previous_verdict` 伝搬経路の安全性（合成文字列が下流を歪める）は Step 2b の reason/evidence 必須化で部分的に対応されたが、Step 3 への入力ゲートは導入されなかった。

### 同根の他経路の調査

`Issue #193` 完了条件「同根の他の経路（agent タイムアウト時 / プロセス kill 時の verdict 扱い）に同様の捏造リスクがないか」への回答:

| 経路 | 挙動 | 捏造リスク |
|------|------|------------|
| **正常終了 + verdict 不在**（本 Issue 対象） | `parse_verdict` が呼ばれ Step 3 で AI 捏造 | **あり**（本 Issue で対処） |
| **`StepTimeoutError` (cli.py で raise)** | runner.py の `try` を抜け `HarnessError` として `cmd_run` で `EXIT_RUNTIME_ERROR` に正規化。`parse_verdict` には到達しない | **なし** |
| **`CLIExecutionError` (非ゼロ終了)** | 同上 | **なし** |
| **`CLINotFoundError` (FileNotFoundError)** | 同上 | **なし** |
| **SIGTERM / SIGKILL 経路** | `cli.py` の subprocess 監視が `StepTimeoutError` を raise（SIGTERM → SIGKILL シーケンス）。`parse_verdict` には到達しない | **なし** |
| **`ai_formatter` 自身の subprocess timeout / 非ゼロ終了** | `formatter()` 内で `VerdictParseError` を raise（`verdict.py:418-426`）。Step 3 の再試行は `max_retries` 回まで、すべて失敗で `VerdictParseError` を raise。捏造 verdict は生成されない | **なし** |
| **`parse_verdict` の Step 3 で AI formatter が空文字を返す** | 同上 (`verdict.py:424-425`) | **なし** |

結論として、捏造リスクは「**プロセスは正常終了しているが verdict が出力されていない**」経路に限定される。例外経路（timeout / kill / non-zero exit / formatter 失敗）はすべて HarnessError として fail-loud に扱われ、捏造 verdict は生成されない。

## インターフェース

`parse_verdict` の公開シグネチャは **変更しない**。後方互換を保つ。

```python
def parse_verdict(
    output: str,
    valid_statuses: set[str],
    *,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> Verdict:
```

戻り値・例外の意味も変更しない。`VerdictNotFound` の発生条件のみ拡張する:

| 条件 | 変更前の挙動 | 変更後の挙動 |
|------|--------------|--------------|
| 出力に verdict marker が一切無い + `ai_formatter` 未提供 | `VerdictNotFound` | （変更なし） |
| 出力に verdict marker が一切無い + `ai_formatter` 提供 | Step 3 起動 → AI が捏造した PASS 等を返す | **`VerdictNotFound`**（Step 3 を起動しない） |
| 出力に verdict marker が存在するが malformed（delimiter のみ / status のみ等） | Step 3 起動（変更なし） | Step 3 起動（変更なし） |

後方互換性: runner.py の呼び出し側は `HarnessError` を `EXIT_RUNTIME_ERROR` にマップする経路を既に持っており（`cli_main.py:385-387`）、新規に発生する `VerdictNotFound` は同経路に乗る。skill 側は「verdict を確実に stdout に出力する」既存規約を満たせば挙動変化なし。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py` | (a) `_has_verdict_marker()` 補助関数を追加し、Step 3 起動前に input gate を導入。(b) `FORMATTER_PROMPT` を強化し、「verdict 不在」用の sentinel 出力を AI に許可する。(c) `_parse_formatted_output()` で sentinel を検出して `VerdictNotFound` を raise する |
| `tests/test_verdict_parser.py` | Small 回帰テスト追加（後述「テスト戦略」参照） |
| `tests/test_verdict_integration.py` | Medium 結合テスト追加（runner 経路で `VerdictNotFound` が `HarnessError` として伝播することを確認） |
| `docs/ARCHITECTURE.md` | Verdict 判定機構セクションの Step 3 説明に「verdict 不在を AI 捏造で穴埋めしない」入力ゲートを追記 |

## 方針（修正アプローチ）

修正は **二重防御** で構成する。AI formatter の振る舞いは原理的に確定的ではないため、入力側ゲート（決定論的）と AI への明示的な指示（緩衝）の両方を導入する。

### 修正点 1: 入力側 verdict marker ゲート（決定論的・主防御）

Step 3 (`ai_formatter`) を起動する前に「出力に verdict 由来の構造的痕跡があるか」を判定する補助関数を追加する:

```python
def _has_verdict_marker(output: str, valid_statuses: set[str]) -> bool:
    """Step 3 起動可否ゲート: 出力に verdict 由来の構造的痕跡が一つでもあるか。

    True を返す条件（いずれか一つでも該当）:
      1. STRICT delimiter `---VERDICT---` を含む（malformed でも block 跡がある）
      2. RELAXED delimiter (`---VERDICT---` の大文字小文字 / 空白揺れ等) を含む
      3. valid_statuses の値を含む status / Result / Status / ステータス キーワード行が
         一つでもある（_build_relaxed_status_patterns と同じパターン集合）

    False の場合、Step 3 を起動せず `VerdictNotFound` を raise する。
    AI formatter が agent の自然言語進捗報告から PASS 等を捏造する経路を
    構造的に閉じる。
    """
```

`parse_verdict` 内での適用箇所:

```python
# Step 3 直前（既存の "if ai_formatter is None" ブロックの直後）
if not _has_verdict_marker(output, valid_statuses):
    raise VerdictNotFound(
        "Step 3 skipped: output contains no verdict marker (delimiter or "
        f"status keyword). Last 500 chars: {output[-500:]}"
    )

logger.info("Step 3 (AI formatter) invoked — Steps 1-2 exhausted")
...
```

ポイント:

- **既存の Step 2b パターン集合（`_build_relaxed_status_patterns`）を再利用** し、status 検出ロジックを二重管理しない。
- valid_statuses 制限により `Status: 200` や `Result = success` 等の無関係文字列は marker 扱いしない（Step 2b と同じ false positive 排除戦略）。
- `_extract_block_strict` / `_extract_block_relaxed` の戻り値（`block` / `relaxed_block`）は既に `parse_verdict` のローカル変数として保持されているため、再度パターンマッチを実行する必要は無い。`_has_verdict_marker` は status キーワード走査のみを行う実装にしてもよい（実装時に最適化判断）。

### 修正点 2: FORMATTER_PROMPT の sentinel 拡張（緩衝・副防御）

`FORMATTER_PROMPT` に「verdict 不在を表現する出力チャネル」を追加する:

```python
FORMATTER_PROMPT = Template(
    "以下の出力から VERDICT を抽出し、正確な YAML フォーマットで出力してください。\n"
    "\n"
    "## 入力\n"
    "$raw_output\n"
    "\n"
    "## 出力フォーマット（厳密に従ってください）\n"
    "---VERDICT---\n"
    "status: <$valid_statuses_str のいずれか1つ>\n"
    'reason: "判定理由"\n'
    'evidence: "判定根拠"\n'
    'suggestion: "次のアクション提案"\n'
    "---END_VERDICT---\n"
    "\n"
    "重要: status 行は必ず $valid_statuses_str のいずれかを出力してください。それ以外の値は使用禁止です。\n"
    "\n"
    "## 例外: verdict が入力に存在しない場合\n"
    "入力に verdict ブロックも status キーワードも含まれない場合は、verdict を捏造せず、\n"
    "以下の sentinel を **単独で** 出力してください。それ以外の本文は付けないでください。\n"
    "\n"
    "---NO_VERDICT_FOUND---\n"
    "\n"
    "agent の中間進捗報告（pytest 待ち / 作業継続中 等）を PASS / ABORT 等の verdict と\n"
    "解釈してはいけません。verdict ブロックが存在しないことそのものが harness への\n"
    "正規の応答です。\n"
)
```

そして `_parse_formatted_output()` 冒頭に sentinel 検出を追加する:

```python
NO_VERDICT_SENTINEL = "---NO_VERDICT_FOUND---"

def _parse_formatted_output(formatted: str, valid_statuses: set[str]) -> Verdict:
    if NO_VERDICT_SENTINEL in formatted:
        raise VerdictNotFound(
            "AI formatter reported no verdict block in agent output"
        )
    # 以下既存ロジック
    ...
```

ポイント:

- AI の選択肢に「verdict 不在」を明示的に与え、捏造圧力を緩和する。
- 修正点 1 のゲートを通過しても（marker は存在するが malformed なケース）、AI が「これは verdict ではない / 捏造に値しない」と判断したら sentinel を返せる。
- sentinel 検出は `VerdictNotFound` への分岐であり、parser の他経路への影響なし。

### 最小侵襲性

- `parse_verdict` の公開シグネチャ・既存 4 経路（Step 1 / 2a / 2b / Step 3 成功）の挙動は不変。
- 影響は「Step 3 入口の追加判定」と「Step 3 成功後の sentinel 分岐」の 2 箇所のみ。
- 他のリファクタは混ぜない（テストの追加とドキュメント更新を除く）。

## テスト戦略

> **CRITICAL**: 本変更は実行時コード変更（`kaji_harness/verdict.py` のロジック変更）であり、Small / Medium のテストを追加する。bug 固有ルールに従い、修正前に Red になる回帰テストを最低 1 本含める。

### 変更タイプ

実行時コード変更（`kaji_harness/verdict.py` のロジック変更）。

### Small テスト

`tests/test_verdict_parser.py` に追加。

**回帰テスト（修正前 Red / 修正後 Green。**bug.md の必須要件**）**:

1. **`test_step3_rejects_output_without_any_verdict_marker`**: Issue #184 の実 stdout（または同等の合成サンプル: agent 自然言語進捗報告のみ、`VERDICT` 文字列および status キーワード一切無し）を入力に `parse_verdict(output, VALID_STATUSES, ai_formatter=mock)` を呼ぶ。
   - 期待: `VerdictNotFound` が raise されること
   - 期待: `mock` formatter が **呼ばれていない** こと（input gate で短絡）
   - 修正前: mock formatter が PASS verdict を返すと parse 成功してしまう → **FAIL**
   - 修正後: VerdictNotFound → **PASS**

2. **`test_step3_invoked_when_delimiter_present_but_malformed`**: `---VERDICT---` delimiter は存在するが YAML が壊れている入力で `parse_verdict` を呼ぶ。
   - 期待: AI formatter が呼ばれること（既存の回復経路）
   - 修正後の入力ゲートで誤検知（false negative）を起こさないことの確認

3. **`test_step3_invoked_when_status_keyword_present_alone`**: delimiter は無いが `Result: PASS` のような有効 status キーワードを含む入力で `parse_verdict` を呼ぶ。
   - Step 2b が reason/evidence 不足で失敗、Step 3 が起動する経路
   - 期待: AI formatter が呼ばれること

4. **`test_step3_skipped_when_only_invalid_status_strings_present`**: `Status: 200`, `Result = success` 等 valid_statuses に含まれない status 風文字列のみを含む入力。
   - 期待: `VerdictNotFound`、AI formatter は呼ばれない（false positive 排除）

5. **`test_formatter_sentinel_response_raises_verdict_not_found`**: `_parse_formatted_output` に sentinel 文字列 `---NO_VERDICT_FOUND---` 単独を渡す。
   - 期待: `VerdictNotFound` が raise されること

6. **`test_formatter_prompt_contains_sentinel_instruction`**: `FORMATTER_PROMPT.template`（または substitute 後の文字列）に sentinel `---NO_VERDICT_FOUND---` の説明が含まれることを確認。
   - prompt の構造的回帰防止

7. **`test_has_verdict_marker_helper_unit`**: `_has_verdict_marker()` に対する単体テスト。
   - True: strict delimiter / relaxed delimiter / 有効 status キーワード（PASS/RETRY/BACK/ABORT 等）
   - False: 空文字 / 自然言語のみ / 無関係文字列のみ

### Medium テスト

`tests/test_verdict_integration.py` に追加。

8. **`test_runner_propagates_verdict_not_found_on_silent_agent_exit`**: 実 runner 経路（mock CLI で stdout 全文を制御）で agent が verdict 不在のまま終了するシナリオを再現。
   - 期待: `runner.run()` が `VerdictNotFound`（= `HarnessError`）を raise すること
   - 期待: `state.last_transition_verdict` に PASS が書き込まれていないこと
   - 既存の `cmd_run` 例外ハンドラ経路（`cli_main.py:385-387`）と組み合わさり `EXIT_RUNTIME_ERROR` にマップされることを `kaji run` 呼び出しレベルで確認するなら CLI 結合テストで補強する（既存の `tests/test_cli_main.py` の patten を踏襲）

### Large テスト

不要。

**省略理由**: 本変更は `parse_verdict` 内のロジック変更であり、実 agent CLI 起動を伴わずに Small / Medium で完全カバー可能。Issue #184 の実 stdout サンプルを fixture 化することで「実出力に対する parser の挙動」も Small レベルで再現できる。`docs/dev/testing-convention.md` の Large 適用基準（実 API / E2E / 外部サービス疎通）に該当しない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存 verdict 判定機構の挙動修正であり、新規技術選定ではない |
| docs/ARCHITECTURE.md | あり | 「Verdict 判定機構」セクション Step 3 説明に「verdict 不在は Step 3 で穴埋めしない」入力ゲートを追記。Issue #77 設計時の 3-stage fallback 説明を補強 |
| docs/dev/workflow_guide.md | 軽微 | 「skill は必ず verdict を stdout に出力する」既存規約の理由として、本 Issue で防いだ捏造リスクを参照リンクで補強（記述追加は最小限。skill-authoring.md 側を主とする） |
| docs/dev/skill-authoring.md | あり | verdict 出力規約セクションに「verdict 不在時は Step 3 で穴埋めされず VerdictNotFound として失敗する」挙動を明記 |
| docs/reference/python/ | なし | 言語規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし（exit code マッピングは既存の `EXIT_RUNTIME_ERROR` を再利用） |
| CLAUDE.md | なし | プロジェクト規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 verdict parser | [`kaji_harness/verdict.py:199-299`](../../kaji_harness/verdict.py) | 3-stage fallback `parse_verdict` 本体。Step 3 起動条件の現状（`ai_formatter is not None` なら無条件起動）が確認できる箇所。本 Issue で input gate を挿入する位置（line 271-279 直後） |
| 現行 FORMATTER_PROMPT | [`kaji_harness/verdict.py:40-55`](../../kaji_harness/verdict.py) | 「VERDICT を抽出」「status は必ず valid_statuses のいずれか」と要求しており、verdict 不在を表現する出力チャネルが無いことの一次根拠 |
| runner 側 parse_verdict 呼び出し | [`kaji_harness/runner.py:367-378`](../../kaji_harness/runner.py) | `valid_statuses = set(current_step.on.keys())` と `create_verdict_formatter` 注入の呼び出しパターン。runner 経路は常に `ai_formatter` 提供のため Step 3 が常時有効 |
| 例外ハンドラ経路 | [`kaji_harness/cli_main.py:382-390`](../../kaji_harness/cli_main.py) | `HarnessError` を `EXIT_RUNTIME_ERROR` (= 3) にマップする。新規 `VerdictNotFound` がこの経路に乗ることの根拠 |
| 関連例外定義 | [`kaji_harness/errors.py:127-137`](../../kaji_harness/errors.py) | `VerdictNotFound` / `VerdictParseError` / `InvalidVerdictValue` の意味定義。「`VerdictNotFound`: 出力に `---VERDICT---` ブロックがない。回復不能」のコメントが本 Issue の方針と整合 |
| Issue #77 設計書 | [`draft/design/issue-77-verdict-resilient-parsing.md`](./issue-77-verdict-resilient-parsing.md) | 3-stage fallback の復元時の設計判断。「`InvalidVerdictValue` のみ即 raise、それ以外は回復対象」「Step 2b で reason/evidence 必須化することで `previous_verdict` 伝搬経路を守る」設計思想は本 Issue の方針と整合する一方、「verdict 不在の捏造リスク」は当時の射程外だったことの根拠 |
| testing-convention | [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) | 実行時コード変更の Small / Medium / Large 適用基準。本 Issue で Large を省略する理由（実 CLI 起動不要、Small fixture で十分カバー）の根拠 |
| bug.md（type 別ガイド） | [`.claude/skills/_shared/design-by-type/bug.md`](../../.claude/skills/_shared/design-by-type/bug.md) | bug 設計の必須セクション構成（OB / EB / steps-to-reproduce / Root Cause / 再現テスト必須）の根拠 |
| Issue #184 実行ログ（OB 一次情報） | `.kaji-artifacts/184/runs/2605260007/run.log` / `.kaji-artifacts/184/runs/2605260007/implement/stdout.log` | OB の一次根拠。`step_end` で PASS が記録された run.log と、対応する stdout に verdict block が存在しない事実（Issue #193 本文参照）。ローカルパスのためレビュー時は Issue #193 本文の引用箇所が二次参照となる |
| 関連 Issue #192 | GitHub Issue #192 | 「`issue-review-code` の Step 1.4 hard gate と BACK semantic が衝突」。本 Issue（#193）の捏造 PASS が下流 step に伝播することで露見した連鎖障害の後段。修正範囲は #192 と分離し、本 Issue は parser 層のみに限定 |
