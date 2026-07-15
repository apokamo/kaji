# [設計] starter 追随・レビュー・リリース skills を追加する

Issue: #341

## 概要

kaji 本体の release 後に `kaji-starter-python` 等の managed starter を追随更新し、独立レビューを経て、対応する kaji version の検証済み template snapshot として tag / GitHub Release を公開するための maintainer 用 3 skill（`update-starter` / `review-starter-update` / `release-starter`）と、その正本 runbook・`/release` からの post-release handoff を追加する。skill 間の機械契約（review PASS と SHA の検証、重複 Release / 部分成功の分岐）は CLI / harness 層の決定的コード（verdict マーカー meta 拡張・`kaji issue resolve-verdict`・`kaji starter release-plan`）として追加する。

## 背景・目的

### ユーザーストーリー

1. **kaji maintainer** として、本体 release 後の starter 追随漏れを防ぐため、対象 release 間の変更を「starter へ反映 / package 更新で吸収 / starter に不要」の 3 区分に全件分類した上で starter 更新案を作りたい。
2. **レビュー担当者** として、実装セッションの結論に依存せず追随の完全性と安全性を判定するため、upstream tag / CHANGELOG / diff と starter 差分を別 session で独立に突合したい。
3. **release maintainer** として、検証済み starter の不変 snapshot と対応 kaji version を明示するため、review PASS・人間承認・quality gate 通過後にのみ tag / GitHub Release を公開したい。

### 代替案と不採用理由

- **既存 `issue-review-code` 等での代替**: 通常の設計整合・コード品質レビューは可能だが、「対象 release 間の upstream 変更を全件分類し、反映の完全性（omission）を判定する」専用契約を持たない。`make check` は「必要な資産が存在しない」omission を検出できない。→ 専用 skill が必要（Issue 本文「目的 § 現状の問題」）。
- **`/release` skill への統合**: kaji 本体 release のトランザクションと starter 追随の成否を分離する人間決定（Issue `## 決定事項` § backlog / 公開 gate）に反するため不採用。`/release` は handoff 案内のみ持つ。

## インターフェース

3 skill はいずれも **手動起動の maintainer skill**（`/release` と同類）であり、workflow YAML には組み込まない。入力は kaji 側の starter-sync tracking Issue の ID に統一する（Issue `## 決定事項` § skill の入力）。

### tracking Issue（3 skill 共通の入力データ）

kaji Release ごと・managed starter ごとに kaji repository へ 1 件作成する（Issue `## 決定事項` § tracking 単位）。本文の必須 field:

| field | 型 / 形式 | 説明 |
|-------|-----------|------|
| `starter_repo` | `owner/name` | 対象 starter の repository identity（正本） |
| `target_kaji_release` | `vX.Y.Z` | 追随対象の kaji release tag |
| `starter_path` | path（任意） | local checkout の上書き。省略時は sibling 既定 `../<repo-name>` |

タイトルは `[starter-sync]: <starter_repo> を kaji vX.Y.Z へ追随する` 形式とする。3 skill は同じ Issue に分類・SHA・review・Release の証跡をコメントとして集約する。

### `update-starter`

- **入力**: `/update-starter <tracking_issue_id>`
- **処理の契約**:
  - tracking Issue から `starter_repo` / `target_kaji_release` / `starter_path` を読み、local checkout を解決する（既定: kaji main worktree の親 directory 直下 `../<repo-name>`）。`git remote get-url` が `starter_repo` と一致しない場合は ABORT（remote identity 検証）
  - 調査開始点は **最新の公開済み starter GitHub Release の tag のみ** を正本とする。Release 不在、または tag / Release / dependency pin の矛盾では fallback せず ABORT
  - 同じ starter に対する古い kaji release の状態が `PENDING` のまま残っている場合（kaji GitHub Release 状態表で判定）、release 順処理のため ABORT
  - 開始点 tag が指す kaji version 〜 `target_kaji_release` の CHANGELOG / commit / changed assets を **全件** 次の 3 区分に分類し、根拠を報告する: (1) starter へ反映が必要 (2) kaji package 更新のみで吸収 (3) starter には不要（理由必須）
  - dependency / lockfile 更新だけで完了と判定せず、BREAKING CHANGE・skills・workflows・config・docs の反映要否を個別判定する
  - 区分 (1) を starter の **remote main と同期した local main へ直接 commit** する。feature branch / worktree / PR / merge は使わず、review 前には push しない
  - maintainer 専用の 3 skill 自身は starter へコピーしない
  - starter の quality gate（対象 repository の Makefile 等の実体から解決。Python 固有名を恒久契約にしない）を実行する
- **出力**: starter local main の未 push commit 群、tracking Issue への分類報告コメント（3 区分全件表・base SHA（remote main）・candidate SHA（local main HEAD）・quality gate 結果を含む）、verdict
- **変更不要（N/A 候補）の場合の出力**: 区分 (1) が空となり、未 push commit は 0 件、candidate SHA == base SHA。分類報告コメントには N/A 根拠（全変更が区分 (2)/(3) に落ちる一覧と理由）を記載し、これが review-starter-update の入力になる。通常更新と N/A の判別は「candidate SHA == base SHA か」で機械的に決まる
- **verdict**: `PASS | ABORT`（`kaji issue comment --verdict-step update-starter --verdict-status <STATUS>` に加え、`--verdict-meta target=<tag> --verdict-meta base=<SHA> --verdict-meta candidate=<SHA>` を無条件付与。「方針 § cross-skill 契約」参照）
- **エラー（ABORT 条件）**: 公開済み starter Release 不在 / tag・Release・pin の矛盾 / 古い `PENDING` の存在 / remote identity 不一致 / local main が remote main へ ff 同期不能 / quality gate が対象 repository から解決不能

### `review-starter-update`

- **入力**: `/review-starter-update <tracking_issue_id>`。update を行った session とは **別 session** で実行する
- **処理の契約**:
  - updater の分類結果を正解とせず、対象 kaji tag / CHANGELOG / upstream diff から独立に証拠を再構成する
  - 変更ごとの 3 区分が全件埋まり、必要な移植が過不足なく実施され、dependency source / lockfile / docs / template cleanliness（maintainer 資産の混入なし）/ validation evidence が整合するかを確認する
  - review は「対象 kaji tag / remote main の base SHA / local main の candidate SHA」に固定する。candidate SHA が変われば PASS は失効し、再 review 必須
  - review 中に更新ファイルを **修正しない**（無修正原則）
- **出力**: tracking Issue へのレビューコメント。`--verdict-step review-starter-update --verdict-status <STATUS>` と `--verdict-meta target=<tag> --verdict-meta base=<SHA> --verdict-meta candidate=<SHA>` を **status によらず無条件付与** する（機械消費用。「方針 § cross-skill 契約」参照）。`evidence` にも target tag / base SHA / candidate SHA を人間可読の必須証跡として含める（共通 verdict schema に新 status / field は追加しない。Issue `## 決定事項` § verdict 契約）
- **verdict**: `PASS | RETRY | ABORT`。RETRY の指摘対応は maintainer が `/update-starter` の再実行または手動修正で行い、candidate SHA が変わるため再 review する

### `release-starter`

- **入力**: `/release-starter <tracking_issue_id>`
- **pre-flight（全件成功が公開の前提。いずれかの不成立・観測不能は fail-closed で公開不可）**:
  1. review PASS の有効性判定: `kaji issue resolve-verdict <tracking_issue_id> --step review-starter-update --require-meta target --require-meta base --require-meta candidate` が解決した **最新** verdict について、`status == PASS` かつ `meta.target == target_kaji_release` かつ `meta.candidate == starter local main HEAD` がすべて成立する。同一 candidate への後続 `RETRY` / `ABORT` 投稿は「最新 verdict 選択」により過去の PASS を無効化する。resolver の未検出・不正形式・meta 欠落・値の不一致はすべて公開不可
  2. starter local main が clean
  3. 対象 kaji Release（`target_kaji_release`）が published 状態
  4. starter の dependency pin / lockfile が対象 kaji version と整合
  5. starter の quality gate 通過
  6. 経路決定: 観測状態（既存 tag 群と SHA・Release 有無・状態表・Issue state）を `kaji starter release-plan` に渡し、後述「重複 Release と部分成功の状態遷移」の決定表で一意に分岐する
  7. 経路別の鮮度検証: 未公開経路（決定 1 / 3）では starter remote main が `meta.base` から不変であること、公開済み candidate の残処理経路（決定 2）では starter remote main == `meta.candidate`（atomic push 済みの整合）であることを確認する
- **人間承認 gate**: pre-flight 成功後、candidate SHA と tag 名を提示し、**workflow 外で人間の明示承認** を得てからのみ ref push（tag + main の atomic push）へ進む。push を伴わない残処理経路（決定 2）では再承認を要求せず、実行前に plan 内容を提示する
- **公開**: annotated tag（初回 `kaji-vX.Y.Z` / 再 release `kaji-vX.Y.Z-rN`）と starter `main` を `git push --atomic` で 1 トランザクション push → template から生成した GitHub Release notes で `gh release create` → 対応 kaji Release 本文の repository 別状態表の該当行を `PENDING -> PASS` に更新（starter Release をリンク）→ tracking Issue を close（処理順は固定）
- **N/A 経路**: starter 変更不要の判定にも独立 review PASS を必須とし（resolver 判定は同一。N/A は `meta.base == meta.candidate` で機械判別）、PASS 後にのみ kaji Release 状態表の該当行を `N/A` と根拠付きで更新して tracking Issue を close する。starter tag / Release は作らない
- **失敗時**: atomic push 後の部分失敗（Release ページ / kaji Release 状態表 / Issue close の失敗）は公開済み tag を rollback せず、同じ tag に対する不足処理のみ再試行する。不足処理の特定は「重複 Release と部分成功の状態遷移」の残処理判定による。kaji 本体 release も rollback しない。状態表の該当行は `PENDING` のまま復旧手順を報告する。**force push / tag 上書きは禁止**
- **verdict**: `PASS | ABORT`（`--verdict-step release-starter` マーカー付与）

#### 重複 Release と部分成功の状態遷移

`release-starter` は観測状態と candidate SHA を入力に、`kaji starter release-plan`（純粋決定関数 + CLI 面。git / GitHub の観測は skill が行い、分岐判定は決定的コードが行う）で経路を一意に決定する。対象 kaji version `vX.Y.Z` の revision 系列を `kaji-vX.Y.Z`（N=0）/ `kaji-vX.Y.Z-rN`（N>=1）とし、`latest` = 最大 N の既存 tag とする。

| # | 観測状態 | 決定 |
|---|----------|------|
| 1 | 対象 version の tag が 0 件 | 新規公開: tag `kaji-vX.Y.Z` を採番し、人間承認 → atomic push へ進む |
| 2 | `latest` の SHA == candidate | 既存 tag を再利用（**新 tag 発行・ref push なし**）。下記の残処理判定へ |
| 3 | `latest` の SHA != candidate | 再 release: tag `kaji-vX.Y.Z-r(maxN+1)` を採番し（既存 `-rN` が複数あれば最大 N + 1）、人間承認 → atomic push へ進む |
| 4 | `latest` 以外の旧 revision tag の SHA == candidate | ABORT（過去 revision の再公開要求。新 tag を発行せず、観測値を人間へ提示） |
| 5 | 観測の矛盾（対象 version の Release だけ存在し tag が無い / tag が annotated でない / kaji Release 状態表に該当行が無い 等） | ABORT（矛盾内容と観測値を提示） |

残処理判定（決定 2 のとき。bookkeeping の処理順は公開手順と同じ固定順: (a) GitHub Release 作成 → (b) kaji Release 状態表 `PENDING -> PASS` 更新 → (c) tracking Issue close）:

| 観測 | 実行 |
|------|------|
| (a) が無い | (a) から順に不足分のみ実行（**新 tag は発行しない**） |
| (a) あり・(b) 未完 | (b) から実行 |
| (a)(b) あり・(c) 未完 | (c) のみ実行 |
| (a)(b)(c) すべて完了 | 何もせず idempotent PASS（evidence に「全処理完了済み」と観測値を記録） |

### starter GitHub Release notes template

`release-starter` 配下の template として定義し、次のセクションを必須とする（Issue 完了条件）: 対応 kaji Release へのリンク / 反映内容 / N/A とした変更と理由 / BREAKING 対応 / 検証 evidence（quality gate・review PASS の参照）/ tag snapshot の利用方法（利用者導線は `Use this template` のみであり、tag は保守・監査 marker である旨）。

### kaji `/release` との連携（既存 skill の変更）

- Step 7（GitHub Release 作成）の notes に **repository 別状態表** セクションを含める。行は runbook の managed starters 表から生成し、初期値 `PENDING`:

  ```markdown
  ## Starter repositories

  | repository | status | tracking Issue | starter Release / N/A 理由 |
  |---|---|---|---|
  | apokamo/kaji-starter-python | PENDING | #<id> | - |
  ```

  各行は独立に `PENDING -> PASS` または `N/A` へ遷移する。単一の集約 status は置かない
- Step 8（完了報告）に post-release handoff を追加: managed starter ごとの tracking Issue 作成（`kaji issue create`）→ 状態表へ tracking Issue リンクを反映（`gh release edit`）→ `/update-starter <tracking_issue_id>` への案内
- starter 側の失敗を理由に公開済み kaji tag / Release / PyPI を rollback しない契約を明記する

### 使用例

```bash
# kaji v0.16.0 release 後（/release Step 8 の handoff に従い tracking Issue #400 を作成済み）
/update-starter 400          # 分類 + starter local main へ未 push commit
# --- 別 session ---
/review-starter-update 400   # 独立検証 → PASS（candidate SHA 固定）
# --- 任意の session ---
/release-starter 400         # pre-flight → 人間承認 → atomic push → Release 公開
```

## 制約・前提条件

- **OUT scope（Issue スコープ境界）**: 本 Issue では `kaji-starter-python` の実ファイル変更、`v0.12.1 -> v0.15.0` の実同期、実 starter への skill 適用テスト、starter 側 push / tag / GitHub Release 作成、consumer への自動 update 機構を行わない
- skill markdown は既存静的ゲート（`tests/test_skill_migration.py`）により `gh issue` / `gh pr` / `gh api` へ言及できない。Issue 操作は `kaji issue`（内部で gh へ委譲）、Release 操作は `gh release`（許容語彙）を使う
- verdict は共通 schema（`status / reason / evidence / suggestion`）のまま。新 status / field は追加しない。skill 間の機械契約（review PASS と target / base / candidate SHA の検証）は SKILL.md 散文ではなく CLI / harness 層に置く（ADR 008 決定 3）: verdict マーカー文法への meta 拡張 + `kaji issue resolve-verdict`（最新 verdict 選択・fail-closed）で実現する（「方針 § cross-skill 契約」）。step 識別子は `^[a-z][a-z0-9_-]*$`（`kaji_harness/providers/markers.py`）に適合済み
- 3 skill は言語非依存。manifest / lockfile / quality gate は対象 repository の実体から解決する。managed starter の一覧は starter sync runbook の表を正本とする
- starter 内 `AGENTS.md` は consumer 向け payload。quality gate 等の実体確認には読むが、maintainer の commit / push 方針には適用しない（distribution 運用の正本は kaji 側 runbook）
- bootstrap（現 starter main の `kaji-v0.12.1` tag + Release 固定）は Issue close 後の有人 handoff。通常 skill に初回 mode / fallback を追加せず、公開済み starter Release 不在は ABORT とする
- skill 本体は責務・必須不変条件・実行順・読込 timing に絞り、詳細手順 / rubric / Release notes 雛形は `references/` / `templates/` に遅延読込として分離する（`docs/dev/skill-authoring.md` § 段階的開示と遅延読込）
- カノニカル skill は `.claude/skills/`（`paths.skill_dir`）、`.agents/skills/` に symlink を置く

## 変更スコープ

| 種別 | パス | 変更内容 |
|------|------|----------|
| 新規 skill | `.claude/skills/update-starter/SKILL.md` + `references/classification-guide.md` | 分類・調査・local main commit の契約。3 区分判定基準は reference へ分離 |
| 新規 skill | `.claude/skills/review-starter-update/SKILL.md` + `references/review-rubric.md` | 独立検証・無修正原則・SHA 固定。レビュー rubric は reference へ分離 |
| 新規 skill | `.claude/skills/release-starter/SKILL.md` + `references/preflight-and-recovery.md` + `templates/release-notes.md` | pre-flight・人間承認・atomic push・N/A 経路・部分失敗復旧。Release notes 雛形は template へ分離 |
| 既存 skill 更新 | `.claude/skills/release/SKILL.md` | Step 7 notes への状態表追加、Step 8 handoff（tracking Issue 作成 → 状態表リンク → `/update-starter` 案内）、rollback 分離契約 |
| symlink | `.agents/skills/{update-starter,review-starter-update,release-starter}` | カノニカルへの symlink 追加 |
| 新規 docs | `docs/operations/release/starter-sync-runbook.md` | starter sync / review / release 運用の正本。managed starters 表（正本）、tracking Issue テンプレート、`kaji-v0.12.1` bootstrap の一度限り有人手順、follow-up（実適用テストは別 Issue）明記 |
| 既存 docs 更新 | `docs/operations/release/runbook.md` | starter sync runbook への連携節追加 |
| 既存 docs 更新 | `docs/README.md` | 索引へ starter sync runbook を追加 |
| 既存 docs 更新 | `CLAUDE.md` | Development Skills 表へ starter 追随フェーズ（3 skill）を追加 |
| 実行時コード | `kaji_harness/providers/markers.py` | verdict マーカー文法へ任意 meta（`key=value`）を後方互換で追加（build / parse とも fail-loud / fail-closed） |
| 実行時コード | `kaji_harness/commands/issue.py` ほか CLI 面 | `kaji issue comment --verdict-meta`（producer）と `kaji issue resolve-verdict`（consumer / 最新 verdict 選択）を追加 |
| 実行時コード | `kaji_harness/starter_release.py` + CLI 面 | `kaji starter release-plan`（重複 Release / 部分成功の決定表の純粋関数実装） |
| 新規テスト | `tests/test_starter_skills.py` / `tests/test_verdict_marker_meta.py` / `tests/test_resolve_verdict.py` / `tests/test_starter_release_plan.py` | skill / docs 静的検証と新規機械契約の回帰テスト（詳細はテスト戦略） |

`kaji_harness/` の実行時変更は上記 3 点（marker meta 文法 / issue comment・resolve-verdict / starter release-plan）に限定する。既存 workflow（dev / docs 等）の marker 発行・消費経路（`issue-design` Step 1.6 の BACK 検出等）は変更しない。層方向・module 境界は既存 fitness test（`tests/test_layer_imports.py` / ADR 009）に従う。

## 方針

処理フローの全体像（minimal how）:

```text
kaji /release（既存・変更）
  Step 7: Release notes に repository 別状態表（全行 PENDING）
  Step 8: managed starter ごとに tracking Issue 作成 → 状態表へリンク → /update-starter を案内
      ↓
/update-starter <id>
  tracking Issue 読取 → local checkout 解決 + remote identity 検証
  → 開始点 = 最新公開 starter Release tag（不在/矛盾は ABORT、古い PENDING も ABORT）
  → 変更全件を 3 区分に分類 → 区分(1) を local main へ直接 commit（未 push）
  → quality gate → 分類報告コメント（base SHA / candidate SHA）→ verdict
      ↓（別 session）
/review-starter-update <id>
  一次情報（tag / CHANGELOG / diff）から独立に再構成
  → 完全性・過剰コピー・cleanliness・検証証跡を判定（無修正）
  → PASS|RETRY|ABORT コメント（target tag / base SHA / candidate SHA を evidence に固定）
      ↓（PASS）
/release-starter <id>
  pre-flight（resolve-verdict による最新 PASS + meta 照合 → release-plan 決定表 → 経路別鮮度検証）
  → 人間の明示承認（workflow 外・ref push 経路のみ）
  → git push --atomic <remote> main kaji-vX.Y.Z[-rN] → gh release create（template）
  → kaji Release 状態表 PENDING->PASS 更新 → tracking Issue close
  （N/A 経路: review PASS（base == candidate）後、tag なしで状態表 N/A 更新 + close）
```

- 3 skill は `/release` と同じ「maintainer 手元実行・人間承認を挟む」skill として書く。SKILL.md には verdict 出力規約（3 経路）と guardrail 節を必ず置く
- review PASS の機械検出は下記「cross-skill 契約」の resolver に一本化し、コメント本文散文への機械依存を作らない（`evidence` 内の SHA は人間可読の証跡として保持する。Issue `## 決定事項` § verdict 契約）
- 各 skill の詳細手順（分類の判定基準、rubric、復旧手順）は references/ へ分離し、SKILL.md の該当 Step に「この時点で初めて Read する」旨を明記する

### cross-skill 契約（verdict マーカー meta と resolver）

skill 間で機械的に消費する契約は、以下のとおり CLI / harness 層のコードに置く（ADR 008 決定 3。`docs/dev/skill-authoring.md` § cross-skill 契約）。

1. **マーカー文法の meta 拡張**（`kaji_harness/providers/markers.py`）:
   `<!-- kaji-verdict: step=<step> status=<status>( <key>=<value>)* -->`
   - key: `^[a-z][a-z0-9_]*$`、value: `^[A-Za-z0-9][A-Za-z0-9._/-]*$`（空白・`-->` を構文上含められない）。build 時の文法違反は `ValueError` で fail-loud
   - meta なしの既存 marker は文法上そのまま有効（後方互換）。**meta 付き marker を発行するのは starter 系 3 skill のみ** とし、既存 step（design / review-code 等）の marker 形式・既存 consumer（`issue-design` Step 1.6 の BACK 検出 regex）は変更しない
2. **producer**: `kaji issue comment ... --verdict-step <step> --verdict-status <STATUS> --verdict-meta key=value`（繰返し指定可。`--verdict-step/--verdict-status` との併用必須）。CLI が marker 1 行目へ決定的に埋め込む
3. **consumer**: `kaji issue resolve-verdict <issue_id> --step <step> [--require-meta <key>]...`
   - provider 中立に対象 Issue の全コメントを走査し、body 1 行目 marker の step が一致するコメントのうち **最新 1 件** を選択して `{step, status, meta, created_at}` の JSON を stdout へ出力する（後続 verdict が常に先行 verdict を上書きする = stale PASS の構造的排除）
   - 未検出 / 最新 marker の不正形式 / `--require-meta` 指定 key の欠落は、それぞれ区別可能な非 0 終了コードで fail-closed（消費側はすべて「公開不可」として扱う）
   - 「最新が PASS でない」は resolver の正常出力（`status` フィールド）であり、公開可否の判定（PASS + 値一致）は消費側 skill が JSON の構造化フィールドの等値比較で行う
4. **release-plan**: `kaji starter release-plan` — 観測状態 JSON（stdin: 既存 tag 群と SHA / Release 有無 / 状態表の行状態 / Issue state / candidate SHA）→ 決定 JSON（stdout: 経路番号・採番 tag 名・残処理リスト・ABORT 理由）の純粋決定関数（`kaji_harness/starter_release.py`）。「重複 Release と部分成功の状態遷移」の決定表を実装する

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| starter の所有モデル・tracking 単位 | 全作業を kaji Issue で管理。kaji Release ごと・starter ごとに tracking Issue 1 件 | Issue `## 決定事項`（`/grill-me 341` 人間回答） | tracking Issue の必須 field（`starter_repo` / `target_kaji_release` / `starter_path`）とタイトル形式を定義 |
| kaji Release の状態正本 | Release 本文の repository 別状態表。単一集約 status なし | Issue `## 決定事項` | 表の列構成（repository / status / tracking Issue / starter Release・N/A 理由）と、`/release` Step 7 での初期行生成・Step 8 でのリンク反映という更新順序を定義 |
| update の Git フロー | local main 直接 commit・review 前 push 禁止・branch/PR/merge 不使用 | Issue `## 決定事項` | ff 同期不能時の ABORT を明示 |
| 独立 review と PASS 失効 | 別 session・target tag / base SHA / candidate SHA へ固定 | Issue `## 決定事項` | release-starter の pre-flight を `kaji issue resolve-verdict` による「**最新** verdict の PASS + meta（target / base / candidate）等値照合」として機械化。後続 RETRY / ABORT が最新選択で過去 PASS を無効化し、未検出・不正形式・欠落・不一致はすべて fail-closed |
| verdict 契約 | 共通 schema 不変。SHA は evidence 内の必須証跡 | Issue `## 決定事項` | verdict schema には手を入れず、機械消費用 SHA を marker meta（CLI 保証・文法検証つき）に、人間可読の証跡を `evidence` 散文に併置する二重化として詳細化（ADR 008 決定 3 準拠） |
| `N/A` gate / 公開 gate / 再 release / tag 契約 | N/A も review PASS 必須。人間承認後のみ atomic push。`-rN` は candidate SHA が変わる場合のみ。利用者導線は `Use this template` のみ | Issue `## 決定事項` | 重複 release / 部分成功 / 完了済み再実行 / 旧 revision / 観測矛盾の全状態を 5 分岐 + 残処理 4 分岐の決定表として定式化し、`kaji starter release-plan` 純粋関数で実装。「GitHub Release 再試行では新 tag を発行しない」を決定 2 の不変条件として固定。人間承認は ref push に対する gate とし、push を伴わない残処理は再承認不要と詳細化 |
| sync 開始点 / backlog | 最新公開 starter Release tag のみ正本。矛盾・Release 不在・古い PENDING は ABORT | Issue `## 決定事項` | 「古い PENDING」の判定を kaji GitHub Release 状態表（状態正本）の走査として具体化 |
| bootstrap / starter Issues 無効化 | Issue close 後の有人 handoff。headless gate に含めない | Issue `## 決定事項` | bootstrap 手順を starter-sync-runbook.md の一度限りセクションとして文書化（skill には含めない） |
| local checkout の標準配置 | sibling `../<repo-name>` + remote identity 検証。例外は Issue の `starter_path` | AI の仮定（Issue `## 決定事項` § AI の仮定に記録済み。現行配置が根拠）。review-design / review-code で検査 | path 解決順序（`starter_path` > sibling 既定）と identity 不一致 ABORT を定義 |
| `N/A` 後の content baseline | 最後に公開された starter Release tag。次回は安全側に再分類 | AI の仮定（同上。omission 防止側に倒れる）。review-design で不要な cursor 導入がないか検査 | update-starter の開始点規則を「公開 Release tag のみ」に一本化（N/A 用の特別 status / cursor を導入しない） |
| atomic push 後の部分失敗 | tag rollback せず同じ tag の不足処理のみ再試行 | AI の仮定（同上。tag 不変・force push 禁止に整合）。review-code で検査 | 再試行対象（Release ページ / 状態表更新 / Issue close）を列挙し、状態表 `PENDING` 維持 + 復旧手順報告として定義 |
| tracking Issue の作成主体 | `/release` Step 8 の handoff で maintainer が作成（`kaji issue create`） | AI の仮定。skill 入力が tracking Issue ID である以上、update-starter 起動前に存在が必要。review-design で検査 | 新規ラベルは追加せず、タイトル prefix `[starter-sync]:` で識別（labels.yml への影響を避ける） |
| CLI 公開面の追加（`--verdict-meta` / `kaji issue resolve-verdict` / `kaji starter release-plan`） | maintainer 向け内部 CLI として追加する | AI の仮定。review-design の Must Fix 指摘（機械消費契約を CLI/harness 層に置く = ADR 008 決定 3 の既存人間決定への準拠）が根拠。kaji は後方互換層を持たず（ADR 008）CHANGELOG の 3 要素で破壊的変更を伝達できるため、命名・引数の誤りは後段で安く直せる two-way door。review-design（本修正の再確認）/ review-code で検査 | marker meta 文法・resolver の選択規則と終了コード契約・release-plan の入出力 JSON を「方針 § cross-skill 契約」に定義。既存 workflow の marker 経路は不変 |
| runbook / skill のファイル配置 | `docs/operations/release/starter-sync-runbook.md`、skill は references / templates 分離 | `docs/dev/skill-authoring.md` § 段階的開示（既存契約）と Issue 完了条件 | 変更スコープ表のとおり具体化 |

## テスト戦略

### 変更タイプ

**実行時コード変更あり + skill / docs 資産の追加**。実行時変更は cross-skill 契約の 3 点（marker meta 文法 / `kaji issue comment --verdict-meta`・`kaji issue resolve-verdict` / `kaji starter release-plan`）に限定する。テストサイズは `docs/dev/testing-convention.md` の定義（外部依存なし = Small / ファイル I/O・内部結合 = Medium / 実 API・subprocess = Large）に従って分類する。

### Small テスト（`@pytest.mark.small` — 外部依存なし・純粋ロジック）

新規機械契約の中核不変条件を関数レベルで検証する:

- `markers.py` meta 拡張: meta 付き marker の build（key / value の文法違反は `ValueError` で fail-loud）、parse（正常 / meta なし後方互換 / 不正形式の fail-closed）、既存 marker 形式との相互非干渉
- verdict 選択ロジック（in-memory のコメント列に対する純粋関数）: 同一 step へ PASS → RETRY の順で投稿された場合に **最新の RETRY** が選ばれる（stale PASS 排除）/ 対象 step の marker 0 件 / 最新 marker の不正形式 / `--require-meta` 指定 key の欠落、の各 fail-closed 挙動
- 公開可否の等値照合テーブル（pre-flight 項目 1・7 の判定規則を純粋関数として検証。各不一致が **それぞれ単独で** 公開不可判定になることを 1 ケースずつ確認する）:

  | ケース | 期待判定 |
  |--------|----------|
  | `meta.candidate != starter local main HEAD`（review PASS 後に candidate SHA が変わった） | 公開不可（PASS 失効） |
  | `meta.target != target_kaji_release`（別 kaji version 向け review PASS の流用） | 公開不可 |
  | 未公開経路（決定 1 / 3）で `starter remote main != meta.base`（review 以降に base が進んだ） | 公開不可 |
  | 公開済み残処理経路（決定 2）で `starter remote main != meta.candidate`（push 整合の破れ） | 続行不可 |
  | 最新 verdict の `status != PASS` | 公開不可 |
  | 全項目一致（正常系） | 公開可 |
- `release-plan` 決定表の全分岐: tag 0 件 → `kaji-vX.Y.Z` 採番 / latest SHA 一致 → 既存 tag 再利用 + 残処理 4 分岐（(a)(b)(c) の各欠落組合せと全完了 idempotent PASS）/ SHA 不一致 → `-r(maxN+1)` 採番（既存 `-rN` 複数時に最大 N + 1 となること）/ 旧 revision の SHA 一致 → ABORT / tag・Release・状態表の観測矛盾 → ABORT
- N/A 判定（`meta.base == meta.candidate`）と、未公開 / 公開済み経路の鮮度検証規則（remote main と meta.base / meta.candidate の照合ロジック）

### Medium テスト（`@pytest.mark.medium` — ファイル I/O・内部結合）

- CLI 結合: `kaji issue comment --verdict-meta` が local provider fixture 上でコメント 1 行目に meta 付き marker を永続化する / `kaji issue resolve-verdict` が PASS → RETRY 投稿後に RETRY を返し、未検出・不正形式で区別された非 0 終了コードを返す（fail-closed の結合検証）
- skill / docs 資産の静的検証（**実ファイル読取のため Medium に分類**。`docs/dev/testing-convention.md` のサイズ定義に従う）:
  - 3 SKILL.md の frontmatter（`name` がディレクトリ名と一致・`description` 非空）と verdict status 語彙（update / release: `PASS | ABORT`、review: `PASS | RETRY | ABORT`）
  - **主要不変条件の欠落検知**（Issue の機能契約が SKILL.md から欠落したら失敗する観点）: update の「全件 3 区分分類」と「dependency / lockfile 更新だけで完了と判定しない」/ review の「無修正原則」「別 session 実行」/「review 前に push しない」/ N/A gate（独立 review PASS 必須）/ 人間承認 gate（ref push 前）/ `git push --atomic` と force push・tag 上書き禁止 / 部分成功復旧（決定表参照の明記）/ pre-flight での `kaji issue resolve-verdict` 使用の明記 / tag 命名 `kaji-vX.Y.Z` / `kaji-vX.Y.Z-rN` / 3 skill 自身を starter へコピーしない
  - `templates/release-notes.md` の必須セクション（対応 kaji Release / 反映内容 / N/A と理由 / BREAKING 対応 / 検証 evidence / snapshot 利用方法）
  - `.agents/skills/{update-starter,review-starter-update,release-starter}` symlink のカノニカル解決、`docs/operations/release/starter-sync-runbook.md` の存在と managed starters 表・bootstrap・follow-up 明記、相互参照（runbook.md / docs/README.md / release SKILL.md）
  - 既存の repo 横断ゲート（`test_skill_migration.py` の `gh issue|pr|api` 禁止等）は `.claude/skills/` を rglob 走査するため新規 3 skill を自動包含する。同種テストは重複追加しない

### Large テスト（`@pytest.mark.large` + `large_local` 細分 — subprocess あり / ネットワークなし）

- `kaji issue resolve-verdict` / `kaji starter release-plan` の CLI dispatcher 到達と終了コード契約を実 subprocess で最小本数検証する（既存 `test_exec_script_subprocess_large.py` / `test_local_cli_large_local.py` と同型。`docs/reference/testing-size-guide.md` の `large_local` = subprocess あり / ネットワーク無し）
- **実 GitHub API / 実 starter repository への疎通テストは追加しない**: Issue の検証境界で OUT scope（「本 Issue 内で実施しない検証」）。4 条件: (1) 疎通部分は gh / git の既存コマンド組合せで独自ロジックがなく、判定ロジックは S/M/L(large_local) で網羅 (2) 想定不具合（stale PASS・重複 tag・部分失敗の誤分岐）は決定表・resolver の回帰テストで捕捉 (3) 未 bootstrap の実 starter への疎通テストは回帰検出情報を増やさない (4) 省略理由を本設計書と runbook に記録。実適用の検証は Issue close 後の follow-up Issue（`v0.12.1 -> v0.15.0`）で行う

### 変更固有検証

- `make verify-docs` — 新規 runbook / 索引 / skill 間リンクの整合
- `source .venv/bin/activate && make check` — 品質ゲート（新規テスト含む全体回帰）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定（ライブラリ採用）はない。cross-skill 契約の配置は既存 ADR 008 決定 3 への準拠であり新規 ADR は不要。運用設計の正本は runbook と Issue `## 決定事項` に記録される |
| docs/ARCHITECTURE.md | なし | 既存レイヤ構成（commands / providers / foundation）内の subcommand・関数追加であり、アーキテクチャと verdict 3 経路解決機構は不変 |
| docs/operations/release/ | **あり** | `starter-sync-runbook.md` 新設（正本）、既存 `runbook.md` に連携節追加 |
| docs/README.md | **あり** | 索引へ starter sync runbook を追加 |
| docs/dev/ | **あり** | `shared_skill_rules.md` § verdict マーカー契約（producer / consumer）へ meta 拡張と `kaji issue resolve-verdict` を正本として追記 |
| docs/reference/ | なし | API 仕様・コーディング規約への影響なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| AGENTS.md / CLAUDE.md | **あり** | CLAUDE.md の Development Skills 表へ starter 追随フェーズを追加（AGENTS.md は変更不要: ルーティング変更なし） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #341 本文 `## 決定事項` + grill-me provenance コメント | https://github.com/apokamo/kaji/issues/341 | 人間確定方針の正本（所有モデル / Git フロー / 独立 review / 公開 gate / tag 命名 / bootstrap 等 17 項目）。本設計はこの範囲内の詳細化のみを行う |
| 現行 release skill | `.claude/skills/release/SKILL.md` | Step 6「main と tag を 1 トランザクションで push（`git push --atomic`）」、Step 7 失敗時「rollback ではなく Release ページのみ再試行」、force push 絶対禁止 — release-starter の公開・復旧設計は同型を踏襲 |
| Release Runbook | `docs/operations/release/runbook.md` | 既存 release フロー全体像と handoff 追加位置（完了報告 / consumer 案内）の確認 |
| Skill 作成マニュアル | `docs/dev/skill-authoring.md` | 「SKILL.md は責務・必須不変条件・実行順・読込 timing に絞る」「references/ / templates/ へ遅延読込分離」「verdict 3 経路出力」「cross-skill 契約は CLI / harness 層に置く（ADR 008 決定 3）」 |
| verdict マーカー実装 | `kaji_harness/providers/markers.py` | step 識別子は `^[a-z][a-z0-9_-]*$`、status は `PASS/RETRY/ABORT/BACK/BACK_*`、build は語彙違反で `ValueError`（fail-loud）— `review-starter-update` step 名の適合を確認。本設計はこの文法へ任意 meta（`key=value`）を後方互換で拡張する |
| verdict マーカー契約の正本 | `docs/dev/shared_skill_rules.md` § verdict マーカー契約（producer / consumer） | producer / consumer 契約の運用正本。meta 拡張と resolver を本 Issue で同期追記する |
| テストサイズ規約 | `docs/dev/testing-convention.md` / `docs/reference/testing-size-guide.md` | 「外部依存なし = Small / ファイル I/O・内部結合 = Medium / 実 API = Large、`large_local` = subprocess あり・ネットワーク無し」— skill 静的検証の Medium 分類と CLI subprocess テストの large_local 分類の根拠 |
| kaji v0.13.0 BREAKING CHANGE | `CHANGELOG.md`（https://github.com/apokamo/kaji/blob/main/CHANGELOG.md#0130---2026-07-09 ） | BREAKING エントリは「壊れる契約 / 影響の判定方法 / 適用指針」3 要素を持つ — update-starter の分類調査は CHANGELOG のこの構造を入力にできる |
| kaji v0.15.0 Release | https://github.com/apokamo/kaji/releases/tag/v0.15.0 | 追随対象の最新公開 release（starter pin `v0.12.1` との乖離の実例） |
| starter dependency pin | https://github.com/apokamo/kaji-starter-python/blob/main/pyproject.toml | 現況 `v0.12.1` pin。pin は開始点の正本ではなく整合検査対象（矛盾時 ABORT）とする根拠 |
| starter repository | https://github.com/apokamo/kaji-starter-python | 2026-07-15 確認時点で tag / GitHub Release 0 件 → bootstrap 前は通常 sync が ABORT になる前提の確認 |
| GitHub template repository 仕様 | https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template | template 生成は default branch の snapshot から行われる — 利用者導線を `Use this template` のみとし、tag を保守・監査 marker とする決定と整合 |
| git push --atomic | https://git-scm.com/docs/git-push#Documentation/git-push.txt---atomic | 「Use an atomic transaction on the remote side if available. Either all refs are updated, or on error, no refs are updated.」— main + tag の部分反映を防ぐ根拠 |
| 既存 skill 静的ゲート | `tests/test_skill_migration.py` / `tests/test_skill_remote_placeholder.py` | skill markdown の決定的静的検証の既存 precedent（rglob 走査は新 skill を自動包含。`gh issue|pr|api` 禁止語彙の確認） |
