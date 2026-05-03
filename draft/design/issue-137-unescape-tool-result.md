# [設計] ツール結果の `\uXXXX` エスケープを表示時にデコードする

Issue: #137

## 概要

`kaji run` のターミナル表示および `console.log` に出力される、エージェント（特に Codex）の MCP ツール結果テキストに含まれる `\uXXXX` 形式の Unicode エスケープシーケンスを、人間可読な文字へデコードしてから表示する。

## 背景・目的

### Observed Behavior（OB）

Codex を agent とするステップで MCP 経由 `gh issue view --json` などを呼ぶと、`console.log` および標準出力に以下のように `\uXXXX` のリテラル（バックスラッシュ + `u` + 16進4桁）がそのまま現れる:

```
[2026-04-09T21:53:27] [verify-design] {"issue": {"title": "config/workflow の暗黙..."}}
```

根拠ログ: `.kaji-artifacts/135/runs/2604092145/verify-design/console.log`

### Expected Behavior（EB）

同じ入力に対し、`の` / `暗` / `黙` 等の対応文字へ展開されて表示される:

```
[2026-04-09T21:53:27] [verify-design] {"issue": {"title": "config/workflow の暗黙..."}}
```

根拠:
- `console.log` は人間が読むためのアーティファクト（`docs/reference/python/logging.md` の RunLogger 設計方針）
- 同一ログ内のエージェント自然言語出力は日本語が直接表示されており、ツール結果のみ可読性が崩れている

### 再現手順（Steps to Reproduce）

1. **前提**: kaji ハーネスがインストール済み、Codex CLI（v0.124.0 系）が利用可能、`gh` CLI 認証済み
2. **操作**: Codex を agent とするステップで MCP 経由 `gh issue view <num> --json title,body` を呼ぶワークフローを実行
   ```bash
   kaji run .kaji/wf/dev.yaml 135 --step verify-design
   ```
3. **観測**: `.kaji-artifacts/<issue>/runs/<run-id>/<step>/console.log` および標準出力に `\uXXXX` リテラルがそのまま出る

検証コマンド:
```bash
grep -P '\\u[0-9a-f]{4}' .kaji-artifacts/.../console.log  # 修正前: ヒット / 修正後: ヒット無し
```

## 根本原因（Root Cause）

二重 JSON 文字列化により、内側の JSON が「文字列としてシリアライズされる際にエスケープされた状態」のまま外側 JSON のフィールド値となっている。具体的には:

1. Codex MCP ツール経由で `gh issue view --json` を実行 → `gh` の標準出力（JSON テキスト）が得られる
2. Codex はこのツール出力を `mcp_tool_call.result.content[].text` の **文字列フィールドとして** 格納する。この格納時、または上流の MCP server 層で、ツール出力が `ensure_ascii=True` 相当の JSON エンコーダで一度文字列化されている（バックスラッシュ + `u` + 16進4桁のリテラルが文字列に含まれる）
3. `json.loads(line)` で外側イベントをデコードすると、`text` フィールドの値は「`\uXXXX` というリテラル文字列を含む通常 Python str」になる（外側のエスケープ解除は1回分しか行われず、内側の `\uXXXX` リテラルはそのまま残る）
4. `CodexAdapter.extract_text`（`kaji_harness/adapters.py:55-71`）が text をそのまま返却
5. `stream_and_log`（`kaji_harness/cli.py:141-216`）がそのまま `console.log` および stdout に書き出す

**いつから**: `mcp_tool_call` の `result.content[].text` 抽出ロジック（V5/V6 復元時、`adapters.py:62-70` 周辺）導入時から。`gh --json` 等を MCP で呼ぶワークフローが増えてから顕在化。

**他の影響箇所調査**:
- `ClaudeAdapter.extract_text` は現状 `tool_result` 系イベントを抽出していない（`assistant.message.content[].text` と `result.result` のみ抽出）。Claude 経由では同症状は表面化していないが、将来 `tool_use_result` を抽出するようになった場合に備え、デコード処理を共通ヘルパに切り出して再利用できる形で実装する
- `GeminiAdapter` は `message.content` を直接抽出。Gemini CLI 側で MCP ツール結果が同様の二重エンコードを経るかは未確認だが、共通ヘルパを通すことで横展開可能とする

## インターフェース

bug 修正のため公開 API のシグネチャ変更はなし。

内部追加:

```python
# kaji_harness/adapters.py または同一モジュール内ユーティリティ
def decode_unicode_escapes(text: str) -> str:
    """ツール結果テキストに含まれる `\\uXXXX` リテラルを実文字へ展開。

    - 全体が JSON 値として parse 可能な場合: ensure_ascii=False で再シリアライズ
    - parse 不可な場合: 正規表現 `\\\\u([0-9a-fA-F]{4})` を `chr(int(m, 16))` に置換
    - サロゲートペアは正しく結合する（`\\uD83D\\uDE00` → 😀 等）
    - 置換対象が存在しない通常テキストはそのまま返す
    """
```

呼び出し箇所: `CodexAdapter.extract_text` 内の `mcp_tool_call` 分岐で、`contents[].text` を返す直前にこのヘルパを通す。

## 変更スコープ

- `kaji_harness/adapters.py` — `CodexAdapter.extract_text` の `mcp_tool_call` 分岐にヘルパ適用、共通ヘルパ `decode_unicode_escapes` を追加
- `tests/test_adapters.py` または新規 `tests/test_unicode_decode.py` — 再現テスト追加

`kaji_harness/cli.py` の `stream_and_log` には手を入れない（adapter 側で正規化するのが責務として適切）。

## 方針（修正アプローチ）

### 採用案: 「JSON parse → 失敗時に regex フォールバック」二段構え

```python
import json
import re

_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

def decode_unicode_escapes(text: str) -> str:
    if "\\u" not in text:
        return text
    # 第一段: JSON 値全体として parse できれば re-serialize（構造を保ったまま日本語化）
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        if isinstance(parsed, str):
            return parsed
    except json.JSONDecodeError:
        pass
    # 第二段: 部分的に \uXXXX を含む通常テキスト → regex で個別置換
    # サロゲートペアは json.loads('"' + match + '"') で結合する
    def _sub(m: re.Match[str]) -> str:
        try:
            return json.loads(f'"{m.group(0)}"')
        except json.JSONDecodeError:
            return m.group(0)
    # サロゲートペア対応のため、まず連続2個のエスケープをまとめて処理
    return _ESCAPE_RE.sub(_sub, text)
```

理由:
- **JSON 全体 parse**: 完全な JSON 出力（`gh --json` の典型ケース）は構造を保ったまま `ensure_ascii=False` で再整形でき、整形済み出力の見やすさも改善する
- **regex フォールバック**: ツール出力が JSON ではない（プレーンテキストに部分的に `\uXXXX` が混じっている）場合に対応
- **early return (`"\\u" not in text`)**: 通常メッセージへのオーバーヘッドを回避

### 検討した代替案と却下理由

| 案 | 内容 | 却下理由 |
|----|------|---------|
| `text.encode().decode("unicode_escape")` | Python 組み込みの unicode_escape codec | UTF-8 で書かれた既存日本語まで Latin-1 として再解釈し、文字化けを引き起こす |
| `stream_and_log` 側で全テキストを一律デコード | adapter 非依存で一箇所改修 | adapter の責務（CLI 固有のイベント正規化）を侵食する。Claude/Gemini 側で意図せず正規化が走り得る |
| `\uXXXX` を含む文字列を一切エスケープしない設計に Codex 側で要望 | 上流修正 | kaji 側で制御不能 / 過去ログ・他ユーザー環境にも影響不可 |

### 誤検知リスク評価

regex で `\uXXXX` を一律置換すると、ツール出力に「**意図的に** `\uXXXX` というリテラル文字列が含まれていた場合」（例: ドキュメント中のサンプルコード、エスケープシーケンスの説明文）も展開される。

評価:
- 適用範囲を `mcp_tool_call.result.content[].text` に限定（adapter 内のみ）→ エージェントの assistant メッセージや人間が書いたドキュメント本文には影響しない
- ツール結果（`gh`, `cat`, `grep` 等の出力）に意図的な `\uXXXX` リテラルが含まれるケースは稀。含まれていたとしても展開後の表示で人間が解釈しやすくなる方向であり、可逆性は `stdout.log`（生イベント）側で担保される
- `stdout.log` は raw な JSONL を保持し続けるため、エスケープ前の状態は失われない

許容可能なリスクと判断する。

## テスト戦略

### 変更タイプ

実行時コード変更（adapter ロジックの変更）

#### Small テスト（必須・再現テスト）

`tests/test_adapters.py` に以下を追加:

1. **mcp_tool_call の result.content[].text に `\uXXXX` を含む擬似イベントを `CodexAdapter.extract_text` に渡し、日本語化されることを assert**
   - 入力: `{"type":"item.completed","item":{"type":"mcp_tool_call","result":{"content":[{"type":"text","text":"{\"title\": \"config/workflow \\u306e\\u6697\\u9ed9\"}"}]}}}`
   - 期待: 戻り値に `の暗黙` を含み、`の` の文字列を含まない
   - **修正前は FAIL、修正後は PASS** することを確認
2. **JSON parse 不能な部分文字列ケース**: `text` が `"prefix \\u3042 suffix"` のようなプレーンテキスト + 部分エスケープ
   - 期待: `prefix あ suffix`
3. **エスケープを含まない通常テキスト**: `"hello world"` → そのまま `"hello world"` を返す（フォールバック動作）
4. **サロゲートペア**: `"\\uD83D\\uDE00"` → `😀`
5. **空 / None ガード**: 既存の挙動（None 返却、空文字スキップ）が維持されること
6. **不正なエスケープ**: `"\\uZZZZ"` のような broken sequence → そのまま残す（クラッシュしない）

#### Medium テスト

不要。adapter は純粋な dict → str 変換ロジックで、ファイル I/O・サブプロセス起動・内部サービス結合を含まない。Small テストで CodexAdapter の挙動を完全にカバーできる。`stream_and_log` の統合動作は既存の `test_cli_streaming_integration.py` で被覆済み。

#### Large テスト

不要。実 API 疎通や Codex CLI E2E を伴う検証は本修正の妥当性確認に必要なく、CI コストに見合わない。Issue の完了条件にある「実機での `console.log` 確認」は変更固有の手動検証として `/i-dev-final-check` 段階で実施し、恒久テストには昇格しない（後述）。

### 変更固有検証（手動）

- 再現環境で `kaji run .kaji/wf/dev.yaml 135 --step verify-design` を実行し、`grep -P '\\u[0-9a-f]{4}' .kaji-artifacts/.../console.log` がヒットしないことを確認
- `make check` 通過（既存テストにデグレなし）

### Claude / Gemini 経路への適用

- Claude: 現状 `tool_result` を抽出していないため、本修正では `ClaudeAdapter` には変更を加えない。ただし `decode_unicode_escapes` ヘルパは module-public（`adapters.py` 内）に置き、将来 `tool_use_result` 抽出を追加する際にすぐ流用できる構造とする
- Gemini: 同上。現状抽出している `message.content` は assistant の自然言語であり MCP ツール結果ではないため、対象外

この設計判断は Issue 完了条件「Claude adapter 経路でも同様に可読化されることを確認」に対しては「**現時点では Claude の console.log 表示パスに `gh --json` 出力が流れない** という事実をテストで固定する（=回帰防止）」で対応する。具体的には `test_adapters.py` に「ClaudeAdapter は `tool_result` を抽出しない」ことを明示する既存挙動の確認テストを 1 本追加する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順変更なし |
| docs/reference/python/logging.md | あり（軽微） | 「ツール結果の表示は人間可読な日本語に正規化される」旨の 1〜2 行追記が望ましい |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python `json` ドキュメント | https://docs.python.org/3/library/json.html#json.dumps | `ensure_ascii` パラメータ: `True` がデフォルトで non-ASCII を `\uXXXX` でエスケープ。`False` を指定すると raw UTF-8 として出力する。本修正で `json.dumps(..., ensure_ascii=False)` を採用する根拠 |
| Python `json` decoder の文字列処理 | https://docs.python.org/3/library/json.html#json.JSONDecoder | JSON 文字列内の `\uXXXX` エスケープは parse 時に対応する Unicode 文字へ展開される。`json.loads('"\\u3042"')` → `"あ"` の挙動根拠 |
| 再現ログ | `.kaji-artifacts/135/runs/2604092145/verify-design/console.log` | OB の根拠（リポジトリ内アーティファクト） |
| Codex MCP イベント抽出ロジック（既存実装） | `kaji_harness/adapters.py:55-71` | 修正対象箇所。`mcp_tool_call.result.content[].text` を返す現行コード |
| ストリーム書き出し | `kaji_harness/cli.py:141-216` | 表示パスの起点。本修正では変更しないが影響範囲評価のための参照 |
| RunLogger 設計方針 | `docs/reference/python/logging.md` | console.log は人間可読アーティファクトという EB の根拠 |
| 関連 Issue | #135（再現元ワークフロー）、#167（CLI 仕様変更追従、独立） | 文脈共有 |
