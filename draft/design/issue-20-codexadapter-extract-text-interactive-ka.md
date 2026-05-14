# [設計] CodexAdapter.extract_text — interactive 表示と同等の進捗を kaji terminal に出す

Issue: #20

## 概要

`kaji_harness/adapters.py` の `CodexAdapter.extract_text` を改修し、現状捨てられている
`command_execution` / `file_change` / `web_search` の `item.completed`（および
`command_execution` の `item.started`）をターミナル表示用に 1 行〜複数行のサマリへ
レンダリングする。`stdout.log`（raw JSONL）と `extract_cost` / 端末イベント判定は不変。
本変更は ClaudeAdapter の先行改修（local-pc5090-14）の Codex 対応版に相当する。

## 背景・目的

### ユースケース

- **開発者として**、`kaji run` の codex step 実行中に「いまどのコマンドを実行しているか」
  「どのファイルを編集したか」「何を検索したか」を逐次見たい。沈黙が長いとハング/エラー
  との区別がつかず Ctrl-C 判断が出来ないため。
- **デバッグ担当として**、`.kaji-artifacts/<issue>/runs/<run_id>/<step>/console.log` を
  読むだけで Codex の作業を時系列で追跡したい。生 JSONL (`stdout.log`) を毎回パース
  するのは現実的でないため。
- **`stdout.log` を一次資料として使う運用担当として**、raw イベントは欠落させない。
  事後検証で full event stream を再生する用途を守るため（既に全件保存されている挙動を
  維持する）。

### 観測された現状（一次情報）

`codex-cli 0.124.0` の `codex exec --json` を `/tmp/codex-probe` で実行した観測
（本 commit 同梱の [`issue-20-codex-jsonl-observation.jsonl`](./issue-20-codex-jsonl-observation.jsonl)
を参照）から、Codex の JSONL イベント構造は以下:

| event.type | item.type | 現 `CodexAdapter.extract_text` の挙動 |
|---|---|---|
| `item.completed` | `agent_message` | extract 済み（本文） |
| `item.completed` | `reasoning` | extract 済み（本文） |
| `item.completed` | `mcp_tool_call` | extract 済み（`result.content[*].text` を改行連結） |
| `item.completed` | **`command_execution`** | `None`（捨てる） |
| `item.completed` | **`file_change`** | `None`（捨てる） |
| `item.completed` | **`web_search`** | `None`（捨てる） |
| `item.started` | `command_execution` / `file_change` / `web_search` | `event.type != "item.completed"` で `None` |
| `turn.completed` | — | `extract_cost` 経路（変更なし） |

実観測 (Sample 1) では 1 ターンの中で `agent_message`(3) + `command_execution`(1) +
`file_change`(1) + `web_search`(1) が発火しているが、ターミナルには
`agent_message` の 3 件しか出ない。複雑な作業（複数 `command_execution` を含む）では
最初と最後の `agent_message` の間がほぼ無音になる。

### ClaudeAdapter の先行例（対称性の根拠）

local-pc5090-14 で ClaudeAdapter は同種改修を完了している（`kaji_harness/adapters.py:78-117`）:

- `assistant.content[*].type == "tool_use"` を `[tool] {Name} {summary}` でレンダリング
- 共通ヘルパ `_tool_summary` がツール別の主要入力を 80 文字 truncate で要約
- `_truncate` ヘルパで先頭 N 文字 + 末尾 `…`

Codex 側は片肺状態。同じユーザー体験要求（interactive と同等の進捗可視化）に対し
Claude 側のみ対応済みという非対称を解消する。

## インターフェース

`CLIEventAdapter.extract_text` のシグネチャ（`event: dict[str, Any] -> str | None`）は不変。
変更は `CodexAdapter.extract_text` の戻り値の **生成ルール** のみ。
`extract_session_id` / `extract_cost` / `is_terminal_event` / `is_terminal_failure` は不変。

### 入力

| 引数 | 型 | 内容 |
|---|---|---|
| `event` | `dict[str, Any]` | Codex CLI が `--json` モードで stdout に流す JSONL の 1 行をパースした dict |

### 出力（変更後の抽出ルール）

| event.type | item.type / 条件 | 戻り値 |
|---|---|---|
| `item.completed` | `agent_message` | `item["text"]`（既存挙動、空なら `None`） |
| `item.completed` | `reasoning` | `item["text"]`（既存挙動、空なら `None`） |
| `item.completed` | `mcp_tool_call` | `result.content[*].text` を `"\n"` 連結（既存挙動） |
| `item.completed` | **`command_execution`** | `[exec] $ <cmd 80字>\n<aggregated_output の head/tail 省略>` ＋ `exit_code≠0` なら末尾 `[exit=N]` |
| `item.completed` | **`file_change`** | `changes[*]` を 1 行ずつ `[edit] <kind> <path>` で改行連結 |
| `item.completed` | **`web_search`** | `[search] <query 80字>`。`query` が空文字なら `None` |
| `item.started` | **`command_execution`** | `[exec] $ <cmd 80字>` の 1 行 |
| `item.started` | `file_change` / `web_search` / `mcp_tool_call` | **`None`**（完了時のみ表示する設計判断） |
| `item.started` | その他 | `None` |
| `turn.started` / `turn.completed` / `thread.started` / `error` 等 | — | `None` |

### 副作用

- ターミナル（`stream_and_log` の `print`）と `console.log` への書き込みが増える
- `CLIResult.full_output` に `[exec]` / `[edit]` / `[search]` プレフィックス行が混入する
  （`verdict.parse_verdict` の入力に渡る）

### 使用例

```python
adapter = CodexAdapter()

# command_execution started
adapter.extract_text({
    "type": "item.started",
    "item": {
        "type": "command_execution",
        "command": "/bin/bash -lc 'ls -la /tmp && echo hello > sample.txt'",
        "aggregated_output": "",
        "exit_code": None,
        "status": "in_progress",
    },
})
# -> "[exec] $ /bin/bash -lc 'ls -la /tmp && echo hello > sample.txt'"

# command_execution completed with multi-line output and non-zero exit
adapter.extract_text({
    "type": "item.completed",
    "item": {
        "type": "command_execution",
        "command": "/bin/bash -lc 'bash -c \"for i in $(seq 1 20); do echo line$i; done; exit 5\"'",
        "aggregated_output": "line1\nline2\n...\nline20\n",  # 20 lines
        "exit_code": 5,
        "status": "failed",
    },
})
# -> "[exec] $ /bin/bash -lc 'bash -c \"for i in $(seq 1 20); do echo line$i; …\n"
#    "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
#    "… (5 more lines)\n"
#    "line16\nline17\nline18\nline19\nline20\n"
#    "[exit=5]"

# file_change with single path
adapter.extract_text({
    "type": "item.completed",
    "item": {
        "type": "file_change",
        "changes": [{"path": "/tmp/codex-probe/demo.py", "kind": "add"}],
        "status": "completed",
    },
})
# -> "[edit] add /tmp/codex-probe/demo.py"

# web_search
adapter.extract_text({
    "type": "item.completed",
    "item": {
        "type": "web_search",
        "query": "OpenAI API",
        "action": {"type": "search", "query": "OpenAI API"},
    },
})
# -> "[search] OpenAI API"
```

### レンダリング詳細

#### `command_execution`（`item.started`）

- 出力: `[exec] $ <cmd>` の 1 行
- `cmd` は `item["command"]` を取り、改行を `" "` に置換し、80 文字で truncate
  （`_truncate` ヘルパを ClaudeAdapter と共用、末尾 `…` 付与）
- 既存 `_TOOL_SUMMARY_LEN = 80` を再利用

#### `command_execution`（`item.completed`）

- 1 行目: 上記 `item.started` と同じ `[exec] $ <cmd 80字>`
- 2 行目以降: `aggregated_output` を行単位で省略表示
  - 全体行数 ≤ 15 → そのまま改行付与で出力
  - 全体行数 > 15 → 先頭 10 行 + `… (N more lines)` + 末尾 5 行（`N` = 全行数 - 15）
  - `aggregated_output` が空文字列 / `None` → 出力なし（コマンド行のみ）
  - 末尾 `\n` は保持しないで join する（後段の `stream_and_log` が 1 つの text として
    扱い、`print` 時に自動改行が付くため）
- 末尾: `item["exit_code"]` が int で `0` 以外なら追加行 `[exit=N]`
  - `exit_code` が `None` / 欠落 → 付与しない（実観測では `item.completed` では常に int）
  - `exit_code == 0` → 付与しない（成功時は無音化）

> **設計判断**: head 10 + tail 5 の不均等は「コマンドの主要情報（最初の操作・パス・エラー
> メッセージ）が先頭に出やすく、サマリは末尾に出やすい」という shell 出力の経験則に基づく。
> `kaji_harness/verdict.py:303-315` の AI Formatter `_truncate_for_formatter` が tail 重視
> （1/3 head + 2/3 tail）を採用しているのは「Verdict ブロックが末尾に出る」前提の特殊事情で、
> shell 出力の一般的可読性とは別物。固定値 10 + 5 は magic number だが、設定化は YAGNI として
> 採用しない（将来必要になれば module 定数で外出し）。

#### `file_change`（`item.completed`）

- 出力: `changes` 配列を 1 entry = 1 行 `[edit] <kind> <path>` で改行連結
- `kind` は `item["changes"][i]["kind"]`（実観測値: `"add"`。仕様上 `"update"` /
  `"delete"` も想定だが本観測には未登場）
- `path` は `item["changes"][i]["path"]` をそのまま出力（truncate しない。ファイルパスは
  ユーザーがコピペする可能性が高く、途中で切れると価値が下がるため）
- `changes` が空配列 / 欠落 → `None`

> **設計判断**: `item.started` でも `file_change` は同じ `changes` を持つが、編集前後の
> 状態差（`status: in_progress` vs `completed`）に表示上の差がほぼなく、二重表示は
> ノイズになる。完了時のみ出す（Issue 本文の合意通り）。

#### `web_search`（`item.completed`）

- 出力: `[search] <query 80字>` の 1 行
- `query` は `item["query"]` を取得（実観測では `item["action"]["query"]` にも同じ値が
  入る冗長構造）。`item["query"]` を優先し、欠落時のみ `item["action"]["query"]` を見る
  - これは「最小の表示用情報を取り、未使用フィールドの構造変化に耐える」ためのフォールバック
- truncate は `_truncate(query, 80)`
- `query` が空文字列 → `None`

> **設計判断**: `item.started` 時点では `query` が空文字列（実観測）。完了時のみ
> 出力する Issue 本文合意と整合する。

### エラー / 異常入力の挙動

| ケース | 挙動 |
|---|---|
| `item` が辞書でない | `None`（type check 兼防御） |
| `item.type` が未知 | `None`（既存の falling through 挙動） |
| `command_execution` で `command` が欠落 | `None`（`[exec] $ ` だけのノイズ行を避ける） |
| `command_execution` で `aggregated_output` が `None` | コマンド行のみ出力（出力本体は省略） |
| `file_change` で `changes` が `[]` または非 list | `None` |
| `web_search` で `query` が空・欠落・非 str | `None`（action.query を見ても空なら `None`） |
| `item.started` で type が `command_execution` 以外 | `None`（明示分岐） |

## 制約・前提条件

- **Python 単一スタック**: 既存 adapter モジュール内の private helper 追加のみ。新規依存なし。
- **既存 ClaudeAdapter ヘルパとの共用**: `_truncate` / `_TOOL_SUMMARY_LEN` は再利用
  （リテラル 80 を 2 箇所に分散させない）。
- **後方互換**: `extract_text` の戻り値型 `str | None` は不変。`stream_and_log`
  (`cli.py:170-218`) の `if text:` 分岐は変更不要。
- **`stdout.log` 不変**: raw JSONL の保存 (`f_raw.write(line)`) は adapter の外で行われており
  本変更の対象外。`console.log` への書き込みは新規 `[exec]` / `[edit]` / `[search]` 行が
  混入する（既存テキスト + 増分のみ）。
- **Codex CLI バージョン依存**: 観測対象は 0.124.0。`item.type` enum は将来追加されうるが
  既存 6 type + 未知 type フォールスルー（`None`）の構造で前方互換を保つ。
- **`extract_cost` / `is_terminal_event` / `is_terminal_failure` 不変**:
  これらは `turn.completed` / `turn.failed` を見ており、本変更と直交する。
- **`verdict.parse_verdict` 不変**: 本 Issue では parser 本体は変更しない。影響評価は
  「方針 §verdict parser への影響評価」を参照。

## 変更スコープ

### 変更ファイル

- `kaji_harness/adapters.py`:
  - `CodexAdapter.extract_text` の本体置換
  - private helper 追加: `_render_codex_command_execution_started` /
    `_render_codex_command_execution_completed` / `_render_codex_file_change` /
    `_render_codex_web_search` / `_truncate_command_output`
  - 既存 `_truncate` / `_TOOL_SUMMARY_LEN` 定数を流用（モジュール private のまま）
  - 新規モジュール定数: `_CMD_OUTPUT_HEAD_LINES = 10`, `_CMD_OUTPUT_TAIL_LINES = 5`
- `tests/test_adapters.py`:
  - `TestCodexAdapter` クラスに新規テスト多数追加（後述「テスト戦略」参照）
  - 既存テスト（`test_extract_text_from_agent_message` 等）は不変

### 変更しないファイル / 機能

- `ClaudeAdapter` / `GeminiAdapter`（本 Issue のスコープ外）
- `CodexAdapter` の `extract_session_id` / `extract_cost` / `is_terminal_event` /
  `is_terminal_failure`
- `kaji_harness/cli.py` の `stream_and_log`（adapter 戻り値の使われ方は不変）
- `kaji_harness/verdict.py`（parser ロジック不変。影響評価のみ）
- `stdout.log` の保存形式（raw JSONL のまま）
- workflow YAML / skill 定義（adapter 内部実装の差し替えのため）

## 方針（Minimal How）

### 1. `CodexAdapter.extract_text` の書き換え（疑似コード）

```python
class CodexAdapter:
    def extract_text(self, event: dict[str, Any]) -> str | None:
        etype = event.get("type")
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        itype = item.get("type")

        if etype == "item.completed":
            if itype == "agent_message" or itype == "reasoning":
                text = item.get("text")
                return text if isinstance(text, str) and text else None
            if itype == "mcp_tool_call":
                return _extract_mcp_tool_call_text(item)  # 既存ロジックの helper 化
            if itype == "command_execution":
                return _render_codex_command_execution_completed(item)
            if itype == "file_change":
                return _render_codex_file_change(item)
            if itype == "web_search":
                return _render_codex_web_search(item)
            return None

        if etype == "item.started":
            if itype == "command_execution":
                return _render_codex_command_execution_started(item)
            return None

        return None
```

### 2. helper（疑似コード）

```python
_CMD_OUTPUT_HEAD_LINES = 10
_CMD_OUTPUT_TAIL_LINES = 5


def _render_codex_command_execution_started(item: dict[str, Any]) -> str | None:
    cmd = item.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    cmd_one = cmd.replace("\n", " ")
    return f"[exec] $ {_truncate(cmd_one, _TOOL_SUMMARY_LEN)}"


def _render_codex_command_execution_completed(item: dict[str, Any]) -> str | None:
    cmd = item.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    cmd_one = cmd.replace("\n", " ")
    header = f"[exec] $ {_truncate(cmd_one, _TOOL_SUMMARY_LEN)}"

    output = item.get("aggregated_output")
    body = _truncate_command_output(output) if isinstance(output, str) and output else ""

    parts = [header]
    if body:
        parts.append(body)

    exit_code = item.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        parts.append(f"[exit={exit_code}]")

    return "\n".join(parts)


def _truncate_command_output(output: str) -> str:
    lines = output.splitlines()
    total = len(lines)
    if total <= _CMD_OUTPUT_HEAD_LINES + _CMD_OUTPUT_TAIL_LINES:
        return "\n".join(lines)
    omitted = total - _CMD_OUTPUT_HEAD_LINES - _CMD_OUTPUT_TAIL_LINES
    head = lines[:_CMD_OUTPUT_HEAD_LINES]
    tail = lines[-_CMD_OUTPUT_TAIL_LINES:]
    return "\n".join([*head, f"… ({omitted} more lines)", *tail])


def _render_codex_file_change(item: dict[str, Any]) -> str | None:
    changes = item.get("changes")
    if not isinstance(changes, list) or not changes:
        return None
    rendered: list[str] = []
    for ch in changes:
        if not isinstance(ch, dict):
            continue
        kind = ch.get("kind", "?")
        path = ch.get("path")
        if not isinstance(path, str) or not path:
            continue
        rendered.append(f"[edit] {kind} {path}")
    return "\n".join(rendered) if rendered else None


def _render_codex_web_search(item: dict[str, Any]) -> str | None:
    query = item.get("query")
    if not isinstance(query, str) or not query:
        action = item.get("action")
        if isinstance(action, dict):
            query = action.get("query")
    if not isinstance(query, str) or not query:
        return None
    return f"[search] {_truncate(query, _TOOL_SUMMARY_LEN)}"
```

### 3. verdict parser への影響評価

`verdict.py` の 3 段パーサに対し、追加される `[exec] ...` / `[edit] ...` /
`[search] ...` 行（および `aggregated_output` 由来の任意テキスト）が誤マッチを引き起こす
リスクを評価する。

| パース段 | パターン | 誤マッチリスク評価 |
|---|---|---|
| Step 1 (Strict) | `---VERDICT---\n...\n---END_VERDICT---` | **なし**。`---VERDICT---` 5 連続デリミタは codex の任意出力にはほぼ出ない。仮に `aggregated_output` に `---VERDICT---` 文字列が含まれても、それは「コマンドが verdict を出力した」結果であり parser 側の責務は変わらない |
| Step 2a (Relaxed delim) | `---\s*VERDICT\s*---` | 同上 |
| Step 2b (Key-Value) | `status:\s*(PASS\|FAIL\|...)` 等を `valid_statuses` 完全マッチで生成 (`verdict.py:122-140`) | **理論上ありうる**: 例えば `aggregated_output` に `status: PASS` のようなテキストが含まれた場合。ただし Step 2b は Step 1 / 2a が両方失敗した場合のみ到達するため、正常系の verdict 出力では到達しない |

#### 採用する防御策

- **追加の防御コードは入れない**（YAGNI）。理由:
  1. Codex の正常な verdict 出力（skill 末尾の `---VERDICT---\n...\n---END_VERDICT---`）は
     `agent_message` の `text` に含まれるため Step 1 で確定する
  2. Step 2b 到達は verdict フォーマットが崩れた異常系のみ
  3. ClaudeAdapter (`[tool] ...`) も同じ性質の risk を持ちつつ防御策を入れていない
     （local-pc5090-14 の判断と整合）
  4. `valid_statuses` 完全マッチは `_build_relaxed_status_patterns` (`verdict.py:122-140`)
     で実装済みで、`status: 200` 等の数値混入はそもそも誤マッチしない
- **将来観測されたら**: stream_and_log 側で `[exec]` / `[edit]` / `[search]` プレフィックス
  行を `full_output` から除外する選択肢はあるが、その時点で別 Issue として扱う
  （現時点では console と full_output の content 一致を優先）

### 4. ClaudeAdapter との出力フォーマット対称性

Issue 本文の完了条件に「ClaudeAdapter (`[tool] ...`) との出力フォーマット対称性の評価」が
要求されている。判断:

- **採用するフォーマット**: Codex 固有プレフィックス（`[exec]` / `[edit]` / `[search]`）
- **理由**:
  1. `[tool]` は「Claude の tool_use ブロック」という抽象 1 段上の概念に対する prefix。
     `tool_use` 内の `name` (Bash / Read / Edit / …) で具体的なツールが識別される
  2. Codex の `command_execution` / `file_change` / `web_search` はそれぞれ独立した
     `item.type` であり、共通の `[tool]` prefix で括ると **どの type からのレンダリングか
     視覚的に判別できない**。ターミナルをスクロール中の grep にも不利
  3. ClaudeAdapter の `[tool] Bash $ cmd` は Codex の `[exec] $ cmd` と意味的に等価。
     prefix が違っても役割は対応している
- **トレードオフ**: log を grep する際に `\[tool\]\|\[exec\]` のように両方を書く必要が
  ある。これは Codex/Claude を併用する運用では受容可能なコスト

### 5. mcp_tool_call の扱い（既存挙動の保持と微整理）

- 現状 `mcp_tool_call` の抽出ロジックは `extract_text` 本体にインラインで書かれている
  (`adapters.py:135-143`)
- 本 Issue では `_extract_mcp_tool_call_text(item)` という名前の helper に切り出すことで
  上記疑似コードの `extract_text` 本体を平坦化する（refactor 範囲は最小限）
- **挙動は不変**: `result.content[*].text` を `"\n"` で連結し、空なら `None`
- `mcp_tool_call` の `item.started` は出力しない（Issue 合意通り、`command_execution`
  のみ started を出力）

## テスト戦略

> **変更タイプ**: 実行時コード変更（adapter のテキスト抽出ロジックの振る舞い変更）。
> `docs-only` / `metadata-only` / `packaging-only` ではないため、Small / Medium で恒久
> 回帰テストを定義する。Large は不要（後述）。

### Small テスト（`tests/test_adapters.py::TestCodexAdapter`）

すべて `@pytest.mark.small`。`CodexAdapter` 単体に対する純粋ロジックテスト。

#### 既存テストの扱い

- `test_extract_text_from_agent_message` / `test_extract_text_from_reasoning_event` /
  `test_extract_text_returns_none_for_non_matching`: **不変**（既存挙動を回帰として保護）
- `test_extract_session_id_*` / `test_extract_cost_*` / terminal 系: **不変**

#### 新規追加

`command_execution`:
- `test_extract_text_from_command_execution_started`:
  `item.started` + `command="echo hi"` → `"[exec] $ echo hi"`
- `test_extract_text_from_command_execution_completed_zero_exit`:
  `item.completed` + 3 行 output + `exit_code=0` → `"[exec] $ <cmd>\n<3行>"`
  （末尾 `[exit=0]` 行が **付かない** ことを assert）
- `test_extract_text_from_command_execution_completed_nonzero_exit`:
  `item.completed` + 短い output + `exit_code=5` → 末尾に `[exit=5]` 行
- `test_extract_text_command_output_within_threshold`:
  ちょうど 15 行 → そのまま全行表示。`… (N more lines)` プレフィックスが **出ない**
- `test_extract_text_command_output_above_threshold`:
  20 行 → 先頭 10 行 + `… (5 more lines)` + 末尾 5 行
- `test_extract_text_command_output_boundary_16_lines`:
  16 行（閾値 +1）→ head/tail 省略開始の境界（先頭 10 + `… (1 more lines)` + 末尾 5）
- `test_extract_text_command_execution_truncates_long_command`:
  100 文字以上の command → 先頭 80 文字 + `…`
- `test_extract_text_command_execution_replaces_newlines_in_command`:
  `command="line1\nline2"` → `"[exec] $ line1 line2"`
- `test_extract_text_command_execution_started_with_empty_command`:
  `command=""` → `None`
- `test_extract_text_command_execution_completed_with_empty_output`:
  `aggregated_output=""` → コマンド行のみ（本体なし、`exit_code=0` で `[exit=...]` なし）
- `test_extract_text_command_execution_completed_with_missing_exit_code`:
  `exit_code` キーなし → `[exit=...]` 行は付かない

`file_change`:
- `test_extract_text_from_file_change_single`:
  `changes=[{path:"a.py", kind:"add"}]` → `"[edit] add a.py"`
- `test_extract_text_from_file_change_multiple`:
  `changes=[{...add}, {...update}, {...delete}]` → 3 行改行連結
- `test_extract_text_from_file_change_empty`:
  `changes=[]` → `None`
- `test_extract_text_from_file_change_started_returns_none`:
  `item.started` + `file_change` → `None`（completed のみ出力する設計判断の固定）
- `test_extract_text_from_file_change_missing_path_skipped`:
  `changes=[{kind:"add"}]`（path 欠落）→ 該当エントリ skip、全体が空なら `None`

`web_search`:
- `test_extract_text_from_web_search_completed`:
  `query="OpenAI API"` → `"[search] OpenAI API"`
- `test_extract_text_from_web_search_fallback_to_action_query`:
  `query=""` + `action.query="foo"` → `"[search] foo"`
- `test_extract_text_from_web_search_empty_query`:
  `query=""` + `action` なし → `None`
- `test_extract_text_from_web_search_started_returns_none`:
  `item.started` + `web_search` → `None`
- `test_extract_text_from_web_search_truncates_long_query`:
  100 文字 query → 80 文字 + `…`

`mcp_tool_call`（refactor 後の回帰）:
- `test_extract_text_from_mcp_tool_call_text_content`:
  既存挙動が helper 切り出し後も同一であることを assert（複数 text content の `"\n"`
  連結）
- `test_extract_text_from_mcp_tool_call_with_null_result`:
  `result: null` → `None`（既存挙動）

`item.started` 全般:
- `test_extract_text_item_started_unknown_type_returns_none`:
  `item.started` + 未知 type → `None`
- `test_extract_text_item_started_mcp_tool_call_returns_none`:
  `item.started` + `mcp_tool_call` → `None`（completed のみ出力する設計判断の固定）

### Medium テスト（`tests/test_cli_streaming_integration.py`）

すべて `@pytest.mark.medium`。Mock CLI script + `stream_and_log` を通した結合テスト。
[`issue-20-codex-jsonl-observation.jsonl`](./issue-20-codex-jsonl-observation.jsonl)
の Sample 1 を最小化した fixture を Mock CLI に流す。

- `test_codex_streaming_renders_command_execution`:
  - mock codex CLI から `command_execution` の started + completed (exit=0) を含む JSONL を流す
  - `result.full_output` に `[exec] $ ...` 行が含まれる
  - `console.log` にも同じ行が書き込まれる
- `test_codex_streaming_renders_file_change`:
  - mock codex CLI から `file_change` (completed) を含む JSONL を流す
  - `result.full_output` に `[edit] add /tmp/codex-probe/demo.py` 行が含まれる
- `test_codex_streaming_renders_web_search`:
  - mock codex CLI から `web_search` (completed) を含む JSONL を流す
  - `result.full_output` に `[search] OpenAI API` 行が含まれる
- `test_codex_streaming_mixed_events_preserves_order`:
  - `agent_message` → `command_execution.started` → `command_execution.completed` →
    `file_change` → `web_search` → `agent_message` の順で fixture を流し、
    `console.log` 内の行順が JSONL の順序と一致する
- `test_codex_streaming_does_not_duplicate_raw_jsonl`:
  - `stdout.log` の行数 = 入力 JSONL の行数（既存挙動の回帰保護）

### Large テスト

**不要**。理由（`docs/dev/testing-convention.md` の 4 条件マッピング）:

1. **独自ロジックの追加・変更をほぼ含まない**: 該当しない（本変更は新規ロジック追加）
2. **想定される不具合パターンが既存ゲートで捕捉済み**: 該当しない
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: **該当する**。Codex CLI 固有の
   JSONL イベント構造に対するパーサ追加であり、JSONL fixture（observation.jsonl の最小化）
   で完全に再現可能。Large で実 codex CLI を起動しても、追加で得られる回帰シグナルは
   「codex CLI 0.124.0 → 将来バージョンの破壊的変更検知」だが、これは本 Issue の対象外
   （Codex CLI バージョン追従は別運用課題）
4. **テスト未追加の理由をレビュー可能な形で説明できる**: 本セクションで明記

代わりに **手動検証**（変更固有・恒久化しない）を実施:
- `agent: codex` を含む既存 workflow step を再実行し、ターミナル / `console.log` に
  `[exec] $ ...` / `[edit] ...` / `[search] ...` が逐次表示されることを目視
- `stdout.log` の行数・内容が回帰していないことを確認
- `extract_cost` が `turn.completed` から従来通り usage を取得していることを確認

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定は不変（既存 adapter pattern 内の挙動修正） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ非変更 |
| `docs/dev/` | なし | ワークフロー手順非変更 |
| `docs/reference/` | なし | adapter は internal API（公開 API 仕様変更なし） |
| `docs/cli-guides/codex-cli-session-guide.md` | **要確認** | item.type 一覧表 (§3.1) に `command_execution` / `file_change` / `web_search` の kaji 側レンダリング規約が増えるが、これは「Codex CLI 仕様」ではなく「kaji adapter の挙動」であり、本 ドキュメントの責務範囲外。**更新不要** と判断 |
| `docs/cli-guides/claude-code-cli-guide.md` | なし | Claude 関連は不変 |
| `CLAUDE.md` | なし | 規約非変更 |
| `docs/operations/` | なし | 運用手順非変更 |

> **補足**: 本変更は internal adapter 改修であり、ユーザー観測上は「ターミナル出力が増える」
> だけ。コマンド体系・設定・ファイル配置に変更がないため、ドキュメント更新は不要。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 観測ログ（生 JSONL） | [`./issue-20-codex-jsonl-observation.jsonl`](./issue-20-codex-jsonl-observation.jsonl) | `codex-cli 0.124.0` を `/tmp/codex-probe` で実行した観測。`command_execution` が `item.started` + `item.completed` の 2 イベントを emit すること、`file_change` / `web_search` も同様に 2 イベント emit すること、`exit_code` が非ゼロのとき `status: "failed"` になることを直接確認 |
| `CodexAdapter` 現状 | `kaji_harness/adapters.py:120-160` | `extract_text` が `item.completed` の `agent_message` / `reasoning` / `mcp_tool_call` のみ抽出し、`command_execution` / `file_change` / `web_search` / `item.started` を捨てている現状。改修対象 |
| `ClaudeAdapter` 対比（先行例） | `kaji_harness/adapters.py:58-99` | `_render_claude_block` で `tool_use` を `[tool] {Name} {summary}` にレンダリング。`_truncate` / `_tool_summary` / `_TOOL_SUMMARY_LEN=80` を helper として持つ。本変更は Codex に対し同じ責務分割を適用 |
| `stream_and_log` 呼び出し側 | `kaji_harness/cli.py:170-218` | `extract_text` の戻り値 `text` が真値なら `texts.append(text)` / `f_con.write` / `print` に流れる。adapter 戻り値の使い方は変更不要 |
| `verdict.parse_verdict` | `kaji_harness/verdict.py:199-299` | 3 段パーサ。Step 1 (`---VERDICT---`) → Step 2a (relaxed delim) → Step 2b (key-value)。Step 2b の status pattern が `valid_statuses` 完全マッチに限定（`verdict.py:122-140`）なので、`[exec] / [edit] / [search]` 行が誤マッチする確率は低いと評価 |
| ClaudeAdapter 先行 Issue / 設計書 | [`./issue-local-pc5090-14-claude-adapter-tool-progress-visibility.md`](./issue-local-pc5090-14-claude-adapter-tool-progress-visibility.md) | local-pc5090-14 の設計書。`[tool] {Name} {summary}` レンダリング規約・80 文字 truncate・verdict parser への影響評価の前例。本設計は Codex 版として対称的な構造を取る |
| Codex CLI セッションガイド（社内一次資料） | [`../../docs/cli-guides/codex-cli-session-guide.md`](../../docs/cli-guides/codex-cli-session-guide.md) | §3.1「item.type の種類」の表で `command_execution` / `file_change` / `web_search` / `mcp_tool_call` / `reasoning` / `agent_message` / `plan_update` の 7 type を列挙。`command_execution` のみ「started → completed」、他は「completed のみ」表記だが、本観測では `file_change` / `web_search` も started を emit することを確認（ガイドの記述が古い可能性）。本設計は実観測を優先 |
| Codex CLI 公式リファレンス | https://developers.openai.com/codex/cli/reference/ | `codex exec --json` の JSONL 出力仕様（公開ドキュメント）。ガイド §16 でも参照されている |

## 完了条件の段階確認（設計段階）

Issue 本文 `## 完了条件 / ### 設計段階で確認` の各項目に対する設計書での対応:

- [x] `CodexAdapter.extract_text` が extract する `item.type` の決定（最低 6 type）と
  `event.type` の取り扱い（`item.started` / `item.completed` の区別）が文書化されている
  → 「インターフェース §出力（変更後の抽出ルール）」表に全 6 type + `item.started` 振り分けを記載
- [x] `command_execution` のレンダリング（`item.started` での `[exec] $ cmd` 表示、
  `item.completed` での output 省略ルール「先頭 10 行 + 末尾 5 行 + `… (N more lines)`」、
  `exit_code≠0` の `[exit=N]` 付与条件）が文書化されている
  → 「インターフェース §レンダリング詳細 §command_execution」+ 「方針 §2 helper」+
  境界テストで仕様確定
- [x] `file_change` のレンダリング（複数 path の改行連結、`kind` の表示）が文書化されている
  → 「インターフェース §レンダリング詳細 §file_change」で `[edit] <kind> <path>` 1 entry 1 行
  改行連結と確定
- [x] `web_search` のレンダリング（`query` の整形と truncate 長）が文書化されている
  → 「インターフェース §レンダリング詳細 §web_search」で 80 文字 truncate + `item.query`
  優先 + `action.query` フォールバックと確定
- [x] `stdout.log`（raw）には変更を加えない方針が明記されている
  → 「制約・前提条件」「変更スコープ §変更しないファイル / 機能」で明記
- [x] `kaji_harness/verdict.py` への影響評価が記載されている
  → 「方針 §3 verdict parser への影響評価」で Step 1 / 2a / 2b それぞれの誤マッチリスクを
  評価し、追加防御コードは入れない判断と根拠を記載
- [x] ClaudeAdapter (`[tool] ...`) との出力フォーマット対称性の評価
  → 「方針 §4」で Codex 固有 prefix (`[exec]` / `[edit]` / `[search]`) を採用する根拠を
  記載（`item.type` ごとに視覚的判別を効かせるため）

実装段階で確認する条件（Small/Medium テスト追加・`make check` 通過・手動目視）は
「テスト戦略」セクションで具体テスト名と期待挙動を確定済み。
