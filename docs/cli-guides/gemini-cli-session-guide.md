# Gemini CLI セッション管理ガイド

## 概要

Gemini CLI のセッション管理機能に関する調査結果をまとめた資料。
非インタラクティブモードでのセッション引き継ぎやJSON出力の詳細を記載。

**調査日**: 2026-03-09
**--help 取得バージョン**: v0.31.0（ローカル環境、2026-03-09 取得）
**Web検索による最新情報**: v0.32.1（2026-03-04 リリース）
**公式ドキュメント**: https://geminicli.com/docs/
**GitHub**: https://github.com/google-gemini/gemini-cli

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

| オプション | 短縮形 | 型 | デフォルト | 説明 |
|-----------|--------|-----|-----------|------|
| `--model` | `-m` | string | `auto` | 使用モデル |
| `--output-format` | `-o` | string | `text` | 出力形式（`text` / `json` / `stream-json`） |
| `--resume` | `-r` | string | — | セッション再開 |
| `--prompt` | `-p` | string | — | プロンプト（**非推奨**） |
| `--prompt-interactive` | `-i` | string | — | プロンプト実行後インタラクティブ継続 |
| `--sandbox` | `-s` | boolean | `false` | サンドボックスモード |
| `--approval-mode` | — | string | `default` | ツール承認モード |
| `--yolo` | `-y` | boolean | `false` | 全アクション自動承認（**非推奨**: `--approval-mode yolo` を使用） |
| `--debug` | `-d` | boolean | `false` | デバッグモード |
| `--version` | `-v` | — | — | バージョン表示 |
| `--help` | `-h` | — | — | ヘルプ表示 |
| `--screen-reader` | — | boolean | — | アクセシビリティモード |
| `--experimental-acp` | — | boolean | — | ACP（Agent Code Pilot）モード |
| `--raw-output` | — | boolean | `false` | モデル出力のサニタイズ無効化（例: ANSIエスケープシーケンスを許可）。**警告**: 信頼できないモデル出力ではセキュリティリスクあり |
| `--accept-raw-output-risk` | — | boolean | `false` | `--raw-output` 使用時のセキュリティ警告を抑制 |
| `--experimental-zed-integration` | — | boolean | — | Zed エディタ統合モード（Web検索情報、v0.31.0 の --help に未記載） |

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
| `--approval-mode auto_edit` | 編集ツール（`write_file`, `replace`）自動承認、シェルコマンドは要承認 |
| `--approval-mode yolo` | 全ツール自動承認（サンドボックスが自動有効化） |
| `-y` / `--yolo` | 全アクション自動承認（**非推奨**: `--approval-mode yolo` を推奨） |

### 2.5 ツール・拡張機能オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--allowed-tools` | 確認なしで実行可能なツール（**非推奨**: Policy Engine を使用） | `--allowed-tools run_shell_command` |
| `--policy` | ユーザー定義ポリシーファイル（v0.30.0+） | `--policy ./my-policy.toml` |
| `--allowed-mcp-server-names` | 許可するMCPサーバー | `--allowed-mcp-server-names server1` |
| `--extensions` | 使用する拡張機能 | `-e ext1 ext2` |
| `--list-extensions` | 拡張機能一覧表示 | `-l` |
| `--include-directories` | 追加ワークスペースディレクトリ | `--include-directories /path1,/path2` |

---

## 2.6 非インタラクティブモードでのツール制限（重要）

**最終調査日**: 2026-03-09

### 2.6.1 問題の背景

非インタラクティブモード（`gemini "プロンプト"` や `gemini -o stream-json "プロンプト"`）では、**セキュリティ上の理由でツールの利用が制限される**。

インタラクティブモードで利用可能な以下のツールが、デフォルトでは無効化されている：

| ツール名 | 機能 | 非インタラクティブ時のデフォルト |
|---------|------|-------------------------------|
| `run_shell_command` | シェルコマンド実行（`gh`, `git`, `npm`, `uv` 等） | 無効 |
| `write_file` | ファイル書き込み | 無効 |
| `replace` | ファイル内テキスト置換 | 無効 |
| `read_file` | ファイル読み込み | 有効 |
| `list_directory` | ディレクトリ一覧 | 有効 |
| `glob` | ファイル検索 | 有効 |

### 2.6.2 エラー例

```bash
# 失敗する例
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

### 2.6.3 解決策

#### 方法1: `--allowed-tools` フラグ（レガシー、非推奨）

v0.30.0 以降 `--allowed-tools` は非推奨。ただし後方互換性のため引き続き動作する：

```bash
# レガシー方式（動作はするが非推奨）
gemini --allowed-tools run_shell_command -o stream-json "gh issue view 182 --json title を実行して"
```

#### 方法2: Policy Engine（v0.30.0+ 推奨）

ポリシーファイル（TOML）でツール許可を宣言的に管理する：

```bash
# ポリシーディレクトリの作成
mkdir -p ~/.gemini/policies
```

```toml
# ~/.gemini/policies/allow-shell.toml
[[rule]]
toolName = "run_shell_command"
decision = "allow"
priority = 100

[[rule]]
toolName = "write_file"
decision = "allow"
priority = 100
```

```bash
# Policy Engine 方式
gemini -o stream-json "gh issue view 182 --json title を実行して"
```

ポリシーファイルは `~/.gemini/policies/` に配置すれば自動読み込みされる。`--policy` フラグで明示的に指定することも可能。

#### 方法3: `--approval-mode yolo`

```bash
# 全ツール自動承認（サンドボックスが自動有効化される）
gemini --approval-mode yolo -o stream-json "gh issue view 182 --json title を実行して"
```

### 2.6.4 Policy Engine の既知の問題（v0.30.0+）

**重要**: 非インタラクティブモード + `--approval-mode auto_edit` の組み合わせでは、Policy Engine の `allow` ルールが無視される既知の問題がある（[Issue #20469](https://github.com/google-gemini/gemini-cli/issues/20469)）。

原因: CLI の設定レイヤーが `auto_edit` モード + 非インタラクティブ時にハードコードされた除外リストを適用し、`run_shell_command` がツールレジストリに登録されない。Policy Engine のルールよりもこの除外が優先される。

**回避策**: 非インタラクティブモードでは `--approval-mode yolo` を使用するか、レガシーの `--allowed-tools` フラグを併用する。

### 2.6.5 `run_shell_command` の詳細

#### 引数

| 引数 | 必須 | 型 | 説明 |
|-----|------|-----|------|
| `command` | 必須 | string | 実行するシェルコマンド |
| `description` | 任意 | string | ユーザー確認用テキスト |
| `dir_path` | 任意 | string | 実行ディレクトリ（絶対パスまたはワークスペースルート相対） |
| `is_background` | 任意 | boolean | バックグラウンド実行 |

#### 戻り値

```json
{
  "Command": "git status",
  "Directory": "/path/to/project",
  "Stdout": "...",
  "Stderr": "...",
  "Exit Code": 0,
  "Background PIDs": []
}
```

#### 設定によるコマンド制限

```json
// settings.json
{
  "tools": {
    "core": ["run_shell_command(git)", "run_shell_command(npm)"],
    "exclude": ["run_shell_command(rm)"]
  }
}
```

プレフィックスマッチングで制御。ブロックリストはアローリストに優先する。コマンドチェーン（`&&`, `||`, `;`）は各コンポーネントが個別に検証される。

#### 実行可能なコマンド例

| カテゴリ | コマンド例 | 検証結果 |
|---------|-----------|---------|
| **GitHub CLI** | `gh issue view`, `gh pr create`, `gh api` | 成功 |
| **Git** | `git status`, `git log`, `git diff`, `git add`, `git commit` | 成功 |
| **Python開発** | `pytest`, `python3`, `pip install`, `uv` | 成功 |
| **Node.js開発** | `npm install`, `npm test`, `npm run build` | 成功 |
| **Docker** | `docker compose up -d`, `docker ps` | 成功 |
| **ファイル操作** | `ls`, `find`, `cat`, `mkdir`, `rm`, `touch`, `mv`, `cp` | 成功 |

### 2.6.6 制約事項

| 制約 | 説明 | 対処法 |
|-----|------|--------|
| **非対話型のみ** | `vim`, `nano`, 対話型シェル（`python` REPL）は実行不可 | `enableInteractiveShell: true` で対応可（インタラクティブモード時のみ） |
| **プロンプト回避** | ユーザー入力待ちのコマンドは失敗 | `-y`, `--yes`, `--force` 等のフラグ使用 |
| **背景プロセス** | 長時間実行は `is_background: true` で背景実行 | PID が返却される |
| **環境変数** | 実行時に `GEMINI_CLI=1` が自動設定される | スクリプト内で検出可能 |

### 2.6.7 セキュリティ考慮事項

| 方式 | フラグ/設定 | セキュリティ | 推奨度 |
|-----|-----------|------------|--------|
| **Policy Engine** | `~/.gemini/policies/*.toml` | 高（宣言的・細粒度制御） | 推奨（v0.30.0+） |
| **ホワイトリスト** | `--allowed-tools run_shell_command` | 中（必要なツールのみ許可、非推奨） | レガシー |
| **YOLO** | `--approval-mode yolo` | 低（全ツール自動承認、サンドボックス自動有効化） | 注意 |
| **デフォルト** | なし | 最高（ツール制限） | 安全 |

**注意**: `--approval-mode yolo` は開発・テスト時のみ使用。本番ワークフローでは Policy Engine によるポリシー管理を推奨。

### 2.6.8 bugfix-agent での実装

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
# TODO: v0.30.0+ では Policy Engine への移行を検討
args += ["--allowed-tools", "run_shell_command"]
args.append(full_prompt)
```

この実装により、INVESTIGATE / IMPLEMENT / PR_CREATE ステートで `gh` コマンドが正常に動作する。

**移行ガイド**: v0.30.0+ では `--allowed-tools` は非推奨。以下のポリシーファイルで同等の制御が可能：

```toml
# .gemini/policies/bugfix-agent.toml（ワークスペースレベル）
[[rule]]
toolName = "run_shell_command"
decision = "allow"
priority = 100
```

---

## 3. Policy Engine（v0.30.0+）

### 3.1 概要

Policy Engine は `--allowed-tools` に代わる宣言的なツール制御メカニズム。TOML ファイルでルールを定義し、ツール実行の許可・拒否・確認を細粒度で制御する。

### 3.2 ポリシーファイルの配置

| ティア | 配置場所 | 優先度（ベース） |
|-------|---------|---------------|
| Default | CLI 組み込み | 1 |
| Extension | 拡張機能定義 | 2 |
| Workspace | `$WORKSPACE_ROOT/.gemini/policies/*.toml` | 3 |
| User | `~/.gemini/policies/*.toml` | 4 |
| Admin (Linux) | `/etc/gemini-cli/policies/*.toml` | 5 |
| Admin (macOS) | `/Library/Application Support/GeminiCli/policies/*.toml` | 5 |

最終優先度 = `tier_base + (toml_priority / 1000)`

### 3.3 ルール構文

```toml
# 基本ルール
[[rule]]
toolName = "run_shell_command"
decision = "allow"
priority = 100

# コマンドプレフィックスによる制限
[[rule]]
toolName = "run_shell_command"
commandPrefix = "git "
decision = "allow"
priority = 200

# 正規表現による制限
[[rule]]
toolName = "run_shell_command"
commandRegex = "^(git|gh|npm|pytest) "
decision = "allow"
priority = 200

# MCP ツールの制御
[[rule]]
mcpName = "my-server"
toolName = "search"
decision = "allow"
priority = 200

# 複数ツールへの適用
[[rule]]
toolName = ["write_file", "replace"]
decision = "ask_user"
priority = 10

# ワイルドカード
[[rule]]
toolName = "my-server__*"
decision = "allow"
priority = 150
```

### 3.4 決定タイプ

| Decision | 動作 | 非インタラクティブ時 |
|----------|------|-------------------|
| `allow` | 自動実行 | 自動実行 |
| `deny` | ブロック（deny_message をモデルに返却） | ブロック |
| `ask_user` | ユーザーに確認を求める | **拒否として扱われる** |

### 3.5 非インタラクティブモード向け推奨設定

```toml
# ~/.gemini/policies/non-interactive.toml

# シェルコマンドを許可（git, gh に限定）
[[rule]]
toolName = "run_shell_command"
commandRegex = "^(git|gh) "
decision = "allow"
priority = 200

# ファイル書き込みを許可
[[rule]]
toolName = ["write_file", "replace"]
decision = "allow"
priority = 100

# その他のシェルコマンドは拒否
[[rule]]
toolName = "run_shell_command"
decision = "deny"
deny_message = "Only git and gh commands are allowed in non-interactive mode"
priority = 50
```

---

## 4. セッション管理

### 4.1 セッション継続オプション

| 指定方法 | 説明 | 例 |
|---------|------|-----|
| `latest` | 最新のセッションを再開 | `-r latest` |
| 番号 | 番号でセッション指定 | `-r 3` |
| UUID | UUIDでセッション指定 | `-r 9d4614fb-e818-45fc-afab-77924f34a5a5` |

### 4.2 セッション一覧の確認

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

### 4.3 セッションIDの取得方法

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

### 4.4 セッション引き継ぎの例

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

### 4.5 セッションの削除

```bash
gemini --delete-session 3  # 番号で削除
```

---

## 5. JSON出力の詳細

### 5.1 基本JSON出力（`-o json`）

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

### 5.2 JSON出力のフィールド説明

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

### 5.3 ストリーミングJSON出力（`-o stream-json`）

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
| `tool_use` | ツール呼び出し（ツール名、パラメータ） |
| `tool_result` | ツール実行結果（成功/エラー） |
| `result` | 最終結果（統計情報含む） |

---

## 6. 利用可能なモデル

### 6.1 モデル一覧（v0.32.1 時点）

| モデル | 説明 | 備考 |
|--------|------|------|
| `auto` | 自動選択（デフォルト） | タスク複雑度に応じてルーティング |
| `gemini-3.1-pro-preview` | 最新・最高性能（プレビュー） | v0.31.0+ 一部ユーザーに提供 |
| `gemini-3-pro` | 高性能（Gemini 3世代） | v0.22.0+ 無料枠あり |
| `gemini-3-flash` | 高速（Gemini 3世代） | v0.21.0+ |
| `gemini-2.5-pro` | 高性能（Gemini 2.5世代） | Gemini 3 Pro の制限到達時のフォールバック |
| `gemini-2.5-flash` | 高速・バランス型 | シンプルタスクのデフォルト |
| `gemini-2.5-flash-lite` | 軽量・最速 | — |

### 6.2 Auto ルーティング

`auto` モードでは、プロンプトの複雑度に基づいてモデルが自動選択される：

- **シンプルなプロンプト**: Gemini 2.5 Flash / 3 Flash
- **複雑なプロンプト**: Gemini 3 Pro（有効時）/ Gemini 2.5 Pro

JSON 出力の `stats.models` フィールドで実際に使用されたモデルを確認可能。

### 6.3 日次使用制限

- Gemini 3 Pro / 3.1 Pro Preview には日次使用制限あり
- 制限到達時は Gemini 2.5 Pro にフォールバック
- Gemini 2.5 Pro にも独自の日次制限あり
- 容量エラー時は指数バックオフによるリトライオプションあり

### 6.4 非推奨モデル

| モデル | 状態 | 移行先 |
|--------|------|--------|
| `gemini-3-pro-preview` | 2026-03-09 にシャットダウン | `gemini-3.1-pro-preview` |

---

## 7. サンドボックスモード

### 7.1 概要

サンドボックスモードは、シェルコマンドやファイル操作をコンテナ内で隔離実行する機能。ホストシステムへの影響を防止する。

### 7.2 有効化方法

```bash
# コマンドフラグ
gemini -s -p "analyze the code structure"

# 環境変数
export GEMINI_SANDBOX=true
gemini -p "run the test suite"

# settings.json
# { "sandbox": true }
```

環境変数の値: `true` / `docker` / `podman` / `sandbox-exec` / `runsc` / `lxc`

### 7.3 サンドボックス方式

| 方式 | プラットフォーム | 隔離レベル | 説明 |
|-----|---------------|-----------|------|
| **macOS Seatbelt** | macOS | 中 | `sandbox-exec` によるプロファイルベース制御 |
| **Docker/Podman** | クロスプラットフォーム | 高 | コンテナベースの隔離 |
| **gVisor/runsc** | Linux | 最高 | ユーザースペースカーネルによる完全隔離 |
| **LXC/LXD** | Linux（実験的） | 高 | フルシステムコンテナ |

### 7.4 カスタム Dockerfile

プロジェクト固有のサンドボックスが必要な場合：

```dockerfile
# .gemini/sandbox.Dockerfile
FROM gemini-cli-sandbox:latest
RUN apt-get update && apt-get install -y python3 python3-pip
```

### 7.5 注意事項

- `--approval-mode yolo` 使用時はサンドボックスが自動有効化される
- `SANDBOX_FLAGS` 環境変数で Docker/Podman にカスタムフラグを注入可能
- `.gemini/.env` でCLI固有の環境変数を設定（プロジェクトルートの `.env` は読み込まれない）

---

## 8. MCP サーバーサポート

### 8.1 設定方法

`settings.json` の `mcpServers` でMCPサーバーを設定：

```json
{
  "mcpServers": {
    "serverName": {
      "command": "path/to/server",
      "args": ["--arg1", "value1"],
      "env": {"API_KEY": "$MY_API_TOKEN"},
      "cwd": "./server-directory",
      "timeout": 30000,
      "trust": false
    }
  }
}
```

### 8.2 トランスポートタイプ

| タイプ | 設定プロパティ | 説明 |
|-------|-------------|------|
| **Stdio** | `command` | サブプロセスを起動し stdin/stdout で通信 |
| **SSE** | `url` | Server-Sent Events エンドポイント |
| **HTTP Streaming** | `httpUrl` | Streamable HTTP トランスポート |

### 8.3 CLI による追加

```bash
gemini mcp add [options] <name> <commandOrUrl> [args...]

# Stdio サーバー（デフォルト）
gemini mcp add my-server python -m my_mcp_server

# SSE サーバー
gemini mcp add -t sse my-sse-server http://localhost:8080/sse

# HTTP サーバー
gemini mcp add -t http my-http-server http://localhost:3000/mcp
```

### 8.4 設定プロパティ

| プロパティ | 必須 | 型 | 説明 |
|-----------|------|-----|------|
| `command` | いずれか1つ | string | Stdio 用の実行パス |
| `url` | いずれか1つ | string | SSE エンドポイント |
| `httpUrl` | いずれか1つ | string | HTTP Streaming エンドポイント |
| `args` | 任意 | string[] | コマンド引数 |
| `headers` | 任意 | object | カスタム HTTP ヘッダー |
| `env` | 任意 | object | 環境変数（`$VAR` 展開対応） |
| `cwd` | 任意 | string | 作業ディレクトリ |
| `timeout` | 任意 | number | タイムアウト（ms、デフォルト 600,000） |
| `trust` | 任意 | boolean | 確認プロンプトをバイパス |
| `includeTools` | 任意 | string[] | ツールのアローリスト |
| `excludeTools` | 任意 | string[] | ツールのブロックリスト |

### 8.5 セキュリティ

- 環境変数は自動展開（POSIX: `$VAR`, Windows: `%VAR%`）
- API キーやトークンなどの機密変数はベース環境から自動除去
- 明示的に `env` で設定した変数のみ MCP サーバーに渡される

---

## 9. 拡張機能（Extensions）

### 9.1 概要

拡張機能は、プロンプト・MCP サーバー・カスタムコマンドをパッケージ化して Gemini CLI の機能を拡張する仕組み。

### 9.2 主なコマンド

```bash
gemini --list-extensions        # 拡張機能一覧
gemini -e ext1 ext2             # 拡張機能を指定して起動
gemini extensions config <ext>  # 拡張機能の設定管理
```

### 9.3 主要な公式拡張機能（v0.32.1 時点）

- Conductor: プランニング支援
- Endor Labs: コード分析
- Rill: データ分析
- Browserbase: Web インタラクション
- Eleven Labs: 音声

### 9.4 v0.32.0+ の改善

- 拡張機能の並列読み込みにより起動が高速化
- カスタムテーマの拡張機能定義（v0.28.0+）
- 拡張機能設定のインストール時プロンプト（v0.28.0+）
- 機密設定のシステムキーチェーン保存

---

## 10. Plan Mode（v0.29.0+）

### 10.1 概要

Plan Mode は、タスク実行前にGeminiと協力して実装計画を設計する機能。

### 10.2 使用方法

```bash
# インタラクティブモードで /plan コマンド
/plan

# または自然言語で
"start a plan for refactoring the authentication module"
```

### 10.3 ワークフロー

1. タスクの説明を入力
2. Gemini がコードベースを分析し、質問や選択肢を提示
3. ユーザーが方針を選択
4. 実装計画が Markdown ファイルとして生成
5. 外部エディタで計画の確認・編集が可能（v0.32.0+）

### 10.4 Agent Skills との統合

Plan Mode 内でスキルを有効化すると、専門的な知識がリサーチ・設計・計画フェーズを支援する。

---

## 11. Agent Skills（v0.23.0+ 実験的、v0.26.0+ デフォルト有効）

### 11.1 概要

Agent Skills は、セキュリティ監査・クラウドデプロイ・コードベース移行などの専門的な能力をオンデマンドで提供する仕組み。一般的なコンテキストファイルと異なり、必要時にのみコンテキストウィンドウにロードされる。

### 11.2 組み込みスキル

- `pr-creator`: PR 作成支援（v0.25.0+）
- `skill-creator`: カスタムスキル作成支援（v0.26.0+）

### 11.3 関連コマンド

```bash
/skills reload           # スキルの再読み込み
/skills install <name>   # スキルのインストール
/skills uninstall <name> # スキルのアンインストール
/agents refresh          # エージェントの更新
```

---

## 12. Hooks（v0.31.0+）

### 12.1 概要

`gemini hooks` コマンドは Gemini CLI のフック機能を管理する。Claude Code からの移行をサポートする `migrate` サブコマンドが提供されている。

### 12.2 コマンド

```bash
gemini hooks <command>       # フック管理（エイリアス: hook）

# サブコマンド
gemini hooks migrate         # Claude Code のフックを Gemini CLI に移行
```

### 12.3 Claude Code からの移行

`gemini hooks migrate` を実行すると、Claude Code で設定済みのフック（pre-tool-use, post-tool-use 等）を Gemini CLI のフック形式に変換・移行する。既存の Claude Code ユーザーが Gemini CLI に移行する際に便利。

---

## 13. インタラクティブコマンド一覧

v0.32.1 で利用可能な主要スラッシュコマンド：

| コマンド | 説明 | 追加バージョン |
|---------|------|--------------|
| `/help` | ヘルプ表示 | — |
| `/plan` | Plan Mode を開始 | v0.29.0 |
| `/model` | モデル切り替え | — |
| `/settings` | 設定エディタを開く | — |
| `/stats` | セッション統計表示 | — |
| `/rewind` | 会話履歴を巻き戻す | v0.27.0 |
| `/introspect` | デバッグ情報表示 | v0.26.0 |
| `/prompt-suggest` | プロンプト提案 | v0.28.0 |
| `/logout` | 認証情報クリア | v0.23.0 |
| `/skills reload` | スキル再読み込み | v0.24.0 |
| `/agents refresh` | エージェント更新 | v0.24.0 |

---

## 14. 3CLI比較表

| 機能 | Gemini CLI (v0.32.1) | Claude Code | Codex CLI |
|------|---------------------|-------------|-----------|
| 非インタラクティブ | 位置引数 / `-p` | `-p` | `exec` |
| セッション継続（最新） | `-r latest` | `-c` | `resume --last` |
| セッション継続（ID指定） | `-r <番号/UUID>` | `-r <uuid>` | `resume <uuid>` |
| セッション一覧 | `--list-sessions` | なし | なし |
| セッション削除 | `--delete-session` | なし | なし |
| JSON出力 | `-o json` | `--output-format json` | `--json` |
| ストリーミング | `-o stream-json` | `--output-format stream-json` | JSONL（デフォルト） |
| モデル指定 | `-m` | `--model` | `-m` |
| 自動承認 | `--approval-mode yolo` | `--permission-mode` | `--full-auto` |
| ツール制御 | Policy Engine（TOML） | — | — |
| サンドボックス | `-s`（Docker/Podman/gVisor/Seatbelt/LXC） | — | — |
| MCP サポート | `settings.json` / `gemini mcp add` | `claude mcp add` | — |
| Plan Mode | `/plan` | — | — |
| Agent Skills | 組み込み + カスタム | — | — |
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
   - Gemini: `auto` で複数モデル自動ルーティング（3世代 + 2.5世代）
   - Claude: 単一モデル（切り替えは手動）
   - Codex: 単一モデル（切り替えは手動）

4. **resume時のプロンプト指定**
   - Gemini: `-p` または stdin 必須
   - Claude: 位置引数または `-r` の後に直接
   - Codex: 位置引数

5. **ツール制御（v0.30.0+ の変化点）**
   - Gemini: Policy Engine で TOML ベースの宣言的制御、ティア別優先度
   - Claude: 組み込みの権限管理
   - Codex: `--full-auto` のみ

---

## 15. 実践的な使用パターン

### 15.1 単一エージェント（シンプル）

```bash
# セッション開始
gemini "タスク1を開始"

# 最新セッションを継続
gemini -r latest -p "続きの作業"
```

### 15.2 複数エージェントの並列実行

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

### 15.3 stream-jsonからセッションID取得

```bash
# セッションIDを取得
SESSION_ID=$(gemini "タスク開始" -o stream-json 2>&1 | \
  grep '"type":"init"' | jq -r '.session_id')

# UUIDでセッション継続
gemini -r "$SESSION_ID" -p "続きの作業" -o json
```

### 15.4 非インタラクティブ + Policy Engine

```bash
# ワークスペースポリシーでツール制御
mkdir -p .gemini/policies

cat > .gemini/policies/automation.toml << 'EOF'
[[rule]]
toolName = "run_shell_command"
commandRegex = "^(git|gh|pytest|ruff) "
decision = "allow"
priority = 200

[[rule]]
toolName = ["write_file", "replace", "read_file", "list_directory", "glob"]
decision = "allow"
priority = 100
EOF

# ポリシーが自動適用される
gemini -o stream-json "pytest を実行して結果を報告して"
```

### 15.5 サンドボックス + 自動承認

```bash
# 安全な自動実行（サンドボックス内で全ツール許可）
gemini --approval-mode yolo -o json "リファクタリングを実施して"
# --approval-mode yolo 時はサンドボックスが自動有効化
```

---

## 16. トラブルシューティング

### 16.1 resume時にエラー

```
When resuming a session, you must provide a message via --prompt (-p) or stdin
```

→ `-p` でプロンプトを指定：
```bash
gemini -r latest -p "続きの質問"
```

### 16.2 認証エラー

```
Loaded cached credentials.
```
このメッセージは正常。エラーの場合は `gemini auth` で再認証。
`/logout` コマンドで認証情報をクリアしてから再認証することも可能。

### 16.3 セッションが見つからない

```bash
gemini --list-sessions  # 現在のセッションを確認
```

セッションはプロジェクトディレクトリごとに管理される。

### 16.4 非インタラクティブモードでツールが使えない

```
Tool "run_shell_command" not found in registry.
```

→ セクション 2.6.3 の解決策を参照。Policy Engine またはレガシーの `--allowed-tools` でツールを有効化する。

### 16.5 Policy Engine のルールが無視される

`--approval-mode auto_edit` + 非インタラクティブモードの組み合わせで発生。セクション 2.6.4 の回避策を参照。

### 16.6 サンドボックスモードの問題

```bash
# デバッグモードで詳細ログを確認
DEBUG=1 gemini -s -p "command"
```

- WSL 環境の Docker Desktop なしでは Docker サンドボックスが失敗する場合がある
- `.gemini/.env` でCLI固有の環境変数を設定（プロジェクトルートの `.env` は読み込まれない）

---

## 17. 参考リンク

- [Gemini CLI GitHub](https://github.com/google-gemini/gemini-cli)
- [Gemini CLI 公式ドキュメント](https://geminicli.com/docs/)
- [Gemini CLI チートシート](https://geminicli.com/docs/cli/cli-reference/)
- [Policy Engine リファレンス](https://geminicli.com/docs/reference/policy-engine/)
- [MCP サーバー設定](https://geminicli.com/docs/tools/mcp-server/)
- [サンドボックスモード](https://geminicli.com/docs/cli/sandbox/)
- [Plan Mode](https://geminicli.com/docs/cli/plan-mode/)
- [Agent Skills](https://geminicli.com/docs/cli/skills/)
- [シェルツール](https://geminicli.com/docs/tools/shell/)
- [Google AI Studio](https://aistudio.google.com/)
- [Gemini API ドキュメント](https://ai.google.dev/gemini-api/docs/models)

---

## 18. 一次情報と検証状況

| 情報 | 一次情報源 | 検証方法 | 検証日 |
|------|-----------|---------|--------|
| コマンドオプション | `gemini --help` (v0.31.0) | ローカル実行 | 2026-03-09 |
| サブコマンド | `gemini skills --help`, `gemini hooks --help` (v0.31.0) | ローカル実行 | 2026-03-09 |
| JSON出力フォーマット | 実機検証（v0.18.0時点） | ローカル実行 | 2025-11-27 |
| stream-json フォーマット | 実機検証（v0.18.0時点） | ローカル実行 | 2025-11-27 |
| セッション管理 | 実機検証（v0.18.0時点） | ローカル実行 | 2025-11-27 |
| 非インタラクティブモードのツール制限 | 実機検証（v0.18.0時点） | ローカル実行 | 2025-11-30 |
| Policy Engine | Web検索 | 未実機検証 | 2026-03-09 |
| モデル一覧（Gemini 3系） | Web検索 | 未実機検証 | 2026-03-09 |
| サンドボックスモード | Web検索 | 未実機検証 | 2026-03-09 |
| MCP サポート | Web検索 | 未実機検証 | 2026-03-09 |
| 拡張機能 | Web検索 | 未実機検証 | 2026-03-09 |
| Plan Mode | Web検索 | 未実機検証 | 2026-03-09 |
| Agent Skills | Web検索 | 未実機検証 | 2026-03-09 |

> **注意**: 「未実機検証」の項目はWeb検索結果に基づく。バージョンアップにより仕様が変更されている可能性がある。
> 実機で検証する場合は `gemini --help` で最新仕様を確認すること。
> 参考URLが実在するかは未検証。特に `geminicli.com` ドメインのURLは要確認。

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2025-11-27 | 初版作成（v0.18.0 対象） |
| 2025-11-30 | セクション 2.6「非インタラクティブモードでのツール制限」追加。`--allowed-tools` フラグによる `run_shell_command` 有効化手順、`gh`/`uv` 等の検証結果、セキュリティ考慮事項を詳細に記載。bugfix-agent での実装方法を追加。 |
| 2026-03-09 | v0.32.1 対応に全面更新。主な変更: (1) Policy Engine（v0.30.0+）セクション追加、`--allowed-tools` 非推奨化を反映、(2) モデル一覧を Gemini 3/3.1 Pro Preview 追加で更新、(3) サンドボックスモード詳細セクション追加（Docker/Podman/gVisor/Seatbelt/LXC）、(4) MCP サーバーサポート詳細セクション追加（Stdio/SSE/HTTP Streaming）、(5) 拡張機能セクション追加、(6) Plan Mode（v0.29.0+）セクション追加、(7) Agent Skills（v0.23.0+）セクション追加、(8) インタラクティブコマンド一覧追加、(9) 3CLI比較表を更新、(10) トラブルシューティングを拡充。v0.18.0 から v0.32.1 間の主要な変更（14バージョン分）を反映。 |
| 2026-03-09 | `--raw-output` / `--accept-raw-output-risk` 追加、バージョン表記を `--help` 取得版（v0.31.0）とWeb検索版（v0.32.1）に分離、`--experimental-zed-integration` に未記載注記追加、`gemini hooks` コマンドセクション追加、一次情報と検証状況セクション追加。 |
