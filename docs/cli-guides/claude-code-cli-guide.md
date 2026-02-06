# Claude Code CLI ガイド

## 概要

Claude Code CLI の包括的なリファレンス。
セッション管理、Permissions設定、JSON出力、実践的な使用パターンを記載。

**対象バージョン**: Claude Code v2.0.55+
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

`-p` / `--print` オプションで応答を出力して終了。

---

## 2. 利用可能なパラメータ

### 2.1 主要オプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--print` | `-p` | 非インタラクティブモード | `-p "質問"` |
| `--model` | - | 使用モデル | `--model sonnet` |
| `--output-format` | - | 出力形式 | `--output-format json` |
| `--continue` | `-c` | 最新セッションを継続 | `-c "続きの質問"` |
| `--resume` | `-r` | セッションIDで再開 | `-r <session_id>` |
| `--session-id` | - | 特定のセッションIDを使用 | `--session-id <uuid>` |
| `--fork-session` | - | 新しいセッションIDで再開 | `--resume -r --fork-session` |
| `--system-prompt` | - | システムプロンプト | `--system-prompt "..."` |
| `--append-system-prompt` | - | システムプロンプトに追加 | `--append-system-prompt "..."` |
| `--add-dir` | - | 追加ディレクトリアクセス | `--add-dir /path` |

### 2.2 出力形式オプション（`--print` 必須）

| オプション | 説明 |
|-----------|------|
| `--output-format text` | テキスト形式（デフォルト） |
| `--output-format json` | JSON形式（単一結果） |
| `--output-format stream-json` | ストリーミングJSON（`--verbose` 必須） |

### 2.3 ツール制御オプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--allowed-tools` | 許可するツール | `--allowed-tools "Bash(git:*) Edit"` |
| `--disallowed-tools` | 禁止するツール | `--disallowed-tools "Bash"` |
| `--tools` | 利用可能ツールを指定 | `--tools "Bash,Edit,Read"` |

### 2.4 権限オプション

| オプション | 説明 |
|-----------|------|
| `--permission-mode default` | デフォルト（確認あり） |
| `--permission-mode acceptEdits` | 編集を自動承認 |
| `--permission-mode bypassPermissions` | 権限チェックをスキップ |
| `--permission-mode dontAsk` | 確認なし |
| `--permission-mode plan` | プランモード |
| `--dangerously-skip-permissions` | 全権限チェックをスキップ（危険） |

---

## 3. Permissions設定

### 3.1 パターン構文

**重要**: パターンには**コロン（`:`）**が必要。

| パターン | 結果 | 備考 |
|---------|------|------|
| `Bash(rm:*)` | ✅ ブロック成功 | 正しい構文 |
| `Bash(git config:*)` | ✅ ブロック成功 | スペース含むコマンドも可 |
| `Bash(rm*)` | ❌ 動作せず | コロン欠落 |

### 3.2 設定ファイル優先順位

| ファイル | スコープ | 優先度 |
|---------|---------|--------|
| `managed-settings.json` | 企業管理 | 最高（上書き不可） |
| `~/.claude/settings.json` | ユーザー全体 | 高 |
| `.claude/settings.json` | プロジェクト | 中 |
| `.claude/settings.local.json` | ローカルのみ | 低 |

### 3.3 処理順序

```
PreToolUse Hook → Deny Rules → Allow Rules → Ask Rules → Permission Mode Check
```

**Deny Rules が最優先**で処理される。

### 3.4 ベストプラクティス

#### セキュリティ重視（エンタープライズ向け）

```json
{
  "permissions": {
    "deny": [
      "Read(**/.env)", "Read(**/.env.*)", "Write(**/.env)", "Edit(**/.env)",
      "Read(~/.aws/**)", "Read(~/.ssh/**)",
      "Bash(sudo:*)", "Bash(su:*)", "Bash(curl:*)", "Bash(wget:*)",
      "Bash(rm:*)", "Bash(chmod:*)", "WebFetch"
    ]
  }
}
```

#### 開発効率重視（個人開発向け）

```json
{
  "permissions": {
    "deny": [
      "Bash(sudo:*)", "Bash(su:*)", "Bash(chmod:*)", "Bash(chown:*)",
      "Bash(dd:*)", "Bash(mkfs:*)", "Bash(fdisk:*)",
      "Bash(shutdown:*)", "Bash(reboot:*)", "Bash(halt:*)", "Bash(poweroff:*)",
      "Bash(curl:*)", "Bash(wget:*)", "Bash(git config:*)"
    ]
  }
}
```

### 3.5 注意点

1. **`Read` をブロックしても `Bash(cat:*)` で読める** → 完全保護には両方ブロック必要
2. **Bash パターンはプレフィックスマッチング** → `Bash(rm:*)` は `rm`, `rm -rf`, `rmdir` 等にマッチ
3. **ツール別に個別設定が必要** → `Read`, `Write`, `Edit` は別々のツール

### 3.6 非インタラクティブモードでの制限

`-p` モードではBashコマンドがデフォルトでブロックされる。

**解決策**: `~/.claude/settings.json` に `permissions.allow` を追加：

```json
{
  "permissions": {
    "allow": [
      "Bash(gh:*)", "Bash(git:*)", "Read", "Write", "Edit"
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
| `-r, --resume [sessionId]` | 指定セッションを再開 | 複数エージェント管理 |
| `--session-id <uuid>` | 特定UUIDでセッション開始 | 事前にID指定 |
| `--fork-session` | 新IDで分岐（resumeと併用） | セッションのコピー |

### 4.2 セッションIDの取得方法

JSON出力から `session_id` フィールドを抽出：

```bash
SESSION_ID=$(claude -p --output-format json "タスク開始" 2>&1 | jq -r '.session_id')
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
    "claude-opus-4-5-20251101": {
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
  "cwd": "/home/aki/claude/kamo2",
  "session_id": "565b60b3-1ee7-43da-9001-03e4ff9b40ee",
  "tools": ["Task", "Bash", "Glob", "Grep", "Read", "Edit", "Write", ...],
  "mcp_servers": [
    {"name": "context7", "status": "connected"},
    {"name": "codex", "status": "connected"}
  ],
  "model": "claude-opus-4-5-20251101",
  "permissionMode": "default",
  "slash_commands": ["compact", "context", "cost", ...],
  "agents": ["general-purpose", "Explore", "Plan", ...],
  "claude_code_version": "2.0.55"
}
```

**イベント2: アシスタント応答**
```json
{
  "type": "assistant",
  "message": {
    "model": "claude-opus-4-5-20251101",
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
  "usage": {...},
  "modelUsage": {...}
}
```

#### stream-json イベントタイプ

| type | subtype | 説明 |
|------|---------|------|
| `system` | `init` | 初期化情報（ツール、MCP、設定等） |
| `assistant` | - | アシスタントの応答メッセージ |
| `result` | `success` / `error` | 最終結果 |

---

## 6. 実践的な使用パターン

### 6.1 単一エージェント（シンプル）

```bash
# セッション開始
claude -p "タスク1を開始"

# 最新セッションを継続
claude -p -c "続きの作業"
```

### 6.2 複数エージェントの並列実行

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

### 6.3 コスト管理パターン

```bash
# コストを取得して記録
RESULT=$(claude -p --output-format json "複雑なタスク")
COST=$(echo "$RESULT" | jq '.total_cost_usd')
SESSION=$(echo "$RESULT" | jq -r '.session_id')
echo "Session: $SESSION, Cost: \$$COST"
```

### 6.4 モデル切り替えパターン

```bash
# 軽いタスクはHaiku
claude -p --model haiku "簡単な質問"

# 複雑なタスクはOpus
claude -p --model opus "複雑な分析"

# Sonnetでバランス
claude -p --model sonnet "通常のタスク"
```

---

## 7. 利用可能なモデル

| エイリアス | モデルID | 特徴 |
|-----------|---------|------|
| `haiku` | `claude-haiku-4-5-20251001` | 軽量・高速・低コスト |
| `sonnet` | `claude-sonnet-4-5-20250929` | バランス型 |
| `opus` | `claude-opus-4-5-20251101` | 高性能・高コスト |

---

## 8. Codex CLI との比較

| 機能 | Claude Code | Codex CLI |
|------|------------|-----------|
| 非インタラクティブ | `-p` | `exec` |
| セッション継続（最新） | `-c` / `--continue` | `exec resume --last` |
| セッション継続（ID指定） | `-r <id>` / `--resume` | `exec resume <id>` |
| JSON出力 | `--output-format json` | `--json` |
| ストリーミング | `--output-format stream-json` | JSONL形式（デフォルト） |
| モデル指定 | `--model` | `-m` |
| システムプロンプト | `--system-prompt` | `-c` で設定 |
| ツール制御 | `--allowed-tools` / `--tools` | MCP設定 |
| コスト情報 | `total_cost_usd` | `usage.input_tokens` 等 |
| 作業ディレクトリ | カレント固定 | `-C` で指定 |

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

4. **セッション設定の引き継ぎ**
   - Claude Code: `-r` で再開時、設定は引き継がれない（再指定必要）
   - Codex CLI: `cwd`, `sandbox` 等は自動引き継ぎ

---

## 9. トラブルシューティング

### 9.1 stream-json でエラー

```
Error: When using --print, --output-format=stream-json requires --verbose
```

→ `--verbose` オプションを追加：
```bash
claude -p --output-format stream-json --verbose "質問"
```

### 9.2 セッション継続が遅い

`-c` での継続は過去のコンテキストを読み込むため時間がかかる場合がある。

→ 短いセッションでは新規開始の方が高速な場合も。

### 9.3 権限エラー

```bash
# 権限を緩和（危険：信頼できる環境のみ）
claude -p --permission-mode bypassPermissions "タスク"

# または特定ツールのみ許可
claude -p --allowed-tools "Read,Glob,Grep" "調査タスク"
```

---

## 10. 参考リンク

- [Claude Code 公式ドキュメント](https://docs.anthropic.com/claude-code)
- [Claude API リファレンス](https://docs.anthropic.com/en/api)
- [Anthropic 価格表](https://www.anthropic.com/pricing)

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2025-11-27 | 初版作成 |
