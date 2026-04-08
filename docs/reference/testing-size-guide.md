# テストサイズ判断ガイド

テストサイズ（S/M/L）の分類に迷う境界ケースの判断基準。
基本定義は [テスト規約](../dev/testing-convention.md) を参照。

## 判断フローチャート

```
テスト対象を確認
  ├── 外部 API / 実サービスへの疎通がある → Large
  ├── ファイル I/O / DB / 内部サービスとの結合がある → Medium
  └── 純粋なロジック・モック完結 → Small
```

## 境界ケース

### tmp ディレクトリへのファイル I/O

| ケース | サイズ | 理由 |
|--------|--------|------|
| `tmp_path` fixture でファイル読み書き | Medium | ファイルシステムとの結合 |
| `io.StringIO` でのメモリ内 I/O | Small | 外部依存なし |

### subprocess / コマンド実行

| ケース | サイズ | 理由 |
|--------|--------|------|
| 実際の CLI コマンドを subprocess で実行 | Large | 外部プロセスとの疎通 |
| コマンド引数の組み立てロジックのみテスト | Small | 文字列操作のみ |
| `click.testing.CliRunner` による CLI テスト | Medium | 内部サービスとの結合 |

### モック / パッチ

| ケース | サイズ | 理由 |
|--------|--------|------|
| 外部 API をモックして呼び出しロジックをテスト | Small | モック完結 |
| モック + ファイル出力の検証 | Medium | ファイル I/O を含む |

### 設定ファイル読み込み

| ケース | サイズ | 理由 |
|--------|--------|------|
| TOML/YAML パース（文字列入力） | Small | 純粋なパース処理 |
| 実ファイルからの設定読み込み | Medium | ファイル I/O |
| 設定ファイル探索（ディレクトリ走査） | Medium | ファイルシステムとの結合 |

## マーカーの付け方

```python
import pytest

@pytest.mark.small
def test_parse_config_string():
    """文字列からの設定パース（外部依存なし）"""
    ...

@pytest.mark.medium
def test_load_config_file(tmp_path):
    """ファイルからの設定読み込み（ファイル I/O あり）"""
    ...

@pytest.mark.large
def test_cli_end_to_end():
    """CLI の E2E テスト（外部プロセス実行）"""
    ...
```
