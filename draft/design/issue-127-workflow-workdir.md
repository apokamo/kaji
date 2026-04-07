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

### workdir の役割分離

| パラメータ | 役割 | 決定タイミング | 影響先 |
|-----------|------|---------------|--------|
| CLI `--workdir` | config discovery の起点 | CLI 起動時 | `.kaji/config.toml` の探索開始ディレクトリ |
| YAML `workflow.workdir` | ステップ実行 cwd のデフォルト | YAML パース時 | `subprocess.Popen(cwd=...)` |
| YAML `step.workdir` | ステップ実行 cwd の個別指定 | YAML パース時 | `subprocess.Popen(cwd=...)` |
| `project_root` | config discovery の結果 | config 読み込み時 | YAML workdir 未指定時のフォールバック |

CLI `--workdir` と YAML `workdir` は異なるフェーズで異なるものを決定する。CLI `--workdir` は「どの config を使うか」、YAML `workdir` は「どのディレクトリでエージェントを動かすか」を制御する。

### 実行 cwd のフォールバック階層

```
step.workdir → workflow.workdir → project_root
  (最優先)        (中間)            (最終・config discovery結果)
```

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

### 2. パーサー変更（workflow.py `_parse_workflow()`）

workdir を読み取り、以下のバリデーションを行う（ステップレベル・ワークフローレベル共通）:

1. **型チェック**: 文字列であること（非文字列は `WorkflowValidationError`）
2. **空文字列拒否**: 空文字列は `WorkflowValidationError`
3. **`~` 展開**: `Path(workdir).expanduser()` で正規化
4. **絶対パス判定**: 展開後のパスが絶対パスであること（相対パスは `WorkflowValidationError`）
5. **正規化結果の格納**: 展開・検証後の絶対パス文字列をモデルに格納

```python
# 疑似コード (_parse_workflow 内)
raw_workdir = step_data.get("workdir")
if raw_workdir is not None:
    if not isinstance(raw_workdir, str):
        raise WorkflowValidationError(...)
    if not raw_workdir:
        raise WorkflowValidationError(...)
    try:
        expanded = Path(raw_workdir).expanduser()
    except RuntimeError as e:
        raise WorkflowValidationError(...) from e
    if not expanded.is_absolute():
        raise WorkflowValidationError(...)
    raw_workdir = str(expanded)
```

`expanduser()` は `config.py:116-121` と同じパターン。パース段階で正規化することで、以降の処理は常に絶対パスを前提にできる。

### 3. バリデーター変更（workflow.py `validate_workflow()`）

直接構築された `Workflow` / `Step` に対しても workdir の不正値を検出する。`timeout` の `validate_workflow()` 契約（`tests/test_timeout_config.py:396-438`）と同一パターン:

- 非文字列型の workdir → エラー
- 空文字列の workdir → エラー
- 相対パスの workdir → エラー

```python
# 疑似コード (validate_workflow 内)
# ワークフローレベル
if workflow.workdir is not None:
    if not isinstance(workflow.workdir, str) or not workflow.workdir:
        errors.append(...)
    elif not Path(workflow.workdir).is_absolute():
        errors.append(...)

# ステップレベル（既存の step ループ内）
if step.workdir is not None:
    if not isinstance(step.workdir, str) or not step.workdir:
        errors.append(...)
    elif not Path(step.workdir).is_absolute():
        errors.append(...)
```

**注意**: `validate_workflow()` では `expanduser()` は行わない。`_parse_workflow()` 経由の値は既に展開済みだが、直接構築の場合は呼び出し元の責務。`validate_workflow()` は「絶対パスであること」だけを検証する。

### 4. ランナー変更（runner.py）

`execute_cli()` 呼び出し時に workdir を解決する。パース段階で `~` 展開済みなので、ここでは フォールバック解決 + 存在確認のみ。

```python
# 疑似コード
raw_workdir = current_step.workdir or self.workflow.workdir
effective_workdir = Path(raw_workdir) if raw_workdir else self.project_root
if not effective_workdir.is_dir():
    raise WorkdirNotFoundError(current_step.id, effective_workdir)
```

### 5. 責務の分担まとめ

| レイヤー | 責務 |
|---------|------|
| `_parse_workflow()` | 型チェック、空文字列拒否、`~` 展開、絶対パス判定、正規化後格納 |
| `validate_workflow()` | 直接構築モデルの不正値検出（非文字列、空文字列、相対パス） |
| `runner.py` | フォールバック解決（step > workflow > project_root）、実行時ディレクトリ存在確認 |

### 6. 影響範囲

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

- **workdir フィールドのパース（`_parse_workflow()`）**:
  - ワークフローレベル・ステップレベルの workdir が正しくパースされること
  - `~` 付きパスが `expanduser()` で展開された絶対パスとして格納されること
  - 空文字列、非文字列型、相対パスでバリデーションエラーになること
  - workdir 未指定時は None としてパースされること（後方互換）
- **直接構築モデルのバリデーション（`validate_workflow()`）**:
  - 非文字列型の workdir → エラー（workflow レベル・step レベル）
  - 空文字列の workdir → エラー
  - 相対パスの workdir → エラー
  - None / 有効な絶対パス → エラーなし
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
| config.py の `~` 展開パターン | `kaji_harness/config.py:116-123` | `Path(value).expanduser()` → `is_absolute()` の順序で正規化。`RuntimeError` を捕捉して `ConfigLoadError` に変換 |
| validate_workflow() の timeout 契約 | `tests/test_timeout_config.py:396-438` | 直接構築した `Workflow`/`Step` の不正値を `validate_workflow()` で弾く契約がテストで保持されている |
| timeout 設計（同パターンの先行実装） | `draft/design/issue-116-timeout-config.md` | ワークフローレベル → ステップレベルのフォールバック階層設計パターン。`_parse_workflow()` と `validate_workflow()` の両方を設計対象に含む |
