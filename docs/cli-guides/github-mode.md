# GitHub Mode CLI Guide

`kaji` を `provider.type = "github"` で運用するためのセットアップ / 運用 / 前提
ガイド。GitHub mode 固有の前提・設定・命名規約・トラブルシュートを 1 ファイルで
提供する。

## いつ使うか

- GitHub 上の repo（`github.com/<owner>/<name>`）を kaji の primary forge として運用する場合
- 検証期間中の local-mode（`docs/cli-guides/local-mode.md`）から GitHub を本格 forge として採用する移行段階
- `kaji sync from-github` で GitHub Issue を local cache に取り込みたい場合

> ⚠️ **auto-close keyword 注意**: GitHub も PR description 内の
> `Closes #N` / `Fixes #N` / `Resolves #N` 等を auto-close keyword として
> 解釈し、default branch への merge で **無関係な issue を自動 close する**。
> 回避規約は
> [docs/dev/shared_skill_rules.md § auto close keyword 回避](../dev/shared_skill_rules.md#auto-close-keyword-回避)
> を参照（公式: [Closing issues using keywords](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)）。

## 1. 前提

### 1.1 必須ツール

| ツール | 役割 | 備考 |
|--------|------|------|
| `gh` | GitHub CLI（`kaji pr` / `kaji issue` / `kaji sync from-github` の背後で起動） | PATH 上に必須 |
| `git` | 通常運用 | `git@github.com` への SSH push が前提 |

`gh` 未導入の場合、`kaji sync from-github` および `provider.type='github'` 配下の
`kaji issue` / `kaji pr` は以下のメッセージで exit する:

```
'gh' CLI not found in PATH. Install GitHub CLI to use provider.type='github'.
```

### 1.2 認証

`gh auth login` で対話的に認証する:

```bash
gh auth login
gh auth status            # → "Logged in to github.com as <user>"
```

CI / 無人スクリプトでは `GH_TOKEN` 環境変数で PAT を渡す。PAT の scope は **`repo`**
（Issue / PR の read/write を含む）が必須。

### 1.3 `.kaji/config.toml`

`provider.type = "github"` の最小設定:

```toml
# .kaji/config.toml （tracked）
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
# worktree_prefix = "kaji"          # 任意。worktree dir 名の先頭 segment（<prefix>-<branch_prefix>-<id>）。未設定時は "kaji"。issue-start skill が別 prefix で worktree を作る consumer のみ設定し、harness の算出値を実体に一致させる

[execution]
default_timeout = 1800

[provider]
type = "github"

[provider.github]
repo = "<owner>/<name>"             # 例: "apokamo/kaji"
default_branch = "main"             # 既定 "main"
git_remote = "origin"               # 任意。default `"origin"`。hybrid setup での remote 名
```

要点:

- `[provider.github].repo` は **`owner/name`** 形式。`https://` プレフィクスや `.git` サフィックスは付けない
- `gh --repo <owner>/<name>` および `gh api repos/<owner>/<name>/...` に渡される
- `default_branch` を省略すると `main`
- `git_remote` は skill 内の `git push` / `git fetch` 等が対象とする git remote
  名。**default `"origin"`**

### 1.4 `.github/labels.yml` の連動

kaji は GitHub project 直下の `.github/labels.yml` を label の正本として運用する。`/issue-create` 等の skill は `kaji_harness/providers/_mappings.py` の標準 label と `.github/labels.yml` の交集合を採用する。`.github/labels.yml` を編集した場合は GitHub Actions の `labels-sync` workflow（`.github/workflows/labels-sync.yml`）で同期される。

## 2. `kaji issue` / `kaji pr` の挙動

`provider.type = "github"` 配下では `kaji issue` / `kaji pr` は skill 互換 contract で動作する。

- `kaji pr create` / `view` / `list` / `comment` / `review` / `merge` / `review-comments` / `reviews` / `reply-to-comment` は GitHub でも同じ呼び出し方が通る
- `kaji pr merge` は `--squash` / `--rebase` flag を **kaji 側で拒否**（`--no-ff` only の merge 規約。`gh pr merge --merge` 固定で叩く）
- `kaji pr review <pr> --approve` / `kaji pr review <pr> --request-changes` は self-PR (PR author == authenticated user) を検知すると `<!-- kaji-review: state=APPROVED -->` / `<!-- kaji-review: state=CHANGES_REQUESTED -->` marker 付き comment を Issue comments API に投稿することで review シグナルを表現し rc=0 を返す。`gh pr review --approve` / `--request-changes` は GitHub API が author の APPROVE / REQUEST_CHANGES event を `Can not approve your own pull request` / `Can not request changes on your own pull request` で 422 拒否するため、self-PR では skip される。非 self-PR では従来通り `gh pr review --approve` / `--request-changes` を委譲する。`--comment` / flag 無しは routing 段で `_github_pr_review` に分岐せず従来通り `gh pr review` へ passthrough
  - **`--request-changes` の body 必須契約（self / 非 self 一貫）**: GitHub REST API の `event=REQUEST_CHANGES` は body parameter を必須とするため、kaji 側で `--body` / `--body-file` 未指定または空白のみは subprocess 呼び出し前に `EXIT_INVALID_INPUT` (rc=2) で fail-fast する。`--approve` は GitHub API 側で body optional のため空 body を許容（既存挙動を維持）
  - **marker comment の観測経路の非対称性**: self-PR fallback で投稿された marker comment は Issue Comments API (`/repos/<repo>/issues/<N>/comments`) に書き込まれるため、`kaji pr view <pr> --comments` 経由では取得可能だが、`kaji pr reviews <pr>` (`/pulls/<N>/reviews`) には現れない。後続の `pr-fix` skill は `kaji pr view --comments` を主要 read path としているため、観測経路上は問題なし

## 3. `kaji sync from-github` の使い方

`provider.type = "local"` 配下から `gh:N` で GitHub Issue を read-only 参照するための cache populate 経路。`provider.type = "github"` ではこの sync は不要（直接 API を叩く）。

```bash
# 初回 sync（[provider.github].repo を config に書いておく場合）
kaji sync from-github

# repo を CLI 引数で指定する場合
kaji sync from-github --repo <owner>/<name>

# cache から read
kaji issue view gh:42

# sync 状態の確認
kaji sync status
```

cache layout は `.kaji/cache/gh-<n>.json`。schema は wrapper を含む:

```json
{
  "schema_version": 1,
  "forge": "github",
  "fetched_at": "2026-05-21T12:34:56Z",
  "kaji_local": {
    "is_stale": false,
    "last_seen_at": "2026-05-21T12:34:56Z",
    "staled_at": null
  },
  "issue": {
    "number": 42,
    "title": "...",
    "body": "...",
    "state": "open",
    "labels": [{"name": "type:feature"}]
  }
}
```

`issue` field は **GitHub REST API `GET /repos/{owner}/{repo}/issues` の生 JSON**（snake_case）。GitHub REST は `/issues` endpoint から PR も返すため、`pull_request` キーを持つ entry は sync 時に除外される。

### 3.1 ローカル手動疎通

GitHub 側 E2E target は本 release では未追加。実 GitHub API 疎通は以下の手順で手動確認する:

```bash
# auth 確認
gh auth status

# Issue 列挙
gh api -X GET repos/<owner>/<name>/issues -F state=open -F per_page=100 -F page=1 | jq '.[].number'

# kaji 経由
kaji sync from-github --repo <owner>/<name>
kaji issue view gh:<N>
kaji sync status            # forge=github / repo=<owner>/<name> / cached=<N>
```

## 4. トラブルシューティング

### 4.1 `'gh' CLI not found in PATH`

`provider.type='github'` 配下で `kaji issue` / `kaji pr` / `kaji sync from-github` を実行したが `gh` 未 install。OS パッケージマネージャで install する。

### 4.2 `gh auth status` が `not logged in`

`gh auth login` を実行、または `GH_TOKEN` env を export する。CI では env 経路を推奨。

### 4.3 `'kaji sync from-github' requires a GitHub repo`

`.kaji/config.toml` の `[provider.github]` セクションに `repo = "owner/name"` を追加するか、`--repo owner/name` を CLI 引数で渡す。

### 4.4 `multiple open pull requests found for head branch ...`

`GitHubProvider.resolve_pr_context` は 1 branch あたり open PR 1 件を前提とする。`gh pr list --head <branch> --state open` で複数件返る場合は、不要な PR を close するか個別に `kaji pr` で操作する。

### 4.5 commit / PR description の `Fix #N` が無関係 GitHub issue を auto close

GitHub の closing keyword（`Closes` / `Fix(es|ed)` / `Resolves` 等 + `#N`）は default branch への merge で当該 issue を自動 close する（[公式](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)）。回避規約は [docs/dev/shared_skill_rules.md § auto close keyword 回避](../dev/shared_skill_rules.md#auto-close-keyword-回避) の grep 手順と placeholder 規約を参照。

## 5. 参照

- Local mode: [docs/cli-guides/local-mode.md](local-mode.md)
- 設計書: `draft/design/issue-34-github-pr-context-auto-injection-kaji-sy.md`
