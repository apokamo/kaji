# [設計] workflow parse 境界に step ID / verdict の型ガードを追加する

Issue: #357

## 概要

`_parse_workflow()` に step ID 系（`id` / `resume` / `cycle.entry` / `cycle.loop` 要素）と verdict 系（`on` のキー / `cycle.on_exhaust`）の型ガードを追加し、非文字列値を parse 境界で `WorkflowValidationError` として拒否する（現状は `validate_workflow()` の集合演算・正規表現まで到達し raw `TypeError` になるか、silently accept される）。

## 背景・目的

`_parse_workflow()` は L1（YAML parse / schema）層として型検証を担うが（`docs/dev/workflow-authoring.md:85-87`）、型検証はフィールドごとに個別実装されており、**step ID 系と verdict 系だけが漏れている**。その結果、非文字列値が L2（`validate_workflow()`）の `set` 畳み込み・`Counter`・`re.match()` に到達し、workflow 定義エラーとして扱われるべき入力が内部実装由来の `TypeError` として利用者に露出する。さらに hashable な不正値（`null` / `""` / `int` / `bool`）は crash すらせず、不正な step ID を持つ workflow が validation を通過する。

### Observed Behavior (OB)

worktree `../kaji-fix-357`（branch `fix/357`、base commit `7597cbf`）で Issue #357 本文の再現スクリプトを本設計時に再実測した結果:

```text
1. step.id = list        -> TypeError: unhashable type: 'list'
2. cycle.entry = list    -> TypeError: unhashable type: 'list'
3. cycle.loop[0] = list  -> TypeError: unhashable type: 'list'
4. on_exhaust = list     -> TypeError: unhashable type: 'list'
5. on key = null         -> TypeError: expected string or bytes-like object, got 'NoneType'
6. step.id = null        -> ACCEPTED (no error)
7. step.id = ''          -> ACCEPTED (no error)
8. step.resume = list    -> WorkflowValidationError: 1 validation error(s): Step 'a' resumes unknown step '['x']'
```

本設計時の追加実測（Issue 完了条件 3 項目目の `id: 1` / `id: true`、および設計判断のための周辺ケース）:

```text
step.id = 1 (int)        -> ACCEPTED  (parsed id=1 resume=None)
step.id = True (bool)    -> ACCEPTED  (parsed id=True resume=None)
on key = 1 (int)         -> TypeError: expected string or bytes-like object, got 'int'
resume = '' (empty)      -> ACCEPTED  (parsed id='a' resume='')
cycle.entry = ''         -> WorkflowValidationError: ... Cycle 'c' entry step '' not found; ...
on_exhaust = ''          -> WorkflowValidationError: ... Cycle 'c' on_exhaust '' is invalid
```

すなわち失敗モードは 3 種類ある:

- **raw `TypeError` 露出**（ケース 1〜5、`on key = 1`）: unhashable 値が `set` / `Counter` に入る、または非 str が `re.match()` に入る
- **silent accept**（ケース 6・7、`id: 1` / `id: true`、`resume: ''`）: hashable な不正値が全検査を通過する
- **誤誘導メッセージ**（ケース 8）: 型エラーを「存在しない step への参照」と報告する

### Expected Behavior (EB)

- ケース 1〜8 および `id: 1` / `id: true` がすべて `WorkflowValidationError` として拒否される。raw `TypeError` と silent accept を出さない（Issue #357「完了条件」1〜3 項目目）。
- エラーメッセージが該当フィールド（`id` / `resume` / `entry` / `loop` / `on_exhaust` / `on` のキー）を指す（同 2 項目目）。ケース 8 は「存在しない step への参照」ではなく型エラーとして報告する。
- `kaji validate` 経由でも exit code 1 と診断を出力する（同 4 項目目）。既存 CLI 経路（`kaji_harness/commands/validate.py`）が `WorkflowValidationError` を整形出力する構造は変更しない。
- 正常な workflow（文字列 `id` / `resume` / cycle 参照）の parse 結果と `validate_workflow()` の戻り値契約は不変（`.kaji/wf/*.yaml` の全 workflow が現状どおり検証を通る）。

EB の裏付け: `docs/dev/workflow-authoring.md:188` は step `id` の型を `str` と規定し、`kaji_harness/models.py:47-68` の `Step.id: str` / `CycleDefinition.entry: str` / `loop: list[str]` / `on_exhaust: str` / `on: dict[str, str]` も str を宣言している。現状の実装はこの宣言を parse 境界で担保していない。

## 再現手順

前提条件: worktree `../kaji-fix-357`（branch `fix/357`、base commit `7597cbf`）、`source .venv/bin/activate` 済み。

1. Issue #357 本文「再現手順」手順 1 のスクリプト（`_parse_workflow()` → `validate_workflow()` を 8 ケースで駆動する）を scratchpad に保存する。
2. `python <スクリプト>` を実行する → OB: 上記「Observed Behavior」の 8 行。
3. 追加実測（`id: 1` / `id: true` / `on key: 1` / `resume: ''` / `entry: ''` / `on_exhaust: ''`）を同じ駆動方法で実行する → OB: 上記「本設計時の追加実測」の 6 行。

本設計では手順 2・3 をいずれも再実行して OB を一次確認した。スクリプトは scratchpad 配下に置き、repo にはコミットしない（恒久化はテスト戦略の Small テストが担う）。

## 根本原因

### なぜ壊れているか

`_parse_workflow()` は L1 として「型、必須、排他、許容値」を検証する責務を持つ（`docs/dev/workflow-authoring.md:85-87`）。実装は**フィールドごとの個別ガード**の集合であり、共通の型検証機構を持たない。

#### 本 Issue の対象母集団: step ID / verdict の全参照点

Issue #357「決定事項」3 が定める単一ルール「step ID / verdict は文字列」の**参照点をすべて列挙**すると、以下の 6 箇所であり、いずれも型ガードを持たない:

| 参照点 | 種別 | 現状の型ガード | 位置 |
|--------|------|---------------|------|
| step `id` | step ID | **なし**（`_STEP_REQUIRED_KEYS = ("id",)` は存在のみ検査） | `workflow.py:58, 132-138, 239` |
| step `resume` | step ID 参照 | **なし** | `workflow.py:248` |
| `cycle.entry` | step ID 参照 | **なし** | `workflow.py:292` |
| `cycle.loop` 要素 | step ID 参照 | **なし**（`loop` が list であることのみ検査） | `workflow.py:274-278, 294` |
| step `on` のキー | verdict | **なし**（`on` 自体が非空 dict であることのみ検査） | `workflow.py:158-170` |
| `cycle.on_exhaust` | verdict | **なし** | `workflow.py:295` |

step ID / verdict を表層値として受け取る parse 点は本設計時に `_parse_workflow()`（`workflow.py:115-357`）を全行走査して列挙しており、上記 6 箇所で網羅されている。`on` の**値**（遷移先）も step ID 参照だが、L2 に既存の isinstance ガード（`workflow.py:538`）があり raw `TypeError` を出さないため、Issue #357「スコープ外」の人間決定により対象外である（後述）。

型ガードが無いフィールドの値は無検証で `Step` / `CycleDefinition` に格納され、L2 に渡る。L2 は step ID / verdict が str である前提で書かれているため、前提が破れると内部実装が例外を出す:

- `Counter(step.id for step in workflow.steps)`（`workflow.py:420`）— unhashable id で `TypeError`
- `step_ids = {step.id for step in workflow.steps}`（同 523）— 同上
- `all_cycle_steps = {cycle.entry} | set(cycle.loop)`（同 596）— unhashable entry / loop 要素で `TypeError`
- `_is_valid_verdict(value)`（同 373-376）— `value in base_verdicts` が unhashable で `TypeError`、`back_suffix_pattern.match(value)` が非 str で `TypeError`。呼び出し元は `on` のキー（同 518）と `cycle.on_exhaust`（同 609）

hashable な不正値（`null` / `""` / `int` / `bool`）はこれらの演算を通過してしまう。L2 には step ID の**妥当性**検査が存在せず（#355 が追加したのは一意性検査のみ）、`find_step()` は「参照先が解決できるか」しか見ないため、`id: null` の step は「一意で、遷移先として解決可能」な step として validation を通過する。

**単一の根本原因**: parse 境界に step ID / verdict の型ガードが無いこと。露出点（`TypeError` 5 箇所 / silent accept 4 ケース / 誤誘導 1 ケース）は帰結が散っているだけで、原因も修正方法も同一である。

### いつから壊れているか

`kaji_harness/workflow.py` の初版は commit `a3baedc`（`refactor: rename dao → kaji ... for #73`）で、当時の `_STEP_REQUIRED_KEYS = ("id", "skill", "agent")` も**存在のみ**を検査していた（`git show a3baedc:kaji_harness/workflow.py:54` および同 94 の `id=step_data["id"]`）。id の型検証は **一度も存在したことがない**。

露出点は後から段階的に増えた:

- `all_cycle_steps = {cycle.entry} | set(cycle.loop)` — `a3baedc`（初版から存在）
- `_is_valid_verdict()`（`in` + `re.match`）— `b24ecc5`（`feat: split final-check BACK into BACK_DESIGN / BACK_IMPLEMENT for #158`）
- `step_ids = {step.id for step in workflow.steps}` — `f5c778d`（`feat: tighten workflow validation for #339`）
- `Counter(step.id for step in workflow.steps)` — `22b1cd2`（`fix: reject duplicate workflow step ids ... for #355`）

すなわち L2 の検査が厚くなるほど「id / verdict が str である」暗黙の前提への依存が増え、露出点が増加してきた。この構造は、前提を parse 境界で担保しない限り今後も同じ形で再発する。

### 同じ原因で他に壊れている箇所

**本 Issue のルール（step ID / verdict は文字列）の範囲内**では、参照点は上表の 6 箇所で網羅されており、同根の未修正箇所は残らない。`find_step()` を経由する参照解決系（遷移先 / resume / cycle entry / loop / tail）はすべて同じ str 前提を共有するが、これらは個別に壊れているのではなく型ガード欠落という単一原因の派生である。parse 境界で非 str を弾けば、下流は非文字列を考慮不要になる。

**L1 全体では、同型の失敗モードを持つ別フィールドが存在する**。本設計時に `_parse_workflow()` の全フィールドを再棚卸しし、各フィールドへ型不正値を与えて base commit `7597cbf` で実測した結果は次のとおり:

| フィールド | 実測結果 | 種別 |
|-----------|---------|------|
| `requires_provider` | `WorkflowValidationError: 'requires_provider' must be a string, got list` | ガードあり |
| `default_timeout` | `WorkflowValidationError: 'default_timeout' must be an integer, got list` | ガードあり |
| top-level `workdir` | `WorkflowValidationError: 'workdir' must be a string, got list` | ガードあり |
| step `agent` | `WorkflowValidationError: Step 'a' 'agent' must be a string or null, got list` | ガードあり |
| step `effort` | `WorkflowValidationError: Step 'a' 'effort' must be a string, got list` | ガードあり |
| step `timeout` | `WorkflowValidationError: Step 'a' 'timeout' must be an integer, got list` | ガードあり |
| step `workdir` | `WorkflowValidationError: Step 'a' 'workdir' must be a string, got list` | ガードあり |
| step `inject_verdict` | `WorkflowValidationError: Step 'a' 'inject_verdict' must be a boolean, got list` | ガードあり |
| step `exec` | `WorkflowValidationError: Step 'a' 'exec' list elements must be non-empty strings, got ['x']` | ガードあり |
| `execution_policy` | **`TypeError: unhashable type: 'list'`** | **ガードなし（本 Issue のスコープ外）** |
| top-level `name` | **ACCEPTED**（`name: [x]` を受理） | **ガードなし（同上）** |
| top-level `description` | **ACCEPTED** | **ガードなし（同上）** |
| step `skill` | **ACCEPTED**（`skill: [x]` を受理） | **ガードなし（同上）** |
| step `model` | **ACCEPTED** | **ガードなし（同上）** |
| step `max_budget_usd` | **ACCEPTED**（`max_budget_usd: "x"` を受理） | **ガードなし（同上）** |

これら 6 フィールドは `docs/dev/workflow-authoring.md:38-40, 189, 193, 195` と `kaji_harness/models.py:47-69, 84-95` に型契約（str / float）を持つが、L1 で担保されていない。失敗モード（raw `TypeError` / silent accept）は本 Issue と同型である。

ただし**本 Issue の対象ではない**。Issue #357「決定事項」3 が定めるルールは「step ID / verdict は文字列」であり、対象を step ID / verdict の参照点に限定している（人間決定）。`execution_policy` / `name` / `description` / `skill` / `model` / `max_budget_usd` はいずれも step ID でも verdict でもなく、ルールの適用対象外である。`_shared/report-unrelated-issues.md` の報告フローに従い既存 Issue を検索し（`gh issue list --search "execution_policy TypeError" / "workflow validation type" / "silent accept"`、該当なし）、follow-up Issue #360 を起票して追跡する（#355 → #357 と同じ分離方式）。

### スコープ外の隣接 gap（本 Issue では修正しない）

- **`on` の値（遷移先）の型検証**: `workflow.py:538` に既存の isinstance ガードがあり、非 str の遷移先は raw `TypeError` を出さず「遷移先が存在しない」と報告される。Issue #357「スコープ外」の人間決定に従い据え置く。
- **cycle 名の型検証**: 非 str でも crash せず error 報告される（同上）。
- **step `id` の書式制約（`[A-Za-z0-9-]+` 等）**: `docs/dev/workflow-authoring.md:188` の文書契約は「型 str」と「書式（英数字とハイフン）」の 2 要素からなるが、本 Issue は型のみを実装する。書式違反 id（例: `a_b`）は契約違反ではあるが crash も silent accept も起こさず原因が異なるため（Issue #357「スコープ外」の人間決定）。
- **手組み `Workflow` に対する `validate_workflow()` の raw `TypeError`**: 後述「制約・前提条件」に残余境界として記録する。
- **`execution_policy` / `name` / `description` / `skill` / `model` / `max_budget_usd` の型検証**: 前述「同じ原因で他に壊れている箇所」のとおり、L1 の型ガードを欠き同型の失敗モード（`execution_policy` は raw `TypeError`、他 5 件は silent accept）を持つが、step ID / verdict のいずれでもないため Issue #357 のルール適用対象外。follow-up Issue #360 で追跡する。

いずれも本設計時の実測で確認済みである。`#360` の対象は本設計の変更で改善も悪化もしない（`_parse_workflow()` 内の独立したフィールドであり、本設計が追加するガードはこれらの値に触れない）。

## インターフェース

既存 IF は維持する。公開シグネチャ・戻り値・例外型はいずれも変更しない。

### 入力

`_parse_workflow(data: dict[str, Any]) -> Workflow` の引数 `data`（`load_workflow()` / `load_workflow_from_str()` 経由の YAML data dict）。変更なし。

### 出力

| 入力 | 変更前 | 変更後 |
|------|--------|--------|
| step ID / verdict がすべて文字列 | `Workflow` を返す | `Workflow` を返す（不変） |
| `id` が非 str（`list` / `null` / `int` / `bool`） | raw `TypeError` または silent accept | `WorkflowValidationError` を送出 |
| `id` が空文字 | silent accept | `WorkflowValidationError` を送出 |
| `resume` が非 str / 空文字 | 誤誘導メッセージ / silent accept | `WorkflowValidationError` を送出 |
| `on` のキーが非 str | raw `TypeError` | `WorkflowValidationError` を送出 |
| `cycle.entry` / `loop` 要素が非 str | raw `TypeError` | `WorkflowValidationError` を送出 |
| `cycle.on_exhaust` が非 str | raw `TypeError` | `WorkflowValidationError` を送出 |

エラーメッセージ書式（既存 L1 の慣習に合わせる。`workflow.py:106-107, 179-187` の `'workdir' must be a string, got int` / `must not be empty` / `'exec' list elements must be non-empty strings, got {elem!r}` が前例）:

| ケース | メッセージ |
|--------|-----------|
| 1. `id: [a, b]` | `Step at index 0 'id' must be a string, got list` |
| 2. `cycle.entry: [a]` | `Cycle 'c' 'entry' must be a string, got list` |
| 3. `cycle.loop: [[a]]` | `Cycle 'c' 'loop' elements must be non-empty strings, got ['a']` |
| 4. `on_exhaust: [end]` | `Cycle 'c' 'on_exhaust' must be a string, got list` |
| 5. `on: {null: end}` | `Step 'a' 'on' keys must be strings, got NoneType` |
| 6. `id: null` | `Step at index 0 'id' must be a string, got NoneType` |
| 7. `id: ""` | `Step at index 0 'id' must not be empty` |
| 8. `resume: [x]` | `Step 'a' 'resume' must be a string, got list` |
| `id: 1` / `id: true` | `Step at index 0 'id' must be a string, got int` / `got bool` |

`id` のメッセージだけ `Step at index {i}` を主語にする。id 自体が不正な段階では `Step '<id>'` 形式で step を指せないためであり、既存の `Step at index {i} must be a mapping, got {type}`（`workflow.py:129-131`）と同じ主語規則に従う。

### 使用例

```python
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.workflow import load_workflow_from_str

yaml_str = """
name: broken
description: non-string step id
execution_policy: auto
steps:
  - id: [a, b]
    skill: issue-design
    agent: claude
    on:
      PASS: end
"""
try:
    load_workflow_from_str(yaml_str)
except WorkflowValidationError as e:
    print(e.errors)
    # ["Step at index 0 'id' must be a string, got list"]
```

```console
$ kaji validate ./broken.yaml; echo "exit=$?"
✗ broken.yaml
  - Step at index 0 'id' must be a string, got list
exit=1
```

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/workflow.py` | `_parse_workflow()` に step ID 系 / verdict 系の型ガードを追加（ヘルパー `_require_str` / `_require_non_empty_str` を新設） |
| `tests/test_workflow_parser.py` | 再現テスト（8 ケース + `id: 1` / `id: true`）と正常系の Small テストを追加 |
| `tests/test_cli_validate.py` | `kaji validate` 経由の型エラー検出 Medium テストを追加 |
| `docs/dev/workflow-authoring.md` | step `id` の検証範囲（型は強制 / 書式は未強制）を注記 |

`validate_workflow()`、`models.py`、`preflight.py`、`commands/validate.py` は変更しない。リファクタ（既存メッセージの `step_data['id']` → `sid` 統一等）は混在させない。

## 制約・前提条件

- **修正レイヤは L1（`_parse_workflow()`）のみ**: `validate_workflow()` 側に defense-in-depth mirror を追加しない（Issue #357「決定事項」1、人間決定）。
- **残余境界（意図的に残す）**: `validate_workflow()` は `_parse_workflow()` を経由せず手組みした `Workflow`（例: `Workflow(steps=[Step(id=["a"])])`）に対しては引き続き raw `TypeError` を送出しうる。既存の `timeout` / `workdir` / `exec` は L2 に mirror を持つ（`workflow.py:452-477`）ため、この点で `id` 系は非対称になる。Issue #357「決定事項」1 が「L2 側に寄せると 420 / 523 / 596 / 518 / 609 の各所にガードが要り、#355 で追加した error aggregation の構造を汚す」として明示的に L1 単独を選んでおり、本設計はこの人間決定に従う。YAML から読む正規経路（`load_workflow` / `load_workflow_from_str` → `kaji validate` / `kaji run` / `kaji recover` / series）はすべて L1 を通るため、Issue の完了条件は L1 単独で満たされる。
- **L1 は fail-fast（エラーを集約しない）**: `_parse_workflow()` の既存型検証はすべて検出時点で即座に単一メッセージの `WorkflowValidationError` を送出する（`workflow.py:118-236`）。本追加もこれに従う。エラー集約（`errors: list[str]` を末尾で 1 回 raise）は L2 = `validate_workflow()` の契約であり（`workflow.py:369, 612-613`）、L1 に持ち込まない。
- **既存 workflow を壊さない**: `.kaji/wf/*.yaml` を `yaml.safe_load()` で読み、本設計が対象とする 6 フィールド群（step `id` / `resume` / `on` のキー / `cycle.entry` / `cycle.loop` 要素 / `cycle.on_exhaust`）が本設計のガード条件（step ID 系は非空 str、verdict 系は str）を満たすかを全件走査で実測した。結果は **files=9 / steps=121 / violations=0**。したがって本変更は既存 workflow を 1 件も拒否しない。走査スクリプトは scratchpad に置き repo にはコミットしない（実装後は既存 `tests/test_cli_validate.py::test_repository_workflows_all_validate` が恒久回帰網となるため、設計時確認を恒久化する価値は低い）。
- **空文字の扱いを固定する既存テストが無い**: `entry: ""` / `loop: [""]` / `resume: ""` の現行メッセージを固定するテストは存在しない（本設計時に `grep -rn "entry step ''\|loop step ''\|resumes unknown step ''" tests/` で確認、ヒット 0 件）。したがって step ID 系への非空要求はテスト契約を破らない。
- **`bool` の特別扱いは不要**: `bool` は `int` のサブクラスだが `str` のサブクラスではない（[Python 組み込み関数 `bool`](https://docs.python.org/3/library/functions.html#bool)）。したがって `isinstance(value, str)` は `True` / `False` を自然に弾く。`timeout` / `max_iterations` の検査が必要とする `isinstance(x, bool)` 除外は、str 系ガードには不要である。
- Python >= 3.11（`pyproject.toml`）。追加依存なし。

## 方針

`_parse_workflow()` の直前に共通ヘルパーを 2 つ置き、各 parse 箇所から呼ぶ。フィールドごとにガードを書き下すと 6 箇所へ同型のコードが分散するため、`_normalize_exec()` と同じく「parse 境界で表層値を内部表現へ確定する関数」として切り出す。

```python
def _require_str(value: Any, label: str, context: str) -> str:
    """表層値が str であることを確定する（verdict 系: 値の妥当性は L2 が検査）。"""
    if not isinstance(value, str):
        raise WorkflowValidationError(
            f"{context} '{label}' must be a string, got {type(value).__name__}"
        )
    return value


def _require_non_empty_str(value: Any, label: str, context: str) -> str:
    """表層値が非空 str であることを確定する（step ID 系）。"""
    text = _require_str(value, label, context)
    if not text:
        raise WorkflowValidationError(f"{context} '{label}' must not be empty")
    return text
```

呼び出し箇所:

```python
# step ループ内（missing チェック直後、現 138 行の置換）
sid = _require_non_empty_str(step_data["id"], "id", f"Step at index {i}")

# on の取得・非空 dict 検査の直後（現 170 行の後）
for verdict_key in raw_on:
    if not isinstance(verdict_key, str):
        raise WorkflowValidationError(
            f"Step '{sid}' 'on' keys must be strings, got {type(verdict_key).__name__}"
        )

# resume（現 248 行の値を事前に確定）
raw_resume = step_data.get("resume")
if raw_resume is not None:
    raw_resume = _require_non_empty_str(raw_resume, "resume", f"Step '{sid}'")

# cycle ループ内（現 274-295 行）
raw_entry = _require_non_empty_str(cycle_data["entry"], "entry", f"Cycle '{cycle_name}'")
for elem in raw_loop:
    if not isinstance(elem, str) or not elem:
        raise WorkflowValidationError(
            f"Cycle '{cycle_name}' 'loop' elements must be non-empty strings, got {elem!r}"
        )
raw_on_exhaust = _require_str(cycle_data["on_exhaust"], "on_exhaust", f"Cycle '{cycle_name}'")
```

### 非空要求の適用範囲（step ID 系のみ）

| フィールド群 | L1 で要求する条件 | 根拠 |
|-------------|------------------|------|
| step ID 系（`id` / `resume` / `cycle.entry` / `cycle.loop` 要素） | **非空 str** | L2 に step ID の妥当性検査が無く、L1 が唯一のゲートだから。`id: ""` / `resume: ""` は現状 silent accept（実測） |
| verdict 系（`on` のキー / `cycle.on_exhaust`） | **str のみ**（非空は要求しない） | 値の妥当性は L2 の `_is_valid_verdict()` が既に正しく報告するから。実測: `on_exhaust: ""` → `Cycle 'c' on_exhaust '' is invalid`、`on: {"": ...}` → `Step 'a' has invalid verdict ''`。L1 で非空を重ねると L2 と二重検査になり、既存メッセージも変わる |

`cycle.entry: ""` / `loop: [""]` は現状 L2 が `entry step '' not found` と報告するため crash はしないが、「空文字の step を探して見つからない」という誤誘導メッセージになる（ケース 8 と同型の問題）。step ID 系として非空を L1 で要求し、フィールドを指すメッセージに統一する。

### `on` のキーを個別ガードにする理由

`_require_str()` のメッセージ主語は「フィールド自体」（`'on' must be a string`）だが、ここで不正なのは `on` mapping の**キー**である。`'exec' list elements must be non-empty strings`（`workflow.py:106-107`）と同じく、要素・キーには専用メッセージを使う。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 修正レイヤ | L1（`_parse_workflow()`）のみに型ガードを置き、`validate_workflow()` には追加しない | Issue #357「決定事項」1（人間決定）。層定義の一次情報: `docs/dev/workflow-authoring.md:85-87`。前例: `_normalize_exec()`（`workflow.py:78-112`） | 対象 6 箇所（`id` / `on` キー / `resume` / `entry` / `loop` 要素 / `on_exhaust`）を特定し、共通ヘルパー 2 関数へ集約する構成へ分解。L2 側の 5 露出点（420 / 523 / 596 / 518 / 609）を無改修と確定 |
| 空文字 `id` の拒否 | 拒否する | Issue #357「決定事項」2（人間決定）。前例の一次情報: `workflow.py:184-187`（`'workdir' must not be empty`）、同 103（`'exec' must not be an empty list`） | メッセージを既存 `must not be empty` 書式へ統一。主語は `Step at index {i}`（id 不正時は `Step '<id>'` で指せないため） |
| `resume` の型検証 | 含める | Issue #357「決定事項」3（人間決定。「step ID / verdict は文字列」という単一ルールを全参照点に適用しきる） | ケース 8 の誤誘導メッセージ（`resumes unknown step '['x']'`）を型エラーへ置換する構成へ具体化 |
| 非空要求の適用範囲 | step ID 系（`id` / `resume` / `entry` / `loop` 要素）は非空 str、verdict 系（`on` キー / `on_exhaust`）は str のみ | AI の仮定。根拠: 決定事項 2 が空文字拒否を明示するのは `id` のみ。verdict 系の空文字は L2 `_is_valid_verdict()` が既に error 報告する（本設計時の実測: `on_exhaust '' is invalid`）ため L1 で重ねると二重検査。一方 `resume: ''` は L2 が `if step.resume:` で falsy 判定し silently 無視する（実測）ため L1 が唯一のゲート。後段の検査先: `/issue-review-design`（適用範囲の妥当性）、`/issue-review-code`（テストが固定する挙動との整合） | 判断表（上記「非空要求の適用範囲」）としてフィールド群ごとの条件と根拠を明文化 |
| エラーメッセージ書式 | `{context} '{label}' must be a string, got {type}` / `must not be empty` / `'loop' elements must be non-empty strings, got {elem!r}` / `'on' keys must be strings, got {type}` | AI の仮定。根拠: 既存 L1 メッセージの慣習（`workflow.py:106-107, 129-131, 179-187`）。後段の検査先: `/issue-review-design`（書式の妥当性）、`/issue-review-code`（テストが固定する文字列との整合） | ケースごとの期待メッセージを IF 節の表に固定し、テストで assert する対象を確定 |
| L1 の fail-fast 維持 | 検出時点で単一メッセージの `WorkflowValidationError` を即時送出し、エラーを集約しない | AI の仮定。根拠: `_parse_workflow()` の既存型検証がすべて即時 raise（`workflow.py:118-236`）。集約は L2 の契約（同 369, 612-613、#355 設計書「制約・前提条件」）。決定事項 1 も「#355 の error aggregation の構造を汚さない」ことを L1 選択の理由に挙げている。後段の検査先: `/issue-review-code` | L1 追加分を既存の即時 raise 群と同じ構造に揃え、`errors` 蓄積経路へ合流させない方針として確定 |
| `validate_workflow()` の mirror 非追加による残余境界 | 手組み `Workflow` に対する raw `TypeError` は残す | Issue #357「決定事項」1（人間決定）から導かれる帰結。既存 mirror の一次情報: `workflow.py:452-477`（`timeout` / `workdir` / `exec` は mirror あり） | 残余境界と `id` 系の非対称性を「制約・前提条件」に明記し、YAML 正規経路では完了条件が満たされることを根拠づけ |
| スコープ外項目（`on` の値の型 / cycle 名の型 / `id` 書式 regex） | 本 Issue では扱わない | Issue #357「スコープ外」（人間決定）。`on` 値の既存ガード: `workflow.py:538` | 各項目が本設計の変更で改善も悪化もしないことを「根本原因 § スコープ外の隣接 gap」で確認・記録 |
| L1 の他フィールド（`execution_policy` / `name` / `description` / `skill` / `model` / `max_budget_usd`）の型ガード欠落 | 本 Issue では修正せず follow-up Issue #360 で追跡する | AI の仮定。根拠: Issue #357「決定事項」3 のルールは「step ID / verdict は文字列」であり、これらのフィールドはいずれの参照点でもなくルール適用対象外（人間決定によるスコープ限定）。`_shared/report-unrelated-issues.md` の報告フローに従い既存 Issue を検索（`execution_policy TypeError` / `workflow validation type` / `silent accept`）し該当なしを確認後、Issue #360 を起票した。#355 → #357 と同じ分離方式。後段の検査先: `/issue-review-design`（スコープ境界の妥当性） | 全 L1 フィールドの実測表を「根本原因 § 同じ原因で他に壊れている箇所」に記録し、本設計の変更がこれらを改善も悪化もさせないことを明示 |

## テスト戦略

### 変更タイプ

実行時コード変更（`_parse_workflow()` の検証ロジック追加）。

### bug 固有ルール: 再現テスト（Red → Green）

`tests/test_workflow_parser.py` に 8 ケースの再現テストを**実装より先に**追加し、修正前に Red となることを確認してから実装へ進む。Issue 本文には OB の実行結果があるが、escape clause には依らず実装前 Red 証跡を取得する（Small テストのため取得コストが低い）。

Red の内訳は失敗モードで異なり、両方を確認する:

- ケース 1〜5（raw `TypeError`）: `pytest.raises(WorkflowValidationError)` が `TypeError` の素通りで Red
- ケース 6・7（silent accept）: 同 context が `Failed: DID NOT RAISE` で Red

#### Small テスト

`tests/test_workflow_parser.py`（新規クラス `TestStepIdAndVerdictTypeGuards`）。外部依存なしの純粋 parse ロジックのため Small。既存の型検証テスト（`workdir` / `timeout` 等）も同ファイルにあり、配置が揃う。

検証観点:

- **raw `TypeError` の非露出**（ケース 1〜5、`on key: 1`）: 各不正入力に対し `WorkflowValidationError` が送出され、`TypeError` が外へ出ない（Issue 完了条件 1 項目目）
- **フィールドを指すメッセージ**（全ケース）: 送出された `WorkflowValidationError.errors` が該当フィールド（`id` / `resume` / `entry` / `loop` / `on_exhaust` / `on` keys）と検出した型名を含む（同 2 項目目）。IF 節の表のメッセージを固定する
- **silent accept の解消**（ケース 6・7、`id: 1` / `id: true`）: `null` / `""` / `int` / `bool` の id が `WorkflowValidationError` になる（同 3 項目目）
- **誤誘導の解消**（ケース 8）: `resume: [x]` が `resumes unknown step` ではなく型エラーとして報告される
- **非空要求の適用範囲**: `resume: ""` / `entry: ""` / `loop: [""]` が `must not be empty` で拒否される一方、verdict 系（`on_exhaust: ""` / `on` キー `""`）は L2 の既存メッセージ（`on_exhaust '' is invalid` / `has invalid verdict ''`）で報告される（設計の「非空要求の適用範囲」判断を固定する）
- **正常系**: 文字列 `id` / `resume` / cycle 参照を持つ workflow が従来どおり parse され、`Step.id` / `CycleDefinition.entry` / `loop` / `on_exhaust` の値が不変（既存の `MINIMAL_WORKFLOW_YAML` / `FULL_WORKFLOW_YAML` を用いた既存テスト群が回帰網を兼ねる）

#### Medium テスト

`tests/test_cli_validate.py`（既存の `_cmd_validate_with_args` 経路を使用）。ファイル I/O + CLI 経路の結合のため Medium。

- **`kaji validate` 経由の型エラー検出**: 非 str `id` の YAML を `tmp_path` へ書き出し、exit code 1 と stderr の該当メッセージを固定する（Issue 完了条件 4 項目目）。既存 `test_duplicate_step_id_via_cli`（#355）と同じ構造を踏襲する。L1 エラーは config / skill 検査（L3）より先に確定するため `_create_skill` / `_create_config` は呼ばない
- **リポジトリ管理 workflow の正常系**: 既存 `test_repository_workflows_all_validate` が `.kaji/wf/*.yaml` の回帰網となるため、新規追加は不要

#### Large テスト

不要。`docs/dev/testing-convention.md` の 4 条件に照らした根拠:

1. 変更は `_parse_workflow()` 内の型検証のみで、外部 API / ネットワーク / subprocess を経由しない
2. 想定される不具合パターン（不正 workflow の受理・raw 例外の露出）は上記 Small / Medium と既存の `make check` で捕捉できる
3. E2E 経路（`kaji run`）を追加しても、workflow 定義の parse 結果以上の回帰シグナルが増えない（`kaji run` も同じ L1 を通る）
4. 省略理由が「実行時間」「環境不備」ではなく、変更の到達範囲が L1 に閉じることに基づく

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規の技術選定を含まない。層分離（L1/L2）の既存方針に従う変更 |
| docs/ARCHITECTURE.md | なし | モジュール構成・依存方向は不変 |
| docs/dev/workflow-authoring.md | **あり** | `:188` の step `id` 行は「型 str / 英数字とハイフン」を規定するが、本変更で強制されるのは**型（非空 str）のみ**で書式は未強制のまま。読者が書式まで validation されると誤解しないよう、検証範囲を注記する。L1/L2 の層定義表（`:85-87`）自体は「型、必須、排他、許容値を検証」で既に正確なため変更不要 |
| docs/dev/ その他 | なし | ワークフロー・開発手順の変更なし |
| docs/reference/python/ | なし | 規約変更なし |
| docs/cli-guides/ | なし | `kaji validate` の CLI 仕様（exit code / 出力書式）は不変。診断メッセージの追加のみ |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #357 本文「決定事項」 | https://github.com/apokamo/kaji/issues/357 | 1: 「修正レイヤ = parse 時（`_parse_workflow()`）」「parse 境界で弾けば下流は非文字列を考慮不要になる」。2: 「空文字 id は拒否する」。3: 「`resume` の型検証を含める」。4: 「`null` / `int` / `bool` の silent accept は `isinstance(str)` チェックの帰結として併せて解消される」 |
| Issue #357 本文「スコープ外」 | 同上 | 「`on` の値（遷移先）の型検証」「cycle 名の型検証」「step ID の書式制約（regex 等）」を意図的に含めない旨と各根拠 |
| workflow 検証層の定義 | `docs/dev/workflow-authoring.md:85-87` | 「L1: YAML parse/schema \| `kaji_harness/workflow.py` \| YAML parse、型、必須、排他、許容値を検証」「L2: workflow 参照整合 \| step ID の一意性、step/cycle の遷移先、resume、verdict、到達可能性など workflow 内の参照を検証」。型検証は L1 の責務であることの根拠 |
| step フィールド契約 | `docs/dev/workflow-authoring.md:188` | 「`id` \| str \| ✅ \| ステップ ID。英数字とハイフン。workflow 内で一意でなければならない」。EB（id は str）の文書上の裏付けであり、書式は今回スコープ外 |
| 棚卸しから漏れていたフィールドの型契約 | `docs/dev/workflow-authoring.md:38-40, 189, 193, 195` | 38-40: 「`name:` 必須: ワークフロー名 / `description:` 必須: 説明 / `execution_policy:` 必須: auto / sandbox / interactive」。189/193/195: 「`skill` \| str」「`model` \| str」「`max_budget_usd` \| float」。これらが型契約を持つが L1 で未担保であることの根拠（follow-up Issue #360） |
| top-level の内部型契約 | `kaji_harness/models.py:84-95`（`Workflow`） | `name: str` / `description: str` / `execution_policy: str`。同上 |
| 内部データ契約 | `kaji_harness/models.py:47`（`Step`）, `:73`（`CycleDefinition`） | `id: str` / `resume: str \| None` / `on: dict[str, str]` / `entry: str` / `loop: list[str]` / `on_exhaust: str`。parse 境界で担保すべき型の宣言 |
| parse 境界正規化の前例 | `kaji_harness/workflow.py:78-112`（`_normalize_exec`） | 「parse 境界で argv に正規化することで、runner / 各 consumer が str と list の二形態を毎回分岐せずに済む」。本設計のヘルパー切り出しが従う既存思想 |
| 既存 L1 型検証とメッセージ書式 | `kaji_harness/workflow.py:129-131, 171-236` | `Step at index {i} must be a mapping, got {type}` / `Step '<id>' 'workdir' must be a string, got {type}` / `must not be empty`。主語規則とメッセージ書式の前例 |
| L2 のエラー集約契約 | `kaji_harness/workflow.py:369, 612-613`; `kaji_harness/errors.py:55-65` | `errors: list[str]` を蓄積し末尾で 1 回 `WorkflowValidationError` を raise。L1 の fail-fast と対比される契約 |
| L2 の露出点 | `kaji_harness/workflow.py:420, 523, 596, 373-376`（`_is_valid_verdict`） | `Counter(step.id ...)` / `{step.id ...}` / `{cycle.entry} | set(cycle.loop)` / `value in base_verdicts` + `back_suffix_pattern.match(value)`。raw `TypeError` の発生源 |
| `kaji validate` の CLI 経路 | `kaji_harness/commands/validate.py:51`（`cmd_validate`） | `WorkflowValidationError` を stderr へ整形出力し exit code 1 を返す既存経路。CLI 側無改修の根拠 |
| bool と str の関係 | https://docs.python.org/3/library/functions.html#bool | `bool` は `int` のサブクラス。`str` のサブクラスではないため `isinstance(value, str)` は `True` を弾く。`timeout` 検査が持つ `isinstance(x, bool)` 除外が str 系では不要である根拠 |
| 隣接 gap の記録元 | `draft/design/issue-355-bug-workflow-step-id-validation.md` §「スコープ外の隣接 gap」 | #355 設計時に非 str id の raw `TypeError`（`unhashable type: 'list'`）を観測し、`_STEP_REQUIRED_KEYS` が型を検査しないことに起因する別欠陥として本 Issue #357 へ分離した経緯 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L のサイズ判定基準（「それ以外（純粋関数・モック完結）→ Small」「ファイル I/O ... → Medium」）と、恒久テストを追加しない理由の 4 条件 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | 「修正前に Red になる再現テスト（regression test）を必ず 1 本以上定義する。省略不可」。OB/EB/再現手順の分離要求 |
