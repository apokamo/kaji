# [設計] PyPI publish workflow

Issue: #270

## 概要

kaji を PyPI に公開し、利用者が `uv tool install kaji` で導入できる状態にする。
publish は GitHub Actions + PyPI Trusted Publisher を正とし、workflow filename は
`.github/workflows/publish-pypi.yml` に固定する。

## 背景・目的

現在の README は Git URL からの install を正としており、PyPI 公開後に
`uv tool install kaji` へ差し替える前提が残っている。英語圏の利用者が repository URL
を知らずに kaji を試せるようにするには、PyPI の project metadata、README 表示、
配布物、publish 手順をまとめて整備する必要がある。

ユースケース:

- kaji を試す開発者として、GitHub repository URL を調べずに `uv tool install kaji` を実行したい。
- maintainer として、`/release` で tag / GitHub Release を作成した後、GitHub Actions の
  environment approval を経て PyPI publish したい。
- maintainer として、長寿命 PyPI API token を local 端末、`.pypirc`、GitHub Secrets に保存せずに
  publish したい。

代替案:

- `pypi_publish.yml`: 意味は通るが、既存の GitHub Actions の慣例ではハイフン区切りの
  filename の方が読みやすい。PyPI の Trusted Publisher 設定で workflow filename を厳密に
  入力するため、視認性を優先して不採用。
- `release.yml`: uv 公式例に近いが、kaji では `/release` skill が既に tag / GitHub Release
  作成を担う。PyPI publish 専用 workflow であることが曖昧になるため不採用。
- `publish.yml`: 短いが publish 先が曖昧。将来 npm / container 等の publish が増えた場合に
  衝突しやすいため不採用。

## インターフェース

### 入力

- GitHub Release: `published` event を publish workflow の起動条件にする。
- GitHub environment: `pypi`
  - repository の Environments で作成し、approval rule を設定する。
- PyPI Pending Trusted Publisher:
  - project name: `kaji`
  - owner: `apokamo`
  - repository: `kaji`
  - workflow filename: `publish-pypi.yml`
  - environment: `pypi`
- Project metadata:
  - `[project.urls]`: `Homepage`, `Repository`, `Issues`, `Changelog`
  - `authors` / `maintainers`: `apokamo`

### 出力

- `.github/workflows/publish-pypi.yml`
  - `id-token: write`
  - `contents: read`
  - `environment: pypi`
  - `uv build --no-sources`
  - `uvx twine check --strict dist/*`
  - isolated wheel CLI smoke test
  - `uv publish`
- `pyproject.toml` の PyPI project metadata
- `README.md` / `README.ja.md` の Install 節
- `.claude/skills/release/SKILL.md` / `docs/operations/release/runbook.md` の publish 手順

### 使用例

```text
maintainer:
  /release
  # release skill creates and pushes vX.Y.Z and GitHub Release

GitHub Actions:
  .github/workflows/publish-pypi.yml runs when GitHub Release vX.Y.Z is published
  waits for environment pypi approval
  builds dist
  checks README/package metadata
  verifies wheel CLI smoke test
  publishes with uv publish via Trusted Publisher
```

### エラー

- PyPI Pending Trusted Publisher の workflow filename / environment / owner / repository が
  workflow と一致しない場合、OIDC token exchange または publish が失敗する。
- GitHub environment `pypi` が未作成の場合、deployment approval の前提が成立しない。
- `twine check --strict dist/*` が失敗する場合、README rendering / metadata 修正後に再実行する。
- wheel install / `kaji --help` が失敗する場合、packaging metadata または package data を修正する。
- 同一 version / filename が既に PyPI に存在する場合、同一 artifact の差し替えはしない。

## 制約・前提条件

- PyPI account `apokamo` の email verification、2FA、recovery codes 保管は maintainer の
  手動前提であり、repository 変更では自動化しない。
- 初回 publish 前に PyPI 側で Pending Trusted Publisher を作成する必要がある。
- Pending Trusted Publisher は package name を予約しないため、設定後は初回 publish まで速やかに進める。
- 通常運用では PyPI API token を使わない。API token は emergency fallback としてのみ docs に残す。
- 本 Issue では package を実際に PyPI へ公開する最終操作は GitHub Actions / PyPI 管理画面権限に依存する。
  repository 側では publish 可能な workflow と手順、事前検証を整備する。

## 方針

1. Issue 本文の workflow filename を `publish-pypi.yml` に固定する。
2. `pyproject.toml` に `maintainers` と `[project.urls]` を追加する。
3. `README.md` / `README.ja.md` の Install 節を PyPI install 正に更新し、Git URL install は
   development / unreleased fallback として短く残す。
4. `.github/workflows/publish-pypi.yml` を追加する。
   - `on.release.types: [published]`
   - job-level `environment: pypi`
   - job-level `permissions.id-token: write` と `permissions.contents: read`
   - `astral-sh/setup-uv` で uv を入れる
   - `uv build --no-sources`
   - `uvx twine check --strict dist/*`
   - isolated `uv tool install --from dist/*.whl kaji && kaji --help`
   - `uv publish`
5. release skill と runbook を更新する。
   - `/release` は引き続き main worktree で version bump / CHANGELOG / tag / GitHub Release 作成を担う。
   - PyPI publish は GitHub Release publish 後の `publish-pypi.yml` と environment approval が担う。
   - dry-run は PyPI publish を起動しない。
   - emergency fallback の API token 手順は「通常運用では使わない」扱いで、token を docs / Issue /
     shell history / repo 内に残さない注意を明記する。

## テスト戦略

### 変更タイプ

- metadata-only
- packaging-only
- CI workflow
- docs update

実行時 Python ロジックは変更しない。

### docs-only / metadata-only / packaging-only の場合

#### 変更固有検証

- `source .venv/bin/activate && make check`
- `make verify-packaging`
- `rm -rf dist && uv build --no-sources`
- `uvx twine check --strict dist/*`
- isolated wheel smoke test:

```bash
tmpdir="$(mktemp -d)"
uv tool install --from dist/kaji-*.whl --install-dir "$tmpdir/bin" kaji
"$tmpdir/bin/kaji" --help
```

GitHub Actions 自体の Trusted Publisher publish は、repository に merge され、PyPI 側の Pending
Trusted Publisher と GitHub environment `pypi` が設定された後で初回 GitHub Release publish により確認する。
AI 実装フェーズでは workflow YAML の静的整合と local packaging 検証までを対象にする。

#### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件に照らし、恒久テストは追加しない。

1. 実行時ロジックの追加・変更を含まない。
2. package metadata / README render / entry point は `make verify-packaging`、`uv build`、
   `twine check`、既存 CLI tests で検証できる。
3. Trusted Publisher の成否は PyPI / GitHub environment 設定に依存し、local unit test にしても
   新しい回帰検出情報がほとんど増えない。
4. 本設計書と実装報告に、local packaging 検証と初回 publish 時の手動確認範囲を記録する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規 ADR が必要な architecture decision ではなく、既存 release 運用の拡張 |
| docs/ARCHITECTURE.md | なし | runtime architecture は変えない |
| docs/dev/ | なし | dev workflow 自体は変えない |
| docs/reference/ | なし | Python 規約や CLI reference は変えない |
| docs/cli-guides/ | なし | CLI interface は変えない |
| docs/operations/release/runbook.md | あり | PyPI publish 手順と運用責務を追加する |
| .claude/skills/release/SKILL.md | あり | `/release` 後の publish handoff と完了報告を追加する |
| README.md / README.ja.md | あり | Install 節を PyPI install 正へ更新する |
| AGENTS.md / CLAUDE.md | なし | 開発規約そのものは変えない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| PyPI Docs: Publishing with a Trusted Publisher | https://docs.pypi.org/trusted-publishers/using-a-publisher/ | GitHub Actions で OIDC token を使うには `id-token: write` が必要。PyPI は OIDC token を短命 API token に交換して publish する。 |
| GitHub Docs: Configuring OpenID Connect in PyPI | https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-pypi | PyPI trust configuration は owner / repository / workflow / environment を一致させる。誤設定は API token 共有と同等のリスク。 |
| uv Docs: Using uv in GitHub Actions | https://docs.astral.sh/uv/guides/integration/github/#publishing-to-pypi | uv 公式例は `environment: pypi`、`id-token: write`、`contents: read`、`uv build`、`uv publish` を使う。 |
| uv Docs: Building and publishing a package | https://docs.astral.sh/uv/guides/package/#publishing-your-package | Trusted Publisher 経由の PyPI publish では credentials を設定せず `uv publish` できる。 |
| Issue #270 | https://github.com/apokamo/kaji/issues/270 | PyPI account / package name / Trusted Publisher 正 / emergency fallback の要件。 |
| testing convention | docs/dev/testing-convention.md | metadata-only / packaging-only 変更では、条件を満たせば恒久回帰テスト追加不要。 |
