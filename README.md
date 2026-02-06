# Bugfix Agent v5 Orchestrator

AI-driven bug fixing workflow automation tool that coordinates Gemini (analyzer), Codex (reviewer), and Claude (implementer) to automatically fix bugs through a multi-stage state machine.

## Features

- **3つの実行モード**: FULL（全体実行）、SINGLE（単一ステート）、FROM_END（範囲実行）
- **9ステートワークフロー**: INIT → INVESTIGATE → ... → PR_CREATE → COMPLETE
- **AI連携**: Gemini（分析）、Codex（レビュー）、Claude（実装）の役割分担
- **証跡管理**: `test-artifacts/<issue-number>/<YYMMDDhhmm>/<state>/` に自動保存
- **柔軟な制御**: 任意のステートから開始・単一ステートのみ実行可能
- **テスト容易性**: IssueProvider抽象化によりGitHub API依存なしでローカルテスト可能

## Quick Start

### 通常実行（FULL モード）

Issue を最初から最後まで自動修正:

```bash
python3 bugfix_agent_orchestrator.py --issue <issue-url>
```

### 単一ステート実行（SINGLE モード）

INVESTIGATE ステートのみ実行（テスト・デバッグ用）:

```bash
python3 bugfix_agent_orchestrator.py -i <issue-url> --state INVESTIGATE
```

### 範囲実行（FROM_END モード）

IMPLEMENT ステートから COMPLETE まで実行:

```bash
python3 bugfix_agent_orchestrator.py -i <issue-url> --from IMPLEMENT
```

### ステート一覧表示

```bash
python3 bugfix_agent_orchestrator.py --list-states
```

## リアルタイムログ監視

実行中のログを `tail -f` でリアルタイム監視できます。ログファイルは各行の出力時に即座にフラッシュされます。

### ログファイル

| ファイル | 内容 |
|----------|------|
| `stdout.log` | CLI 標準出力（生の JSONL）|
| `stderr.log` | CLI 標準エラー |
| `cli_console.log` | 整形済みコンソール出力（人間可読）|

### 使用例

```bash
# ターミナル1: エージェント実行
python3 bugfix_agent_orchestrator.py -i <issue-url>

# ターミナル2: リアルタイム監視（整形済み出力）
tail -f test-artifacts/<issue-number>/*/INVESTIGATE/cli_console.log

# 全ログを監視
tail -f test-artifacts/<issue-number>/*/*/*/*.log
```

### ログディレクトリ構造

```
test-artifacts/<issue-number>/<YYMMDDhhmm>/<state>/
├── stdout.log          # 生の CLI 出力
├── stderr.log          # エラー出力
└── cli_console.log     # 整形済み出力（推奨）
```

## ステート一覧

| ステート | 担当 | 役割 | アウトプット |
|----------|------|------|-------------|
| `INIT` | Codex | Issue 本文の必須情報確認 | Issue コメント |
| `INVESTIGATE` | Gemini | 再現実行、原因仮説 | Issue 本文追記 + コメント |
| `INVESTIGATE_REVIEW` | Codex | INVESTIGATE 成果物レビュー | Eval コメント |
| `DETAIL_DESIGN` | Gemini | 詳細設計・テストケース一覧 | Issue 本文追記 + コメント |
| `DETAIL_DESIGN_REVIEW` | Codex | DETAIL_DESIGN 成果物レビュー | Eval コメント |
| `IMPLEMENT` | Claude | ブランチ作成、実装、テスト実行 | Issue 本文追記 + コメント |
| `IMPLEMENT_REVIEW` | Codex | 実装結果レビュー（QA統合） | Eval コメント |
| `PR_CREATE` | Claude | gh pr create 実行 | Issue コメント + PR |
| `COMPLETE` | - | 終了状態 | - |

## CLI オプション

| オプション | 短縮形 | 説明 | 必須 |
|------------|--------|------|------|
| `--issue` | `-i` | 対象 Issue URL | ✅ |
| `--state` | `-s` | 単一実行するステート名 | - |
| `--from` | `-f` | 開始ステート名 | - |
| `--tool` | `-t` | ツール指定（codex, gemini, claude） | - |
| `--tool-model` | `-tm` | ツール:モデル指定（例: codex:o4-mini） | - |
| `--list-states` | `-l` | ステート一覧を表示して終了 | - |
| `--help` | `-h` | ヘルプ表示 | - |

**排他制約**: `--state` と `--from` は同時指定不可、`--tool` と `--tool-model` は同時指定不可
**必須制約**: `--list-states` 以外の実行では `--issue` が必須

## アーキテクチャ

### コンポーネント構成

```
bugfix_agent_orchestrator.py
├── Configuration (load_config, get_config_value, get_workdir)
├── Context Utilities (build_context - Path Traversal 対策込み)
├── Tool Wrappers (AIToolProtocol, GeminiTool, CodexTool, ClaudeTool)
├── Error Handling (ToolError, LoopLimitExceeded, check_tool_result)
├── State Machine (State Enum, SessionState)
├── Execution Config (ExecutionMode, ExecutionConfig)
├── State Handlers (handle_init, handle_investigate, ...)
├── Prompt Loader (load_prompt - Markdown テンプレート展開)
├── CLI Parser (parse_args)
└── Orchestrator (run)

config.toml
├── [agent] - workdir, max_loop_count, state_transition_delay
├── [tools] - context_max_chars
├── [tools.gemini/codex/claude] - model, timeout, sandbox 等
├── [logging] - artifacts_base (⚠️ 未実装: 現状読み込まれていません)
└── [github] - comment 設定
```

### 依存性注入パターン

`AgentContext` を通じて AI ツールと IssueProvider を注入し、テスト時は Mock で差し替え可能:

```python
# 本番環境
ctx = create_default_context(issue_url)

# テスト環境（AIツールのみモック）
ctx = create_test_context(
    analyzer_responses=["..."],
    reviewer_responses=["..."],
    implementer_responses=["..."]
)

# テスト環境（MockIssueProvider使用）
from tests.utils.providers import MockIssueProvider

provider = MockIssueProvider(initial_body="# Issue content")
ctx = create_test_context(
    reviewer_responses=["## VERDICT\n- Result: PASS"],
    issue_provider=provider,
)
# テスト後にアサーション
assert provider.comment_count >= 1
assert "PASS" in provider.last_comment
```

## テスト

### ローカル完結テスト（推奨）

GitHub API依存なしでテスト可能:

```bash
python3 -m pytest tests/ -v --no-cov
```

`MockIssueProvider` を使用し、ハンドラがコメントを投稿したかを検証:

```python
def test_handler_posts_comment(mock_issue_provider):
    ctx = create_context_with_provider(mock_issue_provider)
    handle_init(ctx, SessionState())
    assert mock_issue_provider.has_comment_containing("INIT Check Result")
```

### 統合テストスイート

```bash
python3 -m pytest test_bugfix_agent_orchestrator.py -v --no-cov
```

主要なテストカテゴリ:
- **ステートハンドラ**: 登録確認、MockTool 動作、各ハンドラの遷移テスト
- **SessionState 管理**: ループカウンタ、会話ID管理
- **CLI**: parse_args() モードテスト、排他制約・必須制約
- **ツール実装**: GeminiTool/CodexTool/ClaudeTool のコンテキスト構築、タイムアウト
- **Circuit Breaker**: LoopLimitExceeded 例外テスト

### プロンプト検証テスト

```bash
python3 -m pytest test_prompts.py -v
```

- 全ステートのプロンプトファイル存在確認
- テンプレート変数展開テスト

## ドキュメント

| ドキュメント | 内容 |
|--------------|------|
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | アーキテクチャ詳細、プロトコル仕様、ADR |
| [docs/cli-guides/](./docs/cli-guides/) | AI CLI ツールガイド（Claude/Codex/Gemini） |

## Development Status

- ✅ **Phase 0**: Tool Wrapper 実装（完了）- AIToolProtocol、GeminiTool/CodexTool/ClaudeTool 実装
- ✅ **Phase 1**: ステートハンドラ分離（完了）
- ✅ **Phase 1.5**: プロンプト外部化（完了）- 全10ステート分のプロンプトをMarkdown化
- ✅ **Phase 2**: CLI 拡張（完了）- FULL/SINGLE/FROM_END モード対応
- ✅ **Phase 2 Hardening**: エラーハンドリング強化（完了）- config.toml、タイムアウト、FileNotFoundError 対応
- ✅ **Phase 2.5**: CLI 出力ストリーミング（完了）- リアルタイム出力表示、verbose 設定
- 🚧 **Phase 3**: 統合テスト拡充（進行中）- 126テスト通過、4スモークテストスキップ

## License

This project is part of the kamo2 repository.

## Related Issues

- [Issue #184](https://github.com/apokamo/kamo2/issues/184): ステート別アウトプットまとめ
