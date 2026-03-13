# [設計] ライセンス選定と SPDX 移行

Issue: #105

## 概要

Apache License 2.0 を正式採用し、`pyproject.toml` の `project.license` を PEP 639 準拠の SPDX 形式に移行する。合わせて `LICENSE` ファイルの新規追加と `README.md` のライセンス表記を更新する。

## 背景・目的

- オープンソース化に向けたライセンス正式選定が未了
- 現状 `license = {text = "MIT"}` のテーブル形式は PEP 639 で非推奨（setuptools が 2027-02-18 に警告期限を設定）
- リポジトリに `LICENSE` ファイルが存在せず、配布物にライセンス文書が含まれない
- Apache 2.0 は特許条項（Patent Grant / Patent Retaliation）によるコントリビューター保護を提供し、kaji のツール層としての性質と合致する

## インターフェース

### 入力

変更対象ファイル:

| ファイル | 現状 | 変更内容 |
|---------|------|---------|
| `pyproject.toml` | `license = {text = "MIT"}` + MIT classifier | SPDX 文字列 + classifier 削除 |
| `LICENSE` | 存在しない | Apache License 2.0 全文を新規作成 |
| `README.md` | `## License` セクションに `MIT` | `Apache-2.0` に更新 |

### 出力

変更後の状態:

```toml
# pyproject.toml
[project]
license = "Apache-2.0"
license-files = ["LICENSE"]
# classifiers から "License :: OSI Approved :: MIT License" を削除
```

```
# LICENSE（リポジトリルート）
Apache License Version 2.0 全文（https://www.apache.org/licenses/LICENSE-2.0.txt から取得）
```

```markdown
# README.md
## License

Apache-2.0
```

### 使用例

変更後の動作確認:

```bash
# パッケージメタデータの確認
pip install -e . && python -c "
from importlib.metadata import metadata
m = metadata('kaji')
print(m['License-Expression'])  # Apache-2.0
"

# sdist/wheel に LICENSE が含まれることの確認
python -m build && tar tzf dist/kaji-*.tar.gz | grep LICENSE
```

## 制約・前提条件

- PEP 639 / Core Metadata 2.4 に準拠すること
- `License ::` classifier は deprecated のため削除する（暫定残置はしない）
- `LICENSE` ファイルは Apache Software Foundation の公式テキストをそのまま使用する（改変不可）
- setuptools >= 68.0 が必要（現在の `build-system.requires` で充足済み）
- `license-files` を明示指定し、配布物への LICENSE 同梱を保証する

## 方針

コード変更は発生しない。プロジェクトメタデータと文書ファイルのみの変更。

1. **LICENSE ファイル作成**: Apache 公式サイトから取得した全文を配置
2. **pyproject.toml 更新**:
   - `license = {text = "MIT"}` → `license = "Apache-2.0"`
   - `license-files = ["LICENSE"]` を追加
   - `classifiers` から `"License :: OSI Approved :: MIT License"` を削除
3. **README.md 更新**: License セクションを `Apache-2.0` に変更

変更順序は上記の通り。LICENSE ファイルが先に存在することで、`license-files` の参照先が確定する。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

- `pyproject.toml` の `license` フィールドが SPDX 文字列 `"Apache-2.0"` であること（テーブル形式でないこと）
- `license-files` に `"LICENSE"` が含まれること
- `classifiers` に `License ::` で始まる項目が存在しないこと
- リポジトリルートに `LICENSE` ファイルが存在すること
- `LICENSE` ファイルに `"Apache License"` と `"Version 2.0"` が含まれること
- `README.md` に `Apache-2.0` のライセンス表記が含まれること

### Medium テスト

- `pip install -e .` がライセンス関連の警告なしに成功すること
- インストール済みパッケージの `importlib.metadata.metadata('kaji')` で `License-Expression` が `Apache-2.0` を返すこと
- `License-File` メタデータに `LICENSE` が含まれること

### Large テスト

- `python -m build` で sdist と wheel をビルドし、両方の配布物に `LICENSE` ファイルが含まれること
- ビルドした配布物の `METADATA` ファイル内に `License-Expression: Apache-2.0` が記載されていること

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定ではなくライセンス選定。Issue 本文に決定根拠がアーカイブされる |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | 開発手順・ワークフロー変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| PEP 639 | https://peps.python.org/pep-0639/ | `license` フィールドは SPDX 文字列を使用。`License ::` classifier は deprecated。`license-files` でライセンス文書の配布物同梱を明示指定 |
| Apache License 2.0 全文 | https://www.apache.org/licenses/LICENSE-2.0.txt | LICENSE ファイルに配置する正式テキスト |
| SPDX License List | https://spdx.org/licenses/ | `Apache-2.0` が正式な SPDX 識別子であることの根拠 |
| setuptools ドキュメント | https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html | `project.license` の SPDX 形式サポートと移行ガイダンス |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
