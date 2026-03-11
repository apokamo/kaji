# [設計] dao run CLIサブコマンドの実装

Issue: #67

## 概要

`dao run` サブコマンドを実装し、ワークフローをコマンドラインから実行可能にする。

## 背景・目的

ハーネスのコア機能（`WorkflowRunner`, `load_workflow`, `SessionState` 等）は実装・テスト済みだが、CLIフロントエンドが存在しないため `dao run <workflow.yaml> <issue>` として実行できない。#65 で CLI基盤（`cli_main.py` + argparse サブコマンド構造 + `pyproject.toml` の `[project.scripts]` 有効化）が作成される前提で、本イシューでは `run` サブコマンドを追加する。

## 前提条件

- #65 (`dao validate`) がマージ済みであること
- `cli_main.py` に argparse サブコマンドパーサーが存在すること

## インターフェース

### 入力

```
dao run <workflow> <issue> [options]
```

| 引数/オプション | 型 | 必須 | 説明 |
|-----------------|-----|------|------|
| `workflow` | positional (str) | ○ | ワークフローYAMLファイルパス |
| `issue` | positional (int) | ○ | GitHub Issue番号 |
| `--from STEP_ID` | option (str) | × | 指定ステップから再開 |
| `--step STEP_ID` | option (str) | × | 単一ステップのみ実行 |
| `--workdir DIR` | option (str) | × | 作業ディレクトリ（デフォルト: カレントディレクトリ） |
| `--quiet` | flag | × | エージェント出力のストリーミング表示を抑制 |

`--from` と `--step` は排他（同時指定はエラー）。

### 出力

- **正常終了**: exit 0、最終状態のサマリーを stdout に出力
- **ワークフロー ABORT**: exit 1、ABORT 理由を stderr に出力
- **バリデーションエラー**: exit 2、エラー詳細を stderr に出力
- **CLI 実行エラー**: exit 3、エラー詳細を stderr に出力

### 使用例

```bash
# 基本実行
dao run workflows/design.yaml 67

# 途中から再開
dao run workflows/design.yaml 67 --from review-design

# 単一ステップ実行
dao run workflows/design.yaml 67 --step implement

# 作業ディレクトリ指定 + 静かに実行
dao run workflows/design.yaml 67 --workdir ../dao-feat-67 --quiet
```

## 制約・前提条件

- 外部依存を追加しない（argparse のみ）
- `WorkflowRunner` の既存インターフェースを変更しない
- エラーハンドリングは `HarnessError` 階層をそのまま活用
- exit code は意味を持たせる（スクリプトからの呼び出しを想定）

## 方針

### 1. `cli_main.py` への `run` サブコマンド追加

#65 が作成する `cli_main.py` のサブコマンドパーサーに `run` を追加する。

```python
# 疑似コード
def register_run(subparsers):
    p = subparsers.add_parser("run", help="Run a workflow")
    p.add_argument("workflow", type=Path)
    p.add_argument("issue", type=int)
    p.add_argument("--from", dest="from_step")
    p.add_argument("--step", dest="single_step")
    p.add_argument("--workdir", type=Path, default=Path.cwd())
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_run)
```

### 2. `cmd_run` 関数

`load_workflow` → `WorkflowRunner` → `run()` のパイプラインを実行し、例外を exit code にマッピングする。

```python
def cmd_run(args) -> int:
    # 排他チェック: --from と --step
    # load_workflow(args.workflow)
    # WorkflowRunner(...).run()
    # エラーハンドリング → 適切な exit code
```

### 3. exit code マッピング

| 例外 | exit code |
|------|-----------|
| 正常終了 | 0 |
| ABORT verdict | 1 |
| `WorkflowValidationError` | 2 |
| `CLIExecutionError` / `CLINotFoundError` / `StepTimeoutError` | 3 |
| `MissingResumeSessionError` / `InvalidTransition` | 3 |
| その他の予期しない例外 | 1 |

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- `--from` と `--step` の排他バリデーション
- 引数パース: 各オプションが正しく `WorkflowRunner` のパラメータにマッピングされること
- exit code マッピング: 各例外クラスが正しい exit code に変換されること
- サマリー出力のフォーマット

### Medium テスト

- 有効なワークフローYAML + モック済み `WorkflowRunner` で正常終了 → exit 0
- 不正なYAML → exit 2 + エラーメッセージが stderr に出力
- `WorkflowRunner.run()` が `CLIExecutionError` → exit 3
- `WorkflowRunner.run()` が ABORT verdict → exit 1
- `--workdir` に存在しないディレクトリ → エラー
- `subprocess` 経由での `dao run --help` 出力検証

### Large テスト

- 実際の `dao run` コマンドを subprocess で実行し、有効なワークフローYAML（ただしエージェントCLI未インストールの状態）で `CLINotFoundError` 相当のエラーが返ること
- `pip install -e .` 後に `dao run --help` が利用可能であること

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（argparse は #65 で選定済み） |
| docs/ARCHITECTURE.md | 軽微 | L108-113 で `dao run` の `--from` 再開を既に記述済み。新フラグ (`--workdir`, `--quiet`) の追記が必要な可能性 |
| docs/dev/workflow-authoring.md | 更新 | L170-181 で `dao run` コマンド例を既に記述済み。新フラグの追記 + exit code の説明追加 |
| docs/dev/skill-authoring.md | なし | コンテキスト変数の変更なし |
| docs/dev/development_workflow.md | なし | スキルライフサイクルの記述であり CLI ハーネスは無関係 |
| docs/cli-guides/ | なし | claude/codex/gemini 各 CLI のリファレンスであり `dao` CLI は対象外 |
| README.md | 更新 | ワークフロー実行方法のセクションが未記載。`dao run` の基本的な使い方を追加 |
| CLAUDE.md | 軽微 | `dao run` の基本3パターンは記載済み。`--workdir` / `--quiet` を追記する可能性 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| argparse 公式ドキュメント | https://docs.python.org/3/library/argparse.html | サブコマンドパーサー (`add_subparsers`) の仕様。#65 で選定済みの手法を踏襲 |
| WorkflowRunner 実装 | `dao_harness/runner.py` | `run()` メソッドのシグネチャ: `workflow`, `issue_number`, `workdir`, `from_step`, `single_step`, `verbose` |
| #65 設計 | GitHub Issue #65 | CLI基盤（`cli_main.py` + argparse + `[project.scripts]`）の設計。`run` サブコマンドは明示的にスコープ外 |
| errors.py | `dao_harness/errors.py` | 例外階層: `HarnessError` を基底とし、各エラーが明確に分類済み |
