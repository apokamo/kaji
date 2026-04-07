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

## 方針

全修正をファイル種別で 4 カテゴリに分類し、順に対応する。

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

- **実行に影響するコード変更**: `Makefile` の setup ターゲット、`scripts/verify-packaging.sh`
- **docs-only**: その他全ファイル

実行に影響する変更は 2 ファイルのみで、いずれもシェルコマンドの置換であり、新規ロジックの追加ではない。

### 変更固有検証

- `uv sync` を実行し、`.venv` にパッケージがインストールされることを確認
- `make check` が通ることを確認（lint → format → typecheck → test）
- `make verify-packaging` が uv ベースで動作することを確認
- `make verify-docs` でドキュメントのリンク整合を確認

### 恒久テストを追加しない理由

1. **独自ロジックの追加・変更をほぼ含まない**: シェルコマンドの置換のみ
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**: `make check` と `make verify-packaging` が既存ゲートとして機能
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: pip → uv の置換は一度完了すれば回帰しない性質
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
| uv 公式ドキュメント: Getting Started | https://docs.astral.sh/uv/getting-started/features/ | `uv sync` は pyproject.toml と uv.lock に基づき依存を解決・インストールし、.venv を自動作成する |
| uv 公式ドキュメント: pip interface | https://docs.astral.sh/uv/pip/ | `uv pip install` は pip 互換のインターフェースを提供し、隔離環境での検証に使用可能 |
| uv 公式ドキュメント: uv venv | https://docs.astral.sh/uv/pip/environments/ | `uv venv` は `python -m venv` の代替として高速に仮想環境を作成する |
| 既存 uv.lock | `uv.lock`（リポジトリルート） | uv.lock が既に存在し、依存関係が解決済みであることが uv 移行の前提 |
| pyproject.toml | `pyproject.toml`（リポジトリルート） | build-backend は setuptools のまま維持。`uv sync` は setuptools backend と互換 |
