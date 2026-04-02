# [設計] 新テスト規約に沿った make コマンド整備

Issue: #124

## 概要

新テスト規約（#107）に沿い、変更タイプごとの検証入口を `make` ターゲットとして整備する。

## 背景・目的

Issue #107 で docs-only / metadata-only / packaging-only 変更に対して S/M/L 全要求をしない規約が確立されたが、開発者向けの実行入口は生コマンド中心のまま。`make` を導入し、以下を実現する:

1. 日常の品質確認を 1 コマンドに統合する（`make check`）
2. 変更タイプに応じた追加検証の入口を明確にする（`make verify-docs` 等）
3. `pip install -e .` 等の副作用ある検証を隔離実行する仕組みを提供する
4. CLAUDE.md / README / skills で案内するコマンドの一貫性を確保する

## インターフェース

### 入力

Makefile ターゲットへの引数。一部ターゲットは環境変数でカスタマイズ可能。

### 出力

各ターゲットは対応するツールの stdout/stderr をそのまま出力し、失敗時は非ゼロで終了する。

### 使用例

```bash
# 日常開発（コミット前）
make check

# docs-only 変更時
make check
make verify-docs

# packaging 変更時
make check
make verify-packaging

# 特定サイズのテストのみ
make test-small
make test-medium
```

## 制約・前提条件

- `.venv` が activate 済み、または `make` 実行時に自動 activate する仕組みは**設けない**（既存運用で `source .venv/bin/activate` が前提）
- GNU Make 互換。POSIX make の範囲を基本とし、`.PHONY` 以外の GNU 拡張は使わない
- `make verify-packaging` は一時 venv を作成・破棄するため、実行に数秒〜十数秒かかる
- Python 3.11+ を前提とする（`pyproject.toml` の `requires-python` に準拠）

## 方針

### ターゲット体系

3 層に分類する。

#### Tier 1: 恒久品質ゲート（全変更タイプ共通、コミット前必須）

| ターゲット | 実行内容 | 責務 |
|-----------|---------|------|
| `make lint` | `ruff check kaji_harness/ tests/` | 静的解析 |
| `make format` | `ruff format kaji_harness/ tests/` | フォーマッタ |
| `make typecheck` | `mypy kaji_harness/` | 型検査 |
| `make test` | `pytest` | 恒久回帰テスト全実行 |
| `make check` | lint → format → typecheck → test を順次実行 | プリコミットゲート |

`make check` は CLAUDE.md の Pre-Commit セクションが規定する 4 コマンドと 1:1 対応させる。順序は高速なものから並べ、早期失敗を優先する。

#### Tier 2: 変更タイプ固有の追加検証

| ターゲット | 実行内容 | 責務 |
|-----------|---------|------|
| `make verify-docs` | `python3 scripts/check_doc_links.py` に全対象パスを明示引数で渡す（後述） | docs 変更のリンク・参照整合 |
| `make verify-packaging` | 一時 venv で `pip install -e .` → entry point・metadata 確認 → venv 破棄 | packaging 変更の配布物検証 |

これらは `make check` に**追加して**実行するもの。`make check` の代替ではない。

#### metadata-only 変更の標準運用

metadata-only 変更（`pyproject.toml` のフィールド値変更、classifiers 追加等）では `make check` + `make verify-packaging` を標準とする。理由:

- metadata 変更の主な不具合パターンは「配布物に反映されない」「entry point が壊れる」であり、`verify-packaging` の隔離 install + metadata 確認でカバーできる
- metadata 固有の独立ターゲット（`make verify-metadata`）は設けない。`verify-packaging` が metadata 確認を内包しており、ターゲットを分離しても検証内容が重複するだけで情報が増えない

| 変更タイプ | 標準運用 |
|-----------|---------|
| 実行時コード変更 | `make check` |
| docs-only | `make check` + `make verify-docs` |
| metadata-only | `make check` + `make verify-packaging` |
| packaging-only | `make check` + `make verify-packaging` |

#### Tier 3: 便利ターゲット

| ターゲット | 実行内容 | 責務 |
|-----------|---------|------|
| `make test-small` | `pytest -m small` | Small テストのみ |
| `make test-medium` | `pytest -m medium` | Medium テストのみ |
| `make test-large` | `pytest -m large` | Large テストのみ |
| `make setup` | `pip install -e ".[dev]"` | 開発環境構築 |

### `make verify-docs` の対象範囲

`scripts/check_doc_links.py` は引数なしだと `docs/` 配下のみを検査する。しかし docs 変更は `README.md`、`CLAUDE.md`、`.claude/skills/**/*.md` にも及ぶため、`make verify-docs` ではこれらを明示引数で渡す:

```makefile
verify-docs:
	python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/
```

スクリプト側は既に引数でファイル・ディレクトリを受け付ける実装になっている（`collect_from_args`）ため、変更不要。

### `make check` の適用文脈と例外

`make check` は**日常開発のプリコミットゲート**として設計する。ただし、以下の文脈では raw command の個別実行を維持する:

| 文脈 | `make check` 使用 | 理由 |
|------|-------------------|------|
| 開発者の手動コミット前 | 使用する | 4 コマンドの一括実行が便利 |
| `/issue-implement` スキル | **使用しない** | baseline failure 判定のため `pytest` を lint/typecheck と分離実行する必要がある（`pytest` の exit code を個別に評価し、baseline との差分で合否判定する）|
| `/issue-review-code` スキル | **使用しない** | 同上。baseline failure との照合ロジックが `pytest` 単独実行を前提としている |

スキルが raw command を維持する理由は、`make check` が 4 コマンドを `&&` チェーンで結合するのに対し、baseline failure がある環境では `pytest` が非ゼロ終了しても「既知の failure のみなら OK」という判定が必要なため。この判定は `make check` の責務外であり、スキル側のロジックに委ねる。

したがって、`make check` 導入後もスキルのコマンド記載は変更しない。ドキュメント更新の対象は CLAUDE.md / README.md / testing-convention.md に限定する。

### `make verify-packaging` の隔離設計

```
make verify-packaging
  1. TMPDIR=$(mktemp -d)
  2. python3 -m venv "$TMPDIR/venv"
  3. $TMPDIR/venv/bin/pip install -e .
  4. $TMPDIR/venv/bin/kaji --help  （entry point 確認）
  5. $TMPDIR/venv/bin/python -c "import importlib.metadata; print(importlib.metadata.version('kaji'))"
  6. rm -rf "$TMPDIR"
```

- 失敗時も `trap` で一時ディレクトリを確実に削除する
- shared `.venv` には一切触れない

### `make check` と既存ドキュメントの対応

現在 CLAUDE.md の Pre-Commit セクションは以下を規定している:

```bash
ruff check kaji_harness/ tests/ && ruff format kaji_harness/ tests/ && mypy kaji_harness/ && pytest
```

`make check` はこれと等価に動作する。ドキュメント更新後は `make check` を推奨コマンドとし、個別コマンドは Essential Commands として残す。

### Makefile 実装方針

疑似コード:

```makefile
.PHONY: check lint format typecheck test test-small test-medium test-large \
        verify-docs verify-packaging setup

SOURCES := kaji_harness/ tests/

check: lint format typecheck test

lint:
	ruff check $(SOURCES)

format:
	ruff format $(SOURCES)

typecheck:
	mypy kaji_harness/

test:
	pytest

test-small:
	pytest -m small

test-medium:
	pytest -m medium

test-large:
	pytest -m large

verify-docs:
	python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/

verify-packaging:
	@scripts/verify-packaging.sh

setup:
	pip install -e ".[dev]"
```

`verify-packaging` は trap 処理を含むため、シェルスクリプト `scripts/verify-packaging.sh` に分離する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ
- **実行時コード変更**: なし。Makefile と shell script の追加であり、Python の実行時コードは変更しない
- **packaging-only / metadata-only**: なし。`pyproject.toml` は変更しない
- **docs-only を含む複合変更**: Makefile（ビルドツール）+ ドキュメント更新

### 変更固有検証

この変更は Makefile（ビルドツール設定）とシェルスクリプトの追加であり、Python の実行時ロジックを変更しない。以下で妥当性を確認する:

| 検証項目 | 方法 |
|---------|------|
| `make check` が 4 コマンドを順次実行し、失敗時に停止すること | `make check` を手動実行 |
| `make verify-docs` がリンク切れを検出すること | 意図的にリンク切れを作り `make verify-docs` で検出を確認 |
| `make verify-packaging` が隔離 venv で動作し、shared .venv を汚染しないこと | `make verify-packaging` 実行後、`.venv` のパッケージ一覧が変化していないことを確認 |
| 各ターゲットが存在し、typo なく動作すること | 全ターゲットを 1 回ずつ実行 |

### 恒久テストを追加しない理由

1. **独自ロジックの追加・変更をほぼ含まない**: Makefile は既存コマンドのラッパーであり、新しいロジックはない。`verify-packaging.sh` も `pip install` / `importlib.metadata` の呼び出しのみ
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**: 対象ツール（ruff, mypy, pytest）自体のテストは各ツールが責務を持つ。Makefile の typo は実行時に即座に発覚する
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: Makefile ターゲットの「存在」をテストしても、実際のコマンド実行は結局手動確認と同等になる
4. **テスト未追加の理由をレビュー可能な形で説明できる**: 本セクションに記載

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定ではなく、既存ツール群のラッパー整備 |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/testing-convention.md | あり | 変更固有検証の実行方法として `make verify-*` を追記 |
| docs/dev/workflow_feature_development.md | あり | Phase 4 の品質チェック手順に `src/` 対象の古い記載あり（`kaji_harness/` に修正）。ただし `make check` への置き換えは行わない（スキルの baseline failure 判定との整合のため） |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | あり | Pre-Commit セクションに `make check` を追記、Essential Commands に make ターゲット一覧を追加 |
| README.md | あり | 品質チェックセクションに `make check` を追記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| テスト規約 | `docs/dev/testing-convention.md` | 変更タイプごとの検証方針（S/M/L と変更固有検証の切り分け）、`pip install -e .` の隔離原則を定義 |
| CLAUDE.md Pre-Commit | `CLAUDE.md` L22-25 | `ruff check && ruff format && mypy && pytest` の 4 コマンドが pre-commit gate。`make check` はこれと 1:1 対応させる |
| Issue #107 | `https://github.com/apokamo/kaji/issues/107` | docs-only/metadata-only/packaging-only 変更で機械的に S/M/L 全要求しない規約変更の経緯 |
| GNU Make Manual | `https://www.gnu.org/software/make/manual/make.html` | `.PHONY` ターゲット、変数定義、依存関係の記法。Makefile 設計の標準参照 |
| pyproject.toml | `pyproject.toml` | `requires-python = ">=3.11"`、dev 依存、entry point `kaji` の定義。verify-packaging で確認する対象 |
| scripts/check_doc_links.py | `scripts/check_doc_links.py` | 既存のリンクチェッカー。`make verify-docs` から呼び出す対象 |
