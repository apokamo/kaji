# kaji project overview
- Purpose: AI-driven software development workflow orchestrator that runs Claude Code / Codex / Gemini CLI skills according to workflow YAML.
- Main package: `kaji_harness/` (current V7 entrypoint). Legacy V5/V6 assets are kept under `legacy/` for reference only.
- High-level structure: `kaji_harness/` core package, `tests/` test suite, `workflows/` workflow YAMLs, `.claude/skills/` skill definitions, `docs/` architecture and development docs, `draft/design/` issue design docs.
- Stack: Python 3.11+, setuptools build, PyYAML runtime dependency.
- CLI entrypoint: `kaji` -> `kaji_harness.cli_main:main`.