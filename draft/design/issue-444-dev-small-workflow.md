# [設計] dev-small で完了条件と workflow の不整合を実装前に検出する

Issue: #444

## 概要

dev-small workflow で、`### ワークフロー完了後の確認項目` を除く完了条件に dev-small では満たせない項目
（dev-small にない工程の成果物を前提とする項目）が残っているとき、`change` step の適用判定で実装前に ABORT する。
独立レビュー（`review-change` / `verify-change`）でも同じ不整合を blocking finding の有無と関係なく検査し、
検出したら RETRY より優先して ABORT する。

## 背景・目的

### ユースケース

- dev-small を起動する保守者として、完了条件が dev-small と合っていないことを実装前の `change` step で知りたい。
  そうすれば実装やレビューの往復を無駄にせず、本文を直すか標準 dev に切り替えるかを決められる。
- dev-small の独立レビューとして、軽量経路では満たせない完了条件を見つけたら、他の指摘の有無に関係なくその時点で
  ABORT したい。そうすれば満たせない条件のために fix-change / verify-change の往復を重ねずに済む。

### 現状の問題（Issue 本文 § 目的、および #438 の実行記録より）

#438 を dev-small で実行した際、完了条件に issue-create テンプレート
（`.claude/skills/issue-create/templates/issue-feat.md:41-42`）由来の「設計書作成」「テスト作成（設計書のテスト戦略に従い
S/M/L を網羅）」が残っていた。

1. `issue-small-change-execute` Step 2（`.claude/skills/issue-small-change-execute/SKILL.md:89-97`）の適用判定 5 項目は
   完了条件と workflow の整合を見ない。`change` はこの 2 項目を残したまま実装して PASS した。
2. `issue-small-change-review` Step 5（`.claude/skills/issue-small-change-review/SKILL.md:142-159`）では、完了条件の
   照合と「軽量経路で満たせない項目は ABORT」が **「finding なし」分岐の中** にしかない。#438 の review-change
   （2026-09-30T16:11:26Z、`step=review-change status=RETRY`）は「本文に残る『設計書作成』『設計書のテスト戦略』の条件は、
   設計書を要求しない dev-small の選択と整合していない。…次回 PASS 判定前に本文条件と選択 workflow の整合を確認する必要がある」
   と書きながら、別の指摘（指摘 1: 削除時期の明示）だけを理由に RETRY した。skill の手順どおりの動作である。
3. その結果 fix-change → verify-change を 1 往復し、verify-change（2026-09-30T16:22:16Z、`step=verify-change status=ABORT`）で
   初めて ABORT した。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| issue-create テンプレートの設計書項目に「dev-small では削除」等の注記を足す | Issue 本文「重要判断」で本 Issue のスコープ外と明記（必要なら別 Issue） |
| `issue-review-ready` に選択 workflow を認識させ不整合を着手前に検出する | 同上。また review-ready は起動 workflow を入力に持たない |
| 不整合を検出したら change / review が Issue 本文の該当項目を削除・書き換える | 完了条件はスコープ・品質ゲートに関わり、変更は人間の判断（`_shared/critical-decision-checklist.md` § one-way door になりやすい判断軸「Issue の目的や完了条件を変えるスコープ変更」）。#438 の verify-change も「未充足条件を無断で削除・チェック済みにしない」と停止している |
| review の検査を Step 1（前提確認）に移し、Step 2〜4 を実行せずに ABORT する | Issue 完了条件は「Step 5 を変更」と指定している。change 側の実装前検出が主防御であり、review 側は backstop として検証結果を ABORT 報告に残せる方が、人間が「既存差分を残すか」を判断しやすい（下記 provenance 参照） |

## インターフェース

本 Issue は skill 手順（agent への instruction）と運用 docs の変更であり、公開 CLI・API・データ契約は変えない。
「インターフェース」は skill の判定入力と verdict 出力の契約として定義する。

### 入力

| 入力 | 取得元 | 用途 |
|------|--------|------|
| Issue 本文の `## 完了条件` | `kaji issue view [issue_id] --json title,body,labels`（両 skill の既存「読み込み条件」の通常入力。新規取得は増やさない） | 不整合検査の対象。`### ワークフロー完了後の確認項目` 以下は検査対象外 |
| dev-small が持たない工程の一覧 | skill 本文・`docs/dev/workflow_guide.md` § dev-small に明記（設計書作成・設計レビュー・Pre-Handoff Review・final-check） | 「dev-small で満たせない項目」の判定基準 |

### 判定基準（「dev-small で満たせない項目」）

完了条件の項目のうち、**その充足を示す証拠または充足範囲の定義が、dev-small にない工程の成果物を前提とするもの**。
dev-small にない工程の成果物 = 設計書（`draft/design/**`）・設計レビュー結果・Pre-Handoff Review 報告・final-check 報告・
設計書の Issue 本文アーカイブ。

| 区分 | 例 |
|------|----|
| 満たせない（該当） | 「設計書作成（`draft/design/issue-<id>-<slug>.md`）」、「テスト作成（設計書のテスト戦略に従い S/M/L を網羅）」、「設計レビューで Approve」、「影響ドキュメントの更新（設計書『影響ドキュメント』セクション参照）」のように範囲を設計書で定める項目 |
| 満たせる（非該当） | 「`make check` 通過」、「〜のテストを追加」、「`docs/dev/workflow_guide.md` § X を同期」など、実 diff・dev-small 自身の検証・docs 更新で照合できる項目 |

完了条件がチェックボックス形式でない場合も、列挙された各条件に同じ基準を適用する（review 側の既存例外読み込み
`docs/dev/workflow_completion_criteria.md` § 本文にチェックボックスがない場合 を維持）。

### 出力

| step | 該当時の verdict | 優先順位 | 副作用 |
|------|------------------|----------|--------|
| `change` | `ABORT`（適用外） | Step 2 で判定。Step 3 以降（方針・baseline scope 評価・編集・commit）を行わない | Issue コメント（ABORT 報告）のみ。worktree・branch は無変更で保全 |
| `review-change` / `verify-change` | `ABORT` | blocking finding の有無に関係なく、RETRY より優先 | Issue コメント（ABORT 報告）のみ。Issue 本文のチェックボックス更新・項目の削除や書き換えはしない |

ABORT 報告には、既存の ABORT 時記載事項に加えて次を含める:

- 該当した完了条件の項目（本文の文言をそのまま引用）と、前提とする dev-small にない工程
- 人間が決める事項: 該当項目を dev-small に合わせて本文から変更するか、標準 dev に切り替えるか
- review 側では、同時に見つかった blocking finding も非 ABORT 理由として併記する（RETRY にはしない）

### 使用例（判定の流れ。疑似コード）

```text
# change Step 2（適用判定）
criteria = completion_criteria(issue_body, exclude="### ワークフロー完了後の確認項目")
unsatisfiable = [c for c in criteria if presupposes_absent_step_artifact(c)]
if unsatisfiable or not existing_5_conditions():
    report_abort(items=unsatisfiable, human_decision="本文を dev-small に合わせる / 標準 dev へ切替")
    return ABORT          # Step 3 以降（編集・commit）に進まない

# review Step 5（判定）
if unsatisfiable(criteria):                 # 最初に、finding の有無に関係なく
    return ABORT (blocking findings は報告に併記)
if blocking_findings: return RETRY
if out_of_scope: return ABORT
# finding なし → 全件照合（満たせる項目の未充足は RETRY）→ PR 前提 → 本文更新 → PASS
```

### エラー・境界

- `## 完了条件` が本文に無い: 既存の適用条件「受け入れ条件が Issue で明確」で判断する（本変更で新たな扱いは追加しない）
- 判定に迷う項目: change では既存 Step 2 の原則（満たさなければ適用外）に従い ABORT 側に倒し、報告で理由を示す。
  review 側も同じ基準で判定し、ABORT 報告に判定根拠を書く
- verify-change の確認範囲限定（新規 RETRY 理由の制限）は本検査に適用しない。検査は HEAD 時点の Issue 本文に対して毎回行う

## 制約・前提条件

- 変更対象は `.claude/skills/issue-small-change-execute/SKILL.md`、`.claude/skills/issue-small-change-review/SKILL.md`、
  `docs/dev/workflow_guide.md` の 3 ファイルに限る。`.agents/skills/*` は `.claude/skills/*` への symlink のため同期作業は不要
- workflow YAML（`.kaji/wf/custom/dev/dev-small.yaml`）の遷移は変えない。`change` / `review-change` / `verify-change` は
  既に `ABORT: end` を持つ。新しい verdict・遷移は追加しない
- Issue 本文の編集権限は増やさない。execute は Issue 本文を編集しない、review は PASS 時のチェックボックス更新のみ、という
  既存の「副作用の境界」を維持する
- 判定は既存の通常入力（Issue 本文）だけで行い、設計書・標準 dev の資料を読まない原則（両 skill の「読み込み条件」）を維持する
- issue-create テンプレートと issue-review-ready は変更しない（Issue 本文「重要判断」）
- auto-close hazard pattern を skill 本文・docs・commit message に書かない

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `.claude/skills/issue-small-change-execute/SKILL.md` | Step 2 に適用条件を 1 項目追加し、該当時は実装前に ABORT と明記。Verdict 表の change ABORT 行に該当条件を明示。報告の ABORT 記載事項に「該当項目と人間の選択肢」を追加 |
| `.claude/skills/issue-small-change-review/SKILL.md` | Step 5 の先頭に、finding の有無に関係なく行う不整合検査と RETRY より優先する ABORT を追加。「finding なし」分岐の照合から「軽量経路で満たせない項目は ABORT」を先頭検査へ移す。verify-change 差分節に「確認範囲限定はこの検査に適用しない」を追加。Verdict 表の RETRY / ABORT 行を同期 |
| `docs/dev/workflow_guide.md` | § dev-small の適用条件に条件を 1 項目追加（例: issue-create テンプレート由来の設計書項目）。§ 適用外 ABORT とやり直し に、change の実装前検出と review の即時 ABORT（RETRY より優先）、人間が決める事項を追記 |

## 方針

1. **execute Step 2**: 既存 5 項目の後に次を追加する。
   - 「`### ワークフロー完了後の確認項目` を除く完了条件に、dev-small で満たせない項目（設計書の作成・設計書への準拠・
     設計レビューなど、dev-small にない工程の成果物を前提とする項目）がない」
   - 該当した場合は実装前（Step 3 以降に進まず）に適用外 ABORT とし、本文の項目を書き換え・削除しないことと、
     報告に該当項目・人間が決める事項を記すことを明記する
   - Step 2 は `change` の初回・RETRY 再入で実行される。`fix-change` は Step 1 と Step 6〜9 のみ共通で Step 2 を持たない
     既存構造を変えない（review 側の検査が backstop になる）
2. **review Step 5**: 判定の評価順を明示する。
   1. 最初に、blocking finding の有無に関係なく、事後確認を除く完了条件を走査し、軽量経路で満たせない項目があれば ABORT
      （RETRY より優先。同時に見つかった blocking finding は報告に併記する）
   2. blocking finding → RETRY（既存）
   3. 適用外（大きな設計判断等）→ ABORT（既存。優先順位は本 Issue で変更しない）
   4. finding なし → 完了条件全件照合（満たせない項目は RETRY。軽量経路で満たせない項目は 1. で検査済み）→ PR 前提 →
      本文更新 → PASS（既存）
3. **verify-change 差分節**: 「新たに RETRY にしてよいのは…に限る」の確認範囲限定は Step 5 の 1. に適用しない
   （HEAD 時点の Issue 本文に対して毎回検査する）と 1 文追加する。
4. **Verdict 表の同期**: review の ABORT 行に「軽量経路で満たせない完了条件（blocking finding の有無に関係なく RETRY より優先）」、
   RETRY 行の「未充足の完了条件」を「軽量経路で満たせる未充足の完了条件」に。execute の change ABORT 行の「適用外」に
   「完了条件と dev-small の不整合を含む」を補う。
5. **workflow_guide 同期**: § dev-small の適用条件の箇条書きに同じ条件を追加し、issue-feat テンプレート由来の
   「設計書作成」「設計書のテスト戦略に従い S/M/L を網羅」を例示する。§ 適用外 ABORT とやり直し に、
   change は実装前に検出して ABORT、review-change / verify-change は他の指摘があっても RETRY より優先して ABORT、
   人間は本文を dev-small に合わせるか標準 dev に切り替えるかを決める、を追記する。
6. 文言は既存 skill の簡潔な箇条書きスタイルに合わせ、同じ判定基準の説明を 3 ファイルで重複させすぎない
   （判定基準の例示は workflow_guide を正本とし、skill は条件文と括弧内の代表例に留める）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 再発防止の範囲 | change の適用判定と review の即時 ABORT の 2 点に限る | Issue 本文「重要判断」1 行目。AI（2026-10-01 セッション）の提案を人間が起票指示した。review-ready（Issue コメント、PASS）も局所的で可逆な手順変更と判定 | 変更ファイルを 3 件に限定し、workflow YAML・verdict 語彙・遷移は変えない |
| issue-create テンプレート / review-ready を変更しない | 変更しない | Issue 本文「重要判断」2 行目（AI 提案、two-way door、必要なら別 Issue） | 代替案表で不採用理由を記録 |
| 不整合検出時の verdict | change は実装前 ABORT、review は RETRY より優先して ABORT | Issue 本文「完了条件」1・2 項目（人間が起票を指示した本文） | 判定の評価順（ABORT 検査 → RETRY → 適用外 → 全件照合）を Step 5 に明示 |
| 「dev-small で満たせない項目」の判定基準 | 充足の証拠または範囲定義が dev-small にない工程の成果物を前提とする項目 | Issue 本文「完了条件」1 項目の括弧内定義（設計書の作成・設計書への準拠・設計レビューなど）。成果物の列挙（Pre-Handoff Review・final-check を含む）は `docs/dev/workflow_guide.md:370` 「設計書・設計レビュー・Pre-Handoff Review・final-check の独立工程を持たない」に基づく | 「影響ドキュメントの更新（設計書『影響ドキュメント』セクション参照）」のように範囲を設計書で定める項目も該当とする。**AI の仮定**: 範囲が設計書に依存すると HEAD に対して照合できないため。review-design / review-code で検査 |
| 判定に迷う項目の扱い | ABORT 側に倒す | **AI の仮定**。根拠: execute Step 2 の既存原則「すべて満たさなければ適用外として ABORT」と、`_shared/critical-decision-checklist.md` の「境界に迷う場合は停止」。review-design で検査 | change / review 双方で判定根拠を報告に記載 |
| review 側の検査位置 | Step 5 の先頭に置き、Step 2〜4（全体レビュー・品質検証）は実行してから判定する | Issue 本文「完了条件」2 項目が「Step 5 を変更し」と指定。**AI の仮定**（Step 1 に前倒ししない点）: change 側の実装前検出が主防御で review は backstop。ABORT 報告に HEAD の検証結果が残る方が、人間が既存差分を残すか判断しやすい。review-design で検査 | Step 1 は変更しない |
| verify-change の確認範囲限定との関係 | 本検査には適用しない | Issue 本文「完了条件」2 項目「review-change・verify-change の両方に適用」 | verify-change 差分節に 1 文追加 |
| 適用外（大きな設計判断）ABORT と RETRY の優先順位 | 本 Issue では変更しない | **AI の仮定**: Issue のスコープは完了条件の不整合に限られる（重要判断 1 行目）。review-design で検査 | Step 5 の箇条書きの順序と文言を既存のまま維持 |
| Issue 本文の書き換え | change / review は該当項目を書き換え・削除しない | `_shared/critical-decision-checklist.md`「Issue の目的や完了条件を変えるスコープ変更」は one-way door になりやすい判断軸。既存「副作用の境界」と #438 verify-change の停止判断に一致 | ABORT 報告に人間の選択肢（本文変更 / 標準 dev 切替）を明記 |
| `workflow_completion_criteria.md` を変更しない | 変更しない | **AI の仮定**: L130-131 は各 step が「確認する」完了条件の範囲を示す表で、本変更は適用判定（ゲート）の追加であり確認範囲は変わらない。L185 の本文更新規則も不変。review-design で検査 | 影響ドキュメント表に理由を記録 |

one-way door の未決: なし。公開 CLI・永続化・quality gate の省略・Issue 目的の変更を伴わず、変更は skill 手順の局所的な追加で
通常の revert で戻せる。

## テスト戦略

### 変更タイプ

docs-only 相当（agent instruction である `SKILL.md` と運用 docs のみ。`kaji_harness/`・`tests/`・workflow YAML・
`pyproject.toml` を変更しない。実行時の Python コードの振る舞いは変わらない）。

### docs-only / metadata-only / packaging-only の場合

#### 変更固有検証

- `make check`（Issue 完了条件。ruff / mypy / `validate-workflows` / pytest。workflow YAML 無変更の回帰確認を含む）
- `make verify-docs`（`docs/dev/workflow_guide.md` を変更するため、リンク・参照整合）
- 3 ファイル間の同期確認（grep）: execute Step 2・review Step 5・workflow_guide § dev-small の適用条件 の条件文が同じ
  判定基準（`### ワークフロー完了後の確認項目` を除く / dev-small にない工程の成果物を前提とする）を指すこと
- review Step 5 の評価順の目視確認: 不整合 ABORT 検査が blocking finding 判定より前にあり、「finding なし」分岐内に
  「軽量経路で満たせない項目は ABORT」が残っていないこと。verify-change 差分節に確認範囲限定の除外が書かれていること
- auto-close hazard pattern の grep（変更ファイルと commit message）:
  `grep -iE '\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*:?\s*#[0-9]'`

#### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` § docs-only / metadata-only / packaging-only 変更 の 4 条件）

1. 独自ロジックの追加・変更をほぼ含まない: 変更は agent が読む手順書と docs の文言のみ。harness のコードは
   `SKILL.md` の内容を解釈しない（skill 本文はプロンプトとして agent に渡るだけ）。追加する手順は自然言語の判定規則で、
   jq / shell など機械実行可能な snippet を追加・変更しない
2. 想定される不具合パターンが既存ゲートで捕捉済み: リンク切れは `make verify-docs`（`.claude/skills/` も走査対象。
   `Makefile:43`）、旧 placeholder の混入は `tests/test_skill_placeholders.py`（全 skill md を走査）、workflow 定義の破損は
   `make validate-workflows`（いずれも `make check` / `make verify-docs` に含む）が捕捉する。手順の意味的な誤りは
   review-design / review-code / PR review の独立レビューが検査する
3. 新規テストを追加しても回帰検出情報がほとんど増えない: 既存の SKILL.md 対象テストのうち内容に踏み込むもの
   （`tests/test_issue_close_evidence_jq.py`）は、SKILL.md に埋め込まれた **実行可能な jq プログラム** を抽出して producer と
   照合する。本変更にはそうした実行対象がなく、文言を assert するテストは文言変更で壊れるだけで agent の判定挙動を検証できない
4. 理由をレビュー可能な形で説明できる: 本節に記録し、実装報告に検証コマンドの結果を残す

`testing-convention.md` の「原則として新規テストを要求する変更」の「過去障害の再発防止」は、実行時の振る舞いを変える変更
（同 § 実行時の振る舞いを変える変更）を対象とする。本 Issue の再発防止は agent instruction の判定手順で行い、実行時コードを
変えないため、上記 4 条件による省略が適用できる。

実運用での効果（実装前 ABORT が発生すること）は、dev-small 試験導入の効果評価（`docs/dev/workflow_guide.md` § 効果評価）で
観測する対象であり、本 Issue の workflow 内完了条件には含めない（Issue 本文の事後確認も「なし」）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定・アーキテクチャ判断を伴わない |
| docs/ARCHITECTURE.md | なし | harness 構造を変えない |
| docs/dev/workflow_guide.md | あり | Issue 完了条件 3。§ dev-small の適用条件、§ 適用外 ABORT とやり直し を同期 |
| docs/dev/workflow_completion_criteria.md | なし | L130-131 は step ごとの「確認する完了条件の範囲」、L185 は本文更新規則。適用判定の追加では変わらない |
| docs/dev/workflow_overview.md | なし | L28-31 は dev-small の選択概要と § dev-small への参照のみ |
| docs/dev/development_workflow.md | なし | L16-17・L123-124・L220 は dev-small が設計書・PHR を持たないことの記述で、本変更と矛盾しない |
| docs/dev/shared_skill_rules.md | なし | L19・L27 は最終判定の担い手の記述で不変 |
| docs/reference/ / docs/cli-guides/ | なし | CLI・API・コーディング規約を変えない |
| AGENTS.md / CLAUDE.md | なし | CLAUDE.md の Development Skills 表の dev-small 行は skill 名のみで不変 |
| CHANGELOG.md | なし | `## [Unreleased]` は `/release` skill が release 時に生成する運用（`.claude/skills/release/SKILL.md` Step 3） |
| `.claude/skills/issue-create/templates/issue-feat.md` | なし | Issue 本文「重要判断」でスコープ外 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #444 本文 | https://github.com/apokamo/kaji/issues/444 | 完了条件 1〜4、「重要判断」表（範囲を 2 点に限る / テンプレート・review-ready を変更しない） |
| execute skill Step 2 | `.claude/skills/issue-small-change-execute/SKILL.md:89-97` | 適用判定 5 項目に完了条件と workflow の整合確認がない |
| review skill Step 5 | `.claude/skills/issue-small-change-review/SKILL.md:142-159` | 「blocking finding がある → RETRY」が先にあり、「軽量経路で満たせない項目は ABORT」は「finding なし」分岐の中（L147-149）にのみある |
| review skill verify-change 差分節 | `.claude/skills/issue-small-change-review/SKILL.md:165-177` | 「新たに RETRY にしてよいのは…HEAD で未充足の完了条件…に限る」。確認範囲限定の規定 |
| workflow_guide § dev-small | `docs/dev/workflow_guide.md:353-440` | L370「設計書・設計レビュー・Pre-Handoff Review・final-check の独立工程を持たない」、L387-401 適用条件、L421-440 適用外 ABORT とやり直し |
| issue-feat テンプレート | `.claude/skills/issue-create/templates/issue-feat.md:41-42` | 「設計書作成（`draft/design/issue-<issue_id>-<slug>.md`）」「テスト作成（設計書のテスト戦略に従い S/M/L を網羅）」が既定の完了条件に含まれる |
| #438 review-change RETRY | https://github.com/apokamo/kaji/issues/438 （2026-09-30T16:11:26Z、`step=review-change status=RETRY`） | 「本文に残る『設計書作成』『設計書のテスト戦略』の条件は…dev-small の選択と整合していない。…次回 PASS 判定前に…整合を確認する必要がある」としつつ指摘 1 で RETRY |
| #438 verify-change ABORT | https://github.com/apokamo/kaji/issues/438 （2026-09-30T16:22:16Z、`step=verify-change status=ABORT`） | 「Step 5 は『軽量経路で満たせない項目は ABORT』と指定…設計書追加を実装側に要求する RETRY にはせず ABORT」「未充足条件を無断で削除・チェック済みにしない」 |
| dev-small workflow 定義 | `.kaji/wf/custom/dev/dev-small.yaml` | `change` / `review-change` / `verify-change` は `ABORT: end` を既に持つ。遷移追加不要 |
| 重要判断チェックリスト | `.claude/skills/_shared/critical-decision-checklist.md` | 「Issue の目的や完了条件を変えるスコープ変更」は one-way door になりやすい。境界に迷う場合は停止 |
| 完了条件の確認責務 | `docs/dev/workflow_completion_criteria.md:130-131, 185` | dev-small の execute / review が確認する範囲と PASS 時の本文更新規則。本変更で不変 |
| テスト規約 | `docs/dev/testing-convention.md:68-75` | docs-only 等で恒久テストを不要とする 4 条件 |
