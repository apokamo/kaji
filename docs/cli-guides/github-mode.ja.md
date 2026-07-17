# GitHub Mode CLI Guide

Language: [English](github-mode.md) | 日本語

`kaji` を `provider.type = "github"` で運用するためのセットアップ / 運用 / 前提
ガイド。GitHub mode 固有の前提・設定・命名規約・トラブルシュートを 1 ファイルで
提供する。

## いつ使うか

- GitHub 上の repo（`github.com/<owner>/<name>`）を kaji の primary forge として運用する場合
- 緊急時 fallback の local-mode（[Local Mode CLI Guide](local-mode.md)）から GitHub 通常運用へ復帰する場合
- `kaji sync from-github` で GitHub Issue を local cache に取り込みたい場合

> ⚠️ **auto-close keyword 注意**: GitHub は `Closes #<N>` / `Fixes #<N>` /
> `Resolves #<N>` 等を auto-close keyword として解釈する。経路は **2 つある**:
> **PR description** に書くと PR↔Issue が linked PR として紐付き merge 時に close
> され、**commit message** に書くとその commit が default branch に到達した時点で
> close される
> （公式: [Closing issues using keywords](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)）。
>
> 本リポジトリ（apokamo/kaji）はリポジトリ設定
> **Auto-close issues with merged linked pull requests**（Settings → General →
> Features → Issues）を無効化している。この設定が抑止するのは **linked PR 経由の
> 経路のみ** であり、commit message 経由の経路をカバーする保証はない。その前提で
> `/i-pr` は PR description に live closing keyword を **1 行だけ**生成する
> （PR↔Issue の紐付けを自動化できる唯一の手段のため）。commit body（merge commit
> message を含む）と、PR description のそれ以外の箇所では引き続き closing keyword
> を書かない。issue の close は `/issue-close` で明示的に行う。
>
> 当該設定を無効化していないリポジトリで kaji を運用する場合は、PR description に
> ついても従来どおり回避規約を適用すること。規約の正本は
> [docs/dev/shared_skill_rules.md § auto close keyword 回避](../dev/shared_skill_rules.md#auto-close-keyword-回避)
> を参照。

## 1. 前提

### 1.1 必須ツール

| ツール | 役割 | 備考 |
|--------|------|------|
| `gh` | GitHub CLI（`kaji pr` / `kaji issue` / `kaji sync from-github` の背後で起動） | PATH 上に必須 |
| `git` | 通常運用 | `git@github.com` への SSH push が前提 |

`gh` 未導入の場合、`kaji sync from-github` および `provider.type='github'` 配下の
`kaji issue` / `kaji pr` は `'gh' CLI not found in PATH. ...` で始まるエラーで exit する
（後続の案内文は entry point ごとに異なる。例: passthrough 経路は
`Install GitHub CLI to use 'kaji issue' / 'kaji pr'.`）。

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
# worktree_prefix = "kaji"          # 任意。既定値・実効挙動は設定リファレンス参照

[execution]
default_timeout = 1800
# agent_runner = "headless"          # 任意。"headless"（既定） | "interactive_terminal"
# interactive_terminal_close_on_verdict = true   # 任意

[provider]
type = "github"

[provider.github]
repo = "<owner>/<name>"             # 例: "apokamo/kaji"
default_branch = "main"             # 既定 "main"
git_remote = "origin"               # 任意。既定 "origin"
```

`agent_runner = "interactive_terminal"` は tmux pane 上で通常 `claude` / `codex` を起動する
runner backend。設定方法・CLI option・手動検証手順は
[Interactive Terminal Runner ガイド](./interactive-terminal-runner.md) を参照。

各 key の型 / 既定 / 検証規則の網羅的な仕様は
[設定リファレンス](../reference/configuration.md) を正本とする。GitHub mode で押さえる要点:

- `[provider.github].repo` は **`owner/name`** 形式。`https://` プレフィクスや `.git` サフィックスは付けない。`gh --repo <owner>/<name>` / `gh api repos/<owner>/<name>/...` に渡される
- `worktree_prefix` / `agent_runner` / `git_remote` の既定値・実効挙動は
  [設定リファレンス](../reference/configuration.md) の section / key 仕様を参照

### 1.4 `.github/labels.yml` の連動

kaji は GitHub project 直下の `.github/labels.yml` を label の正本として運用する（追加・削除手順は [GitHub ラベル運用](../dev/labels.md) 参照）。`kaji_harness/providers/_mappings.py` は `type:*` label から branch prefix への mapping を担う。`.github/labels.yml` を編集した場合は GitHub Actions の `labels-sync` workflow（`.github/workflows/labels-sync.yml`）で GitHub 側へ同期される。

## 2. `kaji issue` / `kaji pr` の挙動

`provider.type = "github"` 配下では `kaji issue` / `kaji pr` は skill 互換 contract で動作する。

- `kaji pr create` / `view` / `list` / `comment` / `review` / `merge` / `review-comments` / `reviews` / `reply-to-comment` / `review-poll` は GitHub でも同じ呼び出し方が通る
- `kaji pr merge` は `--squash` / `--rebase` flag を **kaji 側で silent に除去**し、常に `gh pr merge --merge` 固定で叩く（`--no-ff` only の merge 規約）
- `kaji pr review <pr> --approve` / `kaji pr review <pr> --request-changes` は self-PR (PR author == authenticated user) を検知すると `<!-- kaji-review: state=APPROVED -->` / `<!-- kaji-review: state=CHANGES_REQUESTED -->` marker 付き comment を Issue comments API に投稿することで review シグナルを表現し rc=0 を返す。`gh pr review --approve` / `--request-changes` は GitHub API が author の APPROVE / REQUEST_CHANGES event を `Can not approve your own pull request` / `Can not request changes on your own pull request` で 422 拒否するため、self-PR では skip される。非 self-PR では従来通り `gh pr review --approve` / `--request-changes` を委譲する。`--comment` / flag 無しは routing 段で `_github_pr_review` に分岐せず従来通り `gh pr review` へ passthrough
  - **`--request-changes` の body 必須契約（self / 非 self 一貫）**: GitHub REST API の `event=REQUEST_CHANGES` は body parameter を必須とするため、kaji 側で `--body` / `--body-file` 未指定または空白のみは subprocess 呼び出し前に `EXIT_INVALID_INPUT` (rc=2) で fail-fast する。`--approve` は GitHub API 側で body optional のため空 body を許容（既存挙動を維持）
  - **marker comment の観測経路の非対称性**: self-PR fallback で投稿された marker comment は Issue Comments API (`/repos/<repo>/issues/<N>/comments`) に書き込まれるため、`kaji pr view <pr> --comments` 経由では取得可能だが、`kaji pr reviews <pr>` (`/pulls/<N>/reviews`) には現れない。後続の `pr-fix` skill は `kaji pr view --comments` を主要 read path としているため、観測経路上は問題なし
- `kaji issue comment <id> --verdict-step <step> --verdict-status <STATUS>` は判定コメントに verdict マーカーを付与する（下記 § 2.1）

### 2.2 複数 Issue の sequential series

複数 Issue を明示順で実行するときは tracked な `.kaji/series/<id>.yaml` を使う。本実行前に
validation と副作用のない dry-run を行う。

```bash
kaji validate-series .kaji/series/<id>.yaml
kaji run-series .kaji/series/<id>.yaml --dry-run
kaji run-series .kaji/series/<id>.yaml
```

各 member は既存の `kaji run` で実行され、child の exit 0 と Issue の
`closed/completed` が揃った場合だけ次へ進む。停止・中断後は `--resume` を使う。定義 fingerprint
の変更や生存中の遺留 child は安全側に拒否する。実行 state と lock は
`<artifacts_dir>/_series/<id>/` に保存される。

`validate-series` と `--dry-run` は現在の plan の全 member workflow に対し、YAML schema、
workflow 内参照、skill metadata の完全 preflight を行う。通常実行も開始時に同じ preflight を
再実行し、過去の dry-run 結果には依存しない。invalid member が 1 件でもあれば member process、
series state、lock は作成されない。dry-run は workflow を読み取り・検証するが、provider API、
Issue、artifact、state、lock、member 実行には副作用を与えない。

`/series-create <issues...> --id <id>` は YAML 生成、validation、dry-run まで行って停止する。
標準外 variant は `--workflow <issue>=<path>` で member 単位に明示する。skill は Issue metadata
を read-only で参照し、Issue 更新や本実行は行わない。

### 2.1 `kaji issue comment` の verdict マーカー付与

`kaji issue comment` に `--verdict-step <step> --verdict-status <STATUS>` を渡すと、CLI が comment body の **1 行目**に決定的な HTML コメントマーカー `<!-- kaji-verdict: step=<step> status=<STATUS> -->` を付与してから投稿する（GitHub UI 上では HTML コメントとして不可視）。cross-skill 契約（`issue-design` の BACK 再入検出）を SKILL.md 散文ではなく CLI 層に置くための機構（ADR 008 決定 3）。

- **両フラグ同時必須**: 片方のみの指定は exit 2（stderr にエラー）。両方なしの従来呼び出しは一切変更しない（gh passthrough のまま）
- **語彙検証（fail-loud）**: `--verdict-step` は `^[a-z][a-z0-9_-]*$`、`--verdict-status` は `PASS` / `RETRY` / `ABORT` / `BACK` / `BACK_<UPPER>`（`BACK_[A-Z0-9_]+`）。不正値は exit 2 で gh を起動しない
- github / local 両 provider で同一の振る舞い。`--commit` は github では silent に無視される
- 例: `kaji issue comment 261 --verdict-step review-code --verdict-status BACK --body-file - <<'EOF' ... EOF`

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

### 4.5 commit / PR description の `Fix #<N>` が無関係 GitHub issue を auto close

GitHub の closing keyword（`Closes` / `Fix(es|ed)` / `Resolves` 等 + `#<N>`）は当該 issue を自動 close する。経路は 2 つあり、**PR description 経由**（merge 時に close。リポジトリ設定 **Auto-close issues with merged linked pull requests** を無効化すると抑止される）と、**commit message 経由**（commit が default branch に到達した時点で close。同設定がカバーする保証はない）である（[公式](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)）。apokamo/kaji では同設定を無効化しており、kaji が生成する live closing keyword は `/i-pr` の `Closes <issue_ref>` 行 1 件のみ。それ以外の match（commit body / PR description の他の箇所）は hazard として placeholder 化する。grep 手順と placeholder 規約は [docs/dev/shared_skill_rules.md § auto close keyword 回避](../dev/shared_skill_rules.md#auto-close-keyword-回避) を参照。

意図せず close された場合は `gh issue reopen <N> --repo <owner>/<repo>` で reopen し、当該リポジトリ設定が無効のままかを確認する。

## 5. 参照

- Local mode: [docs/cli-guides/local-mode.md](local-mode.md)
- 設計書: `draft/design/issue-34-github-pr-context-auto-injection-kaji-sy.md`
