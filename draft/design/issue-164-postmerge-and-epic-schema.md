# [設計] post-merge 品質担保スキル群と EPIC 設定スキーマの追加

Issue: #164

## 概要

本 Issue は 2 つの独立した feat を同 Issue で扱う:

1. **post-merge 品質担保ワークフロー**: PR マージ後の `wait-merge` / `verify-main-green` / `post-merge-review` の 3 スキルと、それを直列接続した新規ワークフロー `feature-with-postmerge.yaml` を追加する。
2. **EPIC 設定スキーマ**: 複数 Issue を束ねる `EpicConfig` Pydantic モデルと、`kaji validate-epic <epic.yaml>` CLI を追加する（スキーマと検証のみ。ランタイムは別 Issue）。

両機能とも実装は独立。ファイル群もディレクトリ群もほぼ独立しているが、設計レビューを同時に行う目的で同 Issue にバンドルする。

## 背景・目的

### post-merge 品質担保ワークフロー

**ユーザーストーリー**:
- 開発者として、PR レビュー承認以降「マージ → main の CI green 確認 → 反映済みコミットの差分レビュー → Issue クローズ」までを自動化し、人手介入を承認操作のみに限定したい。

現状の `feature-development.yaml` は `i-pr` で終端しており、マージ後の確認はワークフロー外で手動実施するか、`issue-close` を直接実行している。これにより以下の運用上の損失が発生している:

- main ブランチの CI が壊れた状態に気付くのが遅い
- マージ済みコミットのレビュー（特に rebase / squash 等で差分が変質した場合）が省かれがち

**代替案**: 既存 `feature-development.yaml` に直接ステップを追記する案もあるが、新規ユーザー（特にマージ権限を持たない契約状況）に対する影響が出るため、別ワークフローとして分離して提供する。スコープ膨張時の運用とも整合する。

### EPIC 設定スキーマ

**ユーザーストーリー**:
- リリース計画担当として、関連する複数の Issue（依存関係・並列実行可能性・マージ順序を含む）を 1 つの YAML で宣言的に管理したい。実行ランタイムが完成する前段で、まず YAML スキーマとして整合性検証だけでも CI に組み込めるようにしたい。

**代替案**: 既存の `Workflow` モデルを拡張して複数 Issue を表現する案もあるが、`Workflow` は単一 Issue の step 系列を表すモデルであり、Issue 間の DAG / 並列グループ / マージ順序を表現するには別モデルが妥当。

## インターフェース

### 1. post-merge 品質担保ワークフロー

#### 1.1 スキル `wait-merge`

- **入力（コンテキスト変数）**:
  - `issue_number: int`
  - `step_id: str`
- **入力（設定オプション）**:
  - polling interval は SKILL.md 内で定数として定義（初期値: 60 秒）
  - 最大ポーリング回数（タイムアウト相当）は SKILL.md 内で定数として定義（初期値: 60 回 = 60 分）
  - これらは将来的に workflow YAML 経由で上書き可能にする余地を残すが、本 Issue では SKILL.md 内固定とする
- **出力（verdict）**:
  - `PASS`: PR が merged 状態に到達
  - `RETRY`: 未マージのまま、ポーリング上限に到達（→ ワークフロー側で再実行 or 終了を判断）
  - `ABORT`: PR が closed (not merged)、PR が見つからない、`gh` API エラー
- **副作用**:
  - `gh pr view <branch> --json state,mergedAt,mergeCommit` をポーリング
  - Issue タイムラインへの完了コメント投稿（成功時のみ）

#### 1.2 スキル `verify-main-green`

- **入力**: 上記と同じ + Step 内で `gh pr view` から取得した `mergeCommit.oid`
- **出力（verdict）**:
  - `PASS`: マージコミットを含む main の最新 CI run が `success`
  - `RETRY`: CI run が `in_progress` / `queued`（→ ワークフロー側で本ステップを再走させる）
  - `ABORT`: CI run が `failure` / `cancelled` / `timed_out`、もしくは関連 run が見つからない
- **副作用**:
  - `gh run list --branch main --commit <oid> --json status,conclusion,databaseId,name` で当該コミットに紐付く CI run を特定
  - 結果を Issue にコメント投稿（PASS / ABORT 両方）

#### 1.3 スキル `post-merge-review`

- **入力**: 上記 + マージコミット範囲 `<base>..<merge_commit>`
- **出力（verdict）**:
  - `PASS`: codex レビューが Approve 相当
  - `RETRY`: 修正が必要な指摘あり（Issue にコメント投稿し、後続を PAUSE）
  - `ABORT`: codex 実行失敗、レビュー対象 commit range が空
- **副作用**:
  - `git log <base>..<merge_commit>` で対象コミット範囲を確定
  - 必要に応じて `git diff <base>..<merge_commit>` を生成し codex に渡す
  - レビュー結果を Issue にコメント投稿

#### 1.4 スキル `post-merge-close`

既存 `issue-close` の「Step 3: PR マージ」は、`wait-merge` 成功時点で PR が既に merged 状態のため前提と矛盾する（review 指摘 1）。
したがって post-merge ワークフロー専用に**マージを行わない close スキル**を新規追加する。

- **入力（コンテキスト変数）**:
  - `issue_number: int`
  - `step_id: str`
- **出力（verdict）**:
  - `PASS`: クリーンアップ完了
  - `ABORT`: `gh issue close` 失敗、または worktree / branch 削除で復旧不能なエラー
- **手順（既存 `issue-close` の Step 4 以降のみを実施）**:
  1. PR が merged 状態であることを検証（`gh pr view --json state` が `MERGED`）。merged でなければ ABORT
  2. worktree 削除（既存 `issue-close` Step 4 と同一）
  3. ローカル / リモートブランチ削除（既存 Step 4.5 と同一）
  4. `git pull origin main`（既存 Step 5 と同一）
  5. `gh issue close --reason completed`（既存 Step 5.5 と同一）
- **既存 `issue-close` には手を入れない**（feature-development.yaml ユーザーへの後方互換を担保）

> **代替案検討**: 既存 `issue-close` を「PR が既に merged ならマージ手順をスキップ」と冪等化する案も検討したが、既存スキルの挙動を変えると `feature-development.yaml` 側の意味が変わるため不採用。新規スキルとして責務を分離する。

#### 1.5 ワークフロー `workflows/feature-with-postmerge.yaml`

`feature-development.yaml` の steps を全て複製し、末尾の `pr` ステップの遷移先を `wait-merge` に変更し、以下を追加:

```
pr → wait-merge → verify-main-green → post-merge-review → post-merge-close → end
```

- **PASS 経路**: `wait-merge:PASS → verify-main-green:PASS → post-merge-review:PASS → post-merge-close:PASS → end`
- **RETRY 経路**:
  - `wait-merge:RETRY → end`（マージ待ちタイムアウトは workflow を停止し、再実行は手動）
  - `verify-main-green:RETRY → verify-main-green`（CI が in_progress の場合は同一ステップを再走）
  - `post-merge-review:RETRY → end`（PAUSE 相当。RETRY 後の自動修復ループは本 Issue の non-goal）
- **ABORT 経路**: 全て `end`
- **既存 `feature-development.yaml` は変更しない**
- **`issue-close` は呼び出さない**（merge 前提が衝突するため `post-merge-close` で代替）

### 2. EPIC 設定スキーマ

#### 2.1 `EpicConfig` モデル（Pydantic）

`kaji_harness/epic.py` 配下に新規モジュールを追加（モデル + バリデータ + ローダ）。

```python
class EpicMember(BaseModel):
    issue: int                              # GitHub Issue 番号
    depends_on: list[int] = []              # 同 EPIC 内の他 Issue 番号
    parallel_group: str | None = None       # 並列グループ名（任意）
    merge_order: int | None = None          # マージキュー順序（明示時のみ採用）

class EpicConfig(BaseModel):
    name: str
    description: str = ""
    members: list[EpicMember]
```

- **モデルレベル検証**:
  - `members` 非空
  - `issue` の重複禁止
  - `depends_on` の参照先が `members` に含まれる
  - DAG 循環検出（`networkx` ではなく自前の DFS / topological sort で実装。標準ライブラリのみで足りる）
  - `merge_order` を持つ Issue 群の値は重複禁止（ソート可能性を保証）
  - **DAG 整合制約（review 指摘 2）**:
    - **`parallel_group` 整合**: 直接または推移的な `depends_on` 関係がある 2 Issue が同一 `parallel_group` に属するのを禁止する。理由: 並列グループは「同時実行可能」を意味するため、依存関係がある Issue を同居させると意味が破綻する
    - **`merge_order` 整合**: ある Issue A が B に `depends_on` する場合、両者が `merge_order` を明示しているなら `merge_order(A) < merge_order(B)` を強制する。明示順序が DAG を破る設定はバリデーションエラー
    - これらのチェックは循環検出後（DAG が確定した後）に実施
- **派生プロパティ（メソッド）**:
  - `topological_order() -> list[list[int]]`: 並列グループを段階別に推定（`parallel_group` の明示指定があればそちらを優先、なければ DAG の階層から推定）。**前提**: 上記 `parallel_group` 整合制約をパスしているため、明示優先しても DAG を破らない
  - `sorted_merge_order() -> list[int]`: `merge_order` 明示の Issue は値順、未指定は topological order の末尾に追加。**前提**: 上記 `merge_order` 整合制約をパスしているため、明示順は依存関係を満たす

#### 2.2 CLI: `kaji validate-epic`

- **入力**: `<epic.yaml>` ファイルパス（複数可）
- **出力**:
  - 成功: `✓ <path>` を stdout に出力、exit 0
  - 失敗: `✗ <path>` + 個別エラーを stderr に出力、exit 1
- **既存 `kaji validate <workflow.yaml>` と同様の体裁**で `cli_main.py` に subcommand 登録

#### 2.3 使用例

```yaml
# epic-example.yaml
name: "Release v1.2 EPIC"
description: "Issues required for v1.2 release"
members:
  - issue: 200
  - issue: 201
    depends_on: [200]
  - issue: 202
    depends_on: [200]
    parallel_group: "frontend"
  - issue: 203
    depends_on: [201, 202]
    merge_order: 1
```

```bash
$ kaji validate-epic epic-example.yaml
✓ epic-example.yaml

$ kaji validate-epic broken.yaml
✗ broken.yaml
  - cyclic dependency detected: 201 → 202 → 201
  - issue 999 referenced in depends_on but not in members
```

### エラー一覧（共通）

| 区分 | 種類 | 戻り値 / 例外 |
|------|------|---------------|
| post-merge | gh API 失敗 | スキルは ABORT verdict を返す |
| post-merge | PR 未発見 | ABORT |
| post-merge | CI run 未発見 | ABORT |
| EPIC | YAML パースエラー | `EpicValidationError`、CLI は exit 1 |
| EPIC | バリデーションエラー | `EpicValidationError`（複数エラーをまとめて報告） |

## 制約・前提条件

- **共通**:
  - Python 3.12+ / 既存の `kaji_harness` パッケージへ追加
  - `make check` が通ること（ruff / mypy / pytest）
- **post-merge 系**:
  - `gh` CLI がインストール済み（既存スキル群と同条件）
  - GitHub Actions を CI として使用していることが前提（`gh run list` の意味的整合）
  - codex CLI が利用可能（既存 `review-code` 等と同じ前提）
  - polling は同期的に sleep して行う。並列実行や非同期化はしない
- **EPIC 系**:
  - 既存 `pydantic`（`config.py` 等で利用済み）に依存。新規ライブラリは導入しない
  - DAG / topological sort は標準ライブラリのみで実装（`networkx` 等を新規追加しない）
  - 本 Issue では EPIC ランタイム（実行）には踏み込まない。CLI は検証専用

## 変更スコープ

| 領域 | 追加 / 変更 | 既存への影響 |
|------|------------|-------------|
| `.claude/skills/wait-merge/SKILL.md` | 新規 | なし |
| `.claude/skills/verify-main-green/SKILL.md` | 新規 | なし |
| `.claude/skills/post-merge-review/SKILL.md` | 新規 | なし |
| `.claude/skills/post-merge-close/SKILL.md` | 新規 | なし（既存 `issue-close` は変更しない） |
| `workflows/feature-with-postmerge.yaml` | 新規 | なし（既存 `feature-development.yaml` は touch しない） |
| `kaji_harness/postmerge.py` | 新規モジュール（gh / git 出力パースと判定の純粋関数群） | なし。SKILL.md から `python -m kaji_harness.postmerge ...` 等で呼び出し |
| `kaji_harness/epic.py` | 新規モジュール（モデル + ローダ + バリデータ） | なし |
| `kaji_harness/cli_main.py` | `validate-epic` subcommand 追加 | 既存 `run` / `validate` には影響なし |
| `tests/test_postmerge.py` | 新規（スモール） | なし |
| `tests/test_epic.py` | 新規（スモール） | なし |
| `tests/test_workflow_postmerge.yaml`（フィクスチャ） | 新規 | なし |
| `docs/dev/workflow_overview.md` | post-merge ワークフローの存在を追記 | 微修正のみ |
| `docs/cli-guides/` | `validate-epic` のガイド追加 | 新規ファイル |

## 方針（Minimal How）

### post-merge 系スキル

1. 各スキルは既存スキル（例: `pr-fix`）と同じ SKILL.md 構造（前提知識読込 / 実行手順 / Verdict 出力）を踏襲
2. polling / 外部コマンド呼び出しは SKILL.md 上の Bash 手順として記述。ただし**判定ロジック（merge 状態の解釈、CI conclusion の解釈、コミット範囲の確定、codex 出力からの verdict 抽出）は `kaji_harness/postmerge.py` の純粋関数として切り出し**、SKILL.md からはモジュール呼び出しで参照する（review 指摘 3 への対応）
3. `kaji_harness/postmerge.py` に置く関数（純粋関数 / 副作用なし）:
   - `parse_pr_state(pr_view_json: str) -> Literal["MERGED", "OPEN", "CLOSED_NOT_MERGED"]`
   - `judge_main_ci(run_list_json: str) -> Literal["GREEN", "IN_PROGRESS", "FAILED", "NOT_FOUND"]`
   - `select_review_range(merge_commit: str, parent_count: int) -> tuple[str, str]`（base..head の確定）
   - `parse_codex_review_output(output: str) -> Literal["PASS", "RETRY", "ABORT"]`
4. `wait-merge`: `for` ループ + `gh pr view --json state,mergedAt,mergeCommit` + `sleep <interval>` → `parse_pr_state` で判定
5. `verify-main-green`: `gh run list --branch main --commit <sha> --json status,conclusion,name` → `judge_main_ci` で判定
6. `post-merge-review`: `git log` でコミット範囲確定（`select_review_range` 使用）→ codex を spawn → `parse_codex_review_output` で verdict 抽出
7. `post-merge-close`: 既存 `issue-close` の Step 4 以降のみを実行（Step 1.4 参照）。判定ロジックは `parse_pr_state` 1 関数のみ流用
8. ワークフロー YAML は既存の `feature-development.yaml` を雛形として複製。差分は末尾の 4 ステップ追加（`pr` の遷移先変更含む）のみ

### EPIC スキーマ

1. `EpicMember` / `EpicConfig` を Pydantic v2 モデルで定義
2. ローダ: `load_epic(path: Path) -> EpicConfig`（`yaml.safe_load` → `EpicConfig.model_validate(data)`）
3. DAG 検証:
   - 隣接リスト構築 → DFS で back-edge を検出 → 循環パスを erros に追加
   - 全エラーを 1 回の検証で収集（既存 `validate_workflow` と同じ accumulator パターン）
4. 並列グループ推定:
   - `parallel_group` 明示があればその値で grouping
   - なければ Kahn のアルゴリズムで topological levels を計算し、同 level を 1 グループとする
5. CLI: `cli_main.py` の subparser に `validate-epic` を追加。既存 `cmd_validate` と同じ体裁の `cmd_validate_epic` を実装

## テスト戦略

### 変更タイプ

実行時コード変更（EPIC 系）と、コード変更を含まない宣言定義追加（post-merge スキル + ワークフロー YAML）の混在。それぞれを区別して扱う。

### EPIC 系（実行時コード変更）

#### Small テスト
- `EpicConfig` バリデーション
  - 正常系: 単純 DAG / 並列グループ明示 / `merge_order` 明示
  - 異常系: 循環依存（自己ループ含む）、未定義 Issue への `depends_on`、Issue 重複、`merge_order` 重複、`members` 空
- `topological_order()` の境界:
  - `parallel_group` 明示優先
  - 明示なし時の Kahn による level 推定
- `sorted_merge_order()` の境界:
  - 明示順序が topological 順序と一致しないケースで明示優先
- `load_epic()` の YAML パースエラーハンドリング

#### Medium テスト
- CLI `kaji validate-epic` のエンドツーエンド:
  - 一時ディレクトリに YAML を配置 → `subprocess.run(["kaji", "validate-epic", path])`
  - exit code / stdout / stderr の検証
  - 既存 `tests/test_cli_validate.py` と同様のパターン

#### Large テスト
- 不要。EPIC 検証は外部 API / 外部サービスに依存しない（純粋ロジックと CLI 実行のみ）。
- `docs/dev/testing-convention.md` の 4 条件適用:
  1. 独自ロジック追加なし → ❌（追加あり）。よって Small / Medium で担保すべきで Large は不要
  2-4. 該当なし。Large 不要の根拠は「外部依存なし」で十分

### post-merge 系スキル + ワークフロー YAML

post-merge 系は判定ロジックを `kaji_harness/postmerge.py` に切り出すため、ロジック層は通常の Python コード変更として扱う。
これにより Issue 完了条件「各スキルにユニットテスト（mock 可）が存在する」と整合する（review 指摘 3 への対応）。

#### Small テスト

`tests/test_postmerge.py` で `kaji_harness/postmerge.py` の純粋関数を検証する。
各関数のテストはそれぞれ対応するスキルの判定ロジックを担保する:

- **`parse_pr_state`** (`wait-merge` / `post-merge-close` のロジック):
  - `state=MERGED` の JSON → `"MERGED"`
  - `state=OPEN` → `"OPEN"`
  - `state=CLOSED && mergedAt=null` → `"CLOSED_NOT_MERGED"`
  - 不正 JSON / フィールド欠落の例外
- **`judge_main_ci`** (`verify-main-green` のロジック):
  - 全 run が `conclusion=success` → `"GREEN"`
  - いずれかが `status=in_progress` または `queued` → `"IN_PROGRESS"`
  - いずれかが `conclusion=failure / cancelled / timed_out` → `"FAILED"`
  - run 0 件 → `"NOT_FOUND"`
  - **複数 run が混在する境界**: in_progress と failure が同時に存在する場合は `"FAILED"` を優先（早期 ABORT）
- **`select_review_range`** (`post-merge-review` のロジック):
  - 通常 merge commit (parent=2) → `(parent1, merge_commit)`
  - squash merge (parent=1) → `(merge_commit~1, merge_commit)`
- **`parse_codex_review_output`** (`post-merge-review` のロジック):
  - `---VERDICT---\nstatus: PASS ...` → `"PASS"`
  - `status: RETRY` → `"RETRY"`
  - verdict ブロック欠落 / 不明 status → `"ABORT"`

これらは「各スキルの判定ロジックに対する mock 不要のユニットテスト」として完了条件を満たす。

加えて、ワークフロー YAML 自体の整合性検証として:

- `workflows/feature-with-postmerge.yaml` を `validate_workflow()` + `validate_skill_exists()` で検証する pytest を追加
  - 全ステップが skill discoverable（4 つの新規スキル含む）
  - 全 verdict 遷移先が valid
  - 既存ワークフロー検証テストパターンを踏襲

#### Medium テスト
- 不要。post-merge 系は (a) 純粋関数 (Small で網羅) と (b) bash 手順による外部コマンド呼び出し (gh / git / codex) のみで構成される
- 外部コマンド呼び出し部分のテストは subprocess 全モックとなり、`docs/dev/testing-convention.md` 4 条件のうち以下を満たす:
  1. 独自ロジックは Small に切り出し済み ✅
  2. 既存ゲート（`validate_workflow` + Small の純粋関数テスト）で判定ロジックは捕捉済み ✅
  3. 全モック Medium は回帰検出の情報量が乏しい ✅
  4. 本理由を本セクションに記載 ✅

#### Large テスト
- 不要。post-merge ワークフローの実走には実 PR / 実マージ / 実 CI green が必要で、CI で再現するには専用の sandbox repo が必要となり投資対効果が見合わない
- 4 条件適用: 「物理的に作成不可（CI 上で実 PR をマージするサンドボックスは未提供）」が `docs/dev/testing-convention.md` の正当化理由に該当

#### 変更固有検証（恒久テストにしないもの）
- 開発時に手動で `kaji run workflows/feature-with-postmerge.yaml <issue> --before post-merge-close` を 1 回実走し、各ステップが想定通り遷移することを目視確認
- この実走結果は Issue にコメントで残し、`/i-dev-final-check` で確認する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規ライブラリ / アーキテクチャ判断はない |
| docs/ARCHITECTURE.md | なし | 既存層構造は変えない |
| docs/dev/workflow_overview.md | あり | post-merge ワークフローの存在を選択肢として追記 |
| docs/dev/development_workflow.md | あり（軽微） | post-merge 系の skill 名のみ言及 |
| docs/dev/shared_skill_rules.md | あり（軽微） | `wait-merge` 等が共有スキル相当か言及 |
| docs/reference/ | なし | コーディング規約に変更なし |
| docs/cli-guides/ | あり | `kaji validate-epic` のガイドを新規追加 |
| CLAUDE.md | あり（軽微） | Essential Commands に `kaji validate-epic` を追加 |
| .github/labels.yml | なし | 新規ラベル不要 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| GitHub CLI: `gh pr view` | https://cli.github.com/manual/gh_pr_view | `--json state,mergedAt,mergeCommit` で PR の merge 状態と merge commit oid を取得できる。`wait-merge` のポーリング実装の根拠 |
| GitHub CLI: `gh run list` | https://cli.github.com/manual/gh_run_list | `--branch <name> --commit <sha> --json status,conclusion` で特定コミットの CI run を取得できる。`verify-main-green` の green 判定の根拠 |
| Pydantic v2: Models | https://docs.pydantic.dev/latest/concepts/models/ | `BaseModel.model_validate(data)` による dict → モデル検証の標準パターン。`load_epic` の根拠 |
| Pydantic v2: Validators | https://docs.pydantic.dev/latest/concepts/validators/ | `@model_validator(mode="after")` でモデル横断の制約（DAG 循環、Issue 重複等）を検証可能。`EpicConfig` 検証の根拠 |
| Kahn's algorithm (topological sort) | https://en.wikipedia.org/wiki/Topological_sorting#Kahn's_algorithm | "repeatedly remove a node with no incoming edges" を level 単位で繰り返すと、各 level が並列実行可能なグループになる。`topological_order()` の根拠 |
| 既存ワークフロー仕様 | docs/dev/workflow_overview.md / workflows/feature-development.yaml | 新規ワークフロー `feature-with-postmerge.yaml` のステップ複製元・遷移ルールの基準 |
| testing-convention | docs/dev/testing-convention.md | テストサイズの判定 / 省略 4 条件の根拠 |
