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
| 重要判断と provenance | `.claude/skills/_shared/critical-decision-checklist.md` | 人間決定・AI 仮定・one-way door の分類と停止条件 |

## 重要判断の伝播（共通）

`grill-me` / `issue-review-ready` / `issue-design` / `issue-review-design` は
[`critical-decision-checklist.md`](../../.claude/skills/_shared/critical-decision-checklist.md)
を単一情報源として使う。人間が指定した source of truth と重要方針を後段へ伝播し、
AI の仮定は provenance で区別する。記述を補えば直る不備は `RETRY`、人間の決定が
存在しない one-way door は `ABORT` とし、AI の fix step へ判断を委ねない。

`grill-me` だけは workflow 外の有人 front-load であり、未決を質問して Issue 本文へ
固定する。後続 3 skill は無人 workflow 内でその決定を検査・伝播し、新たな人間判断を
代行しない。

## verdict 永続化（共通）

すべての workflow スキルは作業完了時に verdict を **artifact `verdict.yaml`（primary）+ 作業報告 Issue comment 末尾の `---VERDICT---` block（fallback）+ stdout（互換）** の 3 経路で残す（Issue #220）。harness は `verdict_path`（exec_script では env `KAJI_VERDICT_PATH`）で保存先 attempt の絶対パスを注入し、解決順は artifact → comment → stdout。verdict 専用コメントは新設せず、既存の作業報告コメント末尾に block を追記するだけでよい。詳細・YAML 例・stdout 段階廃止方針は [skill-authoring.md](skill-authoring.md) § verdict 出力規約 を参照。

## 後方互換（共通）

ADR 008（[`docs/adr/008-no-backward-compat-layer.md`](../adr/008-no-backward-compat-layer.md)）に従い、
kaji は**後方互換レイヤを書かない**。

- 旧フォーマット読み取り・フォールバック・バージョン分岐を skill / harness に実装しない。
  レビュー（人間・agent とも）は互換フォールバックの追加を指摘・要求しない。
- 破壊的変更は CHANGELOG / GitHub Release notes の BREAKING セクションで
  **3 要素**（壊れる契約 / 影響の判定方法 / 適用指針）を明示する。`/release` skill
  Step 3 がこの 3 要素の充足を確認する。
- 曖昧状態で破壊的操作を行わない fail-safe（例: 検出不能時は上書きせず ABORT）は
  後方互換ではなく恒久的な安全設計であり、本ルールの対象外（引き続き推奨）。

## verdict マーカー契約（producer / consumer）

cross-skill 契約（BACK 再入検出など）は SKILL.md の散文ではなく **CLI / harness 層**
に置く（ADR 008 決定 3）。判定コメントの合意点として `kaji issue comment` の verdict
マーカーを用いる。**契約の正本は CLI コード**（`kaji_harness/providers/markers.py` の
`build_kaji_verdict_marker` + 語彙検証）であり、本節はその案内。

- **producer（判定を発行する skill）**: 判定コメント投稿時に
  `--verdict-step <step> --verdict-status <STATUS>` を **無条件付与**する。CLI が
  body 1 行目に `<!-- kaji-verdict: step=<step> status=<STATUS> -->` を決定的に
  埋め込む。`<STATUS>` は当該 skill が `---VERDICT---` で返す status と一致させる。
  「BACK のときだけ付ける」条件付き出力は**禁止**（付け忘れが silent に契約を壊すため。
  本マーカー導入の契機となったバグの発生機序そのもの）。
  - 現行 producer: `issue-review-code` / `i-dev-final-check` / `issue-implement` /
    `issue-review-design` / `issue-design`
- **consumer（`issue-design` Step 1.6 / 1.7）**: このマーカーのみを参照する。body
  1 行目を厳密照合し（`test()` の `^`、`m` フラグなし）、design を戻し先とする status
  集合 `{BACK, BACK_DESIGN}` を design 再入として数える。旧来の判定見出しゲート・
  regex は残さない（ADR 008 決定 1）。
- **語彙**: `--verdict-step` は `^[a-z][a-z0-9_-]*$`、`--verdict-status` は
  `PASS` / `RETRY` / `ABORT` / `BACK` / `BACK_<UPPER>`（`BACK_[A-Z0-9_]+`、
  [`workflow-authoring.md`](workflow-authoring.md) § `BACK_*` 文法と整合）。不正語彙・
  片方のみのフラグ指定は fail-loud（exit 2）。github / local 両 provider で同一。
- **BREAKING 適用指針**: 下流 repo で判定コメントを投稿する skill をカスタマイズして
  いる場合、producer 側は投稿コマンドへ `--verdict-step/--verdict-status` を付与
  （呼び出し 1 行の差し替え）、consumer 側は `issue-design` Step 1.6 の diff を自版へ移植する。

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

さらに GitHub 仕様上、**PR と issue の「リンク」と merge 時 auto-close は不可分**
である。リンクされた PR が default branch に merge されると linked issue は自動
close される。これはリンクの作成方法（closing keyword / sidebar の手動リンク /
`createLinkedBranch`）に依存しない、リンクそのものの性質である（仕様:
[Linking a pull request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)）。
したがって「Issue 側に linked PR を表示しつつ auto-close は避ける」は、
keyword 表記の工夫では達成できず、後述のリポジトリ設定でのみ達成できる。

### 前提条件: リポジトリ設定で auto-close を無効化していること

本規約は、以下のリポジトリ設定が **無効化されている** ことを前提とする。

| 項目 | 内容 |
|------|------|
| 設定名 | **Auto-close issues with merged linked pull requests** |
| 場所 | Settings → General → Features → Issues |
| 本リポジトリ（apokamo/kaji）の状態 | **無効化済み**（linked PR を merge しても issue は自動 close されない） |
| API からの検証 | **不可**。REST（`gh api repos/<owner>/<repo>`）にも GraphQL にも当該フィールドは公開されておらず、機械的に検証できない |

- この設定を再び有効化すると、`/i-pr` が生成する PR の merge 時に issue が
  意図せず close される。kaji は `/issue-close` による明示 close を前提とする
  運用のため、**設定を変更しないこと**。
- skill 群を **他リポジトリで流用する場合**、同設定の無効化が前提となる。
  無効化できない環境では PR description の live closing keyword を使わない運用
  （linked PR アイコンを諦める）に切り替える必要がある。

### PR description の closing keyword（`/i-pr` のみ許可 / 必須）

- **`/i-pr` が生成する PR description には live closing keyword（`Closes` +
  `#` + issue 番号）を 1 行含める**（テンプレート上の表記は `Closes [issue_ref]`）。
  これは PR↔Issue の紐付け（Issue 側 sidebar / 一覧での linked PR 表示）を
  自動化できる唯一の手段であるため（既存 PR を issue に手動リンクする public API
  は存在しない）。
- 上記 1 行を **例外**とし、PR description のそれ以外の箇所（Summary / Changes /
  Test Plan 本文など）では、後述の共通規約どおり close keyword + `#N` 表記を
  書かない。
- **commit body 側（merge commit message を含む）の回避規約は維持する**（次節）。
  GitHub の auto-close には linked PR 経由と commit message 経由の 2 経路があり、
  当該リポジトリ設定が抑止するのは前者のみ。commit message 経由（commit が default
  branch に到達した時点で close される経路）をカバーする保証はないため、回避規約を
  残す。

### 共通規約（仕様準拠 / 必須）

skill が生成する **commit body**、および **PR description**（`/i-pr` の
`Closes [issue_ref]` 行を除く）に対して必須:

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
  PR description も `kaji pr create` / `gh pr edit` で渡す本文を同様に grep し、
  match が **`/i-pr` テンプレートの `Closes <issue_ref>` 行 1 件のみ**であること
  を確認する（それ以外の match は placeholder 化する）。
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
