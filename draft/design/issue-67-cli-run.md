# [設計] dao run CLIサブコマンドの実装

Issue: #67

## 概要

`dao run` サブコマンドを実装し、ワークフローをコマンドラインから実行可能にする。

## 背景・目的

ハーネスのコア機能（`WorkflowRunner`, `load_workflow`, `SessionState` 等）は実装・テスト済みだが、CLIフロントエンドが存在しないため `dao run <workflow.yaml> <issue>` として実行できない。#65 で CLI基盤（`cli_main.py` + argparse サブコマンド構造 + `pyproject.toml` の `[project.scripts]` 有効化）が作成される前提で、本イシューでは `run` サブコマンドを追加する。

## 前提条件

- #65 (`dao validate`) がマージ済みであること
- `cli_main.py` に argparse サブコマンドパーサーが存在すること

### #65 依存の解消方針

#65 は 2026-03-11 時点で OPEN。以下の方針で対応する:

1. **実装開始条件**: #65 のマージを待つ。#67 ブランチは `main` から作成済みのため、#65 マージ後に `git merge main` で基盤を取り込む
2. **#65 が長期停滞した場合**: #67 のスコープを拡張し、CLI 基盤（`cli_main.py` + `[project.scripts]`）ごと本イシューで実装する。この場合 #65 は #67 に吸収される

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
| `--workdir DIR` | option (str) | × | エージェント CLI の作業ディレクトリ（デフォルト: カレントディレクトリ）。状態ファイル・ログの保存先には影響しない（後述） |
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

### `--workdir` と状態保存場所の関係

`--workdir` はエージェント CLI の `cwd` のみを制御する。状態ファイルとログは `dao run` を実行したプロセスのカレントディレクトリ基準で保存される:

| 対象 | 保存先 | `--workdir` の影響 |
|------|--------|-------------------|
| `SessionState` | `test-artifacts/<issue>/state.json`（プロセス cwd 基準） | なし |
| 実行ログ | `test-artifacts/<issue>/runs/<timestamp>/`（プロセス cwd 基準） | なし |
| エージェント CLI の `cwd` | `--workdir` で指定されたディレクトリ | **あり** |

この分離は `WorkflowRunner` の既存設計に従ったもの。`runner.py` で `run_dir` はプロセス cwd 基準、`execute_cli()` の `cwd=workdir` はエージェント実行先のみを指す。

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

既知の `HarnessError` サブクラスを網羅的にマッピングする。`HarnessError` を基底で catch することで、将来追加されるサブクラスも「既知エラー」として扱う。

| exit code | 意味 | 対応する例外 |
|-----------|------|-------------|
| 0 | 正常終了 | — |
| 1 | ワークフロー ABORT | ABORT verdict で終了した場合 |
| 2 | 定義エラー（実行前に検出） | `WorkflowValidationError`, `SkillNotFound`, `SecurityError` |
| 3 | 実行時エラー（ステップ実行中に発生） | `CLIExecutionError`, `CLINotFoundError`, `StepTimeoutError`, `MissingResumeSessionError`, `InvalidTransition`, `VerdictNotFound`, `VerdictParseError`, `InvalidVerdictValue` |
| 1 | 予期しないエラー | `HarnessError` の未知サブクラス、または `HarnessError` 以外の例外 |

**実装方針**: 個別の例外クラスを列挙するのではなく、`HarnessError` の catch 内で分類する:

```python
try:
    ...
except WorkflowValidationError | SkillNotFound | SecurityError as e:
    # 定義エラー → exit 2
except HarnessError as e:
    # その他の既知実行時エラー → exit 3
except Exception as e:
    # 予期しないエラー → exit 1
```

**ユーザー向けメッセージ**: 全ての `HarnessError` は `str(e)` で人間可読なメッセージを提供済み（`errors.py` の各 `__init__` で設定）。CLI は `stderr` にそのまま出力する。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- `--from` と `--step` の排他バリデーション
- 引数パース: 各オプションが正しく `WorkflowRunner` のパラメータにマッピングされること
- exit code マッピング: 全 `HarnessError` サブクラス（`WorkflowValidationError`, `SkillNotFound`, `SecurityError`, `CLIExecutionError`, `CLINotFoundError`, `StepTimeoutError`, `MissingResumeSessionError`, `InvalidTransition`, `VerdictNotFound`, `VerdictParseError`, `InvalidVerdictValue`）が正しい exit code に変換されること
- `HarnessError` 以外の予期しない例外 → exit 1
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
