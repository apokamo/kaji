# dev-agent-orchestra

AI-driven software development workflow orchestrator. Claude Code / Codex / Gemini CLI のスキルをワークフロー YAML に従って実行する。

> **V7 (dao_harness) が現在の正規エントリポイントです。** `legacy/` は V5/V6 の参照用アーカイブであり、サポート対象外です。

## アーキテクチャ概要

3層アーキテクチャでAIエージェントを制御:

```
┌─────────────────────────────────────────────┐
│  ハーネス (dao_harness/)                     │
│  ワークフロー YAML を解釈し CLI を順次呼出  │
├─────────────────────────────────────────────┤
│  スキル (.claude/skills/, .agents/skills/)   │
│  各ステップの実作業プロンプト               │
├─────────────────────────────────────────────┤
│  CLI (Claude Code / Codex / Gemini)          │
│  スキルをロードし PJ コンテキストで実行     │
└─────────────────────────────────────────────┘
```

詳細: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 開発ワークフロー

Issue駆動のTDD開発フロー:

```
/issue-create → /issue-start → /issue-design → /issue-implement → /issue-pr → /issue-close
```

各フェーズにレビューサイクルあり。詳細: [docs/dev/development_workflow.md](docs/dev/development_workflow.md)

## 品質チェック

コミット前に必ず実行:

```bash
source .venv/bin/activate
ruff check dao_harness/ tests/       # Lint
ruff format dao_harness/ tests/      # Format
mypy dao_harness/                    # Type check
pytest                               # Test
```

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | V7 アーキテクチャ詳細 |
| [docs/adr/](docs/adr/) | アーキテクチャ決定記録 |
| [docs/dev/development_workflow.md](docs/dev/development_workflow.md) | 開発ワークフロー |
| [docs/dev/testing-convention.md](docs/dev/testing-convention.md) | テスト規約 (S/M/L) |
| [docs/dev/workflow-authoring.md](docs/dev/workflow-authoring.md) | ワークフロー YAML 定義 |
| [docs/dev/skill-authoring.md](docs/dev/skill-authoring.md) | スキル作成ガイド |
| [docs/cli-guides/](docs/cli-guides/) | CLI ツールガイド (Claude/Codex/Gemini) |

## `legacy/` ディレクトリ

V5/V6 の旧コード・テスト・ドキュメントを参照用に保持。

```
legacy/
├── bugfix_agent/                  # V5/V6 パッケージ
├── bugfix_agent_orchestrator.py   # V5 エントリポイント
├── prompts/                       # V6 プロンプト
├── tests/                         # V5 テスト
├── docs/                          # V5 ドキュメント
├── config.toml                    # V5 設定
└── AGENT.md                       # V5 エージェント指示書
```

## License

MIT
