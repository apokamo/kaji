# Release Runbook (Release-Please)

kaji のリリース運用 runbook。Release-Please (Issue #153) 導入以降の新フロー。

- **対象**: リリース担当者（Release PR を merge する者）
- **admin 初期設定**: [`admin-setup.md`](./admin-setup.md) を先に完了していること（token 登録・Actions permissions）

## リリースフロー全体像

Release-Please の導入（Issue #153）以降、バージョンバンプ・タグ打鍵・GitHub Release 作成は自動化されている。リリース担当者の操作は **Release PR の merge 1 操作** に圧縮される。

```
[日常]
releasable な PR を main に merge
※ release-type=python のため、CHANGELOG / version bump 対象は Conventional Commits 全般
   （feat: は minor bump、fix:/その他は patch bump、BREAKING CHANGE: は major bump）
    ↓ (release-please が自動起動)
Release PR (vX.Y.Z) が自動生成・更新される
    ↓ (pyproject.toml / CHANGELOG.md / .release-please-manifest.json を自動更新)
uv-lock follow-up workflow が uv.lock を自動同期
    ↓
[リリース時]
リリース担当者が Release PR を merge
    ↓ (release-please が自動実行)
git tag vX.Y.Z + GitHub Release 公開 + manifest 更新
```

## バージョン管理対象

| ファイル | 用途 | 更新主体 |
|---------|------|----------|
| `pyproject.toml` | Python パッケージバージョン（**source of truth**） | Release-Please が自動更新 |
| `.release-please-manifest.json` | Release-Please 内部の version 状態 | Release-Please が自動更新 |
| `CHANGELOG.md` | リリースノート | Release-Please が自動生成・追記 |
| `uv.lock` | Python 依存 lockfile | `release-please-lock.yml` が `uv sync --locked` 失敗時に自動再生成 |

> **Note**: `kaji_harness/__init__.py` の `__version__` は Issue #153 で削除済み。CLI の `kaji --version` は `importlib.metadata.version("kaji")` 経由で `pyproject.toml` の値を読む。手動編集は非推奨。

## リリース担当者の操作

```bash
# 保留中の Release PR を確認
gh pr list --label "autorelease: pending"

# Release PR を merge するだけ
gh pr merge <release-pr> --merge
# → tag vX.Y.Z と GitHub Release が自動作成される
```

> **Note**: commit message は Conventional Commits を厳守すること。違反時は CHANGELOG に反映されず、version bump 判定も行われない。kaji の commit 規約は CLAUDE.md の "Git & GitHub" セクションを参照。

## ad-hoc 実行（workflow_dispatch）

main 上に release-please.yml が存在する状態（本 PR merge 後）であれば、`workflow_dispatch` で再実行できる。

```bash
gh workflow run release-please.yml -R apokamo/kaji
# target-branch を変えたい場合
gh workflow run release-please.yml -R apokamo/kaji -f target-branch=main
```

## Dry-run 手順

admin が token 登録・Actions permissions 設定・dry-run 実施・cleanup を行う。詳細は [`admin-setup.md`](./admin-setup.md) §Step 4 / §Step 5 を参照。

概要:
1. `chore/release-please-dryrun` branch を main から切り、feat branch を merge して workflow ファイルを配置
2. `release-please.yml` の `on: push: branches` に `chore/release-please-dryrun` を一時追加して push → **push トリガー**で workflow 起動（`workflow_dispatch` は pre-merge では使えない。詳細は admin-setup.md §Step 4 の起動方式選択 note 参照）
3. Release PR 生成・version 提案・CHANGELOG 更新・`uv.lock` 追従を確認
4. Release PR は **close のみ**（merge すると tag が打たれる）、検証 branch と自動生成 branch を削除

## 緊急時の手動バンプ（fallback）

Release-Please / GitHub Actions が停止している場合のみ使用する。通常運用では使わない。

```bash
git checkout -b chore/manual-version-bump-${VERSION}
# pyproject.toml の version を手動編集
uv lock
git add pyproject.toml uv.lock
make check
git commit -m "chore: bump version to ${VERSION} (manual fallback)"
git push -u origin chore/manual-version-bump-${VERSION}
gh pr create --title "chore: bump version to ${VERSION}" --base main
# merge 後
git tag ${VERSION} && git push origin ${VERSION}
gh release create ${VERSION} --title "${VERSION}" --generate-notes
```

> **Note**: `${VERSION}` は `vX.Y.Z` 形式（例: `v0.10.0`）。復旧後は通常フロー（Release-Please）に戻すこと。

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| Release PR が立たない | releasable commit が 1 本も merge されていない可能性。`git log main --grep '^(feat\|fix\|docs\|chore):' --oneline` で確認 |
| Release PR の `uv.lock` が追従しない | [`admin-setup.md`](./admin-setup.md) §トラブルシューティング を参照（token / permissions 起因が大半） |
| version が乖離している | `pyproject.toml` を SoT として Release-Please が一括管理する。merge 前の手動編集は行わない |
| `kaji --version` が想定値と異なる | `importlib.metadata.version("kaji")` 経由で読まれる。隔離環境で `uv pip install -e . && kaji --version` を実行して確認 |
