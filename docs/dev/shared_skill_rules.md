# Shared Skill Rules

workflow 横断で使うスキルの責務境界を定義する。

## `/i-pr` の責務

- worktree / branch 解決
- 未コミット変更の確認
- push
- `kaji pr create`（Phase 2 以降は `gh pr create` 直接呼び出しを禁止し、`kaji` ラッパーを経由する。Skill markdown 内 placeholder は `[issue_id]` / `[issue_ref]` を使用）

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

## GitLab auto-close keyword 回避

`provider.type='gitlab'` 配下で kaji workflow を回す際は、skill が生成する
commit message / MR description / Issue note に GitLab の auto-close keyword
（`Closes #N` / `Fix(es) #N` / `Resolves #N` / `Implements #N` 系、大小区別なし、
部分一致でも有効）を混入させないこと。push / merge 時に当該 GitLab project の
無関係 issue を自動 close する hazard が発生する（仕様: [GitLab Default closing
pattern](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#default-closing-pattern)、
実発生例は [docs/cli-guides/gitlab-mode.md § 5.7](../cli-guides/gitlab-mode.md#57-commit-body-の-fix-n-fix-n-等が無関係-gitlab-issue-を-autoclose-する) 参照）。

### 共通規約（全 skill 共通）

- **commit body / PR(MR) body / `kaji issue comment` 本文に `#N` の生表記を
  書かない**。review item index を参照する場合は `Must Fix item N` /
  `Must Fix 指摘 N` / `point N` 等を使う。`Must Fix [N]` 記法は **使用禁止**
  （`Fix [N]` 部分が auto-close keyword に match する）
- **close keyword + 数字の組合せ禁止**。例文を書く必要があるときは:
  - 数字部分を `<N>` placeholder にする（例: `Fix #<N>` / `Closes #<N>`）
  - もしくは keyword と `#` を文字列リテラルとして分離する
    （例: `` `Clos``es #1` ``）
  - GitLab は code fence 内も scan するため、上記置換と併用すること（fence で
    囲うだけでは不十分）
- **issue 参照は `gl:N` / `local-pNN-N` を明示する**。`#N` 単独表記を避ける
- **`draft/design/` 配下の設計書は対象外**（GitLab は default branch の
  commit / MR / note のみ scan）。ただしその内容を commit body / MR description
  に引用・要約しない（path だけ書く）

### 影響を受ける skill

`/issue-review-design`, `/issue-review-code`, `/issue-fix-design`,
`/issue-fix-code`, `/i-doc-review`, `/i-doc-fix`, `/pr-fix`, `/pr-verify`,
`/i-pr`, `/i-dev-final-check`, `/i-doc-final-check`

### push / push 後の検証

- **push 前**: 該当範囲の commit body を grep し hazard pattern が無いことを
  確認する:
  ```bash
  git log <range> --format='%B' | grep -iE '(clos|fix|resolv|implement)e[sd]?:?\s*#[0-9]'
  ```
  1 件でも match したら commit を amend して placeholder 化してから push
- **push 後**: 意図しない close が発生していないか確認する:
  ```bash
  glab issue list --repo <group>/<project> --state opened
  ```
  消えた issue があれば即 reopen し、原因 commit を特定する

skill SKILL.md / docs 配下の例文を追加・変更する際も同規約を適用し、placeholder
形式（`Fix #<N>` / `Closes #<N>` / `Must Fix item N`）に揃える。
