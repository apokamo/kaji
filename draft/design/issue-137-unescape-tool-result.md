# [設計] ツール結果の `\uXXXX` エスケープを表示時にデコードする

Issue: #137

## 概要

`kaji run` のターミナル表示および `console.log` に出力される、エージェント（特に Codex）の MCP ツール結果テキストに含まれる `\uXXXX` 形式の Unicode エスケープシーケンスを、人間可読な文字へデコードしてから表示する。

## 背景・目的

### Observed Behavior（OB）

Codex を agent とするステップで MCP 経由 `gh issue view --json` などを呼ぶと、`console.log` および標準出力に、payload 内の非 ASCII 文字が `\uXXXX` のリテラル（バックスラッシュ + `u` + 16進4桁）のまま現れる。以下は根拠ログ `.kaji-artifacts/135/runs/2604092145/verify-design/console.log:4` の実バイトをそのまま転記したもの（`\u306e` 等は画面上でもバックスラッシュ表記のまま見え、日本語には展開されない）:

```
{"issue": {"url": "https://github.com/apokamo/kaji/issues/135", "title": "config/workflow \u306e\u6697\u9ed9\u30c7\u30d5\u30a9\u30eb\u30c8\u5024\u3092\u5ec3\u6b62..."}}
```

ここで `\u306e` / `\u6697` / `\u9ed9` / `\u30c7` / `\u30d5` は、それぞれ「の」「暗」「黙」「デ」「フ」を表す JSON Unicode エスケープであり、対応文字へ展開されないまま画面に出ている（バックスラッシュ + `u` + 16 進 4 桁の生文字列）。

根拠ログ: `.kaji-artifacts/135/runs/2604092145/verify-design/console.log:4`

### Expected Behavior（EB）

OB と同じ入力に対し、エスケープが解除され `の` / `暗` / `黙` 等の対応文字へ展開されて表示される:

```
{"issue": {"url": "https://github.com/apokamo/kaji/issues/135", "title": "config/workflow の暗黙デフォルト値を廃止..."}}
```

OB との差分: `\u306e` / `\u6697` / `\u9ed9` … のような `\uXXXX` シーケンスが、それぞれ対応する文字（の / 暗 / 黙 …）へ展開されていること。EB は根拠ログ内でエージェントの自然言語出力が日本語で直接表示されているのと同じ可読性を、ツール結果テキストにも与える。

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
4. `CodexAdapter.extract_text`（`kaji_harness/adapters.py:156-172`、`mcp_tool_call` 分岐は `:163-171`）が text をそのまま返却
5. `stream_and_log`（`kaji_harness/cli.py:240-320`）がそのまま `console.log` および stdout に書き出す

**いつから**: `mcp_tool_call` の `result.content[].text` 抽出ロジック（V5/V6 復元時、`adapters.py:163-171` 周辺）導入時から。`gh --json` 等を MCP で呼ぶワークフローが増えてから顕在化。

**他の影響箇所調査**:
- `ClaudeAdapter.extract_text`（`kaji_harness/adapters.py:104-117`）は現状 `assistant.message.content[].text` のみ抽出し、`tool_result` 系イベントは抽出していない（`result` イベントの text 抽出は issue local-p1-14 で廃止済み）。Claude 経由では同症状は表面化していないが、将来 `tool_use_result` を抽出するようになった場合に備え、デコード処理を共通ヘルパに切り出して再利用できる形で実装する
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

> **worktree の main 同期（review Must Fix 対応）**: 設計が引用する `kaji_harness/adapters.py` / `cli.py` の行番号は現行 `main` に一致する。`fix/137` は当初 merge-base `a6d03b5` から `main` に 742 commits 遅れており、旧 worktree ソース（`adapters.py` 119 行、`mcp_tool_call` が旧 `:62-70`）と設計の行番号が食い違っていた。review 指摘に対応して `main` を worktree へ取り込み（競合なしでマージ、`make check` green を確認）、同期後の worktree で `adapters.py:156-172` / `:163-171`・`cli.py:240-320` / `:257` / `:282-288` / `:314` 等の参照が検証可能になった。

## 方針（修正アプローチ）

### 採用案: 「JSON parse → 失敗時に regex フォールバック（サロゲートペア優先マッチ + 孤立サロゲートガード）」

```python
import json
import re

# 順序が重要: 連続2個のサロゲートペア (high + low) を優先的に1マッチとして拾い、
# それに当てはまらない場合のみ単独 \uXXXX として拾う。
# - high surrogate: U+D800..U+DBFF → \uD[89AB][0-9A-F]{2}
# - low surrogate:  U+DC00..U+DFFF → \uD[CDEF][0-9A-F]{2}
_ESCAPE_RE = re.compile(
    r"\\u[dD][89aAbB][0-9a-fA-F]{2}\\u[dD][cdefCDEF][0-9a-fA-F]{2}"  # surrogate pair
    r"|\\u[0-9a-fA-F]{4}"  # BMP 単独
)


def _escape_lone_surrogates(s: str) -> str:
    """孤立サロゲート(U+D800..U+DFFF)を 16 進エスケープ表記へ戻し UTF-8 書き出し可能にする。

    valid surrogate pair は json.loads 時点で単一コードポイント(>=U+10000)へ結合済みのため、
    復号後の str に残る U+D800..U+DFFF は必ず孤立サロゲートである。これにより
    decode_unicode_escapes の戻り値は常に .encode("utf-8") 可能という不変条件を保つ。
    """
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in s):
        return s
    return "".join(
        f"\\u{ord(c):04x}" if 0xD800 <= ord(c) <= 0xDFFF else c for c in s
    )


def decode_unicode_escapes(text: str) -> str:
    if "\\u" not in text:
        return text
    # 第一段: JSON 値全体として parse できれば re-serialize（構造を保ったまま日本語化）
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return _escape_lone_surrogates(json.dumps(parsed, ensure_ascii=False, indent=2))
        if isinstance(parsed, str):
            return _escape_lone_surrogates(parsed)
    except json.JSONDecodeError:
        pass

    # 第二段: 部分的に \uXXXX を含む通常テキスト → サロゲートペア優先で個別復号
    def _sub(m: re.Match[str]) -> str:
        token = m.group(0)
        try:
            decoded = json.loads(f'"{token}"')
        except json.JSONDecodeError:
            return token
        # 孤立サロゲート（high のみ / low のみ）は UTF-8 で書けないので原文維持
        if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
            return token
        return decoded

    return _ESCAPE_RE.sub(_sub, text)
```

理由:
- **JSON 全体 parse（第一段）**: 完全な JSON 出力（`gh --json` の典型ケース）は構造を保ったまま `ensure_ascii=False` で再整形でき、整形済み出力の見やすさも改善する
- **表示整形（`indent=2`）の明示（review Should Fix 対応）**: 第一段の `json.dumps(..., ensure_ascii=False, indent=2)` は escape 解消に加え、compact な 1 行 JSON を複数行インデント表示へ変える。これは意図的な UX 変更で、`gh --json` の長大 payload を人間可読な階層表示にする狙い。raw な 1 行 JSON は `stdout.log` に保持されるため情報損失はない。1 行維持が必要になった場合は `indent=None` へ切り替え可能だが、本 Issue の表示可読化目的に照らし `indent=2` を既定とする
- **サロゲートペア優先マッチ（第二段）**: 正規表現の `|` は左から評価され、`re.compile` レベルで連続ペアを単一マッチとして消費するので、`json.loads(f'"{pair}"')` がペアをまとめて補助平面コードポイント（例: 😀 U+1F600）に復号する
- **孤立サロゲートガード（戻り値の不変条件）**: `decode_unicode_escapes` の戻り値は常に `.encode("utf-8")` 可能（＝孤立サロゲート U+D800..U+DFFF を含まない）ことを不変条件とする。valid surrogate pair は `json.loads` 時点で単一コードポイントへ結合されるため、復号後の str に残る U+D800..U+DFFF は必ず孤立サロゲートであり、最終段の `_escape_lone_surrogates` が `\uXXXX` リテラルへ戻す。第一段の JSON 全体復号（dict/list/str）は nested value の孤立サロゲートも同ヘルパを通すため、`{"title":"lone \uD83D"}` のような入力でも戻り値は UTF-8 書き出し可能になり、`cli.py:257` の `open(log_dir / "console.log", "a", encoding="utf-8")` パスが破壊されない。第二段の部分復号では `_sub` が孤立サロゲート token を原表記のまま維持する（実サロゲート文字を生成しない）。
- **early return (`"\\u" not in text`)**: 通常メッセージへのオーバーヘッドを回避

### サロゲートペアと孤立サロゲートの根拠

- Python `json.loads` は `\uHHHH\uLLLL` の連続が valid surrogate pair を成すとき、対応する補助平面コードポイント1文字へ復号する（CPython `_json.c` `scanstring` 実装）
- 単独 `\uD83D` の `json.loads` 結果は孤立サロゲート 1 文字 `'\ud83d'` で、`'\ud83d'.encode('utf-8')` は `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud83d' in position 0: surrogates not allowed` を送出する（実測確認済み）
- これは `cli.py:257` の `open(log_dir / "console.log", "a", encoding="utf-8")` 経由の書き出しを失敗させるので、孤立サロゲートを下流に流さないことが必須要件

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
   - 期待: 戻り値に `の暗黙` を含み、literal escape `\u306e` を含まない
   - **修正前は FAIL、修正後は PASS** することを確認
2. **JSON parse 不能な部分文字列ケース**: `text` が `"prefix \\u3042 suffix"` のようなプレーンテキスト + 部分エスケープ
   - 期待: `prefix あ suffix`
3. **エスケープを含まない通常テキスト**: `"hello world"` → そのまま `"hello world"` を返す（フォールバック動作）
4. **サロゲートペア（連続2個）**: `"\\uD83D\\uDE00"` → `😀`（U+1F600 補助平面 1 文字）
5. **孤立サロゲート（high のみ / low のみ）**: `"\\uD83D"` → `"\\uD83D"` のまま返す（復号して孤立サロゲートを下流に流すと `console.log` の UTF-8 書き出しが `UnicodeEncodeError` で失敗するため）
6. **孤立サロゲートが UTF-8 書き出し可能であること**: 上記 5 の戻り値を `.encode("utf-8")` してエラーにならないことを assert（`stream_and_log` 経路の不変条件として固定）
7. **空 / None ガード**: 既存の挙動（None 返却、空文字スキップ）が維持されること
8. **不正なエスケープ**: `"\\uZZZZ"` のような broken sequence → そのまま残す（クラッシュしない）
9. **混在ケース**: ペア + 単独 + 孤立 + 通常テキストが混ざった文字列（例: `"prefix \\uD83D\\uDE00 mid \\u3042 lone \\uD800 tail"`）→ ペアと BMP のみ復号、孤立はリテラル維持、通常テキストはそのまま

> **表示経路 2 sink のカバレッジ根拠**: `stream_and_log`（`cli.py:282-288`, `:314`）は `adapter.extract_text(event)` の戻り値を `texts` に集約し、`console.log`（`f_con.write`）と `CLIResult.full_output`（`"\n".join(texts)`）の **両方** に同一文字列を流す。したがって `CodexAdapter.extract_text` の戻り値を assert する上記テストは、Issue 完了条件が要求する「`console.log` / `CLIResult.full_output` の双方に `config/workflow の暗黙` が含まれ、OB 節に示した未デコードの Unicode escape リテラル（バックスラッシュ + `u` + 16 進 4 桁表記）が残らない」ことを共通上流で担保する。両 sink を直接 assert する統合テストは下記 Medium テストで追加する。

#### Medium テスト（必須・両 sink 直接検証）

`tests/test_cli_streaming_integration.py`（`@pytest.mark.medium` / `TestStreamAndLog` パターン）に、Codex `mcp_tool_call` の二重エンコード fixture を `stream_and_log` に流す統合テストを 1 本追加する。既存 `_create_mock_cli_script` で下記 JSONL を出力する mock CLI を生成し、`CodexAdapter` で `stream_and_log(process, adapter, step_id, log_dir, verbose=False)` を駆動する。

- JSONL: `thread.started` → `item.completed`（`mcp_tool_call`、`result.content[].text` に literal `\uXXXX` を含む二重エンコード payload） → `turn.completed`
- fixture text 例（`\u306e` 等は literal backslash + u + 4 桁）: `{"title": "config/workflow \u306e\u6697\u9ed9"}`

assert 対象（Issue 完了条件「`console.log` と `CLIResult.full_output` の双方をテストで確認」を直接固定）:

1. `result.full_output` に `config/workflow の暗黙` を含み、literal escape（`\u306e` / `\u6697` / `\u9ed9`）を含まない
2. `(log_dir / "console.log").read_text()` に `config/workflow の暗黙` を含み、同 literal escape を含まない
3. `(log_dir / "stdout.log").read_text()`（raw JSONL）には二重エンコードされた literal escape がそのまま残る（表示可読化後も生イベントの検証可能性が失われないことを固定）

このテストは Small（adapter 単体）では担保できない「adapter → `stream_and_log` → 2 sink + raw log」の配線と期待値の結合を直接固定する。修正前は `console.log` / `full_output` に escape が残るため FAIL、修正後 PASS。

#### Large テスト

不要。実 API 疎通や Codex CLI E2E を伴う検証は本修正の妥当性確認に必要なく、CI コストに見合わない。Issue の完了条件にある「実機での `console.log` 確認」は変更固有の手動検証として `/i-dev-final-check` 段階で実施し、恒久テストには昇格しない（後述）。

### 変更固有検証（手動）

- 再現環境で `kaji run .kaji/wf/dev.yaml 135 --step verify-design` を実行し、`grep -P '\\u[0-9a-f]{4}' .kaji-artifacts/.../console.log` がヒットしないことを確認
- `make check` 通過（既存テストにデグレなし）

### Claude / Gemini 経路への適用（完了条件の改定提案を含む）

#### 現状の事実確認（一次情報）

- **ClaudeAdapter.extract_text** (`kaji_harness/adapters.py:104-117`): `event.type == "assistant"` の `message.content[].text` のみ抽出（`result` イベントの text 抽出は issue local-p1-14 で廃止済み）。`user` イベントの `tool_result` content は抽出経路に **存在しない**
- **GeminiAdapter.extract_text** (`kaji_harness/adapters.py:220-224`): `event.type == "message" and event.role == "assistant"` の `content` 文字列のみ抽出。ツール結果イベントは抽出対象外
- **Codex のみ** が `mcp_tool_call.result.content[].text`（`adapters.py:163-171`）を抽出して `console.log` に流している

つまり、現時点で `gh --json` の二重エンコード結果が `console.log` に流入し得るのは Codex 経路のみ。Claude/Gemini ではそもそも tool 結果テキストが表示パスに乗らないため、可読化対象が存在しない。

#### Issue 完了条件との整合（改定は Issue 本文に反映済み）

初版設計では Issue 本文の旧完了条件「Codex 経路だけでなく Claude adapter 経路でも同様に可読化されることを確認」が現状の adapter 設計と整合しない（Claude/Gemini は tool_result を表示しない）と指摘し、対象外化を提案した。この提案は Issue 本文の readiness 修正（2026-07-08、コメント `## レディネス指摘への対応報告`）で採用済みで、現行 Issue の完了条件は次の形に更新されている:

> Claude / Gemini adapter は現状 tool result テキストを表示経路に流していないことをテストまたは既存実装確認で固定し、本 Issue では実装対象外であることを明示する

本設計はこの現行完了条件と整合する。将来これらの adapter で tool_result 抽出が追加された場合に備え、`decode_unicode_escapes` を adapter モジュール内の共通ヘルパとして配置し、即座に流用できる構造にしておく。

整合根拠（一次情報）:
- `kaji_harness/adapters.py:104-117` （ClaudeAdapter 現行実装）
- `kaji_harness/adapters.py:220-224` （GeminiAdapter 現行実装）
- `kaji_harness/cli.py:282-288` （`stream_and_log` は `adapter.extract_text(event)` の戻り値のみを `console.log` に書く。adapter が None を返すイベントは `console.log` に現れない）

このスコープ限定により、本 Issue の対象は「実際に表示が壊れている Codex 経路の修正」に閉じる。Claude/Gemini への横展開は、`tool_result` 抽出を追加するという別のスコープ判断（UX 上 tool 出力を表示させたいか）が伴うため、必要になった時点で別 Issue として切るのが筋。

#### 本 Issue で実装するテスト（Claude/Gemini 部分）

`tests/test_adapters.py` に以下を追加:

- **Claude 回帰防止**: `user` イベント `{"type":"user","message":{"content":[{"type":"tool_result","content":[{"type":"text","text":"\\u306e"}]}]}}` を `ClaudeAdapter.extract_text` に渡し、戻り値が `None` であること（= `console.log` に流れないこと）を assert。将来この挙動が変わる場合は本テストが落ちて、`decode_unicode_escapes` 適用の必要性に気付ける
- **Gemini 同等**: ツール結果類似イベントを `GeminiAdapter.extract_text` に渡し `None` が返ることを assert

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
| 再現ログ | `.kaji-artifacts/135/runs/2604092145/verify-design/console.log`（main working tree にのみ存在。`.gitignore` の `.kaji-artifacts/` 対象で fresh worktree には無い） | OB の根拠。同ログに未デコードの Unicode escape リテラルが残存することを `grep -P '\\u[0-9a-f]{4}'` で確認済み（`の` / `暗` / `黙` / `デ` / `フ` に対応する 5 種以上がヒット）。gitignore 対象のため OB は本設計 OB 節と Issue 本文に同一抜粋を inline 転記し、再現テストは実ログ非依存の擬似 `mcp_tool_call` イベントで固定する |
| Codex MCP イベント抽出ロジック（既存実装） | `kaji_harness/adapters.py:156-172`（`mcp_tool_call` 分岐は `:163-171`） | 修正対象箇所。`mcp_tool_call.result.content[].text` を返す現行コード |
| ClaudeAdapter 現行実装 | `kaji_harness/adapters.py:104-117` | tool_result を抽出していないこと（Claude/Gemini 対象外化の根拠）|
| GeminiAdapter 現行実装 | `kaji_harness/adapters.py:220-224` | tool_result を抽出していないこと（Claude/Gemini 対象外化の根拠）|
| ストリーム書き出し（UTF-8） | `kaji_harness/cli.py:240-320`（特に `:257` で `console.log` を `encoding="utf-8"` で open、`:282-288` で `extract_text` 戻り値のみ書き出し、`:314` で `full_output`）| 孤立サロゲートを下流に流すと `UnicodeEncodeError` が発生する根拠。adapter 出口で孤立サロゲートを除去する必要性の裏付け |
| Python 文字列 UTF-8 エンコード仕様 | https://docs.python.org/3/library/codecs.html#error-handlers | 「`'strict'` ハンドラは sys.flags.utf8_mode に関わらずサロゲートを拒否する」: 孤立サロゲートを `encoding="utf-8"` で書けない仕様根拠（実測でも `UnicodeEncodeError: surrogates not allowed` を確認） |
| Unicode サロゲートペア仕様 | https://www.unicode.org/faq/utf_bom.html#utf16-2 | UTF-16 surrogate range: high U+D800..U+DBFF, low U+DC00..U+DFFF。BMP 外の文字は high+low の連続ペアで表現される。本修正の regex `\\uD[89AB][0-9A-F]{2}\\uD[CDEF][0-9A-F]{2}` の根拠 |
| RunLogger 設計方針 | `docs/reference/python/logging.md` | console.log は人間可読アーティファクトという EB の根拠 |
| 関連 Issue | #135（再現元ワークフロー）、#167（CLI 仕様変更追従、独立） | 文脈共有 |
