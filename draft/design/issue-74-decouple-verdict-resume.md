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

## 方針

### 1. Step モデルに `inject_verdict: bool` を追加

`models.py` の `Step` に `inject_verdict: bool = False` を追加。
`workflow.py` のパーサーで YAML から読み取り。

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
| `TestWorkflowSkillsExist` | 削除（`kaji validate` で代替） |
| `TestWorkflowResumeConfig` | 削除・再設計（`inject_verdict` テストに置換） |
| `TestSkillVerdictParseable` | fixture YAML に切り替え |
| `TestWorkflowTransitions` | fixture YAML に切り替え |

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

- `kaji validate` コマンドで `workflows/feature-development.yaml` がエラーなく通ること

### スキップするサイズ（該当する場合のみ）

- Large: `kaji` CLI エントリポイントが未実装（pyproject.toml で `[project.scripts]` がコメントアウト）のため、`kaji validate` の統合テストは物理的に作成不可。手動実行で検証する。

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
