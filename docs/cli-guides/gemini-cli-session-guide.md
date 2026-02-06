# Gemini CLI セッション管理ガイド

## 概要

Gemini CLI のセッション管理機能に関する調査結果をまとめた資料。
非インタラクティブモードでのセッション引き継ぎやJSON出力の詳細を記載。

**調査日**: 2025-11-27
**対象バージョン**: Gemini CLI v0.18.0
**公式ドキュメント**: https://github.com/anthropics/anthropic-cookbook（参考）

---

## 1. 基本コマンド構造

### 1.1 インタラクティブモード（デフォルト）

```bash
gemini [OPTIONS]
```

### 1.2 非インタラクティブモード

```bash
gemini "プロンプト"                    # 位置引数（推奨）
gemini -p "プロンプト"                 # -p オプション（非推奨）
echo "プロンプト" | gemini             # stdin
```

**注意**: `-p` / `--prompt` は非推奨。位置引数の使用が推奨されている。

---

## 2. 利用可能なパラメータ

### 2.1 主要オプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--model` | `-m` | 使用モデル | `-m gemini-2.5-pro` |
| `--output-format` | `-o` | 出力形式 | `-o json` |
| `--resume` | `-r` | セッション再開 | `-r latest` / `-r 3` |
| `--prompt` | `-p` | プロンプト（非推奨） | `-p "質問"` |
| `--prompt-interactive` | `-i` | プロンプト実行後インタラクティブ継続 | `-i "質問"` |
| `--sandbox` | `-s` | サンドボックスモード | `-s` |
| `--yolo` | `-y` | 全アクション自動承認 | `-y` |
| `--debug` | `-d` | デバッグモード | `-d` |

### 2.2 セッション管理オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--resume` | セッション再開 | `-r latest` / `-r 3` / `-r <uuid>` |
| `--list-sessions` | セッション一覧表示 | `--list-sessions` |
| `--delete-session` | セッション削除 | `--delete-session 3` |

### 2.3 出力形式オプション

| オプション | 説明 |
|-----------|------|
| `-o text` | テキスト形式（デフォルト） |
| `-o json` | JSON形式（単一結果） |
| `-o stream-json` | ストリーミングJSON（JSONL） |

### 2.4 承認モードオプション

| オプション | 説明 |
|-----------|------|
| `--approval-mode default` | 承認を求める（デフォルト） |
| `--approval-mode auto_edit` | 編集ツール自動承認 |
| `--approval-mode yolo` | 全ツール自動承認 |
| `-y` / `--yolo` | 全アクション自動承認（上記と同等） |

### 2.5 ツール・拡張機能オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--allowed-tools` | 確認なしで実行可能なツール | `--allowed-tools run_shell_command` |
| `--allowed-mcp-server-names` | 許可するMCPサーバー | `--allowed-mcp-server-names server1` |
| `--extensions` | 使用する拡張機能 | `-e ext1 ext2` |
| `--list-extensions` | 拡張機能一覧表示 | `-l` |
| `--include-directories` | 追加ワークスペースディレクトリ | `--include-directories /path1,/path2` |

---

## 2.6 非インタラクティブモードでのツール制限（重要）

**調査日**: 2025-11-30

### 2.6.1 問題の背景

非インタラクティブモード（`gemini "プロンプト"` や `gemini -o stream-json "プロンプト"`）では、**セキュリティ上の理由でツールの利用が制限される**。

インタラクティブモードで利用可能な以下のツールが、デフォルトでは無効化されている：

| ツール名 | 機能 | 非インタラクティブ時のデフォルト |
|---------|------|-------------------------------|
| `run_shell_command` | シェルコマンド実行（`gh`, `git`, `npm`, `uv` 等） | ❌ 無効 |
| `write_file` | ファイル書き込み | ❌ 無効 |
| `read_file` | ファイル読み込み | ✅ 有効 |
| `list_directory` | ディレクトリ一覧 | ✅ 有効 |

### 2.6.2 エラー例

```bash
# ❌ 失敗する例
gemini -o stream-json "gh issue view 182 --json title を実行して"
```

```json
{
  "type": "tool_result",
  "status": "error",
  "error": {
    "type": "tool_not_registered",
    "message": "Tool \"run_shell_command\" not found in registry."
  }
}
```

### 2.6.3 解決策: `--allowed-tools` フラグ

`--allowed-tools` フラグでホワイトリスト方式により特定のツールを有効化できる：

```bash
# ✅ 成功する例
gemini --allowed-tools run_shell_command -o stream-json "gh issue view 182 --json title を実行して"
```

```json
{
  "type": "tool_use",
  "tool_name": "run_shell_command",
  "parameters": {"command": "gh issue view 182 --json title"}
}
{
  "type": "tool_result",
  "status": "success",
  "output": "{\"title\": \"[Dryrun] Listed Info: sector17_name/sector33_nameフィールドの未設定問題\"}"
}
```

### 2.6.4 `run_shell_command` で実行可能なコマンド

**検証日**: 2025-11-30

| カテゴリ | コマンド例 | 検証結果 |
|---------|-----------|---------|
| **GitHub CLI** | `gh issue view`, `gh pr create`, `gh api` | ✅ 成功 |
| **Git** | `git status`, `git log`, `git diff`, `git add`, `git commit` | ✅ 成功 |
| **Python開発** | `pytest`, `python3`, `pip install`, `uv` | ✅ 成功 |
| **Node.js開発** | `npm install`, `npm test`, `npm run build` | ✅ 成功 |
| **Docker** | `docker compose up -d`, `docker ps` | ✅ 成功 |
| **ファイル操作** | `ls`, `find`, `cat`, `mkdir`, `rm`, `touch`, `mv`, `cp` | ✅ 成功 |
| **システム情報** | `env`, `pwd`, `whoami`, `ps aux` | ✅ 成功 |

#### 検証ログ（`uv --version`）

```bash
gemini --allowed-tools run_shell_command "実行してください: uv --version"
```

```json
{"type":"tool_use","tool_name":"run_shell_command","parameters":{"command":"uv --version"}}
{"type":"tool_result","status":"success","output":"uv 0.8.14"}
```

#### 検証ログ（`gh issue view`）

```bash
gemini --allowed-tools run_shell_command -o stream-json "gh issue view 182 --json title"
```

```json
{"type":"tool_use","tool_name":"run_shell_command","parameters":{"command":"gh issue view 182 --json title"}}
{"type":"tool_result","status":"success","output":"{\"title\": \"[Dryrun] Listed Info: sector17_name/sector33_nameフィールドの未設定問題\"}"}
```

### 2.6.5 制約事項

`run_shell_command` には以下の制約がある：

| 制約 | 説明 | 対処法 |
|-----|------|--------|
| **非対話型のみ** | `vim`, `nano`, 対話型シェル（`python` REPL）は実行不可 | スクリプト実行やフラグ（`-y`等）で対応 |
| **プロンプト回避** | ユーザー入力待ちのコマンドは失敗 | `-y`, `--yes`, `--force` 等のフラグ使用 |
| **背景プロセス** | 長時間実行は `&` で背景実行 | `npm start &` 等 |

### 2.6.6 セキュリティ考慮事項

| 方式 | フラグ | セキュリティ | 推奨度 |
|-----|--------|------------|--------|
| **ホワイトリスト** | `--allowed-tools run_shell_command` | 高（必要なツールのみ許可） | ⭐⭐⭐ 推奨 |
| **YOLO** | `--yolo` / `-y` | 低（全ツール自動承認） | ⚠️ 注意 |
| **デフォルト** | なし | 最高（ツール制限） | ✅ 安全 |

**注意**: `--yolo` は開発・テスト時のみ使用。本番ワークフローでは `--allowed-tools` によるホワイトリスト方式を推奨。

### 2.6.7 bugfix-agent での実装

`bugfix_agent_orchestrator.py` の `GeminiTool.run()` では以下のように実装：

```python
# CLI 引数を構築
args = ["gemini", "-o", "stream-json"]
if self.model != "auto":
    args += ["-m", self.model]
if session_id:
    args += ["-r", session_id]
# Enable run_shell_command tool for gh/shell operations in non-interactive mode
# Note: Gemini CLI restricts tools by default in non-interactive mode for security.
# Using --allowed-tools whitelist is the recommended approach.
args += ["--allowed-tools", "run_shell_command"]
args.append(full_prompt)
```

この実装により、INVESTIGATE / IMPLEMENT / PR_CREATE ステートで `gh` コマンドが正常に動作する。

---

## 3. セッション管理

### 3.1 セッション継続オプション

| 指定方法 | 説明 | 例 |
|---------|------|-----|
| `latest` | 最新のセッションを再開 | `-r latest` |
| 番号 | 番号でセッション指定 | `-r 3` |
| UUID | UUIDでセッション指定 | `-r 9d4614fb-e818-45fc-afab-77924f34a5a5` |

### 3.2 セッション一覧の確認

```bash
gemini --list-sessions
```

出力例：
```
Available sessions for this project (3):

  1. https://github.com/apokamo/kamo2/issues/184#issuecomment-... (2 days ago) [0de5286d-a4c6-4f03-9643-cf234bd232e8]
  2. 現在のドル円を調べて (23 hours ago) [ff240b43-faa2-4136-ada3-cc90fda7ce44]
  3. 2+2は？ (Just now) [9d4614fb-e818-45fc-afab-77924f34a5a5]
```

### 3.3 セッションIDの取得方法

#### JSON出力から取得（単一結果）

```bash
# JSONにはsession_idが含まれないが、--list-sessionsで確認可能
gemini "タスク開始" -o json
gemini --list-sessions | tail -1  # 最新セッションを確認
```

#### stream-json出力から取得

```bash
SESSION_ID=$(gemini "タスク開始" -o stream-json 2>&1 | \
  grep '"type":"init"' | jq -r '.session_id')
echo $SESSION_ID
# 出力例: 0a1bbf60-f74d-44c1-bd2c-15eeaf376d38
```

### 3.4 セッション引き継ぎの例

```bash
# 新規セッション開始
gemini "1+1は？" -o json

# 最新セッションを継続
gemini -r latest -p "その答えに2を足すと？" -o json

# 番号でセッション指定
gemini -r 3 -p "さらに10を足すと？" -o json

# UUIDでセッション指定
gemini -r 9d4614fb-e818-45fc-afab-77924f34a5a5 -p "続きの計算" -o json
```

**注意**: resume時は `-p` でプロンプトを指定するか、stdinで渡す必要がある。

### 3.5 セッションの削除

```bash
gemini --delete-session 3  # 番号で削除
```

---

## 4. JSON出力の詳細

### 4.1 基本JSON出力（`-o json`）

実行コマンド：
```bash
gemini "2+2は？" -o json
```

出力（整形済み）：
```json
{
  "response": "4です。",
  "stats": {
    "models": {
      "gemini-2.5-flash-lite": {
        "api": {
          "totalRequests": 1,
          "totalErrors": 0,
          "totalLatencyMs": 1650
        },
        "tokens": {
          "prompt": 3154,
          "candidates": 50,
          "total": 3312,
          "cached": 0,
          "thoughts": 108,
          "tool": 0
        }
      },
      "gemini-2.5-flash": {
        "api": {
          "totalRequests": 1,
          "totalErrors": 0,
          "totalLatencyMs": 2864
        },
        "tokens": {
          "prompt": 7584,
          "candidates": 3,
          "total": 7625,
          "cached": 0,
          "thoughts": 38,
          "tool": 0
        }
      }
    },
    "tools": {
      "totalCalls": 0,
      "totalSuccess": 0,
      "totalFail": 0,
      "totalDurationMs": 0,
      "totalDecisions": {
        "accept": 0,
        "reject": 0,
        "modify": 0,
        "auto_accept": 0
      },
      "byName": {}
    },
    "files": {
      "totalLinesAdded": 0,
      "totalLinesRemoved": 0
    }
  }
}
```

### 4.2 JSON出力のフィールド説明

#### トップレベルフィールド

| フィールド | 説明 |
|-----------|------|
| `response` | 最終回答テキスト |
| `stats` | 統計情報 |

#### stats.models フィールド（モデルごと）

| フィールド | 説明 |
|-----------|------|
| `api.totalRequests` | APIリクエスト数 |
| `api.totalErrors` | エラー数 |
| `api.totalLatencyMs` | レイテンシ（ミリ秒） |
| `tokens.prompt` | プロンプトトークン数 |
| `tokens.candidates` | 候補トークン数 |
| `tokens.total` | 総トークン数 |
| `tokens.cached` | キャッシュトークン数 |
| `tokens.thoughts` | 思考トークン数 |
| `tokens.tool` | ツールトークン数 |

#### stats.tools フィールド

| フィールド | 説明 |
|-----------|------|
| `totalCalls` | ツール呼び出し数 |
| `totalSuccess` | 成功数 |
| `totalFail` | 失敗数 |
| `totalDurationMs` | 総実行時間 |
| `totalDecisions` | 承認判定統計 |
| `byName` | ツール別統計 |

#### stats.files フィールド

| フィールド | 説明 |
|-----------|------|
| `totalLinesAdded` | 追加行数 |
| `totalLinesRemoved` | 削除行数 |

### 4.3 ストリーミングJSON出力（`-o stream-json`）

実行コマンド：
```bash
gemini "3+3は？" -o stream-json
```

出力（JSONL形式 - 各行が1イベント）：

**イベント1: 初期化**
```json
{
  "type": "init",
  "timestamp": "2025-11-27T14:32:31.077Z",
  "session_id": "0a1bbf60-f74d-44c1-bd2c-15eeaf376d38",
  "model": "auto"
}
```

**イベント2: ユーザーメッセージ**
```json
{
  "type": "message",
  "timestamp": "2025-11-27T14:32:31.077Z",
  "role": "user",
  "content": "3+3は？"
}
```

**イベント3: アシスタント応答**
```json
{
  "type": "message",
  "timestamp": "2025-11-27T14:32:35.403Z",
  "role": "assistant",
  "content": "3+3は6です。",
  "delta": true
}
```

**イベント4: 結果**
```json
{
  "type": "result",
  "timestamp": "2025-11-27T14:32:35.407Z",
  "status": "success",
  "stats": {
    "total_tokens": 10971,
    "input_tokens": 10738,
    "output_tokens": 57,
    "duration_ms": 4330,
    "tool_calls": 0
  }
}
```

#### stream-json イベントタイプ

| type | 説明 |
|------|------|
| `init` | 初期化（セッションID、モデル等） |
| `message` | メッセージ（user/assistant） |
| `result` | 最終結果（統計情報含む） |

---

## 5. 実践的な使用パターン

### 5.1 単一エージェント（シンプル）

```bash
# セッション開始
gemini "タスク1を開始"

# 最新セッションを継続
gemini -r latest -p "続きの作業"
```

### 5.2 複数エージェントの並列実行

```bash
#!/bin/bash

# エージェント1を開始
gemini "コードレビューを開始" -o json
# --list-sessions で番号を確認

# エージェント2を開始
gemini "テスト作成を開始" -o json

# セッション一覧を確認
gemini --list-sessions
# 出力:
#   1. コードレビューを開始 (Just now) [uuid-1]
#   2. テスト作成を開始 (Just now) [uuid-2]

# 各セッションを番号で継続
gemini -r 1 -p "src/main.py をレビューして"
gemini -r 2 -p "ユニットテストを追加して"
```

### 5.3 stream-jsonからセッションID取得

```bash
# セッションIDを取得
SESSION_ID=$(gemini "タスク開始" -o stream-json 2>&1 | \
  grep '"type":"init"' | jq -r '.session_id')

# UUIDでセッション継続
gemini -r "$SESSION_ID" -p "続きの作業" -o json
```

### 5.4 自動承認モード（YOLO）

```bash
# 全ツール自動承認
gemini -y "ファイルを作成して"

# または
gemini --approval-mode yolo "ファイルを作成して"
```

---

## 6. 利用可能なモデル

| モデル | 説明 |
|--------|------|
| `auto` | 自動選択（デフォルト） |
| `gemini-2.5-flash` | 高速・バランス型 |
| `gemini-2.5-flash-lite` | 軽量・最速 |
| `gemini-2.5-pro` | 高性能 |

**注意**: `auto` モードでは複数モデルが自動的に使い分けられる（JSON出力で確認可能）。

---

## 7. 3CLI比較表

| 機能 | Gemini CLI | Claude Code | Codex CLI |
|------|-----------|-------------|-----------|
| 非インタラクティブ | 位置引数 / `-p` | `-p` | `exec` |
| セッション継続（最新） | `-r latest` | `-c` | `resume --last` |
| セッション継続（ID指定） | `-r <番号/UUID>` | `-r <uuid>` | `resume <uuid>` |
| セッション一覧 | `--list-sessions` | なし | なし |
| セッション削除 | `--delete-session` | なし | なし |
| JSON出力 | `-o json` | `--output-format json` | `--json` |
| ストリーミング | `-o stream-json` | `--output-format stream-json` | JSONL（デフォルト） |
| モデル指定 | `-m` | `--model` | `-m` |
| 自動承認 | `-y` / `--yolo` | `--permission-mode` | `--full-auto` |
| コスト情報 | なし | `total_cost_usd` | なし |

### 主な違い

1. **セッション管理**
   - Gemini: 番号・UUID両方で指定可能、一覧・削除機能あり
   - Claude: UUIDのみ、一覧機能なし
   - Codex: UUIDのみ、一覧機能なし

2. **セッションIDの取得**
   - Gemini: `--list-sessions` または stream-jsonの `init` イベント
   - Claude: JSON出力の `session_id`
   - Codex: JSON出力の `thread_id`

3. **マルチモデル**
   - Gemini: `auto` で複数モデル自動使い分け
   - Claude: 単一モデル（切り替えは手動）
   - Codex: 単一モデル（切り替えは手動）

4. **resume時のプロンプト指定**
   - Gemini: `-p` または stdin 必須
   - Claude: 位置引数または `-r` の後に直接
   - Codex: 位置引数

---

## 8. トラブルシューティング

### 8.1 resume時にエラー

```
When resuming a session, you must provide a message via --prompt (-p) or stdin
```

→ `-p` でプロンプトを指定：
```bash
gemini -r latest -p "続きの質問"
```

### 8.2 認証エラー

```
Loaded cached credentials.
```
このメッセージは正常。エラーの場合は `gemini auth` で再認証。

### 8.3 セッションが見つからない

```bash
gemini --list-sessions  # 現在のセッションを確認
```

セッションはプロジェクトディレクトリごとに管理される。

---

## 9. 参考リンク

- [Gemini CLI GitHub](https://github.com/google/gemini-cli)（推定）
- [Google AI Studio](https://aistudio.google.com/)
- [Gemini API ドキュメント](https://ai.google.dev/docs)

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2025-11-27 | 初版作成 |
| 2025-11-30 | セクション 2.6「非インタラクティブモードでのツール制限」追加。`--allowed-tools` フラグによる `run_shell_command` 有効化手順、`gh`/`uv` 等の検証結果、セキュリティ考慮事項を詳細に記載。bugfix-agent での実装方法を追加。 |
