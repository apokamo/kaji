# [設計] ワークフローYAMLでworkdirを指定可能にする

Issue: #127

## 概要

ワークフローYAMLにworkdirフィールドを追加し、ステップ実行時のカレントディレクトリをワークフローレベル・ステップレベルで指定可能にする。

## 背景・目的

現状、全ステップの作業ディレクトリは `config.toml` discovery で決まる `project_root` 固定。MCP サーバー設定など特定ディレクトリのプロジェクトローカル設定に依存するケースでは、そのディレクトリを cwd として起動する必要がある。CLI `--workdir` はあるが config discovery の起点を変えるだけで、ステップごとの切り替えはできない。

## インターフェース

### 入力

#### ワークフローYAML（ワークフローレベル、任意）

```yaml
name: my-workflow
workdir: /home/user/project/apps/web  # 全ステップのデフォルト
steps:
  - id: step1
    ...
```

#### ワークフローYAML（ステップレベル、任意）

```yaml
steps:
  - id: step1
    workdir: /home/user/project/apps/web  # このステップだけ
    ...
```

### 出力

`execute_cli()` に渡される `workdir` 引数が、指定があれば上書きされる。`subprocess.Popen(cwd=workdir)` でプロセスが起動される。

### フォールバック階層

```
CLI --workdir(config discovery) → step.workdir → workflow.workdir → project_root
  (config discoveryのみ)          (最優先)        (中間)            (最終)
```

**注意**: CLI `--workdir` は config discovery の起点であり、YAML workdir とは役割が異なる。CLI `--workdir` で発見された `project_root` がフォールバックの最終値となる。

### 使用例

```python
# runner.py 内での workdir 解決
effective_workdir = resolve_workdir(step, workflow, self.project_root)
# → step.workdir > workflow.workdir > self.project_root
```

```yaml
# ワークフローYAML例: MCP サーバー設定のあるディレクトリを指定
name: cross-project
workdir: /home/user/dev/other-project
steps:
  - id: design
    skill: issue-design
    agent: claude
    workdir: /home/user/dev/other-project/apps/web  # ステップ単位で上書き
    on:
      PASS: implement
```

## 制約・前提条件

- workdir は文字列型（パス）。`~` のチルダ展開をサポートする
- workdir が指定された場合、そのディレクトリが存在することをバリデーションする（パース時は型チェックのみ、実行時に存在確認）
- 空文字列は不正値としてバリデーションエラー
- 相対パスは許可しない（絶対パスのみ）。ワークフローYAMLの可搬性の問題はあるが、cwd起点の相対パス解決は曖昧さが大きいため、初回リリースでは絶対パスに限定する
- CLI `--workdir` の既存動作（config discovery の起点指定）は変更しない

## 方針

### 1. モデル変更（models.py）

`Step` と `Workflow` dataclass に `workdir` フィールドを追加する。

```python
@dataclass
class Step:
    # ... 既存フィールド ...
    workdir: str | None = None

@dataclass
class Workflow:
    # ... 既存フィールド ...
    workdir: str | None = None
```

### 2. パーサー変更（workflow.py）

`_parse_workflow()` で workdir を読み取り、型バリデーション（文字列であること、空でないこと）を行う。ステップレベル・ワークフローレベル両方。

### 3. ランナー変更（runner.py）

`execute_cli()` 呼び出し時に workdir を解決する。

```python
# 疑似コード
step_workdir = current_step.workdir or self.workflow.workdir
effective_workdir = Path(step_workdir) if step_workdir else self.project_root
```

実行時にディレクトリ存在確認を行い、存在しなければ明確なエラーメッセージで失敗させる。

### 4. 影響範囲

- `cli.py`: 変更不要。`execute_cli(workdir=...)` の引数が変わるだけ
- 各アダプタの `_build_*_args()`: 変更不要。`workdir` パラメータは既に受け取っており、Codex は `-C` フラグにも使用している
- `cli_main.py`: 変更不要。CLI `--workdir` の動作は維持

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 実行時コード変更では Small / Medium / Large の観点を定義し、
> docs-only / metadata-only / packaging-only 変更では変更固有検証と
> 恒久テストを追加しない理由を明記する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### 変更タイプ
- 実行時コード変更（モデル・パーサー・ランナーの変更）

### Small テスト

- **workdir フィールドのパース**: ワークフローレベル・ステップレベルの workdir が正しくパースされること
- **バリデーション**: 空文字列、非文字列型、相対パスでエラーになること
- **workdir 未指定時**: None としてパースされること（後方互換）
- **フォールバック解決**: step.workdir > workflow.workdir > project_root の優先順位が正しいこと

### Medium テスト

- **実行時ディレクトリ存在確認**: 存在しないパスが指定された場合に適切なエラーが発生すること
- **ランナー統合**: workdir 指定ありのワークフローで `execute_cli()` に正しい workdir が渡されること（subprocess のモックで確認）

### Large テスト

- 既存の Large テストで workdir 未指定のケースはカバー済み
- workdir 指定ありの E2E テストは、実際の外部ディレクトリ依存が必要なため、初回リリースでは追加しない。手動検証で代替する

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更はない |
| docs/dev/ | あり | workflow-authoring.md にworkdirフィールドの説明を追加 |
| docs/cli-guides/ | あり | CLI --workdir との関係を明記 |
| CLAUDE.md | なし | 規約変更はない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 runner.py の workdir 伝搬 | `kaji_harness/runner.py:144` | `execute_cli(workdir=self.project_root)` で全ステップ共通の project_root を使用 |
| 現行 cli.py の subprocess 起動 | `kaji_harness/cli.py:117-119` | `subprocess.Popen(..., cwd=workdir)` で workdir を cwd として設定 |
| 現行 CLI --workdir オプション | `kaji_harness/cli_main.py:52-57` | config discovery の起点として使用。`start_dir = args.workdir.resolve()` |
| Issue #127 調査結果 | GitHub Issue #127 本文 | 3アダプタの workdir 扱い調査。全アダプタが `cwd=workdir` で動作し、Codex は追加で `-C` フラグも使用 |
| timeout 設計（同パターンの先行実装） | `draft/design/issue-116-timeout-config.md` | ワークフローレベル → ステップレベルのフォールバック階層設計パターン |
