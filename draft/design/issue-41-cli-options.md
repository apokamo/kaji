# [設計] dao design に --workdir と --dry-run オプションを追加

Issue: #41

## 概要

`dao design` コマンドに `--workdir` と `--dry-run` オプションを追加し、CLI から runner.py の既存機能を利用可能にする。

## 背景・目的

現状:
- `run_design_workflow()` は既に `workdir` と `dry_run` を処理する実装が完了している
- ただし CLI (`src/cli.py`) の `design_parser` にこれらのオプションが未定義
- 結果として CLI からこれらの機能を利用できない

目的:
- CLI と runner.py の機能差を解消する
- worktree 環境での E2E テスト実行を可能にする

## インターフェース

### 入力

**CLI オプション**:

| オプション | 短縮 | 型 | デフォルト | 説明 |
|-----------|------|-----|----------|------|
| `--workdir` | `-w` | str | なし | artifacts 保存先の基準ディレクトリ |
| `--dry-run` | なし | flag | False | Issue へのコメントをスキップ |

### 出力

- `args.workdir`: `str | None`
- `args.dry_run`: `bool`

これらは既存の `run_design_workflow(args)` にそのまま渡される。

### 使用例

```bash
# 基本（現行動作）
dao design --issue https://github.com/org/repo/issues/42

# workdir 指定（artifacts は ./artifacts に保存）
dao design --issue https://github.com/org/repo/issues/42 --workdir .

# dry-run（Issue にコメントしない）
dao design --issue https://github.com/org/repo/issues/42 --dry-run

# 組み合わせ（E2E テスト向け）
dao design --issue https://github.com/org/repo/issues/42 -w . --dry-run
```

## 制約・前提条件

- `runner.py` の実装は変更しない（既に完成済み）
- `dao implement` の `--workdir` と挙動を揃える
- `--dry-run` は `dao design` 固有（`dao implement` には不要）

## 方針

### 実装箇所

`src/cli.py` の `design_parser` に2行追加するのみ:

```python
# Design workflow
design_parser = subparsers.add_parser("design", help="Design workflow")
design_parser.add_argument("--issue", required=True, help="GitHub issue URL")
design_parser.add_argument("--input", "-i", help="Input requirements file (optional)")
design_parser.add_argument("--workdir", "-w", help="Working directory for artifacts")  # 追加
design_parser.add_argument("--dry-run", action="store_true", help="Skip Issue comments")  # 追加
```

### argparse の挙動

| オプション | 未指定時の値 | runner.py での処理 |
|-----------|-------------|-------------------|
| `--workdir` | `None` | `artifacts/` を使用 |
| `--dry-run` | `False` | Issue にコメントを投稿 |

**注記**: `--dry-run` は argparse で `dry_run` 属性に自動変換される（ハイフンがアンダースコアに）。`runner.py` は `getattr(args, "dry_run", False)` で取得しており、この命名規則に適合している。

## 検証観点

### 正常系

- `--workdir .` 指定時、artifacts が `./artifacts/` に保存される
- `--dry-run` 指定時、Issue コメントがスキップされる
- 両オプション組み合わせ時、両方の効果が適用される

### 後方互換性

- オプション未指定時、現行動作と同一である
- `--issue` と `--input` の既存動作に影響しない

### CLI パーサー

- `dao design --help` に新オプションが表示される
- 不正な引数でエラーになる（argparse 標準動作）

## 参考

- `run_design_workflow()` の既存実装: `src/workflows/design/runner.py:24-80`
- 既存テスト: `tests/test_design_runner.py` の `TestRunDesignWorkflow`
