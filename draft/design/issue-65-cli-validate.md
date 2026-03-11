# [設計] dao validate サブコマンドの追加

Issue: #65

## 概要

ワークフロー YAML のスキーマバリデーションを CLI から実行できる `dao validate <file>...` サブコマンドを追加する。

## 背景・目的

ワークフロー YAML を `dao run` で実行する前に、定義の正当性を事前検証する手段がない。`load_workflow()` + `validate_workflow()` は既に実装済みだが、CLI からの直接呼び出し手段が存在しない。ワークフロー作成者が素早くフィードバックを得られるようにするため、薄い CLI ラッパーとして `dao validate` を提供する。

## インターフェース

### 入力

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `<file>...` | 1つ以上のファイルパス | Yes | バリデーション対象のワークフロー YAML |

### 出力

**stdout** — 各ファイルの検証結果:

```
✓ workflows/feature-development.yaml
```

**stderr** — エラー詳細:

```
✗ bad.yaml
  - Step 'review' transitions to unknown step 'fix' on RETRY
  - Cycle 'review-cycle' entry step 'review' not found
```

**複数ファイル時のサマリー** (1つ以上失敗時、stderr):

```
Validation failed: 1 of 2 files had errors.
```

### 終了コード

| コード | 意味 |
|--------|------|
| 0 | 全ファイルバリデーション成功 |
| 1 | 1つ以上のバリデーションエラー |
| 2 | argparse エラー（引数不足など、argparse デフォルト動作） |

### 使用例

```python
# テストからの呼び出し（cli_main.main() を直接使用）
from dao_harness.cli_main import main

exit_code = main(["validate", "workflows/feature-development.yaml"])
assert exit_code == 0

exit_code = main(["validate", "bad.yaml"])
assert exit_code == 1

exit_code = main(["validate", "good.yaml", "bad.yaml"])
assert exit_code == 1
```

## 制約・前提条件

- **新規依存なし**: argparse のみ使用（既存の `cli_main.py` と同様）
- **既存関数の再利用**: `load_workflow()` と `validate_workflow()` をそのまま使用
- **既存の `cli_main.py` への追加**: 新ファイルは作らず、`cli_main.py` の `create_parser()` にサブコマンドを追加する形
- **カラー出力は対象外**: Issue スコープ外（依存追加が必要）
- **JSON 出力モードは対象外**: Issue スコープ外
- **終了コード体系**: 既存の `EXIT_OK = 0`, `EXIT_DEFINITION_ERROR = 2` を再利用。バリデーション失敗用に `EXIT_VALIDATION_ERROR = 1` を追加

## 方針

### 1. `cli_main.py` への `validate` サブコマンド登録

`create_parser()` 内の `subparsers` に `validate` を追加。`nargs="+"` で1つ以上のファイルパスを受け取る。

### 2. `cmd_validate()` の実装

```python
# 疑似コード
def cmd_validate(args) -> int:
    failed = 0
    total = len(args.files)
    for path in args.files:
        if not path.exists():
            print_error(path, "File not found")
            failed += 1
            continue
        try:
            wf = load_workflow(path)
            validate_workflow(wf)
            print_success(path)
        except WorkflowValidationError as e:
            print_error(path, e.errors)
            failed += 1
    if failed > 0 and total > 1:
        print_summary(failed, total)
        return EXIT_VALIDATION_ERROR
    return EXIT_OK if failed == 0 else EXIT_VALIDATION_ERROR
```

### 3. `main()` へのディスパッチ追加

既存の `if args.command == "run"` に `elif args.command == "validate"` を追加。

### 4. 出力ヘルパー

`_print_success(path)` と `_print_errors(path, errors)` を `cli_main.py` 内に定義。stdout/stderr の使い分けは Issue 仕様に従う（成功→stdout、失敗→stderr）。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

`cmd_validate()` のロジックを直接テストする。`capsys` でキャプチャ。

- **有効な YAML → exit 0 + `✓` メッセージが stdout に出力される**
- **スキーマ違反 YAML → exit 1 + エラーメッセージが stderr に出力される**
- **YAML 構文エラー → exit 1 + エラーメッセージが stderr に出力される**
- **存在しないファイル → exit 1 + エラーメッセージが stderr に出力される**
- **複数ファイル全成功 → exit 0**
- **複数ファイル一部失敗 → exit 1 + サマリーが stderr に出力される**
- **引数なし → argparse が exit 2 を返す**（argparse のデフォルト動作確認）

### Medium テスト

実際のファイルシステム上のワークフロー YAML を使った結合テスト。`tmp_path` fixture を使用。

- **実ファイルの読み込み・パース・バリデーションのパイプライン検証**: `tmp_path` に YAML を書き出し、`cmd_validate()` にパスを渡して end-to-end の動作確認
- **複数ファイル混在時のファイル I/O 挙動**: 有効/無効ファイルを混在させ、全ファイルが処理されること（途中で打ち切られないこと）を確認
- **パーミッションエラー時の挙動**: 読み取り権限のないファイルを渡した場合のエラーハンドリング

### Large テスト

実際にインストールされた `dao` コマンドを `subprocess` 経由で実行する E2E テスト。

- **`dao validate <valid.yaml>` の実行 → exit 0 + stdout に `✓` 出力**
- **`dao validate <invalid.yaml>` の実行 → exit 1 + stderr にエラー出力**
- **`dao validate` 引数なし → exit 2**
- **`dao validate <valid.yaml> <invalid.yaml>` の実行 → exit 1 + サマリー出力**

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更はない |
| docs/dev/ | なし | 開発ワークフロー自体への変更はない |
| docs/cli-guides/ | あり | `dao validate` の使い方を追記する必要がある |
| CLAUDE.md | あり | Essential Commands セクションに `dao validate` を追記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 既存 `load_workflow()` | `dao_harness/workflow.py:14-30` | YAML パース + `_parse_workflow()` で Workflow オブジェクトを返す。`yaml.YAMLError` は `WorkflowValidationError` にラップ済み |
| 既存 `validate_workflow()` | `dao_harness/workflow.py:168-301` | Workflow オブジェクトの静的検証。エラーは `WorkflowValidationError(errors: list[str])` で一括 raise |
| `WorkflowValidationError` | `dao_harness/errors.py:9-19` | `errors: list[str]` 属性で複数エラーを保持。`str` 引数の場合は要素1のリストに変換 |
| 既存 `cli_main.py` | `dao_harness/cli_main.py` | `create_parser()` + サブコマンドパターンが確立済み。`_register_run()` と同パターンで `_register_validate()` を追加可能 |
| argparse `nargs="+"` | https://docs.python.org/3/library/argparse.html#nargs | 1つ以上の引数を受け取る。0個の場合 argparse がエラー（exit 2）を出す |
| Issue #65 仕様 | GitHub Issue #65 本文 | CLI 出力フォーマット、終了コード、スコープ定義 |
