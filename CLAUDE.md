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
**kaji** - AI-driven software development workflow orchestrator
- **Purpose**: Coordinate AI agents (Claude, Codex, Gemini) for development tasks
- **Philosophy**: TDD-first, Docs-as-Code
- **Workflows**: design, implement, bugfix

## ⚠️ Pre-Commit (REQUIRED)
```bash
source .venv/bin/activate
ruff check kaji_harness/ tests/ && ruff format kaji_harness/ tests/ && mypy kaji_harness/ && pytest
```

## Essential Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Quality checks (run before commit)
ruff check kaji_harness/ tests/       # Lint
ruff format kaji_harness/ tests/      # Format
mypy kaji_harness/                    # Type check
pytest                               # Test

# CLI harness
kaji run <workflow.yaml> <issue>                    # Run a workflow
kaji run <workflow.yaml> <issue> --from <step-id>   # Resume from a step
kaji run <workflow.yaml> <issue> --step <step-id>   # Run a single step
kaji run <workflow.yaml> <issue> --workdir <dir>    # Set agent working directory
kaji run <workflow.yaml> <issue> --quiet            # Suppress agent output

kaji validate <workflow.yaml>...                    # Validate workflow YAML(s)
```

## Git & GitHub

- **GitHub CLI**: `gh` available (PR, Issue, API operations)
- **Branches**: Feature branches via worktree, never commit to main directly
- **Commits**: Conventional Commits (feat/fix/docs/test/refactor)
- **Merge**: `--no-ff` only (squash merge prohibited)
- **Before commit**: Run pre-commit checks

詳細ガイド:
- [Git Worktree ガイド](docs/guides/git-worktree.md) - Bare Repository + Worktree パターン
- [Git コミット戦略](docs/guides/git-commit-flow.md) - git absorb + --no-ff ワークフロー

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
| Architecture | docs/ARCHITECTURE.md |
| ADR | docs/adr/ |
| CLI Guides | docs/cli-guides/ |
| Development Workflow | docs/dev/development_workflow.md |
| Testing Convention | docs/dev/testing-convention.md |
| Workflow Authoring | docs/dev/workflow-authoring.md |
| Skill Authoring | docs/dev/skill-authoring.md |

## Development Skills

スキルは `.claude/skills/` に格納。`/issue-create` から `/issue-close` までのライフサイクルを管理。

詳細: [Development Workflow](docs/dev/development_workflow.md)
