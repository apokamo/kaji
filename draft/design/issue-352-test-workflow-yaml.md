# [設計] workflow YAML の official / custom 分離と実ファイル I/O テストの Medium 是正

Issue: #352

## 概要

`.kaji/wf/` 直下に混在している workflow YAML 10 本を、kaji 公式提供・更新・テスト対象の
`.kaji/wf/official/**` と、リポジトリ固有・利用者管理の `.kaji/wf/custom/**` に再配置し、
pytest の inventory / contract / invariant を `official/**` のみに限定する。あわせて、repo 上の
official YAML を `load_workflow()` で読むテストの pytest marker を
`docs/dev/testing-convention.md` の判定基準どおり `medium` へ是正する。

## 背景・目的

### 現状の問題

1. **所有権がパスから判定できない**: `.kaji/wf/` 直下に `dev.yaml`（公式提供）と
   `dev-thorough-fable.yaml`（このリポジトリ固有の model variant）が同居しており、
   「kaji が更新・テストする対象か」「利用者が所有する variant か」をパスで機械判定できない。
2. **marker がリソース依存と一致していない**: repo 上の実 YAML を glob / `load_workflow()` する
   テストに `pytest.mark.small` が付いている（現物: `tests/workflows/test_workflow_set_invariants.py:34`、
   `tests/workflows/test_review_code_routing.py:44`、`tests/workflows/test_self_retry_cycle_membership.py:31`、
   `tests/workflows/test_review_poll_exec_migration.py:68`、`tests/workflows/test_incident_workflow.py:61` 他、
   `tests/test_dev_workflow.py:37`、`tests/test_series_create_skill.py:10`、
   `tests/test_workflow_parser.py:278`）。`docs/dev/testing-convention.md` § 判定基準は
   「DB / ファイル / 内部サービス結合あり → Medium」と定義しており、
   `make test-small` / `make test-medium` の責務境界が規約と食い違っている。

### ユースケース

| 主体 | 状況 | 行為 | 便益 |
|------|------|------|------|
| kaji maintainer | workflow YAML を追加・変更する | `official/**` か `custom/**` かをパスで決め、pytest は `official/**` にのみ適用する | 維持・テスト対象がパスから機械的に決まり、custom variant 追加が公式回帰テストを壊さない |
| 下流リポジトリ利用者（starter 利用者含む） | agent / model / effort を変えた workflow を使いたい | `official/**` を直接編集せず `custom/**` へコピーして管理する | kaji 更新時の衝突箇所を事前に特定できる |
| テストを書く / レビューする開発者 | 実 YAML を読むテストに `small` が付いている | official の実ファイル I/O を `medium` へ是正する | サイズ分類の妥当性を規約だけで検証できる |

### 到達したい状態

- `.kaji/wf/official/**` を見れば公式提供・更新・テスト対象が機械的に識別できる
- `.kaji/wf/custom/**` が kaji の pytest 回帰対象外であることがパスから明確
- official の実 YAML を読むテストが `medium`、純粋 parser / 合成 object のテストが `small`

## インターフェース

本 Issue は Python の公開 API を追加しない。契約の実体は **ファイル配置パス**、
**Makefile ターゲットの検証対象集合**、**pytest marker** の 3 つである。

### 入力（変更後の契約）

#### 1. workflow YAML の配置パス

```text
.kaji/wf/
├── official/                       # kaji 公式提供・更新・テスト対象
│   ├── dev.yaml
│   ├── docs.yaml
│   ├── incident.yaml
│   └── local/
│       ├── dev-local.yaml
│       └── docs-local.yaml
└── custom/                         # リポジトリ固有・利用者管理
    ├── dev/
    │   ├── dev-thorough.yaml
    │   └── dev-thorough-fable.yaml
    └── docs/
        ├── docs-codex.yaml
        ├── docs-fable.yaml
        └── docs-thorough-codex.yaml
```

`custom/operations/` は予約カテゴリとし、実ファイルが発生するまで作成しない（Git は空 dir を追跡できない）。

移動元 → 移動先は Issue 本文「現行ファイルの移動先」表と 1:1 で一致させる（10 本）。

#### 2. 利用者から見た起動コマンド（BREAKING）

| 旧 | 新 |
|----|----|
| `kaji run .kaji/wf/dev.yaml <id>` | `kaji run .kaji/wf/official/dev.yaml <id>` |
| `kaji run .kaji/wf/dev-local.yaml <id>` | `kaji run .kaji/wf/official/local/dev-local.yaml <id>` |
| `kaji run .kaji/wf/dev-thorough.yaml <id>` | `kaji run .kaji/wf/custom/dev/dev-thorough.yaml <id>` |
| `kaji validate .kaji/wf/*.yaml` | `make validate-workflows`（下記 3 を参照） |

旧パスの alias / symlink / fallback は追加しない（`docs/adr/008-no-backward-compat-layer.md` 決定 1）。

#### 3. `make validate-workflows` の検証対象集合

**tracked な** `.kaji/wf/**` 配下の `*.yaml` 全件（official + custom）を `kaji validate` に渡す。

- 「tracked」は `git ls-files` で決定する。untracked な作業中 YAML を `make check` の失敗要因にしない
- 列挙結果が空の場合は **エラー終了する**。`kaji validate` は引数 0 件のとき
  `failed == 0` で `EXIT_OK` を返す（`kaji_harness/commands/validate.py:51-96`）ため、
  glob 誤りが silent pass になる。非空ガードで fail-loud にする

#### 4. pytest の対象集合

| 対象 | pytest（inventory / contract / invariant） | `make validate-workflows`（L1/L2/L3） |
|------|:---:|:---:|
| `.kaji/wf/official/**/*.yaml` | ✅ | ✅ |
| `.kaji/wf/custom/**/*.yaml` | ❌（意図的に対象外） | ✅ |

### 出力（副作用）

- `git mv` による 10 ファイルの移動（内容は 1 byte も変更しない）
- Makefile / tests / docs / `.kaji/series/*.yaml` / `.claude/skills/**` の旧パス参照更新
- CHANGELOG の BREAKING エントリ追加
- 新規 Python モジュール・CLI オプション・設定キーの追加はなし

### 使用例

```bash
# 公式 workflow の起動（新パス）
kaji run .kaji/wf/official/dev.yaml 352

# このリポジトリ固有の variant（custom）
kaji run .kaji/wf/custom/dev/dev-thorough-fable.yaml 352

# tracked な official + custom を L1/L2/L3 で一括検証
make validate-workflows

# 下流利用者が model を変えたいとき（official は編集しない）
mkdir -p .kaji/wf/custom/dev
cp .kaji/wf/official/dev.yaml .kaji/wf/custom/dev/dev-opus.yaml
$EDITOR .kaji/wf/custom/dev/dev-opus.yaml   # name: を dev-opus に変更し agent/model/effort を編集
kaji validate .kaji/wf/custom/dev/dev-opus.yaml
```

### エラー時の挙動

| 事象 | 挙動 |
|------|------|
| 旧パスで `kaji run` / `kaji validate` を実行 | 既存の "File not found" 経路でそのまま失敗する（`validate.py:57-59`）。互換 fallback は追加しない |
| tracked workflow が 0 件 | `make validate-workflows` が非空ガードで非 0 終了（silent pass にしない） |
| `official/**` の inventory が期待集合と不一致 | Medium テストが差分（欠落 / 意図しない追加 / 階層 drift）を明示して fail |
| `custom/**` の構文・参照破損 | `make validate-workflows` の L1/L2/L3 で検出。pytest では検出しない（意図的トレードオフ） |

## 制約・前提条件

- `kaji_harness/` 内に `.kaji/wf` のハードコードは存在しない（`rg '\.kaji/wf' kaji_harness/` が 0 件）。
  workflow パスは CLI 引数 / series YAML から与えられるため、**実装コードの変更は不要**
- `.kaji/series/*.yaml` の `workflow:` は repo-relative パスで、`kaji_harness/series/loader.py:47-57`
  が `repo_root / member.workflow` の実在を検査する。旧パスのままだと `not found` で失敗するため更新必須
- `tests/fixtures/series/*.yaml` は `SeriesConfig.model_validate` と fake launcher でしか使われず
  実ファイル解決を伴わない（`tests/test_series_runner.py:373-399`）。挙動影響はないが、
  現実の repo-relative パスを表す fixture として新パスへ更新する
- `make verify-docs` は `docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md` の
  markdown リンクを検査する（`Makefile:38-39`）。`docs/guides/python-starter*.md` の
  `[dev.yaml](../../.kaji/wf/dev.yaml)` は **リンク切れになるため更新必須**
- managed starter repository は本 Issue で変更しない。kaji Release 後の starter-sync で追随する
  （`docs/operations/release/starter-sync-runbook.md`）
- 歴史的 artifact（`CHANGELOG.md` の過去エントリ、`draft/design/` の過去設計書、`.kaji-artifacts/`）は
  一括置換しない。active な実行参照のみ移行する

## 方針

### Phase 1: ファイル移動

`git mv` で 10 本を移動する。YAML の中身（step / cycle / routing / agent / model / effort / `name:` /
`requires_provider:`）は変更しない。`name:` は filename stem と一致する既存不変条件を維持するため、
ファイル名も変えない。検証は `git diff --cached -M --stat` で全件 `R100` を確認する。

`.kaji/wf/**` 内の YAML から他の workflow パスを参照している箇所は存在しない
（`rg '\.kaji/wf' .kaji/wf/` が 0 件）ため、移動によって YAML 間の整合が崩れることはない。

### Phase 2: Makefile

`validate-workflows` を tracked 列挙 + 非空ガードへ書き換える。

```make
validate-workflows:
	@files=$$(git ls-files -- '.kaji/wf' | grep '\.yaml$$'); \
	if [ -z "$$files" ]; then \
	  echo "no tracked workflow YAML under .kaji/wf/" >&2; exit 1; \
	fi; \
	kaji validate $$files
```

`help` ターゲットの説明文（Makefile:57）も official + custom を検証する旨へ更新する。

### Phase 3: テストの棚卸しと是正

判定軸は `docs/dev/testing-convention.md` § 判定基準（外部依存・ファイル I/O の有無）と、
Issue 本文 `### サイズ分類`（`tmp_path` にファイルを作成して読む = Medium）である。

#### 3-0. 棚卸しの母集団と再現コマンド

母集団を混在させないため、**4 つの独立した inventory** を別々のコマンドで確定する。
以下は `chore/352` worktree（base commit `c83f573`）での実測値であり、実装・review-code で同じ
コマンドを再実行して同一集合を再現できる。

| # | 母集団 | 再現コマンド | 実測 | 位置づけ |
|---|--------|-------------|------|----------|
| A | `load_workflow()` を **直接呼ぶ** テスト | `rg -l 'load_workflow\(' tests` | **14 ファイル** | marker 是正の主対象 |
| B | `.kaji/wf` を **文字列リテラル**で持つテスト | `rg -l '\.kaji/wf' tests` | **18 ファイル** | パス文字列更新の対象 |
| D | `.kaji/wf` を **`Path` segment 形**（`/ ".kaji" / "wf"`）で組み立てるテスト | `rg -l '"\.kaji"\s*/\s*"wf"' tests` | **15 ファイル** | B の regex では検出できない経路 |
| C | `load_workflow_from_str()` のみのテスト | `rg -l 'load_workflow_from_str' tests` | **6 ファイル** | 純粋 parser。ファイル I/O なし → Small のまま |

**A / B / D は包含関係にない**。`test_baseline.py` や `test_series_create_skill.py` は
`REPO_ROOT / ".kaji" / "wf"` の segment 形で実 YAML を読むため B の regex には現れず、
A ∖ B = 7 件、D ∖ B = 9 件になる。**母集団 B だけで棚卸しすると実 YAML を読むテストを取りこぼす**
（初版設計の欠落原因）。したがって棚卸し対象は **A ∪ B ∪ D = 27 件**（`.py` 25 + fixture YAML 2）とする。

```bash
# 棚卸し対象の確定（27 件）
{ rg -l 'load_workflow\(' tests; rg -l '\.kaji/wf' tests; rg -l '"\.kaji"\s*/\s*"wf"' tests; } | sort -u
```

27 件を下記 3-1（Medium 是正 **8**）/ 3-2（marker 妥当・対象更新 **4**）/ 3-3（変更なし **4**）/
3-4（文字列のみ **11**）へ排他分類する（8 + 4 + 4 + 11 = 27）。

#### 3-1. Medium 是正が必要（marker と実リソース依存が不一致）

| ファイル | 現状 marker | 実リソース依存 | 対応 |
|----------|------------|----------------|------|
| `tests/workflows/test_workflow_set_invariants.py` | `small`（class） | repo 上の official YAML を glob + load | `medium` へ。期待集合を official の相対パス 5 件へ差し替え |
| `tests/workflows/test_review_code_routing.py` | `small` ×4 | 同上（module import 時に glob + load） | `medium` へ。class 名 `TestReviewCodeRoutingSmall` と docstring の "Small" を是正。glob を `official/**` へ |
| `tests/workflows/test_self_retry_cycle_membership.py` | `small`（class） | 同上 | `medium` へ。docstring の "Small" を是正。glob を `official/**` へ |
| `tests/workflows/test_review_poll_exec_migration.py` | `small`（static class） | 実 YAML 3 本を load | `medium` へ。docstring / セクションコメントの "Small" を是正。対象を official の `dev.yaml` / `docs.yaml` に限定 |
| `tests/workflows/test_incident_workflow.py` | `small` ×6 | 実 YAML + skill / agent / template の `read_text()` | `medium` へ。`WF_PATH` を `official/incident.yaml` へ。skill 本文 assertion（`:176`）を新パス文字列へ |
| `tests/test_dev_workflow.py` | `small` ×4（`TestDevWorkflowSmall`） | 実 `dev.yaml` を load | `medium` へ。class 名と docstring / セクションコメントを是正。`DEV_WORKFLOW_PATH` を `official/dev.yaml` へ |
| `tests/test_workflow_parser.py:275-286`（`TestFileBasedLoading::test_load_workflow_from_file`） | `small` | `tmp_path` へ YAML を書いて `load_workflow()` で読む | `medium` へ。Issue 本文 `### サイズ分類` の「`tmp_path` にファイルを作成して読む: Medium」に従う。同ファイルの他 class（文字列 parser）は Small のまま |
| `tests/test_series_create_skill.py`（module 単位 `pytestmark = pytest.mark.small`、`:10`） | `small` | `SKILL.parents[3] / ".kaji" / "wf"` を glob して実 YAML を `yaml.safe_load` し、`.claude/skills/**/SKILL.md` も `read_text()` | `medium` へ。glob を `official/**` へ。custom の description 契約 assertion を削除（下記） |

`test_incident_workflow.py` は workflow YAML に加えて `.claude/skills/**` / `.claude/agents/**` /
template を `read_text()` しており、YAML を読まないクラス（`TestIncidentCycleSlashWrapper` /
`TestIncidentForbiddenVocabulary` 等）も **ファイル I/O を伴うため一律 `medium`** とする。
`test_series_create_skill.py` も module 単位 marker を持ち、全 test が `SKILL.md` の
`read_text()` を行うため同じ理由で一律 `medium` とする。

**`test_series_create_skill.py::test_builtin_workflow_descriptions_define_unique_auto_selection`
の対象縮小（要注意）**: 現状は `.kaji/wf/*.yaml` 全 10 本の `description` を読み、
`dev.yaml` / `docs.yaml` 以外のすべてに「series 自動選択対象外」の記載を要求している。
移動後は glob を `official/**/*.yaml` に限定し、対象を official 5 本（`dev.yaml` / `docs.yaml` /
`incident.yaml` / `local/dev-local.yaml` / `local/docs-local.yaml`）とする。custom 5 本の
description 契約は pytest では検証しなくなる（人間決定「pytest は official のみ」に従う。
3-5 と同種の意図的トレードオフ）。

`test_workflow_parser.py` は **ファイル単位ではなく class 単位**で是正する。`TestFileBasedLoading`
のみ `medium` とし、`load_workflow_from_str()` ベースの class は Small を維持する
（Issue 完了条件「純粋な parser / validation unit test など、ファイル I/O を伴わない Small test は
変更していない」を満たすため）。

#### 3-2. marker は既に妥当 → パス起点・対象集合のみ更新

| ファイル | marker | 対応 |
|----------|--------|------|
| `tests/test_recovery_workflow_inventory.py` | `medium`（module 単位） | glob を `official/**` へ。docstring の `.kaji/wf/*.yaml` 表記も更新 |
| `tests/test_workflow_validator.py:740-748` | `medium` | glob を `official/**` へ |
| `tests/test_workflow_agent_optional.py:73-82` | `medium`（class） | `dev.yaml` パスを `official/dev.yaml` へ |
| `tests/test_baseline.py:230-243`（`test_dev_workflows_route_design_approval_through_agentless_baseline`） | `medium` | `DEV_WORKFLOWS` を official のみへ縮小（下記） |

**`test_baseline.py` の `DEV_WORKFLOWS` 縮小（要注意）**: 現状の定数は
`("dev.yaml", "dev-thorough.yaml", "dev-thorough-fable.yaml", "dev-local.yaml")` で、
うち `dev-thorough.yaml` / `dev-thorough-fable.yaml` は **custom へ移動する**。
人間決定「pytest の inventory / 契約 / 構造 invariant は official のみ」に従い、
対象を `("dev.yaml", "local/dev-local.yaml")` へ縮小する。これにより baseline step の
routing 不変条件（`baseline.on == {"PASS": "implement", "ABORT": "end"}` 等）は
custom variant では pytest で保証されなくなる。3-5 の `review-poll` と同種の
**意図的なトレードオフ**として設計書・CHANGELOG に記録する。

#### 3-3. 変更しない（4 件）

| ファイル | 理由 |
|----------|------|
| `tests/test_preflight.py:61-79` | `_make_runner` が `tmp_path` 上の合成 repo に `wf.yaml` を書いて読む。呼び出し元は `TestCanonicalIssueId`（`medium`、`:102/115/134/151`）のみで **既に規約どおり**。repo 上の official YAML を読まない |
| `tests/test_runner.py:186-204` | 同上。呼び出し元は `TestRunnerIssueContextNormalization` / `TestRunnerFailFastOnExplicitProvider`（いずれも `medium`、`:224/238/264/283/296/310/322`）。`small` class（`:60/91`）からは呼ばれない |
| `tests/test_skill_harness_adaptation.py` | `tests/fixtures/test_workflow.yaml` を読む。`.kaji/wf/` 非依存。呼び出し箇所（`:142-192`）は既に `medium` |
| `tests/test_cli_validate.py:201,444` | 母集団 D に該当するが、参照は `tmp_path / ".kaji"`（config dir）であり `.kaji/wf` ではない。実 workflow に依存しないため `small` のまま |

> 母集団 C の `tests/test_workflow_requires_provider.py` / `test_exec_step_parser.py` は
> A ∪ B ∪ D に含まれない（`load_workflow_from_str()` のみ）。ファイル I/O を伴わないため
> `small` を維持し、本 Issue では一切変更しない。

#### 3-4. パス文字列のみ更新（11 件）

| ファイル | 対応 |
|----------|------|
| `tests/test_verdict_e2e.py:172-184` | `large`。glob を `official/**` へ。5 本前提の docstring を official 5 本前提として明示 |
| `tests/test_series_io.py` / `test_series_cli.py` / `test_series_cli_large_local.py` / `test_series_models.py` / `test_series_runner.py` | `.kaji/wf/...` を **文字列として**扱うのみ（`tmp_path` 上に合成 YAML を作るか、fake launcher へ渡す）。文字列を新パスへ更新。marker は変更しない |
| `tests/test_recovery_plan.py` / `test_recovery_models.py` / `test_recovery_report.py` | resume command / workflow_path の文字列。新パスへ更新。marker は変更しない |
| `tests/fixtures/series/epic-291.yaml` / `standalone.yaml` | fixture の `workflow:` 文字列を新パスへ更新（`dev-thorough-fable.yaml` は `custom/dev/` 配下へ） |

#### 3-4b. 本 Issue の棚卸し境界（scope 外として明示）

母集団 A / B に含まれないが `tmp_path` へファイルを書く `small` テストが存在する。例:
`tests/test_timeout_config.py:89-193`（`TestExecutionConfigValidation`、`small`）は
`_write_config()`（`:41-52`）経由で `tmp_path` に `.kaji/config.toml` を書き、
`git init` の `subprocess.run` まで実行する。

これらは **workflow YAML ではなく config.toml の棚卸し**であり、Issue 完了条件が対象とする
「`load_workflow()` 呼び出しの棚卸し」の外にある。`docs/dev/testing-convention.md`
§ 既存テストの棚卸し基準の「棚卸しが広範囲になり、複数ファイルの削除・再分類・ワークフロー変更を
伴う場合は、この Issue で抱え込まず派生 Issue を切って追跡する」に従い、本 Issue では扱わず
別 Issue 候補として Issue コメントで報告する。

#### 3-5. official inventory の列挙方式

`test_workflow_set_invariants.py` の期待集合を **`.kaji/wf/official/` からの相対パス文字列集合**で持ち、
`rglob("*.yaml")` の実測集合と完全一致で比較する。

```python
OFFICIAL_DIR = REPO_ROOT / ".kaji" / "wf" / "official"
EXPECTED_OFFICIAL = {
    "dev.yaml", "docs.yaml", "incident.yaml",
    "local/dev-local.yaml", "local/docs-local.yaml",
}
found = {p.relative_to(OFFICIAL_DIR).as_posix() for p in OFFICIAL_DIR.rglob("*.yaml")}
assert found == EXPECTED_OFFICIAL
```

stem 集合ではなく相対パス集合にすることで、(a) 欠落、(b) 意図しない追加、
(c) `local/` 階層の drift、(d) custom YAML が official 側へ紛れ込む事故 を同一 assertion で検出できる。
`name:` == filename stem の既存不変条件は維持する。

#### 3-6. custom を pytest 対象外にする方法と、失われるカバレッジ

custom を「除外リストで引く」のではなく、**glob の起点を `official/` に限定する**ことで
構造的に対象外にする。除外リストは custom カテゴリ追加時に保守が発生し、書き忘れると
custom が公式回帰へ混入するため採らない。

これに伴い、custom へ移る 5 本については次の不変条件が pytest で保証されなくなる。
いずれも Issue で人間が決定した「公式品質保証は official のみ」に従う**意図的なトレードオフ**であり、
設計書・CHANGELOG の双方に記録する。

| 失われる検証 | 現行の担保箇所 | 対象から外れる workflow |
|--------------|----------------|-------------------------|
| `review-poll` の exec argv が `["kaji","pr","review-poll"]` であること | `test_review_poll_exec_migration.py` | `dev-thorough.yaml` |
| baseline step の routing（`{"PASS":"implement","ABORT":"end"}`）と agentless 構成 | `test_baseline.py:230-243` | `dev-thorough.yaml` / `dev-thorough-fable.yaml` |
| `description` の series 自動選択契約（「series 自動選択対象外」の明記） | `test_series_create_skill.py` | custom 5 本 |
| `review-code` routing / self-RETRY cycle 所属 / workflow set inventory | 3-1 の各テスト | custom 5 本 |

移行後も custom に残る保証は、`make validate-workflows` の L1（parse / schema）/
L2（workflow 内参照整合）/ L3（skill metadata 解決）である。**振る舞い**の回帰は kaji の
テストスイートでは検出しない。

### Phase 4: 参照更新（active な実行参照のみ）

更新対象は次のコマンドで確定した（`chore/352` worktree、base commit `c83f573`）。

```bash
# active な旧パス参照ファイルの列挙（21 件）
rg -l '\.kaji/wf' -g '!.kaji-artifacts/**' -g '!draft/**' -g '!CHANGELOG.md' -g '!tests/**' .
```

| 区分 | 対象 |
|------|------|
| 実行設定 | `.kaji/series/epic-324-workflow-improvements.yaml` / `issues-338-339.yaml` / `issues-346-349.yaml` |
| ビルド | `Makefile:21`（Phase 2）、`Makefile:57`（help 文言） |
| skill（実行コマンド） | `.claude/skills/review-cycle/SKILL.md`（`kaji run .kaji/wf/dev.yaml`）、`.claude/skills/incident-cycle/SKILL.md`（`kaji run .kaji/wf/incident.yaml` / `kaji validate`。description 行含む） |
| skill（例示・参照） | `.claude/skills/kaji-run-verify/SKILL.md`、`.claude/skills/issue-design/SKILL.md`（`.kaji/wf/dev.yaml:134` / `:82` の行参照。行番号は移動で不変）、`.claude/skills/grill-me/SKILL.md`、`.claude/skills/series-create/SKILL.md`（`.kaji/wf/*.yaml` の再帰探索表記へ） |
| skill（変更不要） | `.claude/skills/i-doc-update/SKILL.md`（`git add .kaji/wf/` は dir 指定のため有効） |
| docs（パス更新） | `README.md` / `README.ja.md` / `llms.txt` / `docs/README.md:33` / `docs/ARCHITECTURE.md` / `docs/dev/development_workflow.md` / `docs/dev/docs_maintenance_workflow.md` / `docs/cli-guides/failure-recovery{,.ja}.md` / `docs/cli-guides/interactive-terminal-runner{,.ja}.md` / **`docs/cli-guides/local-mode.md:143`** / `docs/cli-guides/local-mode.ja.md:141` / `docs/operations/local-mode-runbook{,.ja}.md` |
| docs（構造の是正が必要） | **`docs/dev/workflow_guide.md`**、**`docs/reference/python/naming-conventions.md:151-156`**（下記） |
| docs（正本の新設） | `docs/dev/workflow-authoring.md` § ファイル配置 |
| docs（リンクのみ） | `docs/guides/python-starter{,.ja}.md` |
| 変更しない | `CHANGELOG.md` の過去エントリ、`draft/design/` の過去設計書、`.kaji-artifacts/` |

#### `docs/dev/workflow_guide.md` の是正（パス置換では不足）

AGENTS.md / CLAUDE.md から参照される active な運用ガイドであり、単なるパス置換では
所有権境界と矛盾する。次の 3 点を構造として直す。

1. **workflow 一覧表（`:14-18`）**: 現状は 5 本を並列に並べ、`dev-thorough.yaml` を含む。
   official（`dev` / `docs` / `incident` / `local/dev-local` / `local/docs-local`）と
   custom（`custom/dev/**` / `custom/docs/**`）を **別表**に分け、所有権列を持たせる
2. **通常運用の選択表（`:29-32`）と workflow 一覧（`:84-87`）**: `dev-thorough.yaml` を
   「このリポジトリ固有の custom variant」と明示し、official と同列に推奨しない
3. **実行例（`:47-49`, `:104-129`, `:147`, `:218-232`, `:242-243`, `:280-281`, `:307-308`）と
   `:313-319` の dev / dev-thorough 節**: パスを新レイアウトへ更新する

#### `docs/reference/python/naming-conventions.md` の是正

`:151-156` は「ワークフロー・スキルファイル」のディレクトリ例として flat layout
（`.kaji/wf/` 直下に `dev.yaml` / `docs.yaml`）を規約例として提示している。これは
新レイアウトと矛盾するため、`official/` / `custom/` を含む例へ差し替える。
初版設計の影響ドキュメント評価では「docs/reference/ は影響なし」としていたが、
実測（`rg -n '\.kaji/wf' docs/reference/`）で該当が 1 件あり、評価を訂正する。

#### 旧パス残存の検査（3 パターン）

`rg` の direct filename regex だけでは、glob 表記と `Path` segment 形を取りこぼす。
実装完了時に次の 3 本すべてを実行し、残存 0 件を確認する。

```bash
# (1) 直接ファイル名参照（flat layout）
rg -n '\.kaji/wf/[a-z0-9-]+\.yaml' -g '!CHANGELOG.md' -g '!draft/**' -g '!.kaji-artifacts/**' .

# (2) 旧 glob 表記（official/custom の再帰を前提としない）
rg -n '\.kaji/wf/\*\.yaml' -g '!CHANGELOG.md' -g '!draft/**' -g '!.kaji-artifacts/**' .

# (3) Path segment 形 + flat 前提の glob（コード側の取りこぼし検出）
rg -n '"\.kaji"\s*/\s*"wf"' tests kaji_harness scripts
```

(1) は移動対象 10 本のファイル名、(2) は `Makefile:21` / `docs/README.md:33` /
`docs/ARCHITECTURE.md:34` / `docs/dev/workflow-authoring.md:486` /
`docs/dev/development_workflow.md:146` / `docs/guides/python-starter{,.ja}.md` /
`tests/` 内 docstring、(3) は `test_baseline.py` / `test_series_create_skill.py` 等
15 ファイルを検出する。(2) と (3) は**残存 0 件ではなく「新レイアウト前提の表記に
更新済みであること」をレビューする**用途（`official/**/*.yaml` 等は正当な残存）。

#### 所有権ドキュメントの正本

`docs/dev/workflow-authoring.md` § ファイル配置 を official/custom 契約の正本とし、次の 4 点を記載する。

1. **所有権**: `official/**` = kaji 公式提供・更新・テスト対象 / `custom/**` = リポジトリ固有・利用者管理
2. **official の直接編集禁止**: 下流利用者は `official/**` を編集しない
3. **custom へのコピー手順**: コピー → `name:` を新 stem に変更 → agent/model/effort を編集 → `kaji validate`
4. **責務境界**: pytest の inventory / contract / invariant は official のみ。tracked custom は
   `make validate-workflows` の L1/L2/L3 静的検証のみ。custom の**振る舞い**回帰は kaji が保証しない

`README.md` / `README.ja.md` / `llms.txt` には 1〜2 行の要約と正本へのリンクのみを置き、契約を二重管理しない。

#### starter guide の扱い

`docs/guides/python-starter{,.ja}.md` は **starter repository の中身**を説明する文書であり、
starter 自体は本 Issue で変更しない。したがって:

- starter のレイアウト記述（"Five workflow YAMLs under `.kaji/wf/`"、
  `uv run kaji validate .kaji/wf/*.yaml` 等）は **変更しない**
- kaji リポジトリ内のファイルを指す相対リンク（`[dev.yaml](../../.kaji/wf/dev.yaml)`、
  en / ja 各 2 箇所）は **新パスへ更新する**（放置すると `make verify-docs` が失敗する）
- starter は kaji Release 後の starter-sync で追随する旨を注記する

### Phase 5: CHANGELOG（BREAKING）

`docs/adr/008-no-backward-compat-layer.md` 決定 2 の 3 要素を満たす形で記載する。

- **壊れる契約**: `.kaji/wf/<name>.yaml` を直接指す `kaji run` / `kaji validate` / series YAML の
  `workflow:` / スクリプト・CI の参照がすべて not found になる
- **影響の判定方法**: `rg -n '\.kaji/wf/[a-z0-9-]+\.yaml' .` で旧パス参照を列挙する
- **適用指針**: official 5 本は `official/` 配下（local provider 用は `official/local/`）、
  model / agent variant は `custom/<用途>/` へ移す。未カスタマイズなら新レイアウトの再コピーで可。
  custom の振る舞いは kaji の pytest 対象外である旨（3-6 の「失われる検証」一覧を含む）も明記する

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| workflow の所有権境界 | `official/**` = 公式提供物、`custom/**` = 利用者管理 | Issue 本文「配置方針」/「決定事項」表（人間決定。grill-me 最終確認で承認） | 移動先 10 件の 1:1 対応、`git mv` による rename 100% 維持を規定 |
| official の編集権限 | 下流は `official/**` を直接編集せず `custom/**` へコピー | Issue「決定事項」表（grill-me 質問への回答 `yes`） | `docs/dev/workflow-authoring.md` を正本とし、コピー手順（`name:` 変更 → validate）を明文化 |
| pytest の保証対象 | inventory / 契約 / 構造・routing invariant は official のみ | Issue 本文「テスト方針」（人間決定） | 除外リストではなく glob 起点を `official/` に限定する構造的分離を採用 |
| 静的 validation の対象 | tracked な official + custom 双方を L1/L2/L3 で検証 | Issue「決定事項」表（grill-me 質問への回答「はい」） | `git ls-files` による tracked 列挙 + 空集合ガードで silent pass を防止 |
| provider directory | `official/github/` を作らず、直下 = GitHub、`official/local/` = local | Issue 本文「provider directory 方針」（人間決定） | 期待 inventory を相対パス集合にして `local/` 階層の drift も検出 |
| custom の用途分類 | `custom/dev/` / `custom/docs/`。`custom/operations/` は予約、空 dir は作らない | Issue 本文「配置方針」（人間決定） | 実ファイル発生まで作成しない旨を配置図に明記 |
| 旧パスの互換性 | alias / symlink / fallback を追加しない | `docs/adr/008-no-backward-compat-layer.md` 決定 1（既存正本） | 旧パスは既存の "File not found" 経路でそのまま失敗させる |
| BREAKING の告知 | CHANGELOG に壊れる契約 / 判定方法 / 適用指針を記載 | 同 ADR 決定 2（既存正本） | 判定コマンドを `rg -n '\.kaji/wf/[a-z0-9-]+\.yaml' .` に具体化 |
| managed starter | #352 で starter repository を変更しない | `docs/operations/release/starter-sync-runbook.md`（既存正本） | starter guide は「レイアウト記述は据え置き、kaji repo 内リンクのみ更新」に分解 |
| テストサイズの正本 | 実行速度や既存 marker ではなく規約のファイル I/O 基準で判定 | `docs/dev/testing-convention.md` § 判定基準（人間決定・既存正本） | 母集団 A ∪ B ∪ D = 27 件を 3-1〜3-4 に排他分類し、Medium 是正を 8 ファイルに確定 |
| `tmp_path` テストのサイズ | `tmp_path` へ書いて読むテストも Medium | Issue 本文 `### サイズ分類`「`tmp_path` にファイルを作成して読む: Medium」（人間決定） | `test_workflow_parser.py::TestFileBasedLoading` を class 単位で Medium へ。文字列 parser class は Small 維持 |
| 棚卸しの母集団定義 | `load_workflow(` / 文字列リテラル / `Path` segment 形の 3 コマンドの和集合を対象にする | **AI の仮定**。単一 regex では `REPO_ROOT / ".kaji" / "wf"` 形の実 YAML 参照（`test_baseline.py` / `test_series_create_skill.py`）を取りこぼすため。後段の検査先: design review / code review（同コマンドの再実行で再現可能） | 3-0 に 4 母集団と実測件数、再現コマンドを記載 |
| 期待 inventory の表現 | stem 集合ではなく `official/` 相対パス集合で完全一致比較 | **AI の仮定**。欠落 / 追加 / 階層 drift / custom 混入を同一 assertion で検出するため。後段の検査先: design review / code review | `rglob` 実測との `==` 比較を規定 |
| `make validate-workflows` の実装 | `git ls-files` による tracked 列挙 + 非空ガード | **AI の仮定**。「tracked を対象」という人間決定を機械的に表現でき、`kaji validate` の引数 0 件が `EXIT_OK` を返す（`validate.py:51-96`）silent pass を塞ぐため。後段の検査先: code review / `make check` | Makefile レシピの形を規定 |
| custom 移動に伴う pytest カバレッジ減 | `review-poll` exec argv / baseline routing / `description` の series 契約 / routing invariant を custom では検証しない | **AI の仮定**（人間決定「pytest は official のみ」の機械的帰結）。後段の検査先: design review / code review | 3-6 に「失われる検証 × 対象 workflow」の一覧を作り、CHANGELOG にも記載 |
| `test_series_create_skill.py` の対象縮小 | `description` の series 自動選択契約を official 5 本のみで検証 | **AI の仮定**（同上の帰結）。custom の description まで pytest で強制すると「custom は利用者所有」と矛盾するため。後段の検査先: design review / code review | glob を `official/**/*.yaml` に限定し、custom の assertion を削除 |
| module 単位 marker の扱い | `test_series_create_skill.py` / `test_incident_workflow.py` は一律 Medium | **AI の仮定**。両者とも全 test が `read_text()` によるファイル I/O を伴い、YAML を読まない test だけ Small に残すと 1 ファイル内で分類根拠が分岐して保守しにくいため。後段の検査先: design review / code review | marker の宣言単位（module / class）を特定してから置換する手順を Medium 節に明記 |
| test fixture 文字列 | `tests/fixtures/series/*.yaml` の workflow 文字列を新パスへ更新 | **AI の仮定**。挙動非依存（`test_series_runner.py:373-399` は fake launcher）だが現実整合を優先。後段の検査先: code review | marker は変更しないことを明記 |
| 共通 glob ヘルパ | 導入しない。各テストの glob 起点更新に留める | **AI の仮定**。現状 5 箇所の重複を増やさず、配置変更に対する diff を最小化するため。後段の検査先: design review / code review | 共通化は本 Issue の scope 外とする |
| 歴史的 artifact | CHANGELOG 過去エントリ / 過去設計書 / artifacts は置換しない | Issue「AI の仮定と後段の検査先」表（Issue 側で仮定として記録済み） | `make verify-docs` の検査範囲外であることを確認し、対象外として固定 |
| config.toml 系 `small` テストの棚卸し | 本 Issue では扱わず別 Issue 候補として報告 | **AI の仮定**。`docs/dev/testing-convention.md` § 既存テストの棚卸し基準「広範囲になる場合は派生 Issue を切って追跡する」に従う。Issue 完了条件の対象は `load_workflow()` 棚卸しであり workflow YAML 外。後段の検査先: design review / 人間による Issue 起票判断 | 3-4b に境界と実例（`test_timeout_config.py:89-193`）を明記 |

## テスト戦略

### 変更タイプ

**実行時アセットの再配置 + テスト分類是正 + docs**。`kaji_harness/` の Python 実装は変更しない
（`.kaji/wf` のハードコードが存在しないため）。ただし workflow YAML は宣言的な実行時定義であり、
配置が壊れれば `kaji run` が動かないため、docs-only 相当としては扱わず恒久回帰テストを維持する。

### Small テスト

**新規追加しない。既存 Small も変更しない。**

理由: 本 Issue は新しい実行時ロジック・分岐・バリデーションを一切追加しないため、
純粋関数レベルで新たに保護すべき振る舞いが存在しない。既存の純粋 parser テスト
（`test_workflow_requires_provider.py` の `load_workflow_from_str` 系、`test_exec_step_parser.py`）は
外部依存を持たず、規約どおり `small` が正しいため無変更で維持する。

`docs/dev/testing-convention.md` § 省略してよい理由に照らすと、
(1) 独自ロジックの追加変更を含まない、(2) 想定される不具合（パス破損・inventory drift）は
下記 Medium と `make validate-workflows` で捕捉済み、(3) Small を追加しても回帰検出情報が増えない、
(4) 本節が理由の記録である、の 4 条件を満たす。

### Medium テスト

本 Issue の主検証層。既存テストの **対象パス更新 + marker 是正**で構成し、次を保証する。

1. **official inventory の完全一致**（`test_workflow_set_invariants.py`）
   - `official/**/*.yaml` の相対パス集合が期待 5 件と一致する（欠落 / 意図しない追加 / `local/` 階層 drift /
     custom 混入をまとめて検出）
   - 各 workflow の `name:` が filename stem と一致する
2. **official 全件の parse + validate**（`test_workflow_validator.py` / `test_recovery_workflow_inventory.py`）
   - `load_workflow()` + `validate_workflow()` が official 全件で通る
   - 対象が空でないこと（glob 誤りによる silent skip の防止）を明示 assert する
3. **official に対する routing / 構造 invariant**
   - `review-code` の `BACK_IMPLEMENT` → implement / bare `BACK` → design（`test_review_code_routing.py`）
   - self-RETRY step の cycle 所属（`test_self_retry_cycle_membership.py`）
   - `review-poll` の exec argv（official の `dev.yaml` / `docs.yaml`）（`test_review_poll_exec_migration.py`）
   - `incident.yaml` の構造 / 遷移 / asset 実在、および `incident-cycle` skill が新パスを起動すること
     （`test_incident_workflow.py`。skill 本文 assertion を新パス文字列へ更新）
   - `dev.yaml` の final-check BACK_DESIGN / BACK_IMPLEMENT split（`test_dev_workflow.py`）
   - official の baseline step が agentless で `{"PASS":"implement","ABORT":"end"}` へ routing すること
     （`test_baseline.py`。`DEV_WORKFLOWS` を official 2 本へ縮小）
   - official の `description` が series 自動選択契約を満たすこと（`test_series_create_skill.py`。
     glob を `official/**` へ限定）
4. **file-based loader の検証**（`test_workflow_parser.py::TestFileBasedLoading`）
   - `tmp_path` の YAML を `load_workflow()` が読めること（Issue の サイズ分類に従い Medium へ移す）

marker 是正では、**`@pytest.mark.small` → `@pytest.mark.medium` の置換だけでなく、
class 名・docstring・セクションコメントに残る "Small" 表記も同時に是正する**
（`TestReviewCodeRoutingSmall` / `TestDevWorkflowSmall` / `# static regression（Small）` 等）。
marker と自然言語が食い違ったままだとレビュー時の分類根拠が再び崩れるため。

module 単位 `pytestmark`（`test_series_create_skill.py:10` / `test_recovery_workflow_inventory.py:24`）と
class 単位 marker が混在するため、**是正は「置換対象を marker の宣言単位で特定してから行う」**。
`test_workflow_parser.py` のように 1 ファイル内で Small / Medium が併存するケースでは、
class 単位で marker を分ける。

### Large テスト

**新規追加しない。** 既存の `test_verdict_e2e.py`（`large`）の glob 起点を `official/**` へ更新するに留める。

理由: 実 API 疎通や E2E データフローの契約自体は変わらず、変わるのは YAML の在処のみである。
E2E で新たに保護すべき振る舞いが増えないため、`docs/dev/testing-convention.md` の 4 条件を満たす。

### 変更固有の一時検証（恒久化しない）

| 検証 | 目的 |
|------|------|
| `git diff --cached -M --stat` で 10 件が `R100` | YAML 内容を 1 byte も変えていないことの証跡 |
| `git ls-files .kaji/wf` が新パス 10 件のみを返す | 旧パスの残骸と untracked 取りこぼしの検出 |
| Phase 4 § 旧パス残存の検査 の 3 コマンド（直接ファイル名 / 旧 glob 表記 / `Path` segment 形） | active な旧パス参照の残存検出。(1) は 0 件、(2)(3) は新レイアウト前提へ更新済みであることをレビュー |
| `{ rg -l 'load_workflow\(' tests; rg -l '\.kaji/wf' tests; rg -l '"\.kaji"\s*/\s*"wf"' tests; } \| sort -u` が 27 件 | 3-0 の棚卸し母集団を実装時に再現し、分類漏れがないことを確認 |
| `make validate-workflows` | tracked official + custom 全件の L1/L2/L3 通過 |
| `make test-small` / `make test-medium` | marker 是正後の分類境界が壊れていないこと |
| `make check` | 品質ゲート全体 |
| `make verify-docs` | starter guide 等の markdown リンク切れ検出 |

これらは今回の移行妥当性の確認手段であり、repo に恒久化する回帰価値は低い（移行は一度きり）。
一方、official inventory / routing invariant は移行後も継続的に価値があるため上記 Medium として恒久化する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規の技術選定はない。互換方針は既存 ADR 008 に従うのみで改訂不要 |
| docs/ARCHITECTURE.md | あり | `.kaji/wf/*.yaml` / `.kaji/wf/dev.yaml` の記述をパス更新 |
| docs/dev/workflow-authoring.md | あり | § ファイル配置を official/custom 契約の**正本**として全面更新。実行例のパス更新 |
| docs/dev/development_workflow.md | あり | builtin workflow のパス表記更新 |
| docs/dev/docs_maintenance_workflow.md | あり | `docs-local.yaml` の起動例を更新 |
| docs/dev/workflow_guide.md | **あり（構造是正）** | AGENTS.md 経由で参照される active な運用ガイド。workflow 一覧・通常運用の選択表・実行例が flat layout 前提で、`dev-thorough.yaml` を official と同列の通常運用 workflow として扱っている（Phase 4 § workflow_guide.md の是正） |
| docs/README.md | あり | `:33` の `.kaji/wf/*.yaml` 表記を新レイアウトへ更新 |
| docs/dev/testing-convention.md | なし | 規約自体は変更しない（本 Issue は規約への追随側） |
| docs/reference/python/naming-conventions.md | **あり** | `:151-156` がワークフロー配置例として flat layout を規約例に提示しており、新レイアウトと矛盾する（初版の「影響なし」評価を訂正） |
| docs/reference/（上記以外） | なし | 設定キー・API 契約の変更なし |
| docs/cli-guides/ | あり | failure-recovery{,.ja} / interactive-terminal-runner{,.ja} / **local-mode.md（`:143`、英語版）** / local-mode.ja.md（`:141`）の実行例パス更新 |
| docs/operations/local-mode-runbook{,.ja}.md | あり | local workflow の起動例パス更新 |
| docs/guides/python-starter{,.ja}.md | あり（限定） | kaji repo 内ファイルへの相対リンクのみ更新。starter レイアウト記述は据え置き、starter-sync 追随を注記 |
| README.md / README.ja.md / llms.txt | あり | 実行例のパス更新 + 所有権の 1〜2 行要約と正本リンク |
| AGENTS.md / CLAUDE.md | なし | workflow パスへの直接参照を持たない（`rg` で 0 件） |
| CHANGELOG.md | あり | BREAKING エントリ（壊れる契約 / 判定方法 / 適用指針） |
| .claude/skills/** | あり | Phase 4 表のとおり（実行コマンド 2 件は必須、例示 4 件、変更不要 1 件） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| テスト規約（サイズ判定の正本） | `docs/dev/testing-convention.md` § 判定基準 | 「外部 API / 実サービス疎通あり → Large / DB / ファイル / 内部サービス結合あり → Medium / それ以外（純粋関数・モック完結） → Small」。repo 上の YAML を `load_workflow()` で読むテストは "ファイル結合あり" に該当し Medium |
| テスト規約（省略条件） | `docs/dev/testing-convention.md` § 省略してよい理由 | 「1. 独自ロジックの追加・変更をほぼ含まない 2. 想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み 3. 新規テストを追加しても回帰検出情報がほとんど増えない 4. テスト未追加の理由をレビュー可能な形で説明できる」。Small / Large を新規追加しない根拠 |
| 互換方針 ADR | `docs/adr/008-no-backward-compat-layer.md` 決定 1 / 2 | 「後方互換レイヤを書かない。旧フォーマット読み取り・フォールバック・バージョン分岐を実装しない」「破壊的変更は CHANGELOG / GitHub Release notes の BREAKING セクションで明示し、壊れる契約 / 影響の判定方法 / 適用指針の 3 要素を必ず記載する」 |
| starter 追随 runbook | `docs/operations/release/starter-sync-runbook.md` | managed starter は kaji Release 後に tracking Issue と独立 review を通して追随する。#352 から starter repository を直接変更しない根拠 |
| `kaji validate` の実装 | `kaji_harness/commands/validate.py:51-96` | `failed = 0` 初期化、ループ後 `return EXIT_VALIDATION_ERROR if failed > 0 else EXIT_OK`。引数 0 件なら `EXIT_OK` を返すため、列挙が空になる glob 事故が silent pass になる。非空ガードが必要な根拠 |
| preflight のレイヤ定義 | `kaji_harness/preflight.py:1,40-123` | モジュール docstring "Shared L1/L2/L3 workflow preflight validation."、`preflight_workflow_path` が「L1, L2, and L3 validation」を適用。`kaji validate` が L1/L2/L3 を担う根拠 |
| series の workflow 解決 | `kaji_harness/series/loader.py:47-57` | `candidate = repo_root / member.workflow` の実在検査で `members.{i}.workflow not found` を出す。`.kaji/series/*.yaml` の更新が必須である根拠 |
| series fixture の非解決性 | `tests/test_series_runner.py:373-399` | fixture は `SeriesConfig.model_validate` + fake `member_launcher` でのみ使用され、`repo_root=tmp_path`。実ファイル解決を伴わない根拠 |
| doc link checker の検査範囲 | `Makefile:38-39` | `python3 scripts/check_doc_links.py docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md`。`draft/` と `.kaji-artifacts/` が対象外であり、歴史的 artifact を据え置ける根拠 |
| 実 YAML を読む segment 形テスト | `tests/test_baseline.py:30-36,230-243` | `DEV_WORKFLOWS = ("dev.yaml", "dev-thorough.yaml", "dev-thorough-fable.yaml", "dev-local.yaml")` を `REPO_ROOT / ".kaji" / "wf" / filename` で load。`rg '\.kaji/wf' tests` では検出できず、custom 2 本を pytest 対象に含んでいる |
| skill 契約テストの workflow glob | `tests/test_series_create_skill.py:10,43-53` | `pytestmark = pytest.mark.small` の下で `SKILL.parents[3] / ".kaji" / "wf"` を glob し、`dev.yaml` / `docs.yaml` 以外のすべてに「series 自動選択対象外」を要求。実 repo I/O かつ custom を対象に含む |
| `tmp_path` file-based loader test | `tests/test_workflow_parser.py:275-286` | `TestFileBasedLoading::test_load_workflow_from_file` が `@pytest.mark.small` のまま `tmp_path` へ YAML を書いて `load_workflow()` で読む。Issue の サイズ分類「`tmp_path` にファイルを作成して読む: Medium」に反する |
| 運用ガイドの flat layout 前提 | `docs/dev/workflow_guide.md:14-18,29-32,47-49,84-87,104-129` | workflow 一覧・通常運用の選択表・series 例・実行例が `.kaji/wf/<name>.yaml` 前提で、`dev-thorough.yaml` を official と同列に扱う。AGENTS.md → CLAUDE.md 経由で参照される active guide |
| 規約側の flat layout 例 | `docs/reference/python/naming-conventions.md:151-156` | 「ワークフロー・スキルファイル」の配置例が `.kaji/wf/` 直下に `dev.yaml` / `docs.yaml` を置く形。新レイアウトと矛盾するため要更新 |
| 棚卸し境界の根拠 | `docs/dev/testing-convention.md` § 既存テストの棚卸し基準 | 「棚卸しが広範囲になり、複数ファイルの削除・再分類・ワークフロー変更を伴う場合は、この Issue で抱え込まず派生 Issue を切って追跡する」 |
| git ls-files（tracked 列挙） | https://git-scm.com/docs/git-ls-files | "Show information about files in the index and the working tree"。index 登録済み（tracked）ファイルのみを列挙するため、「tracked な YAML を検証対象にする」という決定を機械的に表現できる |
