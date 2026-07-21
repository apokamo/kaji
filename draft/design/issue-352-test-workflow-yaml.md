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
   `tests/test_dev_workflow.py:37`）。`docs/dev/testing-convention.md` § 判定基準は
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
  markdown リンクを検査する（Makefile:44）。`docs/guides/python-starter*.md` の
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

`load_workflow()` 呼び出しを持つ `tests/` 18 ファイルを棚卸しした結果を次に示す。
判定軸は `docs/dev/testing-convention.md` § 判定基準（外部依存・ファイル I/O の有無）である。

#### 3-1. official 実 YAML を読む → glob を `official/**` へ、marker を `medium` へ

| ファイル | 現状 marker | 対応 |
|----------|------------|------|
| `tests/workflows/test_workflow_set_invariants.py` | `small`（class） | `medium` へ。期待集合を official の相対パス 5 件へ差し替え |
| `tests/workflows/test_review_code_routing.py` | `small` ×4 | `medium` へ。class 名 `TestReviewCodeRoutingSmall` と docstring の "Small" を是正。glob を `official/**` へ |
| `tests/workflows/test_self_retry_cycle_membership.py` | `small`（class） | `medium` へ。docstring の "Small" を是正。glob を `official/**` へ |
| `tests/workflows/test_review_poll_exec_migration.py` | `small`（static class） | `medium` へ。docstring / セクションコメントの "Small" を是正。対象を official の `dev.yaml` / `docs.yaml` に限定 |
| `tests/workflows/test_incident_workflow.py` | `small` ×6 | `medium` へ。`WF_PATH` を `official/incident.yaml` へ。skill 本文 assertion（:176）を新パス文字列へ |
| `tests/test_dev_workflow.py` | `small` ×4（`TestDevWorkflowSmall`） | `medium` へ。class 名と docstring / セクションコメントを是正。`DEV_WORKFLOW_PATH` を `official/dev.yaml` へ |

`test_incident_workflow.py` は workflow YAML に加えて `.claude/skills/**` / `.claude/agents/**` /
template を `read_text()` しており、YAML を読まないクラス（`TestIncidentCycleSlashWrapper` /
`TestIncidentForbiddenVocabulary` 等）も **ファイル I/O を伴うため一律 `medium`** とする。

#### 3-2. 既に `medium` / `large` で正しい → パス起点のみ更新

| ファイル | marker | 対応 |
|----------|--------|------|
| `tests/test_recovery_workflow_inventory.py` | `medium`（module 単位） | glob を `official/**` へ。docstring の `.kaji/wf/*.yaml` 表記も更新 |
| `tests/test_workflow_validator.py:740` | `medium` | glob を `official/**` へ |
| `tests/test_workflow_agent_optional.py:73` | `medium`（class） | `dev.yaml` パスを `official/dev.yaml` へ |
| `tests/test_verdict_e2e.py:184` | `large` | glob を `official/**` へ。5 本前提の docstring を official 5 本前提として明示 |

#### 3-3. 変更しない（ファイル I/O なし、または repo 上の YAML を読まない）

| ファイル | 理由 |
|----------|------|
| `tests/test_workflow_requires_provider.py` | `load_workflow_from_str()` のみ。純粋 parser → `small` のまま |
| `tests/test_workflow_parser.py` | `tmp_path` へ書いて読む。既存分類を変更しない |
| `tests/test_preflight.py` / `test_runner.py` / `test_workdir_config.py` / `test_timeout_config.py` / `test_baseline.py` | `tmp_path` 上の合成 repo。repo 上の official YAML を読まない |
| `tests/test_skill_harness_adaptation.py` | `tests/fixtures/test_workflow.yaml` を読む。`.kaji/wf/` 非依存 |
| `tests/test_exec_step_parser.py` | 合成 YAML / object のみ |
| `tests/test_series_*.py` / `test_recovery_plan.py` / `test_recovery_models.py` / `test_recovery_report.py` | `.kaji/wf/...` を **文字列として**扱うだけ。fixture 文字列は現実整合のため新パスへ更新するが marker は変えない |

#### 3-4. official inventory の列挙方式

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

#### 3-5. custom を pytest 対象外にする方法

custom を「除外リストで引く」のではなく、**glob の起点を `official/` に限定する**ことで
構造的に対象外にする。除外リストは custom カテゴリ追加時に保守が発生し、書き忘れると
custom が公式回帰へ混入するため採らない。

これに伴い `test_review_poll_exec_migration.py` の static 検証対象から `dev-thorough.yaml` が外れる
（custom へ移動するため）。`review-poll` の exec argv 不変条件は custom variant では pytest で
保証されず、`make validate-workflows` の L1/L2/L3（schema / 参照整合 / skill 解決）で検出できる範囲に留まる。
これは Issue で人間が決定した「公式品質保証は official のみ」に従う**意図的なトレードオフ**であり、
設計書・CHANGELOG の双方に記録する。

### Phase 4: 参照更新（active な実行参照のみ）

| 区分 | 対象 |
|------|------|
| 実行設定 | `.kaji/series/epic-324-workflow-improvements.yaml` / `issues-338-339.yaml` / `issues-346-349.yaml` |
| skill（実行コマンド） | `.claude/skills/review-cycle/SKILL.md`（`kaji run .kaji/wf/dev.yaml`）、`.claude/skills/incident-cycle/SKILL.md`（`kaji run .kaji/wf/incident.yaml` / `kaji validate`。description 行含む） |
| skill（例示・参照） | `.claude/skills/kaji-run-verify/SKILL.md`、`.claude/skills/issue-design/SKILL.md`（`.kaji/wf/dev.yaml:134` / `:82` の行参照。行番号は移動で不変）、`.claude/skills/grill-me/SKILL.md`、`.claude/skills/series-create/SKILL.md`（`.kaji/wf/*.yaml` の再帰探索表記へ） |
| skill（変更不要） | `.claude/skills/i-doc-update/SKILL.md`（`git add .kaji/wf/` は dir 指定のため有効） |
| docs（パス更新） | `README.md` / `README.ja.md` / `llms.txt` / `docs/ARCHITECTURE.md` / `docs/dev/development_workflow.md` / `docs/dev/docs_maintenance_workflow.md` / `docs/cli-guides/failure-recovery{,.ja}.md` / `docs/cli-guides/interactive-terminal-runner{,.ja}.md` / `docs/cli-guides/local-mode.ja.md` / `docs/operations/local-mode-runbook{,.ja}.md` |
| docs（正本の新設） | `docs/dev/workflow-authoring.md` § ファイル配置 |
| docs（リンクのみ） | `docs/guides/python-starter{,.ja}.md` |
| 変更しない | `CHANGELOG.md` の過去エントリ、`draft/design/` の過去設計書、`.kaji-artifacts/` |

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
  custom の振る舞いは kaji の pytest 対象外である旨も明記する

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
| テストサイズの正本 | 実行速度や既存 marker ではなく規約のファイル I/O 基準で判定 | `docs/dev/testing-convention.md` § 判定基準（人間決定・既存正本） | 18 ファイルを 3-1 / 3-2 / 3-3 に分類し、是正対象を 6 ファイルに確定 |
| 期待 inventory の表現 | stem 集合ではなく `official/` 相対パス集合で完全一致比較 | **AI の仮定**。欠落 / 追加 / 階層 drift / custom 混入を同一 assertion で検出するため。後段の検査先: design review / code review | `rglob` 実測との `==` 比較を規定 |
| `make validate-workflows` の実装 | `git ls-files` による tracked 列挙 + 非空ガード | **AI の仮定**。「tracked を対象」という人間決定を機械的に表現でき、`kaji validate` の引数 0 件が `EXIT_OK` を返す（`validate.py:51-96`）silent pass を塞ぐため。後段の検査先: code review / `make check` | Makefile レシピの形を規定 |
| `dev-thorough` の review-poll static 検証 | custom へ移動するため pytest 対象から外す | **AI の仮定**（人間決定「pytest は official のみ」の帰結）。後段の検査先: design review / code review | カバレッジ減をトレードオフとして設計書・CHANGELOG に記録 |
| test fixture 文字列 | `tests/fixtures/series/*.yaml` の workflow 文字列を新パスへ更新 | **AI の仮定**。挙動非依存（`test_series_runner.py:373-399` は fake launcher）だが現実整合を優先。後段の検査先: code review | marker は変更しないことを明記 |
| 共通 glob ヘルパ | 導入しない。各テストの glob 起点更新に留める | **AI の仮定**。現状 5 箇所の重複を増やさず、配置変更に対する diff を最小化するため。後段の検査先: design review / code review | 共通化は本 Issue の scope 外とする |
| 歴史的 artifact | CHANGELOG 過去エントリ / 過去設計書 / artifacts は置換しない | Issue「AI の仮定と後段の検査先」表（Issue 側で仮定として記録済み） | `make verify-docs` の検査範囲外であることを確認し、対象外として固定 |

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

marker 是正では、**`@pytest.mark.small` → `@pytest.mark.medium` の置換だけでなく、
class 名・docstring・セクションコメントに残る "Small" 表記も同時に是正する**
（`TestReviewCodeRoutingSmall` / `TestDevWorkflowSmall` / `# static regression（Small）` 等）。
marker と自然言語が食い違ったままだとレビュー時の分類根拠が再び崩れるため。

### Large テスト

**新規追加しない。** 既存の `test_verdict_e2e.py`（`large`）の glob 起点を `official/**` へ更新するに留める。

理由: 実 API 疎通や E2E データフローの契約自体は変わらず、変わるのは YAML の在処のみである。
E2E で新たに保護すべき振る舞いが増えないため、`docs/dev/testing-convention.md` の 4 条件を満たす。

### 変更固有の一時検証（恒久化しない）

| 検証 | 目的 |
|------|------|
| `git diff --cached -M --stat` で 10 件が `R100` | YAML 内容を 1 byte も変えていないことの証跡 |
| `git ls-files .kaji/wf` が新パス 10 件のみを返す | 旧パスの残骸と untracked 取りこぼしの検出 |
| `rg -n '\.kaji/wf/[a-z0-9-]+\.yaml' -g '!CHANGELOG.md' -g '!draft/**' -g '!.kaji-artifacts/**' .` が 0 件 | active な旧パス参照の残存検出 |
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
| docs/dev/testing-convention.md | なし | 規約自体は変更しない（本 Issue は規約への追随側） |
| docs/reference/ | なし | 設定キー・API 契約の変更なし |
| docs/cli-guides/ | あり | failure-recovery{,.ja} / interactive-terminal-runner{,.ja} / local-mode.ja の実行例パス更新 |
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
| doc link checker の検査範囲 | `Makefile:44` | `python3 scripts/check_doc_links.py docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md`。`draft/` と `.kaji-artifacts/` が対象外であり、歴史的 artifact を据え置ける根拠 |
| git ls-files（tracked 列挙） | https://git-scm.com/docs/git-ls-files | "Show information about files in the index and the working tree"。index 登録済み（tracked）ファイルのみを列挙するため、「tracked な YAML を検証対象にする」という決定を機械的に表現できる |
