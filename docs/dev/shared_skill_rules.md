# Shared Skill Rules

workflow 横断で使うスキルの責務境界を定義する。

## `/i-pr` の責務

- worktree / branch 解決
- 未コミット変更の確認
- push
- `gh pr create`

## `/i-pr` が持たない責務

- workflow 固有の完了条件判定
- dev / docs-only の個別ルール判定
- docs 昇格や docs 同梱の妥当性判定
- final-check 実行済みかの代行判断

workflow 固有の最終判定は `i-dev-final-check` または `i-doc-final-check` が持つ。

## レビューサイクルの責務境界

| 責務 | 担当スキル |
|------|-----------|
| 新規指摘 | `issue-review-design`, `issue-review-code`, `i-doc-review` |
| 修正確認のみ（新規指摘不可） | `issue-verify-design`, `issue-verify-code`, `i-doc-verify` |

`fix/verify` 系（`issue-fix-*` / `issue-verify-*` / `pr-fix` / `pr-verify` / `i-doc-fix` / `i-doc-verify`）はレビューサイクルの収束保証のため、新規指摘を行わない原則を共有する。

## 共通参照ドキュメント

| 共通ルール | パス | 用途 |
|-----------|------|------|
| worktree パス解決 | `.claude/skills/_shared/worktree-resolve.md` | Issue 本文 NOTE ブロックから worktree パスを取得 |
| 無関係な問題の報告 | `.claude/skills/_shared/report-unrelated-issues.md` | 作業中に発見した無関係な問題の報告手順 |
| 設計書の昇格 | `.claude/skills/_shared/promote-design.md` | draft 設計書から恒久ドキュメントへの昇格手順 |

## スキル実体

- 実体: `.claude/skills/`
- 互換導線: `.agents/skills/` の symlink

新規スキル追加や改名時は `.claude/skills/` を先に更新し、必要なら `.agents/skills/` に symlink を追加する。
