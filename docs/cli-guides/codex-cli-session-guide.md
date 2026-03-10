# Codex CLI セッション管理ガイド

## 概要

OpenAI Codex CLI のセッション管理機能に関する調査結果をまとめた資料。
複数エージェントの並列実行やセッション引き継ぎのベストプラクティスを記載。

**調査日**: 2026-03-09
**対象バージョン**: OpenAI Codex CLI v0.112.0
**公式リファレンス**: https://developers.openai.com/codex/cli/reference/
**--help 取得日**: 2026-03-09（v0.112.0 のローカル環境で取得）

---

## 1. 基本コマンド構造

### 1.1 主要コマンド一覧

| コマンド | エイリアス | 説明 |
|----------|-----------|------|
| `codex exec` | `codex e` | 非インタラクティブ実行 |
| `codex exec resume` | - | セッション再開 |
| `codex fork` | - | セッションフォーク（新スレッドに分岐） |
| `codex apply` | `codex a` | Codex Cloud タスクの diff を適用 |
| `codex cloud` | - | Cloud タスク管理 |
| `codex cloud exec` | - | Cloud タスクの直接実行 |
| `codex cloud list` | - | Cloud タスク一覧 |
| `codex resume` | - | インタラクティブセッション再開 |
| `codex mcp` | - | MCP サーバー管理 |
| `codex features` | - | フィーチャーフラグ管理 |
| `codex login` | - | OAuth / API キー認証 |
| `codex logout` | - | 認証情報の削除 |
| `codex completion` | - | シェル補完スクリプト生成 |
| `codex app` | - | デスクトップクライアント起動（macOS のみ） |
| `codex app-server` | - | アプリサーバーをローカル起動（実験的） |

### 1.2 新規セッション開始

```bash
codex exec [OPTIONS] [PROMPT]
```

### 1.3 セッション再開

```bash
codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]
```

### 1.4 セッションフォーク

```bash
codex fork [OPTIONS] [SESSION_ID]
```

前のインタラクティブセッションを新しいスレッドにフォーク（元のトランスクリプトを保持）。

---

## 2. 利用可能なパラメータ

### 2.1 グローバルフラグ（全サブコマンド共通）

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--model` | `-m` | 使用モデル | `-m gpt-5.4` |
| `--cd` | `-C` | 作業ディレクトリ | `-C /path/to/project` |
| `--sandbox` | `-s` | サンドボックスポリシー | `-s workspace-write` |
| `--config` | `-c` | 設定オーバーライド | `-c key=value` |
| `--enable` | - | 機能を有効化 | `--enable web_search_request` |
| `--disable` | - | 機能を無効化 | `--disable feature_name` |
| `--image` | `-i` | 画像添付 | `-i image.png` |
| `--full-auto` | - | 低摩擦モード（`--ask-for-approval on-request` のショートカット） | 承認なしで実行 |
| `--ask-for-approval` | `-a` | 承認タイミング制御 | `-a never` |
| `--add-dir` | - | 追加の書き込み可能ディレクトリ | `--add-dir /other/path` |
| `--profile` | `-p` | 設定プロファイル読み込み | `-p my-profile` |
| `--oss` | - | ローカル OSS モデルプロバイダー使用 | Ollama / LM Studio |
| `--search` | - | ライブ Web 検索を有効化 | - |
| `--no-alt-screen` | - | 代替スクリーンモード無効化 | - |
| `--dangerously-bypass-approvals-and-sandbox` | `--yolo` | 全承認・サンドボックスをバイパス（危険） | - |

### 2.2 `codex exec` 固有のオプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--json` | `--experimental-json` | JSONL形式で出力 | セッションID抽出に使用 |
| `--output-last-message` | `-o` | 最終メッセージをファイル出力 | `-o result.txt` |
| `--output-schema` | - | JSON Schema によるレスポンス検証 | `--output-schema schema.json` |
| `--ephemeral` | - | セッションファイルをディスクに永続化しない | CI 向け |
| `--color` | - | ANSI カラー出力制御 | `--color never` |
| `--skip-git-repo-check` | - | Gitリポジトリ外での実行許可 | - |

### 2.3 `codex exec resume` のオプション

`codex exec resume --help`（v0.112.0）で確認済みのオプション一覧：

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--last` | - | 最新セッションを再開 | `--last` |
| `--all` | - | 現在のディレクトリ外のセッションも対象 | `--all` |
| `--config` | `-c` | 設定オーバーライド | `-c key=value` |
| `--enable` | - | 機能を有効化 | `--enable web_search_request` |
| `--disable` | - | 機能を無効化 | `--disable feature_name` |
| `--image` | `-i` | フォローアップに画像添付 | `-i screenshot.png` |
| `--model` | `-m` | 使用モデル | `-m gpt-5.3-codex` |
| `--full-auto` | - | 低摩擦モード | 承認なしで実行 |
| `--dangerously-bypass-approvals-and-sandbox` | - | 全承認・サンドボックスをバイパス | - |
| `--skip-git-repo-check` | - | Gitリポジトリ外での実行許可 | - |
| `--ephemeral` | - | セッションファイルをディスクに永続化しない | CI 向け |
| `--json` | - | JSONL形式で出力 | セッションID抽出に使用 |
| `--output-last-message` | `-o` | 最終メッセージをファイル出力 | `-o result.txt` |
| `SESSION_ID` | - | 特定セッションID | UUID形式 |
| `PROMPT` | - | フォローアッププロンプト | stdin からもパイプ可 |

#### 注意: `exec resume` で使用**できない**オプション

- `--output-schema` → exec 固有（resume では使用不可）

> グローバルフラグ（`--model`, `--sandbox`, `--config` 等）はセッション開始時の設定を引き継ぐ。
> resume 時に `-m` でモデルを直接変更可能（`-c model=` での回避は不要）。

#### `--json` オプションの resume 対応状況

v0.112.0 にて `--json` が `codex exec resume` でも使用可能であることを確認済み。
以前のバージョン（v0.63.0 時点）では resume 時に `--json` が使用できなかったが、この制約は v0.112.0 で解消されている。

### 2.4 `codex fork` のオプション

| オプション | 説明 |
|-----------|------|
| `--all` | 現在のディレクトリ外のセッションも表示 |
| `--last` | ピッカーをスキップし最新セッションをフォーク |
| `SESSION_ID` | 特定セッションを指定してフォーク |

### 2.5 `codex cloud exec` のオプション

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--env` | 環境ID（必須） | `--env env_abc123` |
| `--attempts` | アシスタント試行回数（1-4） | `--attempts 3` |
| `QUERY` | タスクプロンプト | `"バグを修正して"` |

### 2.6 `codex cloud list` のオプション

| オプション | 説明 |
|-----------|------|
| `--cursor` | ページネーションカーソル |
| `--env` | 環境でフィルタ |
| `--json` | 機械可読出力 |
| `--limit` | 最大件数（1-20） |

---

## 3. セッションIDの取得方法

### 3.1 標準出力から確認

`codex exec` 実行時にヘッダーとして表示される：

```
OpenAI Codex v0.112.0
--------
workdir: /home/aki/project
model: gpt-5.4
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, $TMPDIR] (network access enabled)
session id: 019ac592-167e-7ac2-94c5-38ffcd86fbd0   ← ここ
--------
```

### 3.2 JSON出力からプログラム的に取得

```bash
SESSION_ID=$(codex exec -m gpt-5.4 --json "タスク" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')
```

#### JSON出力の全体例（コマンド実行を含む場合）

実行コマンド：
```bash
codex exec -m gpt-5.4 --json "pwdを実行して" 2>&1 | jq '.'
```

出力（JSONL形式 - 各行が1つのJSONイベント、以下は整形済み）：

```json
{
  "type": "thread.started",
  "thread_id": "019ac5b0-190b-75c0-bf3e-5f1acf69fcce"
}
{
  "type": "turn.started"
}
{
  "type": "item.completed",
  "item": {
    "id": "item_0",
    "type": "reasoning",
    "text": "**Executing command**"
  }
}
{
  "type": "item.started",
  "item": {
    "id": "item_1",
    "type": "command_execution",
    "command": "/bin/bash -lc pwd",
    "aggregated_output": "",
    "exit_code": null,
    "status": "in_progress"
  }
}
{
  "type": "item.completed",
  "item": {
    "id": "item_1",
    "type": "command_execution",
    "command": "/bin/bash -lc pwd",
    "aggregated_output": "/home/aki/project\n",
    "exit_code": 0,
    "status": "completed"
  }
}
{
  "type": "item.completed",
  "item": {
    "id": "item_2",
    "type": "agent_message",
    "text": "/home/aki/project"
  }
}
{
  "type": "turn.completed",
  "usage": {
    "input_tokens": 44105,
    "cached_input_tokens": 42752,
    "output_tokens": 53
  }
}
```

#### イベントタイプの説明

| type | 説明 |
|------|------|
| `thread.started` | セッション開始。`thread_id` がセッションIDとなる |
| `turn.started` | ターン（会話の1往復）の開始 |
| `item.started` | アイテム（アクション）の開始。主にコマンド実行で使用 |
| `item.completed` | アイテムの完了。推論・コマンド実行・回答など |
| `turn.completed` | ターンの終了。トークン使用量を含む |
| `turn.failed` | ターンの失敗 |
| `error` | エラーイベント |

#### item.type の種類

| item.type | 説明 | イベント |
|-----------|------|---------|
| `reasoning` | エージェントの推論・思考ステップ | `completed` のみ |
| `command_execution` | シェルコマンドの実行 | `started` → `completed` |
| `agent_message` | ユーザーへの最終回答 | `completed` のみ |
| `file_change` | ファイル変更 | `completed` のみ |
| `mcp_tool_call` | MCP ツール呼び出し | `started` → `completed` |
| `web_search` | Web 検索 | `completed` のみ |
| `plan_update` | プラン更新 | `completed` のみ |

#### command_execution の詳細フィールド

| フィールド | 説明 | 例 |
|-----------|------|-----|
| `command` | 実行されたコマンド | `"/bin/bash -lc pwd"` |
| `aggregated_output` | コマンドの標準出力全体 | `"/home/aki/project\n"` |
| `exit_code` | 終了コード（実行中は `null`） | `0` (成功), `1` (失敗) |
| `status` | 実行状態 | `"in_progress"` → `"completed"` |

**注意**: `aggregated_output` にはコマンドの出力全体が含まれるため、`ls` や `cat` などを実行すると JSON が非常に大きくなる。

#### turn.completed の usage フィールド

| フィールド | 説明 | 用途 |
|-----------|------|------|
| `input_tokens` | 入力トークン数 | コスト計算 |
| `cached_input_tokens` | キャッシュ済みトークン数 | 課金対象外（コスト削減） |
| `output_tokens` | 出力トークン数 | コスト計算 |

```bash
# トークン使用量の抽出
codex exec -m gpt-5.4 --json "質問" 2>&1 | \
  grep '"type":"turn.completed"' | jq '.usage'
```

#### セッションID抽出のワンライナー

```bash
# jq を使用
codex exec -m gpt-5.4 --json "タスク" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id'

# jq なしで抽出（sed使用）
codex exec -m gpt-5.4 --json "タスク" 2>&1 | \
  grep '"type":"thread.started"' | sed 's/.*"thread_id":"\([^"]*\)".*/\1/'
```

#### item 数の目安

| タスクの複雑さ | item数 | 主な内訳 |
|---------------|--------|----------|
| 単純な質問回答 | 2 | reasoning(1) + agent_message(1) |
| コマンド1回実行 | 3-4 | reasoning + command_execution + agent_message |
| 複数ステップ | 5+ | 各ステップごとに reasoning + 実行が増加 |

---

## 4. stdout/stderr の出力分離

Codex CLI はヘッダー情報と最終応答を異なるストリームに出力する。

| 出力先 | 内容 |
|--------|------|
| **stderr** | バージョン、workdir、model、session id、プロンプト、thinking、exec、codex応答、tokens |
| **stdout** | 最終応答のみ |

### 4.1 プログラム的な処理例

```python
# session_id は stderr から抽出
for line in stderr.splitlines():
    if line.startswith("session id:"):
        session_id = line.split(":", 1)[1].strip()
        break

# レビュー結果は stderr + stdout を結合して検索
full_output = f"{stderr}\n{stdout}".strip()
```

**注意**: `--json` モードでは全出力が stdout に JSONL 形式で出力される。

---

## 5. セッション設定の引き継ぎ動作

### 5.1 引き継ぎ一覧

| 設定項目 | 開始時指定 | resume時 | 備考 |
|---------|-----------|----------|------|
| `cwd` (`-C`) | `-C /path` | **自動引き継ぎ** | 変更不可 |
| `sandbox` (`-s`) | `-s workspace-write` | **自動引き継ぎ** | 変更不可 |
| `model` (`-m`) | `-m gpt-5.4` | **自動引き継ぎ** | `-m` で直接変更可能 |
| `features` (`--enable`) | `--enable web_search_request` | **自動引き継ぎ** | 追加/変更可能 |
| その他 `-c` 設定 | `-c key=value` | **自動引き継ぎ** | 追加/変更可能 |
| Git コンテキスト | 自動 | **自動引き継ぎ** | v0.111.0 で修正 |
| アプリ（プラグイン） | 自動 | **自動引き継ぎ** | v0.111.0 で修正 |

### 5.2 検証結果

セッション開始時に `--enable web_search_request` を指定した場合、resume時に指定しなくてもWeb検索が有効のまま維持されることを確認済み。

> **v0.111.0 修正**: resume 時に Git コンテキストとアプリが壊れる問題が修正された。

---

## 6. 実践的な使用パターン

### 6.1 単一エージェント（シンプル）

```bash
# セッション開始
codex exec -m gpt-5.4 "タスク1を開始"

# 最新セッションを引き継ぎ
codex exec resume --last "続きの作業"
```

### 6.2 複数エージェントの並列実行

```bash
#!/bin/bash

# エージェント1: コードレビュー担当
SESSION_REVIEW=$(codex exec \
  -m gpt-5.4 \
  -C /home/aki/project \
  -s workspace-write \
  --json "コードレビューを開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# エージェント2: テスト担当
SESSION_TEST=$(codex exec \
  -m gpt-5.4 \
  -C /home/aki/project \
  -s workspace-write \
  --json "テスト作成を開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# エージェント3: ドキュメント担当（Web検索有効）
SESSION_DOCS=$(codex exec \
  -m gpt-5.4 \
  -C /home/aki/project \
  -s workspace-write \
  --search \
  --json "ドキュメント作成を開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

echo "Review Session: $SESSION_REVIEW"
echo "Test Session: $SESSION_TEST"
echo "Docs Session: $SESSION_DOCS"

# 各セッションを個別に継続
codex exec resume "$SESSION_REVIEW" "src/main.py をレビューして"
codex exec resume "$SESSION_TEST" "ユニットテストを追加して"
codex exec resume "$SESSION_DOCS" "README.mdを更新して"
```

### 6.3 セッション環境の完全固定（ベストプラクティス）

```bash
# すべての設定をセッション開始時に指定
SESSION_ID=$(codex exec \
  -m gpt-5.4 \
  -C /home/aki/project \
  -s workspace-write \
  --search \
  --json "プロジェクト分析を開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# resume時はプロンプトのみ（設定はすべて引き継がれる）
codex exec resume "$SESSION_ID" "依存関係を調査して"
codex exec resume "$SESSION_ID" "セキュリティ脆弱性をチェックして"

# 必要に応じてモデルをアップグレード
codex exec resume "$SESSION_ID" \
  -m gpt-5.3-codex \
  "複雑なリファクタリングを提案して"
```

### 6.4 プログラム的実装パターン

v0.112.0 では `--json` が resume でも使用可能なため、新規・resume で統一的に扱える。

```python
def build_codex_args(session_id: str | None, model: str, workdir: str, sandbox: str) -> list[str]:
    """Codex CLI の引数を構築する（新規・resume 共通で --json 使用可能）"""
    if session_id:
        args = ["codex", "exec", "resume", session_id, "--json"]
    else:
        args = [
            "codex", "exec",
            "-m", model,
            "-C", workdir,
            "-s", sandbox,
            "--search",
            "--json",
        ]
    return args
```

**ポイント**:
- 新規・resume ともに `--json` で構造化出力が可能
- セッションIDは `thread.started` イベントの `thread_id` から取得
- `--output-schema` のみ exec 固有（resume では使用不可）

### 6.5 CI/CD での使用

```bash
# API キーを環境変数で設定（推奨）
CODEX_API_KEY=<key> codex exec \
  --json \
  --ephemeral \
  --full-auto \
  -s workspace-write \
  -o result.txt \
  "テストを実行してレポートを生成"

# --output-last-message と --json の併用で
# 機械可読なイベントストリームと最終サマリの両方を取得
```

### 6.6 セッションフォーク

```bash
# 最新セッションをフォーク（新しいスレッドに分岐）
codex fork --last

# 特定のセッションをフォーク
codex fork <SESSION_ID>

# インタラクティブセッション内では /fork スラッシュコマンドも使用可能
```

### 6.7 プロファイルの活用

```bash
# プロファイルを使用してセッション開始
codex exec -p my-review-profile "コードレビューを実行"

# プロファイルの設定例（~/.codex/config.toml）
# [profiles.my-review-profile]
# model = "gpt-5.4"
# model_reasoning_effort = "high"
# web_search = "cached"
```

### 6.8 ローカル LLM の使用

```bash
# --oss フラグでローカルモデルを使用
codex exec --oss "タスクを実行"

# Ollama プロバイダーの設定例（~/.codex/config.toml）
# [model_providers.ollama]
# name = "Ollama"
# base_url = "http://localhost:11434/v1"
#
# [profiles.gpt-oss-120b-ollama]
# model_provider = "ollama"
# model = "gpt-oss:120b"

# プロファイルと組み合わせ
codex exec -p gpt-oss-120b-ollama "ローカルで分析"
```

---

## 7. 利用可能なモデル

### 7.1 推奨モデル

| モデル | 特徴 | 用途 |
|--------|------|------|
| `gpt-5.4` | フラッグシップ。コーディング + 強力な推論 + エージェンティック | **最も推奨** |
| `gpt-5.3-codex` | 業界最高水準のコーディング特化モデル | 複雑なソフトウェアエンジニアリング |
| `gpt-5.3-codex-spark` | テキスト専用。ほぼ即座の反復に最適化 | 高速イテレーション（ChatGPT Pro 限定） |

### 7.2 その他のモデル

| モデル | 特徴 | 状態 |
|--------|------|------|
| `gpt-5.2-codex` | 高度なコーディングモデル | gpt-5.3-codex に後継 |
| `gpt-5.2` | 汎用モデル | gpt-5.4 に後継 |
| `gpt-5.1-codex-max` | 長期エージェンティックタスク最適化 | 利用可能 |
| `gpt-5.1` | クロスドメイン + エージェンティック | 利用可能 |
| `gpt-5.1-codex` | 長時間エージェンティックタスク | 後継あり |
| `gpt-5-codex` | 初代エージェンティックバリアント | レガシー |
| `gpt-5-codex-mini` | 小型・低コスト | レガシー |
| `gpt-5` | 推論重視 | レガシー |

> **注意**: 旧ガイドで記載していた `gpt-5.1-codex-mini` は `gpt-5-codex-mini` の誤記の可能性あり。
> 最新の推奨は `gpt-5.4`（汎用）または `gpt-5.3-codex`（コーディング特化）。

---

## 8. Web検索機能

### 8.1 有効化方法

```bash
# 推奨: --search フラグ（ライブ検索）
codex exec --search "質問"

# --enable フラグでも有効化可能
codex exec --enable web_search_request "質問"

# 設定ファイルで制御
# web_search = "disabled" | "cached" | "live"
```

### 8.2 Web検索の出力例

```
🌐 Searched: current USD JPY exchange rate today
```

> **注意**: デフォルトでキャッシュ済み検索が有効。`--search` でライブ検索に切り替え。

---

## 9. サンドボックスポリシー

| ポリシー | 説明 |
|---------|------|
| `read-only` | 読み取り専用（デフォルト） |
| `workspace-write` | 作業ディレクトリ + /tmp への書き込み許可 |
| `danger-full-access` | フルアクセス（危険） |

### 9.1 追加の書き込みディレクトリ

```bash
# --add-dir で追加のディレクトリに書き込み許可を付与
codex exec -s workspace-write --add-dir /other/project "クロスプロジェクト変更"
```

### 9.2 サンドボックスの詳細

`workspace-write` ではデフォルトでネットワークアクセスが有効：
```
sandbox: workspace-write [workdir, /tmp, $TMPDIR] (network access enabled)
```

> **v0.110.0**: Linux での読み取り専用アクセスが改善。`~/.ssh` 等の機密ディレクトリを除外。

### 9.3 承認ポリシー

| 値 | 説明 |
|----|------|
| `untrusted` | すべてのアクションに承認を要求 |
| `on-request` | リクエスト時のみ承認（`--full-auto` のデフォルト） |
| `never` | 承認なし（`--yolo` と同等） |

---

## 10. MCP（Model Context Protocol）サポート

### 10.1 概要

MCP サーバーを接続して追加ツールやコンテキストを Codex に提供可能。

### 10.2 CLI での管理

```bash
# MCP サーバーを追加
codex mcp add context7 -- npx -y @upstash/context7-mcp

# インタラクティブセッション内で確認
# /mcp スラッシュコマンド
```

### 10.3 設定ファイルでの構成

```toml
# ~/.codex/config.toml（グローバル）
# または .codex/config.toml（プロジェクトスコープ、信頼済みプロジェクトのみ）

[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.my-server]
command = "node"
args = ["server.js"]
bearer_token_env_var = "MY_SERVER_TOKEN"  # 任意: 認証トークン
# http_headers = { "X-Custom" = "value" }  # 任意: カスタムヘッダー
```

### 10.4 サーバータイプ

| タイプ | 説明 |
|--------|------|
| STDIO | ローカルプロセスとして起動（`command` で指定） |
| Streamable HTTP | アドレスで接続（URL で指定） |

---

## 11. スラッシュコマンド

インタラクティブセッション内で使用可能なコマンド。

### 11.1 セッション・モデル制御

| コマンド | 説明 |
|---------|------|
| `/model` | アクティブモデルの切り替え（推論努力レベルも設定可能） |
| `/personality` | コミュニケーションスタイルの調整 |
| `/plan` | プランモードに切り替え（実行前に計画を提案） |
| `/experimental` | 実験的機能の有効化（マルチエージェント等） |
| `/permissions` | 承認ポリシーの変更 |

### 11.2 ナビゲーション・スレッド

| コマンド | 説明 |
|---------|------|
| `/fork` | 現在の会話を新しいスレッドにクローン |
| `/new` | 新しい会話を開始（同じ CLI セッション内） |
| `/resume` | 保存済みセッションのトランスクリプトを再読み込み |
| `/agent` | スポーンされたサブエージェントスレッド間の切り替え |

### 11.3 レビュー・分析

| コマンド | 説明 |
|---------|------|
| `/review` | ワーキングツリーの評価（動作変更とテストに焦点） |
| `/diff` | Git 変更の表示（未追跡ファイルを含む） |
| `/compact` | 可視会話を要約してトークンを解放 |
| `/status` | セッション設定とトークン使用量を表示 |

### 11.4 ユーティリティ

| コマンド | 説明 |
|---------|------|
| `/mention` | 特定ファイルを会話に添付 |
| `/mcp` | 設定済み MCP ツールの一覧 |
| `/apps` | コネクターの参照・挿入 |
| `/init` | `AGENTS.md` スキャフォールドの生成 |
| `/copy` | 最新の応答をクリップボードにコピー |
| `/ps` | バックグラウンドターミナルの状態と最近の出力 |
| `/debug-config` | 設定レイヤーと診断情報の表示 |
| `/statusline` | フッターステータスラインのカスタマイズ |
| `/feedback` | メンテナーに診断情報を送信 |
| `/clear` | ターミナルリセット＋新規チャット |
| `/logout` | ローカル認証情報のクリア |
| `/quit`, `/exit` | CLI を終了 |

---

## 12. Codex Cloud

### 12.1 概要

`codex cloud` コマンドでクラウドタスクをターミナルから管理。引数なしでインタラクティブピッカーが開く。

### 12.2 タスクの実行

```bash
# クラウドでタスクを実行
codex cloud exec --env ENV_ID "バグを修正して"

# Best-of-N（複数試行）
codex cloud exec --env ENV_ID --attempts 3 "最適化案を提案して"
```

### 12.3 結果の適用

```bash
# クラウドタスクの diff をローカルに適用
codex apply <TASK_ID>
```

### 12.4 タスク一覧

```bash
# 最近のタスクを確認
codex cloud list --limit 10

# JSON で出力
codex cloud list --json --env ENV_ID
```

---

## 13. プラグインシステム（v0.110.0+）

### 13.1 概要

スキル、MCP エントリ、アプリコネクターを設定またはローカルマーケットプレースから読み込み。

### 13.2 `@plugin` メンション（v0.112.0+）

チャット内で `@plugin_name` のようにプラグインを直接参照し、関連する MCP/アプリ/スキルコンテキストを自動的に含めることが可能。

### 13.3 マルチエージェント（v0.110.0+）

- `/agent` ベースのサブエージェント有効化
- 承認プロンプト対応
- 序数ニックネーム（ordinal nicknames）でのエージェント管理

---

## 14. トラブルシューティング

### 14.1 モデル変更時の警告

```
warning: This session was recorded with model `gpt-5.4` but is resuming with `gpt-5.3-codex`. Consider switching back to `gpt-5.4` as it may affect Codex performance.
```

→ 警告は出るが動作に問題なし。必要に応じてモデル変更可能。

### 14.2 `--last` の競合リスク

複数エージェントを並列実行している場合、`--last` は最新のセッションを参照するため、意図しないセッションを引き継ぐ可能性がある。

→ **解決策**: 明示的にセッションIDを管理する（6.2の方法）

### 14.3 resume 時の Git コンテキスト消失

v0.111.0 以前では resume 時に Git コンテキストとアプリが保持されない問題があった。

→ **解決策**: v0.111.0 以降にアップグレード

### 14.4 認証

```bash
# CI/CD での認証（推奨）
export CODEX_API_KEY=<your-api-key>

# インタラクティブ認証
codex login
```

---

## 15. 比較: 他のCLIツール

| 機能 | Codex CLI | Gemini CLI | Claude Code |
|------|-----------|------------|-------------|
| セッション引き継ぎ | `exec resume` | 未確認 | MCP経由 |
| セッションフォーク | `fork` / `/fork` | - | - |
| JSON出力 | `--json` | - | - |
| Web検索 | `--search` / `--enable web_search_request` | 組み込み | `WebSearch` tool |
| モデル指定 | `-m` | `-m` | `/model` |
| MCP サポート | `codex mcp` + config.toml | 設定ファイル | 設定ファイル |
| ローカル LLM | `--oss` / `--local-provider` | - | - |
| クラウドタスク | `codex cloud` | - | - |
| コードレビュー | `/review` | - | - |
| プロファイル | `--profile` | - | - |

---

## 16. 参考リンク

- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference/)
- [Codex CLI Features](https://developers.openai.com/codex/cli/features/)
- [Codex Non-interactive Mode](https://developers.openai.com/codex/noninteractive/)
- [Codex Models](https://developers.openai.com/codex/models/)
- [Codex Config Reference](https://developers.openai.com/codex/config-reference)
- [Codex Advanced Configuration](https://developers.openai.com/codex/config-advanced/)
- [Codex MCP Integration](https://developers.openai.com/codex/mcp/)
- [Codex Slash Commands](https://developers.openai.com/codex/cli/slash-commands/)
- [Codex Changelog](https://developers.openai.com/codex/changelog/)
- [Codex GitHub Repository](https://github.com/openai/codex)

---

## 17. 一次情報と検証状況

| 情報 | 一次情報源 | 検証方法 | 検証日 |
|------|-----------|---------|--------|
| コマンドオプション | `codex exec --help` / `codex exec resume --help` (v0.112.0) | ローカル実行 | 2026-03-09 |
| モデル一覧 | Web検索（OpenAI公式） | 未実機検証 | 2026-03-09 |
| JSONL出力フォーマット | 実機検証（v0.63.0時点） | ローカル実行 | 2025-11-27 |
| セッション設定引き継ぎ | 実機検証（v0.63.0時点） | ローカル実行 | 2025-12-02 |
| 新コマンド（fork/cloud/apply） | Web検索 | 未実機検証 | 2026-03-09 |
| MCP サポート | Web検索 | 未実機検証 | 2026-03-09 |
| プラグインシステム | Web検索 | 未実機検証 | 2026-03-09 |
| スラッシュコマンド | Web検索 | 未実機検証 | 2026-03-09 |

> **注意**: 「未実機検証」の項目はWeb検索結果に基づく。バージョンアップにより仕様が変更されている可能性がある。
> 実機で検証する場合は `codex exec --help`, `codex exec resume --help` 等で最新仕様を確認すること。

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2025-11-27 | 初版作成（v0.63.0 対象）|
| 2025-12-02 | `--json` オプションの制約を追記（resume時に引き継がれない問題）|
| 2026-03-09 | v0.112.0 対応に全面更新。モデル一覧更新（gpt-5.4 推奨）、新コマンド追加（fork/cloud/apply/mcp/features）、スラッシュコマンド一覧、MCP サポート、プロファイル・OSS 対応、プラグインシステム、サンドボックス改善、item.type の拡張（file_change/mcp_tool_call/web_search/plan_update）、CI/CD パターン追加。`--json` resume 制約の解消を確認・修正（v0.112.0 で resume でも `--json` 使用可能）。一次情報セクション追加 |
