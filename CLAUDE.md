# CLAUDE.md

## AI Collaboration Style
Act as a high-level strategic collaborator — not a cheerleader, not a tyrant.
- **Challenge** assumptions with logic and real-world context
- **Direct** but emotionally intelligent — clear, not harsh
- **Disagree** with reasoning and better alternatives

Every response balances:
- **Truth** — objective analysis without sugar-coating
- **Nuance** — awareness of constraints and trade-offs
- **Action** — prioritized next step or recommendation

Treat the user as an equal partner. Goal: clarity, traction, and progress.

## Project Overview
**dev-agent-orchestra** - AI-driven software development workflow orchestrator
- **Purpose**: Coordinate AI agents (Claude, Codex, Gemini) for development tasks
- **Philosophy**: TDD-first, Docs-as-Code
- **Workflows**: design, implement, bugfix

## ⚠️ Pre-Commit (REQUIRED)
```bash
source .venv/bin/activate
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/ && pytest
```

## Essential Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Quality checks (run before commit)
ruff check src/ tests/       # Lint
ruff format src/ tests/      # Format
mypy src/                    # Type check
pytest                       # Test

# CLI
dao list              # List workflows
dao design --help     # Design workflow help
```

## Git Worktree

This project uses bare repository + worktree pattern:

```bash
# Create new worktree
cd /home/aki/dev/dev-agent-orchestra
git worktree add <branch-name> -b <branch-name> main

# Switch context
cd ../<branch-name>

# Remove worktree
git worktree remove <branch-name>
```

**Do:** ディレクトリ移動でブランチ切り替え
**Don't:** `git checkout` を使わない

## Git & GitHub
- **GitHub CLI**: `gh` available (PR, Issue, API operations)
- **Branches**: Feature branches via worktree, never commit to main directly
- **Commits**: Conventional Commits (feat/fix/docs/test/refactor)
- **Merge**: `--no-ff` only (squash merge prohibited)
- **Before commit**: Run pre-commit checks

## Git Workflow (git absorb + --no-ff)

作業中は自由にコミット（バックアップ）、PR前に `git absorb` で整理:

```bash
# 1. 作業中（自由にコミット = バックアップ）
git commit -m "feat: 機能追加"
git commit -m "wip: バックアップ"
git commit -m "fix: 修正"

# 2. レビュー指摘対応後（自動でfixup + rebase）
git add .
git absorb --and-rebase

# 3. PR作成
gh pr create ...

# 4. マージ（ブランチ可視化維持）
git switch main
git merge --no-ff feature-branch
git push
```

**Required tool**: [git-absorb](https://github.com/tummychow/git-absorb)
```bash
brew install git-absorb  # macOS
apt install git-absorb   # Ubuntu/Debian
```

**Why this workflow?**
- バックアップ: レビュー前に自由にコミット可能
- 履歴: `git absorb` で自動整理
- 可視化: `--no-ff` でブランチの分岐・合流が git graph で確認可能

## Core Principles

### Code Quality
- **Python**: snake_case, type hints required, Google docstrings
- **Testing**: TDD required, 80% coverage target
- **Tools**: ruff, mypy, pytest

### Validation
- Pydantic for all inputs
- Never trust external input without validation

## Prohibitions
1. Never commit to main directly
2. Never trust user input without validation
3. Never hardcode secrets
4. Never skip pre-commit checks

## Documentation

| Topic | Location |
|-------|----------|
| Architecture | docs/architecture.md |
| ADR | docs/adr/ |
