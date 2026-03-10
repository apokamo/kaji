# Claude Code CLI ガイド

## 概要

Claude Code CLI の包括的なリファレンス。
セッション管理、Permissions設定、JSON出力、サブエージェント、実践的な使用パターンを記載。

**対象バージョン**: Claude Code v2.1.71+
**--help 取得日**: 2026-03-09（v2.1.71 のローカル環境で取得）
**公式ドキュメント**: https://docs.anthropic.com/claude-code

---

## 1. 基本コマンド構造

### 1.1 インタラクティブモード（デフォルト）

```bash
claude [OPTIONS] [PROMPT]
```

### 1.2 非インタラクティブモード（パイプ向け）

```bash
claude -p [OPTIONS] "プロンプト"
```

`-p` / `--print` オプションで応答を出力して終了。Agent SDK からのプログラム的利用にも対応。

### 1.3 その他のコマンド

| コマンド | 説明 |
|---------|------|
| `claude update` | 最新バージョンに更新 |
| `claude auth login` | Anthropicアカウントにログイン（`--email`, `--sso` オプション） |
| `claude auth logout` | ログアウト |
| `claude auth status` | 認証状態をJSON表示（`--text` でテキスト表示） |
| `claude agents` | 設定済みサブエージェント一覧（ソース別グループ表示） |
| `claude mcp` | MCP サーバー設定 |
| `claude remote-control` | Claude.ai / Claude アプリからのリモート制御セッション開始 |

---

## 2. 利用可能なパラメータ

### 2.1 主要オプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--print` | `-p` | 非インタラクティブモード | `-p "質問"` |
| `--model` | - | 使用モデル（エイリアスまたはフルモデル名） | `--model opus` |
| `--output-format` | - | 出力形式（`-p` 必須） | `--output-format json` |
| `--input-format` | - | 入力形式（`-p` 必須） | `--input-format stream-json` |
| `--continue` | `-c` | 最新セッションを継続 | `-c "続きの質問"` |
| `--resume` | `-r` | セッションIDまたは名前で再開 | `-r "auth-refactor"` |
| `--session-id` | - | 特定のセッションIDを使用（UUID形式） | `--session-id <uuid>` |
| `--fork-session` | - | 新しいセッションIDで再開（`-r`/`-c` と併用） | `--resume abc --fork-session` |
| `--from-pr` | - | PR番号/URLに紐づくセッションを再開 | `--from-pr 123` |
| `--system-prompt` | - | システムプロンプト全体を置換 | `--system-prompt "..."` |
| `--system-prompt-file` | - | ファイルからシステムプロンプトを読み込み（置換） | `--system-prompt-file ./prompt.txt` |
| `--append-system-prompt` | - | デフォルトプロンプトに追加 | `--append-system-prompt "..."` |
| `--append-system-prompt-file` | - | ファイルからプロンプトを追加読み込み | `--append-system-prompt-file ./rules.txt` |
| `--add-dir` | - | 追加ディレクトリアクセス | `--add-dir ../apps ../lib` |
| `--verbose` | - | 詳細ログ出力（ターン毎の出力） | `--verbose` |
| `--debug` | `-d` | デバッグモード（カテゴリフィルタ可） | `--debug "api,mcp"` |
| `--debug-file` | - | デバッグログを指定ファイルに出力（暗黙的にデバッグモード有効） | `--debug-file ./debug.log` |
| `--effort` | - | セッションのエフォートレベル（low, medium, high） | `--effort high` |
| `--version` | `-v` | バージョン表示 | `-v` |

### 2.2 新機能オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--agent` | セッションで使用するエージェントを指定 | `--agent my-custom-agent` |
| `--agents` | カスタムサブエージェントをJSON定義 | `--agents '{"reviewer":{...}}'` |
| `--max-budget-usd` | API呼び出しのコスト上限（`-p` 必須） | `--max-budget-usd 5.00` |
| `--max-turns` | エージェントターン数の上限（`-p` 必須） | `--max-turns 3` |
| `--json-schema` | 構造化出力のJSONスキーマ（`-p` 必須） | `--json-schema '{"type":"object",...}'` |
| `--fallback-model` | 過負荷時のフォールバックモデル（`-p` 必須） | `--fallback-model sonnet` |
| `--worktree` | `-w` | 隔離されたgit worktreeで起動 | `-w feature-auth` |
| `--teammate-mode` | エージェントチームの表示モード | `--teammate-mode tmux` |
| `--no-session-persistence` | セッション永続化無効（`-p` 必須） | `--no-session-persistence` |
| `--include-partial-messages` | 部分ストリーミングイベント出力（`-p` + `stream-json`） | `--include-partial-messages` |
| `--file` | 起動時にダウンロードするファイルリソース（file_id:相対パス形式） | `--file file_abc:doc.txt` |
| `--settings` | 追加設定ファイルまたはJSON文字列 | `--settings ./settings.json` |
| `--setting-sources` | 読み込む設定ソース（user,project,local） | `--setting-sources user,project` |
| `--chrome` | Chrome ブラウザ統合を有効化 | `--chrome` |
| `--no-chrome` | Chrome ブラウザ統合を無効化 | `--no-chrome` |
| `--ide` | IDE自動接続 | `--ide` |
| `--betas` | ベータ機能ヘッダー（APIキーユーザーのみ） | `--betas interleaved-thinking` |
| `--init` | 初期化フックを実行してインタラクティブモード開始 | `--init` |
| `--init-only` | 初期化フックを実行して終了 | `--init-only` |
| `--maintenance` | メンテナンスフックを実行して終了 | `--maintenance` |
| `--remote` | claude.ai でウェブセッション作成 | `--remote "Fix the bug"` |
| `--teleport` | ウェブセッションをローカルターミナルで再開 | `--teleport` |
| `--disable-slash-commands` | スキル・コマンドを無効化 | `--disable-slash-commands` |
| `--strict-mcp-config` | `--mcp-config` のMCPサーバーのみ使用 | `--strict-mcp-config` |
| `--mcp-config` | MCPサーバー設定ファイル | `--mcp-config ./mcp.json` |
| `--permission-prompt-tool` | 権限プロンプトを処理するMCPツール | `--permission-prompt-tool mcp_auth` |
| `--plugin-dir` | プラグインディレクトリ読み込み | `--plugin-dir ./my-plugins` |
| `--allow-dangerously-skip-permissions` | 権限バイパスをオプションとして有効化 | `--permission-mode plan --allow-dangerously-skip-permissions` |
| `--replay-user-messages` | ユーザーメッセージのリプレイ | `--replay-user-messages` |

### 2.3 出力形式オプション（`--print` 必須）

| オプション | 説明 |
|-----------|------|
| `--output-format text` | テキスト形式（デフォルト） |
| `--output-format json` | JSON形式（単一結果） |
| `--output-format stream-json` | ストリーミングJSON（NDJSON形式） |

### 2.4 入力形式オプション（`--print` 必須）

| オプション | 説明 |
|-----------|------|
| `--input-format text` | テキスト入力（デフォルト） |
| `--input-format stream-json` | NDJSON形式でストリーミング入力（マルチエージェントパイプライン向け） |

### 2.5 ツール制御オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--allowedTools` | 許可するツール（権限プロンプトなしで実行） | `--allowedTools "Bash(git log *)" "Read"` |
| `--disallowedTools` | 禁止するツール（コンテキストから除外） | `--disallowedTools "Bash" "Edit"` |
| `--tools` | 利用可能ツールを制限 | `--tools "Bash,Edit,Read"` |

**注意**: `--allowedTools` はパーミッションの自動承認、`--tools` は利用可能なツール自体の制限。用途が異なる。

### 2.6 権限オプション

| オプション | 説明 |
|-----------|------|
| `--permission-mode default` | デフォルト（初回使用時に確認） |
| `--permission-mode acceptEdits` | ファイル編集を自動承認 |
| `--permission-mode plan` | プランモード（分析のみ、変更不可） |
| `--permission-mode dontAsk` | 確認なし（事前許可済みツールのみ実行、他はすべて拒否） |
| `--permission-mode bypassPermissions` | 全権限チェックをスキップ（コンテナ/VM等の隔離環境のみ） |
| `--dangerously-skip-permissions` | 全権限チェックをスキップ（危険） |
| `--allow-dangerously-skip-permissions` | 権限バイパスをオプション有効化（他モードと組み合わせ可） |

---

## 3. Permissions設定

### 3.1 パターン構文

**v2.1.x 以降のワイルドカード構文**:

| パターン | 結果 | 備考 |
|---------|------|------|
| `Bash(rm *)` | ✅ ブロック成功 | スペース+`*` でワードバウンダリ |
| `Bash(git commit *)` | ✅ マッチ | `git commit -m "msg"` 等にマッチ |
| `Bash(ls*)` | ✅ マッチ | `ls`, `lsof` 両方にマッチ |
| `Bash(* --help *)` | ✅ マッチ | 先頭・中間・末尾のワイルドカード対応 |

**レガシー構文**: `Bash(rm:*)` のコロン構文は非推奨だが引き続き動作する。

### 3.2 設定ファイル優先順位

| ファイル | スコープ | 優先度 |
|---------|---------|--------|
| `managed-settings.json` | 企業管理（上書き不可） | 最高 |
| CLI引数（`--allowedTools`等） | セッション一時 | 高 |
| `.claude/settings.local.json` | ローカルプロジェクト | 中高 |
| `.claude/settings.json` | プロジェクト共有 | 中 |
| `~/.claude/settings.json` | ユーザー全体 | 低 |

### 3.3 処理順序

```
PreToolUse Hook → Deny Rules → Ask Rules → Allow Rules → Permission Mode Check
```

**Deny Rules が最優先**で処理される。どのレベルでもDenyされたツールは他のレベルで許可できない。

### 3.4 ツール別パターン

| ツール | パターン例 | 説明 |
|--------|-----------|------|
| `Bash` | `Bash(npm run *)` | ワイルドカード対応 |
| `Read` | `Read(./.env)`, `Read(~/Documents/*.pdf)` | gitignore仕様パス |
| `Edit` | `Edit(/src/**/*.ts)` | プロジェクトルート相対 |
| `WebFetch` | `WebFetch(domain:example.com)` | ドメイン指定 |
| `Agent` | `Agent(Explore)`, `Agent(my-agent)` | サブエージェント制御 |
| `mcp__*` | `mcp__puppeteer__puppeteer_navigate` | MCPツール制御 |

**パス記法の注意**:
- `/path` → プロジェクトルート相対（**NOT** 絶対パス）
- `//path` → ファイルシステムルートからの絶対パス
- `~/path` → ホームディレクトリ相対

### 3.5 ベストプラクティス

#### セキュリティ重視（エンタープライズ向け）

```json
{
  "permissions": {
    "deny": [
      "Read(.env)", "Read(.env.*)", "Edit(.env)", "Write(.env)",
      "Read(~/.aws/**)", "Read(~/.ssh/**)",
      "Bash(sudo *)", "Bash(su *)", "Bash(curl *)", "Bash(wget *)",
      "Bash(rm *)", "Bash(chmod *)", "WebFetch"
    ]
  }
}
```

#### 開発効率重視（個人開発向け）

```json
{
  "permissions": {
    "deny": [
      "Bash(sudo *)", "Bash(su *)", "Bash(chmod *)", "Bash(chown *)",
      "Bash(dd *)", "Bash(mkfs *)", "Bash(fdisk *)",
      "Bash(shutdown *)", "Bash(reboot *)", "Bash(halt *)", "Bash(poweroff *)",
      "Bash(curl *)", "Bash(wget *)", "Bash(git config *)"
    ]
  }
}
```

### 3.6 注意点

1. **`Read` をブロックしても `Bash(cat *)` で読める** → 完全保護には両方ブロック必要
2. **Bash パターンはワイルドカードマッチ** → `Bash(rm *)` は `rm`, `rm -rf` 等にマッチ。`Bash(ls *)` は `lsof` にはマッチしない（スペース+`*`）
3. **ツール別に個別設定が必要** → `Read`, `Write`, `Edit` は別々のツール
4. **シェル演算子を認識** → `Bash(safe-cmd *)` は `safe-cmd && other-cmd` を許可しない

### 3.7 非インタラクティブモードでの制限

`-p` モードではBashコマンドがデフォルトでブロックされる。

**解決策**: `~/.claude/settings.json` に `permissions.allow` を追加：

```json
{
  "permissions": {
    "allow": [
      "Bash(gh *)", "Bash(git *)", "Read", "Write", "Edit"
    ],
    "deny": [...]
  }
}
```

---

## 4. セッション管理

### 4.1 セッション継続オプション

| オプション | 説明 | 用途 |
|-----------|------|------|
| `-c, --continue` | 最新のセッションを継続 | 単一エージェント |
| `-r, --resume [sessionId/name]` | 指定セッションを再開（IDまたは名前） | 複数エージェント管理 |
| `--session-id <uuid>` | 特定UUIDでセッション開始 | 事前にID指定 |
| `--fork-session` | 新IDで分岐（resume/continueと併用） | セッションのコピー |
| `--from-pr <number/url>` | PR紐づきセッションを再開 | PR連携ワークフロー |
| `--no-session-persistence` | セッション永続化無効（`-p` 必須） | 一時的な処理 |

### 4.2 セッションIDの取得方法

JSON出力から `session_id` フィールドを抽出：

```bash
SESSION_ID=$(claude -p --output-format json "タスク開始" | jq -r '.session_id')
echo $SESSION_ID
# 出力例: 4c5a56e4-7a81-4603-a105-947e81bbfd6a
```

### 4.3 セッション引き継ぎの例

```bash
# 新規セッション開始、IDを取得
SESSION_ID=$(claude -p --output-format json "1+1は？" | jq -r '.session_id')

# 同じセッションを継続（最新セッション）
claude -p -c "その答えに2を足すと？"

# セッションIDを明示的に指定
claude -p -r "$SESSION_ID" "さらに10を足すと？"

# セッションを分岐（新しいIDで継続）
claude -p -r "$SESSION_ID" --fork-session "別の計算をして"

# PR紐づきセッションを再開
claude --from-pr 123 "PRのレビューを続けて"
```

### 4.4 セッション永続化の制御

```bash
# セッションを保存しない（一時的処理向け）
claude -p --no-session-persistence "一時的な質問"
```

---

## 5. JSON出力の詳細

### 5.1 基本JSON出力（`--output-format json`）

実行コマンド：
```bash
claude -p --output-format json "2+2は？"
```

出力（整形済み）：
```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 3371,
  "duration_api_ms": 10013,
  "num_turns": 1,
  "result": "4です。",
  "session_id": "4c5a56e4-7a81-4603-a105-947e81bbfd6a",
  "total_cost_usd": 0.233663,
  "usage": {
    "input_tokens": 3,
    "cache_creation_input_tokens": 31675,
    "cache_read_input_tokens": 0,
    "output_tokens": 7,
    "server_tool_use": {
      "web_search_requests": 0,
      "web_fetch_requests": 0
    },
    "service_tier": "standard",
    "cache_creation": {
      "ephemeral_1h_input_tokens": 0,
      "ephemeral_5m_input_tokens": 31675
    }
  },
  "modelUsage": {
    "claude-opus-4-6": {
      "inputTokens": 6,
      "outputTokens": 35,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 36215,
      "webSearchRequests": 0,
      "costUSD": 0.22724875,
      "contextWindow": 200000
    },
    "claude-haiku-4-5-20251001": {
      "inputTokens": 3,
      "outputTokens": 194,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 4353,
      "webSearchRequests": 0,
      "costUSD": 0.00641425,
      "contextWindow": 200000
    }
  },
  "permission_denials": [],
  "uuid": "1f79c5e0-2a13-4b03-b276-5308297e3b8e"
}
```

### 5.2 JSON出力のフィールド説明

#### トップレベルフィールド

| フィールド | 説明 |
|-----------|------|
| `type` | 結果タイプ（`"result"`） |
| `subtype` | 結果サブタイプ（`"success"` / `"error"`） |
| `is_error` | エラーかどうか |
| `duration_ms` | 総実行時間（ミリ秒） |
| `duration_api_ms` | API呼び出し時間（ミリ秒） |
| `num_turns` | ターン数（会話の往復数） |
| `result` | 最終回答テキスト |
| `session_id` | セッションID（UUID形式） |
| `total_cost_usd` | 総コスト（USD） |
| `uuid` | このリクエストの一意ID |

#### usage フィールド

| フィールド | 説明 |
|-----------|------|
| `input_tokens` | 入力トークン数 |
| `cache_creation_input_tokens` | キャッシュ作成トークン数 |
| `cache_read_input_tokens` | キャッシュ読み取りトークン数 |
| `output_tokens` | 出力トークン数 |
| `server_tool_use.web_search_requests` | Web検索リクエスト数 |
| `server_tool_use.web_fetch_requests` | Webフェッチリクエスト数 |
| `service_tier` | サービス層（`"standard"` 等） |

#### modelUsage フィールド

モデルごとの詳細な使用量：

| フィールド | 説明 |
|-----------|------|
| `inputTokens` | 入力トークン数 |
| `outputTokens` | 出力トークン数 |
| `cacheReadInputTokens` | キャッシュ読み取り |
| `cacheCreationInputTokens` | キャッシュ作成 |
| `webSearchRequests` | Web検索数 |
| `costUSD` | そのモデルのコスト |
| `contextWindow` | コンテキストウィンドウサイズ |

### 5.3 ストリーミングJSON出力（`--output-format stream-json`）

実行コマンド：
```bash
claude -p --output-format stream-json --verbose "2+2は？"
```

出力（JSONL形式 - 複数イベント）：

**イベント1: 初期化**
```json
{
  "type": "system",
  "subtype": "init",
  "cwd": "/home/user/project",
  "session_id": "565b60b3-1ee7-43da-9001-03e4ff9b40ee",
  "tools": ["Agent", "Bash", "Glob", "Grep", "Read", "Edit", "Write", "..."],
  "mcp_servers": [
    {"name": "context7", "status": "connected"}
  ],
  "model": "claude-opus-4-6",
  "permissionMode": "default",
  "slash_commands": ["compact", "context", "cost", "..."],
  "agents": ["general-purpose", "Explore", "Plan", "..."],
  "claude_code_version": "2.1.71"
}
```

**イベント2: アシスタント応答**
```json
{
  "type": "assistant",
  "message": {
    "model": "claude-opus-4-6",
    "id": "msg_0125ShwoNM22AEWJtiLyCYcN",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "4です。"}],
    "stop_reason": null,
    "usage": {
      "input_tokens": 3,
      "cache_creation_input_tokens": 31823,
      "output_tokens": 2
    }
  },
  "session_id": "565b60b3-1ee7-43da-9001-03e4ff9b40ee"
}
```

**イベント3: 結果**
```json
{
  "type": "result",
  "subtype": "success",
  "result": "4です。",
  "session_id": "565b60b3-1ee7-43da-9001-03e4ff9b40ee",
  "total_cost_usd": 0.203442,
  "usage": {"..."},
  "modelUsage": {"..."}
}
```

#### stream-json イベントタイプ

| type | subtype | 説明 |
|------|---------|------|
| `system` | `init` | 初期化情報（ツール、MCP、設定等） |
| `system` | `compact_boundary` | コンパクション発生 |
| `assistant` | - | アシスタントの応答メッセージ |
| `result` | `success` / `error` | 最終結果 |

### 5.4 構造化出力（`--json-schema`）

エージェント完了後にJSONスキーマに沿った検証済みJSON出力を取得：

```bash
claude -p --json-schema '{
  "type": "object",
  "properties": {
    "summary": {"type": "string"},
    "issues": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["summary", "issues"]
}' "このプロジェクトの問題点を分析して"
```

### 5.5 ストリームチェイニング（`--input-format stream-json`）

エージェント間のパイプラインを構築：

```bash
claude -p --output-format stream-json "コードを分析" \
  | claude -p --input-format stream-json --output-format stream-json "結果を処理" \
  | claude -p --input-format stream-json "最終レポート"
```

---

## 6. サブエージェント

### 6.1 概要

サブエージェントは独自のコンテキストウィンドウ、システムプロンプト、ツールアクセスを持つ特化型AIアシスタント。タスクに応じてClaude が自動的に委譲する。

### 6.2 ビルトインサブエージェント

| エージェント | モデル | ツール | 用途 |
|-------------|--------|--------|------|
| **Explore** | Haiku | 読み取り専用 | コードベース検索・分析 |
| **Plan** | 継承 | 読み取り専用 | プランモード時の調査 |
| **general-purpose** | 継承 | 全ツール | 複雑なマルチステップタスク |
| **Bash** | 継承 | ターミナル | 別コンテキストでのコマンド実行 |
| **Claude Code Guide** | Haiku | - | Claude Code 機能の質問対応 |

### 6.3 カスタムサブエージェント定義

#### ファイルベース（Markdownファイル + YAML frontmatter）

保存場所:

| 場所 | スコープ | 優先度 |
|------|---------|--------|
| `--agents` CLI フラグ | 現セッションのみ | 最高 |
| `.claude/agents/` | プロジェクト | 高 |
| `~/.claude/agents/` | ユーザー全体 | 中 |
| プラグインの `agents/` | プラグイン有効範囲 | 低 |

#### frontmatter フィールド

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | Yes | 一意識別子（小文字+ハイフン） |
| `description` | Yes | 委譲判断に使う自然言語の説明 |
| `tools` | No | 使用可能ツール。省略時は全ツール継承 |
| `disallowedTools` | No | 禁止ツール |
| `model` | No | `sonnet`, `opus`, `haiku`, `inherit`（デフォルト: `inherit`） |
| `permissionMode` | No | 権限モード |
| `maxTurns` | No | 最大ターン数 |
| `skills` | No | プリロードするスキル |
| `mcpServers` | No | MCP サーバー |
| `hooks` | No | ライフサイクルフック |
| `memory` | No | 永続メモリスコープ（`user`, `project`, `local`） |
| `background` | No | バックグラウンド実行（`true`/`false`） |
| `isolation` | No | `worktree` で隔離された作業コピーで実行 |

#### ファイル例

```markdown
---
name: code-reviewer
description: Expert code review specialist. Use proactively after code changes.
tools: Read, Glob, Grep, Bash
model: sonnet
---

You are a senior code reviewer. Focus on code quality, security, and best practices.
Review checklist:
- Code clarity and readability
- Error handling
- Security vulnerabilities
- Test coverage
```

### 6.4 CLIからのサブエージェント定義（`--agents`）

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  },
  "debugger": {
    "description": "Debugging specialist for errors and test failures.",
    "prompt": "You are an expert debugger. Analyze errors and provide fixes."
  }
}'
```

### 6.5 メインエージェントとしての起動（`--agent`）

```bash
# 特定のエージェントをメインとして起動
claude --agent my-custom-agent "タスクを実行して"
```

`--agent` で起動したエージェントは `Agent(agent_type)` 構文でサブエージェントの生成を制限できる。

---

## 7. Worktree & 並列実行

### 7.1 Git Worktree 統合（`--worktree` / `-w`）

```bash
# 名前付きworktreeで起動（.claude/worktrees/feature-auth/ に作成）
claude -w feature-auth

# 自動命名でworktree作成
claude -w

# worktree + tmux セッション
claude -w feature-auth --teammate-mode tmux
```

### 7.2 エージェントチーム表示モード（`--teammate-mode`）

| モード | 説明 |
|--------|------|
| `auto` | デフォルト（自動選択） |
| `in-process` | プロセス内表示 |
| `tmux` | tmux セッションで表示 |

### 7.3 サブエージェントの worktree 隔離

```yaml
---
name: experimental-refactor
description: Experimental code refactoring in isolation
isolation: worktree
---
```

---

## 8. 実践的な使用パターン

### 8.1 単一エージェント（シンプル）

```bash
# セッション開始
claude -p "タスク1を開始"

# 最新セッションを継続
claude -p -c "続きの作業"
```

### 8.2 複数エージェントの並列実行

```bash
#!/bin/bash

# エージェント1: コードレビュー
SESSION_REVIEW=$(claude -p --output-format json \
  --system-prompt "あなたはコードレビュアーです" \
  "レビューを開始" | jq -r '.session_id')

# エージェント2: テスト作成
SESSION_TEST=$(claude -p --output-format json \
  --system-prompt "あなたはテストエンジニアです" \
  "テスト作成を開始" | jq -r '.session_id')

echo "Review Session: $SESSION_REVIEW"
echo "Test Session: $SESSION_TEST"

# 各セッションを個別に継続
claude -p -r "$SESSION_REVIEW" "src/main.py をレビューして"
claude -p -r "$SESSION_TEST" "ユニットテストを追加して"
```

### 8.3 コスト管理パターン

```bash
# コスト上限付きで実行
claude -p --max-budget-usd 5.00 "複雑なリファクタリングタスク"

# コストを取得して記録
RESULT=$(claude -p --output-format json "タスク")
COST=$(echo "$RESULT" | jq '.total_cost_usd')
SESSION=$(echo "$RESULT" | jq -r '.session_id')
echo "Session: $SESSION, Cost: \$$COST"
```

### 8.4 モデル切り替えパターン

```bash
# 軽いタスクはHaiku
claude -p --model haiku "簡単な質問"

# 複雑なタスクはOpus
claude -p --model opus "複雑な分析"

# Sonnetでバランス
claude -p --model sonnet "通常のタスク"

# フォールバック付き（過負荷時にsonnetへ自動切替）
claude -p --model opus --fallback-model sonnet "重要なタスク"

# opusplan: プラン時Opus、実行時Sonnet
claude --model opusplan "設計と実装"
```

### 8.5 ターン数制限パターン

```bash
# 最大3ターンで終了（無限ループ防止）
claude -p --max-turns 3 "簡単な修正"
```

### 8.6 カスタムエージェントによるCI/CDパイプライン

```bash
# セッション永続化なしでCI実行
claude -p --no-session-persistence \
  --max-budget-usd 2.00 \
  --max-turns 5 \
  --output-format json \
  --allowedTools "Bash(npm *)" "Read" "Grep" "Glob" \
  "テストを実行して結果を報告"
```

### 8.7 Worktree を使った並列開発

```bash
# 機能A: worktree + tmux
claude -w feature-a --teammate-mode tmux "認証機能を実装"

# 機能B: 別のworktree
claude -w feature-b --teammate-mode tmux "APIエンドポイントを実装"
```

---

## 9. 利用可能なモデル

### 9.1 モデルエイリアス

| エイリアス | 現在のモデル | 特徴 |
|-----------|-------------|------|
| `default` | アカウントタイプ依存 | Max/Team Premium → Opus 4.6、Pro/Team Standard → Sonnet 4.6 |
| `sonnet` | Sonnet 4.6 (`claude-sonnet-4-6`) | バランス型・日常コーディング |
| `opus` | Opus 4.6 (`claude-opus-4-6`) | 高性能・複雑な推論 |
| `haiku` | Haiku 4.5 | 軽量・高速・低コスト |
| `sonnet[1m]` | Sonnet 4.6（1Mコンテキスト） | 大規模コードベース向け |
| `opusplan` | Opus(plan) + Sonnet(実行) | プラン時にOpusの推論力、実行時にSonnetの効率 |

### 9.2 拡張コンテキスト

Opus 4.6 と Sonnet 4.6 は **100万トークンのコンテキストウィンドウ** をサポート（ベータ）。

```bash
# 1M コンテキストを使用
claude --model sonnet[1m]
```

- 200Kトークンまでは通常料金
- 200K超はロングコンテキスト料金が適用
- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` で無効化可能

### 9.3 Effort Level（適応的推論）

タスクの複雑さに応じて推論の深さを制御：

| レベル | 説明 |
|--------|------|
| `low` | 高速・低コスト（簡単なタスク向け） |
| `medium` | バランス（Opus 4.6 のデフォルト） |
| `high` | 深い推論（複雑な問題向け） |

設定方法：
- **セッション中**: `/model` でスライダー調整
- **環境変数**: `CLAUDE_CODE_EFFORT_LEVEL=low|medium|high`
- **設定ファイル**: `"effortLevel": "medium"`
- **キーワード**: `ultrathink` で一時的に high effort

### 9.4 環境変数によるモデル制御

| 環境変数 | 説明 |
|---------|------|
| `ANTHROPIC_MODEL` | デフォルトモデル設定 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `opus` エイリアスのモデル指定 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `sonnet` エイリアスのモデル指定 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `haiku` エイリアスのモデル指定 |
| `CLAUDE_CODE_SUBAGENT_MODEL` | サブエージェントのモデル指定 |
| `CLAUDE_CODE_EFFORT_LEVEL` | Effort Level 設定 |
| `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` | `1` で適応的推論を無効化 |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT` | `1` で1Mコンテキストを無効化 |

---

## 10. Codex CLI との比較

| 機能 | Claude Code | Codex CLI |
|------|------------|-----------|
| 非インタラクティブ | `-p` | `exec` |
| セッション継続（最新） | `-c` / `--continue` | `exec resume --last` |
| セッション継続（ID指定） | `-r <id>` / `--resume` | `exec resume <id>` |
| JSON出力 | `--output-format json` | `--json` |
| ストリーミング | `--output-format stream-json` | JSONL形式（デフォルト） |
| モデル指定 | `--model` | `-m` |
| システムプロンプト | `--system-prompt` | `-c` で設定 |
| ツール制御 | `--allowedTools` / `--tools` | MCP設定 |
| コスト情報 | `total_cost_usd` | `usage.input_tokens` 等 |
| コスト上限 | `--max-budget-usd` | なし |
| ターン上限 | `--max-turns` | なし |
| 作業ディレクトリ | カレント固定 / `--add-dir` | `-C` で指定 |
| サブエージェント | `--agent` / `--agents` | なし |
| Worktree統合 | `--worktree` / `-w` | なし |

### 主な違い

1. **セッションID形式**
   - Claude Code: UUID形式（`4c5a56e4-7a81-4603-a105-947e81bbfd6a`）
   - Codex CLI: UUID形式（`019ac5b0-190b-75c0-bf3e-5f1acf69fcce`）

2. **JSON構造**
   - Claude Code: 単一JSONオブジェクト or JSONL
   - Codex CLI: JSONL（常にイベントベース）

3. **コスト情報**
   - Claude Code: `total_cost_usd` で総コスト、`modelUsage` でモデル別コスト
   - Codex CLI: トークン数のみ（コスト計算は別途必要）

4. **エージェント機能**
   - Claude Code: サブエージェント、エージェントチーム、worktree 隔離
   - Codex CLI: シンプルな単一エージェント

---

## 11. トラブルシューティング

### 11.1 stream-json でエラー

```
Error: When using --print, --output-format=stream-json requires --verbose
```

→ `--verbose` オプションを追加：
```bash
claude -p --output-format stream-json --verbose "質問"
```

### 11.2 セッション継続が遅い

`-c` での継続は過去のコンテキストを読み込むため時間がかかる場合がある。

→ 短いセッションでは新規開始の方が高速な場合も。

### 11.3 権限エラー

```bash
# 権限を緩和（危険：隔離環境のみ）
claude -p --permission-mode bypassPermissions "タスク"

# または特定ツールのみ許可
claude -p --allowedTools "Read" "Glob" "Grep" "調査タスク"
```

### 11.4 コスト超過

```bash
# 予算上限を設定
claude -p --max-budget-usd 1.00 "タスク"
```

### 11.5 サブエージェントが使われない

→ サブエージェントの `description` フィールドを具体的に記述。`"Use proactively"` を含めると自動委譲されやすい。

### 11.6 Worktree の競合

→ `claude -w <name>` で名前を明示指定。自動命名の場合は `.claude/worktrees/` を確認。

---

## 12. 参考リンク

- [Claude Code 公式ドキュメント](https://docs.anthropic.com/claude-code)
- [サブエージェント](https://code.claude.com/docs/en/sub-agents)
- [モデル設定](https://code.claude.com/docs/en/model-config)
- [権限設定](https://code.claude.com/docs/en/permissions)
- [Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview)
- [構造化出力](https://platform.claude.com/docs/en/agent-sdk/structured-outputs)
- [Claude API リファレンス](https://platform.claude.com/docs/en/api)
- [Anthropic 価格表](https://www.anthropic.com/pricing)

---

## 13. 一次情報と検証状況

| 情報 | 一次情報源 | 検証方法 | 検証日 |
|------|-----------|---------|--------|
| コマンドオプション | `claude --help` (v2.1.71) | ローカル実行 | 2026-03-09 |
| JSON出力フォーマット | 実機検証（v2.0.55時点） | ローカル実行 | 2025-11-27 |
| stream-json フォーマット | 実機検証（v2.0.55時点） | ローカル実行 | 2025-11-27 |
| セッション管理 | 実機検証（v2.0.55時点） | ローカル実行 | 2025-11-27 |
| サブエージェント | Web検索（公式ドキュメント） | 未実機検証 | 2026-03-09 |
| Worktree統合 | Web検索 | 未実機検証 | 2026-03-09 |
| Effort Level | Web検索 + `claude --help` | 部分検証 | 2026-03-09 |
| モデルエイリアス | Web検索 | 未実機検証 | 2026-03-09 |
| パーミッション構文（ワイルドカード） | Web検索 | 未実機検証 | 2026-03-09 |
| 拡張コンテキスト（1M） | Web検索 | 未実機検証 | 2026-03-09 |

> **注意**: 「未実機検証」の項目はWeb検索結果に基づく。バージョンアップにより仕様が変更されている可能性がある。
> 実機で検証する場合は `claude --help` で最新仕様を確認すること。
> 参考URLが実在するかは未検証。アクセスできない場合は公式ドキュメント（https://docs.anthropic.com/claude-code）から辿ること。

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-03-09 | `--file`形式修正、`--effort`/`--debug-file`追加、一次情報セクション追加、公式URLを修正 |
| 2026-03-09 | v2.1.71+対応: サブエージェント、worktree統合、effort level、1Mコンテキスト、新CLIオプション多数追加、ワイルドカード構文更新、モデル情報更新 |
| 2025-11-27 | 初版作成 |
