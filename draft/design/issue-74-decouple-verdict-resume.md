# [設計] resume と previous_verdict の結合を解消する

Issue: #74

## 概要

`previous_verdict` の注入条件を `resume`（セッション継続）から分離し、ワークフローYAMLで独立に制御できるようにする。併せて、テストが本番YAMLに依存している構造を解消する。

## 背景・目的

現在 `previous_verdict` は `step.resume` が設定されている場合のみ注入される（`prompt.py:36`）。
これにより mixed-agent 構成（例: review を codex、fix を claude で実行）では、fix ステップが review のセッションを resume できないため `previous_verdict` も受け取れない。

現状の `feature-development.yaml` では `fix-code` から `resume` を外すことで `MissingResumeSessionError` を回避しているが、結果として review の verdict 情報が fix に渡らない。

また、`test_skill_harness_adaptation.py` が `workflows/feature-development.yaml` を直接参照しており、本番ワークフローの構造変更でテストが壊れる。

## インターフェース

### 入力

#### Step モデルの変更

```python
@dataclass
class Step:
    # ... 既存フィールド ...
    resume: str | None = None
    inject_verdict: bool = False  # 新規追加
```

#### ワークフローYAMLの変更

```yaml
- id: fix-code
  skill: issue-fix-code
  agent: claude
  model: opus
  inject_verdict: true    # resume なしでも verdict を受け取る
  on:
    PASS: verify-code
    ABORT: end
```

### 出力

`build_prompt` が生成するプロンプトに `previous_verdict` 変数が含まれる条件:

| 現在 | 変更後 |
|------|--------|
| `step.resume and state.last_transition_verdict` | `(step.resume or step.inject_verdict) and state.last_transition_verdict` |

### 使用例

```yaml
# mixed-agent: review(codex) → fix(claude)
# resume 不可だが verdict は引き継ぎたい
- id: fix-code
  skill: issue-fix-code
  agent: claude
  inject_verdict: true
  on:
    PASS: verify-code

# same-agent: design(claude) → fix-design(claude)
# セッション継続 + verdict 引き継ぎ
- id: fix-design
  skill: issue-fix-design
  agent: claude
  resume: design           # resume があれば inject_verdict は暗黙 true
  on:
    PASS: verify-design
```

## 制約・前提条件

- `resume` が設定されている場合は `inject_verdict` を明示しなくても verdict が注入される（後方互換性）
- `inject_verdict` は `resume` と独立に設定可能
- ワークフローYAMLのパース（`workflow.py`）で `inject_verdict` フィールドを認識する必要がある
- 既存のワークフローYAMLは変更なしで動作する（`inject_verdict` のデフォルトは `false`）
- `inject_verdict` のYAMLパース時に型検証を行う: `bool` 以外の値（文字列・数値等）は `WorkflowValidationError` とする

## 方針

### 1. Step モデルに `inject_verdict: bool` を追加

`models.py` の `Step` に `inject_verdict: bool = False` を追加。
`workflow.py` のパーサーで YAML から読み取り。型検証を追加:

```python
# workflow.py _parse_workflow() 内
raw_inject_verdict = step_data.get("inject_verdict", False)
if not isinstance(raw_inject_verdict, bool):
    raise WorkflowValidationError(
        f"Step '{step_data['id']}' 'inject_verdict' must be a boolean, "
        f"got {type(raw_inject_verdict).__name__}"
    )
```

### 2. `build_prompt` の注入条件を変更

```python
# 変更前
if step.resume and state.last_transition_verdict:

# 変更後
if (step.resume or step.inject_verdict) and state.last_transition_verdict:
```

### 3. `feature-development.yaml` の更新

`fix-code` に `inject_verdict: true` を追加。

### 4. テスト用YAML分離

`test_skill_harness_adaptation.py` が `workflows/feature-development.yaml` を直接参照している構造を解消する。

- エンジンロジックのテスト → `tests/fixtures/` にミニマルなfixture YAMLを配置
- 本番YAMLのバリデーション → `kaji validate` コマンドに委ねる（pytestでの二重検証は不要）

対象テストクラスの分離方針:

| テストクラス | 方針 |
|-------------|------|
| `TestWorkflowYamlParseable` | fixture YAML に切り替え |
| `TestWorkflowValidation` | fixture YAML に切り替え |
| `TestWorkflowSkillsExist` | 削除。ただし skill 存在確認を `cmd_validate` に追加（後述 4a） |
| `TestWorkflowResumeConfig` | 削除・再設計（`inject_verdict` テストに置換） |
| `TestSkillVerdictParseable` | fixture YAML に切り替え |
| `TestWorkflowTransitions` | fixture YAML に切り替え |

### 4a. `kaji validate` に skill 存在確認を追加

現行の `cmd_validate()` は `load_workflow()` + `validate_workflow()` のみで、workflow 内の `skill` フィールドが実際にファイルシステム上に存在するかは検証していない。`TestWorkflowSkillsExist` を削除するにあたり、この責務を `cmd_validate` に移管する。

```python
# cli_main.py cmd_validate() 内
wf = load_workflow(path)
validate_workflow(wf)
# 追加: skill 存在確認
for step in wf.steps:
    validate_skill_exists(step.skill, step.agent, path.parent)
```

`validate_skill_exists` は既存の `kaji_harness/skill.py` にあり、`WorkflowRunner.run()` でも使用されている。`cmd_validate` にも同じ検証を追加することで、`kaji validate` 単体で skill 欠落を検出できるようになる。

**workdir の扱い**: `cmd_validate` は `--workdir` オプションを持たないため、YAML ファイルの親ディレクトリをプロジェクトルートとして使用する。通常 `workflows/` はプロジェクトルート直下にあるため `path.parent` で十分だが、より正確には `path.parent` から `pyproject.toml` を探索してプロジェクトルートを特定する方法もある。初回実装では `path.parent` を使用し、不足があれば後続で対応する。

### 5. ドキュメント更新

- `docs/dev/workflow-authoring.md`: `inject_verdict` フィールドの説明を追加
- `docs/dev/skill-authoring.md`: `previous_verdict` 注入条件の記述を更新

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- `build_prompt` で `inject_verdict=True, resume=None` の場合に `previous_verdict` が注入されること
- `build_prompt` で `inject_verdict=False, resume=None` の場合に `previous_verdict` が注入されないこと
- `build_prompt` で `resume` 設定時は `inject_verdict` の値に関わらず verdict が注入されること（後方互換）
- `Step` モデルに `inject_verdict` フィールドが存在し、デフォルト `False` であること

### Medium テスト

- fixture YAML からワークフローをロードし、`inject_verdict` フィールドが正しくパースされること
- fixture YAML のバリデーション（cycle integrity, transitions）がエンジンロジックとして正しく動作すること
- SKILL.md の verdict example が fixture YAML のステップ定義に基づいてパースできること

### Large テスト

`kaji` CLI は `pyproject.toml` に `kaji = "kaji_harness.cli_main:main"` として登録済みであり、`tests/test_cli_validate.py` に既存の Large テストがある。

- `cmd_validate` に skill 存在確認を追加した後、既存の `test_cli_validate.py` の Large テストで `validate_skill_exists` 統合が検証されることを確認する
- 必要に応じて `test_cli_validate.py` に skill 欠落時のエラーケースを追加する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/workflow-authoring.md | あり | `inject_verdict` フィールドの追加 |
| docs/dev/skill-authoring.md | あり | `previous_verdict` 注入条件の変更 |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行の注入ロジック | `kaji_harness/prompt.py:35-40` | `if step.resume and state.last_transition_verdict:` — resume に結合 |
| Step モデル定義 | `kaji_harness/models.py:38-50` | `resume: str \| None = None` — inject_verdict フィールドなし |
| 本番ワークフロー | `workflows/feature-development.yaml:79-85` | `fix-code` に resume なし、verdict 注入されない |
| テストの本番YAML依存 | `tests/test_skill_harness_adaptation.py:48` | `WORKFLOW_YAML_PATH = PROJECT_ROOT / "workflows" / "feature-development.yaml"` |
| workflow-authoring ドキュメント | `docs/dev/workflow-authoring.md:89-109` | resume セクション — inject_verdict 未記載 |
| CLI エントリポイント | `pyproject.toml:33-34` | `kaji = "kaji_harness.cli_main:main"` — 実装済み |
| cmd_validate 実装 | `kaji_harness/cli_main.py:64-91` | `load_workflow()` + `validate_workflow()` のみ、skill 存在確認なし |
| validate の既存テスト | `tests/test_cli_validate.py` | S/M/L 全サイズのテストが実装済み |
| validate_skill_exists | `kaji_harness/skill.py` | `WorkflowRunner.run()` で使用されている skill 存在確認関数 |
