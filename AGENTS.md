# AGENTS.md (Codex CLI)

<!-- Codex: Add "CLAUDE.md" to project_doc_fallback_filenames in ~/.codex/config.toml -->
<!-- Or configure: project_doc_fallback_filenames = ["CLAUDE.md", "AGENTS.md"] -->

See [CLAUDE.md](./CLAUDE.md) for full instructions.

## Quick Reference

### ⚠️ Pre-Commit (REQUIRED)
```bash
source .venv/bin/activate
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/ && pytest
```

### GitHub CLI
`gh` available for PR, Issue, API operations.

### Core Rules
- TDD-first, 80% coverage target
- Python: snake_case, type hints, Google docstrings
- Conventional Commits (feat/fix/docs/test/refactor)
- Never commit to main directly
- Merge: `--no-ff` only (squash merge prohibited)
- Use `git absorb --and-rebase` before PR

### Documentation
Full details: [docs/architecture.md](./docs/architecture.md)
