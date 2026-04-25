# [設計] kamo2 移植 (5/6) final-check / docs workflow / i-pr 系 + docs/dev 全書き換え

Issue: #148 / 親: #143 / 依存: #144 / #145 / #146 / #147 / 並行: #149

## 概要

最終チェック・docs workflow・PR 系の 10 スキル（`i-dev-final-check` /
`i-doc-final-check` / `i-doc-update` / `i-doc-review` / `i-doc-fix` /
`i-doc-verify` / `i-pr` / `issue-pr` / `issue-doc-check` / `kaji-run-verify`）を
kamo2 版に置換し、`docs/dev/` 配下の 6 ドキュメント
（`development_workflow.md` / `docs_maintenance_workflow.md` /
`workflow_overview.md` / `workflow_completion_criteria.md` /
`documentation_update_criteria.md` / `shared_skill_rules.md`）を kamo2 版で
全書き換えする。kamo2 版はワークフロー仕様の正本を `docs/dev/` に一元化し、
スキル本文を手順実行に集中させる薄い構造を採っており、本 Issue でこの構造に
収束させる。なお `kaji-run-verify` は kaji 固有資産であり全置換しない（参照先
更新のみ）。`workflow_guide.md` / `workflow-authoring.md` / `skill-authoring.md`
は kaji 固有メタドキュメントとして温存する。Python 単一スタック化・`make check`
統一・kamo2 固有 Issue 番号除去は #146 / #147 と共通の変換ルールを適用する。

## 背景・目的

### 現状の問題（観測可能）

- **重複定義**: kaji 現行のスキル本文には、ワークフロー仕様（フェーズ間遷移・
  完了条件・docs 更新基準など）の説明が各スキルに分散して記述されており、
  仕様変更時に複数ファイルの同時更新が必要。例: `i-dev-final-check` /
  `i-doc-final-check` / `issue-doc-check` の 3 ファイルに「完了条件照合」の
  類似手順が個別記述。
- **`docs/dev/` の薄さ**: kaji 現行の `docs/dev/development_workflow.md` (244 行)
  は kaji 独自進化を反映しているが、ワークフロー全体図・フェーズ遷移・収束
  保証パターンは kamo2 版が体系的に整理している（kamo2 版 146 行に Mermaid
  フロー + フェーズ表 + type 別差分表が同居）。
- **収束保証の欠如**: docs workflow 側に review/fix/verify サイクル（収束保証）
  が無く、`/i-doc-review` で Changes Requested が出た場合の正規ループが
  スキル定義レベルで記述されていない。kamo2 版は `i-doc-review` →
  `i-doc-fix` → `i-doc-verify` → 再判定のループを正規化済み。
- **kamo2 固有 Scope 軸の混入リスク**: 対象 10 スキルは kamo2 から派生した
  Scope 軸（backend / frontend / fullstack）の残骸を含みうる。`grep -rE
  'verify-backend|release-type=python|gate-backend|check-all|FE_E2E|apps/(api\|web)'`
  が現状で何件ヒットするかをベースライン計測し、置換後 0 件にすることで
  「Python 単一スタックへの畳み」を観測可能化する。

### 改善指標（完了条件の検証セクションと整合）

- 対象 10 スキルおよび `docs/dev/*` 6 ドキュメント全件で、kamo2 固有パターン
  （`verify-backend` / `release-type=python` / `gate-backend` / `check-all` /
  `FE_E2E` / `apps/(api|web)`）の grep ヒット数 0。
- 同範囲で `docs/reference/(backend|frontend)/`, `docs/howto/backend/`,
  `docs/product/features/` への参照ヒット数 0（ダングリング参照ゼロ）。
- 対象 10 スキル本文中、`docs/dev/` への相互リンクが各スキル最低 1 件
  （`grep -l 'docs/dev/' .claude/skills/<skill>/SKILL.md` が全件 hit）。
  これにより「スキル本文を手順実行に集中させ、仕様の正本は docs/dev に
  寄せる」薄い構造への収束を観測可能にする。
- `make check`（exit 0）/ `make verify-docs`（exit 0）通過。
- feature-development workflow と docs-maintenance workflow の end-to-end
  完走（手動試行、専用検証 Issue を作成し最終ステップまで全 step verdict=PASS）。

### 親 Issue との関係

- #143 の 6 分割移植の 5/6。
- #144（_shared 整備）/ #145（lifecycle + ready/PR gate）/ #146（設計サイクル）
  / #147（実装サイクル）が前提。本 Issue は最終チェック・docs workflow・PR 系を
  担当し、#149 が機能ドキュメント書き換え（README / CLAUDE / ARCHITECTURE /
  concepts / guides）を担当する。本 Issue と #149 は並行マージ可能（各 PR 内で
  参照整合が閉じる）。

## ベースライン計測

実装フェーズの冒頭で再計測し、改修後と比較する。worktree 配下で実行:

```bash
# B-1. kamo2 固有パターン残骸（現状件数）
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEn 'verify-backend|release-type=python|gate-backend|check-all|FE_E2E|apps/(api|web)' \
    .claude/skills/i-dev-final-check/ \
    .claude/skills/i-doc-final-check/ \
    .claude/skills/i-doc-update/ \
    .claude/skills/i-doc-review/ \
    .claude/skills/i-doc-fix/ \
    .claude/skills/i-doc-verify/ \
    .claude/skills/i-pr/ \
    .claude/skills/issue-pr/ \
    .claude/skills/issue-doc-check/ \
    .claude/skills/kaji-run-verify/ \
    docs/dev/development_workflow.md \
    docs/dev/docs_maintenance_workflow.md \
    docs/dev/workflow_overview.md \
    docs/dev/workflow_completion_criteria.md \
    docs/dev/documentation_update_criteria.md \
    docs/dev/shared_skill_rules.md \
  | wc -l

# B-2. ダングリング参照（現状件数）
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEn 'docs/reference/(backend|frontend)/|docs/howto/backend/|docs/product/features/' \
    .claude/skills/i-dev-final-check/ ... (B-1 と同範囲) | wc -l

# B-3. スキル本文の docs/dev 相互リンク（現状件数）
for s in i-dev-final-check i-doc-final-check i-doc-update i-doc-review \
         i-doc-fix i-doc-verify i-pr issue-pr issue-doc-check kaji-run-verify; do
  if grep -l 'docs/dev/' "/home/aki/dev/kaji-refactor-148/.claude/skills/$s/SKILL.md" \
       >/dev/null 2>&1; then
    echo "OK $s"; else echo "MISSING $s"; fi
done | tee /tmp/baseline-b3.txt

# B-4. 行数規模（薄化の目安）
wc -l /home/aki/dev/kaji-refactor-148/.claude/skills/{i-dev-final-check,i-doc-final-check,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify,i-pr,issue-pr,issue-doc-check,kaji-run-verify}/SKILL.md
wc -l /home/aki/dev/kaji-refactor-148/docs/dev/{development_workflow,docs_maintenance_workflow,workflow_overview,workflow_completion_criteria,documentation_update_criteria,shared_skill_rules}.md
```

ベースライン計測結果は実装フェーズで Issue にコメントとして記録する。改修後に
B-1, B-2 を 0 件、B-3 を全件 OK にすることが完了条件。

## インターフェース

本 Issue の成果物はスキル定義 Markdown と docs/dev Markdown のみ。Python
実行時 IF は存在しない。論理的 IF は以下。

### 入力（移植元）

- kamo2 リポジトリ（ローカル）:
  - `/home/aki/dev/kamo2/.claude/skills/i-dev-final-check/SKILL.md` (299 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-doc-final-check/SKILL.md` (62 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-doc-update/SKILL.md` (146 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-doc-review/SKILL.md` (101 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-doc-fix/SKILL.md` (70 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-doc-verify/SKILL.md` (72 行)
  - `/home/aki/dev/kamo2/.claude/skills/i-pr/SKILL.md` (172 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-pr/SKILL.md` (67 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-doc-check/SKILL.md` (84 行)
  - `/home/aki/dev/kamo2/docs/dev/development_workflow.md` (146 行)
  - `/home/aki/dev/kamo2/docs/dev/docs_maintenance_workflow.md` (57 行)
  - `/home/aki/dev/kamo2/docs/dev/workflow_overview.md` (56 行)
  - `/home/aki/dev/kamo2/docs/dev/workflow_completion_criteria.md` (145 行)
  - `/home/aki/dev/kamo2/docs/dev/documentation_update_criteria.md` (33 行)
  - `/home/aki/dev/kamo2/docs/dev/shared_skill_rules.md` (26 行)

`editor.md` は移植しない（kamo2 のみ、apps/api・Prettier・TS/JS 前提のため）。

### 出力（最終配置）

```
.claude/skills/
├── i-dev-final-check/SKILL.md      (全置換: kamo2 版 + kaji 適応)
├── i-doc-final-check/SKILL.md      (全置換: kamo2 版 + kaji 適応)
├── i-doc-update/SKILL.md           (全置換: kamo2 版 + kaji 適応)
├── i-doc-review/SKILL.md           (全置換: kamo2 版 + kaji 適応)
├── i-doc-fix/SKILL.md              (全置換: kamo2 版 + kaji 適応)
├── i-doc-verify/SKILL.md           (全置換: kamo2 版 + kaji 適応)
├── i-pr/SKILL.md                   (全置換: kamo2 版 + kaji 適応)
├── issue-pr/SKILL.md               (全置換: kamo2 版 + kaji 適応)
├── issue-doc-check/SKILL.md        (全置換: kamo2 版 + kaji 適応)
└── kaji-run-verify/SKILL.md        (kaji 固有資産: 参照先のみ追従更新)

docs/dev/
├── development_workflow.md         (全置換: kamo2 版 + kaji 適応)
├── docs_maintenance_workflow.md    (全置換: kamo2 版 + kaji 適応)
├── workflow_overview.md            (全置換: kamo2 版 + kaji 適応)
├── workflow_completion_criteria.md (全置換: kamo2 版 + kaji 適応)
├── documentation_update_criteria.md (全置換: kamo2 版 + kaji 適応)
└── shared_skill_rules.md           (全置換: kamo2 版 + kaji 適応)
```

`.agents/skills/` 配下の symlink は対象 10 スキル分すべて既存のため
**追加・変更不要**。

### 公開 IF（スラッシュコマンド名）

公開 IF は不変。`/i-dev-final-check` / `/i-doc-final-check` /
`/i-doc-update` / `/i-doc-review` / `/i-doc-fix` / `/i-doc-verify` /
`/i-pr` / `/issue-pr` / `/issue-doc-check` / `/kaji-run-verify` の
コマンド名はすべて維持する。

### 使用例（置換後の誘導フロー）

```text
[dev workflow]
/issue-review-code APPROVE
  ↓
/i-dev-final-check <issue-number>
  ├─ PASS → /i-pr
  ├─ RETRY → /i-dev-final-check（自己ループ）
  └─ BACK: docs / implement / design → 該当ステップへ戻る
  ↓
/i-pr <issue-number>
  ↓
/issue-close <issue-number>

[docs-maintenance workflow]
/issue-review-ready PASS → /issue-start
  ↓
/i-doc-update <issue-number>
  ↓
/i-doc-review <issue-number>
  ├─ APPROVE → /i-doc-final-check
  └─ Changes Requested → /i-doc-fix → /i-doc-verify → 再判定
  ↓
/i-doc-final-check <issue-number>
  ├─ PASS → /i-pr → /issue-close
  └─ RETRY → /i-doc-update へ戻る
```

## 変更スコープ

### スキル（10 件）

#### 全置換対象（9 件）

`i-dev-final-check`, `i-doc-final-check`, `i-doc-update`, `i-doc-review`,
`i-doc-fix`, `i-doc-verify`, `i-pr`, `issue-pr`, `issue-doc-check`

#### 参照先追従のみ（1 件）

`kaji-run-verify`: kaji 固有資産（kamo2 に存在しない）。本体構造は温存し、
本 Issue で書き換える docs/dev の新名称・新構成への参照箇所のみ追従更新。
具体的には:
- `docs/dev/development_workflow.md` への参照: 名称は不変だが内容が
  kamo2 版に変わるため、引用文や section リンクが整合するか確認。
- `docs/dev/workflow_overview.md` への参照: 同上。
- `docs/dev/workflow_completion_criteria.md` への参照: 同上。

### docs/dev（6 件）

`development_workflow.md`, `docs_maintenance_workflow.md`,
`workflow_overview.md`, `workflow_completion_criteria.md`,
`documentation_update_criteria.md`, `shared_skill_rules.md`

#### 温存（kaji 固有メタドキュメント、3 件）

`workflow_guide.md`, `workflow-authoring.md`, `skill-authoring.md` は
kamo2 に存在しない kaji 独自のメタドキュメント。本 Issue で書き換える
新ドキュメントとの整合確認のみ実施し、本体は温存する。具体的には:
- `workflow_guide.md`: 各スキルの一覧と要約。書き換え後のスキルセットと
  整合する記述に最小修正。
- `workflow-authoring.md` / `skill-authoring.md`: ワークフロー / スキル
  作成ガイド。kamo2 由来の記述があれば確認するが、原則手を入れない。

### 不移植

`editor.md`（kamo2 のみ、apps/api・Prettier・TS/JS 前提のため kaji に該当物
なし）。

## 制約・前提条件

### スコープ厳守

- 機能ドキュメント（README / CLAUDE.md / `docs/ARCHITECTURE.md` /
  `docs/concepts/` / `docs/guides/`）は本 Issue で触らない（#149 の責務）。
- 実装サイクル 4 スキル（`issue-implement` / `issue-review-code` /
  `issue-fix-code` / `issue-verify-code`）は #147 で完了済。本 Issue では
  これらのスキル本体に手を入れない（誘導文言の整合確認のみ）。
- 設計サイクル 4 スキル（`issue-design` / `issue-review-design` /
  `issue-fix-design` / `issue-verify-design`）は #146 で完了済。同上。
- lifecycle 系（`issue-create` / `issue-start` / `issue-close`）および
  ready/PR gate 系（`issue-review-ready` / `issue-fix-ready` / `pr-fix` /
  `pr-verify`）は #145 で完了済。同上。

### 本 PR 内の参照整合は完結させる

- 本 Issue の成果物（10 スキル + 6 ドキュメント）の相互参照は本 PR 内で完結。
- 外部誘導先（既存スキル・既存 docs）はすべて #144〜#147 で整備済として
  参照可能。

### Python 単一スタック化（kamo2 固有要素の完全除去）

kamo2 の `backend / frontend / fullstack` の Scope 分岐を Python 単一に
畳む。対象 10 スキル + 6 docs/dev に、以下のパターンが残ってはならない:

- `apps/api`, `apps/web`, `apps/*`
- `verify-backend`, `verify-frontend`, `gate-backend`, `gate-frontend`,
  `check-all`, `FE_E2E`
- `release-type=python`, `Release-Please`（kamo2 固有のリリース機構）
- `backend` / `frontend` / `fullstack` の Scope 分岐記述
- `Scope: backend / frontend / fullstack` といった設計書 Scope 行への参照
- `docs/reference/backend/`, `docs/reference/frontend/`, `docs/howto/backend/`,
  `docs/product/features/`
- vitest / Playwright / ESLint / Prettier / React BP への参照
- kamo2 固有 Issue 番号（`#542`, `#948` 等）
- worktree プレフィックス `kamo2-`

### 参照先再マップ（#146 / #147 と共通ルール）

| kamo2 参照 | kaji での対応 |
|-----------|--------------|
| `make verify-backend` / `make gate-backend` / `make check-all` | `make check` |
| `docs/reference/backend/testing-convention.md` | `docs/dev/testing-convention.md` |
| `docs/reference/backend/testing-size-guide.md` | `docs/reference/testing-size-guide.md` |
| `docs/reference/backend/coding-standards-comprehensive.md` / `python-style.md` | `docs/reference/python/python-style.md` ほか分割済ファイル群 |
| `docs/reference/frontend/*` | **削除**（kaji に対応物なし） |
| `docs/howto/backend/run-tests.md` | `CLAUDE.md` の Essential Commands 節案内に置換 |
| `docs/product/features/<key>/` | **削除**（kaji に該当する product docs ディレクトリなし） |
| `Release-Please` / `release-type=python` 関連 | kaji の Conventional Commits 運用への置換（`docs/guides/git-commit-flow.md` 参照誘導） |

### kamo2 構造の取り込みポイント（スキルごと）

#### `i-dev-final-check`

kamo2 版 (299 行) の中核構造:
- Step 2: 前段証跡の集約（`gh issue view --comments` 走査 + 完了報告
  コメント存在確認 + fix/verify サイクルの最新判定採用）
- Step 4: Scope に応じた品質ゲート → kaji では Python 単一に畳み
  `make check` と `make verify-docs` に統一
- Step 6: 設計書昇格 → kaji 現行ロジック（`draft/design/` から本決定 docs
  への昇格判定、もしくは `_shared/promote-design.md` 相当の手順）を kamo2
  ベースに再注入
- Step 7.5: 設計書を Issue 本文の NOTE ブロック直下に添付
- BACK / RETRY 判定の正規化（PASS / RETRY / BACK: docs / BACK: implement /
  BACK: design）

#### `i-doc-final-check`

kamo2 版 (62 行、薄い) の構造:
- docs-only ワークフローの最終ゲート
- リンク整合性 (`make verify-docs`) + 完了条件照合のみ
- 恒久テスト追加禁止の確認

kaji 適応:
- `make verify-docs` への参照は維持（kaji に既存）。
- 完了条件照合は `docs/dev/workflow_completion_criteria.md` 参照に統一。

#### `i-doc-update` / `i-doc-review` / `i-doc-fix` / `i-doc-verify`

docs workflow の review/fix/verify サイクル。kamo2 版は収束保証付きで
体系化されている:
- `i-doc-update` (146 行): 事実整合性・実装整合性・運用整合性の 3 観点
  での更新手順
- `i-doc-review` (101 行): 同 3 観点でのレビュー判定（APPROVE /
  Changes Requested）
- `i-doc-fix` (70 行): レビュー指摘への対応（Agree / Disagree-Discuss）
- `i-doc-verify` (72 行): 修正確認（収束保証のため新規指摘禁止）

kaji 適応:
- `docs/reference/backend/*` / `docs/reference/frontend/*` 参照を kaji
  正規パスへ再マップ。
- スキル本体は手順実行に集中させ、3 観点の定義は `docs/dev/documentation_update_criteria.md`
  へ寄せる（薄い構造）。

#### `i-pr` / `issue-pr`

- `i-pr` (172 行): スコープを絞った PR 作成（branch/worktree 解決 + push +
  `gh pr create`）。PR テンプレートに Documentation セクション追加。
- `issue-pr` (67 行): `i-pr` への委譲ラッパー。

kaji 適応:
- `Release-Please` / `release-type=python` を Conventional Commits 運用への
  誘導に置換。`docs/guides/git-commit-flow.md` を参照誘導先に。
- `--no-ff` merge を維持（kaji 既存方針、CLAUDE.md 記載）。

#### `issue-doc-check`

kamo2 版 (84 行): PR 前の品質ゲートとして、コード変更に伴うドキュメント
影響を網羅チェック。

kaji 適応:
- `docs/product/features/` への参照を削除。
- `docs/reference/backend/*` 参照を kaji 正規パスへ再マップ。
- 影響範囲チェックの観点表は維持（コードと docs の同期を担保する役割）。

#### `kaji-run-verify`（参照先追従のみ）

kaji 固有資産。kamo2 にない理由は kamo2 が `kaji run` ハーネス自体を持たないため。
本体ロジックは温存し、書き換え対象 docs/dev への参照箇所のみ整合確認:
- `docs/dev/development_workflow.md`
- `docs/dev/docs_maintenance_workflow.md`
- `docs/dev/workflow_overview.md`
- `docs/dev/workflow_completion_criteria.md`

これら 4 つは本 Issue で書き換えるため、`kaji-run-verify` SKILL.md 内の
section リンク（`#セクション名`）が新版にも存在するか実装時に確認する。

### docs/dev 書き換えの注意点

#### `development_workflow.md`

kamo2 版 (146 行) は dev workflow の正本。Mermaid フロー + フェーズ表 +
type 別差分表が体系化されている。kaji 適応:
- BE/FE Scope 分岐を Python 単一スタックに畳む。
- `Release-Please` 節を Conventional Commits 運用に置換。
- type 別差分表（feat / bug / refactor）は #145 / #146 / #147 と整合。

#### `docs_maintenance_workflow.md`

kamo2 版 (57 行) は docs-only workflow の正本。`/i-doc-update` →
`/i-doc-review` → `/i-doc-fix` → `/i-doc-verify` → `/i-doc-final-check` の
収束ループ。

#### `workflow_overview.md`

kamo2 版 (56 行) は両ワークフローの入口を集約。本 Issue では review-ready
をフロー起点に含める形（#145 で整備済）。

#### `workflow_completion_criteria.md`

kamo2 版 (145 行) は完了条件の段階確認方針を定義。design / implement /
final-check / PR / close の各フェーズで何を確認するかを表化。

#### `documentation_update_criteria.md`

kamo2 版 (33 行) は docs 更新基準（事実整合性・実装整合性・運用整合性の
3 観点）を定義。

#### `shared_skill_rules.md`

kamo2 版 (26 行) はスキル間共通の運用ルール（無関係 issue 報告ルール、
worktree 解決手順など）を集約。

### kaji 固有進化の温存

以下の kaji 現行進化は本 Issue でも維持する:

- **Conventional Commits 運用**: kaji の標準 commit 規則。kamo2 の
  `Release-Please` 機構に置換しない（kaji は採用していない）。
- **`make check` 統一**: #146 / #147 と同じく、kamo2 の Scope 別 verify
  ターゲットを `make check` に畳む。
- **docs-only / metadata-only / packaging-only テスト戦略分岐**: #147 で
  実装サイクルに導入済み。本 Issue で final-check / docs workflow に
  影響する範囲では、`docs/dev/testing-convention.md` 参照誘導を維持。
- **設計書 NOTE ブロック直下添付**: #145 で導入済の方針（`/issue-start`
  時に Issue 本文 NOTE ブロックに worktree/branch を追記）。
  `/i-dev-final-check` Step 7.5 の設計書添付もこの NOTE 直下方式に整合。

### 一次情報アクセス

- 移植元 kamo2 はローカルパス `/home/aki/dev/kamo2/` でレビュワー（agent）が
  アクセス可能。
- kaji 現行スキルは `/home/aki/dev/kaji/.claude/skills/<name>/SKILL.md` で
  アクセス可能。
- 本 Issue の worktree は `/home/aki/dev/kaji-refactor-148/` でアクセス可能。

## 方針

### フェーズ 1: ベースライン計測 + docs/dev 6 件書き換え

1. ベースライン計測（B-1, B-2, B-3, B-4）を実行し Issue にコメント。
2. `docs/dev/` の 6 ドキュメントを kamo2 版で全書き換え。順序:
   1. `shared_skill_rules.md`（依存なし、最薄）
   2. `documentation_update_criteria.md`（依存: shared_skill_rules）
   3. `workflow_completion_criteria.md`（依存: shared_skill_rules）
   4. `workflow_overview.md`（両ワークフローの入口、他 4 つを参照）
   5. `development_workflow.md`（dev workflow 詳細）
   6. `docs_maintenance_workflow.md`（docs workflow 詳細）
3. 各書き換えで Python 単一スタック化・参照先再マップを適用。
4. `workflow_guide.md` / `workflow-authoring.md` / `skill-authoring.md`
   との整合確認（最小修正のみ）。

### フェーズ 2: 最終チェック系スキル置換（2 件）

1. `i-dev-final-check/SKILL.md` を kamo2 版で全置換 + kaji 適応。
2. `i-doc-final-check/SKILL.md` を kamo2 版で全置換 + kaji 適応。

両者ともフェーズ 1 で書き換えた `docs/dev/` への相互リンクを最低 1 件含む。

### フェーズ 3: docs workflow review/fix/verify サイクル置換（4 件）

1. `i-doc-update/SKILL.md` を kamo2 版で全置換 + kaji 適応。
2. `i-doc-review/SKILL.md` を kamo2 版で全置換 + kaji 適応。
3. `i-doc-fix/SKILL.md` を kamo2 版で全置換 + kaji 適応。
4. `i-doc-verify/SKILL.md` を kamo2 版で全置換 + kaji 適応。

`docs/dev/documentation_update_criteria.md` を 3 観点定義の正本として参照
誘導する形に薄化。

### フェーズ 4: PR 系スキル置換（3 件）+ 参照先追従（1 件）

1. `i-pr/SKILL.md` を kamo2 版で全置換 + kaji 適応（Release-Please →
   Conventional Commits 置換、`--no-ff` merge 維持）。
2. `issue-pr/SKILL.md` を kamo2 版で全置換 + kaji 適応（`i-pr` への委譲
   ラッパー）。
3. `issue-doc-check/SKILL.md` を kamo2 版で全置換 + kaji 適応。
4. `kaji-run-verify/SKILL.md` の docs/dev 参照箇所を新版整合確認 → 必要なら
   section リンク修正のみ。

### フェーズ 5: 全体検証

#### 5.1 grep 検証（kamo2 残骸ゼロ）

```bash
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEn 'verify-backend|release-type=python|gate-backend|check-all|FE_E2E|apps/(api|web)' \
    .claude/skills/{i-dev-final-check,i-doc-final-check,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify,i-pr,issue-pr,issue-doc-check,kaji-run-verify}/ \
    docs/dev/{development_workflow,docs_maintenance_workflow,workflow_overview,workflow_completion_criteria,documentation_update_criteria,shared_skill_rules}.md
```

ヒット数 0 件であること。

#### 5.2 ダングリング参照ゼロ

```bash
# docs パス参照
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEoh 'docs/[a-zA-Z0-9/_.-]+\.md' \
    .claude/skills/{i-dev-final-check,i-doc-final-check,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify,i-pr,issue-pr,issue-doc-check,kaji-run-verify}/ \
    docs/dev/{development_workflow,docs_maintenance_workflow,workflow_overview,workflow_completion_criteria,documentation_update_criteria,shared_skill_rules}.md \
  | sort -u | while read -r p; do
    if [ -e "$p" ]; then echo "OK $p"; else echo "MISSING $p"; fi
  done | grep MISSING
```

`MISSING` 0 件。

```bash
# 廃止パスへの参照
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEn 'docs/reference/(backend|frontend)/|docs/howto/backend/|docs/product/features/' \
    .claude/skills/{i-dev-final-check,i-doc-final-check,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify,i-pr,issue-pr,issue-doc-check,kaji-run-verify}/ \
    docs/dev/{development_workflow,docs_maintenance_workflow,workflow_overview,workflow_completion_criteria,documentation_update_criteria,shared_skill_rules}.md
```

ヒット数 0 件。

```bash
# スラッシュコマンド参照
cd /home/aki/dev/kaji-refactor-148 && \
  grep -rEoh '/(issue|i)-[a-z][a-z-]*[a-z]' \
    .claude/skills/{i-dev-final-check,i-doc-final-check,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify,i-pr,issue-pr,issue-doc-check,kaji-run-verify}/ \
  | sort -u | while read -r cmd; do
    name="${cmd#/}"
    if [ -f ".claude/skills/${name}/SKILL.md" ]; then echo "OK ${cmd}"; else echo "MISSING ${cmd}"; fi
  done | grep MISSING
```

`MISSING` 0 件。例外扱いの方針は #147 の設計書（5.2.3 節）と同じ:
本 Issue でも例外なし。`/help` 等の組み込みコマンドが誤検出された場合のみ
レビュー時に明示。

#### 5.3 スキル本文の薄化（observability）

```bash
for s in i-dev-final-check i-doc-final-check i-doc-update i-doc-review \
         i-doc-fix i-doc-verify i-pr issue-pr issue-doc-check kaji-run-verify; do
  if grep -l 'docs/dev/' "/home/aki/dev/kaji-refactor-148/.claude/skills/$s/SKILL.md" \
       >/dev/null 2>&1; then
    echo "OK $s"; else echo "MISSING $s"; fi
done | grep MISSING
```

`MISSING` 0 件（10 スキル全件で `docs/dev/` への相互リンク最低 1 件）。

#### 5.4 `make check` / `make verify-docs` 通過

```bash
cd /home/aki/dev/kaji-refactor-148 && source .venv/bin/activate && make check
cd /home/aki/dev/kaji-refactor-148 && source .venv/bin/activate && make verify-docs
```

両方とも exit 0。

#### 5.5 手動試行（両 workflow の end-to-end 完走）

完了条件「手動試行」セクションどおり、専用検証 Issue を 2 件作成し、
`kaji run workflows/feature-development.yaml <verify-issue>` および
`kaji run workflows/docs-maintenance.yaml <verify-issue>` を実行。
`kaji-run-verify` スキルでログ収集。

- feature-development: 最終ステップ `issue-close` まで全 step verdict=PASS
  （または妥当な BACK/RETRY 後の PASS）。
- docs-maintenance: 最終ステップ `i-doc-final-check` まで全 step verdict=PASS。

該当する小さい検証用 Issue が無い場合は本 Issue で別途作成（`type:docs` /
`type:chore` 等の小規模 Issue）し、手動試行のログを Issue コメントとして
残す。

## テスト戦略

### 変更タイプ

**docs-only**（スキル定義 Markdown と docs/dev Markdown のみの変更。
Python 実行時コードは変更なし）。

### docs-only としての変更固有検証

#### 5.1 grep 検証（kamo2 残骸ゼロ）

フェーズ 5.1 のコマンドで kamo2 固有パターン残骸 0 件を確認。

#### 5.2 ダングリング参照ゼロ

フェーズ 5.2 の 3 系統（docs パス実在 / 廃止パス参照ゼロ / スラッシュ
コマンド実在）を確認。スキル定義ファイルがユーザー誘導 IF として直接
利用されるため、誤誘導の実運用破綻リスクをゼロ化する。

#### 5.3 スキル本文の薄化観測

フェーズ 5.3 で 10 スキル全件が `docs/dev/` への相互リンクを持つことを確認。
これにより「ワークフロー仕様の正本を `docs/dev/` に一元化、スキル本文は
手順実行に集中」という改善目標を機械的に判定する。

#### 5.4 `make check` / `make verify-docs` 通過

フェーズ 5.4 で両方 exit 0 確認。`make verify-docs` がリンク整合性を、
`make check` が ruff/format/mypy/pytest の通過（既存テストの回帰なし）を
担保する。本 Issue では Python コードに変更を入れないため、`make check`
の `pytest` 部分は既存テストの非回帰確認用。

#### 5.5 手動試行（両 workflow の end-to-end 完走）

フェーズ 5.5 のとおり feature-development / docs-maintenance の両
workflow を `kaji run` で完走させる。`kaji-run-verify` スキルでログ収集
し、Issue コメントに添付。

### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件に沿って:

1. **独自ロジックの追加・変更をほぼ含まない**: 本 Issue はスキル定義
   Markdown と docs/dev Markdown の全置換であり、Python 実行時コード
   （`kaji_harness/`）の変更は無い。harness ロジックには手を入れない。
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**:
   - リンク整合性 → `make verify-docs` で捕捉
   - kamo2 固有要素の残骸 → 5.1 の grep で捕捉
   - ダングリング参照（廃止パス・スラッシュコマンド） → 5.2 の grep で捕捉
   - スキル本文の薄化観測 → 5.3 の grep で捕捉
   - 手順自体の妥当性 → 5.5 の手動試行で捕捉
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: スキル
   Markdown / docs Markdown のテキスト構造をユニットテスト化しても、
   現実の運用で問題になるのは「文中の参照先パスが存在するか」「kamo2
   固有要素が混入していないか」「両 workflow が完走するか」であり、これらは
   既存ゲート（`make verify-docs`）と本 Issue で実施する grep + 手動試行で
   十分検出できる。
4. **テスト未追加の理由をレビュー可能な形で説明できる**: 本セクションが
   その説明。レビュー時に grep / verify-docs / 手動試行ログを確認することで
   変更妥当性を外部から検証可能。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定の変更なし。kamo2 移植方針は #143 で確定済 |
| `docs/ARCHITECTURE.md` | なし（#149 の責務） | 機能ドキュメントは #149 担当 |
| `docs/dev/development_workflow.md` | **あり（全置換）** | kamo2 版で全書き換え |
| `docs/dev/docs_maintenance_workflow.md` | **あり（全置換）** | 同上 |
| `docs/dev/workflow_overview.md` | **あり（全置換）** | 同上 |
| `docs/dev/workflow_completion_criteria.md` | **あり（全置換）** | 同上 |
| `docs/dev/documentation_update_criteria.md` | **あり（全置換）** | 同上 |
| `docs/dev/shared_skill_rules.md` | **あり（全置換）** | 同上 |
| `docs/dev/workflow_guide.md` | あり（最小修正） | kaji 固有メタドキュメント。書き換え後のスキルセットと整合確認 |
| `docs/dev/workflow-authoring.md` | あり（最小修正想定、なしの可能性も） | 同上 |
| `docs/dev/skill-authoring.md` | あり（最小修正想定、なしの可能性も） | 同上 |
| `docs/dev/testing-convention.md` | なし | テスト規約は #147 の段階で kaji 既存進化を温存済 |
| `docs/reference/python/*` | なし | コーディング規約は #141 で整備済 |
| `docs/cli-guides/` | なし | CLI 仕様変更なし |
| `CLAUDE.md` | なし（#149 の責務） | プロジェクト規約変更は #149 担当 |
| `README.md` | なし（#149 の責務） | 同上 |
| `.claude/skills/_shared/*` | なし | #144 で整備済。参照のみ |
| `.agents/skills/*` の symlink | なし | 全対象スキル分既存。追加・変更不要 |

## 参照情報（Primary Sources）

レビュワー再現性のため、GitHub Issue / コミットには検証コマンドを併記する。

| 情報源 | URL/パス / 検証コマンド | 根拠（引用/要約） |
|--------|------------------------|-------------------|
| 親 Issue #143 | https://github.com/apokamo/kaji/issues/143 / `gh issue view 143` | kamo2 移植 6 分割方針。本 Issue は 5/6 |
| 並行 Issue #149 | https://github.com/apokamo/kaji/issues/149 / `gh issue view 149` | 機能ドキュメント書き換え（README / CLAUDE / ARCHITECTURE / concepts / guides）。本 Issue とのスコープ境界を確定 |
| 依存 Issue #144 | https://github.com/apokamo/kaji/issues/144 / `git show 6dcd48b` | `_shared/` 整備 + docs/dev 名称リネーム済み |
| 依存 Issue #145 | https://github.com/apokamo/kaji/issues/145 / `git show a9f1b87` | lifecycle + ready/PR gate 4 スキル新設済み |
| 依存 Issue #146（先行 merge） | https://github.com/apokamo/kaji/issues/146 / `git show ed66db4` | 設計サイクル 4 スキル kamo2 化済み。type 軸の設計と整合 |
| 依存 Issue #147（先行 merge） | https://github.com/apokamo/kaji/issues/147 / `git show 0a931d8` | 実装サイクル 4 スキル kamo2 化済み。Python 単一スタック化のルール先例 |
| kamo2 移植元 i-dev-final-check | `/home/aki/dev/kamo2/.claude/skills/i-dev-final-check/SKILL.md` | 前段証跡集約・Issue 本文更新プロトコル・BACK/RETRY 判定の正本 |
| kamo2 移植元 i-doc-final-check | `/home/aki/dev/kamo2/.claude/skills/i-doc-final-check/SKILL.md` | docs-only ワークフロー最終ゲート |
| kamo2 移植元 i-doc-update / i-doc-review / i-doc-fix / i-doc-verify | `/home/aki/dev/kamo2/.claude/skills/i-doc-{update,review,fix,verify}/SKILL.md` | docs workflow review/fix/verify サイクルの収束保証構造 |
| kamo2 移植元 i-pr / issue-pr | `/home/aki/dev/kamo2/.claude/skills/{i-pr,issue-pr}/SKILL.md` | PR 作成スキル + 委譲ラッパーの構造 |
| kamo2 移植元 issue-doc-check | `/home/aki/dev/kamo2/.claude/skills/issue-doc-check/SKILL.md` | コード変更に伴う docs 影響網羅チェック |
| kamo2 移植元 docs/dev | `/home/aki/dev/kamo2/docs/dev/{development_workflow,docs_maintenance_workflow,workflow_overview,workflow_completion_criteria,documentation_update_criteria,shared_skill_rules}.md` | ワークフロー仕様の正本（kamo2 では薄い構造に整理済） |
| kaji 現行 i-dev-final-check | `/home/aki/dev/kaji/.claude/skills/i-dev-final-check/SKILL.md` | kaji 固有進化（設計書 NOTE ブロック直下添付など）の温存元 |
| kaji 現行 i-pr / issue-pr | `/home/aki/dev/kaji/.claude/skills/{i-pr,issue-pr}/SKILL.md` | `--no-ff` merge / Conventional Commits 運用の温存元 |
| kaji 現行 kaji-run-verify | `/home/aki/dev/kaji/.claude/skills/kaji-run-verify/SKILL.md` | kaji 固有資産。本 Issue では本体温存、参照先のみ追従 |
| Conventional Commits 運用 | `docs/guides/git-commit-flow.md` | kaji の commit 規則。Release-Please の置換誘導先 |
| Git Worktree 運用 | `docs/guides/git-worktree.md` | bare repository + worktree パターン |
| 開発ワークフロー定義 | `docs/dev/development_workflow.md`（書き換え後） | 本 Issue 成果物の正本 |
| docs ワークフロー定義 | `docs/dev/docs_maintenance_workflow.md`（書き換え後） | 同上 |
| 完了条件運用 | `docs/dev/workflow_completion_criteria.md`（書き換え後） | 同上 |
| docs 更新基準 | `docs/dev/documentation_update_criteria.md`（書き換え後） | 同上 |
| 共有スキルルール | `docs/dev/shared_skill_rules.md`（書き換え後） | 同上 |
| テスト規約 | `docs/dev/testing-convention.md` | docs-only / metadata-only / packaging-only 分岐の根拠 + 4 条件 |
