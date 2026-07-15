# [設計] starter 追随・レビュー・リリース skills を追加する

Issue: #341

## 概要

kaji 本体の release 後に `kaji-starter-python` 等の managed starter を追随更新し、独立レビューを経て、対応する kaji version の検証済み template snapshot として tag / GitHub Release を公開するための maintainer 用 3 skill（`update-starter` / `review-starter-update` / `release-starter`）と、その正本 runbook・`/release` からの post-release handoff を追加する。

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
- **verdict**: `PASS | ABORT`（`kaji issue comment --verdict-step update-starter --verdict-status <STATUS>` を無条件付与）
- **エラー（ABORT 条件）**: 公開済み starter Release 不在 / tag・Release・pin の矛盾 / 古い `PENDING` の存在 / remote identity 不一致 / local main が remote main へ ff 同期不能 / quality gate が対象 repository から解決不能

### `review-starter-update`

- **入力**: `/review-starter-update <tracking_issue_id>`。update を行った session とは **別 session** で実行する
- **処理の契約**:
  - updater の分類結果を正解とせず、対象 kaji tag / CHANGELOG / upstream diff から独立に証拠を再構成する
  - 変更ごとの 3 区分が全件埋まり、必要な移植が過不足なく実施され、dependency source / lockfile / docs / template cleanliness（maintainer 資産の混入なし）/ validation evidence が整合するかを確認する
  - review は「対象 kaji tag / remote main の base SHA / local main の candidate SHA」に固定する。candidate SHA が変われば PASS は失効し、再 review 必須
  - review 中に更新ファイルを **修正しない**（無修正原則）
- **出力**: tracking Issue へのレビューコメント。`--verdict-step review-starter-update --verdict-status <STATUS>` マーカーを無条件付与し、`evidence` に target tag / base SHA / candidate SHA を必須証跡として含める（新 status / field は追加しない。Issue `## 決定事項` § verdict 契約）
- **verdict**: `PASS | RETRY | ABORT`。RETRY の指摘対応は maintainer が `/update-starter` の再実行または手動修正で行い、candidate SHA が変わるため再 review する

### `release-starter`

- **入力**: `/release-starter <tracking_issue_id>`
- **pre-flight（全件成功が公開の前提）**:
  1. tracking Issue に現 candidate SHA と一致する `review-starter-update` の PASS マーカーコメントが存在する
  2. starter local main が clean、かつ base SHA（remote main）が review 時点から不変
  3. 対象 kaji Release（`target_kaji_release`）が published 状態
  4. starter の dependency pin / lockfile が対象 kaji version と整合
  5. starter の quality gate 通過
  6. 重複 release 検出: 同一 kaji version の公開済み tag があり candidate SHA が異なる場合は `-rN`（`N` = 既存最大値 + 1）を採番。同一 SHA で Release ページのみ欠けている場合は **新 tag を発行せず** Release 作成のみ再試行
- **人間承認 gate**: pre-flight 成功後、candidate SHA と tag 名を提示し、**workflow 外で人間の明示承認** を得てからのみ公開へ進む
- **公開**: annotated tag（初回 `kaji-vX.Y.Z` / 再 release `kaji-vX.Y.Z-rN`）と starter `main` を `git push --atomic` で 1 トランザクション push → template から生成した GitHub Release notes で `gh release create` → 対応 kaji Release 本文の repository 別状態表の該当行を `PENDING -> PASS` に更新（starter Release をリンク）→ tracking Issue を close
- **N/A 経路**: starter 変更不要の判定にも独立 review PASS を必須とし、PASS 後にのみ kaji Release 状態表の該当行を `N/A` と根拠付きで更新して tracking Issue を close する。starter tag / Release は作らない
- **失敗時**: atomic push 後の部分失敗（Release ページ / kaji Release 状態表 / Issue close の失敗）は公開済み tag を rollback せず、同じ tag に対する不足処理のみ再試行する。kaji 本体 release も rollback しない。状態表の該当行は `PENDING` のまま復旧手順を報告する。**force push / tag 上書きは禁止**
- **verdict**: `PASS | ABORT`（`--verdict-step release-starter` マーカー付与）

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
- verdict は共通 schema（`status / reason / evidence / suggestion`）のまま。新 status / field は追加しない。skill 間の機械契約（review PASS の検出）は CLI の verdict マーカー（`kaji issue comment --verdict-step/--verdict-status`、ADR 008 決定 3）に置く。step 識別子は `^[a-z][a-z0-9_-]*$`（`kaji_harness/providers/markers.py`）に適合済み
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
| 新規テスト | `tests/test_starter_skills.py` | 静的・決定的検証（詳細はテスト戦略） |

`kaji_harness/` の実行時コードは変更しない。

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
  pre-flight 6 項目 → 人間の明示承認（workflow 外）
  → git push --atomic <remote> main kaji-vX.Y.Z[-rN] → gh release create（template）
  → kaji Release 状態表 PENDING->PASS 更新 → tracking Issue close
  （N/A 経路: review PASS 後、tag なしで状態表 N/A 更新 + close）
```

- 3 skill は `/release` と同じ「maintainer 手元実行・人間承認を挟む」skill として書く。SKILL.md には verdict 出力規約（3 経路）と guardrail 節を必ず置く
- release PASS の機械検出は verdict マーカー 1 行目照合（`<!-- kaji-verdict: step=review-starter-update status=PASS -->`）+ コメント本文の candidate SHA 照合で行い、散文 regex への依存を作らない
- 各 skill の詳細手順（分類の判定基準、rubric、復旧手順）は references/ へ分離し、SKILL.md の該当 Step に「この時点で初めて Read する」旨を明記する

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| starter の所有モデル・tracking 単位 | 全作業を kaji Issue で管理。kaji Release ごと・starter ごとに tracking Issue 1 件 | Issue `## 決定事項`（`/grill-me 341` 人間回答） | tracking Issue の必須 field（`starter_repo` / `target_kaji_release` / `starter_path`）とタイトル形式を定義 |
| kaji Release の状態正本 | Release 本文の repository 別状態表。単一集約 status なし | Issue `## 決定事項` | 表の列構成（repository / status / tracking Issue / starter Release・N/A 理由）と、`/release` Step 7 での初期行生成・Step 8 でのリンク反映という更新順序を定義 |
| update の Git フロー | local main 直接 commit・review 前 push 禁止・branch/PR/merge 不使用 | Issue `## 決定事項` | ff 同期不能時の ABORT を明示 |
| 独立 review と PASS 失効 | 別 session・target tag / base SHA / candidate SHA へ固定 | Issue `## 決定事項` | release-starter の pre-flight で「マーカー PASS + candidate SHA 一致」を機械照合する形へ具体化 |
| verdict 契約 | 共通 schema 不変。SHA は evidence 内の必須証跡 | Issue `## 決定事項` | review PASS 検出を CLI verdict マーカー（ADR 008 決定 3）に置き、step 語彙 `^[a-z][a-z0-9_-]*$` 適合を `markers.py` で確認済み |
| `N/A` gate / 公開 gate / 再 release / tag 契約 | N/A も review PASS 必須。人間承認後のみ atomic push。`-rN` は candidate SHA が変わる場合のみ。利用者導線は `Use this template` のみ | Issue `## 決定事項` | 重複 release 検出の分岐（同一 SHA なら Release 作成のみ再試行 / SHA 差異なら `-rN` 採番）を pre-flight 項目 6 として定式化 |
| sync 開始点 / backlog | 最新公開 starter Release tag のみ正本。矛盾・Release 不在・古い PENDING は ABORT | Issue `## 決定事項` | 「古い PENDING」の判定を kaji GitHub Release 状態表（状態正本）の走査として具体化 |
| bootstrap / starter Issues 無効化 | Issue close 後の有人 handoff。headless gate に含めない | Issue `## 決定事項` | bootstrap 手順を starter-sync-runbook.md の一度限りセクションとして文書化（skill には含めない） |
| local checkout の標準配置 | sibling `../<repo-name>` + remote identity 検証。例外は Issue の `starter_path` | AI の仮定（Issue `## 決定事項` § AI の仮定に記録済み。現行配置が根拠）。review-design / review-code で検査 | path 解決順序（`starter_path` > sibling 既定）と identity 不一致 ABORT を定義 |
| `N/A` 後の content baseline | 最後に公開された starter Release tag。次回は安全側に再分類 | AI の仮定（同上。omission 防止側に倒れる）。review-design で不要な cursor 導入がないか検査 | update-starter の開始点規則を「公開 Release tag のみ」に一本化（N/A 用の特別 status / cursor を導入しない） |
| atomic push 後の部分失敗 | tag rollback せず同じ tag の不足処理のみ再試行 | AI の仮定（同上。tag 不変・force push 禁止に整合）。review-code で検査 | 再試行対象（Release ページ / 状態表更新 / Issue close）を列挙し、状態表 `PENDING` 維持 + 復旧手順報告として定義 |
| tracking Issue の作成主体 | `/release` Step 8 の handoff で maintainer が作成（`kaji issue create`） | AI の仮定。skill 入力が tracking Issue ID である以上、update-starter 起動前に存在が必要。review-design で検査 | 新規ラベルは追加せず、タイトル prefix `[starter-sync]:` で識別（labels.yml への影響を避ける） |
| runbook / skill のファイル配置 | `docs/operations/release/starter-sync-runbook.md`、skill は references / templates 分離 | `docs/dev/skill-authoring.md` § 段階的開示（既存契約）と Issue 完了条件 | 変更スコープ表のとおり具体化 |

## テスト戦略

### 変更タイプ

**実行時コード変更なし**（skill / docs 資産の追加 + 静的検証テストの追加）。`kaji_harness/` は変更しない。ただし Issue 完了条件が「実 external repository を変更しない静的 / 決定的検証を S/M/L 分類で整理する」ことを要求しており、skill 契約は将来の変更で回帰しうる（既存 precedent: `tests/test_skill_migration.py` / `tests/test_skill_remote_placeholder.py`）ため、恒久回帰テストを追加する。

### Small テスト（`@pytest.mark.small`）

特定ファイルの内容を決定的に検証する（外部依存なし・repo 内ファイル読取のみ。`test_skill_remote_placeholder.py` と同分類）:

- 3 SKILL.md の frontmatter: `name` がディレクトリ名と一致し、`description` が非空である
- 3 SKILL.md に verdict 出力節（`---VERDICT---` block）が存在し、status 語彙が設計どおりである（update/release: `PASS | ABORT`、review: `PASS | RETRY | ABORT`）
- `release-starter/SKILL.md` に tag 命名規則（`kaji-vX.Y.Z` / `kaji-vX.Y.Z-rN`）、`git push --atomic`、annotated tag の記載が存在する
- `templates/release-notes.md` に必須セクション（対応 kaji Release / 反映内容 / N/A と理由 / BREAKING 対応 / 検証 evidence / snapshot 利用方法）が存在する
- `review-starter-update/SKILL.md` に target tag / base SHA / candidate SHA の evidence 必須証跡の記載が存在する

### Medium テスト（`@pytest.mark.medium`）

ファイルシステム構造・repo 横断整合を検証する（`test_skill_migration.py` と同分類）:

- `.agents/skills/{update-starter,review-starter-update,release-starter}` symlink がカノニカル skill ディレクトリへ解決される
- `docs/operations/release/starter-sync-runbook.md` が存在し、managed starters 表・bootstrap セクション・follow-up（実適用テストは別 Issue）明記を含む
- `docs/operations/release/runbook.md` / `docs/README.md` / `.claude/skills/release/SKILL.md` から starter sync runbook（または handoff）への参照が存在する
- 既存の repo 横断ゲート（`test_skill_migration.py` の `gh issue|pr|api` 禁止・legacy placeholder 禁止）は `.claude/skills/` を rglob 走査するため、新規 3 skill を **自動的に検査対象へ含む**。同種のテストは重複追加しない

### Large テスト

**不要**。実 starter repository / 実 GitHub API への疎通は Issue の検証境界で OUT scope と明示されており（「本 Issue 内で実施しない検証」）、実適用テストは Issue close 後の follow-up Issue（`v0.12.1 -> v0.15.0`）で行う。恒久テスト不追加の 4 条件: (1) 実行時ロジックの追加なし (2) skill 静的契約は上記 S/M で捕捉 (3) 実 API テストを本 Issue で追加しても対象 repository 未 bootstrap のため検証情報が増えない (4) 本設計書と runbook に省略理由を記録済み。

### 変更固有検証

- `make verify-docs` — 新規 runbook / 索引 / skill 間リンクの整合
- `source .venv/bin/activate && make check` — 品質ゲート（新規テスト含む全体回帰）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定（ライブラリ・アーキテクチャ変更）はない。運用設計の正本は runbook と Issue `## 決定事項` に記録される |
| docs/ARCHITECTURE.md | なし | harness / CLI の実行時変更なし |
| docs/operations/release/ | **あり** | `starter-sync-runbook.md` 新設（正本）、既存 `runbook.md` に連携節追加 |
| docs/README.md | **あり** | 索引へ starter sync runbook を追加 |
| docs/dev/ | なし | 開発 workflow・skill-authoring 規約自体は変更しない（新 skill は既存規約に従うのみ） |
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
| verdict マーカー実装 | `kaji_harness/providers/markers.py` | step 識別子は `^[a-z][a-z0-9_-]*$`、status は `PASS/RETRY/ABORT/BACK/BACK_*` — `review-starter-update` step 名と PASS 検出設計が語彙に適合することを確認 |
| kaji v0.13.0 BREAKING CHANGE | `CHANGELOG.md`（https://github.com/apokamo/kaji/blob/main/CHANGELOG.md#0130---2026-07-09 ） | BREAKING エントリは「壊れる契約 / 影響の判定方法 / 適用指針」3 要素を持つ — update-starter の分類調査は CHANGELOG のこの構造を入力にできる |
| kaji v0.15.0 Release | https://github.com/apokamo/kaji/releases/tag/v0.15.0 | 追随対象の最新公開 release（starter pin `v0.12.1` との乖離の実例） |
| starter dependency pin | https://github.com/apokamo/kaji-starter-python/blob/main/pyproject.toml | 現況 `v0.12.1` pin。pin は開始点の正本ではなく整合検査対象（矛盾時 ABORT）とする根拠 |
| starter repository | https://github.com/apokamo/kaji-starter-python | 2026-07-15 確認時点で tag / GitHub Release 0 件 → bootstrap 前は通常 sync が ABORT になる前提の確認 |
| GitHub template repository 仕様 | https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template | template 生成は default branch の snapshot から行われる — 利用者導線を `Use this template` のみとし、tag を保守・監査 marker とする決定と整合 |
| git push --atomic | https://git-scm.com/docs/git-push#Documentation/git-push.txt---atomic | 「Use an atomic transaction on the remote side if available. Either all refs are updated, or on error, no refs are updated.」— main + tag の部分反映を防ぐ根拠 |
| 既存 skill 静的ゲート | `tests/test_skill_migration.py` / `tests/test_skill_remote_placeholder.py` | skill markdown の決定的静的検証の既存 precedent（rglob 走査は新 skill を自動包含。`gh issue|pr|api` 禁止語彙の確認） |
