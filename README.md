# dev-agent-orchestra

AI-driven software development workflow orchestrator.

## Overview

dev-agent-orchestra は、AI エージェント（Claude, Codex, Gemini）を協調させてソフトウェア開発タスクを自動化するオーケストレーターです。

## Features

- **プラガブルワークフロー**: 設計、実装、バグ修正など用途別のワークフロー
- **マルチAIエージェント**: Claude (実装), Codex (レビュー), Gemini (分析) の役割分担
- **VERDICTプロトコル**: 統一された判定形式 (PASS/RETRY/BACK_DESIGN/ABORT)
- **ステートマシン**: 堅牢な状態遷移管理

## Workflows

| Workflow | Description | States |
|----------|-------------|--------|
| `design` | 詳細設計 ↔ レビューループ | DESIGN, DESIGN_REVIEW |
| `implement` | 実装 ↔ レビューループ | IMPLEMENT, IMPLEMENT_REVIEW |
| `bugfix` | フルバグ修正フロー | 9 states |

## Installation

```bash
pip install -e .
```

## Usage

```bash
# 設計ワークフロー
dao design --input requirements.md --output design.md

# 実装ワークフロー
dao implement --input design.md --workdir ./src

# バグ修正ワークフロー
dao bugfix --issue <github-issue-url>
```

## Development

```bash
# 開発環境セットアップ
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# テスト実行
pytest
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for details.

## License

MIT
