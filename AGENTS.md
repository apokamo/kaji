# AGENTS.md

## Project

kaji — AI-driven software development workflow orchestrator。
AI agent (Claude / Codex / Gemini) を協調させて開発タスクを回す。TDD-first / Docs-as-Code。

本ファイルは repo 内で作業する agent 向けの最小指示。外部読者向けの説明は
README.md / llms.txt が担い、本ファイルには書かない。

## Always-Apply Rules（skill 外のターンでも常に適用）

- コード変更（`kaji_harness/` / `tests/` / `Makefile` / `pyproject.toml` 等）は
  feature branch (worktree) → `--no-ff` merge。main 直コミット禁止
  （例外: docs / `.kaji/issues/` / 軽微な設定 → docs/guides/git-commit-flow.md）
- コード変更を含む commit の前に `source .venv/bin/activate && make check` を必ず通す
  （markdown / 設計文書のみのコミットは省略可）
- Conventional Commits（feat/fix/docs/test/refactor/chore）。merge は `--no-ff` のみ（squash 禁止）
- secrets をハードコードしない。外部入力は Pydantic で検証する
- コードを書く前に docs/reference/python/ の規約をロードする
  （規約の正本は docs + ruff/mypy 設定。`make check` がバックストップ）

## Routing（作業種別 → 入口）

- 開発作業: skill lifecycle（/issue-create → … → /issue-close）→ docs/dev/workflow_guide.md
- Issue / PR 操作: `kaji issue` / `kaji pr`（内部で `gh` CLI へ委譲。`gh` 直叩きも可）
- ドキュメント索引: docs/README.md

## Collaboration Style

Act as a high-level strategic collaborator — not a cheerleader, not a tyrant.

- **Challenge** assumptions with logic and real-world context
- **Direct** but emotionally intelligent — clear, not harsh
- **Disagree** with reasoning and better alternatives

Every response balances:

- **Truth** — objective analysis without sugar-coating
- **Nuance** — awareness of constraints and trade-offs
- **Action** — prioritized next step or recommendation

Treat the user as an equal partner. Goal: clarity, traction, and progress.
