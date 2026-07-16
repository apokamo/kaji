# [設計] series validate-series / dry-run / run の全 member workflow 完全 preflight 統合

Issue: #338

## 概要

`kaji validate-series` / `kaji run-series --dry-run` / 通常の `kaji run-series` が member workflow を L1（YAML parse・型検証）までしか検証せず、L2（参照整合）/ L3（skill metadata）の invalid を後続 member の subprocess 起動時まで検出できない bug を修正する。既存の L1 / L2 / L3 検証を単一の共通 preflight に統合し、`kaji validate` / `kaji run` / `kaji recover` / series 全入口から同じ検証を使う。

## 背景・目的

### Observed Behavior（OB）

一次情報: Issue #338 本文 `## 目的` § OB の実行ログ（2026-07-14〜15 の series `epic-324-workflow-improvements` 実障害）。

1. `kaji validate-series` と `kaji run-series --dry-run` が双方 exit 0（fingerprint `sha256:44ce47d5...`）
2. dry-run 後に series の workflow 選択が variant（`dev-thorough-fable.yaml` / `docs-codex.yaml`）へ変更され、本実行は別 fingerprint（`sha256:719e7e...`）で開始された
3. member #325 が 76 分（4,564,578ms）かけて完走した後、member #326 の `kaji run` Step 0 で初めて L2 validation error が検出され停止した:

```text
Error: 1 validation error(s): Step 'doc-verify' resumes 'doc-review' but agents differ (codex != claude)
series epic-324-workflow-improvements: member 326 exited code=2
Series stopped: member 326 failed: exit:2
```

このエラー自体は `validate_workflow()`（`kaji_harness/workflow.py:357` の L2 ルール「resume 先の agent 一致」）の正当な検出である。問題は検出タイミングが後続 member 起動時まで遅れたこと。

### Expected Behavior（EB）

Issue 本文 `## 目的` § EB（人間決定）:

- `kaji validate-series` / `kaji run-series --dry-run` / 通常の `kaji run-series` は、現在の plan が参照する全 member workflow に L1 / L2 / L3 を適用する
- 通常実行は過去の dry-run 成功を信用せず、実行開始時に現在の plan を再検証する
- 1 件でも invalid なら、member subprocess / series state / lock を作成せず validation error で停止する
- dry-run は member workflow を読み取り・検証するが、provider API / Issue / artifact / state / lock / member 実行に副作用を与えない
- `kaji run` / `kaji validate` / recovery 経路も同じ preflight 実装を使い、入口ごとのルール drift を起こさない

## 再現手順

Issue 本文 `## 再現手順` を設計上の受け入れシナリオとして固定する:

1. L1 は通るが L2（例: `resume` 先と agent 不一致）または L3（例: skill 不在）に違反する workflow を作る
2. その workflow を 2 番目以降の member に指定した series YAML を作る
3. 修正前: `kaji validate-series <series>` と `kaji run-series <series> --dry-run` が exit 0 になる
4. 修正前: 通常の `kaji run-series <series>` で 1 番目の member が起動・完了し、2 番目の `kaji run` Step 0 で初めて validation error になる
5. 修正後: 3〜4 のどの入口でも同じ invalid を事前検出し、1 member も起動しない
6. stale plan 回帰: valid な plan で dry-run 後、参照 workflow を L2 / L3 invalid に変更して通常実行し、開始時再検証で停止する

## 根本原因（Root Cause）

### なぜ間違っているか

workflow 検証は 3 層に分かれているが、単一の関数として公開されておらず、入口ごとに呼び出す層が異なる。

| 層 | 内容 | 現行実装 |
|---|---|---|
| L1 | YAML parse・型・排他・effort×agent 許容値 | `load_workflow()`（`workflow.py:16`） |
| L2 | resume / on 遷移先 / cycle 参照整合、verdict 妥当性 | `validate_workflow()`（`workflow.py:357`） |
| L3 | skill 存在・frontmatter・agent 省略×exec_script 整合 | `runner._collect_skill_metadata()`（`runner.py:800`）と `commands/validate.cmd_validate()`（`validate.py:54`）の**重複実装** |

- `load_series()`（`series/loader.py:25`）は member workflow に対し `load_workflow(resolved)` しか呼ばない（L1 のみ）。`validate-series` / `run-series` dry-run / normal はすべて `load_series()` 経由のため、3 入口とも L2 / L3 を素通しする
- `cmd_recover()`（`commands/recover.py:62`）も `load_workflow()` のみで、L2 / L3 を検証しない
- L3 は `runner.py` と `commands/validate.py` の別実装で、片方だけにルールが追加されると入口間で drift する

### いつから壊れているか

- `load_series()` は series runner 導入 commit `49c9a28`（feat: add sequential series runner for #313）で L1 のみの検証として実装され、導入時から L2 / L3 を検証していない
- L3 の重複は `cmd_validate` の L3 追検証（`validate.py:70-87`）が runner の `_collect_skill_metadata` と独立に実装された時点から存在する

### 同根の他の壊れ箇所

Issue 本文の入口カバレッジ表（人間決定・一次情報）のとおり、`kaji recover` の初期 load も L1 のみである。本 Issue の影響範囲に含まれており、同じ preflight へ統合する。fingerprint binding（dry-run 承認の永続化）は現行契約に存在せず、Issue 重要判断で本 Issue のスコープ外と決定済み。

## インターフェース

### 公開 CLI（変更なし・維持が契約）

Issue 本文 `## 重要判断` および Issue コメント（2026-07-16T17:55:12Z「人間決定: 公開 CLI error 契約」）で、公開 CLI 境界は**既存の command 別契約を維持**すると決定済み:

| 入口 | invalid 時 exit code | rendering |
|---|---|---|
| `kaji validate` | 1（`EXIT_VALIDATION_ERROR`） | stdout `✓ path` / stderr `✗ path` + error list |
| `kaji validate-series` | 1（`EXIT_VALIDATION_ERROR`） | stdout `✓ path (N members)` / stderr `✗ path` + error list |
| `kaji run-series --dry-run` / 通常実行 | 2（`EXIT_INVALID_INPUT`、`SeriesValidationError` 経由） | stderr `Error: ...` |
| `kaji run` | 2（`EXIT_DEFINITION_ERROR`、`WorkflowValidationError` 経由） | stderr `Error: ...` |
| `kaji recover` | 2（`EXIT_DEFINITION_ERROR`） | stderr `Error: ...` |

exit code 定数の正本: `kaji_harness/commands/exit_codes.py`（`EXIT_VALIDATION_ERROR = 1` / `EXIT_DEFINITION_ERROR = EXIT_INVALID_INPUT = 2`）。

### 入力（新規内部 API）

新モジュール `kaji_harness/preflight.py` に共通 preflight を置く（配置は AI の仮定、「方針」節参照）:

```python
@dataclass(frozen=True)
class WorkflowPreflightResult:
    """単一 workflow の L1/L2/L3 preflight 結果（構造化 error list）。"""
    workflow: Workflow | None            # L1 通過時のみ非 None
    skill_metadata: dict[str, SkillMetadata | None]  # L3 で解決できた step 分
    errors: list[str]                    # L1 / L2 / L3 の集約
    warnings: list[str]                  # exec_script × agent/model/effort 無視警告

def preflight_workflow(
    workflow: Workflow, *, project_root: Path, skill_dir: str
) -> WorkflowPreflightResult:
    """ロード済み Workflow に L2 + L3 を適用する（runner 用）。"""

def preflight_workflow_path(
    path: Path, *, project_root: Path, skill_dir: str
) -> WorkflowPreflightResult:
    """path から L1 → L2 → L3 を適用する（validate / series / recover 用）。"""
```

- 例外を投げず、構造化 error list を返す（Issue 完了条件「共通 preflight は構造化 error list を返し、公開 CLI 境界では既存契約を維持する」）。各入口が既存の例外型 / exit code / rendering へ mapping する
- L1 失敗（YAML parse・型エラー）時は L2 / L3 を実行せず L1 エラーのみ返す（`workflow=None`）
- L2 と L3 は独立に評価し、両方のエラーを集約する（series の「全件集約」要件と整合）
- L3 は step ごとに `SkillNotFound` / `SecurityError` / `SkillFrontmatterError` を捕捉して `errors` に文字列化し、次の step へ継続する（現行 `cmd_validate` は最初の例外で当該ファイルの検証を打ち切るため、集約性が向上する。exit code / ✗ rendering は不変）

### 出力（各入口の適用後の挙動）

| 入口 | 変更内容 |
|---|---|
| `load_series()` | 各 member に `preflight_workflow_path()` を適用。エラーは `members.{index}.workflow is invalid: ...` 形式で **member index / workflow path 付きで全件集約**し、最後に `SeriesValidationError(errors)` を送出。ファイル不在は現行 `not found`、読取 OSError は `could not be loaded`、preflight エラーは `is invalid` と**区別**する |
| `kaji validate-series` | `load_series()` 経由で自動的に L1/L2/L3 化。exit 1・rendering 不変 |
| `run-series --dry-run` | `load_series()` 経由で完全検証後に plan / fingerprint 表示。読み取りのみで副作用なし（現行同様、provider API / Issue / artifact / state / lock / member 実行に触れない） |
| 通常の `run-series` | `load_series()` が `SeriesRunner` 生成**前**に走るため、invalid なら member subprocess / series state / lock を一切作成せず exit 2。過去の dry-run 有無・結果には依存しない（開始時再検証が安全性の正本） |
| `kaji run`（runner） | `runner.run()` 冒頭の `_collect_skill_metadata()` + `validate_workflow()` を `preflight_workflow(self.workflow, ...)` に置換。`errors` 非空なら `WorkflowValidationError(errors)` を送出（`cmd_run` の既存 mapping で exit 2）。`warnings` は現行どおり stderr へ。`skill_metadata` は実行に引き続き使用 |
| `kaji validate` | `cmd_validate` の L2/L3 手組みを `preflight_workflow_path()` に置換。config 解決（`_resolve_project_root_for_validate`）は現行維持。exit 1・✓/✗ rendering 不変 |
| `kaji recover` | `load_workflow()` 後に `preflight_workflow()` を追加適用。エラー時は既存 `WorkflowValidationError` 分岐と同じ `EXIT_DEFINITION_ERROR` |

### 使用例

```python
# load_series() 内（疑似コード）
result = preflight_workflow_path(resolved, project_root=repo_root, skill_dir=config.paths.skill_dir)
if result.errors:
    errors.extend(
        f"members.{index}.workflow is invalid ({member.workflow}): {e}" for e in result.errors
    )
    continue
```

## 制約・前提条件

- 公開 CLI の exit code / stdout / stderr rendering は変更しない（人間決定。上表が契約）
- 新しい validation ルールの追加・厳密化は #339 のスコープ（本 Issue は既存ルールの入口統合に閉じる）
- dry-run 結果の永続化・fingerprint binding は追加しない（Issue 重要判断）
- series 実行中の workflow YAML 編集に対する TOCTOU 対策・snapshot はスコープ外
- `load_series()` の series 固有検証（repo root 内解決・`requires_provider` が `github`/`any`）は series 層に残す（`kaji validate` は local 用 workflow も通すため preflight に含めない）
- L3 には `project_root` と `skill_dir`（`KajiConfig.paths.skill_dir`、repo root 相対の str）が必要。preflight は `KajiConfig` 全体でなく必要な 2 値のみ受け取り、config 発見の責務は各入口に残す
- `kaji_harness/preflight.py` は `workflow.py` / `skill.py` にのみ依存し、`runner.py` / `commands/` からの逆依存を持たない（循環 import 防止）

## 方針

最小侵襲の 3 段構成。リファクタは preflight 統合に必要な範囲に限定する。

1. **共通 preflight の新設**（`kaji_harness/preflight.py`）
   - `preflight_workflow()`: L2 は `validate_workflow()` を呼び `WorkflowValidationError.errors` を回収。L3 は現行 `_collect_skill_metadata()` / `cmd_validate` の検査項目（skill 存在・frontmatter・agent 省略×exec_script・exec_script×agent/model/effort 警告）を集約実装
   - `preflight_workflow_path()`: `load_workflow()`（L1）→ `preflight_workflow()`。L1 の `WorkflowValidationError` は errors へ回収
   - エラー文言は既存実装の文言を維持する（例: `Step 'X' resumes 'Y' but agents differ`、`Step 'X' omits 'agent' but skill ...`）。既存テスト・運用ログとの互換のため

2. **各入口の置換**（上記「出力」表のとおり）
   - `runner._collect_skill_metadata()` と `cmd_validate` の L3 手組みを削除し preflight 呼び出しへ一本化（重複解消）
   - `load_series()`: member ループ内を preflight 化し、`is invalid` / `could not be loaded` / `not found` を区別して全件集約
   - `cmd_recover`: `load_workflow()` 直後に preflight を追加

3. **品質ゲートへの組み込み**
   - Makefile に `validate-workflows` ターゲット（`kaji validate .kaji/wf/*.yaml`）を追加し、`check` の依存に加える。CI は `make check` 経由で自動包含
   - `.kaji/wf/` 配下の既存 workflow が現時点の preflight を全通過することを実装時に確認する（通らないものがあれば workflow YAML 側の修正も本 Issue の bug 修正範囲）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| dry-run の責務 | member workflow を読み取り L1/L2/L3 を完全検証。副作用なし | Issue 本文 `## 重要判断`（人間決定） | `load_series()` 内で完結させ、`SeriesRunner` 生成前に停止する構成 |
| 本実行の安全性の正本 | 過去 dry-run でなく開始時再検証 | Issue 本文 `## 重要判断` / `## 根本原因` A（人間決定） | `cmd_run_series` が normal / dry-run とも同一 `load_series()` を通る現行フローを利用し、追加の状態参照を導入しない |
| fingerprint binding | 本 Issue では追加しない | Issue 本文 `## 重要判断`（人間決定） | 設計対象外として除外 |
| 公開 CLI error 契約 | command 別の既存契約を維持。preflight は構造化 error list を返し各入口が mapping | Issue コメント 2026-07-16T17:55:12Z + Issue 本文 `## 重要判断`（人間決定） | `WorkflowPreflightResult` dataclass と入口別 mapping 表として具体化 |
| validation ルール追加 | #339 に分離 | Issue 本文 `## 重要判断`（人間決定） | エラー文言も既存実装を維持し、新規ルールを混入させない |
| preflight の配置 | 新モジュール `kaji_harness/preflight.py` | AI の仮定。根拠: `workflow.py` に置くと `skill.py` 依存が増え leaf 性が崩れる。`runner.py` / `commands/` に置くと相互依存になる。review-design / review-code で検査 | 依存方向（workflow / skill のみ）を制約として明文化 |
| 返却形 | frozen dataclass `WorkflowPreflightResult`（workflow / skill_metadata / errors / warnings） | AI の仮定。根拠: runner が skill_metadata を実行に再利用するため、検証と metadata 取得を 1 パスに統合すると二重ロードを避けられる。review-code で検査 | フィールド構成と L1 失敗時の `workflow=None` 契約を定義 |
| L2/L3 の集約方式 | L1 失敗時は L1 のみ。L2 と L3 は独立評価して両方集約 | AI の仮定。根拠: series の「全件 error 集約」完了条件との整合と、L3 が L2 結果に依存しない事実。review-code で検査 | 例外→errors 回収の変換規則を定義 |
| Makefile ターゲット名 | `validate-workflows` を `check` 依存に追加 | AI の仮定。根拠: 完了条件「make check と CI で .kaji/wf/*.yaml を共通 preflight により検証」。名称・組み込み位置は two-way door。review-code で検査 | `kaji validate` CLI 経由とし、preflight 実装の直接 import を避ける |

## テスト戦略

### 変更タイプ

実行時コード変更（`kaji_harness/preflight.py` 新設 + 5 入口の検証経路変更 + Makefile）。

### 再現テスト（bug 固有・必須）

修正前に Red となる恒久回帰テストを定義する。なお Issue 本文 OB には実障害ログ（member #326 の validation error・exit 2・実行日時・fingerprint）が含まれ、`_shared/design-by-type/bug.md` の escape clause（実ログによる実装前 Red 代替）の要件を満たすが、以下の回帰テストは修正前コードに対して実際に FAIL することを実装フェーズで確認する（L2 invalid member で validate-series が exit 0 になる現行挙動 → 修正後 exit 1 を assert）。

### Small テスト

- `preflight_workflow_path()`: L1 invalid（YAML parse error）→ L1 エラーのみ・`workflow=None` / L2 invalid（実障害と同一の resume agent 不一致）→ 当該エラー文言 / L3 invalid（skill 不在・agent 省略×exec_script なし）→ 当該エラー文言 / L2+L3 同時 invalid → 両方集約 / valid → errors 空・skill_metadata 充足
- warnings: exec_script skill に agent/model/effort 指定 → warnings に回収され errors に混入しない
- `load_series()` のエラー分類: ファイル不在 → `not found` / 読取失敗 → `could not be loaded` / preflight エラー → `is invalid`（member index / path 付き）— 既存 `test_series_io.py` に追加
- 既存 `test_workflow_validator.py` / `test_cli_validate.py` が L2 / `kaji validate` の挙動不変を回帰保護

### Medium テスト（tmp repo fixture、既存 `test_series_cli.py` の流儀）

- **L2 invalid / L3 invalid の双方**について、`validate-series`（exit 1・stderr に member index + 理由）/ `run-series --dry-run`（exit 2・`Error:` stderr）/ 通常 `run-series`（exit 2）が**同じ理由で停止**する回帰テスト（完了条件対応）
- invalid な 2 番目の member を含む series で、**1 番目を含め member subprocess が 1 件も起動せず**、series state ファイル / lock が作成されないテスト（完了条件対応）
- **stale-plan 回帰**: valid plan で dry-run 成功 → 参照 workflow を L2 invalid に書き換え → 通常実行が開始時再検証で exit 2 停止（完了条件対応）
- `kaji recover`: L2 invalid workflow で `EXIT_DEFINITION_ERROR`（exit 2）
- `kaji run`: 既存 `test_workflow_execution.py` 等で L2/L3 検出の回帰を確認（置換後も挙動不変）
- dry-run の副作用なし: dry-run 成功後に artifacts / state / lock が存在しないことの確認

### Large テスト

- 追加不要。理由: 本修正は外部 API 疎通を持たず、対象経路はすべて subprocess 起動**前**に停止する（Medium で観測可能）。member 実走を伴う既存経路は `test_series_cli_large_local.py` が回帰保護しており、本修正で valid series の実行動線は変わらない（`docs/dev/testing-convention.md` の判定基準「外部 API / 実サービス疎通あり → Large」に該当しない）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（既存検証の配置統合。ADR 010 は series 入力検証の Pydantic 採用で、本変更は矛盾しない） |
| docs/ARCHITECTURE.md | あり | series 検証フロー / validation 層の記述があれば preflight 統合を反映（`validate-series` 言及あり） |
| docs/dev/workflow_guide.md | あり | dry-run / validation 契約の更新（完了条件で明示） |
| docs/dev/workflow-authoring.md | あり | `kaji validate` の検証範囲（L1/L2/L3 統合）記述の更新（完了条件で明示） |
| docs/cli-guides/github-mode.md / .ja.md | あり | `validate-series` / `run-series --dry-run` の検証範囲・開始時再検証の説明更新 |
| docs/reference/ | なし | Python 規約・API 仕様の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| Makefile（`make help` 文言） | あり | `validate-workflows` ターゲット追加 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #338 本文 | GitHub Issue #338 | OB 実行ログ（`Step 'doc-verify' resumes 'doc-review' but agents differ (codex != claude)` / exit 2）、入口×検証層カバレッジ表、`## 重要判断`（dry-run 責務・開始時再検証・CLI 契約維持・#339 分離） |
| Issue #338 コメント 2026-07-16T17:55:12Z | GitHub Issue #338 コメント | 人間決定「公開 CLI 境界では既存の command 別契約を維持」「validation failure の exit code / 表示形式は全入口で統一しない」 |
| `kaji_harness/series/loader.py:60` | repo 内 | `workflow = load_workflow(resolved)` — member 検証が L1 のみである根拠 |
| `kaji_harness/workflow.py:357-568` | repo 内 | `validate_workflow()` の L2 ルール集合（resume agent 一致 `workflow.py:472-476` が実障害の検出ルール） |
| `kaji_harness/runner.py:800-828, 933-934` | repo 内 | runner 側 L3 実装 `_collect_skill_metadata()` と `validate_workflow()` 呼び出し（`kaji run` Step 0 の現行完全検証） |
| `kaji_harness/commands/validate.py:70-87` | repo 内 | `cmd_validate` 側 L3 重複実装（agent 省略×exec_script 検査を含む） |
| `kaji_harness/commands/series.py:38-97` | repo 内 | `validate-series` exit 1 / `run-series` の `SeriesValidationError` → exit 2 mapping と rendering（維持対象の契約） |
| `kaji_harness/commands/recover.py:62-65` | repo 内 | recover 初期 load が `load_workflow()` のみである根拠 |
| `kaji_harness/commands/exit_codes.py` | repo 内 | `EXIT_VALIDATION_ERROR = 1` / `EXIT_DEFINITION_ERROR = EXIT_INVALID_INPUT = 2` |
| `git log --follow kaji_harness/series/loader.py` | repo 内 | 導入 commit `49c9a28`（#313）から L1 のみ — 「いつから壊れているか」の根拠 |
| docs/dev/testing-convention.md | repo 内 | テストサイズ判定基準（subprocess 起動前停止は Medium で観測、外部疎通なしのため Large 不要） |
| 後続 Issue #339 | GitHub Issue #339 | validation ルール厳密化の分離先（スコープ境界） |
