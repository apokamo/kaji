# [設計] test_legacy_cleanup.py を削除

Issue: #97

## 概要

V5/V6→V7移行時に作成された一時的な回帰テストファイル `tests/test_legacy_cleanup.py` を削除する。

## 背景・目的

`tests/test_legacy_cleanup.py` は Issue #59 で V5/V6 コードを `legacy/` へ移動した際に作成された回帰テストである。移行は完了しており、テストの役割は終えている。

さらに、`TestPackageInstallation.test_bugfix_agent_not_importable_after_install` が subprocess で `pip install -e` を実行するため、worktree 環境で shared `.venv` の editable install パスを上書きする副作用があり、#87 の直接原因となっている。

削除理由をまとめると:

1. **役割の完了**: 移行検証は完了済み。ファイル/ディレクトリの存在確認やドキュメント内容の検証は、リポジトリの状態が変われば自然に壊れる性質のテストであり、継続的な価値がない
2. **副作用の除去**: Large テストが shared `.venv` を破壊する問題（#87）の根本原因を除去

## インターフェース

### 入力

なし（ファイル削除のみ）

### 出力

- `tests/test_legacy_cleanup.py` が存在しなくなる
- テストスイートから 22 テストケース（Small 17 + Medium 3 + Large 2）が除去される

### 使用例

```bash
# 削除後の確認
pytest  # test_legacy_cleanup.py のテストが含まれないこと
```

## 制約・前提条件

- 他のテストファイルから `test_legacy_cleanup.py` への参照・依存がないこと（確認済み: `grep` で参照なし）
- `conftest.py` やフィクスチャに影響がないこと（このファイルは独立しており外部フィクスチャを定義していない）
- 削除対象のテストが検証していた内容（legacy 配置、pyproject.toml、ドキュメント内容）は、リポジトリの状態そのものが一次情報であり、テストで保護する必要がない

## 方針

1. `tests/test_legacy_cleanup.py` を `git rm` で削除
2. `ruff check`, `ruff format`, `mypy`, `pytest` で品質チェック
3. コミット

単純なファイル削除であり、コード修正やリファクタリングは不要。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### スキップするサイズ

- **Small**: 物理的に作成不可。この変更はファイル削除のみであり、新規ロジック・バリデーション・マッピング等のテスト対象コードが存在しない。
- **Medium**: 物理的に作成不可。DB連携・内部サービス結合等の検証対象が存在しない。
- **Large**: 物理的に作成不可。実API疎通・E2Eデータフロー等の検証対象が存在しない。

### 検証方法

テストコードの新規作成は不要だが、以下で正しく削除されたことを検証する:

- `pytest` の全テストパス（既存テストに影響がないこと）
- `pytest --collect-only -q` でテスト一覧から `test_legacy_cleanup` が消えていること
- `ruff check` / `mypy` がパスすること（他ファイルからの参照がないこと）

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定の変更なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし |
| docs/cli-guides/ | なし | CLI仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 削除対象ファイル | `tests/test_legacy_cleanup.py` | 22テスト全件の内容を確認。全て V5/V6→V7 移行検証用であり、移行完了後は不要。冒頭 docstring: `"""Tests for #59: V5/V6 legacy file cleanup and V7 base clarification."""` |
| Issue #87 (副作用報告) | https://github.com/apokamo/kaji/issues/87 | `TestPackageInstallation` が shared `.venv` の editable install パスを上書きする副作用が報告されている |
| Issue #59 (移行元Issue) | https://github.com/apokamo/kaji/issues/59 | このテストファイルが作成された移行作業の元 Issue。移行完了により役割終了 |
