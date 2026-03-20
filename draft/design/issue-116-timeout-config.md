# [設計] タイムアウトのハードコード削除と設定ファイル化

Issue: #116

## 概要

`kaji_harness/cli.py` の `DEFAULT_TIMEOUT = 1800` を削除し、config.toml → workflow YAML → step YAML の3層フォールバックでタイムアウトを解決する。

## 背景・目的

現状、タイムアウトは `cli.py:19` にハードコードされており、ユーザーが変更できない。ワークフロー定義やプロジェクト設定からタイムアウトを制御する手段がなく、軽いステップも重いステップも一律 1800s になる。これを設定ファイルベースの階層的フォールバックに変更し、タイムアウトの所在を明確にする。

## インターフェース

### 入力

#### `.kaji/config.toml`（必須）

```toml
[execution]
default_timeout = 1800  # 秒。必須。未設定は ConfigLoadError
```

#### ワークフロー YAML（任意）

```yaml
name: feature-development
default_timeout: 600  # ワークフロー全体のデフォルト
```

#### ステップ YAML（任意）

```yaml
steps:
  - id: implement
    timeout: 3600  # ステップ個別
```

### 出力

`execute_cli()` 内で使用されるタイムアウト値（秒）。

### フォールバック階層

```
step.timeout → workflow.default_timeout → config.execution.default_timeout
  (最優先)          (中間)                     (最終・必須)
```

### 使用例

```python
# execute_cli 内でのタイムアウト解決
timeout = resolve_timeout(step, workflow, config)
```

## 制約・前提条件

- `config.toml` の `[execution] default_timeout` は必須。未設定時は `ConfigLoadError` を送出
- すべての timeout 値（`config.execution.default_timeout`, `workflow.default_timeout`, `step.timeout`）は正の整数（秒単位）。0以下・非整数・bool 型はバリデーションエラー
- `step.timeout` の型は `int | None` のまま変更しないが、`_parse_workflow()` と `validate_workflow()` の双方でバリデーションを追加する
- `Workflow` データクラスに `default_timeout: int | None = None` フィールドを追加
- `KajiConfig` に `ExecutionConfig` データクラスを追加
- `execute_cli()` のシグネチャに `default_timeout: int` パラメータを追加
- **timeout: 0 の扱い**: `step.timeout or default_timeout` のような truthy 判定は使用しない。`step.timeout is not None` で明示的に None チェックする（0 が silent fallback されることを防止）

## 方針

### 1. `KajiConfig` の拡張

`config.py` に `ExecutionConfig` データクラスを追加し、`KajiConfig` に持たせる。

```python
@dataclass(frozen=True)
class ExecutionConfig:
    default_timeout: int  # 必須。デフォルト値なし

@dataclass(frozen=True)
class KajiConfig:
    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig
```

`_load()` 内で `[execution]` セクションの存在と `default_timeout` の値を検証する。未設定・型不正・0以下は `ConfigLoadError`。

### 2. `Workflow` モデルの拡張

`models.py` の `Workflow` に `default_timeout: int | None = None` を追加。

**パーサ (`_parse_workflow()`)**: `data.get("default_timeout")` をパースし、型（int かつ非 bool）と値（正の整数）を検証する。`step.timeout` についても同様のバリデーションを追加する。

**バリデータ (`validate_workflow()`)**: パーサを経由せず直接構築された `Workflow(...)` / `Step(...)` にも同等のバリデーションを適用する。これは `validate_workflow()` が直接構築モデルも守る既存契約（`tests/test_workflow_validator.py` で確認済み）を維持するため。具体的には:
- `workflow.default_timeout` が設定されている場合: 正の整数であること
- 各 `step.timeout` が設定されている場合: 正の整数であること

### 3. `execute_cli()` の変更

- `DEFAULT_TIMEOUT` 定数を削除
- `execute_cli()` に `default_timeout: int` パラメータを追加
- タイムアウト解決: `step.timeout if step.timeout is not None else default_timeout`（truthy 判定ではなく明示的 None チェック）

### 4. `WorkflowRunner` の変更

- `WorkflowRunner` に `config: KajiConfig` を持たせる（既に `artifacts_dir` 経由で config を使用しているため、config 自体を渡す形に変更）
- `execute_cli()` 呼び出し時に `default_timeout` を算出して渡す:
  `workflow.default_timeout if workflow.default_timeout is not None else config.execution.default_timeout`

### 5. `cli_main.py` の変更

- `cmd_run()` で `config` オブジェクトを `WorkflowRunner` に渡す

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

- **ExecutionConfig のバリデーション**: `default_timeout` 未設定・型不正（文字列, bool, float）・0以下のケースで `ConfigLoadError`
- **KajiConfig._load() の [execution] パース**: 正常値・`[execution]` セクション欠損・`default_timeout` キー欠損・不正値の各ケース
- **Workflow.default_timeout パース（`_parse_workflow()`）**: 正常値・None（省略）・型不正（文字列, bool）・0以下のケース
- **step.timeout パース（`_parse_workflow()`）**: 型不正（文字列, bool, float）・0以下のケースでエラー
- **validate_workflow() での timeout バリデーション**: 直接構築した `Workflow(default_timeout=0)` や `Step(timeout=-1)` が `WorkflowValidationError` になること
- **タイムアウト解決ロジック**: `step.timeout is not None` の明示的 None チェック。step.timeout=0 がフォールバックされないこと（バリデーション通過後の安全性確認）
- **フォールバック組み合わせ**: step.timeout あり/なし × workflow.default_timeout あり/なし の組み合わせ
- **既存テストの修正**: `DEFAULT_TIMEOUT` を参照しているテストの更新

### Medium テスト

- **config.toml → execute_cli のフォールバック結合**: 実ファイルの config.toml を読み込み、`execute_cli()` に渡されるタイムアウト値が正しいことを検証（プロセス起動はモック）
- **WorkflowRunner 結合**: config + workflow + step の3層フォールバックが `WorkflowRunner.run()` 経由で正しく解決されることを検証（CLI 実行はモック）
- **config.toml 未設定時のエラーパス**: `[execution]` セクションなしの config.toml でワークフロー実行するとエラー終了すること

### Large テスト

- **CLI E2E**: 実際の `kaji run` コマンドで config.toml の `default_timeout` が反映されることを検証（既存の E2E テストフレームワークに準拠。実 agent 呼び出しはスコープ外）
- **kaji validate E2E**: `default_timeout` を含むワークフロー YAML が正常にバリデーションを通過すること

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | あり | `config.py` の説明（L58）に `ExecutionConfig` の追加を反映。エラー階層（L238-244）に config 関連エラーの更新が必要な場合あり |
| docs/dev/workflow-authoring.md | あり | `timeout` フィールドのデフォルト値・フォールバック仕様・`default_timeout` ワークフローフィールドの追記が必要 |
| README.md | あり | `.kaji/config.toml` の最小構成例（L46-52）に `[execution] default_timeout` が必須項目として追加されるため更新が必要 |
| docs/cli-guides/ | なし | CLI インターフェースの変更はない |
| CLAUDE.md | なし | 規約変更はない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 cli.py | `kaji_harness/cli.py:19,58` | `DEFAULT_TIMEOUT = 1800` がハードコード。`timeout = step.timeout or DEFAULT_TIMEOUT` で解決 |
| 現行 config.py | `kaji_harness/config.py` | `KajiConfig` は `repo_root` + `PathsConfig` のみ。`[execution]` セクションは未定義 |
| 現行 models.py | `kaji_harness/models.py:66-73` | `Workflow` に `default_timeout` フィールドなし |
| 現行 workflow.py | `kaji_harness/workflow.py:166-172` | `_parse_workflow()` は `default_timeout` をパースしていない |
| validate_workflow() 契約 | `kaji_harness/workflow.py:175-308`, `tests/test_workflow_validator.py` | `validate_workflow()` は直接構築の `Workflow(...)` / `Step(...)` にも適用される。テストで `Step(...)` を直接構築して検証している |
| ワークフロー定義マニュアル | `docs/dev/workflow-authoring.md:58` | `timeout` フィールドは記載あるがデフォルト値の説明なし |
| README.md | `README.md:46-52` | `.kaji/config.toml` 最小構成例に `[paths]` のみ記載。`[execution]` 必須化で更新が必要 |
| docs/ARCHITECTURE.md | `docs/ARCHITECTURE.md:58,201-203` | `config.py` の説明と `artifacts_dir` の config.toml 参照あり |
| Python tomllib | https://docs.python.org/3/library/tomllib.html | TOML パースに使用。kaji は Python 3.11+ で `tomllib` を標準使用 |
| TOML 仕様 | https://toml.io/en/v1.0.0 | `[execution]` テーブルと `default_timeout` キーは TOML v1.0.0 準拠 |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
