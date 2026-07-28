@AGENTS.md

## Claude Code Memory

Claude Code の auto-memory 機能は、このリポジトリでは使用しない。
`~/.claude/settings.json` の `autoMemoryEnabled: false` を維持し、memory file を再作成しない。

## Code Intelligence

Claude Code では Serena を使用しない（MCP に登録しない）。セッション途中の worktree 切替で
active root が再バインドされず、誤った checkout を読み書きしうるため。
コードナビゲーションが必要な場合は公式 Pyright LSP plugin を任意利用する
（診断の正本は従来通り `make check`）。詳細: [docs/guides/git-worktree.md](docs/guides/git-worktree.md)

## Development Skills

スキルは `.claude/skills/` に格納。`/issue-create` から `/issue-close` までのライフサイクルと、
PR 作成後のレビュー収束サイクルを管理する。

| フェーズ | スキル |
|---------|--------|
| 起票 | `/issue-create` |
| workflow 開始前の有人 interview（任意・明示起動） | `/grill-me` |
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
| Starter 追随（Release 後） | `/update-starter` → 別 session `/review-starter-update` → `/release-starter` |

各スキルの役割詳細: [docs/dev/workflow_guide.md](docs/dev/workflow_guide.md)
