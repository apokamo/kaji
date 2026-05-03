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

概要（**post-merge / 初回リリース前**に実施）:
1. `chore/release-please-dryrun` branch を main（既に release-please workflow が存在）から切って push
2. `gh workflow run release-please.yml -f target-branch=chore/release-please-dryrun` で `workflow_dispatch` 起動
3. Release PR 生成・version 提案・CHANGELOG 更新・`uv.lock` 追従を確認
4. Release PR は **close のみ**（merge すると tag が打たれる）、検証 branch と自動生成 branch を削除

## 初回リリース前チェックリスト

本 Issue (#153) merge 後、初回 Release PR を merge する**前**に必ず以下を確認する。dry-run（`admin-setup.md` §Step 4）を未実施で初回 Release PR に進む場合は、以下を Release PR 上で同等に確認すること（dry-run を実施済みの場合も、本番 Release PR に対してはもう一度突き合わせる）。

### A. 前提作業（admin / user 実施）

- [ ] **GitHub App `kaji-release-please`** を作成し、`apokamo/kaji` に install 済み（[`admin-setup.md`](./admin-setup.md) §Step 1 Option A）
- [ ] **repo secret** に `RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY` が登録済み（`gh secret list -R apokamo/kaji` で確認）
- [ ] **Actions permissions**: 「Read and write」+「Allow GitHub Actions to create and approve pull requests」が ON（`apokamo/kaji` Settings → Actions → General）
- [ ] **dry-run の実施 or 同等確認の完了**: `admin-setup.md` §Step 4 を実施済み、または初回 Release PR で B / C / D を確認する旨を意識している

### B. Release PR の構造確認

- [ ] Release PR の **head branch** が `release-please--branches--main--components--kaji`（または target-branch に応じた前方一致）になっている
- [ ] **提案 version** が期待どおり（初回は `0.10.0` 想定。manifest `0.9.1` + feat 起因の minor bump）
- [ ] **CHANGELOG.md** が新規生成され、`v0.9.1..main` の commits が `changelog-sections`（✨ Features / 🐛 Bug Fixes / 📝 Documentation など）で section 別に分類されている
- [ ] `pyproject.toml` の `version` が提案 version に書き換わっている
- [ ] `.release-please-manifest.json` が提案 version に書き換わっている

### C. lock 追従 workflow の動作確認

- [ ] `release-please-lock.yml` run が Release PR の opened / synchronize に応じて起動している（`gh run list -R apokamo/kaji -w release-please-lock.yml`）
- [ ] `uv sync --locked` 結果が `in_sync=true`、または `uv lock` による自動 commit が Release PR に追加されている
- [ ] Release PR head に GitHub App bot 名義の commit のみが追加されている（人手 push が混入していない）

### D. version SoT / CLI 動作確認

- [ ] `kaji_harness/__init__.py` に `__version__` が**ない**（Issue #153 で削除済み。`importlib.metadata.version("kaji")` 経由に統一）
- [ ] Release PR を base に隔離環境で `uv pip install -e .` → `kaji --version` が提案 version を返す（任意 / 不安なときの追加確認）

### E. Release PR merge 後の事後確認（参考）

- [ ] tag `v0.10.0`（または採番された値）が自動付与されている（`gh release list -R apokamo/kaji`）
- [ ] GitHub Release が公開され、CHANGELOG の該当セクションが Release notes として転記されている
- [ ] 次回以降、main への conventional commits の merge ごとに Release PR が自動更新されることを最初の数本で目視確認する

> **未充足が見つかった場合**: 原因が config / workflow 側にあれば別 Issue を起票して修正 PR を出す（本 Issue は merge 済み）。secret / permissions 起因であれば admin が `admin-setup.md` の該当 Step を再確認する。

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
