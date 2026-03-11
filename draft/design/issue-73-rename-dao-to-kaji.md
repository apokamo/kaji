# [設計] dao → kaji リネーム

Issue: #73

## 概要

パッケージ名・CLI コマンド・リポジトリ名を `dao` から `kaji`（舵）に全面リネームする。DAO（Data Access Object）パターンとの名前衝突を解消する。

## 背景・目的

- `dao` は広く知られた Data Access Object パターンの略称であり、本プロジェクトの目的（ワークフローオーケストレーション）と無関係な連想を生む
- `kaji`（舵）は「舵取り＝方向制御」のメタファーで、ワークフローの制御という本質を表現する
- PyPI・GitHub 上で `kaji` / `apokamo/kaji` が未使用であることを確認済み

## インターフェース

### 入力

リネーム対象の全ファイル（後述のスコープ表参照）。

### 出力

| 対象 | Before | After |
|------|--------|-------|
| パッケージディレクトリ | `dao_harness/` | `kaji_harness/` |
| CLI コマンド | `dao` | `kaji` |
| pyproject.toml name | `dev-agent-orchestra` | `kaji` |
| pyproject.toml scripts | `dao = "dao_harness.cli_main:main"` | `kaji = "kaji_harness.cli_main:main"` |
| setuptools packages | `dao_harness*` | `kaji_harness*` |
| GitHub リポジトリ | `apokamo/dev-agent-orchestra` | `apokamo/kaji` |
| Worktree 命名規則 | `dao-[prefix]-[number]` | `kaji-[prefix]-[number]` |

### 使用例

```bash
# Before
dao run workflows/feature-development.yaml 73
dao validate workflows/feature-development.yaml

# After
kaji run workflows/feature-development.yaml 73
kaji validate workflows/feature-development.yaml
```

```python
# Before
from dao_harness.runner import WorkflowRunner
from dao_harness.models import Workflow

# After
from kaji_harness.runner import WorkflowRunner
from kaji_harness.models import Workflow
```

## 制約・前提条件

- **後方互換性は不要**: `dao` コマンドのエイリアス・互換レイヤーは設けない（ユーザーは作者のみ）
- **legacy/ は対象外**: V5/V6 コードはすでに非サポート。参照先も存在しない
- **draft/design/ の過去設計書**: リネーム対象に含める（アーカイブ時に整合性を保つため）
- **workflow YAML 変更済み**: `workflows/feature-development.yaml` の agent/model 調整は事前に完了済み（未コミット）。本 Issue のスコープに含めてコミットする
- **`.dao/` ディレクトリ**: #70 で新設予定のため本 Issue では扱わない（まだ存在しない）

## 方針

### フェーズ1: パッケージリネーム（コード）

1. `git mv dao_harness/ kaji_harness/` でディレクトリリネーム
2. `kaji_harness/` 内の全 `.py` ファイルで `dao_harness` → `kaji_harness` を置換
3. `tests/` 内の全 `.py` ファイルで `dao_harness` → `kaji_harness` を置換
4. `pyproject.toml` を更新:
   - `name = "kaji"`
   - `dao = "dao_harness.cli_main:main"` → `kaji = "kaji_harness.cli_main:main"`
   - `include = ["dao_harness*"]` → `include = ["kaji_harness*"]`
5. `pip install -e ".[dev]"` で再インストール

### フェーズ2: ドキュメント・スキル定義

1. `CLAUDE.md`: `dao_harness` → `kaji_harness`、`dao` CLI → `kaji` CLI
2. `README.md`: プロジェクト名・使用例
3. `docs/` 配下: CLI コマンド例、パッケージ参照
4. `.claude/skills/`: Worktree 命名規則 `dao-` → `kaji-`
5. `draft/design/` 配下の過去設計書: `dao` 参照を `kaji` に更新

### フェーズ3: 品質検証

```bash
source .venv/bin/activate
ruff check kaji_harness/ tests/ && ruff format kaji_harness/ tests/ && mypy kaji_harness/ && pytest
```

全チェック通過を確認。

### フェーズ4: コミット・プッシュ

1. workflow YAML 変更を含めてコミット
2. パッケージリネームをコミット
3. ドキュメント更新をコミット
4. push

### フェーズ5: リポジトリリネーム（GitHub）

1. `gh repo rename kaji`
2. ローカル bare repository の再 clone（Issue 本文の手順に従う）

### 置換対象の網羅的リスト

| カテゴリ | ファイル数 | 置換パターン |
|----------|-----------|-------------|
| パッケージソース (`kaji_harness/`) | 13 | `dao_harness` → `kaji_harness` |
| テスト (`tests/`) | 21 | `dao_harness` → `kaji_harness` |
| pyproject.toml | 1 | name, scripts, packages |
| CLAUDE.md | 1 | `dao_harness` → `kaji_harness`, `dao` CLI → `kaji` |
| README.md | 1 | プロジェクト名、CLI 例 |
| docs/ | ~8 | CLI 例、パッケージ参照 |
| .claude/skills/ | 3 | Worktree 命名規則 `dao-` → `kaji-` |
| draft/design/ | 5 | `dao` 参照 → `kaji` |

### 注意事項: 部分一致の回避

- `dao` の単純置換は `document` 等に誤マッチしない（`dao` は独立トークンとして出現）
- ただし `dao_harness` は先に置換し、残った `dao` CLI 参照を個別に処理する
- 正規表現 `\bdao\b` で境界マッチを使用し、誤置換を防止する

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

本変更はリファクタリング（リネーム）であり、新規ロジックの追加はない。既存テストスイートがそのままリグレッションテストとして機能する。

### Small テスト
- **既存テストの通過確認**: `tests/` 内の全 Small テスト（`@pytest.mark.small`）が `kaji_harness` import で正常に動作すること
- 対象: バリデーション、パーサー、モデル、プロンプトビルダー等の単体テスト
- 新規テスト追加: 不要（ロジック変更なし）

### Medium テスト
- **既存テストの通過確認**: 全 Medium テスト（`@pytest.mark.medium`）が新しいパッケージ名・CLI 名で動作すること
- 対象: CLI 引数パース、ワークフロー実行、ロギング統合、セッション状態永続化等
- 特に注意: CLI エントリーポイントが `kaji` コマンドとして動作すること

### Large テスト
- **既存テストの通過確認**: 全 Large テスト（`@pytest.mark.large`）が E2E で動作すること
- 対象: `test_e2e_cli.py` — 実際の CLI 呼び出しが `kaji` コマンドで動作すること
- CLI のサブプロセス呼び出しパスが正しく解決されることを検証

### スキップするサイズ
- なし（全サイズとも既存テストで検証可能）

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | ADR 003 に `dao_harness` パッケージ参照あり |
| docs/ARCHITECTURE.md | あり | `dao` CLI・パッケージへの参照あり |
| docs/dev/ | あり | development_workflow.md の Worktree 命名規則、workflow-authoring.md の CLI 例 |
| docs/cli-guides/ | 確認要 | 存在すれば `dao` CLI 参照を更新 |
| CLAUDE.md | あり | pre-commit コマンド、CLI 使用例 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #73 本文 | `gh issue view 73` | リネームスコープ、作業手順、注意事項の定義元 |
| pyproject.toml | `./pyproject.toml` | 現行の CLI エントリーポイント定義: `dao = "dao_harness.cli_main:main"` |
| setuptools find_packages | https://setuptools.pypa.io/en/latest/userguide/package_discovery.html | `[tool.setuptools.packages.find]` の `include` パターンが `kaji_harness*` へ変更可能であることの根拠 |
| gh repo rename | https://cli.github.com/manual/gh_repo_rename | `gh repo rename kaji` でリポジトリ名を変更。GitHub が自動リダイレクトを設定する |
| PyPI name availability | https://pypi.org/project/kaji/ | `kaji` が PyPI 上で未使用であることの確認（Issue 本文に記載） |
