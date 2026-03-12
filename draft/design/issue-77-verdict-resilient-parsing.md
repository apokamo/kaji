# [設計] V5/V6 VERDICT 判定機構の V7 復元

Issue: #77

## 概要

V7 `kaji_harness.verdict` の厳密一致パーサーを、V5/V6 で 100 回以上の E2E テストに基づき設計されたハイブリッドフォールバック判定機構に置き換える。出力揺れで workflow 全体が致命停止する現状を解消する。

## 背景・目的

#73 の `issue-pr` ステップで PR 作成自体は成功したにもかかわらず、エージェント出力の終端が `---END VERDICT---`（アンダースコア欠落）だったため `VerdictNotFound` で workflow 全体が ERROR になった。

V7 の現行パーサーは以下の正規表現のみで動作している:

```python
VERDICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)
```

一方 V5/V6 では、strict → relaxed → AI formatter retry の 3 段階フォールバックが設計・実装されており、delimiter 揺れ・フィールドラベル揺れ・ノイズ混入に対する耐性を持っていた。今回はこの回復戦略を V7 のアーキテクチャに適合する形で復元する。

## インターフェース

### 入力

```python
def parse_verdict(
    output: str,
    valid_statuses: set[str],
    *,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> Verdict:
```

| 引数 | 型 | 説明 |
|------|-----|------|
| `output` | `str` | CLI プロセスの全出力テキスト |
| `valid_statuses` | `set[str]` | ステップの `on` フィールドに定義された verdict 値の集合 |
| `ai_formatter` | `Callable[[str], str] \| None` | Step 3 用 AI 整形関数（省略時は Step 2 までで停止） |
| `max_retries` | `int` | Step 3 の最大リトライ回数（デフォルト: 2） |

### 出力

```python
@dataclass
class Verdict:
    status: str       # "PASS" | "RETRY" | "BACK" | "ABORT" など
    reason: str       # 判定理由
    evidence: str     # 判定根拠
    suggestion: str   # 次のアクション提案（ABORT/BACK は必須、他は空文字許容）
```

戻り値の `Verdict` dataclass は変更なし。

### 例外

| 例外 | フォールバック対象 | 意味 |
|------|-------------------|------|
| `VerdictNotFound` | いいえ（全ステップ失敗後） | 出力から verdict を抽出できなかった |
| `VerdictParseError` | いいえ（全ステップ失敗後） | 必須フィールド欠損または構造解析エラー |
| `InvalidVerdictValue` | **いいえ（即座に raise）** | 不正な verdict 値。プロンプト違反。リトライ対象外 |

`InvalidVerdictValue` のみ、どのステップで発生しても即座に raise する。これは V5/V6 からの設計方針を継承：フォーマット揺れは回復対象だが、意味的に不正な値は即失敗。

### 使用例

```python
# runner.py から呼び出し（既存の呼び出しはそのまま動作）
verdict = parse_verdict(
    result.full_output,
    valid_statuses=set(current_step.on.keys()),
)

# AI formatter 付き（将来の拡張）
verdict = parse_verdict(
    result.full_output,
    valid_statuses=set(current_step.on.keys()),
    ai_formatter=my_formatter_func,
    max_retries=2,
)
```

## 制約・前提条件

- V7 の verdict フォーマットは YAML ベース（`status:`, `reason:`, `evidence:`, `suggestion:`）を標準とする
- V5/V6 の `Result:` / `Status:` パターンもフォールバックで対応する（エージェントが旧フォーマットで出力した場合への備え）
- `InvalidVerdictValue` は全ステップで即座に raise（V5/V6 と同一方針）
- AI formatter（Step 3）はオプショナル。提供されない場合は Step 2 までで判定終了
- 既存の runner.py は `ai_formatter` を渡さないため、Step 1-2 のみで動作する（後方互換）
- `valid_statuses` による検証ロジックは変更しない

## 方針

### 3 段階フォールバック戦略

```
Step 1: Strict Parse（現行 V7 相当）
  ├─ 成功 → Verdict 返却
  ├─ InvalidVerdictValue → 即 raise（回復不能）
  └─ VerdictNotFound / VerdictParseError → Step 2 へ

Step 2: Relaxed Parse（V5/V6 由来の緩和判定）
  ├─ 成功 → Verdict 返却
  ├─ InvalidVerdictValue → 即 raise（回復不能）
  └─ 失敗 → Step 3 へ（ai_formatter 未提供なら raise）

Step 3: AI Formatter Retry（V5/V6 由来の最終手段）
  ├─ 成功 → Verdict 返却
  ├─ InvalidVerdictValue → 即 raise（回復不能）
  └─ 全リトライ失敗 → VerdictParseError raise
```

### Step 1: Strict Parse

現行 V7 と同一。`---VERDICT---` と `---END_VERDICT---` の厳密一致 + YAML パース。

```python
STRICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)
```

### Step 2: Relaxed Parse

2 段階のフォールバックから成る。

**2a. Delimiter 緩和**

以下のバリエーションを許容する正規表現で verdict ブロックを抽出:

```python
RELAXED_PATTERN = re.compile(
    r"---\s*VERDICT\s*---\s*\n(.*?)\n\s*---\s*END[\s_]VERDICT\s*---",
    re.DOTALL | re.IGNORECASE,
)
```

| 許容する揺れ | 例 |
|-------------|-----|
| アンダースコア/スペース | `---END_VERDICT---`, `---END VERDICT---` |
| 大文字小文字 | `---verdict---`, `---Verdict---` |
| delimiter 前後の空白 | `--- VERDICT ---` |

抽出されたブロック内の YAML パースを試みる。

**2b. Key-Value パターン抽出**

YAML パースに失敗した場合、V5/V6 由来の正規表現パターンで `status` 値を探索:

```python
RELAXED_STATUS_PATTERNS = [
    r"status:\s*(\w+)",
    r"Status:\s*(\w+)",
    r"Result:\s*(\w+)",
    r"-\s*Result:\s*(\w+)",
    r"-\s*Status:\s*(\w+)",
    r"\*\*Status\*\*:\s*(\w+)",
    r"ステータス:\s*(\w+)",
    r"Status\s*=\s*(\w+)",
    r"Result\s*=\s*(\w+)",
]
```

status 以外のフィールド（reason, evidence, suggestion）も同様にパターンで探索する。見つからないフィールドは空文字ではなく `"(extracted by relaxed parser)"` を設定し、strict parse との区別を可能にする。ただし reason と evidence が全く抽出できない場合は VerdictParseError を raise する（Verdict の意味的完全性を維持）。

**注意**: relaxed parse で status を抽出した後、`valid_statuses` に含まれない値は `InvalidVerdictValue` を即座に raise する（V5/V6 と同一方針）。

### Step 3: AI Formatter Retry

`ai_formatter` が提供された場合のみ実行。V5/V6 の設計を踏襲:

1. 入力テキストを head + tail 戦略で truncate（verdict は末尾に出現しやすいため tail 重視）
2. `ai_formatter(truncated_text)` を呼び出し、整形されたテキストを取得
3. 整形結果に対して Step 1 → Step 2 を再実行
4. 失敗した場合 `max_retries` 回まで繰り返す
5. 全リトライ失敗で `VerdictParseError` を raise

```python
AI_FORMATTER_MAX_INPUT_CHARS: int = 8000  # V5/V6 と同一
```

### モジュール構造

変更対象は `kaji_harness/verdict.py` のみ。新ファイルは作成しない。

```python
# verdict.py 内部構成
_extract_block_strict(output) -> str        # Step 1: delimiter 厳密抽出
_extract_block_relaxed(output) -> str       # Step 2a: delimiter 緩和抽出
_parse_yaml_fields(block) -> Verdict        # YAML フィールド解析（既存の _parse_fields 改名）
_parse_relaxed_fields(text) -> Verdict      # Step 2b: regex フィールド抽出
_validate(verdict, valid_statuses) -> None  # 検証（既存のまま）
parse_verdict(output, valid_statuses, *, ai_formatter, max_retries) -> Verdict  # 公開 API
```

### runner.py への影響

`parse_verdict` のシグネチャは後方互換。既存の呼び出しは変更不要:

```python
# 変更不要（ai_formatter=None がデフォルト）
verdict = parse_verdict(
    result.full_output,
    valid_statuses=set(current_step.on.keys()),
)
```

将来的に AI formatter を統合する場合のみ、runner.py 側でファクトリ関数を用意して渡す。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

`tests/test_verdict_parser.py` を拡張。V5/V6 の E2E テスト知見に基づくケースを網羅する。

**Step 1 (Strict) — 既存テストを維持**:
- 正常な YAML verdict ブロックの抽出（全 status 値: PASS, RETRY, BACK, ABORT）
- ABORT/BACK の suggestion 必須チェック
- VerdictNotFound（ブロックなし、空出力）
- InvalidVerdictValue（不正 status）
- VerdictParseError（必須フィールド欠損、不正 YAML）
- 出力途中に verdict があるケース
- 複数 verdict ブロック（先頭優先）

**Step 2a (Delimiter 緩和) — 新規**:
- `---END VERDICT---`（スペース区切り、#73 実事例）
- `---end_verdict---`（小文字）
- `--- VERDICT ---`（前後スペース）
- `---VERDICT---` + `---END VERDICT---`（開始は正常、終端のみ揺れ）
- delimiter 前後に余分な空行やログ行がある場合

**Step 2b (Key-Value パターン) — 新規、V5/V6 由来**:
- `Result: PASS` パターン
- `- Result: PASS` リスト形式
- `Status: PASS` レガシー形式
- `- Status: PASS` リスト形式
- `**Status**: PASS` Markdown 強調形式
- `ステータス: PASS` 日本語
- `Status = PASS` / `Result = PASS` 代入形式
- reason / evidence / suggestion のパターン抽出
- status のみ抽出可能で reason/evidence が欠落 → VerdictParseError

**Step 3 (AI Formatter) — 新規**:
- ai_formatter 成功ケース（整形結果が strict parse 可能）
- ai_formatter 成功ケース（整形結果が relaxed parse でのみ成功）
- ai_formatter 全リトライ失敗 → VerdictParseError
- ai_formatter 未提供で Step 2 も失敗 → VerdictParseError
- max_retries=1 で 1 回のみリトライ
- max_retries < 1 で ValueError

**横断テスト**:
- InvalidVerdictValue は全ステップで即 raise（strict/relaxed/formatter それぞれで確認）
- 入力テキストの truncation（8000 文字超のテキスト）
- ノイズ混入（verdict ブロック前後にログ出力、思考トレース）
- verdict が出力途中（非末尾）にあるケース + 末尾にノイズ

### Medium テスト

`tests/test_verdict_integration.py` を新規作成。

- `runner.py` の `parse_verdict` 呼び出しとの結合テスト
  - 正常な verdict → ステップ遷移が正しく動作
  - relaxed parse で回復した verdict → ステップ遷移が正しく動作
  - VerdictNotFound → runner が適切にエラーハンドリング
- `state.py` への verdict 永続化（relaxed parse 結果を含む）
- `logger.py` への verdict ログ出力
- 実際のスキル出力テンプレート（`.claude/skills/` のファイル）から抽出したサンプルでパース

### Large テスト

`tests/test_verdict_e2e.py` を新規作成。

- 実際のエージェント出力ログ（`test-artifacts/` に保存）を使ったパーステスト
  - #73 で実際に出力された `---END VERDICT---` ケース
  - 将来の回帰テスト用に実出力サンプルをフィクスチャとして保存
- `kaji run` コマンドで workflow を実行し、verdict パースが全ステップで成功することを確認
  - CLI を実際に起動するため Large サイズ

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存の判定機構の復元であり、新規技術選定ではない |
| docs/ARCHITECTURE.md | なし | verdict モジュールの外部インターフェースは変更なし |
| docs/dev/ | なし | ワークフロー・開発手順に変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| V5/V6 verdict 実装 | `legacy/bugfix_agent/verdict.py` | 3 段階フォールバック（strict → relaxed → AI formatter）の参照実装。RELAXED_PATTERNS 定義、AI_FORMATTER_MAX_INPUT_CHARS=8000、InvalidVerdictValueError の即 raise 方針 |
| V5/V6 アーキテクチャ | `legacy/docs/ARCHITECTURE.ja.md` Section 10 | エラーハンドリングとフォールバックの設計思想。「フォーマット揺れは回復対象、意味的に不正な値は即失敗」の方針 |
| E2E テスト知見 | `legacy/docs/E2E_TEST_FINDINGS.md` | 19 回の E2E テストで発見された出力パターン。Codex mcp_tool_call モードでのプレーンテキスト出力、VerdictParseError 根本原因（セクション 3.1）、非 JSON テキスト行の常時収集の必要性 |
| テスト設計書 | `legacy/docs/TEST_DESIGN.md` | CodexTool JSON パース仕様（セクション 2.5）。「Step 1 (Strict Parse) → Step 2 (Relaxed Parse) → Step 3 (AI Formatter)」の下流処理フロー |
| #73 実行ログ | Issue #77 コメント | `---END VERDICT---`（スペース区切り）による VerdictNotFound の実事例。「出力揺れで workflow が即死しない」ことが最重要要件 |
| V7 現行実装 | `kaji_harness/verdict.py` | 厳密一致のみ（`VERDICT_PATTERN` 正規表現）。緩和パース・フォールバックなし |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
