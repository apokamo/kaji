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

## GitLab auto close keyword 回避

### GitLab 仕様（公式）

`provider.type='gitlab'` 配下では、**commit message** および **merge request
description** 内の以下パターン（大小区別なし、word boundary 単位で検出。
`Affixed` のような単語内部分一致は対象外だが、space / 句読点で独立 token として
出現すれば文中位置を問わず match する）が auto-close keyword として解釈され、
commit が default branch に push された時点 / MR が merge された時点で issue が
自動 close される（仕様:
[GitLab Closing issues automatically / Default closing pattern](https://docs.gitlab.com/user/project/issues/managing_issues/#default-closing-pattern)、
実発生例は [docs/cli-guides/gitlab-mode.md § 5.7](../cli-guides/gitlab-mode.md#57-commit-mr-description-の-fix-n-等が無関係-gitlab-issue-を-auto-close-する) 参照）。

- `Closes #N` / `Closing #N` / `Close #N`
- `Fixes #N` / `Fixing #N` / `Fix #N`
- `Resolves #N` / `Resolving #N` / `Resolve #N`
- `Implements #N` 系

issue reference の form は `#N` / `group/project#N` / issue URL（GitLab spec の
closing pattern 対象に角括弧 `[N]` 形式は含まれない）。**Issue note / comment は
本 auto-close pattern の対象外**（Issue 上の close は `/close` 等の quick action
という別経路で処理される）。

### 共通規約（仕様準拠 / 必須）

skill が生成する **commit body** および **MR description** に対して必須:

- close keyword（`Clos(e|es|ing)` / `Fix(es|ing)?` / `Resolv(e|es|ing)` /
  `Implement(s|ing)?`）の直後に `#` + 数字が連続するテキストを書かない。例文を
  書く必要があるときは:
  - 数字部分を `<N>` placeholder にする（例: `Fix #<N>` / `Closes #<N>`）
  - もしくは keyword と `#` を文字列リテラルとして分離する
    （例: `` `Clos``es #1` ``）
  - GitLab は code fence 内も scan するため、上記置換と併用すること（fence で
    囲うだけでは不十分）
- review item index の参照は `Must Fix item N` / `Must Fix 指摘 N` / `point N` 等
  を使い、`Must Fix #N` / `(Fix #N)` のような close keyword と隣接する `#` 表記を
  避ける
- issue 参照は `gl:N` / `local-pNN-N` を明示する（`#N` 単独表記を避ける）

#### placeholder convention 使い分け

| 用途 | 推奨表記 | 例 | 備考 |
|------|---------|-----|------|
| spec / API の literal 例文 | `#<N>` placeholder | `Fix #<N>` / `Closes #<N>` | `<N>` を明示することで close keyword pattern と grep の両方で生 hazard と区別できる |
| review item / 指摘の参照 | `item N` 形式 | `Must Fix item 3` / `指摘 5` | `#` を伴わないので auto-close 対象外 |
| forge 上の issue 参照 | `gl:N` / `local-pNN-N` | `gl:42 を close する` | provider 横断で共通の表記。`#N` 単独は GitHub も含めて避ける |
| 句読点で隣接する場合 | 1 char 以上の空白を keyword と `#` の間に挟まない placeholder 化 | `(Closes #<N>)` ではなく `(closes-pattern: item N)` | 句読点内でも word boundary で match するため隣接配置自体を避ける

### kaji の追加運用ルール（仕様外 / 保守的防御）

GitLab 仕様の対象では **ない** が、kaji workflow では起点で抑止する追加ルール:

- **`Must Fix [N]` / `Fix [N]` 等の角括弧表記も使わない**。GitLab spec の closing
  pattern 対象ではないが、review workflow の出力が後で commit / MR description
  へ転記される導線で `[` `]` が `#` に書き換わる事故を避けるため、index 自体を
  `item N` / `指摘 N` 形式で統一する
- **`kaji issue comment` 本文での `#N` 表記も避ける**。Issue note は GitLab spec
  の auto-close 対象外で comment 単体では issue を close しないが、comment 内容
  を後で commit / MR description へ転記する際の hazard 持ち込みを起点で防ぐ
- **`draft/design/` 配下の設計書は対象外**（GitLab は default branch の commit /
  MR description のみ scan）。ただしその内容を commit body / MR description に
  引用・要約しない（path だけ書く）

### GitHub との非対称性

GitHub の closing keyword は `close[sd]?` / `fix(es|ed)?` / `resolve[sd]?` の 3 系統
のみで、`implement(s|ing|ed)?` 系は対象外。また GitHub の closing pattern は
原則 **PR description / squash commit summary が default branch に merge された
時** のみ trigger され、通常の commit message（feature branch への push）では
issue を close しない。GitLab はこれより広く `Implements` 系も含み、`default
branch に push された commit message` でも trigger するため、**GitHub 想定で書か
れた skill / commit body を GitLab project に向けると hazard 表面積が増える**。
kaji の skill は GitHub/GitLab 両対応前提なので、本 § の共通規約（より厳しい
GitLab spec 側）に揃える。

### 影響を受ける skill

`/issue-review-design`, `/issue-review-code`, `/issue-fix-design`,
`/issue-fix-code`, `/i-doc-review`, `/i-doc-fix`, `/pr-fix`, `/pr-verify`,
`/i-pr`, `/i-dev-final-check`, `/i-doc-final-check`

### push / push 後の検証

- **push 前**: 該当範囲の commit body を grep し hazard pattern が無いことを
  確認する。正規表現は GitLab spec の closing pattern（`Close[sd]?` / `Closing` /
  `Fix(es|ed|ing)?` / `Resolve[sd]?` / `Resolving` / `Implement(s|ed|ing)?`）を
  網羅する形にする:
  ```bash
  git log <range> --format='%B' | \
    grep -iE '\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*:?\s*#[0-9]'
  ```
  1 件でも match したら commit を amend して placeholder 化してから push する。
  MR description も `kaji pr create` / `glab mr update` で渡す本文を同様に grep
  する。

  > ⚠️ かつて推奨していた `grep -iE '(clos|fix|resolv|implement)e[sd]?:?\s*#[0-9]'`
  > は `e[sd]?` の `e` が必須なため `Fix #N` / `Closing #N` / `Implementing #N`
  > 等の **本 incident のトリガー pattern を検出できない**。旧 regex を skill /
  > docs に転記しないこと。
- **push 後**: 意図しない close が発生していないか確認する:
  ```bash
  # 開いている issue を一覧（push 前との差分で消えた N を特定）
  glab issue list --repo <group>/<project> --state opened

  # 該当 N の close 経緯を確認（system note に "closed by ..." が残る）
  glab issue view <N> --repo <group>/<project> --comments

  # reopen
  glab issue reopen <N> --repo <group>/<project>
  ```
  原因 commit を amend / 追加 commit で hazard 表記を placeholder 化し、
  workflow が再生成しないよう生成元（skill markdown / prompt）にも反映する

skill SKILL.md / docs 配下の例文を追加・変更する際も同規約を適用し、placeholder
形式（`Fix #<N>` / `Closes #<N>` / `Must Fix item N`）に揃える。
