---
id: local-pc5090-14
title: kaji run の Claude ステップで進捗が見えず最終メッセージが二重表示される（ClaudeAdapter.extract_text 改修）
state: open
slug: claude-adapter-tool-progress-visibility
labels:
- type:bug
created_at: '2026-05-09T07:22:52Z'
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-pc5090-14`
> **Branch**: `feat/local-pc5090-14`
> **同時対応**: local-pc5090-13

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] ClaudeAdapter.extract_text — tool_use の可視化 + result イベントの責務分離

Issue: local-pc5090-14

## 概要

`kaji_harness/adapters.py` の `ClaudeAdapter.extract_text` を改修し、
(A) `assistant` イベントの `tool_use` / `thinking` ブロックも 1 行サマリで抽出して
ターミナルに進捗表示する、(B) `result` イベントからのテキスト抽出を廃止して
最終メッセージの二重表示を解消する。`extract_cost` は `result` イベントの処理を継続する。

## 背景・目的

### Observed Behavior (OB)

`uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5 --step design`
を 16:08:47 〜 16:10:00 に実行したときの観測（生 JSONL: 580 KB / 112 行,
`.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log`）:

| イベント / コンテンツ種別 | 件数 |
|---|---:|
| `system:init` | 1 |
| `rate_limit_event` | 1 |
| `assistant` | 58 |
| ├ `tool_use` ブロック | 50（Read 24 / Bash 17 / TodoWrite 5 / Skill 1 / ToolSearch 1 / Grep 1 / Write 1） |
| ├ `thinking` ブロック | 6（全て `thinking` フィールド長 0、`signature` のみ。Extended Thinking redacted 形式） |
| └ `text` ブロック | 2（初手 + 最終 verdict のみ） |
| `user`（tool_result） | 51 |
| `result:success` | 1 |

#### 異常 A: ツール呼び出しの不可視化

73 秒間、ターミナルには下記 2 行しか出力されない:

```
[2026-05-09T16:08:47] [design] 設計書ができたのでコミットして Issue にコメント。
[2026-05-09T16:10:00] [design] ## 設計書作成完了
```

`tool_use` 50 件と `thinking` 6 件は `extract_text` で `None` を返すため、
`stream_and_log` (`kaji_harness/cli.py:181-187`) の `if text:` 分岐に入らず、
ターミナル表示・`console.log` 書き込み・`full_output` 蓄積のすべてから抜け落ちる。

#### 異常 B: 最終メッセージの二重表示

stdout.log の末尾 2 件（`assistant` 直後の `result:success`）は、`text` フィールドと
`result` フィールドが完全一致（character-for-character、両方 length 2205）:

```
type=assistant ... text length=2205, head: '## 設計書作成完了\n\n| 項目 | 値 |\n...'
type=result    ... result length=2205, head: '## 設計書作成完了\n\n| 項目 | 値 |\n...'
```

`extract_text` は両方を抽出するため、`stream_and_log` がターミナル / `console.log` /
`full_output` の三者に同一内容を 2 回 print する。

### Expected Behavior (EB)

#### 異常 A について

ツール呼び出しが逐次表示され、進捗が分かる。最低でも:

- ツール名（Bash / Read / Edit / Write / Grep / TodoWrite / Skill / ToolSearch / Glob 等）が
  逐次表示される
- ツールごとの主要入力（Bash → command 先頭、Read/Edit/Write → file_path、Grep → pattern、
  Skill → skill name、ToolSearch → query 等）が要約として 1 行に表示される

#### 異常 B について

最終アシスタントメッセージはターミナル / `console.log` / `full_output` のいずれにおいても
1 回だけ現れる。`total_cost_usd` は引き続き `result` イベントから取得できる。

#### 共通の根拠（`adapters.py` 現状コード）

- `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text`:
  - `assistant` の content から `type == "text"` のブロックしか抽出していない
    （→ `tool_use` / `thinking` が捨てられて異常 A 発生）
  - `result` イベントの `result` フィールドからもテキストを抽出している
    （→ 最終 `assistant` text と二重抽出されて異常 B 発生）
- `kaji_harness/adapters.py:55-71` `CodexAdapter.extract_text` の対比:
  - `agent_message` / `reasoning` / `mcp_tool_call` を抽出（reasoning も含むため
    Codex 駆動 step は進捗が見える）
  - `turn.completed`（`result` 相当）から **テキストは抽出していない**（コストのみ）
- `kaji_harness/adapters.py:98-102` `GeminiAdapter.extract_text` も `message` イベントのみ
  抽出し `result` イベントからはコストだけ取る対称設計
- すなわち `ClaudeAdapter` のみが (1) `tool_use` を捨てる、(2) `result` からも text を取る、
  という二重の非対称性を持つ

## 再現手順

1. **前提**: `agent: claude` を含む workflow（例: `.kaji/wf/feature-development-local.yaml`
   の `design` step）と着手済み Issue（例: `local-pc5090-5`、worktree 作成済み）。
2. `uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5 --step design`
   （または `--from design`）を実行。
3. **異常 A**: 初回 assistant text 出力後、最終 assistant text 出力までの間、ターミナル
   出力が完全に停止する。`.kaji-artifacts/<issue>/runs/<run_id>/design/stdout.log` に多数の
   `tool_use` イベントが記録されていることを生 JSONL で確認できる。
4. **異常 B**: ターミナル出力末尾（および `console.log` 末尾）に最終 assistant メッセージが
   連続 2 回現れる。同 stdout.log 末尾に同一テキストの `assistant` / `result` イベントが
   連続して存在することを確認できる。

OB セクション記載の `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log` が
再現済みの 1 次資料であり、回帰テストはこの実観測 JSONL を最小化した fixture で構築する。

## 根本原因

`ClaudeAdapter.extract_text` の実装が、Claude Code stream-json イベントの構造に対して
2 つの非対称性を持っている:

1. **`assistant` content の抽出スコープが `text` ブロックに限定されている** (line 31-33)
   - Claude の `assistant` イベントは `message.content` に複数種のコンテンツブロック
     （`text` / `tool_use` / `thinking`）を持つ。Anthropic Messages API の標準仕様。
   - 現状は `text` だけ抽出しているため、`tool_use` / `thinking` の発生は呼び出し側
     （`stream_and_log`）に届かない。adapter の責務として「assistant が今 何をしているか」
     を表示可能な形に decode して返すべき。
2. **`result` イベントから text と cost の両方を抽出している** (line 34-36, 39-44)
   - Claude Code の `result:success` イベントは「直前の最終 assistant text のリプレイ」
     と「コスト集計」を同時に含む。Codex / Gemini のアダプタは「コスト集計のみ」を
     抽出する対称設計を取っており、Claude だけがリプレイテキストも取り出している。
   - これにより `assistant` の最終 text と `result.result` が `stream_and_log` を 2 回
     通り、二重出力になる。

### いつから壊れているか

`git log -p kaji_harness/adapters.py` 上、`ClaudeAdapter.extract_text` がこの形になった
PR は単一であり（V7 系の adapter 整理時に `result.result` テキスト抽出が追加された）、
追加時点から異常 B は構造的に発生していた。異常 A は Claude Code が `tool_use` ブロックを
ストリーミングするようになった時点から発生していると推定されるが、ユーザー体感としては
LLM 駆動の skill 呼び出しが普及した phase5 期に「沈黙が長すぎる」として顕在化した。

### 同じ原因で他に壊れている箇所

- `CodexAdapter` (`adapters.py:55-71`): `agent_message` / `reasoning` / `mcp_tool_call` を
  抽出する対称設計を取っており、`turn.completed` からは usage のみ。**問題なし**。
- `GeminiAdapter` (`adapters.py:98-102`): `message` イベントのみ抽出し `result` からは
  stats のみ。**問題なし**。
- 結論: 同種の問題は ClaudeAdapter 固有。並行修正は不要。

## インターフェース

`CLIEventAdapter.extract_text` のシグネチャ（`event: dict[str, Any] -> str | None`）は維持。
変更は `ClaudeAdapter` 内の戻り値の **生成ルール** のみ。後方互換性の影響:

| 呼び出し側 | 影響 |
|---|---|
| `stream_and_log` (`cli.py:181-187`) | 戻り値の **量と種類** が増える（`tool_use` / `thinking` 行が追加 / `result` リプレイが消失）。`if text:` の分岐ロジック自体は変更不要。 |
| `verdict.parse_verdict` (`verdict.py`) | 入力 `full_output` に `[tool] ...` 行が混入。後述「方針 §verdict parser への影響評価」で扱う。 |

### 抽出ルール（変更後）

| event.type | 条件 | 戻り値 |
|---|---|---|
| `assistant` | content の各ブロック | 下記「ブロック種別ごとのレンダリング」を改行連結。空なら `None` |
| `result` | （任意） | 常に `None`（cost は `extract_cost` が引き続き処理） |
| その他 | — | `None` |

### ブロック種別ごとのレンダリング

| ブロック type | 出力形式 | 主要入力の選び方 |
|---|---|---|
| `text` | `text` フィールドそのまま | — |
| `tool_use` | `[tool] {Name} {summary}` | 後述「ツール別 summary」 |
| `thinking` | redacted（`thinking` フィールドが空文字列）→ 出力しない（`None` 扱い）。<br>内容あり → `[thinking] {thinking[:160]}` | — |

#### ツール別 summary（決定）

すべて 1 行に収め、要約値は **80 文字で切り詰め**（末尾に `…` を付与）。
未知ツールは `[tool] {Name}` のみ（input 全体の repr は出さない: 安全側）。

| ツール名 | summary フォーマット | 例 |
|---|---|---|
| `Bash` | `command` の先頭（改行は ` ` に置換） | `[tool] Bash $ ls -la` |
| `Read` | `file_path` | `[tool] Read kaji_harness/adapters.py` |
| `Edit` | `file_path` | `[tool] Edit kaji_harness/adapters.py` |
| `Write` | `file_path` | `[tool] Write draft/design/issue-XX.md` |
| `Grep` | `pattern`（先頭 80 文字） | `[tool] Grep extract_text` |
| `Glob` | `pattern` | `[tool] Glob kaji_harness/**/*.py` |
| `TodoWrite` | `len(todos)` 件 | `[tool] TodoWrite (4 items)` |
| `Skill` | `skill` フィールド | `[tool] Skill issue-design` |
| `ToolSearch` | `query` | `[tool] ToolSearch select:TodoWrite` |
| 上記以外 | （summary なし） | `[tool] WebFetch` |

> **設計判断**: 未知ツールで input dict 全体を repr すると、長大な JSON や秘匿情報
> （API キーを含むツール）が VERDICT パーサに混入するリスクがある。安全側に倒し
> ツール名のみ表示する。必要が生じたら future work で増やす。

## 変更スコープ

### 変更ファイル

- `kaji_harness/adapters.py`:
  - `ClaudeAdapter.extract_text` の本体置換
  - `tool_use` レンダリング用ヘルパ追加（モジュール private 関数）
- `tests/test_adapters.py`:
  - `test_extract_text_from_result_event` の **削除または反転**
  - `tool_use` / `thinking` 抽出テストを Small で複数追加
- `tests/test_cli_streaming_integration.py`:
  - `test_claude_streaming_extracts_session_and_text` の `"Done" in result.full_output`
    アサートを **反転**（`"Done" not in result.full_output`）し、`tool_use` 行が出ることの
    Medium テストを追加
- `kaji_harness/cli.py` / `kaji_harness/verdict.py`: **変更なし**（後述参照）

### 変更しないファイル / 機能

- `extract_cost` のロジック（`result` イベントの `total_cost_usd` 抽出）は不変
- `CodexAdapter` / `GeminiAdapter` は不変（同種の問題なし）
- `_build_claude_args` 等の CLI 引数組み立ては不変（`--output-format stream-json --verbose` の
  ままで `tool_use` ブロックは取れる）

## 方針

### 1. `ClaudeAdapter.extract_text` の書き換え（疑似コード）

```python
def extract_text(self, event: dict[str, Any]) -> str | None:
    if event.get("type") != "assistant":
        return None
    content = event.get("message", {}).get("content", [])
    rendered: list[str] = []
    for block in content:
        line = _render_claude_block(block)
        if line:
            rendered.append(line)
    return "\n".join(rendered) if rendered else None


def _render_claude_block(block: dict[str, Any]) -> str | None:
    btype = block.get("type")
    if btype == "text":
        text = block.get("text")
        return text if isinstance(text, str) and text else None
    if btype == "tool_use":
        name = block.get("name", "?")
        summary = _tool_summary(name, block.get("input", {}))
        return f"[tool] {name} {summary}".rstrip()
    if btype == "thinking":
        # signature のみで thinking 文字列が空の redacted ケースは表示しない
        thinking = block.get("thinking")
        if isinstance(thinking, str) and thinking:
            return f"[thinking] {thinking[:160]}"
        return None
    return None  # 未知ブロックは無視


_TOOL_SUMMARY_LEN = 80


def _tool_summary(name: str, inp: dict[str, Any]) -> str:
    match name:
        case "Bash":
            cmd = str(inp.get("command", "")).replace("\n", " ")
            return f"$ {cmd[:_TOOL_SUMMARY_LEN]}"
        case "Read" | "Edit" | "Write":
            return str(inp.get("file_path", ""))[:_TOOL_SUMMARY_LEN]
        case "Grep" | "Glob":
            return str(inp.get("pattern", ""))[:_TOOL_SUMMARY_LEN]
        case "TodoWrite":
            todos = inp.get("todos", [])
            return f"({len(todos)} items)"
        case "Skill":
            return str(inp.get("skill", ""))[:_TOOL_SUMMARY_LEN]
        case "ToolSearch":
            return str(inp.get("query", ""))[:_TOOL_SUMMARY_LEN]
        case _:
            return ""
```

### 2. `result` イベントの責務分離

```python
def extract_text(self, event):
    # ... 上記参照
    # 旧: if event.get("type") == "result": return event.get("result")
    # → 削除。result イベントは extract_cost のみが扱う

def extract_cost(self, event):
    # 変更なし
```

### 3. verdict parser への影響評価

`verdict.py` の 3 段パーサに対し、追加される `[tool] ...` / `[thinking] ...` 行が
誤マッチを引き起こすリスクを評価する:

| パース段 | パターン | `[tool] ...` 行が誤マッチするか |
|---|---|---|
| Step 1 (Strict) | `---VERDICT---\n...\n---END_VERDICT---` | しない（`---` 5 連続デリミタは tool 出力に出ない） |
| Step 2a (Relaxed delim) | `---\s*VERDICT\s*---` | しない（同上） |
| Step 2b (Key-Value) | `status:\s*(PASS\|FAIL\|...)` 等 | **理論上ありうる**: ツール入力に `status: PASS` のような文字列があり、それが summary 80 文字に収まった場合 |

Step 2b の誤マッチリスクへの対処:

- **採用する防御策**: Step 2b の status pattern は `valid_statuses`（例: `{PASS, FAIL, BACK, ABORT}`）
  への完全マッチを要求する正規表現で、かつ Step 1 / 2a が成功すれば Step 2b に到達しない。
  正常系では Verdict 出力の冒頭に `---VERDICT---` が常に含まれるため Step 1 で確定する。
  → **追加の防御コードは入れない**（YAGNI）。
- **将来の予防策（採用しない、メモ）**: もし誤マッチが観測された場合は、`stream_and_log`
  側で `full_output` に追加する際 `[tool]` プレフィックス行をスキップする選択肢があるが、
  そうすると stdout（ターミナル）と `full_output`（verdict parser 入力）の content が
  乖離して保守性が下がるため、現時点では不採用。

> **判断根拠**: 異常 B 解消後は最終 assistant text が `full_output` に 1 回だけ含まれ、
> その中の `---VERDICT---` ブロックが Step 1 で確実に拾われる。Step 2b 到達は Verdict
> フォーマットが崩れた異常系のみで、その時点で `[tool] ...` 行が混じっていても誤マッチ
> 確率は極めて低い（ツール入力に `status: PASS` のような文字列が含まれる前提が必要）。

### 4. 既存テストの扱い

- `tests/test_adapters.py::test_extract_text_from_result_event` (line 64-68):
  - 「`result` イベントから `result` 文字列を抽出する」前提を反転。
  - 新テスト名: `test_extract_text_from_result_event_returns_none`
- `tests/test_cli_streaming_integration.py::test_claude_streaming_extracts_session_and_text`
  (line 38-70):
  - `"Done" in result.full_output` を `"Done" not in result.full_output` に反転
  - 別途 `tool_use` を含む JSONL fixture を追加して `[tool] ...` 行が `full_output` に
    含まれることを assert する Medium テストを新規追加
- `extract_cost` 系テスト（`test_extract_cost_from_result_event` 等）は **変更不要**
  （`result` イベントから `total_cost_usd` を取り続ける）

## テスト戦略

### 変更タイプ

実行時コード変更（adapter のテキスト抽出ロジックの振る舞い変更）。`docs-only` /
`metadata-only` ではないため、Small / Medium 双方で恒久回帰テストを定義する。

### Small テスト（`tests/test_adapters.py`）

すべて `@pytest.mark.small`。`ClaudeAdapter` 単体に対する純粋ロジックテスト:

#### 既存テストの修正

- `test_extract_text_from_assistant_message`: そのまま PASS（text 単独ブロックの正常系）
- `test_extract_text_from_result_event`: **削除 or 反転**
  → `test_extract_text_from_result_event_returns_none` として残す
- `test_extract_cost_from_result_event` / `test_extract_cost_from_result_event_with_usd`:
  変更なし

#### 新規追加（OB 再現を含む）

- **異常 A 再現テスト群**:
  - `test_extract_text_from_tool_use_bash`: `Bash` の `command` が summary に出る
  - `test_extract_text_from_tool_use_read`: `Read` の `file_path` が summary に出る
  - `test_extract_text_from_tool_use_edit`: `Edit` の `file_path` が出る
  - `test_extract_text_from_tool_use_write`: `Write` の `file_path` が出る
  - `test_extract_text_from_tool_use_grep`: `Grep` の `pattern` が出る
  - `test_extract_text_from_tool_use_glob`: `Glob` の `pattern` が出る
  - `test_extract_text_from_tool_use_todowrite`: `(N items)` が出る
  - `test_extract_text_from_tool_use_skill`: `Skill` の `skill` 名が出る
  - `test_extract_text_from_tool_use_toolsearch`: `ToolSearch` の `query` が出る
  - `test_extract_text_from_tool_use_unknown`: 未知ツールはツール名のみ
  - `test_tool_summary_truncated_at_80_chars`: 切り詰め長の境界
- **thinking ブロック**:
  - `test_extract_text_from_thinking_redacted_returns_none`: `thinking` 空文字列のみ
    （Extended Thinking redacted）→ `None`
  - `test_extract_text_from_thinking_with_content`: 内容ありなら `[thinking] ...`
- **複合ブロック**:
  - `test_extract_text_mixed_text_and_tool_use`: `text` + `tool_use` 同居 →
    `\n` 連結された 1 つの文字列
- **異常 B 再現テスト**:
  - `test_extract_text_from_result_event_returns_none`: `result` イベントから text を
    返さないことを assert（旧 test の反転版）

### Medium テスト（`tests/test_cli_streaming_integration.py`）

すべて `@pytest.mark.medium`。Mock CLI script + `stream_and_log` を通した結合テスト:

- **異常 A 再現テスト**: `test_claude_streaming_renders_tool_use_lines`
  - `tool_use` を含む JSONL ストリームを mock CLI から流す
  - `result.full_output` に `[tool] Bash` / `[tool] Read ...` 等の文字列が含まれること
  - `console.log` にも同じ行が書き込まれていること
- **異常 B 再現テスト**: `test_claude_streaming_no_duplicate_final_text`
  - `assistant` の最終 text と直後の `result:success` の `result` が同一テキストの
    JSONL fixture を流す
  - `result.full_output` 内で当該テキストが **1 回しか** 出現しないこと
    （`result.full_output.count("最終テキスト") == 1`）
- **既存テストの反転**: `test_claude_streaming_extracts_session_and_text`
  - `"Done" in result.full_output` → `"Done" not in result.full_output` に反転
  - `result.cost.usd == 0.05` は維持（`extract_cost` は不変）

### Large テスト

不要。理由:

- 本変更は Claude Code CLI 固有の JSONL イベント構造に対するパーサ変更であり、
  実 CLI を起動しなくても JSONL fixture（実観測ログ
  `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log` の最小化）で
  完全に再現可能。
- 「実 CLI が将来 JSONL 形式を変えた場合の検知」は本 Issue のスコープ外
  （別途 Claude Code CLI バージョン追従の運用課題）。
- `docs/dev/testing-convention.md` 4 条件のうち「既存ゲートで不具合パターンを捕捉できる」
  「物理的に作成不可」のいずれにも該当はしないが、**回帰価値が Small + Medium で十分に
  確保される**ため Large は不要と判断。

### 手動検証（変更固有・恒久化しない）

- OB セクションで参照した 73 秒の `design` step を再実行し:
  1. ツール呼び出しが連続表示されること（異常 A 解消）
  2. 最終メッセージが 1 回しか出ないこと（異常 B 解消）
  3. `total_cost_usd` がログに記録されること（cost 抽出の回帰なし）
- 上記は手動目視確認に留め、CI 化はしない（実 CLI を呼ぶため）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定は不変（既存 adapter pattern 内の挙動修正） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ非変更 |
| `docs/dev/` | なし | ワークフロー手順非変更 |
| `docs/reference/` | なし | API 仕様変更なし（adapter は internal） |
| `docs/cli-guides/` | なし | CLI 仕様非変更（`kaji run` の挙動は変わらないが、表示が増えるだけ） |
| `CLAUDE.md` | なし | 規約非変更 |
| `docs/operations/` | なし | 運用手順非変更 |

> **補足**: 本変更は internal adapter 改修であり、ユーザー観測上は「ターミナル出力が
> 増える + 最終メッセージが二重で出ていたのが 1 回になる」だけ。コマンド体系・設定・
> ファイル配置に変更がないため、ドキュメント更新は不要。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 観測ログ（生 JSONL） | `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log` | 112 行の生 JSONL。assistant 58 / tool_use 50 / thinking 6 / text 2 の分布、末尾 assistant text と result.result が完全一致（length 2205）であることを集計で確認（OB セクション参照） |
| `ClaudeAdapter` 現状 | `kaji_harness/adapters.py:29-37` | `assistant` の content から `text` のみ抽出、`result` から text + cost を抽出する現在の実装。改修対象 |
| `CodexAdapter` 対比 | `kaji_harness/adapters.py:55-71` | `agent_message` / `reasoning` / `mcp_tool_call` を抽出し `turn.completed` からは usage のみ。Claude が取るべき対称設計の参照モデル |
| `GeminiAdapter` 対比 | `kaji_harness/adapters.py:98-102` | `message` のみ抽出し `result` からは stats のみ。同上 |
| `stream_and_log` 呼び出し側 | `kaji_harness/cli.py:141-216` | `extract_text` の戻り値 `text` が真値なら `texts.append(text)` / `f_con.write` / `print` に流れる。adapter 戻り値の使い方は変更不要 |
| `verdict.parse_verdict` | `kaji_harness/verdict.py:199-299` | 3 段パーサ。Step 1 (`---VERDICT---`) → Step 2a (relaxed delim) → Step 2b (key-value) → Step 3 (AI formatter)。Step 2b の status pattern が valid_statuses 限定なので、`[tool] ...` 行は誤マッチしないと評価 |
| 既存テスト（修正対象） | `tests/test_adapters.py:64-68` (`test_extract_text_from_result_event`) / `tests/test_cli_streaming_integration.py:38-70` (`test_claude_streaming_extracts_session_and_text`) | 異常 B の根本原因を「正しい挙動」として固定している既存テスト。反転または削除が必要 |
| Anthropic Messages API（content blocks 仕様） | https://docs.claude.com/en/api/messages | assistant の `content` 配列に `text` / `tool_use` / `thinking` 等のブロック型が含まれる Messages API 標準仕様。Claude Code stream-json はこの構造をそのままラップしてストリームする |

## 完了条件の段階確認（設計段階）

Issue 本文 `## 完了条件 / ### 設計段階で確認` の各項目に対する設計書での対応:

- [x] `ClaudeAdapter.extract_text` で抽出する `assistant` content type の決定
  → 「インターフェース §抽出ルール」「方針 §1」で `text` + `tool_use` + `thinking`
  （内容あり時のみ）を抽出すると明記
- [x] `tool_use` の表示形式（ツール名 + 主要入力の選び方 + 切り詰め長）が決定
  → 「インターフェース §ツール別 summary」で 9 ツール + 未知ツール、切り詰め 80 文字を確定
- [x] `thinking` ブロックの扱い（redacted 時の表示有無、内容ありの場合の表示形式）が決定
  → 「インターフェース §ブロック種別ごとのレンダリング」で redacted は出さない、
  内容ありは `[thinking] {head 160 chars}` と確定
- [x] `result` イベントからの text 抽出を廃止する方針が、コスト抽出 (`extract_cost`) との
  責務分離とともに文書化
  → 「インターフェース §抽出ルール」で `result → 常に None`、`extract_cost` は不変と明記
- [x] `full_output` に tool_use 表示行が混入することによる verdict parser への影響評価
  → 「方針 §3 verdict parser への影響評価」で 3 段パーサそれぞれの誤マッチリスクを
  評価し、追加の防御コードは入れない判断と根拠を記載
- [x] CodexAdapter / GeminiAdapter で同種の進捗欠落・重複抽出が無いことの調査結果
  → 「根本原因 §同じ原因で他に壊れている箇所」「インターフェース §抽出ルール」で
  両者は対称設計のため問題なしと記載

実装段階で確認する条件（再現テスト追加・既存テスト反転・`make check` 通過・手動目視）は
「テスト戦略」セクションで具体テスト名と期待挙動を確定済み。

</details>


## 概要

`kaji run` で `agent: claude` の step（design / fix-design / implement / fix-code 等）を実行すると `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text` の現実装に起因する 2 つの観測異常が同時に発生する。両方とも同一関数の責務（`assistant` content / `result` event のうちどれを extract するか）の見直しで解消するため、1 つのスコープに統合して扱う。

- **異常 A（ツール呼び出しの不可視化）**: ツール（Bash / Read / Edit / Write / Grep / TodoWrite / Skill / ToolSearch 等）の連続実行中、ターミナル出力が完全に止まる。最終 assistant text までの数十秒〜数分間、進捗が一切見えない。
- **異常 B（最終メッセージの二重表示）**: 最終アシスタントメッセージがターミナル / `console.log` / `full_output` の全てに同一内容で連続 2 回現れる。

## 目的

### Observed Behavior（OB）

#### 異常 A: ツール呼び出しの不可視化

`uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5` の `[design]` step は 16:08:47 〜 16:10:00 の 73 秒間走ったが、ターミナルには次の 2 行しか出力されなかった:

```
[2026-05-09T16:08:47] [design] 設計書ができたのでコミットして Issue にコメント。
[2026-05-09T16:10:00] [design] ## 設計書作成完了
```

実体ログ `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log`（580 KB）の生 JSONL を集計したところ、その 73 秒間に大量のツール呼び出しが発生していた:

| イベント / コンテンツ種別 | 件数 |
|---|---|
| `assistant` イベント | 58 |
| ├ `tool_use` ブロック | 50（Read 24 / Bash 17 / TodoWrite 5 / Skill 1 / ToolSearch 1 / Grep 1 / Write 1）|
| ├ `thinking` ブロック | 6（全て `thinking` フィールド長 0、Extended Thinking redacted 形式）|
| └ `text` ブロック | 2（初手 + 最終 verdict のみ）|

50 件の `tool_use` と 6 件の `thinking` はターミナルに 1 行も現れない。

#### 異常 B: 最終メッセージの二重表示

同一 step の verbose 出力末尾で、"## 設計書作成完了 ... ---END_VERDICT---" ブロックが完全に同一の内容で連続 2 回 print された:

```
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (約 30 行の同一ブロック) ...
---END_VERDICT---
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (まったく同じ約 30 行) ...
---END_VERDICT---
```

同じ stdout.log 末尾の生 JSONL を検査すると、最後の `assistant` イベントと、その直後の `result` イベント（`result` フィールド）に同一テキストが格納されている:

```
$ tail -n 5 stdout.log | python3 -c "import sys,json; [print(json.loads(l).get('type'), '|', ...) for l in sys.stdin]"
assistant | ## 設計書作成完了 ...
result    | ## 設計書作成完了 ...
```

### Expected Behavior（EB）

#### 異常 A について

Claude を直接 CLI 実行した場合と同様、ツール呼び出しがターミナルに逐次表示され、進捗が分かる。最低でも:
- ツール名（Bash / Read / Edit / Write / Grep / TodoWrite / Skill / ToolSearch 等）が逐次表示される
- ツールごとの主要入力（Bash → command の先頭、Read/Edit/Write → file_path、Grep → pattern、Skill → skill name、ToolSearch → query 等）が要約として 1 行に表示される

#### 異常 B について

最終アシスタントメッセージはターミナル / `console.log` / `full_output` のいずれにおいても 1 回だけ現れる。コスト情報（`total_cost_usd`）は引き続き `result` イベントから取得できる。

#### 共通の根拠

- `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text` の現実装:
  - `assistant` イベントの content から `type == "text"` のブロックしか抽出していない（→ tool_use / thinking が捨てられて異常 A 発生）
  - 加えて `result` イベントの `result` フィールドからもテキストを抽出している（→ 最終 assistant text と二重抽出されて異常 B 発生）
- `kaji_harness/adapters.py:55-71` `CodexAdapter.extract_text` は対比設計として:
  - `agent_message` / `reasoning` / `mcp_tool_call` を抽出（reasoning も含むため Codex 駆動 step は進捗が見える）
  - `result` 相当の `turn.completed` から **テキストは抽出していない**（コストのみ）
- `kaji_harness/adapters.py:98-102` `GeminiAdapter.extract_text` も `message` イベントのみを抽出し `result` イベントからはコストだけ取る対称設計
- すなわち Claude Adapter のみが (1) tool_use を捨てる、(2) result からも text を取る、という二重の非対称性を持つ

### 再現手順（Steps to Reproduce）

1. 前提: `agent: claude` を含む workflow（例: `.kaji/wf/feature-development-local.yaml` の `design` step）と着手済み Issue（例: `local-pc5090-5`、worktree 作成済み）。
2. `uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5 --step design`（または `--from design`）を実行。
3. 異常 A: 初回の assistant text 出力後、最終 assistant text 出力までの間、ターミナル出力が完全に停止する。`.kaji-artifacts/<issue>/runs/<run_id>/design/stdout.log` に多数の `tool_use` イベントが記録されていることを生 JSONL で確認できる。
4. 異常 B: ターミナル出力末尾（および `console.log` 末尾）に最終 assistant メッセージが連続 2 回現れる。同 stdout.log 末尾に同一テキストの `assistant` / `result` イベントが連続して存在することを確認できる。

## 完了条件

### 設計段階で確認

- [x] `ClaudeAdapter.extract_text` で抽出する `assistant` content type の決定（最低でも `text` + `tool_use` を扱う方針が文書化されている）
- [x] `tool_use` の表示形式（ツール名 + 主要入力の選び方 + 切り詰め長）が決定されている
- [x] `thinking` ブロックの扱い（redacted 時の表示有無、内容ありの場合の表示形式）が決定されている
- [x] `result` イベントからの text 抽出を廃止する方針が、コスト抽出 (`extract_cost`) との責務分離とともに文書化されている（`extract_cost` は `result` を引き続き処理）
- [x] `full_output` に tool_use 表示行が混入することによる verdict parser への影響評価が記載され、必要なら parser 側の防御策が決定されている（例: `[tool] ` プレフィックスを VERDICT 行と誤認しないこと）
- [x] CodexAdapter / GeminiAdapter で同種の進捗欠落・重複抽出が無いことの調査結果が設計書に記載されている

### 実装段階で確認

- [x] 異常 A の再現テスト追加: `tool_use` を含む Claude JSONL ストリームに対し `full_output` / `console.log` にツール呼び出し情報が含まれることを assert
- [x] 異常 B の再現テスト追加: `assistant` 直後の `result` イベントを含む JSONL ストリームに対し `full_output` 内の最終テキストが 1 回しか現れないことを assert
- [x] 既存テストの期待値見直し:
  - `tests/test_adapters.py::test_extract_text_from_result_event`（result event から text が返る前提を反転または削除）
  - `tests/test_cli_streaming_integration.py::test_claude_streaming_extracts_session_and_text`（`"Done" in result.full_output` を反転）
- [x] `total_cost_usd` の取得が `result` イベント経由で従来どおり機能することを既存テストまたは追加テストで確認
- [x] `make check` 通過
- [x] 73 秒の実例 step を再実行し、(1) ツール呼び出しが連続表示され、(2) 最終メッセージが 1 回しか出ないことを目視確認

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - `kaji_harness/adapters.py`（`ClaudeAdapter.extract_text`、`tool_use` レンダリング用ヘルパ）
  - 影響を受ける workflow step: `agent: claude` の全 step（design / fix-design / implement / fix-code / その他 claude 駆動 skill）
  - 副次的に verdict parser（`kaji_harness/verdict.py` 配下）への影響評価
- 深刻度: medium
  - 異常 A は UX 大（長時間沈黙はハング・エラーとの区別がつかず、Ctrl-C の判断材料を奪う）
  - 異常 B は表示の乱れ + verdict parser 入力（`full_output`）の重複による誤読リスク
- 回避策の有無: なし（生 JSONL の `stdout.log` を別端末で `tail -f` してパースすれば見える、という非現実的な回避策のみ）

## 参考

- 統合された Issue:
  - **local-pc5090-13**（duplicate close 済み）— 異常 B（最終メッセージ二重表示）単独。同一関数 `ClaudeAdapter.extract_text` を触るため当 Issue に統合した
- 関連コード:
  - `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text`（修正対象）
  - `kaji_harness/adapters.py:55-71` `CodexAdapter.extract_text`（対比対象）
  - `kaji_harness/adapters.py:98-102` `GeminiAdapter.extract_text`（対比対象）
  - `kaji_harness/cli.py:141-216` `stream_and_log`（adapter 戻り値の使われ方）
- 1 次情報（観測ログ）:
  - `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log`（580 KB, 73 秒分の生 JSONL）
  - assistant イベント分布: tool_use 50 / thinking 6 / text 2
  - 末尾に同一テキストの assistant / result イベントが連続して存在
- 関連ドキュメント:
  - `docs/dev/workflow_overview.md`（workflow 実行の前提）
  - 設計書配置予定: `draft/design/issue-local-pc5090-14-claude-adapter-tool-progress.md`
