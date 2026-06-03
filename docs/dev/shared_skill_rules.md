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

## verdict 永続化（共通）

すべての workflow スキルは作業完了時に verdict を **artifact `verdict.yaml`（primary）+ 作業報告 Issue comment 末尾の `---VERDICT---` block（fallback）+ stdout（互換）** の 3 経路で残す（Issue #220）。harness は `verdict_path`（exec_script では env `KAJI_VERDICT_PATH`）で保存先 attempt の絶対パスを注入し、解決順は artifact → comment → stdout。verdict 専用コメントは新設せず、既存の作業報告コメント末尾に block を追記するだけでよい。詳細・YAML 例・stdout 段階廃止方針は [skill-authoring.md](skill-authoring.md) § verdict 出力規約 を参照。

## スキル実体

- 実体: `.claude/skills/`
- 互換導線: `.agents/skills/` の symlink

新規スキル追加や改名時は `.claude/skills/` を先に更新し、必要なら `.agents/skills/` に symlink を追加する。

## auto close keyword 回避

### GitHub 仕様（公式）

`provider.type='github'` 配下では、**PR description**（および default branch に
merge された commit message）内の以下パターン（大小区別なし、word boundary 単位
で検出）が auto-close keyword として解釈され、PR が default branch に merge され
た時点で issue が自動 close される（仕様:
[Linking a pull request to an issue using a keyword](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)）。

- `Closes #N` / `Closing #N` / `Close #N` / `Closed #N`
- `Fixes #N` / `Fix #N` / `Fixed #N`
- `Resolves #N` / `Resolve #N` / `Resolved #N`

issue reference の form は `#N` / `owner/repo#N` / issue URL。GitHub の closing
pattern には `Implements` 系は **含まれない** が、kaji workflow では起点で抑止
する追加ルールとして本規約に含める（保守的防御）。

### 共通規約（仕様準拠 / 必須）

skill が生成する **commit body** および **PR description** に対して必須:

- close keyword（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` /
  `Implement(s|ing|ed)?`）の直後に `#` + 数字が連続するテキストを書かない。例文
  を書く必要があるときは:
  - 数字部分を `<N>` placeholder にする（例: `Fix #<N>` / `Closes #<N>`）
  - もしくは keyword と `#` を文字列リテラルとして分離する
    （例: `` `Clos``es #1` ``）
- review item index の参照は `Must Fix item N` / `Must Fix 指摘 N` / `point N` 等
  を使い、`Must Fix #N` / `(Fix #N)` のような close keyword と隣接する `#` 表記を
  避ける
- issue 参照は `local-pNN-N` などの provider 内部 ID を明示する（`#N` 単独表記を
  避ける）

#### placeholder convention 使い分け

| 用途 | 推奨表記 | 例 | 備考 |
|------|---------|-----|------|
| spec / API の literal 例文 | `#<N>` placeholder | `Fix #<N>` / `Closes #<N>` | `<N>` を明示することで close keyword pattern と grep の両方で生 hazard と区別できる |
| review item / 指摘の参照 | `item N` 形式 | `Must Fix item 3` / `指摘 5` | `#` を伴わないので auto-close 対象外 |
| forge 上の issue 参照 | `local-pNN-N` などの provider 内部 ID | `local-p1-7 を close する` | provider 横断で共通の表記。`#N` 単独は避ける |
| 句読点で隣接する場合 | 1 char 以上の空白を keyword と `#` の間に挟まない placeholder 化 | `(Closes #<N>)` ではなく `(closes-pattern: item N)` | 句読点内でも word boundary で match するため隣接配置自体を避ける

### kaji の追加運用ルール（仕様外 / 保守的防御）

GitHub 仕様の対象では **ない** が、kaji workflow では起点で抑止する追加ルール:

- **`Must Fix [N]` / `Fix [N]` 等の角括弧表記も使わない**。GitHub closing pattern
  対象ではないが、review workflow の出力が後で commit / PR description へ転記
  される導線で `[` `]` が `#` に書き換わる事故を避けるため、index 自体を
  `item N` / `指摘 N` 形式で統一する
- **`Implements #N` 系も使わない**。GitHub closing pattern 対象外だが、誤検出 /
  外部ツールの追従に備えて placeholder 化する
- **`kaji issue comment` 本文での `#N` 表記も避ける**。Issue comment 単体では
  issue を close しないが、comment 内容を後で commit / PR description へ転記
  する際の hazard 持ち込みを起点で防ぐ
- **`draft/design/` 配下の設計書は対象外**（GitHub は PR description のみ scan）。
  ただしその内容を commit body / PR description に引用・要約しない（path だけ書く）

### 影響を受ける skill

`/issue-design`（Step 2.6 design self-check 出力）, `/issue-implement`（Step 8.5
pre-handoff review 出力）, `/issue-review-design`, `/issue-review-code`,
`/issue-fix-design`, `/issue-fix-code`, `/i-doc-review`, `/i-doc-fix`, `/pr-fix`,
`/pr-verify`, `/i-pr`, `/i-dev-final-check`, `/i-doc-final-check`

pre-handoff review で起動する subagent（`.claude/agents/kaji-code-reviewer.md`）
の system prompt および出力テンプレートも本規約に準拠する。指摘 index は
`Must Fix item N` / `指摘 N` / `point N` 形式で統一し、`Must Fix #N` / `Fix [N]`
等の close keyword 隣接表記を生成しない。

### push / push 後の検証

- **push 前**: 該当範囲の commit body を grep し hazard pattern が無いことを
  確認する:
  ```bash
  git log <range> --format='%B' | \
    grep -iE '\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*:?\s*#[0-9]'
  ```
  1 件でも match したら commit を amend して placeholder 化してから push する。
  PR description も `kaji pr create` / `gh pr edit` で渡す本文を同様に grep
  する。
- **push 後**: 意図しない close が発生していないか確認する:
  ```bash
  # 開いている issue を一覧（push 前との差分で消えた N を特定）
  gh issue list --repo <owner>/<repo> --state open

  # 該当 N の close 経緯を確認
  gh issue view <N> --repo <owner>/<repo> --comments

  # reopen
  gh issue reopen <N> --repo <owner>/<repo>
  ```
  原因 commit を amend / 追加 commit で hazard 表記を placeholder 化し、
  workflow が再生成しないよう生成元（skill markdown / prompt）にも反映する

skill SKILL.md / docs 配下の例文を追加・変更する際も同規約を適用し、placeholder
形式（`Fix #<N>` / `Closes #<N>` / `Must Fix item N`）に揃える。
