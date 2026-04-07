# [設計] --version オプションの追加

Issue: #131

## 概要

`kaji --version` でパッケージバージョンを表示できるようにする。

## 背景・目的

CLIツールとして基本的な `--version` オプションが未実装。ユーザーがインストール済みバージョンを確認する手段がない。

## インターフェース

### 入力

- CLI引数: `kaji --version`

### 出力

- 標準出力: `kaji X.Y.Z` 形式のバージョン文字列
- 出力後、終了コード 0 でプロセス終了（argparse の `version` アクションの標準動作）

### 使用例

```bash
$ kaji --version
kaji 0.9.0
```

## 制約・前提条件

- バージョンはハードコードしない。`importlib.metadata.version()` で `pyproject.toml` の値を動的に取得する
- Python 3.11+ を前提とするため `importlib.metadata` は標準ライブラリで利用可能
- `--version` は argparse の組み込み `version` アクションを使用するため、サブコマンド指定なしでトップレベルで動作する
- `importlib.metadata.version()` は distribution metadata が見つからない場合 `PackageNotFoundError` を送出する。parser 構築時に即時評価する場合、`--version` 以外のコマンド（`kaji run` 等）もこの例外で失敗するため、parser 構築を安全に保つ必要がある

## 方針

`create_parser()` 内で `parser.add_argument("--version", ...)` を追加する。

`importlib.metadata.version()` は parser 構築時（f-string 評価時）に呼ばれるため、distribution metadata が見つからない環境では `PackageNotFoundError` が発生し、`--version` 以外のコマンドまで巻き込んで失敗する。これを防ぐため、バージョン取得を `try/except` で囲み、失敗時は `"unknown"` にフォールバックする。

```python
from importlib.metadata import PackageNotFoundError, version

def _get_version() -> str:
    try:
        return version("kaji")
    except PackageNotFoundError:
        return "unknown"

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(...)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_get_version()}"
    )
    # 既存のsubparsers登録...
```

- argparse の `action="version"` は `--version` 指定時にバージョン文字列を出力して `sys.exit(0)` する。サブコマンドの `required=True` より先に評価されるため、`kaji --version` 単体で動作する
- `_get_version()` は parser 構築時に1回だけ呼ばれる。通常の `uv sync` 後の環境では常にバージョンが取得できる。`"unknown"` フォールバックは未インストール状態でのソース直接実行時のみ発生する

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 実行時コード変更では Small / Medium / Large の観点を定義し、
> docs-only / metadata-only / packaging-only 変更では変更固有検証と
> 恒久テストを追加しない理由を明記する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### 変更タイプ
- 実行時コード変更（CLI引数パース処理の追加）

### Small テスト
- `create_parser()` が `--version` 引数を受け付けること
- `--version` パース時に `SystemExit(0)` が発生し、出力に `kaji` とバージョン文字列が含まれること
- バージョン文字列が `importlib.metadata.version("kaji")` と一致すること
- `_get_version()` が `PackageNotFoundError` 発生時に `"unknown"` を返すこと（モックで検証）

### Medium テスト
- 不要。ファイルI/O・DB・内部サービスとの結合はない

### Large テスト
- 不要。外部APIやE2Eデータフローは関係しない

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/workflow-authoring.md | なし | `kaji run` / `kaji validate` の使用例があるが `--version` は無関係 |
| docs/cli-guides/ | なし | 各ファイルは Claude/Codex/Gemini の CLI ガイドであり、kaji CLI 自体の仕様書ではない |
| CLAUDE.md | あり | 「Essential Commands」セクションに `kaji --version` の記載を追加する候補 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| argparse `version` アクション | https://docs.python.org/3/library/argparse.html#action | `action="version"` はバージョン情報を表示して終了する組み込みアクション |
| importlib.metadata.version | https://docs.python.org/3/library/importlib.metadata.html#importlib.metadata.version | `version(package_name)` でインストール済みパッケージのバージョンを取得する |
| 既存 CLI 実装 | `kaji_harness/cli_main.py` | `create_parser()` にトップレベル引数を追加する箇所 |
