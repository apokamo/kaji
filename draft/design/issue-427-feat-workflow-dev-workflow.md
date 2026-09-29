# [設計] 小修正向けの軽量 dev workflow（dev-small）を試験導入する

Issue: #427

## 概要

設計判断が済んだ小修正を「方針確認・実装・検証」と「独立レビュー・最終確認」の 2 工程で進める
custom workflow `.kaji/wf/custom/dev/dev-small.yaml` と、新規 skill 2 本
（`issue-small-change-execute` / `issue-small-change-review`）を追加する。標準 `official/dev.yaml`・
docs workflow・既存 skill の標準 dev 向け契約は変えない。

## 背景・目的

### ユースケース

開発者として、期待動作と修正範囲が Issue で確定している小修正を、baseline・`make check` 相当の検証・
独立レビュー・PR review を受けながら、設計書作成・設計レビュー・工程間の読み直しを経ずに PR・close まで
進めたい。標準 dev と軽量経路の選択は差分の行数ではなく、残っている設計判断の大きさで起動者が明示的に行う。

### 現状の問題（Issue 本文 § 目的より）

- 標準 dev の通常成功経路は agent 起動 9 回（review-ready / start / design / review-design / implement /
  review-code / final-check / pr / close）。baseline は `exec_script` skill、review-poll は `exec:` step で
  agent を起動しない。
- YAML から step を削るだけでは成立しない。`issue-review-code` は設計書（Step 1-2）と
  `## Pre-Handoff Review` コメント（Step 1.4 の hard gate、欠落時 `BACK_IMPLEMENT`）を、
  `i-dev-final-check` は設計・設計レビュー報告（Step 2-1）、設計書昇格（Step 6）、設計書添付（Step 7.5）を前提にする。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `official/dev.yaml` から design / review-design / final-check を削る | 標準 dev の置換は Issue のスコープ外。残る `issue-review-code` / `i-dev-final-check` が設計書・PHR を前提にしており成立しない |
| `dev.yaml` を `--from implement` 等で途中起動する | 同上。review-code の PHR hard gate と final-check の設計書前提が残る。`--from` は開始点を変えるだけで前提は消えない |
| 既存 `issue-implement` / `issue-review-code` / `i-dev-final-check` に「設計書なし」分岐を足す | 標準 dev の設計要求・レビュー条件を維持する決定（Issue 完了条件 5）に反し、標準 skill の契約が経路依存になる。Issue は「既存 skill の短縮版・結合版を出発点にしない」と決定済み |
| 先行実装（k-aiagent `c3f22a7`）の skill をそのまま移植 | 先行実装は k-aiagent 固有の `_shared/` 契約（workflow-contract・verdict・review-rubric・verification-matrix・lane-evidence）と lane record に依存する。Issue は外部固有契約の移植をスコープ外とし、kaji 既存機構への置換を決定済み |

## インターフェース

### 入力

#### 起動（CLI。既存 `kaji run` をそのまま使う。CLI 変更なし）

| 入力 | 型・値 | 必須 | 説明 |
|------|--------|------|------|
| workflow path | `.kaji/wf/custom/dev/dev-small.yaml` | 必須 | 起動者が明示選択する。series 自動選択・自動切替はしない |
| issue | GitHub Issue 番号 | 必須 | `issue-create` 済み、`type:*` ラベル 1 件（`type:docs` 以外） |
| `--before <step>` | step id | 任意 | レビュー前停止は `--before review-change` |
| `--from <step>` | step id | 任意 | レビュー前停止からの通常再開は `--from review-change`。適用外 ABORT 後の中途再開には使わない |

provider は `github` 必須（`requires_provider: github`）。`i-pr` と review-poll を含むため。

#### Issue に必要な内容（軽量経路の要件の正本）

- 期待動作・対象・受け入れ条件（`## 完了条件` のチェックボックス推奨）
- 人間の決定事項（Issue 本文、または本文が参照する人間コメント）
- 設計書は要求しない

#### 新規 skill へ注入されるコンテキスト変数（既存 `prompt.py` の注入をそのまま利用）

| 変数 | execute（change / fix-change） | review（review-change / verify-change） |
|------|:---:|:---:|
| `issue_id` / `issue_ref` / `step_id` / `verdict_path` | 使用 | 使用 |
| `worktree_dir` / `branch_name` / `default_branch` / `git_remote` | 使用 | 使用 |
| `cycle_count` / `max_iterations` | 参照可（全 4 step が cycle 所属） | 参照可 |
| `design_path` | **使用しない**（注入はされるが設計書を要求しない） | **使用しない** |
| `previous_verdict` | 注入されない（`resume:` 不使用） | 注入されない |

手動実行は `$ARGUMENTS = <issue_id>`。worktree 未注入時だけ `_shared/worktree-resolve.md` を読む。
`step_id` 未注入時は verdict marker から phase を判定する（execute: `review-change` / `verify-change` の最新
`RETRY` が最新 `fix-change` 報告より新しければ `fix-change`、それ以外は `change`。review: 最新 `fix-change`
`PASS` が最新の review 報告より新しければ `verify-change`、それ以外は `review-change`）。

以降の `[worktree_dir]` は、harness 起動では注入値、手動実行では worktree-resolve で得た絶対パスを指す。
baseline CLI（`kaji_harness.scripts.baseline_precheck`）は cwd から対象を推論せず、`--worktree` か環境変数
`KAJI_WORKTREE_DIR` を必須とする。runner が `KAJI_*` を注入するのは script-like step だけで、agent step と手動実行には
注入されないため、両 skill は `--evaluate` / `--compare` の全呼び出しで `--worktree [worktree_dir]` を明示する。

### 出力

| 出力 | 内容 |
|------|------|
| workflow 遷移 | 下記「workflow 定義」の `on` に従う |
| Issue コメント | 各 step 1 件。1 行目に `kaji issue comment --verdict-step [step_id] --verdict-status <STATUS>` による verdict marker、末尾に `---VERDICT---` block |
| verdict | 作業報告コメント末尾・stdout・`verdict_path` の 3 経路（既存規約） |
| git | execute のみ: 対象 worktree 内の編集と commit（push しない） |
| Issue 本文 | review のみ: `PASS` 時に確認済み通常完了条件を `[x]` へ更新。`### ワークフロー完了後の確認項目` は触らない |
| artifact | 新規必須 artifact なし。既存 `[worktree]/.kaji-artifacts/baseline/baseline.json` を読むのみ |

### workflow 定義（`.kaji/wf/custom/dev/dev-small.yaml`）

ヘッダ: `name: dev-small` / `execution_policy: auto` / `requires_provider: github`。
`description` は冒頭に「series 自動選択対象外の明示 override 専用 variant（試験導入）」を含め、
設計判断済み小修正向けであること、設計書・設計レビュー・Pre-Handoff Review・final-check の独立工程を持たないこと、
baseline・検証・独立レビュー・PR review を維持すること、適用外は ABORT で停止することを書く。

| step | skill / exec | agent / model / effort | on |
|------|--------------|------------------------|----|
| review-ready | `issue-review-ready` | codex / gpt-6-sol / medium | PASS: start, RETRY: fix-ready, ABORT: end |
| fix-ready | `issue-fix-ready` | claude / opus / medium | PASS: review-ready, ABORT: end |
| start | `issue-start` | codex / gpt-5.6-luna / medium | PASS: baseline, ABORT: end |
| baseline | `baseline-precheck`（exec_script。`timeout: 1800`） | なし | PASS: change, ABORT: end |
| change | `issue-small-change-execute` | claude / opus / medium | PASS: review-change, RETRY: change, ABORT: end |
| review-change | `issue-small-change-review` | codex / gpt-6-sol / medium | PASS: pr, RETRY: fix-change, ABORT: end |
| fix-change | `issue-small-change-execute` | claude / opus / medium | PASS: verify-change, ABORT: end |
| verify-change | `issue-small-change-review` | codex / gpt-6-sol / medium | PASS: pr, RETRY: fix-change, ABORT: end |
| pr | `i-pr` | codex / gpt-5.6-luna / medium | PASS: review-poll, RETRY: pr, ABORT: end |
| review-poll | exec `[kaji, pr, review-poll]` | なし | PASS: close, RETRY: pr-fix, BACK_FALLBACK: review, ABORT: end |
| review | `review` | codex / gpt-6-sol / medium | PASS: close, RETRY: pr-fix, ABORT: end |
| pr-fix | `pr-fix` | claude / sonnet / high | PASS: pr-verify, ABORT: end |
| pr-verify | `pr-verify` | codex / gpt-6-sol / medium | PASS: close, RETRY: pr-fix, ABORT: end |
| close | `issue-close` | codex / gpt-5.6-luna / medium | PASS: end, ABORT: end |

| cycle | entry | loop | max_iterations | on_exhaust |
|-------|-------|------|:---:|:---:|
| small-ready | review-ready | [fix-ready, review-ready] | 3 | ABORT |
| small-execute | change | [change] | 3 | ABORT |
| small-change-review | review-change | [fix-change, verify-change] | 3 | ABORT |
| small-publish | pr | [pr] | 3 | ABORT |
| small-pr-review | review-poll | [pr-fix, pr-verify] | 3 | ABORT |

- 通常成功経路（先頭 step から `PASS` だけを辿る）: review-ready → start → baseline → change →
  review-change → pr → review-poll → close。`agent:` を持つ step は 6（baseline・review-poll は agent なし）。
- どの step も `resume:` を持たない。review-change / verify-change は実装 step と別 session で起動する
  （独立レビューの定義「実装した session と同じ context でレビューしない」を YAML 構造で満たす）。
- cycle 名は `official/dev.yaml` の cycle 名（ready-review / design-review / code-review / implementation /
  final-check / pr-create / pr-review）と重ならない。

### skill IF: `issue-small-change-execute`（step: change / fix-change）

責務: 短い方針、baseline scope 評価、実装・テスト・docs 整合、実装者の差分確認、commit 前の必須検証、
commit、変更報告。PR 公開・merge・cleanup は持たない。

**読み込み条件**

| 区分 | 読むもの | 条件 |
|------|----------|------|
| 通常時 | 本 SKILL.md | 常時 |
| 通常時 | Issue 本文・labels（`kaji issue view [issue_id] --json title,body,labels`） | 常時 |
| 通常時 | worktree の状態（`git status --porcelain` / `git log --oneline [default_branch]..HEAD`） | 常時 |
| 通常時 | `--evaluate` / 検証コマンドの出力（成功時は終了状態・集計・JSON） | 常時 |
| 通常時 | 直近の `RETRY` 報告 1 件（`review-change` / `verify-change` の最新 RETRY marker コメント） | fix-change のみ |
| 通常時 | 直近の自 step `RETRY` 報告 1 件 | change の RETRY 再入時のみ |
| 条件付き | `docs/reference/python/*.md` | Python コードを書く場合（AGENTS.md の常時規則） |
| 条件付き | `docs/dev/testing-convention.md` § テストサイズ定義 / § テスト戦略の原則 | テストを追加・変更する場合 |
| 例外時 | `docs/dev/baseline-check.md` | `--evaluate` が `known_failures` / `missing_baseline` / `stale_baseline` 等を返した場合 |
| 例外時 | `.claude/skills/_shared/worktree-resolve.md` | `worktree_dir` 未注入（手動実行） |
| 例外時 | Issue 本文が参照する人間コメント | 本文が参照している、または決定の特定に不確実さがある場合 |
| 例外時 | `docs/dev/workflow_completion_criteria.md` § type 別に追加で確認する項目（escape clause） | bug で実ログを実装前 Red の代替にする場合 |
| 例外時 | `docs/dev/documentation_update_criteria.md` 該当節 | docs 影響の判断に迷う場合 |
| 例外時 | 検証失敗時の関連ログ全文 | 検証が失敗した場合 |
| 例外時 | `.claude/skills/_shared/report-unrelated-issues.md` | 無関係な問題を発見した場合 |
| 読まない | `draft/design/**`、`issue-implement/references/pre-handoff-review.md`、`_shared/implement-by-type/*`、`implement-quickref.md`、`_shared/promote-design.md`、Issue 全コメント履歴の無条件再読 | 常に（設計書・PHR を前提とする資料を経由して省略工程を再必須化しないため） |

**手順（change）**

1. 前提確認: worktree・branch の存在。`type:*` ラベルが 1 件でなければ ABORT。`type:docs` は docs workflow
   を案内して ABORT（type を付け替えない）。working tree の扱いは下記「入場時の working tree 判定」に従う。
2. 適用判定: 次のいずれかを満たさなければ適用外 ABORT。期待動作・対象・受け入れ条件が明確 / 大きな設計判断が
   残らない（公開互換性・権限境界・データ移行・再開処理・状態永続化・merge 条件等の判断を伴わない） /
   影響範囲を説明できる / 既存検証または局所的な回帰テストで確認できる / 通常の revert で戻せる。
   正本は `docs/dev/workflow_guide.md` § dev-small の適用条件とし、skill には検出条件の要約だけを置く。
3. 短い方針（編集前に確定。承認待ちにしない）: 変更内容・維持する不変条件・変更対象 path・確認方法
   （追加/変更するテストと実行する検証コマンド）・docs 影響。
4. baseline scope 評価（毎回 1 回）:
   `python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --evaluate --scope <path1> --scope <path2> ...`
   （変更対象 path ごとに `--scope` を繰り返す）。
   出力の `verdict` が `missing_baseline` / `stale_baseline`、または `baseline_status` が `blocked` / `invalid` なら ABORT。
   `known_failures` で `stop: true`、または `stop: false` でも既知 failure が変更対象と意味的に関連する場合は ABORT
   （`docs/dev/baseline-check.md` の既存停止基準）。`clean` と無関係な `known_failures` は継続し、手順 7 の gate を決める。
5. 実装: type 別の最小規律を守る。bug は修正前に失敗する回帰テストを先に置き修正後の成功を確認する
   （実ログ代替は既存 escape clause の範囲内）。feat は新しい挙動のテストを追加する。refactor は既存テストで
   振る舞い非変更を確認し、Issue に測定方法が決まっていない改善指標が必要なら適用外 ABORT。canonical 外 type は feat と同等。
   docs 更新は本工程で完了させる。
6. 実装者の差分確認: `git status` と `git diff`（未 commit 分を含む）で Issue scope 外の変更・一時ファイル・秘密情報がないこと、
   commit message・報告に auto-close hazard pattern を書かないことを確認する。
7. commit 前の必須検証（成功まで commit しない。検証後に編集したら再検証）:
   - baseline `clean`: `source .venv/bin/activate && make check`
   - baseline `known_failures`: `ruff check kaji_harness/ tests/ experiments/` / `ruff format --check kaji_harness/ tests/ experiments/` /
     `mypy kaji_harness/` の全 PASS と、`python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --compare` の
     `verdict: ok` かつ `regressions: []`。空配列や終了コードだけで成功扱いしない
   - 変更に応じた追加検証（docs を変更したら `make verify-docs` 等）
8. commit: 対象 path を明示して stage し、Issue type に対応する Conventional Commits prefix で commit。
   commit 後に `git status --porcelain` が空であること、HEAD の full SHA を確認する。
9. 報告（1 コメント）→ stdout → `verdict_path`。

**手順（fix-change）の差分**

- 入力は直近の review 系 `RETRY` 報告 1 件の指摘と引き継ぎ状態。指摘ごとに修正するか、根拠を示して反論する。指摘外の改善を混ぜない。
- 「報告 SHA と HEAD の不一致」指摘の場合は、追加 commit の差分を確認し、手順 6〜7 を行って報告に含める。HEAD が確認済みの差分をすでに含み
  working tree が clean なら手順 8 の commit は行わない（空 commit を作らない）。全指摘を反論で対応し差分が生じない場合も同様。
- 引き継いだ dirty path は、下記の判定で受け入れた場合だけ扱う。Issue scope 内の実装変更（例: レビュー前停止中の未 commit 変更）は
  差分確認・検証の上で commit する。レビューの検証が生成・変更した path は、原因（テストや設定が tracked file を書き換える等）を
  修正したうえで、報告で検証由来と特定された path に限り HEAD の内容へ戻す。scope 外・意図を判断できない変更は保全して ABORT。
  どの扱いにしたかを指摘対応表に記録する。
- 修正で大きな設計判断が必要と判明したら適用外 ABORT。検証失敗を解消できない場合も ABORT（`fix-change` は `RETRY` を持たない）。
- 手順 4 の scope 評価は、修正で変更対象 path が増えた場合だけ再実行する。

**入場時の working tree 判定**（change / fix-change 共通。dirty tree を無条件に取り込まない）

| 入場の種類 | 入力報告 | working tree が dirty の場合 |
|------------|----------|------------------------------|
| change 初回（自 step の報告なし） | なし | 不明な変更として保全し ABORT |
| change の RETRY 再入 | 直近の自 step `RETRY` 報告 | 下記 3 条件をすべて満たす場合だけ引き継ぐ |
| fix-change | 直近の review 系（`review-change` / `verify-change`）`RETRY` 報告 | 下記 3 条件をすべて満たす場合だけ引き継ぐ |

引き継ぐ条件: (1) 現在の HEAD full SHA が入力報告の記載 SHA と一致する (2) 現在の `git status --porcelain` の全 path が
入力報告の dirty path 一覧に含まれる (3) 各 path の実際の差分が報告記載の原因（作業途中の実装変更 / レビュー前停止中の未 commit 変更 /
検証による変更 等）と矛盾しない。1 つでも満たさなければ、報告外の変更として restore・stash・commit のいずれもせず保全し ABORT する
（ABORT 報告に該当 path と不一致の内容を記載）。working tree が clean の場合は入力報告の記載 SHA と HEAD の一致だけを確認し、
不一致なら同様に ABORT する。

**報告の内容**: change は方針、fix-change は指摘対応表（指摘 N → 修正 / 反論と根拠 / 引き継いだ dirty path の扱い）。共通で対象 commit
（full SHA）、変更ファイル要約、検証（コマンド・終了状態・pytest 集計・`--evaluate` / `--compare` JSON の要点）、docs 更新、未解決事項、
引き継ぎ状態（報告時の HEAD full SHA と working tree。dirty なら path ごとの原因）。成功時の検証ログ全文は貼らず、失敗時は関連部分を
引用する。同じ説明を他工程・PR へ全文複製しない。

**副作用の境界**: 対象 worktree 内の編集・commit と Issue コメント投稿のみ。既存 commit の書き換え（amend / rebase / reset）は
しない（レビュー済み範囲の祖先関係を保つため）。push・PR 作成・merge・label 変更・worktree 作成/削除・Issue 本文編集・
session-state 編集・他 workflow 起動はしない。

| step | status | 条件 |
|------|--------|------|
| change | PASS | 実装・必須検証・commit・報告が完了し、working tree が clean |
| change | RETRY | 新しい session で解消できる実装・検証・報告の失敗が残る（cycle `small-execute` が上限を管理。dirty path は原因付きで報告） |
| change | ABORT | 適用外、type ラベル不正、baseline 前提違反・停止、引き継ぎ条件を満たさない dirty tree・HEAD 不一致、provider 障害 |
| fix-change | PASS | 全指摘に対応（修正または反論）し、必須検証・報告が完了し（差分があれば commit 済み）、working tree が clean |
| fix-change | ABORT | 適用外、検証失敗を解消できない、入力の RETRY 報告を特定できない、引き継ぎ条件を満たさない dirty tree・HEAD 不一致、scope 外・意図不明の未 commit 変更、provider 障害 |

### skill IF: `issue-small-change-review`（step: review-change / verify-change）

責務: 実 diff と受け入れ条件の確認、自身による品質検証、PR 前提確認、判定、`PASS` 時の完了条件チェック更新。
tracked file は編集しない。修正が必要なら実装側へ戻す（RETRY）。

**読み込み条件**

| 区分 | 読むもの | 条件 |
|------|----------|------|
| 通常時 | 本 SKILL.md、Issue 本文・labels | 常時 |
| 通常時 | 入力報告: 最新の `change` `PASS` 報告 | review-change |
| 通常時 | 入力報告: 最新の `fix-change` `PASS` 報告と、それが対応した直前の最新 `RETRY` 報告 | verify-change |
| 通常時 | `git diff [default_branch]...HEAD` と変更ファイル実体 | review-change |
| 通常時 | `git diff <全体レビュー済み SHA>..HEAD` と変更ファイル実体 | verify-change（SHA は直近の review 系報告に記載。記録なし・非 ancestor なら `[default_branch]...HEAD`） |
| 通常時 | 自身の検証出力（成功時は終了状態・集計・JSON） | 常時 |
| 例外時 | `docs/dev/baseline-check.md` | `known_failures` / baseline 不整合 |
| 例外時 | 失敗した検証の関連ログ全文 | 検証失敗時 |
| 例外時 | `docs/dev/testing-convention.md` / `docs/reference/python/*.md` 該当節 | テストの妥当性・規約逸脱の判断が必要な場合 |
| 例外時 | `docs/dev/shared_skill_rules.md` § auto close keyword 回避 | hazard pattern の判定に迷う場合 |
| 例外時 | `docs/dev/workflow_completion_criteria.md` § 本文にチェックボックスがない場合 | Issue の完了条件がチェックボックス形式でない場合 |
| 例外時 | `.claude/skills/_shared/worktree-resolve.md` | 手動実行 |
| 読まない | 設計書、Pre-Handoff Review 報告、final-check 報告、Issue 全コメント履歴の無条件再読 | 常に |

**手順（review-change）**

1. 前提: type ラベル確認（execute と同じ ABORT 条件）。入力報告（最新 `step=change status=PASS` marker）が無ければ ABORT。
2. 状態確認: HEAD full SHA を記録する。入力報告の SHA と一致しなければ追加 commit の範囲を指摘に含める。
   `git status --porcelain` が空でなければ、path ごとの差分を読んで原因（レビュー前停止中の未 commit 変更 / 原因不明 等）を
   指摘に記載する（ファイルは触らない）。どちらの場合もレビューを打ち切らず、手順 3 の全体レビューを commit 済みの
   `[default_branch]...HEAD` に対して行う（未 commit 変更はレビュー対象外として指摘に残す）。
3. レビュー観点（`[default_branch]...HEAD` の全体に対して行う）: (a) Issue の決定事項・受け入れ条件・不変条件の充足 / (b) Scope 混在（Issue スコープ外・type 責任範囲外・
   無関係なついで修正） / (c) 検証の妥当性（bug の回帰テスト、feat の新挙動テスト、refactor の振る舞い非変更、
   docs/metadata-only の変更固有検証） / (d) docs 整合 / (e) auto-close 規約（commit message・追加/変更ファイル・報告に
   hazard pattern がない） / (f) 適用条件の維持（大きな設計判断が現れていない） / (g) 報告と実物の対応（SHA・検証内容）。
   (b)(e) は標準 dev の Pre-Handoff Review 観点を独立レビューへ集約したもの。
4. 自身の品質検証（HEAD に対して 1 回）: `git diff --name-only [default_branch]...HEAD` の各 path を `--scope` に繰り返し渡して
   `python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --evaluate --scope <path1> --scope <path2> ...`
   を実行し `baseline_status` を得る。`known_failures` で `stop: true` または意味的関連があれば ABORT。
   `clean` は `make check`、`known_failures` は非 pytest gate 全 PASS かつ
   `python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --compare` の `verdict: ok`・`regressions: []`。
   変更に応じた追加検証（docs 変更時の `make verify-docs` 等）。失敗は関連ログを引用して RETRY。
   手順 2 で working tree が dirty だった場合は、結果が HEAD に対応しないため品質検証を実行せず「未実施（dirty tree）」と記録する
   （PASS にはならず、次の verify-change が HEAD で実行する）。検証で tracked file が変化した場合は、変化した path と
   「検証（コマンド名）による変更」という原因を指摘に記載して保全し RETRY。
5. 判定:
   - blocking finding がある → RETRY。指摘は `指摘 N` 形式で file:line・根拠・期待する修正を書く。好み・scope 外改善・
     将来の改善だけでは RETRY にしない（非 blocking 所見として記載可）。
   - 適用外（大きな設計判断・公開互換性・権限境界等が必要と判明） → ABORT。
   - finding なし → `### ワークフロー完了後の確認項目` を除く完了条件を HEAD に対して全件照合（満たせない項目は RETRY、
     軽量経路で満たせない項目は ABORT）→ PR 前提確認（working tree clean、HEAD == 検証した SHA、
     `[default_branch]` より先行する commit が 1 件以上）→ Issue 本文を再取得し確認済み項目だけ `[x]` に更新
     （`kaji issue edit [issue_id] --commit --body-file <file>`。失敗は ABORT）→ PASS。
6. 報告（1 コメント）→ stdout → `verdict_path`。内容: レビュー対象 full SHA、入力報告の参照（step・created_at）、判定、
   指摘または所見、自身の検証（コマンド・終了状態・集計・JSON 要点、または未実施とその理由）、完了条件照合と本文更新結果、
   PR 前提、**全体レビュー済み範囲**（`[default_branch]...<SHA>` の全体レビューを完了した SHA。完了していなければ「未完了」と
   未実施の範囲・理由）、引き継ぎ状態（報告時の HEAD full SHA と working tree。dirty なら path ごとの原因）。

**手順（verify-change）の差分**

- 確認範囲の起点: 直近の review 系報告（review-change / verify-change）に記録された全体レビュー済み SHA を使う。
  記録が無い（初回の全体レビューが未完了）、またはその SHA が HEAD の ancestor でない場合は、手順 3 の全体レビューを
  `[default_branch]...HEAD` に対して review-change と同じ規則（新規指摘可）で行い、完了を報告に記録する。
- 全体レビュー済み SHA がある場合の確認範囲は、直前 RETRY 報告の各指摘の解消（または反論の妥当性）と、
  `git diff <全体レビュー済み SHA>..HEAD` の修正影響（回帰・scope・auto-close 規約）。解消済み指摘を再開しない。新たに RETRY に
  してよいのは、修正差分起因の問題・HEAD での検証失敗・HEAD で未充足の完了条件・報告と実物の不一致・working tree の dirty に
  限る（レビューサイクル収束のため）。収束制約は全体レビューが完了した範囲の後の修正差分にだけ適用する。
- 手順 2・4〜6（状態確認・検証・完了条件照合・本文更新・PR 前提・報告）は review-change と同じ。報告の全体レビュー済み範囲は
  確認を終えた HEAD に更新する。

**副作用の境界**: Issue コメント投稿と、`PASS` 時の Issue 本文チェックボックス更新のみ。tracked file の編集・commit・push・
PR 作成・merge・label 変更・worktree 作成/削除・session-state 編集・他 workflow 起動はしない。人間レビューはこの工程の
検証・判定を代替しない。

| status | 条件（review-change / verify-change 共通） |
|--------|------|
| PASS | `[default_branch]...HEAD` の全体レビューが完了済み、finding なし、working tree clean、HEAD で自身の検証が成功、完了条件を全件確認し本文更新済み、PR 前提を満たす |
| RETRY | 具体的な修正を要する blocking finding（未 commit 変更・SHA 不一致・検証失敗・検証による tracked file の変化・未充足の完了条件を含む） |
| ABORT | 適用外、入力報告の欠落、baseline 停止基準、type ラベル不正、Issue 本文更新・コメント投稿の失敗 |

### 既存 skill の接続と変更（依存確認の結果）

| skill | 軽量経路での依存 | 変更 |
|-------|------------------|------|
| `issue-review-ready` / `issue-fix-ready` / `issue-start` | 設計書・final-check への依存なし | なし |
| `baseline-precheck` | `_has_implementation_commit` は `draft/design/**` を除外して実装 commit を判定。start 直後の clean tree で測定される | なし |
| `i-pr` | 実行ロジックは設計書に依存しない。「いつ使うか」表と「やらないこと」が final-check 系だけを前提に書かれている | 「いつ使うか」に `issue-small-change-review` PASS 後（dev-small）を追加し、「やらないこと」の担当に同 skill を併記 |
| `review`（review-poll の `BACK_FALLBACK` 先） | Step 5 の観点 1/3/4/5 が `draft/design/issue-*.md` を前提とする | 設計書が無く、かつ `kaji issue resolve-verdict [issue_id] --step verify-change` または `--step review-change` が `status: PASS` を返す場合に限り、Issue 本文の決定事項・完了条件を要件の正本として同観点を評価する条件文を追加。description の fallback 対象 workflow に dev-small を併記。該当しない場合の挙動は変えない |
| `pr-fix` / `pr-verify` | 設計書・PHR・final-check への依存なし（grep 確認） | なし |
| `issue-close` | 通常完了条件の `[x]` を前提にしない。`### ワークフロー完了後の確認項目` の `[ ]` だけを follow-up へ移す | なし |
| `issue-implement` / `issue-review-code` / `i-dev-final-check` / `kaji-code-reviewer` | 軽量経路では使わない | なし（標準 dev の設計要求・PHR・レビュー条件を維持） |
| recovery（`NON_RESUMABLE_SKILLS`） | `Step.skill` で判定するため、dev-small の start / pr / close にもそのまま適用 | なし |

### 使用例

```bash
# 起動（起動者が明示選択。標準 dev の自動切替はない）
kaji run .kaji/wf/custom/dev/dev-small.yaml 431

# レビュー前停止 → 人間が差分を確認 → 独立レビュー・最終確認から通常再開
kaji run .kaji/wf/custom/dev/dev-small.yaml 431 --before review-change
kaji run .kaji/wf/custom/dev/dev-small.yaml 431 --from review-change

# 停止中に人間が commit を追加した場合: review-change が HEAD 全体をレビューしたうえで報告 SHA と HEAD の
#   不一致を指摘して RETRY → fix-change が追加差分を確認・検証・報告 → verify-change が確認
# 停止中の変更を未 commit のまま残した場合: review-change は commit 済みの [default_branch]...HEAD を全体レビューし、
#   未 commit の path と原因を報告して RETRY（品質検証は未実施と記録）→ fix-change は報告に載った path だけを
#   引き継いで扱い commit → verify-change は全体レビュー済み SHA 以降の差分を確認し、HEAD で検証する

# 適用外 ABORT 後に初めからやり直す（中途再開・標準 dev への引継ぎはしない）
#   1. ABORT 報告を確認し、再実行するかを人間が判断する
#   2. 残す必要のある作業を確認したうえで、既存 worktree / branch を削除する
#      （docs/guides/git-worktree.md § Worktree の削除）
kaji run .kaji/wf/official/dev.yaml 431                  # 標準 dev で初めから
kaji run .kaji/wf/custom/dev/dev-small.yaml 431          # 原因を解消して dev-small で初めから
```

### エラー・停止時の挙動

| 事象 | 挙動 |
|------|------|
| 適用外（execute / review のいずれかで判明） | ABORT。Issue に停止理由（該当条件と根拠）、人間が決める必要のある事項、完了済み/未完了の作業、branch・worktree path・HEAD full SHA・`git status --porcelain` の要約、関連報告の参照、「再実行は初めから」の案内を残す。worktree・branch・dirty 変更は保全し、session-state 編集・label 変更・他 workflow 起動はしない |
| baseline `blocked` / `invalid` | baseline step が ABORT（既存） |
| baseline artifact 欠落・非 ancestor | execute / review が ABORT |
| 既知 failure と変更対象の重複・意味的関連 | ABORT（既存停止基準） |
| change の検証失敗 | 修正して再検証。解消できず新 session で解消可能なら RETRY（上限 3、超過は runner が ABORT） |
| fix-change の検証失敗 | 解消できなければ ABORT |
| review-change / verify-change の検証失敗 | RETRY → fix-change（`small-change-review` 上限 3、超過は runner が ABORT） |
| review 開始時の未 commit 変更 | 全体レビューは commit 済み差分に対して完了させ、未 commit path と原因を報告して RETRY（品質検証は未実施と記録） |
| review の検証が tracked file を変更 | 変化した path と原因を報告し、保全して RETRY。fix-change が原因を修正し、検証由来と特定された path だけを HEAD の内容へ戻す |
| execute 入場時の報告外 dirty path・HEAD 不一致 | 保全して ABORT（該当 path と不一致内容を報告） |
| 初回の全体レビューが未完了のまま verify-change に到達 | verify-change が `[default_branch]...HEAD` の全体レビューを review-change と同じ規則で行う |
| baseline CLI の対象未指定 | 起こさない。全呼び出しで `--worktree [worktree_dir]` を渡す（未指定時の CLI は `ValueError` で終了する） |
| Issue コメント・本文更新の失敗 | ABORT。verdict は stdout と `verdict_path` に残す（`VerdictNotFound` を避ける） |

## 制約・前提条件

- Python 実行時コード（`kaji_harness/`）・公開 CLI・永続化 schema・`Makefile` / `pyproject.toml` を変更しない。
- 標準 `official/dev.yaml`・docs workflow・`issue-implement` / `issue-review-code` / `i-dev-final-check` の契約を変えない。
- custom workflow は kaji の pytest 回帰対象外（`docs/dev/workflow-authoring.md` § 品質保証の責務境界）。
  `make validate-workflows` は `git ls-files` の tracked YAML だけを検証するため、検証前に新 YAML を stage する。
- validator は custom の「self-RETRY step が cycle loop 末尾に所属する」不変条件を検査しない
  （`tests/workflows/test_self_retry_cycle_membership.py` は official のみ）。dev-small では change / pr を cycle 末尾に置く。
- `session-state.json` の `cycle_counts` は Issue 単位で永続化され、`--from` なしの再実行でもリセットされない。
  exhaust 判定は cycle 所属 step の dispatch 前に行われる（`runner.py`）。dev-small は新しい reset 手段を持たない。
- skill markdown は全件走査される既存テストの制約を受ける: `gh issue|pr|api` の記述禁止（`kaji issue` / `kaji pr` を使う）、
  旧 placeholder 禁止（`tests/test_skill_migration.py` / `tests/test_skill_placeholders.py`）。
- Codex 系 step 用に `.agents/skills/<name>` を `.claude/skills/<name>` への symlink として追加する。
- 先行実装と外部提案の repository は private。設計書・docs へ本文を転記せず、Issue で公開済みの構造と kaji 側の決定だけを扱う。
- 新規 skill と dev-small は repository 固有の試験導入物であり、starter へ公開しない（Issue スコープ外）。

## 変更スコープ

| 種別 | path |
|------|------|
| 新規 | `.kaji/wf/custom/dev/dev-small.yaml` |
| 新規 | `.claude/skills/issue-small-change-execute/SKILL.md` |
| 新規 | `.claude/skills/issue-small-change-review/SKILL.md` |
| 新規 | `.agents/skills/issue-small-change-execute`・`.agents/skills/issue-small-change-review`（symlink） |
| 変更 | `.claude/skills/review/SKILL.md`（Step 5 の条件文、description） |
| 変更 | `.claude/skills/i-pr/SKILL.md`（「いつ使うか」「やらないこと」） |
| 変更 | 「影響ドキュメント」で「あり」とした docs、`CLAUDE.md`、`CHANGELOG.md` |

## 方針

### 通常成功経路のデータフロー

```text
review-ready ─PASS→ start ─PASS→ baseline(exec_script) ─PASS→ change
  change: Issue 要件 → 適用判定 → 方針 → --evaluate → 実装・docs → 差分確認 → 検証 → commit → 報告(step=change PASS)
─PASS→ review-change（別 session）
  review-change: Issue 要件 + 最新 change 報告 + 実 diff → 観点 (a)〜(g) → 自身の検証 → 完了条件照合
                 → PR 前提 → 本文 [x] 更新 → 報告(step=review-change PASS)
─PASS→ pr ─PASS→ review-poll(exec) ─PASS→ close
```

### 修正ループ

```text
review-change ─RETRY→ fix-change ─PASS→ verify-change ─PASS→ pr
                          ↑                   │
                          └──────RETRY────────┘   （small-change-review: 上限 3、超過は ABORT）
change ─RETRY→ change                             （small-execute: 上限 3）
```

報告間の受け渡しは既存 verdict marker（1 行目）で行う。本文の取得は issue-design Step 1.6 と同じ
`kaji issue view [issue_id] --json comments | jq`（1 行目 marker の厳密照合で最新 1 件）を使い、
status だけで足りる判定には `kaji issue resolve-verdict` を使う。新しい artifact・台帳・metadata は追加しない。

各報告の本文には、後続工程が必要とする引き継ぎ状態を既存の報告項目として書く。

| 項目 | 書く工程 | 使う工程 |
|------|----------|----------|
| 報告時の HEAD full SHA | 全工程 | 次工程の SHA 照合（review の不一致指摘、execute の入場判定） |
| working tree の状態（clean、または dirty path と path ごとの原因） | 全工程 | execute の入場時 working tree 判定 |
| 全体レビュー済み範囲（`[default_branch]...<SHA>` の完了 SHA、または未完了と理由） | review-change / verify-change | verify-change の確認範囲の起点 |

### 停止と再開

- `--before review-change` は change の PASS 後、review-change dispatch 直前で止まる（既存の exclusive barrier）。
  停止は Issue 完了やレビュー承認を意味しない。再開は `--from review-change` で独立レビュー・最終確認から行う。
- 人間レビューを理由に `--from pr` で review-change を飛ばす運用と、人間レビュー結果の自動取り込みは対象外
  （統合工程は品質検証・完了条件照合・PR 前提確認も担うため）。
- ABORT 後は中途再開を定義しない。再実行は worktree を含めて初めから（`--from` なし）。dev-small の cycle 名は
  dev.yaml と重ならないため、標準 dev で初めからやり直す場合に dev-small の消費回数は影響しない。dev-small 自体を
  再実行した場合は既存仕様どおり消費回数が持ち越され、exhaust 済み cycle は入口で再び停止する。その解除は既存
  `--reset-cycle` 手順（`docs/dev/workflow_guide.md` § cycle exhaust からの復旧）に従う人間判断とし、本 Issue は追加しない。

### skill 簡素化の方針

- 新規 2 本は既存 skill の短縮版ではなく、上記の責務・読み込み条件・verdict 表から書き起こす。
- 報告雛形は SKILL.md 内の短い箇条書きに留め、`templates/` / `references/` に分割しない
  （毎回読むだけの分割は簡素化とみなさない決定のため）。
- 既存機構の再利用: worktree 解決（`_shared/worktree-resolve.md`、手動時のみ）、baseline（`--evaluate` / `--compare`）、
  verdict 3 経路と marker、`kaji issue edit` による本文更新、`i-pr` / review-poll / `pr-fix` / `pr-verify` / `issue-close`。
- 省略した設計書・Pre-Handoff Review・final-check の代替 artifact や工程別報告を、skill 本文・共有参照・docs・検証の
  いずれからも必須化しない。

### 先行実装（k-aiagent `c3f22a7`）との差分

Issue で決定した 4 差分:

| 観点 | 先行実装 | 本設計 |
|------|----------|--------|
| baseline | 独立 step なし | start の後に `baseline` step。change / review が `--evaluate --scope`、known_failures 時は `--compare` |
| レビュー側の検証 | lane record が有効なら再実行しない | review-change / verify-change が HEAD に対して自分で 1 回実行 |
| 適用外時 | 標準 dev への手動引継ぎを案内 | ABORT のみ。必要なら初めからやり直す |
| 共有参照 | `_shared/` の workflow-contract・verdict・rubric・verification-matrix・lane-evidence に依存 | kaji 既存機構（`kaji issue` / verdict marker / baseline / `make check`）で置換。外部固有契約は移植しない |

4 差分以外の差異と理由:

| 差異 | 理由 |
|------|------|
| 再利用 step の skill を kaji の同等 skill（`issue-review-ready` / `issue-fix-ready` / `issue-start` / `i-pr` / `review` / `pr-fix` / `pr-verify` / `issue-close`）と `kaji pr review-poll` exec に置換 | 差分 4（kaji 既存機構への置換）の適用 |
| 再利用 step の agent / model / effort は `official/dev.yaml` と同一。change / fix-change は先行実装どおり claude / opus / medium、review-change / verify-change は codex / gpt-6-sol / medium | 再利用 step は標準 dev と揃えて事後比較の条件差を新規工程に限定する。新規工程は先行実装の運用値を踏襲 |
| review が known_failures 時に実 diff の path で `--evaluate` を実行 | 差分 1（scope 評価維持）の具体化。設計レビューが無いため、実装者が申告した scope を独立に照合する |
| 報告 SHA と HEAD の不一致を RETRY にする判定を、`--before` 停止中の人間 commit にも適用 | Issue 完了条件「対象 commit と対応しない証跡を成功扱いしない」の具体化（先行実装にも同趣旨の規定あり） |
| `review` fallback skill に設計書なし経路の条件文を追加 | kaji の `review` skill が設計書を前提にしているため（kaji 固有の接続） |
| 先行実装の observation-eval follow-up Issue 作成は持たない | k-aiagent 固有の LLM eval 契約であり、差分 4 に含まれる外部固有契約 |

### 静的評価の方法（完了条件 11）

実装報告（Issue コメント）に次の表を記録する。新しい台帳・計測基盤は作らない。未測定の token・時間・削減率は記載しない。

| 指標 | 測り方 |
|------|--------|
| 通常成功経路の agent 起動数 | 先頭 step から `PASS` だけを辿り、`agent` を持つ step を数える（下記 V3 のスクリプト）。dev.yaml と dev-small を同じ方法で算出 |
| 通常経路で読む skill と参照先の総量 | 固定シナリオ「`type:bug`、Python コード変更あり（回帰テスト追加あり）、baseline `clean`、RETRY なし、手動実行でない」で、各 agent step の SKILL.md、その skill が無条件で読ませる repo 内ファイル、およびシナリオで条件が確定的に成立する条件付き参照（例: Python コードを書く → `docs/reference/python/*.md`、テストを追加する → `docs/dev/testing-convention.md` の該当節、標準 dev では type 別ガイド等）の行数・バイト数（`wc -l -c`、節指定の参照は該当節の範囲）を合計する。両経路に同じ規則を適用する。Issue 本文・diff・コマンド出力など可変入力は数えない。両経路で共通の step（review-ready / start / pr / close）は別行にする |
| 例外時だけの参照量 | 上記と同じ方法で、固定シナリオでは条件が成立しない参照先（baseline 異常時、手動実行時、判断に迷う場合、検証失敗時 等）を別に合計 |
| 必須 artifact 数 | dev: 設計書・`baseline.json`、dev-small: `baseline.json` |
| 必須報告数（通常経路の Issue コメント・本文更新） | 各 skill が通常経路で必ず投稿する Issue コメントと本文更新の数 |
| 引継ぎ数 | 通常成功経路上で agent step から次の agent step へ移る回数 |

skill 単体の行数だけで簡素化を判断しない。起動回数の差を token 削減率とみなさない。実運用の所要時間・token・差し戻しは
Issue の「ワークフロー完了後の確認項目」で別途記録する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 導入範囲 | custom 一本、起動者が明示選択。標準 dev/docs・official・starter は変えない | Issue 本文 § 決定事項 > 構成と責務 1 点目、§ スコープ境界、§ 重要判断「導入範囲」（2026-09-06 ユーザー承認） | path を `.kaji/wf/custom/dev/dev-small.yaml`、description に series 自動選択対象外を明記 |
| 工程構成 | readiness → worktree → baseline → 方針確認・実装・検証 → 独立レビュー・最終確認 → PR → PR review → close | Issue § 決定事項 > 構成と責務 の図、§ 先行実装の扱い（2026-09-29 ユーザー承認） | step id を change / review-change / fix-change / verify-change、同一 skill を別 step id で呼ぶ fix/verify ループ |
| 新規 skill 2 本 | 方針確認・実装・検証と独立レビュー・最終確認 | Issue § 決定事項 > 構成と責務 2 点目 | skill 名は先行実装と同名（下の AI 仮定） |
| 独立レビューの定義 | 実装 session と同じ context でレビューしない。追加条件なし | Issue § 重要判断（2026-09-07 ユーザー対話） | review step に `resume:` を付けない。agent / model の別指定は独立性の要件にしない |
| 要件の正本 | Issue と人間決定。設計書を必須化しない。短い方針は承認待ちにしない | Issue § 決定事項 > 構成と責務 5 点目、§ 重要判断「要件の正本」 | 方針は編集前に確定し変更報告に記録。`design_path` を使わない |
| baseline と scope 評価 | baseline step を残す。実装前に確認した変更対象 path を `--evaluate --scope` へ渡す。設計書・新 scope artifact は不要 | Issue § 決定事項 > 構成と責務 6・13 点目、2026-09-07 レビュー対応のユーザー指示 | start の後に baseline。change が方針の path で評価。review が実 diff の path でも評価（下の AI 仮定） |
| 品質検証 | commit 前に `make check`（known_failures は非 pytest gate + `--compare`、`verdict: ok` かつ `regressions: []`）。レビュー側も一度自分で実行。独自の引用規則なし | Issue § 決定事項 > 構成と責務 12・14 点目、§ 2026-09-07 表「品質検証」 | clean / known_failures の分岐、変更に応じた追加検証、HEAD と検証 SHA の一致確認 |
| Pre-Handoff Review | 軽量版では独立工程・報告を省き、Scope 混在・auto-close 等を独立レビューに集約。実装者の差分確認は残す。標準 dev は変えない | Issue § 決定事項 > 構成と責務 7 点目、§ 2026-09-07 表「Pre-Handoff Review」 | review 観点 (b)(e) として定義。execute 手順 6 に実装者の差分確認 |
| docs 更新の責務 | 実装側で完了、レビュー側は必要なら戻す | Issue § 決定事項 > 構成と責務 8 点目 | review は tracked file を編集せず RETRY |
| 完了条件の更新 | レビュー・最終確認側が確認済み通常項目を `[x]`、事後確認項目は触らない | Issue § 決定事項 > 構成と責務 9 点目、§ 2026-09-07 表「起動回数・完了条件更新」 | PASS 時のみ、本文を再取得して更新。失敗は ABORT |
| 差し戻し | 実装 skill を再利用。再レビューは未解決指摘と修正の影響。好み・scope 外で RETRY しない | Issue § 決定事項 > 構成と責務 10 点目 | verify-change で新たに RETRY できる範囲を列挙 |
| PR・merge の分離 | 実装 skill に PR 公開・merge・cleanup を持たせない。PR review gate と `--no-ff` を維持 | Issue § 決定事項 > 構成と責務 11 点目 | 既存 `i-pr` / review-poll / `issue-close` をそのまま接続 |
| 適用外時 | ABORT のみ。標準 dev への引継ぎ・中途再開は仕様外。必要なら worktree を含めて初めから | Issue § 適用条件、§ 重要判断「適用外時の扱い」（2026-09-07 ユーザー対話） | ABORT 時に残す情報を列挙。やり直し手順は既存 git 操作と `kaji run` の案内だけ |
| レビュー前停止と再開 | `--before` で停止、停止した独立レビュー・最終確認から再開。`--from pr` によるスキップは対象外 | Issue § 設計で具体化する事項 5 点目、§ 2026-09-07 表「レビュー前停止後」 | `--before review-change` / `--from review-change`。停止中の追加 commit は SHA 不一致で RETRY |
| 起動回数 | 通常成功経路の agent 起動 6 回。数え方は PASS 遷移のみ | Issue § 決定事項 > 構成と責務 最終点、§ 2026-09-07 表 | 経路を列挙し、V3 で静的確認 |
| 先行実装との差分 | 構造は先行実装に倣い、差分は 4 点。他の差分は理由を記録 | Issue § 先行実装の扱い（2026-09-29 ユーザー承認） | 「先行実装との差分」表に 4 点以外の差異と理由を記録 |
| skill 名 | `issue-small-change-execute` / `issue-small-change-review` | AI の仮定。先行実装と同名にして差分を増やさない。命名は two-way door。review-design で検査 | frontmatter `name` とディレクトリ名 |
| cycle 名・上限 | small-ready / small-execute / small-change-review / small-publish / small-pr-review、各 3 回・ABORT | AI の仮定。先行実装と同名・同値、dev.yaml と重複しない（Issue 本文レビュー §4 の配慮を採用）。review-design で検査 | cycle 表 |
| agent / model / effort | 再利用 step は dev.yaml と同一、新規工程は先行実装の値 | AI の仮定。Issue は「多数のモデル別 variant」を対象外とするだけで割当を決めていない。YAML 編集で戻せる。review-design で検査 | step 表 |
| review の known_failures 時 scope 照合 | 実 diff の path で `--evaluate` を実行し停止基準を適用 | AI の仮定。scope 評価維持（人間決定）の範囲内の具体化。known_failures 時だけの 1 コマンド。review-design で検査 | review 手順 4 |
| `review` fallback の変更範囲 | 設計書なし かつ dev-small の review PASS marker がある場合だけ Issue 要件で評価 | AI の仮定。Issue「既存 skill を再利用する場合の前提条件更新」「標準 dev の要求は維持」の両立。review-design・review-code で検査 | 条件判定に既存 `kaji issue resolve-verdict` を使う |
| 恒久テストを追加しない | custom YAML・新 skill に pytest を追加せず、一時の変更固有検証で確認 | AI の仮定。custom の pytest 対象外契約（`workflow-authoring.md`）と prose 検査テスト削除の方針（commit `4dfb343`）。Issue は独自 validator 追加を対象外とする。review-design で検査 | テスト戦略 |
| 修正工程への dirty tree の引き継ぎ | 入力報告の HEAD SHA 一致・dirty path が報告記載の範囲内・差分が報告の原因と矛盾しない、の 3 条件を満たす場合だけ引き継ぐ。満たさなければ保全して ABORT | AI の仮定。人間決定「差し戻しは実装 skill を再利用」「docs・修正は実装側で完了」と、適用外・不明変更は保全して停止する方針の範囲内の具体化。設計レビュー指摘 1 を受けて補完。verify-design・review-code で検査 | execute の入場時 working tree 判定、fix-change の扱い（取り込み / 検証由来 path の復元 / ABORT）、報告の引き継ぎ状態 |
| 全体レビューの完了追跡 | review 系報告に全体レビュー済み SHA を記録し、未完了・非 ancestor なら verify-change が全体レビューを行う。収束制約は完了済み範囲の後にだけ適用 | AI の仮定。人間決定「独立レビュー」「再レビューは未解決指摘と修正の影響」の両立。設計レビュー指摘 2 を受けて補完。新規 artifact・承認 gate は追加しない。verify-design・review-code で検査 | review-change は dirty・SHA 不一致でも commit 済み差分の全体レビューを完了させる。execute は既存 commit を書き換えない |
| baseline CLI の対象指定 | `--evaluate` / `--compare` の全呼び出しで `--worktree [worktree_dir]`、複数 scope は `--scope` の繰り返し | 既存契約（`baseline_precheck.py` の `--worktree` / `KAJI_WORKTREE_DIR` 必須、runner は agent step に `KAJI_*` を注入しない）への適合。設計レビュー指摘 3 | 手動・harness 起動とも同じ明示引数で対象へ到達。既存標準 skill の同種問題は #430 として分離 |
| docs の正本位置 | dev-small の選択基準・運用は `docs/dev/workflow_guide.md` § dev-small を正本にする | AI の仮定。custom variant（dev-thorough）の既存の記載位置に合わせる。新規 doc を作らない。review-design で検査 | 影響ドキュメント |
| one-way door | 該当なし | 公開 CLI・永続化 schema・Python 実行時コード・標準 dev 契約を変えず、全変更が revert で戻せる新規 custom 資産と条件付き文書変更に限られるため（critical-decision-checklist の代表軸を確認） | — |

## テスト戦略

### 変更タイプ

workflow 定義（custom YAML）・skill 指示（Markdown）・docs の追加と変更。Python 実行時コード（`kaji_harness/`）・
`Makefile`・`pyproject.toml` の変更はない。runner の遷移・`--before` / `--from` / cycle 上限の意味論は変えない。

### 実行時コード変更の場合

該当しない。ただし workflow の遷移は実行時に意味を持つため、次の分担で確認する。

- runner の意味論（exclusive barrier、`--from` 合成、loop 末尾 RETRY の計数、exhaust 時の `on_exhaust`、
  `--reset-cycle`）は既存テストが保護している: `tests/test_runner_before.py`、`tests/test_cycle_limit.py`、
  `tests/test_runner_reset_cycle.py`、`tests/test_workflow_validator.py`。
- dev-small 固有の遷移グラフは、custom の pytest 対象外契約に従い下記の一時検証で確認する。

#### Small テスト

- 追加しない。純粋ロジックの追加がない。

#### Medium テスト

- 追加しない。custom YAML を読む恒久テストは所有権契約（`docs/dev/workflow-authoring.md` § 品質保証の責務境界）に反する。
  新 skill の文言一致テストは挙動を保証しない（commit `4dfb343` で同種テストを削除済み）。

#### Large テスト

- 追加しない。agent を実起動する E2E は外部 CLI・課金・非決定性を伴い、実運用での試行は Issue の
  「ワークフロー完了後の確認項目」に分離済み。

### 変更固有検証

実装者が実行して実装報告に記録し、レビュー側が主要項目を再実行する。スクリプトは commit しない（独自 validator を増やさない）。

| ID | 検証 | 期待結果 |
|----|------|----------|
| V1 | 新 YAML・skill・symlink を stage 後に `source .venv/bin/activate && make check` | exit 0。`validate-workflows` が dev-small を L1/L2/L3 で検証、既存の skill markdown 全件走査テスト（gh 記述・旧 placeholder）が通る |
| V2 | `make verify-docs` | exit 0（docs リンク整合） |
| V3 | `kaji_harness.workflow.load_workflow` を使う一時スクリプトで dev-small の遷移を検査 | (1) `name == "dev-small"`、`requires_provider == "github"`、description に「series 自動選択対象外」 (2) PASS 経路が review-ready → start → baseline → change → review-change → pr → review-poll → close、`agent` 付き step が 6。同じ関数で `official/dev.yaml` が 9 (3) blocking finding → 修正 → 確認: review-change.RETRY = fix-change、fix-change.PASS = verify-change、verify-change.PASS = pr、verify-change.RETRY = fix-change (4) 検証失敗: change.RETRY = change、fix-change は RETRY を持たない (5) 適用外停止: change / review-change / fix-change / verify-change / baseline の ABORT = end、4 step に `BACK*` がない (6) self-RETRY step（change / pr）が cycle loop 末尾 (7) review-change / verify-change の `resume` が None、change / fix-change と review-change / verify-change の skill 対応 (8) cycle 名が dev.yaml と非重複 (9) レビュー前停止と再開: change.PASS = review-change で、review-change が先頭から到達可能（`--before` / `--from` の対象） |
| V4 | `rg -n 'draft/design|設計書|Pre-Handoff|implement-by-type|promote-design|kaji-code-reviewer|final-check' .claude/skills/issue-small-change-*/` と、両 skill が通常時・例外時に読ませるファイルの確認 | ヒットは「読まない・要求しない」旨の記述だけ。読ませるファイルが設計書・PHR・final-check 報告を要求しない。`review` の変更が dev-small 条件下でだけ設計書を不要にする |
| V5 | `readlink -f .agents/skills/issue-small-change-{execute,review}` | `.claude/skills/` の同名ディレクトリへ解決 |
| V6 | 新 skill の verdict 例を `kaji_harness.verdict.parse_verdict` で各 step の valid status に対して parse | 例外なし |
| V7 | 変更・追加ファイルと commit message に `rg -nP '(Clos(e[sd]?|ing)|Fix(e[sd]|ing)?|Resolv(e[sd]?|ing)|Implement(s|ing|ed)?)\s*:?\s*#[0-9]'` | 0 件 |
| V8 | 「静的評価の方法」の表を算出 | 実装報告に記録。未測定値を含まない |
| V9 | baseline CLI の対象指定: (1) 両 skill の `--evaluate` / `--compare` 呼び出しがすべて `--worktree [worktree_dir]` を含み、複数 scope を `--scope` の繰り返しで渡すことを確認 (2) 実装 worktree 以外の cwd（main checkout）から `env -u KAJI_WORKTREE_DIR .venv/bin/python -m kaji_harness.scripts.baseline_precheck --worktree <実装 worktree の絶対パス> --evaluate --scope kaji_harness --scope tests` を実行（読み取りのみ） | (1) 欠落 0 件 (2) exit 0 で構造化 JSON を返し、`measured_commit` が実装 worktree の `baseline.json` と一致する（`KAJI_WORKTREE_DIR` 未設定でも、手動・harness のどちらの起動形でも同じ対象に到達する）。比較として `--worktree` なしでは `ValueError` になることを記録 |
| V10 | 例外経路のシナリオ確認: 実装した両 SKILL.md と YAML を次のシナリオに当て、各シナリオを処理する SKILL.md の手順（見出し・行）と YAML の遷移を対応表として実装報告に記録する。S1: review の検証が tracked file を変更 → 報告に path・原因・SHA → fix-change が 3 条件で受け入れ、原因修正と検証由来 path の復元 → commit → verify-change が HEAD で検証し PASS 可能 / S2: fix-change 入場時に報告外の dirty path または HEAD 不一致 → 保全して ABORT / S3: レビュー前停止中の未 commit 変更 → review-change が commit 済み差分を全体レビューし品質検証は未実施と記録して RETRY → fix-change が取り込み commit → verify-change は全体レビュー済み SHA 以降を確認 / S4: review 系報告に全体レビュー完了の記録がない → verify-change が `[default_branch]...HEAD` を全体レビュー（収束制約を適用しない） / S5: 全体レビュー済み SHA が HEAD の ancestor でない → S4 と同じ / S6: change 初回入場で dirty → ABORT / S7: review 開始時に working tree が dirty → PASS にならない | 全シナリオに処理箇所があり、元の実装差分が独立レビューを受けないまま PASS する経路、報告外の変更を取り込む経路、dirty tree で PASS する経路がない |

既存の標準 dev skill（`issue-implement` / `issue-review-code` / `issue-fix-code` / `i-dev-final-check`）と
`docs/dev/baseline-check.md` の例にも `--worktree` を渡さない同種の呼び出しがあるが、本 Issue の変更範囲外のため #430 で扱う。

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` の 4 条件）

1. 独自ロジックの追加・変更: Python の追加・変更がない。遷移は宣言的 YAML、skill は agent への指示文。
2. 想定される不具合パターンの捕捉: YAML の構文・参照・skill 解決は `make validate-workflows`（`make check` に含まれる）、
   skill markdown の禁止記述は既存の全件走査テスト、runner の意味論は既存テストが捕捉する。dev-small 固有の遷移は V3 で確認する。
3. 回帰検出情報の増分: custom YAML は利用者所有で pytest 対象外という契約があり、恒久化すると所有権境界を崩す。
   skill 文言の部分一致テストは挙動を保証しない。
4. 説明可能性: 本節と V1〜V10 の実行記録で、検証内容と省略理由をレビューできる。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/dev/workflow_guide.md | あり | custom 表・選択表・provider 表・PR review 軸と baseline の記述に dev-small を追加。新節「dev-small（custom・試験導入）」に位置付け、経路、適用条件と選択基準（設計判断の大きさ）、候補と標準 dev の具体例、起動・レビュー前停止と再開・ABORT とやり直し（cycle 消費回数の持ち越しを含む）、既存 skill との関係、効果評価の扱い（起動回数 ≠ token）を書く。適用条件の正本 |
| docs/dev/workflow_overview.md | あり | 「どの workflow を使うか」に dev-small の行と選択の考え方、workflow_guide への参照 |
| docs/dev/development_workflow.md | あり | § 対象 / § Pre-Handoff Review / § 設計書の扱い が標準 dev（dev / dev-thorough 系 / dev-local）の規定であり、dev-small は持たないことを明記 |
| docs/dev/baseline-check.md | あり | § workflow 上の位置に dev-small（start → baseline → change）、scope の入力（change が編集前に確定した path、review が実 diff の path）、artifact・比較関数の consumer に新 skill を追加。dev-small について書く呼び出し例は `--worktree [worktree_dir]` 付きにする（既存例の修正は #430） |
| docs/dev/workflow_completion_criteria.md | あり | § ステップ別の確認責務と証跡、§ 本文更新のタイミングと実行者に新 skill の行を追加 |
| docs/dev/shared_skill_rules.md | あり | verdict marker の producer 一覧、レビューサイクルの責務境界、`/i-pr` が持たない責務の最終判定担当に新 skill を追加 |
| docs/dev/workflow-authoring.md | あり | § ファイル配置のツリーに `dev-small.yaml` を追加 |
| docs/dev/testing-convention.md | なし | テスト規約・実行マトリクスの規則は変わらない（マトリクスは標準 dev の PR 前を例示しており、dev-small を排除していない） |
| docs/dev/skill-authoring.md / documentation_update_criteria.md | なし | 作成規約・更新基準は変わらない |
| docs/adr/ | なし | 新しい技術選定・恒久的アーキテクチャ決定がない（試験導入の custom variant） |
| docs/ARCHITECTURE.md | なし | harness の構造・skill 解決・verdict 機構は変わらない |
| docs/reference/ | なし | API・コーディング規約の変更なし |
| docs/cli-guides/ | なし | CLI 仕様の変更なし |
| docs/README.md | なし | 新規 doc を作らない。workflow_guide の索引説明は引き続き正しい |
| README.md / README.ja.md / llms.txt | なし | 「custom variants such as dev-thorough」の記述で包含される。外部読者向けに試験導入物を追記しない |
| CLAUDE.md | あり | Development Skills 表に軽量経路（custom dev-small・試験導入・明示選択）と新 skill 2 本の行を追加 |
| AGENTS.md | なし | 常時適用ルール・routing は変わらない（routing 先の workflow_guide に記載） |
| CHANGELOG.md | あり | `[Unreleased]` の Added に dev-small の試験導入（repository 固有の custom、starter 非公開）を記載。starter 追随時の区分判断の根拠になる |
| experiments/wf-token-usage/ | なし | `QUALITY_STEP_IDS` の step 名固定は導入後の測定時に扱う（Issue § 参考の決定） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #427 本文・コメント | https://github.com/apokamo/kaji/issues/427 | 決定事項・スコープ境界・完了条件・重要判断表（2026-09-06 / 09-07 / 09-29 のユーザー承認）。本文レビューと対応報告（issuecomment-5559144892 ほか） |
| 標準 dev workflow | `.kaji/wf/official/dev.yaml` | PASS 経路の agent step 9、baseline は `skill: baseline-precheck`（agent なし）、review-poll は `exec: [kaji, pr, review-poll]`、各 step の agent / model / effort と cycle 名 |
| workflow validator | `kaji_harness/workflow.py` `validate_workflow` | loop 末尾の RETRY は loop 先頭へ、cycle は PASS で外へ出る、全 step が先頭から到達可能、`BACK_*` の文法。self-RETRY の cycle 所属は検査しない |
| cycle 解決・上限 | `kaji_harness/models.py` `find_cycle_for_step`、`kaji_harness/runner.py`（「サイクル上限チェック」「サイクルカウント」） | entry と loop を cycle 所属とみなし、dispatch 前に `cycle_iterations >= max_iterations` で `on_exhaust`。increment は loop 末尾 step の RETRY 時 |
| session state | `kaji_harness/state.py` `SessionState.load_or_create` | `.kaji-artifacts/<issue>/session-state.json` を読み、`cycle_counts` を run 間で持ち越す |
| prompt 注入 | `kaji_harness/prompt.py` `build_prompt` | 注入変数（`design_path` を含む）、cycle 内 step の `cycle_count` / `max_iterations`、`previous_verdict` は `resume` 指定 step のみ |
| preflight | `kaji_harness/preflight.py`、`Makefile` `validate-workflows` | L1/L2/L3 と skill の存在・frontmatter 検証。対象は `git ls-files -- '.kaji/wf'` |
| workflow 所有権と実行コマンド | `docs/dev/workflow-authoring.md` § ファイル配置 / § 品質保証の責務境界 / § サイクル定義 / § `--from` / `--step` / `--before` / `--reset-cycle` の使い分け | custom は pytest 対象外で L1/L2/L3 のみ。self-RETRY は cycle loop 末尾に所属させる。`--before` は exclusive barrier、`--from` は到達可能 step から開始 |
| self-RETRY 検査の範囲 | `tests/workflows/test_self_retry_cycle_membership.py` | official のみを glob 起点に検査 |
| series 自動選択 | `.claude/skills/series-create/SKILL.md` Step 3、`tests/test_series_create_skill.py` | custom YAML の description も読み、「standard series auto-selection target」と書かれた候補だけを選ぶ。variant は「series 自動選択対象外」 |
| baseline 契約 | `docs/dev/baseline-check.md`、`kaji_harness/scripts/baseline_precheck.py`（`_evaluate` / `_compare`）、`kaji_harness/baseline.py`（`ScopeEvaluation`） | `--evaluate` は artifact 検証・ancestor 確認の上で `verdict` / `stop` / `overlapping` / `baseline_status` を返す。`--compare` は baseline 欠落・非 ancestor でも `regressions: []` を返すため `verdict` の確認が必要。measure は `draft/design/**` 以外の commit があれば再測定しない |
| baseline CLI の対象解決 | `kaji_harness/scripts/baseline_precheck.py` `_parse_args` / `main`、`kaji_harness/runner.py` `_dispatch` / `_build_context_env` | `--worktree` の既定値は環境変数 `KAJI_WORKTREE_DIR` で、どちらも無ければ `ValueError("KAJI_WORKTREE_DIR or --worktree is required")`。runner が `KAJI_*` を渡すのは `settings.is_script_like` の step だけ。`--scope` は `action="append"` で繰り返し指定する。設計修正時に `env -u KAJI_WORKTREE_DIR` で `--worktree` なしは `ValueError`、付与すると構造化 JSON に到達することを確認 |
| 既存 skill の同種問題 | https://github.com/apokamo/kaji/issues/430 | 標準 dev skill の `--compare` 呼び出しに `--worktree` が無い問題を本 Issue と分離して起票 |
| 既存 skill の設計書・PHR 依存 | `.claude/skills/issue-review-code/SKILL.md` Step 1-2・Step 1.4、`.claude/skills/i-dev-final-check/SKILL.md` Step 2-1・Step 6・Step 7.5、`.claude/skills/issue-implement/SKILL.md` Step 2・Step 8.5 | 軽量経路で再利用できない理由 |
| PR 系 skill の依存確認 | `.claude/skills/i-pr/SKILL.md`、`.claude/skills/review/SKILL.md` Step 5、`.claude/skills/pr-fix/SKILL.md`、`.claude/skills/pr-verify/SKILL.md`、`.claude/skills/issue-close/SKILL.md` § 共通: ワークフロー完了後の確認項目の移管 | `review` Step 5 だけが設計書を前提にする。`issue-close` は事後確認の `[ ]` だけを移す |
| type 別実装ガイド | `.claude/skills/_shared/implement-by-type/{feat,bug,refactor}.md` | 設計書の IF・再現手順・根本原因・改善指標を前提に書かれているため新 skill から参照しない |
| Pre-Handoff Review 観点 | `.claude/agents/kaji-code-reviewer.md` § チェック観点 | 設計書整合・テスト証跡・Scope 混在・auto-close 規約。軽量経路では Scope 混在・auto-close 等を独立レビューへ集約 |
| verdict marker と resolver | `docs/dev/shared_skill_rules.md` § verdict マーカー契約、`kaji_harness/commands/issue.py` `resolve_latest_verdict` | producer は `--verdict-step` / `--verdict-status` を無条件付与。`resolve-verdict` は最新 marker の `step / status / meta / created_at` を返し本文は返さない |
| auto-close 規約 | `docs/dev/shared_skill_rules.md` § auto close keyword 回避 | hazard pattern と `指摘 N` 形式 |
| skill 作成規約 | `docs/dev/skill-authoring.md` § 段階的開示と遅延読込 / § verdict 出力規約 / § 手動・ハーネス両立スキルの書き方 | 読込 Step の明記、verdict 3 経路、`$ARGUMENTS` と注入変数の両立 |
| skill markdown の全件検査 | `tests/test_skill_migration.py`、`tests/test_skill_placeholders.py` | `gh issue|pr|api` 記述と旧 placeholder を全 skill で禁止 |
| prose 検査テストの方針 | commit `4dfb343`（`git show 4dfb343`） | 文言の部分一致だけを検査するテストは挙動上の契約を保証しないため削除 |
| recovery の副作用 skill | `kaji_harness/recovery/models.py` `NON_RESUMABLE_SKILLS` | `issue-start` / `i-pr` / `issue-close` を skill 名で判定 |
| worktree 作成と削除 | `.claude/skills/issue-start/SKILL.md` Step 2、`docs/guides/git-worktree.md` § Worktree の削除 | `git worktree add -b` は既存 branch / worktree があると成立しないため、初めからのやり直しには既存 worktree・branch の削除が要る |
| テスト規約 | `docs/dev/testing-convention.md` § docs-only / metadata-only / packaging-only 変更、§ 省略してよい理由 | 恒久テスト不要の 4 条件 |
| starter 追随の区分 | `.claude/skills/update-starter/references/classification-guide.md` § 3 区分 | starter 反映は Release 間の変更を人が 3 区分して決める。自動公開されない |
| 先行実装（private） | `apokamo/k-aiagent` `c3f22a7`。ローカル: `git -C ~/dev/k-aiagent show c3f22a7:.kaji/wf/custom/dev/dev-small.yaml`、同 `:.claude/skills/issue-small-change-execute/SKILL.md`、同 `:.claude/skills/issue-small-change-review/SKILL.md` | 要約: step 構成 review-ready → start → change → review-change → pr → review-poll → close、同一 skill を別 step id で呼ぶ fix/verify ループ、cycle 名 small-*、change は RETRY 自己ループ・fix-change は PASS/ABORT、レビューは別 context で完了条件チェックを更新、適用外は ABORT。本文は private のため転記しない |
| 外部提案（private） | apokamo/fullstack-agent-template#141 | 採用・不採用の内容は Issue #427 本文に固定済み。本設計は Issue 本文を正本として参照する |
