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
make check
```

等価な個別コマンド: `ruff check kaji_harness/ tests/ && ruff format kaji_harness/ tests/ && mypy kaji_harness/ && pytest`

## Essential Commands

```bash
# Setup
uv sync                               # Install dependencies + create .venv
source .venv/bin/activate

# Quality checks (run before commit)
make check                            # lint → format → typecheck → test
make lint                             # ruff check kaji_harness/ tests/
make format                           # ruff format kaji_harness/ tests/
make typecheck                        # mypy kaji_harness/
make test                             # pytest
make test-small                       # pytest -m small
make test-medium                      # pytest -m medium
make test-large                       # pytest -m large

# Change-type specific verification
make verify-docs                      # Doc link checker
make verify-packaging                 # Isolated uv install + metadata check

# CLI harness
kaji run <workflow.yaml> <issue>                    # Run a workflow
kaji run <workflow.yaml> <issue> --from <step-id>   # Resume from a step
kaji run <workflow.yaml> <issue> --step <step-id>   # Run a single step
kaji run <workflow.yaml> <issue> --workdir <dir>    # Config discovery start dir
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
| Document Index | docs/README.md |
| Architecture | docs/ARCHITECTURE.md |
| ADR | docs/adr/ |
| CLI Guides | docs/cli-guides/ |
| Workflow Overview | docs/dev/workflow_overview.md |
| Workflow Guide | docs/dev/workflow_guide.md |
| Completion Criteria | docs/dev/workflow_completion_criteria.md |
| Testing Convention | docs/dev/testing-convention.md |
| Testing Size Guide | docs/reference/testing-size-guide.md |
| Doc Update Criteria | docs/dev/documentation_update_criteria.md |
| Shared Skill Rules | docs/dev/shared_skill_rules.md |
| Workflow Authoring | docs/dev/workflow-authoring.md |
| Skill Authoring | docs/dev/skill-authoring.md |
| AI Strategy | docs/concepts/ai-driven-strategy.md |
| AI Docs Management | docs/concepts/ai-docs-management.md |
| Label Standardization | docs/rfc/github-labels-standardization.md |

## Development Skills

スキルは `.claude/skills/` に格納。`/issue-create` から `/issue-close` までのライフサイクルを管理。

詳細: [Workflow Guide](docs/dev/workflow_guide.md)
