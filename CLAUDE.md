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

## Claude Code Memory

Claude Code の auto-memory 機能は、このリポジトリでは使用しない。
`~/.claude/settings.json` の `autoMemoryEnabled: false` を維持し、memory file を再作成しない。

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

## Git

- **Forge**: GitHub 単独。Issue / PR 操作は `kaji issue` / `kaji pr`（内部で `gh` CLI へ委譲）を使う。`gh` 直叩きも可
- **GitHub auto-close**: PR description / merge 後の commit message に `Closes #N` 等を記載すると merge 時に Issue が自動 close される
- **Branches / main 直コミット**:
  - **Feature / 実装作業**（`kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` 等のコード変更）は **必ず feature branch (worktree) → `--no-ff` merge**。main 直コミット禁止
  - 以下は **main 直コミット許容**:
    - `chore(local)`: kaji local Issue ファイル (`.kaji/issues/`) の追加・更新（`kaji issue create/edit/comment/close` の永続化）
    - `docs(...)`: `docs/` / `draft/` 配下の設計文書 / lab note 等、コードを伴わない文書変更
    - `chore`: 設定ファイル (`.gitignore` / `.github/labels.yml` 等) の minor 修正（コードビルドに影響しない範囲）
- **Commits**: Conventional Commits (feat/fix/docs/test/refactor/chore)
- **Merge**: `--no-ff` only (squash merge prohibited)
- **Before commit**: Run pre-commit checks（コード変更を含むコミット時。markdown / 設計文書のみのコミットは省略可）

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
1. Never commit **code changes** to main directly（`kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` 等。例外として markdown / docs / `.kaji/issues/` のみのコミットは main 直可。詳細は § Git）
2. Never trust user input without validation
3. Never hardcode secrets
4. Never skip pre-commit checks **for commits that include code changes**（markdown / 設計文書のみのコミットは省略可）

## Documentation

| Topic | Location |
|-------|----------|
| Document Index | docs/README.md |
| Architecture | docs/ARCHITECTURE.md |
| ADR | docs/adr/ |
| CLI Guides | docs/cli-guides/ |
| Interactive Terminal Runner | docs/cli-guides/interactive-terminal-runner.md |
| Workflow Overview | docs/dev/workflow_overview.md |
| Workflow Guide | docs/dev/workflow_guide.md |
| Development Workflow | docs/dev/development_workflow.md |
| Docs Maintenance Workflow | docs/dev/docs_maintenance_workflow.md |
| Completion Criteria | docs/dev/workflow_completion_criteria.md |
| Testing Convention | docs/dev/testing-convention.md |
| Testing Size Guide | docs/reference/testing-size-guide.md |
| Doc Update Criteria | docs/dev/documentation_update_criteria.md |
| Shared Skill Rules | docs/dev/shared_skill_rules.md |
| GitHub Labels | docs/dev/labels.md |
| Label Definitions (declarative) | .github/labels.yml |
| Workflow Authoring | docs/dev/workflow-authoring.md |
| Skill Authoring | docs/dev/skill-authoring.md |
| AI Strategy | docs/concepts/ai-driven-strategy.md |
| AI Docs Management | docs/concepts/ai-docs-management.md |
| Label Standardization | docs/rfc/github-labels-standardization.md |
| Python Style | docs/reference/python/python-style.md |
| Naming Conventions | docs/reference/python/naming-conventions.md |
| Type Hints | docs/reference/python/type-hints.md |
| Docstring Style | docs/reference/python/docstring-style.md |
| Error Handling | docs/reference/python/error-handling.md |
| Logging (RunLogger) | docs/reference/python/logging.md |
| Release Admin Setup | docs/operations/release/admin-setup.md |
| Release Runbook | docs/operations/release/runbook.md |

## Development Skills

スキルは `.claude/skills/` に格納。`/issue-create` から `/issue-close` までのライフサイクルと、PR 作成後のレビュー収束サイクルを管理する。

| フェーズ | スキル | 役割 |
|---------|--------|------|
| 起票 | `/issue-create` | Issue 作成 + ラベル付与 |
| 着手前ゲート | **`/issue-review-ready`** / **`/issue-fix-ready`** | Issue 本文の品質ゲート（全 workflow 共通） |
| 着手 | `/issue-start` | worktree 作成 + Issue にメタ情報追記 |
| 設計 | `/issue-design` → `/issue-review-design` → (`/issue-fix-design` → `/issue-verify-design`) | 設計書作成と設計レビューサイクル |
| 実装 | `/issue-implement` → `/issue-review-code` → (`/issue-fix-code` → `/issue-verify-code`) | TDD 実装とコードレビューサイクル |
| docs-only | `/i-doc-update` → `/i-doc-review` → (`/i-doc-fix` → `/i-doc-verify`) | ドキュメント修正と整合性レビューサイクル |
| 最終チェック | `/i-dev-final-check` / `/i-doc-final-check` | エビデンス集約 + 品質チェック |
| PR 作成 | `/i-pr` | コミット整理 + プッシュ + PR 作成 |
| PR レビュー後 | **`/pr-fix`** / **`/pr-verify`** | PR レビュー指摘対応とレビュー収束 |
| PR レビューサイクル起動 | **`/review-cycle`** | `.kaji/wf/dev.yaml <id> --from review-poll --before close` を起動し、review → pr-fix → pr-verify ループを 1 コマンドで回す（close は手動。close まで全自動なら `kaji run .kaji/wf/dev.yaml <id> --from review-poll`） |
| 完了 | `/issue-close` | PR マージ + worktree 削除 + ブランチ削除 |
| Release | `/release` | version bump + CHANGELOG + tag + GitHub Release ページ作成（CI 非依存 / maintainer 手元実行） |

詳細: [Workflow Guide](docs/dev/workflow_guide.md) / [Development Workflow](docs/dev/development_workflow.md) / [Docs Maintenance Workflow](docs/dev/docs_maintenance_workflow.md) / [Release Runbook](docs/operations/release/runbook.md)
