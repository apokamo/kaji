# [設計] パッケージ管理を pip から uv に統一する

Issue: #128

## 概要

Makefile・ドキュメント・スキルファイルのセットアップ手順を pip/venv ベースから uv ベースに統一する。

## 背景・目的

uv.lock は既に存在し、実態として uv が使える状態にあるが、Makefile・ドキュメント・スキルファイルの記述が pip/venv のまま残っている。この乖離が AI エージェント・人間双方の作業にブレを生むため、記述を実態に合わせて uv に統一する。

## インターフェース

### 入力

変更対象ファイル群（後述の修正対象一覧）。

### 出力

- `uv sync` でセットアップが完結する状態
- 全ドキュメント・スキルの pip/venv 記述が uv に統一された状態

### 使用例

```bash
# 変更前
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 変更後
uv sync
source .venv/bin/activate
```

## 制約・前提条件

- build-backend は setuptools のまま変更しない（pyproject.toml の `[build-system]` は触らない）
- `.venv` シンボリックリンク（worktree 共有）の仕組みは変更不要
- CI/CD の変更なし（現時点で GitHub Actions なし）
- `draft/` 配下の過去設計書アーカイブは修正スコープ外

## dev 依存の扱い: optional-dependencies → dependency-groups 移行

### 問題

現行 `pyproject.toml` では開発依存が `[project.optional-dependencies].dev` に定義されている（25-34 行）。
plain `uv sync` は optional-dependencies（extras）をデフォルトでインストールしない。
このため、`uv sync` 単体では `pip install -e ".[dev]"` 相当にならない。

### 選択肢

| 選択肢 | コマンド | pyproject.toml 変更 | 評価 |
|--------|---------|---------------------|------|
| A: `--extra` フラグ | `uv sync --extra dev` | 不要 | Issue の「`uv sync` で完結」に反する |
| B: dependency-groups 移行 | `uv sync` | `[project.optional-dependencies].dev` → `[dependency-groups].dev` | uv の推奨パターン。`uv sync` で dev グループが自動同期される |

### 採用: B（dependency-groups 移行）

**理由**:

1. uv は `[dependency-groups].dev` をデフォルトで同期する（`--no-default-groups` で除外可能）
2. 開発依存（pytest, ruff, mypy 等）は「ローカル開発専用の非公開依存」であり、
   公開メタデータとして配布される optional-dependencies より dependency-groups が意味的に正確
3. Issue の完了条件「`uv sync` でセットアップが完結する」を満たす
4. build-backend（setuptools）は変更しない。`[dependency-groups]` は PEP 735 で定義された
   ビルドシステム非依存の仕様であり、`[build-system]` には影響しない

### pyproject.toml の変更内容

```python
# 削除
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    ...
]

# 追加
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    ...
]
```

依存パッケージの一覧自体は変更しない。セクション名のみ移行する。

## 方針

全修正をファイル種別で 4 カテゴリに分類し、順に対応する。

### 0. pyproject.toml の依存定義移行

| ファイル | 現状 | 修正後 |
|---------|------|--------|
| `pyproject.toml` | `[project.optional-dependencies].dev` | `[dependency-groups].dev` |

### 1. 実行に影響する修正

| ファイル | 現状 | 修正後 |
|---------|------|--------|
| `Makefile` setup ターゲット | `pip install -e ".[dev]"` | `uv sync` |
| `scripts/verify-packaging.sh` | `python3 -m venv` + `pip install` | `uv venv` + `uv pip install` |

### 2. ドキュメント修正

| ファイル | 修正概要 |
|---------|----------|
| `CLAUDE.md` セットアップ手順 | `python -m venv .venv` + `make setup` → `uv sync` |
| `CLAUDE.md` verify-packaging 説明 | pip → uv |
| `README.md` セットアップ手順 | 同上 |
| `README.md` verify-packaging 説明 | 同上 |
| `docs/dev/testing-convention.md` | `pip install -e .` の扱いセクションを uv 前提に書き換え |
| `docs/guides/git-worktree.md` | .venv 共有の警告文で pip → uv |
| `docs/dev/workflow_feature_development.md` | セットアップ手順を uv に更新 |

### 3. スキルファイル修正

| ファイル | 修正概要 |
|---------|----------|
| `.claude/skills/issue-start/SKILL.md` | fallback の venv 作成コマンドを `uv venv` + `uv sync` に |
| `.claude/skills/issue-implement/SKILL.md` | 禁止事項の `pip install -e .` → `uv pip install -e .` |

### 4. Serena 設定

| ファイル | 修正概要 |
|---------|----------|
| `.serena/memories/suggested_commands.md` | セットアップ手順を uv に |
| `.serena/memories/task_completion.md` | venv activate 手順を更新 |

**注意**: Serena 設定ファイルが存在しない場合は、Issue に存在しない旨をコメントしスキップする。

### 修正方針の要点

- 単純な文字列置換ではなく、各ファイルの文脈に合わせて記述を更新する
- `uv sync` は `.venv` を自動作成するため、`python -m venv .venv` のステップは削除できる
- `source .venv/bin/activate` は引き続き必要（シェル環境の PATH 設定のため）

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

- **packaging-only**: `pyproject.toml`（dependency-groups 移行）、`Makefile`（setup ターゲット）、`scripts/verify-packaging.sh`
- **docs-only**: その他全ファイル（CLAUDE.md, README.md, docs/*, スキルファイル, Serena 設定）

`Makefile` の setup ターゲットと `scripts/verify-packaging.sh` はビルド・セットアップ基盤であり、
kaji アプリケーションの実行時ロジック（`kaji_harness/` 配下の Python コード）には変更を加えない。
シェルコマンドの pip → uv 置換のみであり、新規の Python ロジック追加は一切ない。

### Small / Medium / Large の検証観点

本変更は packaging-only + docs-only であり、`kaji_harness/` 配下の実行時コードに変更はない。
各サイズの恒久テストを追加しない理由をサイズ別に記載する。

#### Small テスト（恒久テスト追加なし）

- 変更対象はシェルスクリプト・Makefile・ドキュメントのみ。pytest で検証可能な Python ロジックの追加・変更がない
- 既存の Small テストは kaji_harness の単体ロジックを検証しており、本変更の回帰は検出対象外

#### Medium テスト（恒久テスト追加なし）

- ファイル I/O・DB・内部サービス結合に関わる変更がない
- `scripts/verify-packaging.sh` は隔離環境でのパッケージインストールを行うが、
  これ自体が packaging 検証ツールであり、テスト対象ではなく検証手段である

#### Large テスト（恒久テスト追加なし）

- 実 API 疎通・E2E データフローに関わる変更がない
- `uv sync` → `make check` の実行が E2E 検証に相当するが、
  これは CI/開発フローで常時実行されるゲートであり、専用の Large テストを新設する価値がない

### 変更固有検証

- `uv sync` を実行し、`.venv` に dev 依存（pytest, ruff, mypy 等）がインストールされることを確認
- `make check` が通ることを確認（lint → format → typecheck → test）
- `make verify-packaging` が uv ベースで動作することを確認
- `make verify-docs` でドキュメントのリンク整合を確認

### 恒久テストを追加しない理由（4 条件）

1. **独自ロジックの追加・変更をほぼ含まない**: シェルコマンドの置換と pyproject.toml セクション名の変更のみ
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**: `make check`（既存テスト全量実行）と `make verify-packaging`（隔離インストール検証）が既存ゲートとして機能
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: pip → uv の置換は一度完了すれば回帰しない性質。dependency-groups 移行も同様
4. **テスト未追加の理由をレビュー可能な形で説明できる**: 上記の通り

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定ではない（uv は既に導入済み） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | あり | testing-convention.md, workflow_feature_development.md が修正対象 |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | あり | セットアップ手順・verify-packaging 説明が修正対象 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| uv 公式ドキュメント: Dependencies | https://docs.astral.sh/uv/concepts/projects/dependencies/ | `uv sync` は `[dependency-groups].dev` をデフォルトで同期する。optional-dependencies はデフォルト同期されない。`tool.uv.default-groups` で制御可能 |
| uv 公式ドキュメント: Getting Started | https://docs.astral.sh/uv/getting-started/features/ | `uv sync` は pyproject.toml と uv.lock に基づき依存を解決・インストールし、.venv を自動作成する |
| uv 公式ドキュメント: pip interface | https://docs.astral.sh/uv/pip/ | `uv pip install` は pip 互換のインターフェースを提供し、隔離環境での検証に使用可能 |
| uv 公式ドキュメント: uv venv | https://docs.astral.sh/uv/pip/environments/ | `uv venv` は `python -m venv` の代替として高速に仮想環境を作成する |
| 既存 uv.lock | `uv.lock`（リポジトリルート） | uv.lock が既に存在し、依存関係が解決済みであることが uv 移行の前提 |
| pyproject.toml | `pyproject.toml`（リポジトリルート） | build-backend は setuptools のまま維持。`uv sync` は setuptools backend と互換 |
