@AGENTS.md

## Claude Code Memory

Claude Code の auto-memory 機能は、このリポジトリでは使用しない。
`~/.claude/settings.json` の `autoMemoryEnabled: false` を維持し、memory file を再作成しない。

## Development Skills

スキルは `.claude/skills/` に格納。`/issue-create` から `/issue-close` までのライフサイクルと、
PR 作成後のレビュー収束サイクルを管理する。

| フェーズ | スキル |
|---------|--------|
| 起票 | `/issue-create` |
| 着手前ゲート | `/issue-review-ready` → (`/issue-fix-ready`) |
| 着手 | `/issue-start` |
| 設計 | `/issue-design` → `/issue-review-design` → (`/issue-fix-design` → `/issue-verify-design`) |
| 実装 | `/issue-implement` → `/issue-review-code` → (`/issue-fix-code` → `/issue-verify-code`) |
| docs-only | `/i-doc-update` → `/i-doc-review` → (`/i-doc-fix` → `/i-doc-verify`) |
| 最終チェック | `/i-dev-final-check` / `/i-doc-final-check` |
| PR 作成 | `/i-pr` |
| PR レビュー後 | `/pr-fix` / `/pr-verify` / `/review-cycle` |
| 完了 | `/issue-close` |
| インシデント調査（第2層・手動起動） | `/incident-cycle`（内部: `incident-investigate` → `incident-review` → (`incident-fix` → `incident-verify`) → `incident-report`） |
| Release | `/release` |

各スキルの役割詳細: [docs/dev/workflow_guide.md](docs/dev/workflow_guide.md)
