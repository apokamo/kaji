# [設計] workflow の重複 step ID を validation で拒否する

Issue: #355

## 概要

`validate_workflow()` に step ID の一意性検査を追加し、同じ ID を持つ複数 step を含む workflow を実行前 validation で拒否する（重複 ID を指すメッセージを `errors` に持つ `WorkflowValidationError` を送出する）。

## 背景・目的

`Workflow.find_step()` は最初に一致した step を返すため（`kaji_harness/models.py:96-101`）、重複 ID を持つ workflow では 2 番目以降の同名 step が silently shadow される。遷移先解決・resume 先解決・cycle membership・到達可能性検査はすべて `find_step()` または ID の `set` 畳み込みに依存しており、重複 ID があると「YAML に書いた step」と「実行される step」が乖離する。この乖離は validation を通過するため、定義ミスが実行時の暗黙の step 取り違えとしてしか表面化しない。

### Observed Behavior (OB)

重複 step ID (`alpha` × 2) を含む最小 workflow に対し、commit `33f7ea9` の作業ツリー（worktree `../kaji-fix-355`、branch `fix/355`）で本設計時に再実測した結果:

```
steps: [('alpha', 'issue-design', 'claude'), ('beta', 'issue-implement', 'claude'), ('alpha', 'issue-review-code', 'codex')]
find_step(alpha): ('alpha', 'issue-design', 'claude')
validate_workflow: returned None (no error raised)
```

`kaji validate ./dup-repro.yaml; echo "exit=$?"` は `✓ dup-repro.yaml` / `exit=0`（Issue #355 本文「再現手順」手順 2 の実測）。

すなわち `validate_workflow()` は例外を送出せず `None` を返し、3 番目の `alpha`（`issue-review-code` / `codex`）は 1 番目の `alpha`（`issue-design` / `claude`）に shadow されたまま検出されない。到達可能性検査も ID を `set[str]` へ畳み込む（`kaji_harness/workflow.py:515`）ため、shadow された step を「到達不能」としても報告しない。

### Expected Behavior (EB)

- `validate_workflow()` が重複 ID を指すメッセージを `errors` に格納した `WorkflowValidationError` を送出する（Issue #355「期待する状態」「完了条件」）。
- `kaji validate ./dup-repro.yaml` が exit code 1 と重複 ID を指すエラーを返す。
- 戻り値契約 `-> None`（成功時）と失敗時の `WorkflowValidationError` 送出は変更しない（Issue #355「既存契約」、`kaji_harness/workflow.py:359-366, 604-605`、`docs/reference/python/naming-conventions.md:75`）。

## 再現手順

前提条件: worktree `../kaji-fix-355`（branch `fix/355`、base commit `33f7ea9`）、`source .venv/bin/activate` 済み。

1. 重複 step ID (`alpha` が 2 回) を持つ最小 workflow `dup-repro.yaml` を作成する（内容は Issue #355「再現手順」手順 1 の YAML と同一）。
2. `kaji validate ./dup-repro.yaml; echo "exit=$?"` を実行する → OB: `✓ dup-repro.yaml` / `exit=0`。
3. `load_workflow()` → `find_step('alpha')` → `validate_workflow()` を直接駆動する（Issue #355「再現手順」手順 3 の python スクリプト）→ OB: 上記「Observed Behavior」の 3 行。

本設計では手順 3 を再実行して OB を一次確認した。手順で作成した YAML は scratchpad 配下に置き、repo にはコミットしない。

## 根本原因

### なぜ壊れているか

`validate_workflow()` の検査は「各 step 単体の妥当性」と「ID による参照の解決可能性」で構成されており、**ID が識別子として機能する前提（一意性）そのものを検査していない**。具体的には:

- 遷移先検査（`kaji_harness/workflow.py:502-506`）は `find_step(next_id)` が `None` でないことのみ確認する。先頭一致で解決するため、重複があっても「存在する」と判定される。
- 到達可能性検査（同 513-537）は `step_ids = {step.id for step in workflow.steps}` と `reachable_step_ids: set[str]` で ID を集合に畳み込む。集合は重複を吸収するため、shadow された step は「到達済み ID」として扱われ、未報告のまま通過する。
- cycle の entry / loop / tail 検査（同 565-598）も `find_step()` 経由で先頭一致 step に解決される。

したがって重複 ID は、参照解決系のどの検査にも「エラーとして観測されない形」で吸収される。検出には、参照解決とは独立した「step 集合そのものの一意性」検査が必要である。

### いつから壊れているか

`find_step()` の先頭一致実装と `validate_workflow()` は commit `a3baedc`（`refactor: rename dao → kaji (package, CLI, docs) for #73`）時点で既に現在の形であり、一意性検査は **一度も存在したことがない**。ID を `set` に畳み込む到達可能性検査は commit `f5c778d`（`feat: tighten workflow validation for #339`）で追加された。#339 のコードレビューで本 gap がスコープ外の既存欠陥として分離され、本 Issue に至った（Issue #355「発見時点」）。

### 同じ原因で他に壊れている箇所

`find_step()` の先頭一致に依存する呼び出し箇所（遷移先 / resume / cycle entry / cycle loop / cycle tail、`kaji_harness/workflow.py:477, 492, 503, 524, 565, 570, 575, 593`）はすべて同じ曖昧性を持つが、これらは individually に壊れているのではなく、**一意性検査の欠落という単一原因の派生**である。重複 ID を validation で拒否すれば、これらの呼び出しは一意な step に解決されることが保証される（Issue #355「期待する状態」2 項目目）。したがって個別の呼び出し側修正は不要であり、修正は一意性検査の追加 1 点に閉じる。

### スコープ外の隣接 gap（本 Issue では修正しない）

本設計時の調査で、`step.id` が非 str（例: YAML `id: [a, b]`）の場合に `validate_workflow()` が `WorkflowValidationError` ではなく raw `TypeError: unhashable type: 'list'` を送出することを観測した（発生箇所: `kaji_harness/workflow.py:515` の集合内包表記。branch `fix/355` / commit `33f7ea9` で確認）。

```
parsed id: ['a', 'b'] list
validate raised TypeError : unhashable type: 'list'
```

これは `_parse_workflow()` の `_STEP_REQUIRED_KEYS = ("id",)` が id の**存在**のみ検査し**型**を検査しないことに起因する別欠陥であり、重複 ID の検出可否とは独立している。本 Issue のスコープ（重複 step ID の検出）には含めず、follow-up Issue #357 で追跡する。本設計の一意性検査は既存の `set` 畳み込み（515 行）と同じ hashable 前提を共有するため、この挙動を改善も悪化もさせない（後述「制約・前提条件」）。

## インターフェース

既存 IF は維持する。公開シグネチャ・戻り値・例外型はいずれも変更しない。

### 入力

`validate_workflow(workflow: Workflow) -> None` の引数 `workflow`。変更なし。

### 出力

| 条件 | 変更前 | 変更後 |
|------|--------|--------|
| step ID が一意、他エラーなし | `None` を返す | `None` を返す（不変） |
| step ID が重複 | `None` を返す（OB） | `errors` に重複 ID を指すメッセージを含む `WorkflowValidationError` を送出（EB） |
| step ID が重複 + 他の validation error | 他エラーのみを含む `WorkflowValidationError` を送出 | 単一の `WorkflowValidationError` の `errors` に両方を含む |

`kaji validate` は `WorkflowValidationError.errors` を stderr へ整形出力し exit code 1 を返す既存経路（`kaji_harness/commands/validate.py:83-84, 96, 104-108`）をそのまま使うため、CLI 側の変更は不要である。

エラーメッセージ書式（重複 ID 1 件につき 1 メッセージ）:

```
Duplicate step id 'alpha' (defined 2 times)
```

### 使用例

```python
from pathlib import Path

from kaji_harness.errors import WorkflowValidationError
from kaji_harness.workflow import load_workflow, validate_workflow

wf = load_workflow(Path("dup-repro.yaml"))
try:
    validate_workflow(wf)
except WorkflowValidationError as e:
    print(e.errors)
    # ["Duplicate step id 'alpha' (defined 2 times)"]
```

```console
$ kaji validate ./dup-repro.yaml; echo "exit=$?"
✗ dup-repro.yaml
  - Duplicate step id 'alpha' (defined 2 times)
exit=1
```

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/workflow.py` | `validate_workflow()` に step ID 一意性検査を追加 |
| `tests/test_workflow_validator.py` | 重複系 / 正常系 / 集約の Small テストを追加 |
| `tests/test_cli_validate.py` | `kaji validate` 経由の重複検出 Medium テストを追加 |
| `docs/dev/workflow-authoring.md` | step `id` の一意性制約と L2 検証項目に重複 ID を追記 |

`find_step()`（`kaji_harness/models.py`）、`_parse_workflow()`、`preflight.py`、`commands/validate.py` は変更しない。リファクタは混在させない。

## 制約・前提条件

- **戻り値・例外契約を変更しない**: 成功時 `None`、失敗時に全エラーを `errors: list[str]` に格納した `WorkflowValidationError` を送出する（Issue #355「既存契約」）。
- **エラーを集約する**: `validate_workflow()` は `errors` を蓄積し末尾で 1 回だけ raise する（`kaji_harness/workflow.py:604-605`）。重複検査もこの集約に従い、検出時に即時 raise しない（Issue #355 の集約テスト条件を満たすため）。
- **L1 / L2 の層分離を守る**: `docs/dev/workflow-authoring.md:85-87` の定義では L1（`_parse_workflow`）が「YAML parse、型、必須、排他、許容値」＝ **step 単体のスキーマ**、L2（`validate_workflow`）が「step/cycle の遷移先、resume、verdict、到達可能性など workflow 内の参照」＝ **step 横断の整合**を担う。ID の一意性は単一 step のスキーマでは判定できない step 横断の性質であるため L2 に置く。
- **hashable 前提の据え置き**: 一意性検査は `step.id` が hashable であることを前提とする。これは既存の到達可能性検査（`kaji_harness/workflow.py:515`）が既に置いている前提と同一であり、非 str id の raw `TypeError`（前述「スコープ外の隣接 gap」）は本 Issue で改善も悪化もさせない。
- **既存 workflow を壊さない**: `.kaji/wf/*.yaml` は設計レビュー時の直接 `Counter` 走査で ID 一意を確認済み。本変更後は `tests/test_workflow_validator.py::TestDuplicateStepIdValidation::test_repository_workflows_have_unique_step_ids` が L2 validation を直接固定し、既存 `tests/test_cli_validate.py::test_repository_workflows_all_validate` が CLI + preflight 回帰網となる。
- Python >= 3.11（`pyproject.toml:6`）。`collections.Counter` は標準ライブラリで追加依存なし。

## 方針

`validate_workflow()` のワークフローレベル検証（`if not workflow.steps:` の直後、step 単体ループより前）に一意性検査を挿入する。step 横断の検査であり、後続の参照解決系エラーより先に読ませたいためこの位置とする。

```python
from collections import Counter

# ワークフローレベルの検証
if not workflow.steps:
    errors.append("Workflow must have at least one step")

# step ID の一意性（find_step() は先頭一致で解決するため、重複は後続 step を
# silently shadow する。Issue #355）
id_counts = Counter(step.id for step in workflow.steps)
for step_id, count in id_counts.items():
    if count > 1:
        errors.append(f"Duplicate step id '{step_id}' (defined {count} times)")
```

- **1 重複 ID = 1 エラー**: 同一 ID が N 回出現しても `errors` は 1 件とし、出現回数を `(defined N times)` で示す。N 件に分割すると同じ事実が重複報告され、他の validation error に埋もれる。
- **報告順序の決定性**: `Counter` は挿入順（= step 定義順）を保持する dict を継承するため、複数の ID が重複する場合は**最初に出現した順**で報告される。既存の到達可能性検査が定義順で報告する慣習（`tests/test_workflow_validator.py::test_unreachable_steps_are_reported_in_declaration_order`）と揃う。
- **検査を打ち切らない**: 重複検出後も step 単体ループ・到達可能性・cycle 検査を継続し、1 回の呼び出しで全エラーを集約する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 戻り値・例外契約 | `-> None` を維持し、重複 ID を `WorkflowValidationError.errors` に追加する | Issue #355 「既存契約」「完了条件」1 項目目（人間決定）。一次情報: `kaji_harness/workflow.py:359-366, 604-605` / `kaji_harness/errors.py:55-65` / `docs/reference/python/naming-conventions.md:75` | 既存の `errors` 集約経路（604-605 の単一 raise）に検査を合流させ、CLI 側を無改修とする構成へ分解 |
| 検査の配置層（L1 parse か L2 validate か） | L2 = `validate_workflow()` にのみ置き、`_parse_workflow()` には追加しない | Issue #355 「完了条件」が `validate_workflow()` での検出と、他 validation error との単一例外への集約を要求（人間決定）。層定義の一次情報: `docs/dev/workflow-authoring.md:85-87` | L1 に fail-fast を足すと集約要件（`kaji validate` が重複と他エラーを同時報告）を壊すこと、および ID 一意性が step 横断の性質で L1 の「step 単体スキーマ」に該当しないことを根拠に L2 単独と確定 |
| エラーメッセージ書式 | `Duplicate step id 'alpha' (defined 2 times)` | AI の仮定。根拠: Issue #355 EB の「`Duplicate step id 'alpha'` 相当」という文言と、既存メッセージが `Step '<id>' ...` / `Cycle '<name>' ...` と主語を引用符で示す慣習（`kaji_harness/workflow.py:420-598`）。後段の検査先: `/issue-review-design`（書式の妥当性）、`/issue-review-code`（テストが固定する文字列との整合） | 重複 ID 1 件につき 1 メッセージ、出現回数を括弧内に添える形へ具体化 |
| 重複 ID の報告粒度と順序 | 1 ID = 1 エラー、step 定義順 | AI の仮定。根拠: 既存の到達可能性検査が定義順で 1 step = 1 エラーを報告する（`tests/test_workflow_validator.py::test_unreachable_steps_are_reported_in_declaration_order`）。後段の検査先: `/issue-review-code`（テストで順序を固定） | `Counter` の挿入順保持を用いて決定的順序を担保する構成へ具体化 |
| 隣接 gap（非 str `id` の raw `TypeError`）の扱い | 本 Issue では修正せず follow-up Issue #357 で追跡する | AI の仮定。根拠: Issue #355 の目的・完了条件はいずれも「重複 step ID の検出」に限定され、id の型検証を含まない。`_shared/report-unrelated-issues.md` の報告フローに従い既存 Issue を検索し、該当なしを確認後に Issue #357 を起票した。 | 観測結果・発生箇所・原因（L1 の id 型検証欠落）を本設計に記録し、本変更が当該挙動を改善も悪化もさせないことを制約として明示 |
| `find_step()` の先頭一致仕様 | 変更しない | AI の仮定。根拠: Issue #355「期待する状態」は「重複 ID が曖昧な解釈を生まない」ことを求めており、validation で重複を拒否すれば先頭一致は一意解決に帰着する。`find_step()` 自体の変更は IF 変更を伴い Issue の完了条件外。後段の検査先: `/issue-review-design`（修正箇所の妥当性） | 修正を一意性検査の追加 1 点に閉じ、`find_step()` 呼び出し側（8 箇所）を無改修とする方針へ具体化 |

## テスト戦略

### 変更タイプ

実行時コード変更（`validate_workflow()` の検証ロジック追加）。

### bug 固有ルール: 再現テスト（Red → Green）

`tests/test_workflow_validator.py` に重複 ID の再現テストを**実装より先に**追加し、修正前に Red（`pytest.raises(WorkflowValidationError)` が `Failed: DID NOT RAISE` で失敗）となることを確認してから実装へ進む。Issue #355 本文には OB の実ログがあるが、escape clause には依らず実装前 Red 証跡を取得する（Small テストのため取得コストが低い）。

#### Small テスト

`tests/test_workflow_validator.py`（新規クラス `TestDuplicateStepIdValidation` を追加）。純粋ロジック・外部依存なしのため Small。

- **重複系（再現テスト）**: 同一 ID を 2 回持つ workflow で `validate_workflow()` が `WorkflowValidationError` を送出し、`errors` に重複 ID `alpha` を指すメッセージが含まれる（Issue 完了条件 3 項目目）。
- **報告粒度**: 同一 ID が 3 回出現しても `errors` の重複エラーは 1 件で、出現回数が反映される。
- **報告順序**: 2 つの ID が重複する workflow で、定義順（最初の出現順）に報告される。
- **正常系**: ID が一意な workflow で例外を送出せず `None` を返す（Issue 完了条件 4 項目目の一部）。既存の `TestValidWorkflows` が回帰網となるため、重複検査が誤検出しないことは既存テスト群の pass でも担保される。
- **集約**: 重複 ID と未知の遷移先（`Step 'X' transitions to unknown step 'Y' on PASS`）を同時に持つ workflow で、1 回の `validate_workflow()` 呼び出しが送出する**単一の** `WorkflowValidationError` の `errors` に両方が含まれる（Issue 完了条件 5 項目目）。

#### Medium テスト

ファイル I/O・CLI 経路の結合のため Medium。

- **`kaji validate` 経由の重複検出**: `tests/test_cli_validate.py` に、重複 ID の YAML を tmp_path へ書き出し `_cmd_validate_with_args` で `cmd_validate` を駆動して exit code 1 と stderr の重複 ID メッセージを固定するテストを追加する（Issue 完了条件 2 項目目）。L2 validation error が L3 の config / skill 検査より先に確定する既存テスト経路を使うため、`_create_skill` / `_create_config` は呼び出さない。
- **リポジトリ管理 workflow の正常系**: `.kaji/wf/*.yaml` を `load_workflow()` → `validate_workflow()` で直接駆動し、例外を送出しないことを固定するテストを `tests/test_workflow_validator.py` に追加する（Issue 完了条件 4 項目目。ファイル読み込みを伴うため Medium）。既存の `tests/test_cli_validate.py::test_repository_workflows_all_validate` は CLI + preflight（L3 込み）経路を固定しており、`validate_workflow()` 単体の契約（例外を送出せず `None`）は固定していないため、両者は重複しない。

#### Large テスト

**不要**。理由を `docs/dev/testing-convention.md` の 4 条件に照らして記載する:

1. 本変更は `validate_workflow()` 内の純粋ロジック追加であり、外部 API 疎通・E2E データフローに新規ロジックを追加しない。
2. 想定される不具合パターン（重複の未検出 / 誤検出 / メッセージ不一致 / exit code 不一致）は Small と Medium で捕捉できる。`kaji validate` の subprocess 起動経路そのものは既存の `TestCLIValidateLarge::test_kaji_validate_valid_yaml`（Large）が既に固定しており、本変更で起動経路は変わらない。
3. 重複 ID 用の Large を追加しても、Medium の CLI テストが返す exit code / stderr と同じシグナルを subprocess 越しに再取得するだけで、新しい回帰検出情報が増えない。
4. 以上により Large 省略は「実行時間が長い」「環境がない」といった不正当な理由ではなく、検証情報量に基づく判断である。

### 品質ゲート

`make check`（`ruff check` → `ruff format --check` → `mypy` → `pytest` 全実行）が pass すること（Issue 完了条件 6 項目目）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定・アーキテクチャ決定を伴わない。既存の L1/L2/L3 検証層（#339 で確立）の L2 に検査を 1 つ追加するのみ |
| docs/ARCHITECTURE.md | なし | モジュール構成・層構造は不変。`validate_workflow()` の責務範囲内の追加 |
| docs/dev/workflow-authoring.md | **あり** | (1) step フィールド表の `id` 行（188 行目）が「ステップ ID。英数字とハイフン」のみで一意性制約に言及していない。workflow 作者向けの仕様正本のため、workflow 内で一意である必要があることを追記する。(2) 検証層の表（85-87 行目）の L2 の説明「step/cycle の遷移先、resume、verdict、到達可能性など」に step ID の一意性を追記し、新検査の所属層を明示する |
| docs/dev/ その他 | なし | ワークフロー実行手順・開発手順は不変 |
| docs/reference/python/naming-conventions.md | なし | `validate_workflow(workflow) -> None` の記載（75 行目）は現行契約のままで正しい。契約を変更しないため修正不要 |
| docs/cli-guides/ | なし | `kaji validate` の CLI 仕様（引数・exit code の意味）は不変。検出されるエラーが 1 種類増えるだけで、exit code 1 の意味（validation error あり）は既存のまま |
| AGENTS.md / CLAUDE.md | なし | 開発規約の変更を伴わない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #355 本文 | https://github.com/apokamo/kaji/issues/355 | 「既存契約」節: `validate_workflow(workflow) -> None` は成功時 `None`、失敗時に全エラーを `errors: list[str]` に格納した `WorkflowValidationError` を送出する。「本 Issue はこの戻り値・例外契約を変更せず、重複 step ID を `errors` に加える検査を追加する」。完了条件が重複系 / 正常系 / 集約の 3 テストと `make check` を要求 |
| `validate_workflow()` 実装 | `kaji_harness/workflow.py:359-366` / `604-605` | docstring `Raises: WorkflowValidationError: 検証エラーがある場合`、末尾 `if errors: raise WorkflowValidationError(errors)` — 検証は `errors` を蓄積し 1 回だけ raise する集約設計。ID 一意性検査は存在しない |
| 到達可能性検査 | `kaji_harness/workflow.py:513-537` | `step_ids = {step.id for step in workflow.steps}` と `reachable_step_ids: set[str]` により ID を集合へ畳み込む → 重複が吸収され shadow された step が検出されない。同時に、id が hashable であることを既に前提としている |
| `Workflow.find_step()` | `kaji_harness/models.py:96-101` | `for step in self.steps: if step.id == step_id: return step` — 先頭一致で返すため、重複時は 2 番目以降が到達不能になる |
| `WorkflowValidationError` | `kaji_harness/errors.py:55-65` | `__init__(errors: list[str] | str)` が `self.errors` を保持し、list 指定時のメッセージは `f"{len(errors)} validation error(s): " + "; ".join(errors)` |
| `kaji validate` 実装 | `kaji_harness/commands/validate.py:83-84` / `96` / `104-108` | `except WorkflowValidationError as e: _print_error(path, e.errors)` → stderr へ `  - <error>` 形式で出力し、`return EXIT_VALIDATION_ERROR if failed > 0 else EXIT_OK`。CLI 無改修で EB（exit 1 + 重複エラー出力）を満たせる根拠 |
| workflow 検証層の定義 | `docs/dev/workflow-authoring.md:85-87` | 「L1: YAML parse/schema — YAML parse、型、必須、排他、許容値を検証」「L2: workflow 参照整合 — step/cycle の遷移先、resume、verdict、到達可能性など workflow 内の参照を検証」— 一意性検査を L2 に置く層判断の根拠 |
| step フィールド仕様 | `docs/dev/workflow-authoring.md:188` | `| id | str | ✅ | ステップ ID。英数字とハイフン |` — 一意性への言及がなく、追記対象であることの根拠 |
| 命名・契約規約 | `docs/reference/python/naming-conventions.md:75` | `def validate_workflow(workflow: Workflow) -> None:  # 検証（失敗時 raise）` — 戻り値契約を維持する根拠 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ判定基準（外部 API → Large / ファイル I/O・内部結合 → Medium / 純粋関数 → Small）、および恒久テストを追加しない 4 条件。Large 省略理由の根拠 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | 「修正前に Red になる再現テスト（regression test）を必ず 1 本以上定義する。省略不可」— 再現テスト先行方針の根拠 |
| 既存の報告順序慣習 | `tests/test_workflow_validator.py:217-235` | `test_unreachable_steps_are_reported_in_declaration_order` が `errors == [...]` を定義順で完全一致検証 — 重複エラーを定義順で報告する設計判断の根拠 |
| リポジトリ workflow の現行妥当性 | `tests/test_workflow_validator.py::TestDuplicateStepIdValidation::test_repository_workflows_have_unique_step_ids` / `tests/test_cli_validate.py::test_repository_workflows_all_validate` | 設計レビュー時に 9 workflow を直接 `Counter` 走査して ID 一意を確認。実装後は前者が全 `.kaji/wf/*.yaml` の L2 validation を直接固定し、後者が CLI + preflight 回帰網として exit 0 を固定する |
| `collections.Counter` | https://docs.python.org/3/library/collections.html#collections.Counter | 「Elements are counted from an iterable」。`Counter` は `dict` のサブクラスであり、Python 3.7 以降の dict と同じく挿入順を保持する（https://docs.python.org/3/library/stdtypes.html#dict — "Dictionaries preserve insertion order"）。定義順の決定的報告に依拠できる根拠 |
