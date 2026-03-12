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
- `valid_statuses` による検証ロジックは変更しない
- runner.py は `create_verdict_formatter` で生成した AI formatter を `parse_verdict` に渡し、3 段階すべてを実運用経路で有効にする

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

YAML パースに失敗した場合、V5/V6 由来の正規表現パターンで `status` 値を探索する。

**V5/V6 の安全策を継承**: パターン自体を `valid_statuses` から動的に生成し、有効な verdict 値のみをマッチ対象にする。これにより `Status: 200` や `Result = success` のような無関係な文字列による false positive を構造的に排除する（`legacy/bugfix_agent/verdict.py:61-78` の設計方針）。

```python
def _build_relaxed_status_patterns(valid_statuses: set[str]) -> list[re.Pattern[str]]:
    """valid_statuses から relaxed パターンを動的に生成。"""
    alt = "|".join(re.escape(s) for s in sorted(valid_statuses))
    templates = [
        rf"status:\s*({alt})",
        rf"Status:\s*({alt})",
        rf"Result:\s*({alt})",
        rf"-\s*Result:\s*({alt})",
        rf"-\s*Status:\s*({alt})",
        rf"\*\*Status\*\*:\s*({alt})",
        rf"ステータス:\s*({alt})",
        rf"Status\s*=\s*({alt})",
        rf"Result\s*=\s*({alt})",
    ]
    return [re.compile(t, re.IGNORECASE) for t in templates]
```

パターンが有効値のみにマッチするため、relaxed parse で `InvalidVerdictValue` は発生しない（構造的に不可能）。

status 以外のフィールド（reason, evidence, suggestion）も同様にパターンで探索する。**ただし、reason または evidence が抽出できなかった場合は Verdict を生成せず、Step 3（AI formatter）にフォールスルーする**。理由: V7 では `previous_verdict` として次ステップに伝搬されるため（`kaji_harness/prompt.py:35-40`）、合成文字列を含む不完全な Verdict を返すと、fix スキルが実在しない指摘を根拠に動作する危険がある。

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

変更対象は以下の 4 ファイル:

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py` | 3 段階フォールバックパーサー + formatter ファクトリ |
| `kaji_harness/runner.py` | formatter 生成・注入 |
| `kaji_harness/cli.py` | 非 JSON 行の収集（出力収集レイヤーの復元） |
| `kaji_harness/adapters.py` | Codex `mcp_tool_call` 等の追加 item type からのテキスト抽出 |

```python
# verdict.py 内部構成
_extract_block_strict(output) -> str                          # Step 1: delimiter 厳密抽出
_extract_block_relaxed(output) -> str                         # Step 2a: delimiter 緩和抽出
_parse_yaml_fields(block) -> Verdict                          # YAML フィールド解析（既存の _parse_fields 改名）
_build_relaxed_status_patterns(valid_statuses) -> list[...]   # Step 2b: パターン動的生成
_parse_relaxed_fields(text, valid_statuses) -> Verdict        # Step 2b: regex フィールド抽出
_validate(verdict, valid_statuses) -> None                    # 検証（既存のまま）
parse_verdict(output, valid_statuses, *, ai_formatter, max_retries) -> Verdict  # 公開 API

FORMATTER_PROMPT: str                                         # Step 3 用プロンプト（V5/V6 由来、V7 YAML 形式に適合）
create_verdict_formatter(agent, model, workdir, valid_statuses) -> Callable  # ファクトリ関数
```

### Step 3 の実運用統合: `create_verdict_formatter` と runner.py 変更

V5/V6 では各ハンドラが `create_ai_formatter(ctx.reviewer, ...)` で formatter を生成し `parse_verdict` に渡していた（`legacy/bugfix_agent/handlers/init.py:38-40` 等）。V7 でも同じパターンを踏襲する。

**`verdict.py` に追加する `create_verdict_formatter` ファクトリ関数**:

```python
FORMATTER_PROMPT: str = """以下の出力から VERDICT を抽出し、正確な YAML フォーマットで出力してください。

## 入力
{raw_output}

## 出力フォーマット（厳密に従ってください）
---VERDICT---
status: <{valid_statuses_str} のいずれか1つ>
reason: "判定理由"
evidence: "判定根拠"
suggestion: "次のアクション提案"
---END_VERDICT---

重要: status 行は必ず {valid_statuses_str} のいずれかを出力してください。それ以外の値は使用禁止です。
"""

def create_verdict_formatter(
    agent: str,
    valid_statuses: set[str],
    *,
    model: str | None = None,
    workdir: Path | None = None,
) -> Callable[[str], str]:
    """AI verdict formatter を生成する。

    軽量な CLI 呼び出しで出力を整形する。
    ステップ実行の execute_cli とは独立した簡易プロセス。

    Args:
        agent: CLI エージェント名 ("claude" | "codex" | "gemini")
        valid_statuses: ステップの on に定義された verdict 値の集合。
                        formatter prompt に埋め込み、ステップが受理しない値の出力を防止する。
        model: モデル指定（省略時はエージェントデフォルト）
        workdir: 作業ディレクトリ（省略時はカレント）

    Returns:
        Callable[[str], str]: parse_verdict の ai_formatter 引数に渡す関数
    """
    statuses_str = "|".join(sorted(valid_statuses))

    def formatter(raw_output: str) -> str:
        prompt = FORMATTER_PROMPT.format(
            raw_output=raw_output,
            valid_statuses_str=statuses_str,
        )
        args = _build_formatter_cli_args(agent, model, prompt)
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workdir,
        )
        return result.stdout

    return formatter
```

`_build_formatter_cli_args` は `cli.py` の agent 別引数構築を簡略化したもの。streaming/logging 不要のため `-p` (print) モードで呼び出す。

**`runner.py` の変更**:

```python
from .verdict import parse_verdict, create_verdict_formatter

# メインループ内、CLI 実行後:
valid = set(current_step.on.keys())
formatter = create_verdict_formatter(
    agent=current_step.agent,
    valid_statuses=valid,
    model=current_step.model,
    workdir=self.workdir,
)
verdict = parse_verdict(
    result.full_output,
    valid_statuses=valid,
    ai_formatter=formatter,
)
```

`valid_statuses` は `parse_verdict` と `create_verdict_formatter` の両方に同じ値を渡す。これにより formatter prompt が出力する status 値が常にステップの `on` 定義と一致し、`InvalidVerdictValue` 即 raise による Step 3 自体の failure 化を防止する。

`parse_verdict` のシグネチャ自体は後方互換を維持（`ai_formatter=None` がデフォルト）するため、テストコード等の既存呼び出しは変更不要。

### 出力収集レイヤーの復元: `cli.py` / `adapters.py` 変更

parser だけ強化しても、`parse_verdict()` に届く `full_output` に VERDICT テキストが含まれなければ無意味。legacy の一次情報は出力収集レイヤーの回復が前提であることを明記している（`legacy/docs/TEST_DESIGN.md:166-194`, `legacy/docs/E2E_TEST_FINDINGS.md:82-94`）。

**問題 1: `cli.py` の `stream_and_log()` が非 JSON 行を破棄**

現行 `kaji_harness/cli.py:99-102`:
```python
try:
    event: dict[str, Any] = json.loads(line)
except json.JSONDecodeError:
    continue  # ← 非 JSON 行はすべて捨てている
```

V5/V6 の知見では、Codex が `mcp_tool_call` モードで動作する場合、VERDICT がプレーンテキスト（非 JSON）として stdout に出力されることが確認されている（`legacy/docs/E2E_TEST_FINDINGS.md` Section 3.1, Section 4.1）。

修正方針:
```python
try:
    event: dict[str, Any] = json.loads(line)
except json.JSONDecodeError:
    # 非 JSON 行も収集（VERDICT がプレーンテキストで出力される場合への備え）
    stripped = line.strip()
    if stripped:
        texts.append(stripped)
    continue
```

これにより非 JSON 行が `full_output` に含まれ、downstream の `parse_verdict()` が VERDICT を検出可能になる。

**問題 2: `CodexAdapter` が `agent_message` / `reasoning` 以外の item type を無視**

現行 `kaji_harness/adapters.py:55-61`:
```python
def extract_text(self, event: dict[str, Any]) -> str | None:
    if event.get("type") == "item.completed":
        item = event.get("item", {})
        if item.get("type") in ("agent_message", "reasoning"):
            text = item.get("text")
            return text if text else None
    return None
```

V5/V6 では `mcp_tool_call` item type の `result.content[].text` にも VERDICT が含まれることが確認されている（`legacy/docs/TEST_DESIGN.md` Section 2.5）。

修正方針: `CodexAdapter.extract_text()` で `mcp_tool_call` の `result.content` からもテキストを抽出する。

```python
def extract_text(self, event: dict[str, Any]) -> str | None:
    if event.get("type") == "item.completed":
        item = event.get("item", {})
        item_type = item.get("type")
        if item_type in ("agent_message", "reasoning"):
            text = item.get("text")
            return text if text else None
        if item_type == "mcp_tool_call":
            # mcp_tool_call の result.content からテキストを抽出
            result = item.get("result", {})
            contents = result.get("content", [])
            texts = [c["text"] for c in contents if c.get("type") == "text" and "text" in c]
            return "\n".join(texts) if texts else None
    return None
```

これら 2 つの変更により、VERDICT テキストがどのような出力形式で出現しても `full_output` に収集され、`parse_verdict()` に到達する。

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
- status のみ抽出可能で reason/evidence が欠落 → Step 3 へフォールスルー（ai_formatter なしなら VerdictParseError）
- **false positive 排除**: `Status: 200`、`Result = success` 等の無関係文字列はパターンにマッチしないことを確認（valid_statuses 制限）

**Step 3 (AI Formatter) — 新規**:
- ai_formatter 成功ケース（整形結果が strict parse 可能）
- ai_formatter 成功ケース（整形結果が relaxed parse でのみ成功）
- ai_formatter 全リトライ失敗 → VerdictParseError
- ai_formatter 未提供で Step 2 も失敗 → VerdictParseError
- max_retries=1 で 1 回のみリトライ
- max_retries < 1 で ValueError
- **formatter prompt の valid_statuses 制限**: `valid_statuses={"PASS", "ABORT"}` で生成した formatter が `BACK` や `RETRY` を含む prompt を出さないことを確認

**出力収集レイヤー — 新規**:
- `stream_and_log()` が非 JSON 行を `full_output` に含めること（`cli.py` 変更の回帰テスト）
- `CodexAdapter.extract_text()` が `mcp_tool_call` item type からテキストを抽出すること
- JSON 行 + 非 JSON 行が混在する stdout で、すべてのテキストが `full_output` に結合されること
- 非 JSON 行内の VERDICT ブロックが `parse_verdict()` で正しく抽出されること（収集→パースの結合）

**横断テスト**:
- InvalidVerdictValue は Step 1 (strict) と Step 3 (formatter) で即 raise（strict は `_validate` 経由、formatter は整形結果の再パース時に `_validate` 経由）
- Step 2b (relaxed pattern) では `InvalidVerdictValue` が構造的に発生しないことを確認（パターン自体が valid_statuses に制限されているため）
- relaxed pattern の false positive 排除: `Status: 200`, `Result = success`, `status: running` 等がマッチしないことを確認
- 入力テキストの truncation（8000 文字超のテキスト）
- ノイズ混入（verdict ブロック前後にログ出力、思考トレース）
- verdict が出力途中（非末尾）にあるケース + 末尾にノイズ

### Medium テスト

`tests/test_verdict_integration.py` を新規作成。

- `runner.py` の `parse_verdict` 呼び出しとの結合テスト
  - 正常な verdict → ステップ遷移が正しく動作
  - relaxed parse で回復した verdict → ステップ遷移が正しく動作
  - Step 3 (AI formatter) で回復した verdict → ステップ遷移が正しく動作
  - VerdictNotFound → runner が適切にエラーハンドリング
- `create_verdict_formatter` のファクトリ結合テスト
  - 各エージェント（claude/codex/gemini）用の CLI 引数が正しく構築されること
  - subprocess 呼び出しをモックし、formatter が正しくプロンプトを構築・結果を返却すること
  - `valid_statuses={"PASS", "ABORT"}` で生成した formatter の prompt に `RETRY`/`BACK` が含まれないこと
- 出力収集レイヤーの結合テスト（`cli.py` + `adapters.py`）
  - Codex の `mcp_tool_call` イベントからテキストが `full_output` に含まれること（subprocess をモック）
  - 非 JSON 行が混在する stdout から verdict が `parse_verdict()` で抽出できること
- `state.py` への verdict 永続化（relaxed parse 結果を含む）
- `previous_verdict` 伝搬テスト: relaxed parse 結果の reason/evidence が次ステップに渡されても正常に動作すること
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
| V7 プロンプト伝搬 | `kaji_harness/prompt.py:35-40` | `previous_verdict` として reason/evidence/suggestion を次ステップにそのまま注入。合成文字列を含む verdict は下流ステップの判断を歪める根拠 |
| V5/V6 ハンドラ統合パターン | `legacy/bugfix_agent/handlers/init.py:38-40`, `design.py:78-79`, `implement.py:90-91` | 各ハンドラで `create_ai_formatter(ctx.reviewer, ...)` を生成し `parse_verdict` に渡す統合パターンの実例。V7 runner.py での統合設計の根拠 |
| V5/V6 relaxed pattern 制限 | `legacy/bugfix_agent/verdict.py:61-78`, `legacy/docs/ARCHITECTURE.ja.md` Section 10 | relaxed pattern は有効 verdict 値のみに制限（`PASS\|RETRY\|BACK_DESIGN\|ABORT`）。false positive 防止の設計方針 |
| V5/V6 非 JSON 行収集 | `legacy/docs/TEST_DESIGN.md:166-194`, `legacy/docs/E2E_TEST_FINDINGS.md:82-94` | Codex `mcp_tool_call` モードでは VERDICT がプレーンテキスト化し得るため「無効な JSON 行はスキップせず収集」が必要。出力収集レイヤーを含めた復元の根拠 |
| V7 出力収集（現行） | `kaji_harness/cli.py:99-102`, `kaji_harness/adapters.py:55-61` | 現行は非 JSON 行を `continue` で破棄、CodexAdapter は `agent_message`/`reasoning` のみ抽出。`mcp_tool_call` やプレーンテキスト VERDICT は `full_output` に到達しない |
| V7 ワークフロー定義 | `workflows/feature-development.yaml` | ステップごとの `valid_statuses` が異なる（例: design は `PASS\|ABORT` のみ、implement は `PASS\|RETRY\|BACK\|ABORT`）。formatter prompt を `valid_statuses` ベースにする根拠 |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
