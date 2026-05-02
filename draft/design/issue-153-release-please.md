# [設計] release-please 導入によるリリース自動化（kamo2 実装の移植）

Issue: #153

## 概要

kaji リポジトリに release-please を導入し、main マージ起点で Release PR を自動生成・更新、merge により version bump / CHANGELOG 更新 / tag 付与 / GitHub Release 公開までを完結させる。kamo2 (Issue #958) の実装を kaji 用にスコープ調整して移植する。

## 背景・目的

### 現状の問題

| 観点 | 現状 | 1 次情報 |
|------|------|---------|
| version 採番 | 手動。`pyproject.toml` を直接編集し tag を付与 | `pyproject.toml:3` = `0.9.1`、`git tag --list` = `v0.8.0..v0.9.1` |
| CHANGELOG | 不在。GitHub Release も無い | repo に `CHANGELOG.md` 無し |
| version SoT | 破綻。`__init__.py` の `0.1.0` と `pyproject.toml` の `0.9.1` が乖離 | `kaji_harness/__init__.py:3` |
| リリース手順 | 暗黙知。`docs/operations/release/` 自体が存在しない | `ls docs/operations/` |
| CI | `.github/workflows/` 自体が存在しない | `ls .github/` |

`kaji_harness/cli_main.py:10` は既に `importlib.metadata.version("kaji")` を使用済み。`__init__.py` の `__version__` は dead code（grep の結果、外部参照ゼロ）。

### ユーザーストーリー

- **Maintainer (apokamo) として**、リリース運用の再現性を確保するために、main へのマージで Release PR が自動生成・更新され、それを merge するだけで version bump / CHANGELOG / tag / GitHub Release が完結する状態にしたい。
- **Contributor として**、kaji の変化を追跡するために、CHANGELOG.md と GitHub Releases から conventional commits ベースの構造化された差分を参照したい。
- **新規参入者 / 引き継ぎ受け手として**、属人知に頼らずリリース運用ができるよう、`docs/operations/release/admin-setup.md`（初回セットアップ）と `runbook.md`（通常運用）を読めば独力で作業できる状態にしたい。

### 設計判断のサマリ

| 論点 | 決定 | 理由 |
|------|------|------|
| トークン方式 | GitHub App | 監査・rotation 容易。Release PR 上で後続 workflow が発火する利点。kamo2 と同方式 |
| App | 専用に新規作成 (`kaji-release-please`) | install / permission を repo 単位で分離 |
| `__version__` | 削除（importlib.metadata に統一） | SoT を `pyproject.toml` に一元化。`cli_main.py` は既に該当 API 利用、外部参照ゼロ |
| bootstrap | manifest = `{".": "0.9.1"}` | 既存 tag からの採番継続 |
| changelog-sections | kamo2 と同形 | 設定共通化により運用知見を共有。docs commit のノイズは初回のみ |
| dry-run | 初手から導入（`chore/release-please-dryrun` ブランチ） | merge 前に挙動を検証可能 |

## インターフェース

本 Issue は CLI / API ではなく **GitHub Actions ワークフロー + JSON 設定 + 運用 docs** を成果物とする。インターフェースは「リリース運用の振る舞い契約」として記述する。

### 入力

- **トリガー (`release-please.yml`)**:
  - `push: branches: [main]`（通常運用）
  - `workflow_dispatch` (input: `target-branch`, default `main`、post-merge ad-hoc 再実行用)
- **トリガー (`release-please-lock.yml`)**:
  - `pull_request: types: [opened, synchronize, reopened]`、head_ref が `release-please--branches--` で始まる PR のみ実行
- **必要 secret**: `RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY`
- **必要 repo 設定**: Settings → Actions → General で「Allow GHA to create and approve PRs」+ Workflow permissions「Read and write」
- **設定ファイル**:
  - `.github/release-please-config.json` — release-type / changelog-sections / packages
  - `.release-please-manifest.json` — `{".": "0.9.1"}`
- **commits**: Conventional Commits 形式（`feat:` / `fix:` / `docs:` 等）

### 出力

- main への push 後、release-please-action が Release PR (`release-please--branches--main`) を生成・更新
- Release PR は `pyproject.toml` の version, `CHANGELOG.md`, `.release-please-manifest.json` を更新
- Release PR merge 後、tag (`vX.Y.Z`) と GitHub Release が自動公開
- `release-please-lock.yml` は Release PR への commit 後に `uv.lock` を pyproject.toml へ追従させる

### 使用例

```bash
# 開発者の通常フロー（変更なし）
git switch -c feat/foo && ... && gh pr create
# → main マージ後、release-please-action が自動的に Release PR を生成 / 更新

# Maintainer のリリースフロー
gh pr review release-please--branches--main --approve
gh pr merge release-please--branches--main --merge
# → tag (vX.Y.Z) + GitHub Release 自動公開

# dry-run（初回検証 / 変更時の挙動確認）
git switch -c chore/release-please-dryrun
git push origin chore/release-please-dryrun
# → workflow が target-branch=chore/release-please-dryrun として起動
```

### エラー

| 失敗ケース | 挙動 |
|-----------|------|
| secret 未登録 | `actions/create-github-app-token@v1` が失敗。job log にエラー、Release PR は作られない |
| Repo 設定（PR 作成許可）未設定 | release-please-action が PR 作成権限エラーで失敗 |
| `uv sync --locked` 失敗 | `release-please-lock.yml` が `uv lock` を実行し、差分があれば追加 commit を push（`continue-on-error: true` で 1 段受け止める） |
| Conventional Commits 違反 | release-please-action は該当 commit を CHANGELOG に含めずスキップ。バージョン bump も発生しない |
| 既存 tag との不整合 | manifest = `{".": "0.9.1"}` を SoT として使用するため、release-please は manifest 値を起点に採番 |

## 制約・前提条件

- Python 3.11+（`importlib.metadata` 標準利用、kaji の `requires-python = ">=3.11"`）
- `pyproject.toml` の `version` フィールドが SoT（release-type: python が直接更新）
- `.github/` ディレクトリは現状空。新規にツリーを構築する
- GitHub App の作成・install・secret 登録は **admin 権限ユーザー (apokamo)** の手作業。本 PR の範囲外（admin-setup.md に手順を記載）
- `--no-ff` merge 必須・squash merge 禁止という既存規約と整合（release-please は merge commit ベースで動作）
- 既存 tag (`v0.8.0..v0.9.1`) は手動付与のため、release-please の認識上は manifest が真。bootstrap 時に既存 tag は無視されるが、削除はしない

## 変更スコープ

新規:

- `.github/release-please-config.json`
- `.release-please-manifest.json`
- `.github/workflows/release-please.yml`
- `.github/workflows/release-please-lock.yml`
- `docs/operations/release/admin-setup.md`
- `docs/operations/release/runbook.md`

変更:

- `kaji_harness/__init__.py` — `__version__ = "0.1.0"` を削除（モジュール docstring は維持）
- `docs/README.md` — Documentation インデックスに `docs/operations/release/` セクション追加
- `CLAUDE.md` — Documentation 表に admin-setup.md / runbook.md 行追加

scope 外（明示）:

- PyPI publish 自動化、commitlint / PR title lint、`labels.yml` 追加、CI workflow 新規追加（`autorelease: *` ラベルは release-please が自動作成するため不要）

## 方針（Minimal How）

### `.github/release-please-config.json`

kamo2 設定をベースに `extra-files` ブロックを除いた最小構成。

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "release-type": "python",
  "include-component-in-tag": false,
  "include-v-in-tag": true,
  "bump-minor-pre-major": false,
  "bump-patch-for-minor-pre-major": false,
  "changelog-sections": [ /* feat / fix / perf / refactor / docs / chore / test / build / ci, kamo2 と同形 */ ],
  "packages": {
    ".": {
      "package-name": "kaji",
      "release-type": "python"
    }
  }
}
```

`bump-minor-pre-major: false` により、0.x.y のままでも `feat:` で minor、`fix:` で patch を上げる。kaji は GA 前だが kamo2 と挙動を揃える。

### `.release-please-manifest.json`

```json
{ ".": "0.9.1" }
```

既存 tag `v0.9.1` から採番継続。初回 Release PR は `v0.9.1..HEAD` の commits（feat 起因）から **0.10.0** を提案する見込み。

### `.github/workflows/release-please.yml`

kamo2 の同名 workflow を kaji 用にコメント書き換え（kamo2 Issue #958 への参照を本 Issue #153 に置換）。構造:

1. `actions/create-github-app-token@v1` で installation token を発行
2. `googleapis/release-please-action@v4` に token / config-file / manifest-file / target-branch を渡す

トリガー: `push: branches: [main]` + `workflow_dispatch` (`target-branch` input)。

### `.github/workflows/release-please-lock.yml`

kamo2 の同名 workflow をそのまま移植。

1. PR head_ref が `release-please--branches--` で始まるか判定
2. App token で checkout
3. `uv sync --locked` を試行 → 失敗時 `uv lock` 実行 → `uv.lock` 差分があれば追加 commit を push
4. App slug / bot user ID で commit author を設定

### `kaji_harness/__init__.py`

```python
"""kaji_harness - AI-driven development workflow orchestrator."""
```

`__version__ = "0.1.0"` を削除。参照元の `cli_main.py:10,34-37,48` は `importlib.metadata.version("kaji")` を使用済みのため、影響なし。

### docs

- `admin-setup.md`: kamo2 を kaji 用に書き換え。GitHub App 作成手順、secret 登録（`gh secret set ...`）、Repo Settings 有効化、dry-run 実施手順、cleanup を網羅。kamo2 の Option B (PAT) 記載は維持するが、現行 workflow が App 前提である旨を明記
- `runbook.md`: kamo2 を kaji 用に書き換え。通常運用（Release PR merge → tag / Release 自動生成）、トラブルシュート（CHANGELOG 空 / lock 同期失敗 / merge conflict）、ad-hoc 実行（workflow_dispatch）

### dry-run 手順（admin-setup.md に記載予定）

1. `chore/release-please-dryrun` ブランチに本 Issue の変更を含めて push
2. release-please-action が Release PR (`release-please--branches--chore-release-please-dryrun`) を生成
3. Release PR で:
   - 提案 version = **0.10.0**（feat 起因の minor bump）
   - CHANGELOG.md に `v0.9.1..HEAD` の 36 commits が構造化されて表示
   - `release-please-lock.yml` が起動し `uv.lock` 同期 or `in_sync` でスキップ
4. cleanup: dry-run PR / branch / 万一作られた tag を削除

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

混在変更:

- **metadata-only / packaging-only**: `__init__.py` の `__version__` 削除（dead code 削除、振る舞い変化なし）
- **CI / 設定 / docs 追加**: workflow YAML、release-please 設定 JSON、運用 docs

実行時コードの振る舞いを変える変更は含まない。

### 実行時コード変更の場合

該当なし。`__version__` 削除は dead code 除去であり、`cli_main.py` は既に `importlib.metadata` 経由の参照に切り替わっているため、利用者から観測される `kaji --version` の挙動は変わらない。

### docs-only / metadata-only / packaging-only の場合

#### 変更固有検証

| 検証項目 | 手段 |
|---------|------|
| `__version__` 削除後も `kaji --version` が `0.9.1` を返す | 隔離環境で `uv venv && uv pip install -e . && kaji --version` |
| `release-please-config.json` の構文・スキーマ整合 | `python -c "import json; json.load(open('.github/release-please-config.json'))"`、`$schema` URL（release-please 公式）でレビュー時の目視整合 |
| `.release-please-manifest.json` の構文整合 | `python -m json.tool < .release-please-manifest.json` |
| workflow YAML の構文整合 | `actionlint` 相当の目視レビュー（kaji に CI が無いため自動化不可）。kamo2 の正常稼働実績を移植のベースとする |
| docs リンク整合 | `make verify-docs` |
| 全体品質ゲート | `make check`（`__init__.py` 編集に対する ruff / mypy / pytest 通過確認） |
| **動作検証（最重要）** | **dry-run**: `chore/release-please-dryrun` ブランチへの push で Release PR が `0.10.0` を提案し、CHANGELOG が構造化、`uv.lock` 同期が動作することを実 GitHub Actions 上で確認 |

#### 恒久テストを追加しない理由

[testing-convention.md](../../docs/dev/testing-convention.md) の 4 条件に基づく:

1. **独自ロジックを追加しない**: ロジックは release-please-action と uv に委譲。kaji 内部にはコード追加なし（`__version__` は削除のみ）
2. **既存テスト/品質ゲートで捕捉済み**: `__version__` 削除の影響は `make check` の pytest（CLI バージョン取得経路を含む）で捕捉。設定ファイルは静的構造のためレビュー + dry-run で十分
3. **新規テストの回帰検出情報が増えない**: workflow 自体の動作は GitHub Actions ランタイムに依存し、ローカル単体テストでは再現できない
4. **理由が説明可能**: 上記 1〜3 を本セクションで明示

dry-run は変更固有の一時検証であり、repo に恒久化しない（kamo2 でも同方針）。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/adr/` | なし | release-please 採用方針は kamo2 ADR-015 と同じ。kaji 側で新規 ADR は追加せず、本 Issue の設計書 + admin-setup.md で根拠を残す |
| `docs/ARCHITECTURE.md` | なし | アプリケーションのアーキテクチャに変更なし |
| `docs/dev/` | なし | 開発ワークフロー（design / implement / review）には影響しない |
| `docs/reference/` | なし | API / 規約変更なし |
| `docs/cli-guides/` | なし | CLI 仕様（`kaji run` / `kaji validate`）変更なし。`kaji --version` の挙動は `__version__` 削除後も変わらない |
| `docs/operations/release/` | **新規** | admin-setup.md / runbook.md を新規作成 |
| `docs/README.md` | あり | Documentation インデックスに `docs/operations/release/` 行を追加 |
| `CLAUDE.md` | あり | Documentation 表に admin-setup.md / runbook.md 行を追加 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|---------|-------------------|
| release-please-action 公式 | https://github.com/googleapis/release-please-action | token / permissions 要件、`config-file` / `manifest-file` / `target-branch` input 仕様。本設計の workflow 構造の根拠 |
| release-please config schema | https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json | `release-type: python` / `include-v-in-tag` / `changelog-sections` / `packages` の構造定義。`.github/release-please-config.json` の `$schema` 参照先 |
| Conventional Commits | https://www.conventionalcommits.org/ | commit prefix 仕様。CHANGELOG 構造化と version bump 判定の根拠 |
| GitHub Docs: Triggering a workflow from a workflow | https://docs.github.com/en/actions/using-workflows/triggering-a-workflow#triggering-a-workflow-from-a-workflow | built-in `GITHUB_TOKEN` で作られた PR では後続 workflow が発火しない。GitHub App 採用の根拠 |
| kamo2 release-please 設定（移植元） | `/home/aki/dev/kamo2/.github/release-please-config.json` | `release-type: python` + `changelog-sections` (feat/fix/perf/refactor/docs/chore/test/build/ci) を移植。`extra-files` は kaji では不要なため除外 |
| kamo2 manifest（移植元） | `/home/aki/dev/kamo2/.release-please-manifest.json` | `{".": "<version>"}` 形式。kaji では `0.9.1` を初期値とする |
| kamo2 release-please.yml（移植元） | `/home/aki/dev/kamo2/.github/workflows/release-please.yml` | `actions/create-github-app-token@v1` → `googleapis/release-please-action@v4` の構造、`push: branches: [main]` + `workflow_dispatch` トリガー |
| kamo2 release-please-lock.yml（移植元） | `/home/aki/dev/kamo2/.github/workflows/release-please-lock.yml` | `release-please--branches--` 前方一致 + `uv sync --locked` 失敗時の `uv lock` 追従 + App slug bot による commit |
| kamo2 admin-setup.md / runbook.md（移植元） | `/home/aki/dev/kamo2/docs/operations/release/{admin-setup,runbook}.md` | 初回セットアップ・通常運用・トラブルシュートの構成 |
| kaji 現状: pyproject.toml | `pyproject.toml:3` (`version = "0.9.1"`) | manifest 初期値 `0.9.1` の根拠 |
| kaji 現状: dead code | `kaji_harness/__init__.py:3` (`__version__ = "0.1.0"`) | 削除対象。SoT を `pyproject.toml` に一元化する根拠 |
| kaji 現状: importlib.metadata 利用 | `kaji_harness/cli_main.py:10` (`from importlib.metadata import PackageNotFoundError, version`) | `__version__` 削除しても `kaji --version` が機能する根拠 |
