# [設計] RunLogger: v5からJSONL実行ログ機能を移植

Issue: #29

## 概要

v5のログ機能をdaoに移植し、ワークフロー実行のトレーサビリティ・デバッガビリティを確保する。

## 背景・目的

**問題**: ワークフロー実行中に何が起きているかわからず、開発・デバッグが困難

**解決策**: v5で設計された3層のログ機能を移植
1. **RunLogger**: オーケストレーターレベルのJSONL実行ログ
2. **cli_console.log**: CLIツール出力の整形ログ
3. **format_jsonl_line拡張**: gemini/codex対応

## 現状分析

| コンポーネント | v5 | dao (bugfix_agent) | dao (core/tools) |
|---------------|-----|-------------------|------------------|
| RunLogger | ✅ | ❌ | ❌ |
| stdout.log / stderr.log | ✅ | ✅ | ✅ |
| cli_console.log | ✅ | ✅ | ❌ |
| format_jsonl_line (claude) | ✅ | ✅ | ✅ |
| format_jsonl_line (gemini) | ✅ | ✅ | ❌ |
| format_jsonl_line (codex) | ✅ | ✅ | ❌ |
| リアルタイムflush | ✅ | ✅ | ❌ |

**発見**: `src/bugfix_agent/cli.py` には既にv5の機能がほぼ移植済み。
`src/core/tools/_cli.py` が簡略版で機能が足りない。

## 方針

### 1. RunLogger を `src/core/run_logger.py` に新規作成

v5の `RunLogger` をそのまま移植。

```python
class RunLogger:
    def __init__(self, log_path: Path): ...
    def log_run_start(self, issue_url: str, run_id: str) -> None: ...
    def log_state_enter(self, state: str, session_id: str | None = None) -> None: ...
    def log_state_exit(self, state: str, result: str, next_state: str) -> None: ...
    def log_run_end(self, status: str, loop_counters: dict[str, int], error: str | None = None) -> None: ...
```

#### 出力パス規約

```
artifacts/<workflow>/<timestamp>/
├── run.log                    # RunLogger (オーケストレーター)
├── init/
│   ├── stdout.log             # _cli.py (run_cli_streaming)
│   ├── stderr.log             # _cli.py (run_cli_streaming)
│   └── cli_console.log        # _cli.py (run_cli_streaming, tool_name指定時)
├── investigate/
│   └── ...
└── fix/
    └── ...
```

#### 呼び出し元

| コンポーネント | 呼び出し元 | タイミング |
|---------------|-----------|-----------|
| RunLogger | `src/workflows/*/runner.py` | ワークフロー開始時にインスタンス化 |
| cli_console.log | `src/core/tools/_cli.py` | `run_cli_streaming(tool_name=...)` 呼び出し時 |
| stdout/stderr.log | `src/core/tools/_cli.py` | `run_cli_streaming(log_dir=...)` 呼び出し時 |

**責務分離**:
- **runner.py**: artifacts ディレクトリ作成、RunLogger インスタンス化、状態遷移ログ
- **_cli.py**: 各状態内での CLI 実行ログ（stdout/stderr/cli_console）

### 2. `src/core/tools/_cli.py` を `src/bugfix_agent/cli.py` 相当に拡張

現在の `_cli.py` に不足している機能:
- **cli_console.log 保存**: tool_name 指定時に整形ログを保存
- **リアルタイム flush**: tail -f で監視可能
- **gemini/codex 対応**: format_jsonl_line の拡張

### 3. 重複コードの整理

`src/bugfix_agent/cli.py` と `src/core/tools/_cli.py` の重複を解消:
- `src/core/tools/_cli.py` を正とし、フル機能を実装
- `src/bugfix_agent/cli.py` は `src/core/tools/_cli.py` を再エクスポート

## インターフェース

### RunLogger

**入力**:
- `log_path: Path` - ログファイルパス

**出力**:
- `run.log` (JSONL形式)

**イベント形式**:
```json
{"ts": "2025-01-29T12:00:00+00:00", "event": "run_start", "issue_url": "...", "run_id": "..."}
{"ts": "2025-01-29T12:00:01+00:00", "event": "state_enter", "state": "init"}
{"ts": "2025-01-29T12:00:02+00:00", "event": "state_exit", "state": "init", "result": "success", "next": "investigate"}
{"ts": "2025-01-29T12:00:10+00:00", "event": "run_end", "status": "COMPLETE", "loop_counters": {...}}
```

### run_cli_streaming 拡張

**追加される出力**:
- `cli_console.log`: tool_name 指定時に作成

**リアルタイム監視**:
```bash
tail -f artifacts/<workflow>/<timestamp>/init/cli_console.log
```

### format_jsonl_line 拡張

**追加対応**:
- `tool_name="gemini"`: `{"type":"response","response":{"content":[...]}}`
- `tool_name="codex"`: `{"type":"item.completed","item":{...}}`

#### フォーマット仕様

各ツールのJSONL形式と抽出ルール:

| ツール | 必須フィールド | 抽出対象 | 出力形式 |
|-------|---------------|---------|---------|
| claude | `type`, `result` or `message.content` | `type=result`: result値 / `type=assistant`: content[].text | テキスト結合 |
| gemini | `type=response`, `response.content` | content[type=text].text | テキスト結合 |
| codex | `type=item.completed`, `item.type` | reasoning/agent_message: item.text / command_execution: command + aggregated_output | 整形出力 |

**Codex command_execution 整形ルール**:
- コマンド表示: `$ <command>`（bash wrapper は除去）
- 出力表示: `> <line>` 形式、最大3行、超過分は `... (N more lines)` 表示
- 非ゼロ終了コード: `[exit: N]` を末尾に付加

#### エラー時挙動

| 入力 | 挙動 | 戻り値 |
|------|------|--------|
| 不正なJSON | 文字列をそのまま返す（空白のみの場合は `None`） | `str \| None` |
| 未知の `tool_name` | 抽出せずスキップ | `None` |
| 必須フィールド欠落 | 抽出せずスキップ | `None` |
| 空の抽出結果 | 出力しない | `None` |

**設計根拠**: ログ整形は補助機能であり、パース失敗で例外を投げるとワークフロー全体が停止するリスクがある。そのため、失敗時は静かに `None` を返し、呼び出し元で無視する方針とする。

## 制約・前提条件

- v5の機能を保持（リファクタリングOK、機能削除NG）
- 既存の `src/bugfix_agent` のテストが壊れないこと
- `src/core/tools/_cli.py` を使用している箇所への影響を最小化

## 検証観点

### RunLogger
- ディレクトリが存在しない場合に自動作成される
- 各イベントが正しいJSONL形式で出力される
- エラーイベントにerrorフィールドが含まれる

### cli_console.log
- tool_name 指定時のみ作成される
- tool_name 未指定時は作成されない
- リアルタイムでflushされる（tail -f で確認可能）

### format_jsonl_line
- Claude形式が正しくパースされる
- Gemini形式が正しくパースされる
- Codex形式が正しくパースされる（command_execution含む）
- 不正なJSONでもエラーにならない

## 参考

- v5 RunLogger: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/run_logger.py`
- v5 cli.py: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/cli.py`
- v5 テスト: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/test_bugfix_agent_orchestrator.py`
