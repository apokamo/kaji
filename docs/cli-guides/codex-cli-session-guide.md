# Codex CLI セッション管理ガイド

## 概要

OpenAI Codex CLI (v0.63.0) のセッション管理機能に関する調査結果をまとめた資料。
複数エージェントの並列実行やセッション引き継ぎのベストプラクティスを記載。

**調査日**: 2025-11-27
**対象バージョン**: OpenAI Codex v0.63.0 (research preview)
**公式リファレンス**: https://developers.openai.com/codex/cli/reference/

---

## 1. 基本コマンド構造

### 1.1 新規セッション開始

```bash
codex exec [OPTIONS] [PROMPT]
```

### 1.2 セッション再開

```bash
codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]
```

---

## 2. 利用可能なパラメータ

### 2.1 `codex exec` のオプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--model` | `-m` | 使用モデル | `-m gpt-5.1-codex-mini` |
| `--cd` | `-C` | 作業ディレクトリ | `-C /path/to/project` |
| `--sandbox` | `-s` | サンドボックスポリシー | `-s workspace-write` |
| `--config` | `-c` | 設定オーバーライド | `-c key=value` |
| `--enable` | - | 機能を有効化 | `--enable web_search_request` |
| `--disable` | - | 機能を無効化 | `--disable feature_name` |
| `--json` | - | JSON形式で出力 | セッションID抽出に使用 |
| `--image` | `-i` | 画像添付 | `-i image.png` |
| `--output-last-message` | `-o` | 最終メッセージをファイル出力 | `-o result.txt` |
| `--full-auto` | - | 自動実行プリセット | 承認なしで実行 |
| `--skip-git-repo-check` | - | Gitリポジトリ外での実行許可 | - |

### 2.2 `codex exec resume` のオプション

| オプション | 短縮形 | 説明 | 例 |
|-----------|--------|------|-----|
| `--last` | - | 最新セッションを再開 | `--last` |
| `--config` | `-c` | 設定オーバーライド | `-c model="gpt-5.1-codex-mini"` |
| `--enable` | - | 機能を有効化 | `--enable web_search_request` |
| `--disable` | - | 機能を無効化 | `--disable feature_name` |

#### 注意: `resume` で使用**できない**オプション

- `-m, --model` → 代わりに `-c model="..."` を使用
- `-C, --cd` → セッション開始時に固定される
- `-s, --sandbox` → セッション開始時に固定される
- `--json` → **resume時は使用不可、かつ初回セッションからも引き継がれない**

#### ⚠️ 重要: `--json` オプションの制約（2025-12-02 検証）

`--json` オプションは `codex exec resume` でサポートされておらず、初回セッションで指定しても引き継がれない。

**検証結果**:

| テスト | コマンド | 結果 |
|--------|----------|------|
| 新規 + `--json` | `codex exec --json "..."` | ✅ JSON出力 |
| resume（`--json`なし） | `codex exec resume <id> "..."` | ❌ テキスト出力（引き継がれない） |
| resume + `--json` | `codex exec resume <id> --json "..."` | ❌ CLIエラー |

**エラー例**:
```
error: unexpected argument '--json' found

  tip: to pass '--json' as a value, use '-- --json'

Usage: codex exec resume <SESSION_ID> [PROMPT]
```

**結論**:
- resume時はJSON出力が**不可能**
- プログラム的にCodexを使用する場合は**テキスト出力に統一**することを推奨
- セッションIDは `session id: <uuid>` 行から抽出可能

詳細: [codex-cli-guide.md](./codex-cli-guide.md)

---

## 3. セッションIDの取得方法

### 3.1 標準出力から確認

`codex exec` 実行時にヘッダーとして表示される：

```
OpenAI Codex v0.63.0 (research preview)
--------
workdir: /home/aki/claude/kamo2
model: gpt-5.1-codex-mini
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, $TMPDIR] (network access enabled)
reasoning effort: medium
reasoning summaries: auto
session id: 019ac592-167e-7ac2-94c5-38ffcd86fbd0   ← ここ
--------
```

### 3.2 JSON出力からプログラム的に取得

```bash
SESSION_ID=$(codex exec -m gpt-5.1-codex-mini --json "タスク" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')
```

#### JSON出力の全体例（コマンド実行を含む場合）

実行コマンド：
```bash
codex exec -m gpt-5.1-codex-mini --json "pwdを実行して" 2>&1 | jq '.'
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
    "aggregated_output": "/home/aki/claude/kamo2\n",
    "exit_code": 0,
    "status": "completed"
  }
}
{
  "type": "item.completed",
  "item": {
    "id": "item_2",
    "type": "agent_message",
    "text": "/home/aki/claude/kamo2"
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

#### item.type の種類

| item.type | 説明 | イベント |
|-----------|------|---------|
| `reasoning` | エージェントの推論・思考ステップ | `completed` のみ |
| `command_execution` | シェルコマンドの実行 | `started` → `completed` |
| `agent_message` | ユーザーへの最終回答 | `completed` のみ |

#### command_execution の詳細フィールド

| フィールド | 説明 | 例 |
|-----------|------|-----|
| `command` | 実行されたコマンド | `"/bin/bash -lc pwd"` |
| `aggregated_output` | コマンドの標準出力全体 | `"/home/aki/claude/kamo2\n"` |
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
codex exec -m gpt-5.1-codex-mini --json "質問" 2>&1 | \
  grep '"type":"turn.completed"' | jq '.usage'
```

#### セッションID抽出のワンライナー

```bash
# jq を使用
codex exec -m gpt-5.1-codex-mini --json "タスク" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id'

# jq なしで抽出（sed使用）
codex exec -m gpt-5.1-codex-mini --json "タスク" 2>&1 | \
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
| `model` (`-m`) | `-m gpt-5.1-codex-mini` | **自動引き継ぎ** | `-c model=` で変更可能 |
| `features` (`--enable`) | `--enable web_search_request` | **自動引き継ぎ** | 追加/変更可能 |
| その他 `-c` 設定 | `-c key=value` | **自動引き継ぎ** | 追加/変更可能 |

### 5.2 検証結果

セッション開始時に `--enable web_search_request` を指定した場合、resume時に指定しなくてもWeb検索が有効のまま維持されることを確認済み。

---

## 6. 実践的な使用パターン

### 6.1 単一エージェント（シンプル）

```bash
# セッション開始
codex exec -m gpt-5.1-codex-mini "タスク1を開始"

# 最新セッションを引き継ぎ
codex exec resume --last "続きの作業"
```

### 6.2 複数エージェントの並列実行

```bash
#!/bin/bash

# エージェント1: コードレビュー担当
SESSION_REVIEW=$(codex exec \
  -m gpt-5.1-codex-mini \
  -C /home/aki/project \
  -s workspace-write \
  --json "コードレビューを開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# エージェント2: テスト担当
SESSION_TEST=$(codex exec \
  -m gpt-5.1-codex-mini \
  -C /home/aki/project \
  -s workspace-write \
  --json "テスト作成を開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# エージェント3: ドキュメント担当（Web検索有効）
SESSION_DOCS=$(codex exec \
  -m gpt-5.1-codex-mini \
  -C /home/aki/project \
  -s workspace-write \
  --enable web_search_request \
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
  -m gpt-5.1-codex-mini \
  -C /home/aki/claude/kamo2 \
  -s workspace-write \
  --enable web_search_request \
  --json "プロジェクト分析を開始" 2>&1 | \
  grep '"type":"thread.started"' | jq -r '.thread_id')

# resume時はプロンプトのみ（設定はすべて引き継がれる）
codex exec resume "$SESSION_ID" "依存関係を調査して"
codex exec resume "$SESSION_ID" "セキュリティ脆弱性をチェックして"

# 必要に応じてモデルをアップグレード
codex exec resume "$SESSION_ID" \
  -c model="gpt-5.1-codex-max" \
  "複雑なリファクタリングを提案して"
```

### 6.4 resume時の推奨実装（--json制約対応）

`--json` オプションは resume 時に使用できないため、プログラム的に Codex を使用する場合は以下のパターンを推奨。

```python
def build_codex_args(session_id: str | None, model: str, workdir: str, sandbox: str) -> list[str]:
    """Codex CLI の引数を構築する"""
    if session_id:
        # resume: JSON オプションなし（テキスト出力）
        args = ["codex", "exec", "resume", session_id]
    else:
        # 新規: 全オプション指定（JSON出力可能）
        args = [
            "codex", "exec",
            "-m", model,
            "-C", workdir,
            "-s", sandbox,
            "--enable", "web_search_request",
            "--json",
        ]
    return args
```

**ポイント**:
- 新規セッション: `--json` で構造化出力、セッションIDは `thread.started` イベントから取得
- resume: テキスト出力のみ、セッションIDは stderr の `session id:` 行から取得
- 出力パース処理は新規/resume で分岐が必要

---

## 7. 利用可能なモデル

| モデル | 特徴 | 用途 |
|--------|------|------|
| `gpt-5.1-codex-mini` | 軽量・高速・低コスト | 日常的なタスク |
| `gpt-5.1-codex-max` | 高性能・高コスト | 複雑な分析・生成 |

---

## 8. Web検索機能

### 8.1 有効化方法

```bash
# 推奨: --enable フラグ
codex exec --enable web_search_request "質問"

# 非推奨（deprecatedの警告が出る）
codex exec -c 'tools.web_search=true' "質問"
```

### 8.2 Web検索の出力例

```
🌐 Searched: current USD JPY exchange rate today
```

---

## 9. サンドボックスポリシー

| ポリシー | 説明 |
|---------|------|
| `read-only` | 読み取り専用 |
| `workspace-write` | 作業ディレクトリ + /tmp への書き込み許可 |
| `danger-full-access` | フルアクセス（危険） |

`workspace-write` ではデフォルトでネットワークアクセスが有効：
```
sandbox: workspace-write [workdir, /tmp, $TMPDIR] (network access enabled)
```

---

## 10. トラブルシューティング

### 10.1 モデル変更時の警告

```
warning: This session was recorded with model `gpt-5.1-codex-mini` but is resuming with `gpt-5.1-codex-max`. Consider switching back to `gpt-5.1-codex-mini` as it may affect Codex performance.
```

→ 警告は出るが動作に問題なし。必要に応じてモデル変更可能。

### 10.2 `--last` の競合リスク

複数エージェントを並列実行している場合、`--last` は最新のセッションを参照するため、意図しないセッションを引き継ぐ可能性がある。

→ **解決策**: 明示的にセッションIDを管理する（6.2の方法）

---

## 11. 比較: 他のCLIツール

| 機能 | Codex CLI | Gemini CLI | Claude Code |
|------|-----------|------------|-------------|
| セッション引き継ぎ | `exec resume` | 未確認 | MCP経由 |
| JSON出力 | `--json` | - | - |
| Web検索 | `--enable web_search_request` | 組み込み | `WebSearch` tool |
| モデル指定 | `-m` | `-m` | `/model` |

---

## 12. 参考リンク

- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference/)
- [Codex Config Documentation](https://github.com/openai/codex/blob/main/docs/config.md)
- [Feature Flags](https://github.com/openai/codex/blob/main/docs/config.md#feature-flags)

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2025-11-27 | 初版作成 |
| 2025-12-02 | `--json` オプションの制約を追記（resume時に引き継がれない問題）|
