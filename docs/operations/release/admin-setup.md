# Release-Please Admin 初期設定手順（historical）

> **⚠️ 現状: 非運用 (historical)**
>
> kaji の release 運用は gl:19 以降 **`/release` skill ベース（CI 非依存 / GitLab + `glab` / maintainer 手元実行）** に移行している。本ドキュメントが対象とする GitHub release-please フローは **現在使用していない**。
>
> - 現行リリース運用: [`runbook.md`](./runbook.md)（`/release` skill ベース）と [`.claude/skills/release/SKILL.md`](../../../.claude/skills/release/SKILL.md)
> - 本ドキュメントの位置付け: 将来 GitHub 運用を再開する場合の参考資料 / `.github/workflows/release-please.yml` 等の歴史的経緯
> - GitHub workflow ファイル (`.github/workflows/release-please.yml` / `release-please-lock.yml`) は gl:19 非目的により削除せず保持
>
> 以下の手順は **当時の release-please 稼働手順** をそのまま残したものであり、`runbook.md` 内の旧 section (通常リリース運用 / 初回リリース前チェックリスト 等) への参照は現行 runbook では存在しない。読み替えながら参照すること。

本ドキュメントは、Release-Please を稼働させるために **1 度だけ実施する GitHub 側の初期設定** を扱う。

- **実施者**: `apokamo/kaji` の Admin ロール保有者
- **前提**: `gh` CLI ログイン済み（`gh auth status` で確認）
- **根拠**: 設計書 `draft/design/issue-153-release-please.md`、Release-Please 公式 README [token / permissions 要件](https://github.com/googleapis/release-please-action#github-credentials)

## 全体像

1. `RELEASE_PLEASE_TOKEN` の発行（GitHub App 推奨 / fine-grained PAT は暫定）
2. repo secret への登録
3. Actions permissions の有効化（"Allow Actions to create and approve PRs" + "Read and write"）
4. **(post-merge / 初回リリース前)** dry-run の実施と証跡取得
5. cleanup（検証成果物の整理）

> **重要**: 本 Issue (#153) の PR merge 前に Step 1-3 が完了している必要は**ない**。Step 1-3 は admin 権限を要するため AI フェーズ完了条件には含めず、Step 4 (dry-run) と合わせて **post-merge から初回リリース前までに完了**させる運用とする。理由は [`docs/dev/workflow_completion_criteria.md`](../../dev/workflow_completion_criteria.md) §「admin 権限を要する検証の扱い」を参照。

## Step 1: `RELEASE_PLEASE_TOKEN` の発行

built-in `GITHUB_TOKEN` で release-please-action を起動した場合、その token で作られた Release PR への push は「GITHUB_TOKEN 起点の event」扱いとなり、後続 workflow (`release-please-lock.yml` 等) が発火しない（[GitHub Docs: Triggering a workflow from a workflow](https://docs.github.com/en/actions/using-workflows/triggering-a-workflow#triggering-a-workflow-from-a-workflow)）。このため別 token が必須。

### Option の選択基準

| 条件 | 推奨 Option |
|------|-------------|
| 複数人運用 / organization 運用 / 監査要件あり | **Option A: GitHub App** |
| single-dev / 個人アカウント運用 / 最小構成で進めたい | **Option B: fine-grained PAT** |

> **Note**: release-please-action の公式 README は **PAT を推奨** している（2026-01 時点、[#1144](https://github.com/googleapis/release-please-action/issues/1144) で App 推奨化が "Out of scope" で却下）。Option A は GitHub / コミュニティ全般のベタープラクティスに基づく選択。kaji では将来の org 化や監査性を見据え Option A を第一候補としている（kamo2 Issue #958 と同方式）。

> **⚠️ 現行 workflow の制約**: `.github/workflows/release-please.yml` と `.github/workflows/release-please-lock.yml` は **Option A (GitHub App) 前提で実装**されている（`actions/create-github-app-token@v1` で installation token を発行し、`RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY` を必須参照）。Option B (PAT) を使いたい場合は、これら 2 つの workflow の `Generate GitHub App installation token` ステップを削除し、`token: ${{ secrets.RELEASE_PLEASE_TOKEN }}` を直接参照する形に書き換える必要がある。docs 上は両 Option を記載するが、workflow 切替は別作業。

### Option A: GitHub App

監査可能・ローテーション容易で organization 管理にも向く。

1. GitHub → **ユーザー Settings**（右上アバター → Settings）→ Developer settings → GitHub Apps → **New GitHub App**
2. 入力項目:
   - App name: `kaji-release-please`（任意）
   - Homepage URL: リポジトリ URL
   - Webhook: OFF（Active チェックを外す）
3. Repository permissions:
   - **Contents: Read and write**
   - **Pull requests: Read and write**
   - **Issues: Read and write**
4. 発行後、App ページで:
   - **Generate a private key** → `.pem` をダウンロード
   - **Install App** で対象リポジトリ（`apokamo/kaji`）にインストール
5. Actions 内で token を取得するために、secret に 2 値を登録（Step 2 で実施）:
   - `RELEASE_PLEASE_APP_ID`（App ID）
   - `RELEASE_PLEASE_APP_PRIVATE_KEY`（`.pem` の内容）
6. workflow で `actions/create-github-app-token@v1` を使って installation token を発行し、それを release-please-action に渡す（実装は `.github/workflows/release-please.yml` 参照）

### Option B: fine-grained PAT

release-please-action 公式推奨。単一ユーザー権限で簡易。90 日 rotation 必須。

1. GitHub → **ユーザー Settings**（右上アバター → Settings）→ Developer settings → Personal access tokens → **Fine-grained tokens** → **Generate new token**
2. 入力項目:
   - Token name: `kaji-release-please`
   - Expiration: 90 日以内（推奨。満了前に rotation）
   - Resource owner: `apokamo`
   - Repository access: **Only select repositories** → `apokamo/kaji`
3. Repository permissions:
   - **Contents: Read and write**
   - **Pull requests: Read and write**
   - **Issues: Read and write**
   - **Metadata: Read-only**（自動付与）
4. Generate → 表示された `github_pat_...` をコピー（再表示不可）

## Step 2: Repository secret への登録

```bash
# Option A (GitHub App) の場合
gh secret set RELEASE_PLEASE_APP_ID -R apokamo/kaji -b "<App ID>"
gh secret set RELEASE_PLEASE_APP_PRIVATE_KEY -R apokamo/kaji < path/to/private-key.pem

# Option B (PAT) の場合
gh secret set RELEASE_PLEASE_TOKEN -R apokamo/kaji -b "<github_pat_...>"

# 登録確認（値は見えないが名前は確認可能）
gh secret list -R apokamo/kaji
```

## Step 3: Actions permissions の有効化

GitHub UI から設定する（`gh` CLI 未対応）:

1. **リポジトリ Settings**（`apokamo/kaji` のページ上部 Settings タブ）→ Actions → **General**
2. **Workflow permissions**:
   - `Read and write permissions` を選択
   - **Allow GitHub Actions to create and approve pull requests** にチェック

両方とも release-please-action が Release PR を作成するために必須（[release-please-action README](https://github.com/googleapis/release-please-action#github-credentials)）。

## Step 4: Dry-run の実施（post-merge / 初回リリース前）

実機検証を本番 Release PR 生成より前に、main を汚さずに行う。**本 Issue (#153) の PR merge 後に main 上で workflow が利用可能になっていることを前提**とし、専用 branch (`chore/release-please-dryrun`) を target にして `workflow_dispatch` で起動する。

> **起動方式の選択理由（post-merge）**: 本 Issue merge 後は main に `release-please.yml` が存在するため、`workflow_dispatch` で `target-branch` input を `chore/release-please-dryrun` に指定して起動できる（[GitHub Docs: Manually run a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)）。pre-merge dry-run は本 Issue 完了条件外（admin 権限 / secret 登録が前提のため）であり、AI フェーズの完了条件は静的検証（JSON schema validation + actionlint + 移植元 diff レビュー）で代替済み（[`docs/dev/workflow_completion_criteria.md`](../../dev/workflow_completion_criteria.md) §「admin 権限を要する検証の扱い」）。

> **タイミング**: Step 1-3 完了後、初回 Release PR を merge する**前**までに実施する。
>
> **historical note**: 当時の運用では「dry-run 未実施で初回 Release PR を merge する場合は runbook.md §『初回リリース前チェックリスト』を参照」としていたが、現行 runbook.md は `/release` skill ベースに置換済みで該当 section は存在しない。GitHub 運用を再開する場合は本ドキュメントの記述から該当チェックリストを再構築すること。

### 4-1. 検証用 branch の準備

```bash
# main を最新化
git fetch origin main
git checkout main && git pull --ff-only origin main

# 検証用 branch を main から切る（main には #153 の workflow / config 一式が既に存在）
git checkout -b chore/release-please-dryrun
git push -u origin chore/release-please-dryrun
```

### 4-2. Workflow を target-branch 指定で起動

```bash
# main 上の release-please.yml を、chore/release-please-dryrun を target として起動
gh workflow run release-please.yml -R apokamo/kaji \
  -f target-branch=chore/release-please-dryrun

# 起動した run を確認
gh run list -R apokamo/kaji -w release-please.yml --limit 3
gh run watch <run-id> -R apokamo/kaji --exit-status
```

### 4-3. 期待結果（全て Issue / リリース準備記録に証跡として残す）

| 確認項目 | 期待値 | 取得方法 |
|---------|--------|---------|
| Release PR 生成 | `release-please--branches--chore-release-please-dryrun--components--kaji` 等の前方一致 branch で PR が作成 | `gh pr list -R apokamo/kaji --head 'release-please--branches--chore-release-please-dryrun'` URL |
| 提案 version | feat 起因の minor bump で **0.10.0** が提案される（v0.9.1 manifest 起点） | Release PR タイトル / 更新後の `pyproject.toml` |
| CHANGELOG.md 生成 | `v0.9.1..HEAD` の commits が `changelog-sections` で定義した section（✨ Features / 🐛 Bug Fixes / 📝 Documentation 等）に分類されて表示（commit 数は dryrun 時点の `git rev-list --count v0.9.1..HEAD` 実測値に従う） | Release PR の Files changed タブ diff 抜粋 |
| `uv.lock` 追従 | `release-please-lock.yml` run が起動し、`uv sync --locked` 検証 → 必要時のみ `uv lock` の追加 commit を push、または `in_sync=true` でスキップ | `gh run list -R apokamo/kaji -w release-please-lock.yml` run log link |

> **Note**: kaji リポジトリには現状 `verify-backend` 等の CI workflow が存在しないため、Release PR 上の追加 CI green 確認は対象外。dry-run の責務は「release-please mechanism の動作確認」までとする。

証跡が 1 件でも欠けた場合は、原因（secret 未登録 / Actions permissions 不足 / workflow 設定不備）を特定して修正 → 再 dry-run。workflow / config 自体の不備が見つかった場合は別 Issue を起票して対応する（本 Issue は merge 済みのため）。

## Step 5: Cleanup

dry-run 完了後、**必ず以下を実施**（放置すると Release PR が誤って merge される危険 / branch ゴミが残る）:

```bash
# 5-1. 検証用 Release PR を close のみ（merge 厳禁 — tag が打たれてしまう）
PR_NUMBER=$(gh pr list -R apokamo/kaji --head 'release-please--branches--chore-release-please-dryrun' --json number -q '.[0].number')
gh pr close "$PR_NUMBER" -R apokamo/kaji -c "dry-run 検証完了。#153 admin-setup.md §Step 5 に従い close"

# 5-2. Release-Please が生成した branch を削除
RELEASE_BRANCH=$(gh pr view "$PR_NUMBER" -R apokamo/kaji --json headRefName -q .headRefName)
gh api -X DELETE "repos/apokamo/kaji/git/refs/heads/${RELEASE_BRANCH}"

# 5-3. 検証用 branch を削除
git push origin --delete chore/release-please-dryrun

# 5-4. ローカル branch もクリーン
git branch -D chore/release-please-dryrun
```

> **保持するもの**: dry-run で起動した release-please run と release-please-lock run（Actions history）は削除しない。後日のリリース運用 / 監査の参照資料として残す。

## Step 6: 完了報告

dry-run まで含む admin セットアップが完了したら、リリース準備完了として記録する（運用記録 / 引き継ぎ用）:

- Step 2 の `gh secret list` 出力（token 名のみ）
- Step 3 の Actions permissions 確認文
- Step 4-3 の証跡一式（Release PR URL / 提案 version / CHANGELOG / lock workflow run URL）
- Step 5 の cleanup 完了確認

これらは Issue #153 が既に close 済みでも構わない。

> **historical note**: 当時は runbook.md §「初回リリース前チェックリスト」と突き合わせる運用だったが、現行 runbook は `/release` skill ベースで該当 section が存在しない。GitHub 運用を再開する場合は本ドキュメントの Step 4-3 期待結果表をチェックリストとして使うこと。

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| Release PR が作成されない | `RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY`（または PAT）未設定 / Actions permissions 不足 | Step 2, 3 を再確認 |
| Release PR 上の後続 workflow が動かない | built-in `GITHUB_TOKEN` で Release PR が作られている | workflow が App token を渡しているか確認（`.github/workflows/release-please.yml`） |
| `release-please-lock.yml` が起動しない | head_ref 前方一致条件にミス / token 不在 | workflow の `if: startsWith(github.event.pull_request.head.ref, 'release-please--branches--')` を確認 |
| `uv sync --locked` が Release PR で失敗 | `pyproject.toml` の version 変更に `uv.lock` が追従していない | follow-up workflow が `uv lock` を実行して自動 commit する。2 分程度待つ |

## PAT の rotation

Option B を採用した場合、90 日ごとに以下を実施:

```bash
# 新しい PAT を発行（Step 1 Option B を再実行）
# 旧値を上書き
gh secret set RELEASE_PLEASE_TOKEN -R apokamo/kaji -b "<新しい github_pat_...>"
# 旧 PAT をユーザー Settings → Developer settings から revoke
```

Option A (GitHub App) ではプライベートキーの rotation のみで可（App ID は変わらない）。
