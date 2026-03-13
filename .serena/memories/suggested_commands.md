# Suggested commands
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Run tests: `pytest`
- Type check: `mypy kaji_harness/`
- Lint: `ruff check kaji_harness/ tests/`
- Format: `ruff format kaji_harness/ tests/`
- Run workflow: `kaji run workflows/feature-development.yaml <issue-number>`
- Resume workflow: `kaji run workflows/feature-development.yaml <issue-number> --from <step-id>`
- Single step: `kaji run workflows/feature-development.yaml <issue-number> --step <step-id>`
- Basic repo commands: `git status`, `git log --oneline -10`, `rg <pattern>`, `rg --files`, `ls`, `cd`, `find`.