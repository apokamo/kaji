# kaji pr ↔ glab mr 互換 contract

> **Status**: Decided (2026-05-09)
> **Resolves**: EPIC `local-pc5090-4` の OQ-2「`kaji pr` MR エイリアスの実装範囲」
> **Related**: 確定事項 #7（MR discussion / PR review comment 差分吸収）

## 目的

`kaji pr` が `provider.type='github'` 配下では `gh pr` を、`provider.type='gitlab'` 配下では `glab mr` を背後に持つ場合に、skill 側 contract を不変に保つための互換 contract を定義する。skill 側に GitHub/GitLab 分岐を持ち込まないことを必須要件とする。

## 決定（OQ-2）

**「skill 互換性に必要な subcommand に限定し、確定事項 #7 と同じ原則で kaji 側の contract に揃える」** を採用する。

選択肢 (a) URL / 番号系のみ / (b) 全 subcommand 対応 / (c) 引数体系吸収検証 のいずれでもなく、調査の上で実用解として (d) を採る。

### 採用根拠

- (a) は不足: skill が `list` / `view` / `comment` / `review` を使用しているため、URL / 番号系だけでは skill が動かない
- (b) は過剰: `glab mr` の `approvers` / `for` / `subscribe` / `todo` 等は skill が使わない。silent passthrough を許すと skill 依存関係が暗黙化する
- (c) は調査済み: 純粋 passthrough できるのは `create` / `merge` のみで、残りは provider 側の contract 統一が必要

## 設計原則

確定事項 #7 と同型の原則を適用する:

1. **skill 側 contract は GitHub 互換 subset を正本** とする
2. GitLab 固有のコマンド名差分（`comment` ↔ `note`、`review --approve/--request-changes` ↔ `approve`/`revoke`）は **provider 内部で吸収** する
3. `--json fields` / `--jq expr` の field 名も **GitHub 命名で揃える**。GitLab field は provider 内部で変換
4. **skill 側に GitHub/GitLab 分岐を入れない**
5. skill が使わない `glab mr` 固有 sub は **silent passthrough せず、明示的な未対応エラー** で失敗させる

## 必須実装対象

### Tier B（既存 passthrough を contract 化）

| kaji pr sub | gh pr | glab mr 実体 | provider 吸収責務 |
|---|---|---|---|
| `create` | `create` | `create` | 出力 URL / 番号を IID で正規化 |
| `merge` | `merge` | `merge` | **`--squash` / `--rebase` flag を kaji 側で拒否**（CLAUDE.md "Merge: --no-ff only" 遵守）。GitLab project 設定への依存性は別節「merge method 保証範囲」で吸収 |
| `view` | `view` | `view --output json` | output shape を GitHub 互換 field に変換 |
| `list` | `list --json F --jq E` | `list -F json` | **`--json fields` / `--jq expr` 引数体系を kaji 側で受けて GitLab 出力を変換**。GitHub field を正本 |
| `comment` | `comment` | **`note`** | **コマンド名差分を provider が吸収**（skill は `kaji pr comment` のまま） |
| `review --approve --body[-file]` | `review` (body 付き) | **`note --message <body>` + `approve` のシーケンス** | body は捨てない（別節「body 取り扱い原則」参照） |
| `review --request-changes --body[-file]` | `review` (body 付き) | **`note --message <body>` + `revoke` のシーケンス** | body は捨てない。未 approve 状態なら revoke は no-op として skip し note のみ実施 |

### body 取り扱い原則（`review --approve` / `--request-changes` 詳細）

既存 `pr-verify` は `kaji pr review [pr_id] --request-changes --body-file -` 等で **本文付きの正式なレビュー** を投稿する。`glab mr approve` / `glab mr revoke` は body 引数を持たないため、provider 側で以下を保証する:

1. `--body` / `--body-file` で渡された text は **必ず GitLab 側で `glab mr note --message` として記録（捨てない）**
2. 順序は **「note 投稿 → approve / revoke」**（note 投稿失敗時に approve だけ通って "本文なしレビュー" になる事故を防ぐ）
3. `--request-changes` 時に **未 approve 状態の MR は revoke を no-op として skip**、note 投稿のみ実施
4. skill 側 contract 上は GitHub と同様に「body 付きの review が成立した」状態を返す（kaji が成功 / 失敗を responsible に判定）

> **設計含意**: GitLab の approval 機構は GitHub の review state（APPROVED / CHANGES_REQUESTED / COMMENTED）と完全には対称ではないが、skill が必要とする「approve したか」「差し戻したか」「本文があるか」の 3 観点は note + approve / revoke の組み合わせで再現できる。状態の細部（例: "CHANGES_REQUESTED" 相当の正規 GitLab state）は確定事項 #7 と同様に provider 内部で吸収する。

### merge method 保証範囲（`merge` 詳細）

CLAUDE.md の `Merge: --no-ff only (squash merge prohibited)` は kaji 全体のルール。`gh pr merge` では `--merge` flag で no-ff merge を強制できるが、`glab mr merge` には GitHub の `--merge` 相当の明示 flag が存在せず、実際の merge method は **GitLab project 設定に依存** する。

#### kaji が保証する範囲

- `kaji pr merge` 経由では `--squash` / `--rebase` を渡せない（GitHub mode と同じ guard を GitLab mode でも実装）
- `glab mr merge` を呼び出す際、`--squash` / `--rebase` flag を **付加しない**（GitLab project の squash_option = "always" 等の設定がない限り、project default に従って merge commit が作られる）

#### GitLab project 設定への依存範囲

GitLab project Settings → Merge requests には以下の設定があり、`kaji pr merge` の挙動に影響する:

- **Merge method**: `Merge commit` / `Merge commit with semi-linear history` / `Fast-forward merge`
- **Squash commits when merging**: `Do not allow` / `Allow` / `Encourage` / `Require`

これらが kaji の運用と矛盾する場合（例: `Fast-forward merge` 強制 / `Squash: Require`）、`kaji pr merge` は失敗または期待外動作する可能性がある。

#### mitigation

- **(ii) docs での前提明記**: `docs/cli-guides/gitlab-mode.md` に必須前提として以下を記載（子 Issue #5 の責務）:
  - **Merge method**: `Merge commit` に設定する
  - **Squash**: `Do not allow` または `Allow` に設定する（`Require` は不可）
- **(iii) 将来の preflight 統合候補として記録**: 将来 ADR-004 系統の preflight 機構（環境前提検証）が整備された際に、`glab api projects/:id` で上記 2 設定を pre-check して矛盾なら fail-fast する処理を統合する候補として本文書に記録する。**本 EPIC では実装しない**（スコープ膨張回避、merge コマンドのレイテンシ悪化回避、ADR-004 整備後の二重実装回避）。

### Tier A（確定事項 #7 で方針確定済み）

| kaji pr sub | gh 実装 | glab 実装 | 参照 |
|---|---|---|---|
| `review-comments` | `gh api .../pulls/<N>/comments` | discussion API | 確定事項 #7 |
| `reviews` | `gh api .../pulls/<N>/reviews` | approval API | 確定事項 #7 |
| `reply-to-comment` | `gh api ...` で reply | discussion thread reply | 確定事項 #7 |

## 実装しない範囲

`glab mr` 固有の以下 sub は **本 EPIC では実装しない**。`kaji pr <sub>` で呼ばれた場合は **明示的な「未対応」エラー** で `EXIT_INVALID_INPUT` 終了させる:

- `approvers` / `checkout` / `diff` / `for` / `issues` / `rebase` / `reopen` / `revoke`（直接呼び出し）/ `subscribe` / `todo` / `update` / `unsubscribe` / `delete`

> **silent passthrough を禁止する理由**: skill 側が将来新しい sub を使い始めたとき、「動くはずが動かない」状態を発生させないため。明示エラーにすることで skill ↔ provider の依存関係を可視化する。

## skill 側への影響

本 contract の採用により、以下 skill は **修正不要**:

- `i-pr` (`kaji pr create`)
- `issue-close` (`kaji pr merge`)
- `pr-fix` / `pr-verify` (`kaji pr list / view / reviews / review-comments / reply-to-comment / comment / review`)

skill 側で唯一残る修正候補は `i-pr/SKILL.md:234` の `gh auth status` 直接記述のみ（子 Issue #5 で対応）。

## 子 Issue への反映

- **子 Issue #2**（`kaji issue` / `kaji pr` passthrough gitlab 対応 + ID 規約）: 本 contract を実装責務として明記する。特に `review --approve/--request-changes` の note + approve/revoke シーケンス、`merge` の `--squash` / `--rebase` 拒否を含む
- **子 Issue #3**（`GitLabProvider.resolve_pr_context` + prompt 注入）: Tier A 部分（特に `reply-to-comment` の provider-local ID 形式設計）と整合させる
- **子 Issue #5**（docs: `gitlab-mode.md` 新設）: 「merge method 保証範囲」節の (ii) docs 前提（Merge method = `Merge commit`、Squash = `Do not allow` または `Allow`）を必須前提として明記する責務を持つ
- **子 Issue #6**（`make test-large-gitlab` + E2E）: Tier B 全 sub と Tier A 全 sub について GitHub provider と GitLab provider のラウンドトリップ等価性をテストする。`review --approve --body` の body 保持、`review --request-changes` の未 approve 時 no-op 挙動、`merge` の squash/rebase 拒否を E2E 検証項目に含む

## 参照

- EPIC: `local-pc5090-4`
- 現状実装: `kaji_harness/cli_main.py:436-723`（`_PR_BUILTIN_SUBCOMMANDS` / `_dispatch_pr_builtin` / `_handle_pr`）
- 確定事項 #7: 本 EPIC 本文 § 確定事項
- skill 一覧: `.claude/skills/{i-pr,issue-close,pr-fix,pr-verify}/SKILL.md`
