# [設計] verdict 不在の agent 出力に対する AI formatter による PASS 捏造の抑止

Issue: #193

## 概要

`kaji_harness/verdict.py` の 3-stage fallback parser において、agent が `---VERDICT---` ブロックを一切出力せずにセッション終了した場合に、Step 3 の AI formatter が agent の最終発話（進捗報告など）を素材に PASS verdict を捏造する failure mode を抑止する。verdict 不在は明示的に `VerdictNotFound` として扱う。

## 背景・目的

### Observed Behavior (OB)

Issue #184 の `.kaji/wf/full-cycle.yaml` 実行で、`implement` step の agent が verdict を一度も出力せずにセッション終了したにもかかわらず、harness は PASS verdict を `step_end` として記録した。

**parser 入力の特定（重要）**: `parse_verdict()` の入力は `CLIResult.full_output` であり、これは `kaji_harness/cli.py` の `stream_and_log()` が JSONL イベントを `CodexAdapter.extract_text()` 等で抽出して連結したテキストである。Issue #184 の `.kaji-artifacts/184/runs/2605260007/implement/console.log`（= adapter 通過後・parser 入力に対応する view）の末尾は以下:

```
baseline 改善確認OK。pytest 完了待ち。
```

`console.log` 全体に `---VERDICT---` / `---END_VERDICT---` ブロックは存在しない（`ScheduleWakeup` で再起動を期待しつつ pytest を待つ間にメインセッションが終了したケース）。

> **証跡区別の注意**: 同 run の raw `implement/stdout.log` は JSONL であり、user prompt イベントには skill SKILL.md に書かれた `---VERDICT---` テンプレートが prompt 内容として含まれる。これは adapter の text 抽出経路（`CodexAdapter.extract_text()` は `agent_message` / `reasoning` / `mcp_tool_call.result.content` のみ抽出。user prompt event は対象外）で除外され、`full_output` には到達しない。本 Issue で議論すべき parser 入力は `console.log`（= `full_output` 相当）であり、`stdout.log` ではない。

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
2. agent の `full_output`（= `console.log` 相当）には `---VERDICT---` delimiter および `status:` / `Status:` / `Result:` 等の status キーワードのいずれも含まれず、末尾は自然言語進捗報告のみ
3. `kaji_harness/runner.py:374-378` が `parse_verdict(full_output, valid_statuses, ai_formatter=formatter)` を呼ぶ
4. `parse_verdict` の Step 1 (`_extract_block_strict`) / Step 2a (`_extract_block_relaxed`) / Step 2b (`_parse_relaxed_fields`) はすべて失敗
5. Step 3 で `ai_formatter` が起動し、`FORMATTER_PROMPT`（`verdict.py:40-55`）が agent 出力末尾を整形対象として CLI 呼び出し
6. **観測値**: AI が agent の進捗報告を「PASS の verdict」と解釈し、整形済み verdict block を返す。`_parse_formatted_output` が成功し PASS が `step_end` として記録される
7. **期待値**: Step 3 を起動せず `VerdictNotFound` を raise する

### 隣接ケース: status キーワード単独（delimiter 不在）

OB 本体は「delimiter も status キーワードも完全に不在」のケースだが、設計レビュー（指摘 1）で「status キーワードのみ含まれる入力（例: agent が `I will set Status: PASS once tests finish` のような自然言語に valid status を埋め込んだ場合）でも Step 3 が起動し捏造リスクが残る」点が指摘された。実 formatter stub で `"Status: PASS\npytest waiting"` を渡すと PASS が生成される事実をもって、本 Issue の対象範囲を「delimiter 不在の全入力」に拡大する（後述「方針」§ 修正点 1）。

`Status:` / `Result:` 等のキーワードは自然言語文中で偶発的に出現しうる一方、`---VERDICT---` delimiter は skill SKILL.md テンプレートに従って agent が **意図的に** 出力したときのみ現れる構造。後者を Step 3 起動の唯一の signal とすることで、natural-language fabrication 経路を構造的に閉じる。

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

戻り値・例外の意味も変更しない。挙動変更点は「Step 3 を起動する境界」を **delimiter 存在のみ** に厳格化することと、それに伴う `VerdictNotFound` の発生条件拡張の 2 点:

| 入力パターン | 変更前の挙動 | 変更後の挙動 |
|--------------|--------------|--------------|
| `---VERDICT---` 完備 + 正常 YAML | Step 1 成功 | （変更なし） |
| delimiter 存在（揺れあり）+ YAML 完備 | Step 2a 成功 | （変更なし） |
| delimiter 存在 + 内部 malformed（YAML 破損 / フィールド欠落） | Step 3 起動 → 回復試行 | Step 3 起動 → 回復試行（変更なし） |
| delimiter 不在 + valid status キーワード + reason/evidence 完備 | Step 2b 成功 | Step 2b 成功（変更なし。delimiter 不在でも reason/evidence が揃えば既存挙動を維持） |
| delimiter 不在 + valid status キーワードのみ（reason/evidence 欠落） | Step 2b 失敗 → Step 3 起動 → AI が穴埋め | **`VerdictNotFound`**（Step 3 を起動しない） |
| delimiter 不在 + status キーワードも一切無し（自然言語のみ） | Step 3 起動（`ai_formatter` 提供時）→ AI が捏造 | **`VerdictNotFound`**（Step 3 を起動しない） |
| delimiter 不在 + `ai_formatter` 未提供 | `VerdictNotFound` または `VerdictParseError` | （変更なし。Step 2b 成功時のみ verdict 返却） |

**ゲート要約**: Step 3 (AI formatter) は **`---VERDICT---` delimiter（strict / relaxed のいずれか）が `_extract_block_strict` または `_extract_block_relaxed` で抽出された場合に限り起動する**。delimiter は skill SKILL.md テンプレートに従う「意図的な verdict 出力」の構造的 signal であり、これを唯一の起動条件とすることで自然言語からの捏造経路を閉じる。

後方互換性: runner.py の呼び出し側は `HarnessError` を `EXIT_RUNTIME_ERROR` にマップする経路を既に持っており（`cli_main.py:385-387`）、新規に発生する `VerdictNotFound` は同経路に乗る。skill 側は「verdict を確実に stdout に出力する」既存規約を満たせば挙動変化なし。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py` | (a) `_has_verdict_marker()` 補助関数を追加し、Step 3 起動前に input gate を導入。(b) `FORMATTER_PROMPT` を強化し、「verdict 不在」用の sentinel 出力を AI に許可する。(c) `_parse_formatted_output()` で sentinel を検出して `VerdictNotFound` を raise する |
| `tests/test_verdict_parser.py` | (a) Small 回帰テスト 7 件を新規追加（後述「テスト戦略」§ Small 新規テスト）。(b) 既存テスト 6 件の入力を marker-less から delimiter-bearing malformed に移行（同 § 既存テストの移行） |
| `tests/test_verdict_integration.py` | Medium 結合テスト追加（runner 経路で `VerdictNotFound` が `HarnessError` として伝播することを確認） |
| `docs/ARCHITECTURE.md` | Verdict 判定機構セクションの Step 3 説明に「verdict 不在を AI 捏造で穴埋めしない」入力ゲートを追記 |

## 方針（修正アプローチ）

修正は **二重防御** で構成する。AI formatter の振る舞いは原理的に確定的ではないため、入力側ゲート（決定論的）と AI への明示的な指示（緩衝）の両方を導入する。

### 修正点 1: delimiter-presence-only gate（決定論的・主防御）

Step 3 (`ai_formatter`) を起動する条件を **「`_extract_block_strict` または `_extract_block_relaxed` で verdict block が抽出された場合のみ」** に厳格化する。status キーワード単独（`Status: PASS` 等の自然言語混入）は Step 3 起動条件に含めない。

**選定理由**: 設計レビュー指摘 1 で示された通り、`Status: PASS` のような valid status を含む自然言語（例: `"I will set Status: PASS once tests finish"`）でも Step 3 を起動すると AI formatter が PASS verdict を構築しうる（formatter stub による実証あり）。一方 `---VERDICT---` delimiter は skill SKILL.md テンプレートに従い agent が **意図的に** 出力する構造であり、natural-language で偶発出現する確率は極小。delimiter 存在を「verdict 出力意図の signal」とすることで、自然言語からの捏造経路を構造的に閉じる。

なお Step 2b 自体は変更しない:

- **Step 2b は delimiter 不在でも reason/evidence が揃えば成功する経路** であり、これは V5/V6 互換性確保のための既存仕様（[`draft/design/issue-77-verdict-resilient-parsing.md`](./issue-77-verdict-resilient-parsing.md) L149-176）。`Status: PASS` + `Reason: ...` + `Evidence: ...` を含む整形済み出力は引き続き Step 2b で成功する
- **Step 2b が「status キーワード発見 + reason/evidence 欠落」で失敗した場合**、変更前は Step 3 へフォールスルーしていたが、変更後は **delimiter が無い限り Step 3 を起動せず `VerdictNotFound` を raise** する

`parse_verdict` 内での適用箇所:

```python
# Step 2b 失敗後、Step 3 直前。既存の "if ai_formatter is None" ブロックを以下に置き換える:

if block is None and relaxed_block is None:
    # delimiter 不在: Step 3 を起動しない（formatter は marker 不在から verdict を
    # 捏造する経路を持つため）。ai_formatter の提供有無に関わらず常に
    # VerdictNotFound として fail-loud に扱う。
    raise VerdictNotFound(
        "No verdict delimiter found in output. Step 3 (AI formatter) skipped "
        f"to prevent fabrication. Last 500 chars: {output[-500:]}"
    )

if ai_formatter is None:
    # delimiter は存在するが Step 1-2 で回復不能 + formatter 未提供
    raise VerdictParseError(
        "All parse attempts failed (Step 1-2). Provide ai_formatter for Step 3 retry."
    )

logger.info("Step 3 (AI formatter) invoked — Steps 1-2 exhausted")
...
```

ポイント:

- 補助関数を新規導入せず、既存ローカル変数 `block` / `relaxed_block` の値で判定する（実装最小化）。
- 「delimiter 存在 + 内部 malformed」のケースは従来通り Step 3 で回復試行する（後方互換）。
- `ai_formatter is None` 経路は従来挙動を維持し、delimiter 存在時のみ `VerdictParseError` を raise する（既存の `VerdictNotFound` 経路は `block is None and relaxed_block is None` の段階で短絡）。

### 修正点 2: FORMATTER_PROMPT の sentinel 拡張（緩衝・副防御）

修正点 1 の delimiter gate で「delimiter も無いケース」は構造的に排除されるが、**delimiter は存在するが内部が壊れている / 内容が verdict としての体を成さない** ケースは依然 Step 3 に到達する。このとき AI が「delimiter があるから何かしら verdict を作ろう」と捏造に傾く圧力を緩和するため、`FORMATTER_PROMPT` に「verdict 不在を表現する出力チャネル」を追加する:

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

> 修正点 1 と修正点 2 の関係: 修正点 1（delimiter gate）により「delimiter 完全不在」のケースは Step 3 に到達しないため、prompt の「verdict ブロックも status キーワードも含まれない場合」という文言は、現実には **delimiter は存在するが内容が壊れている / 空である** ケース（例: `---VERDICT---\n\n---END_VERDICT---`、または delimiter + 非関連テキストの組み合わせ）が主に該当する。修正点 1 を通過した後の防衛線として機能させる。

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

- `parse_verdict` の公開シグネチャは不変。Step 1 / Step 2a / Step 2b の各経路の成功条件も不変（delimiter 不在 + reason/evidence 完備での Step 2b 成功も維持）。
- 挙動変更箇所は 2 つ:
  - **(a) Step 3 起動条件**: 「`ai_formatter` 提供時に常時起動」→「delimiter（strict / relaxed）が抽出できた場合のみ起動」へ厳格化
  - **(b) Step 3 成功後**: `_parse_formatted_output` の冒頭で sentinel を検出して `VerdictNotFound` に分岐
- 他のリファクタは混ぜない（既存テスト入力の移行とドキュメント更新を除く）。

### Step 2b の挙動を変更しない理由

`Status: PASS\nReason: ...\nEvidence: ...` のような delimiter なし整形済み出力は、V5/V6 時代から想定された agent 出力フォーマット揺れの一つで、Step 2b で成功させることが Issue #77 設計時の意図だった。本 Issue では「**自然言語に偶発出現する status キーワード**」（reason/evidence 欠落ケース）のみを排除すれば fabrication 経路を閉じられるため、Step 2b 本体のパターン定義・成功条件は触らず、Step 3 起動条件のみを厳格化する。これにより V5/V6 互換性を維持しつつ捏造経路を閉じる最小修正となる。

## テスト戦略

> **CRITICAL**: 本変更は実行時コード変更（`kaji_harness/verdict.py` のロジック変更）であり、Small / Medium のテストを追加する。bug 固有ルールに従い、修正前に Red になる回帰テストを最低 1 本含める。

### 変更タイプ

実行時コード変更（`kaji_harness/verdict.py` のロジック変更）。

### Small テスト

`tests/test_verdict_parser.py` に追加。

**新規回帰テスト（修正前 Red / 修正後 Green。**bug.md の必須要件**）**:

1. **`test_step3_rejects_output_without_delimiter_natural_language_only`**: Issue #184 `console.log` 末尾相当の合成サンプル（自然言語進捗報告のみ、`---VERDICT---` delimiter 不在、status キーワードも不在）を入力に `parse_verdict(output, VALID_STATUSES, ai_formatter=mock)` を呼ぶ。
   - 期待: `VerdictNotFound` が raise されること
   - 期待: `mock` formatter が **呼ばれていない** こと（delimiter gate で短絡）
   - 修正前: mock formatter が PASS verdict を返すと parse 成功してしまう → **FAIL**
   - 修正後: `VerdictNotFound` → **PASS**

2. **`test_step3_rejects_output_with_status_keyword_only_no_delimiter`**: `"Status: PASS\npytest waiting"` 等、valid status キーワードを含むが delimiter 不在の入力で `parse_verdict` を呼ぶ。
   - 期待: `VerdictNotFound`、`mock` formatter が呼ばれない
   - レビュー指摘 1 の境界ケースを直接固定する（formatter stub による fabrication の再現を構造的に閉じる）
   - 修正前: Step 2b が reason/evidence 欠落で失敗 → Step 3 起動 → formatter が PASS を捏造 → **FAIL**
   - 修正後: delimiter gate で短絡 → `VerdictNotFound` → **PASS**

3. **`test_step3_invoked_when_delimiter_present_but_malformed`**: `---VERDICT---` delimiter は存在するが YAML が壊れている入力で `parse_verdict` を呼ぶ。
   - 期待: AI formatter が呼ばれること（delimiter gate を通過する経路）
   - 修正後のゲートが false negative を起こさないことの確認

4. **`test_step3_invoked_when_relaxed_delimiter_only_present`**: `--- VERDICT ---` / `---END VERDICT---`（空白揺れ）等 RELAXED_PATTERN にマッチする delimiter のみが存在し内部 YAML が壊れている入力。
   - 期待: AI formatter が呼ばれること
   - relaxed delimiter も gate を通過することの確認

5. **`test_step2b_still_succeeds_with_status_and_fields_no_delimiter`**: delimiter 不在だが `Status: PASS` / `Reason: ...` / `Evidence: ...` が揃っている入力。
   - 期待: Step 2b が成功し PASS verdict を返す。`mock` formatter は呼ばれない
   - V5/V6 互換経路（[Issue #77 設計](./issue-77-verdict-resilient-parsing.md) L149-176）が delimiter gate 厳格化で壊れないことの回帰防止

6. **`test_formatter_sentinel_response_raises_verdict_not_found`**: `_parse_formatted_output` に sentinel 文字列 `---NO_VERDICT_FOUND---` 単独を渡す。
   - 期待: `VerdictNotFound` が raise されること

7. **`test_formatter_prompt_contains_sentinel_instruction`**: `FORMATTER_PROMPT.template` に sentinel `---NO_VERDICT_FOUND---` の説明が含まれることを確認。
   - prompt の構造的回帰防止

**既存テストの移行（レビュー指摘 3 への対応）**:

`tests/test_verdict_parser.py` の以下 6 件は marker-less な入力（`"unparseable text"` / `"garbage that can't be parsed at all"` / `"x" * N` 等）で formatter 起動を期待しており、delimiter gate 厳格化により **入力を更新しなければ failure** する。各テストの **意図（formatter 経路の機能検証）は維持** したまま、入力を「delimiter は存在するが内部 malformed」に置き換える。

| テスト名（line） | 現状の入力 | 移行後の入力 | 維持される検証意図 |
|------------------|------------|--------------|-------------------|
| `test_formatter_strict_success` (L701-707) | `"garbage that can't be parsed at all"` | `"---VERDICT---\nstatus: ???\n---END_VERDICT---"` 等 delimiter 付き malformed | formatter 整形結果が strict parse 可能なケースの成功経路 |
| `test_formatter_relaxed_success` (L715-727) | `"unparseable text"` | 同上（delimiter 付き malformed） | formatter 整形結果が relaxed parse でのみ成功するケース |
| `test_all_retries_fail` (L734-744) | `"unparseable"` | 同上 | formatter が常に garbage を返す場合の `max_retries` 消費挙動 |
| `test_single_retry` (L761-771) | `"unparseable"` | 同上 | `max_retries=1` で 1 回呼び出される挙動 |
| `test_formatter_invalid_value_raise` (L866-880) | `"unparseable"` | 同上 | formatter が `BOGUS` を返すと `InvalidVerdictValue` 即 raise |
| `test_long_input_truncated_for_formatter` (L910-926) | `"x" * (AI_FORMATTER_MAX_INPUT_CHARS + 5000)` | 同等長で **末尾に** `---VERDICT---\nstatus: ???\n---END_VERDICT---` を含むテキスト | input truncation（head/tail strategy）で末尾 delimiter が保持され gate を通過すること（truncation 戦略の回帰防止にもなる） |

すべて「delimiter は存在するが内容が壊れている」入力に揃えることで、現行のテスト意図（formatter 経路の機能検証）を保ちつつ delimiter gate と整合させる。新規テスト 1, 2 が delimiter 不在ケースを別途固定するため、移行による検証カバレッジ低下は発生しない。

なお `tests/test_verdict_parser.py:751-754` (`test_no_formatter_raises_parse_error`) は `parse_verdict("completely empty of verdicts", VALID_STATUSES)` で `(VerdictNotFound, VerdictParseError)` のいずれかを許容しており、delimiter gate 後は `VerdictNotFound` 単独に強化される。テスト assertion は既に `VerdictNotFound` を許容しているため変更不要（むしろ assertion を `VerdictNotFound` 単独へ厳格化する余地ありだが本 Issue scope では現状維持で十分）。

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
| Issue #184 実行ログ（OB 一次情報） | `.kaji-artifacts/184/runs/2605260007/run.log` / `.kaji-artifacts/184/runs/2605260007/implement/console.log` / `.kaji-artifacts/184/runs/2605260007/implement/stdout.log` | OB の一次根拠。**parser 入力に対応する view は `console.log`**（adapter 通過後）であり末尾は `baseline 改善確認OK。pytest 完了待ち。` で delimiter 不在。`stdout.log` は JSONL で user prompt event に delimiter リテラルを含むが adapter で除外される（設計レビュー指摘 2 への対応）。`run.log` は `step_end` で PASS verdict 記録の事実根拠。ローカルパスのためレビュー時は Issue #193 本文 + 本設計書の引用が二次参照 |
| 出力収集レイヤー | [`kaji_harness/cli.py:stream_and_log`](../../kaji_harness/cli.py) / [`kaji_harness/adapters.py:CodexAdapter.extract_text`](../../kaji_harness/adapters.py) | `CLIResult.full_output` が adapter `extract_text()` 経由で `agent_message` / `reasoning` / `mcp_tool_call.result.content` のみを抽出し、user prompt event を除外する根拠。stdout.log と full_output の差分の一次定義 |
| 既存テスト影響範囲 | [`tests/test_verdict_parser.py:701-707, 715-727, 734-744, 761-771, 866-880, 910-926`](../../tests/test_verdict_parser.py) | marker-less 入力で formatter 起動を期待する既存テスト 6 件。delimiter gate 厳格化に伴い入力を delimiter-bearing malformed へ移行する対象（設計レビュー指摘 3 への対応） |
| status-only fabrication の実証 | 設計レビュー (#193 コメント) の formatter stub 検証 | `"Status: PASS\npytest waiting"` を formatter stub に渡すと PASS verdict が生成される事実。delimiter-only gate（status キーワード単独を排除）採用の直接根拠 |
| 関連 Issue #192 | GitHub Issue #192 | 「`issue-review-code` の Step 1.4 hard gate と BACK semantic が衝突」。本 Issue（#193）の捏造 PASS が下流 step に伝播することで露見した連鎖障害の後段。修正範囲は #192 と分離し、本 Issue は parser 層のみに限定 |
