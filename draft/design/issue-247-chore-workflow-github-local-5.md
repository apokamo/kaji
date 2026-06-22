# [設計] workflow を GitHub/local の通常運用 5 本に整理する

Issue: #247

## 概要

`.kaji/wf/` の 12 本 + root `workflows/` 2 本の workflow YAML を、通常運用で使う
**GitHub 3 本（`dev` / `dev-thorough` / `docs`）+ local fallback 2 本（`dev-local` /
`docs-local`）= 計 5 本**に集約し、削除・リネーム・参照更新（README / docs / `CLAUDE.md` /
skill / tests）まで一括で行う chore。PR review 専用 YAML（`review-cycle` / `review-close`）は
削除し、`kaji run .kaji/wf/dev.yaml <id> --from review-poll [--before close]` の途中起動へ寄せる。

## 背景・目的

### 現状の問題

`.kaji/wf/` に新旧 4 系統が混在し（新: `dev*`/`docs`、旧 dev: `feature-development*`、
旧 full-cycle: `full-cycle*`、旧 docs: `docs-maintenance*`、PR review 専用: `review-cycle`/`review-close`）、
root `workflows/` にも旧 YAML が残る。README / docs / `CLAUDE.md` / skill / tests には
旧 workflow 名と root `workflows/` 参照が散在し（実調査で active ファイルに多数。§ 変更スコープ参照）、
利用者が「どれを使うべきか」を迷う。さらに `feature-development-light.yaml`（`name: feature-development`）と
`full-cycle-xhigh.yaml`（`name: full-cycle`）は **`name:` がファイル名と不一致**。

### 方針確定事項（Issue 本文 + 方針レビュー往復で確定済み）

本 Issue は着手前に maintainer の方針レビューを 3 往復しており、設計上の重要分岐は確定済み。

1. **disposition は「非推奨残置」ではなく「削除/リネーム + tests 更新込み」**。tests が旧ファイル名に
   パス依存しているため、削除/リネームは tests 更新を不可避に含む（→ tests/ は本 chore の in-scope）。
2. **review-cycle / review-close は別軸 YAML として残さない**。`dev` / `dev-thorough` / `docs` の
   3 本が review 軸（`review-poll` step + `pr-review` cycle `entry: review-poll` / `loop: [pr-fix, pr-verify]` /
   `max_iterations: 3` / `on_exhaust: ABORT` + `BACK_FALLBACK: review`）を**完全内包**しており、
   `--from review-poll` / `--from review-poll --before close` で機能欠落なく代替可能（実ファイル裏取り済み）。
3. **local は「検証期間中の主 workflow」ではなく GitHub 障害時・緊急時の fallback**。既存 docs に残る
   「検証期間中は local が主 / github は使用しない」という逆の記述は、**検証期間終了 → 通常運用は GitHub**
   として本 Issue で更新する。
4. **root `workflows/` と `.github/workflows/` を区別**。前者は廃止し正本を `.kaji/wf/` に一本化、
   後者は GitHub Actions 用で対象外。ADR 等の歴史的記述は許容リスト化する。
5. **runner backend は workflow YAML に固定しない**。通常運用は repository config の
   `[execution].agent_runner`、`--agent-runner` は一時 override。`claude -p` は headless runner の
   実装詳細であり workflow 選択基準に含めない。

### ユースケース

- **作業者**として、通常時は GitHub 3 本、GitHub 障害時は local 2 本から迷わず選びたい。
- **運用者**として、README / docs / skill / tests の workflow 名・コマンド例を `.kaji/wf/` 5 本運用に
  揃え、旧名・旧ディレクトリ参照による実行ミスを避けたい。
- **実装者**として、途中開始・途中終了・単発実行は専用 YAML ではなく `--from` / `--before` / `--step`
  で行えると docs で確認したい。
- **利用者**として、runner backend は通常 `[execution].agent_runner`、必要時のみ `--agent-runner`
  で override と確認したい。

### 代替案と不採用理由

- **(a) 非推奨残置（ファイルを残し docs から落とす）**: tests 不変で chore として一貫するが、
  「5 本固定」がファイルシステム上は未達のまま docs 整理に留まる。利用者が `kaji run .kaji/wf/full-cycle.yaml`
  を実行でき続け、二重正本の混乱が解消しない → **不採用**。
- **(b) review-cycle / review-close を別カテゴリで 2 本残す**: 当初案だが、3 本が review 軸を完全内包する
  ことが確認できたため二重正本になる → **不採用**（`--from review-poll` で代替）。

## インターフェース

本 chore の「インターフェース」は、利用者が触れる **workflow ファイル集合・起動コマンド・選択基準**である。

### 出力（到達状態）: 通常運用 workflow 5 本

| ファイル | provider | 用途 | `name:` |
|----------|----------|------|---------|
| `.kaji/wf/dev.yaml` | github | 標準 dev workflow | `dev` |
| `.kaji/wf/dev-thorough.yaml` | github | 丁寧版 dev workflow | `dev-thorough` |
| `.kaji/wf/docs.yaml` | github | docs-only workflow | `docs` |
| `.kaji/wf/dev-local.yaml` | local | 緊急時 fallback dev workflow | `dev-local` |
| `.kaji/wf/docs-local.yaml` | local | 緊急時 fallback docs-only workflow | `docs-local` |

- 各 `name:` はファイル名から `.yaml` を除いた値と一致する。
- local 2 本は GitHub 前提 step（`i-pr` / `review-poll` / PR review）を**持たない**構成
  （最終 step は `issue-close`）。現行 `feature-development-local.yaml` / `docs-maintenance-local.yaml` の
  step 構成をそのまま引き継ぐため、リネームと `name:` 修正以外の step 変更は不要。

### 後継コマンド（PR review 軸・途中起動）

| 旧専用 YAML | 後継コマンド | 等価性の根拠 |
|-------------|--------------|--------------|
| `review-cycle.yaml`（review→fix→verify、close 手前で停止） | `kaji run .kaji/wf/dev.yaml <id> --from review-poll --before close` | `review-poll` の `PASS: close` を `--before close` で止めると `review-cycle.yaml` の `PASS: end` と等価 |
| `review-close.yaml`（close まで全自動） | `kaji run .kaji/wf/dev.yaml <id> --from review-poll` | review-poll〜close の step 列が `dev.yaml` に内包済 |

- wrapper（`/review-cycle` skill）は issue 種別による workflow 判定を**しない**。review 以降の step 列は
  dev/docs/dev-thorough で同一のため、canonical に `dev.yaml` を `--from review-poll` するだけで足りる。
- 旧 `feature-development.yaml` の「PR 作成で停止（close しない）」相当は `--before review-poll`
  で表現できる（docs に例示）。

### 使用例（docs に追加する想定）

```bash
# 通常運用（GitHub）
kaji run .kaji/wf/dev.yaml 247              # 標準 dev
kaji run .kaji/wf/dev-thorough.yaml 247     # 丁寧版
kaji run .kaji/wf/docs.yaml 247             # docs-only

# 緊急時 fallback（GitHub 障害・不通時）
kaji run .kaji/wf/dev-local.yaml 247
kaji run .kaji/wf/docs-local.yaml 247

# 途中開始・途中終了・単発実行（専用 YAML を増やさない）
kaji run .kaji/wf/dev.yaml 247 --from review-poll               # review-close 相当（close まで）
kaji run .kaji/wf/dev.yaml 247 --from review-poll --before close # review-cycle 相当（close 手前で停止）
kaji run .kaji/wf/dev.yaml 247 --step review-code               # 単発実行
kaji run .kaji/wf/dev.yaml 247 --before review-poll             # PR 作成で停止

# runner backend は config で選び、--agent-runner は一時 override
#   config TOML: [execution] agent_runner = "interactive_terminal"  (アンダースコア)
#   CLI override: kaji run ... --agent-runner interactive-terminal   (ハイフン)
```

### workflow 選択表（docs に追加する想定）

| 作業種類 | 通常時（GitHub 正常） | 緊急時（GitHub 障害・不通） |
|----------|------------------------|------------------------------|
| 標準コード変更 | `dev.yaml` | `dev-local.yaml` |
| 丁寧に進めたいコード変更 | `dev-thorough.yaml` | `dev-local.yaml`（thorough local 版は持たない） |
| docs-only | `docs.yaml` | `docs-local.yaml` |
| 既存 PR の review 収束のみ | `dev.yaml --from review-poll [--before close]` | （PR concept なし。local では非対象） |

## 制約・前提条件

- **scope は config / docs / skill / workflow 参照 tests に閉じる**。`kaji_harness/` の
  runner / validator / provider 実装は変更しない。実調査で `kaji_harness/` 内に workflow ファイル名の
  ハードコード参照は無く、`pyproject.toml` の package-data も workflow YAML を同梱しないため、
  ファイル削除/リネームによる runtime / packaging 破壊は発生しない。
- **local 版は GitHub 版と完全同一にしない**。`i-pr` / `review-poll` / PR review は GitHub workflow 側に限定。
- **`.github/workflows/`（GitHub Actions）は対象外**。一括 sed での誤爆を避け、差分レビューで確認する。
- **ADR / historical docs / 過去 Issue 記録など歴史的記述は変更しない**（許容リスト。§ 方針 参照）。
- **cycle 構造名 `review-cycle` は workflow ファイル名と別概念**。`tests/fixtures/test_workflow.yaml` の
  `cycles: review-cycle:` と、それを検証する `tests/test_skill_harness_adaptation.py` の
  `assert "review-cycle" in cycle_names` は production workflow に紐づかない engine fixture であり、
  削除・改名しない（grep 誤検出の対象外）。
- 完了の客観ゲート: `kaji validate .kaji/wf/*.yaml` 成功 / 許容リスト除外で旧名・root `workflows/` grep ゼロ /
  docs link check / `make check`。

## 変更スコープ

### 1. workflow YAML の disposition

| 現ファイル | provider | 現 `name:` | disposition |
|------------|----------|-----------|-------------|
| `.kaji/wf/dev.yaml` | github | `dev` | **KEEP**（変更なし） |
| `.kaji/wf/dev-thorough.yaml` | github | `dev-thorough` | **KEEP** |
| `.kaji/wf/docs.yaml` | github | `docs` | **KEEP** |
| `.kaji/wf/feature-development-local.yaml` | local | `feature-development-local` | **RENAME → `dev-local.yaml`**、`name: dev-local` に修正 |
| `.kaji/wf/docs-maintenance-local.yaml` | local | `docs-maintenance-local` | **RENAME → `docs-local.yaml`**、`name: docs-local` に修正 |
| `.kaji/wf/feature-development.yaml` | any | `feature-development` | **DELETE** |
| `.kaji/wf/feature-development-light.yaml` | any | `feature-development`(不一致) | **DELETE** |
| `.kaji/wf/full-cycle.yaml` | any | `full-cycle` | **DELETE** |
| `.kaji/wf/full-cycle-xhigh.yaml` | any | `full-cycle`(不一致) | **DELETE** |
| `.kaji/wf/docs-maintenance.yaml` | any | `docs-maintenance` | **DELETE** |
| `.kaji/wf/review-cycle.yaml` | github | `review-cycle` | **DELETE**（`--from review-poll --before close` へ） |
| `.kaji/wf/review-close.yaml` | github | `review-close` | **DELETE**（`--from review-poll` へ） |
| `workflows/feature-development.yaml` | — | — | **DELETE**（root `workflows/` 廃止） |
| `workflows/docs-maintenance.yaml` | — | — | **DELETE**（root `workflows/` 廃止） |

結果: `.kaji/wf/` に 5 本ちょうど、root `workflows/` は空（ディレクトリごと削除）。

### 2. tests の更新（in-scope。grep ゼロと `make check` 緑のため必須）

| ファイル | 現依存 | 対応方針 |
|----------|--------|----------|
| `tests/test_feature_development_workflow.py` | root + builtin の `feature-development.yaml` を parametrize ロード | 削除対象 YAML を検証している。なお残すべき構造検証（#158 final-check の BACK_DESIGN/BACK_IMPLEMENT 分割）は `dev.yaml` を対象に移設し、削除済みの「PR で停止（close 無し）」前提のアサーションは撤去（`--before review-poll` で代替されるため恒久不要）。 |
| `tests/test_workflow_agent_optional.py:79` | `.kaji/wf/full-cycle.yaml` をロード | 対象を `.kaji/wf/dev.yaml` に差し替え（同一の review-poll exec step を持つ）。 |
| `tests/workflows/test_review_poll_exec_migration.py` | `TARGET_WORKFLOWS = [review-cycle, review-close, full-cycle, full-cycle-xhigh]` を検証し、`expected_pass = "end" if review-cycle.yaml else "close"` 分岐を持つ | 削除 4 本を対象にした `TARGET_*` 系（legacy `uv run ... review_poll_entry` exec 形）を撤去し、`PUBLIC_WORKFLOWS = [dev, dev-thorough, docs]`（`kaji pr review-poll` exec 形）の検証のみ残す。**`review-cycle.yaml は PASS=end / 他は PASS=close` の旧専用 YAML 前提を解消**（完了条件明記項目）。 |
| `tests/test_runner_exec_script_dispatch.py:183` | docstring が `review-cycle.yaml / review-close.yaml の互換ケース` と記述（test 本体は inline `Workflow` 構築でファイル非依存） | 削除済みファイル名を指す docstring の文言を一般化（test ロジックは不変）。 |
| `tests/test_verdict_e2e.py:172` | `test_kaji_validate_workflows` が root `workflows/` を glob し、無ければ `pytest.skip` | root `workflows/` 削除で silent skip 化する。対象を `.kaji/wf/` に向け直し、5 本の validate を検証（silent skip 回避）。 |
| `tests/test_cli_validate.py:267,268,426` | tmp_path 内に `project_root/workflows/<...>.yaml` を作る simulated layout（実 root `workflows/` 非依存の generic skill 解決テスト） | grep 衝突回避のため simulated subdir 名を中立名（例 `flows/`）に変更し、test 意図（任意 subdir からの skill 解決）を保つ。**reword しない場合は許容リスト明記**で対応も可。 |

> `tests/fixtures/test_workflow.yaml:9` の `review-cycle:`（cycle 名）と `tests/test_skill_harness_adaptation.py:158`
> の cycle 名アサーションは **workflow ファイルではなく cycle 構造名**であり、本 chore の更新・grep 対象外。

### 3. docs / README / CLAUDE.md の更新（active 参照）

| ファイル | 主な更新内容 |
|----------|--------------|
| `docs/dev/workflow_guide.md` | 5 本運用表 / provider×workflow 対応 / 選択表（作業種類×通常時/緊急時）/ `--from`・`--before`・`--step` 例 / agent_runner 両表記 / review-cycle・review-close 後継コマンド。「検証期間中は local が主」記述を「通常運用 GitHub・local は緊急 fallback」へ反転。 |
| `docs/dev/workflow-authoring.md` | `kaji run workflows/feature-development.yaml ...` 例を `.kaji/wf/dev.yaml ...` へ。`name: feature-development` 例を現行名へ。`workflows/` ディレクトリ例を `.kaji/wf/` へ。 |
| `docs/operations/local-mode-runbook.md` | `feature-development-local` → `dev-local` 等の名称更新。「検証期間中は github 不使用」を緊急 fallback 位置づけへ更新。 |
| `docs/cli-guides/interactive-terminal-runner.md` | 旧 workflow 名のコマンド例を 5 本運用へ。 |
| `docs/cli-guides/local-mode.md` | `kaji run` 例を `dev-local` / `docs-local` へ。 |
| `docs/concepts/ai-docs-management.md` | 旧 workflow 名参照更新。 |
| `docs/README.md` | doc index の `feature-development` / `docs-maintenance` を `dev` / `docs` へ。`workflows/*.yaml` を `.kaji/wf/*.yaml` へ。 |
| `docs/ARCHITECTURE.md` | 旧名 + `workflows/` パス更新。 |
| `docs/dev/docs_maintenance_workflow.md` | 旧名 + `workflows/` パス更新。 |
| `docs/dev/development_workflow.md` | `kaji run feature-development.yaml` の例（provider 整合 fail-fast 節）を `dev.yaml` へ。 |
| `docs/reference/python/naming-conventions.md` | ワークフローファイル命名例の `workflows/` を `.kaji/wf/` へ（grep ゲートを単純に保つため更新。allow-list 扱いも可）。 |
| `README.md` / `README.ja.md` | `.kaji/wf/feature-development-local.yaml` → `.kaji/wf/dev-local.yaml`。 |
| `CLAUDE.md` | skill 表 `review-cycle.yaml` / `kaji run .kaji/wf/review-close.yaml <id>` 現役参照を後継コマンドへ更新。 |

### 4. skill の更新（active 参照）

| ファイル | 主な更新内容 |
|----------|--------------|
| `.claude/skills/review-cycle/SKILL.md` | 起動方式を `kaji run .kaji/wf/review-cycle.yaml` から **`kaji run .kaji/wf/dev.yaml <id> --from review-poll --before close`** へ全面更新。frontmatter description / 起動コマンド / error message の `kaji validate .kaji/wf/review-cycle.yaml` も追随。close まで全自動 = `--from review-poll`（`--before close` 無し）も案内。 |
| `.claude/skills/kaji-run-verify/SKILL.md` | 旧 workflow 名 / root `workflows/` 例を 5 本運用へ。 |
| `.claude/skills/issue-design/SKILL.md` | Step 1.6/1.7 の `feature-development.yaml:79` / `:27`（BACK: design 経路）citation を `dev.yaml` 該当行へ。 |
| `.claude/skills/i-dev-final-check/SKILL.md` | 旧 workflow 名参照更新。 |
| `.claude/skills/review/SKILL.md` / `.claude/skills/review-poll/SKILL.md` | `review-cycle` / `review-close` 参照を後継方式へ。 |
| `.claude/skills/i-doc-update/SKILL.md` | root `workflows/` パス参照更新。 |

## 方針（Minimal How）

### 手順の大枠

1. **YAML 物理操作**: `git mv` で local 2 本をリネーム → `name:` 1 行修正。`git rm` で削除 9 本
   （`.kaji/wf/` 7 本: `feature-development.yaml` / `feature-development-light.yaml` / `full-cycle.yaml` /
   `full-cycle-xhigh.yaml` / `docs-maintenance.yaml` / `review-cycle.yaml` / `review-close.yaml`
   ＋ root `workflows/` 2 本: `feature-development.yaml` / `docs-maintenance.yaml`）。
2. **tests 更新**: § 変更スコープ 2 の表に従い、削除/リネーム先へ追随。`make check`（pytest 含む）が緑になることを確認。
3. **docs / skill 更新**: § 変更スコープ 3・4 の表に従い、旧名・旧パスを 5 本運用へ。docs 追加分
   （選択表 / `--from・--before・--step` 例 / agent_runner 両表記 / 後継コマンド）は主に `workflow_guide.md`。
4. **検証ゲート**: 下記「grep ゲート」+ `kaji validate .kaji/wf/*.yaml` + `make verify-docs` + `make check`。

### 許容リスト（grep ゲートで除外する歴史的・別概念参照）

旧 workflow 名 / root `workflows/` 参照のうち、**変更せず残す**もの:

| 範囲 | 理由 |
|------|------|
| `.github/workflows/**` | GitHub Actions 用。kaji workflow YAML ではない。 |
| `draft/**`（`draft/design/`・`draft/lab/`） | 過去の設計書・lab note。歴史的記録として不変。 |
| `.kaji/issues/**` | 過去の local Issue 記録。immutable history。 |
| `.kaji-artifacts/**` | run artifacts。 |
| `CHANGELOG.md` | release 履歴（過去 release 時点の workflow 名は歴史的事実）。 |
| `docs/adr/003-skill-harness-architecture.md`, `docs/adr/004-remove-gitlab-forge.md` | ADR の歴史的記述（`workflows/*.yaml` の層定義 / `full-cycle` を含む #184 当時の経緯）。 |
| `tests/fixtures/test_workflow.yaml` の cycle 名 `review-cycle` + `tests/test_skill_harness_adaptation.py` の cycle 名アサーション | workflow ファイルではなく cycle 構造名。別概念。 |
| `tests/test_cli_validate.py` の simulated `workflows/` subdir（reword しない選択時のみ） | 実 root `workflows/` 非依存の generic skill 解決テスト。 |

### grep ゲート（機械検証）

許容リスト配下を除外し、以下 2 系統が**ゼロ**であることを確認する（疑似）:

```bash
# (A) 旧 workflow ファイル名参照（.yaml suffix / kaji run 起動文脈）
grep -rEn '(feature-development|full-cycle|docs-maintenance|review-cycle|review-close)[A-Za-z-]*\.ya?ml' \
  <active 範囲: .kaji/wf .claude/skills tests docs README*.md CLAUDE.md>
# (B) root workflows/ パス参照（.kaji/wf/ と .github/workflows/ を除く）
grep -rEn '(^|[^./[:alnum:]_-])workflows/' <active 範囲> | grep -vE '\.kaji/wf/|\.github/workflows/'
```

- 許容リスト（`draft/` `.kaji/issues/` `.kaji-artifacts/` `CHANGELOG.md` `docs/adr/` `.github/`）を除外。
- (A) は `.yaml` suffix を要求することで、cycle 構造名 `review-cycle`（suffix 無し）を自然に除外する。
- 残存ヒットが出た場合、それが「別概念（cycle 名など）」か「更新漏れ」かを目視判定し、後者なら修正。

## テスト戦略

> 本 chore は config（workflow YAML）/ docs / skill / **workflow 参照 tests** の整理であり、
> `kaji_harness/` の runtime ロジックは変更しない。よって新規の恒久回帰テストは原則不要で、
> 検証は「既存 structural test の追随 + 変更固有ゲート」に置く。

### 変更タイプ

- **config + docs + skill + test-reference の reorganization**（runtime コード変更なし / 新規ロジックなし）。

### 実行時コード変更の場合（S/M/L）

本 chore は runtime ロジックを追加・変更しないため、**新規の Small / Medium / Large 恒久テストは追加しない**。
ただし以下は実施する:

- **既存 structural test の追随**（新規ではなく更新）: `test_feature_development_workflow.py` /
  `test_workflow_agent_optional.py` / `test_review_poll_exec_migration.py` /
  `test_runner_exec_script_dispatch.py` / `test_verdict_e2e.py` /（必要なら）`test_cli_validate.py`。
  削除/リネーム後も `make check`（pytest 全実行）が緑であることが回帰シグナル。
- **推奨する 1 本の Small 不変条件テスト（任意・高価値）**: `.kaji/wf/*.yaml` が
  **(1) ちょうど 5 本** かつ **(2) 各 `name:` がファイル名 stem と一致** することを assert する小テスト。
  本 Issue が確立する不変条件（5 本固定 / `name:` 一致）を直接守り、旧 workflow の再追加や
  `name:` 不一致の再発を低コストで検出できる。loader は既存テスト済みのため追加コストは最小。

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` の 4 条件）

1. 独自ロジックの追加・変更を含まない（YAML 削除/リネーム + 文字列参照更新のみ）。
2. 想定不具合（壊れた YAML / 名称不一致 / skill 解決失敗）は `kaji validate` / 既存
   loader・validator テスト / 更新後の structural test / `make check` で捕捉済み。
3. 上記推奨 Small テスト以外に新規テストを足しても回帰検出情報がほとんど増えない。
4. 本設計書にスキップ理由を明記（レビュー可能）。

### 変更固有検証

- `source .venv/bin/activate && kaji validate .kaji/wf/*.yaml` が 5 本すべてで成功。
- 許容リスト除外で旧 workflow 名 / root `workflows/` 参照の **grep ゼロ**（§ 方針 grep ゲート）。
- `make verify-docs`（docs link check。リネーム/削除に伴う死リンクが無いこと）。
- `make check`（`ruff` → `format` → `mypy` → `pytest` 全実行）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし（参照のみ・変更しない） | ADR 003/004 は歴史的記述で許容リスト。新規 ADR が必要な技術選定は無し。 |
| docs/ARCHITECTURE.md | あり | 旧 workflow 名 / `workflows/` パス参照を更新。 |
| docs/dev/ | あり | `workflow_guide.md` / `workflow-authoring.md` / `development_workflow.md` / `docs_maintenance_workflow.md` を更新（選択表・後継コマンド・agent_runner・名称）。 |
| docs/reference/ | あり | `naming-conventions.md` の workflow 命名例を更新。 |
| docs/cli-guides/ | あり | `interactive-terminal-runner.md` / `local-mode.md` のコマンド例を更新。 |
| docs/operations/ | あり | `local-mode-runbook.md` の local 位置づけ反転・名称更新。 |
| docs/concepts/ | あり | `ai-docs-management.md` の旧名参照更新。 |
| docs/README.md | あり | doc index の workflow 名 / `workflows/*.yaml` 更新。 |
| README.md / README.ja.md | あり | `feature-development-local.yaml` → `dev-local.yaml`。 |
| CLAUDE.md | あり | skill 表の `review-cycle.yaml` / `review-close.yaml` 現役参照を後継コマンドへ。 |
| .claude/skills/ | あり | `review-cycle` / `kaji-run-verify` / `issue-design` / `i-dev-final-check` / `review` / `review-poll` / `i-doc-update` を更新。 |
| tests/ | あり | § 変更スコープ 2 の 6 ファイルを更新（in-scope）。 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #247 本文 + 方針レビューコメント | 本 Issue（GitHub `apokamo/kaji#247`） | disposition＝削除/リネーム+tests込み / review 軸は `--from review-poll` 代替 / local＝緊急 fallback / `.github/workflows/` 除外 / grep ゼロゲート / agent_runner 両表記 が確定済み。 |
| 現行 5 本（KEEP/RENAME 元）の step・cycle 構造 | `.kaji/wf/dev.yaml`（`cycles: pr-review: entry: review-poll / loop: [pr-fix, pr-verify] / max_iterations: 3 / on_exhaust: ABORT`、`review-poll` step `exec: [kaji, pr, review-poll]`、`BACK_FALLBACK: review`）, `.kaji/wf/feature-development-local.yaml`, `.kaji/wf/docs-maintenance-local.yaml` | `dev` が review 軸を完全内包し `--from review-poll [--before close]` で `review-close`/`review-cycle` を代替可能。local 2 本は GitHub 前提 step を持たず（最終 step `issue-close`）リネーム+`name:`修正で足りる。 |
| review-poll PASS 分岐の旧前提 | `tests/workflows/test_review_poll_exec_migration.py:34-37,113`（`TARGET_WORKFLOWS` 列挙、`expected_pass = "end" if path.name == "review-cycle.yaml" else "close"`） | `review-cycle.yaml` のみ `PASS=end`・他は `PASS=close` の旧専用 YAML 前提。削除に伴い解消対象（完了条件明記）。 |
| tests のファイル名パス依存 | `tests/test_feature_development_workflow.py:24-25`（root + builtin `feature-development.yaml`）, `tests/test_workflow_agent_optional.py:79`（`full-cycle.yaml`）, `tests/test_verdict_e2e.py:172-175`（root `workflows/` glob + `pytest.skip`） | 削除/リネームに伴い更新必須。silent skip 化（verdict_e2e）も回避対象。 |
| cycle 構造名 vs workflow ファイル名 | `tests/fixtures/test_workflow.yaml:9`（`cycles: review-cycle:`）, `tests/test_skill_harness_adaptation.py:158`（`assert "review-cycle" in cycle_names`） | `review-cycle` は cycle 構造名で production workflow 非依存。grep 誤検出の除外根拠。 |
| `[execution].agent_runner` / `--agent-runner` の実在と表記差 | `kaji_harness/config.py:32`（`Literal["headless","interactive_terminal"]` = アンダースコア）, `kaji_harness/cli_main.py:180-181`（`--agent-runner` choices = ハイフン `interactive-terminal`） | runner backend は config で選択、CLI は一時 override。docs に両表記（TOML=`interactive_terminal` / CLI=`interactive-terminal`）を明示する根拠。 |
| `--from` / `--before` / `--step` の実在 | `kaji run --help`（`--from` 途中開始 / `--before` 途中終了 / `--step` 単発） | 途中起動を専用 YAML ではなく flag で行う方針の裏付け。 |
| 許容リスト（歴史的記述） | `docs/adr/003-skill-harness-architecture.md:27`（`workflows/*.yaml`）, `docs/adr/004-remove-gitlab-forge.md:11`（`full-cycle`）, `CHANGELOG.md`, `draft/**`, `.kaji/issues/**` | ADR / release 履歴 / 過去設計・Issue は歴史的記述として変更しない根拠。 |
| packaging 非依存 | `pyproject.toml`（`[tool.setuptools.package-data]` に workflow YAML 非同梱）, `kaji_harness/` 内に workflow ファイル名ハードコード参照なし | 削除/リネームが runtime / packaging を壊さない根拠。scope を `kaji_harness/` 外に保てる。 |
| テスト規約（恒久テスト不要 4 条件 / 変更タイプ別検証） | `docs/dev/testing-convention.md` | config/docs/skill/test-reference 整理で新規恒久テストを原則不要とし変更固有検証に寄せる根拠。 |
