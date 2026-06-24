# Local Mode CLI Guide

`kaji` を GitHub なしで運用するための最小ガイド。Phase 3-d で追加された
`kaji local init` と `dev-local.yaml` が前提。

## いつ使うか

- GitHub 不通 / 個人開発で issue / PR を立てたくない
- 数週間〜数ヶ月の長期 local 運用も想定

## 1. インストール

```bash
uv sync
source .venv/bin/activate
```

## 2. 初期化（`kaji local init`）

### 前提: tracked `.kaji/config.toml`

`kaji local init` は **overlay (`.kaji/config.local.toml`) しか作らない**。
tracked `.kaji/config.toml` が無い repo では `kaji issue` / `kaji pr` /
`kaji run` がいずれも `.kaji/config.toml not found` で停止するため、
overlay 生成より前に最低限の base config を 1 度だけ commit する必要がある。

最小テンプレート:

```toml
# .kaji/config.toml （tracked）
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
# worktree_prefix = "kaji"          # 任意。worktree dir 名の先頭 segment（<prefix>-<branch_prefix>-<id>）。未設定時は "kaji"。issue-start skill が別 prefix で worktree を作る consumer のみ設定し、harness の算出値を実体に一致させる

[execution]
default_timeout = 1800
# agent_runner = "headless"                      # 任意。"headless"（既定） | "interactive_terminal"
# interactive_terminal_close_on_verdict = true   # 任意。interactive_terminal で verdict 検知後に terminal を閉じるか

[provider]
type = "local"
```

`agent_runner = "interactive_terminal"` は tmux pane 上で通常 `claude` / `codex` を起動する
runner backend。設定方法・CLI option・手動検証手順は
[Interactive Terminal Runner ガイド](./interactive-terminal-runner.md) を参照。

各 key（`[paths]` / `[execution]` / `[provider.local]` 等）の型 / 既定 / 検証規則の
網羅的な仕様は [設定リファレンス](../reference/configuration.md) を正本とする。本ガイドは
local mode の運用 how-to に責務を絞る。

`type = "github"` 運用なら上記 `[provider]` ブロックを以下に差し替える:

```toml
[provider]
type = "github"

[provider.github]
repo = "<owner>/<repo>"
```

### overlay 生成

リポジトリ root から:

```bash
kaji local init
```

挙動:

- `.kaji/config.local.toml` を新規生成（既存があれば exit 3 で abort）
- `.gitignore` に `.kaji/config.local.toml` 行を追記（重複時は no-op）
- tracked `.kaji/config.toml` は **touch しない**（個人選択を commit しない設計）

machine_id は次の優先順で決まる:

1. `--machine-id <name>` 明示（`[a-z0-9]{1,16}` 違反は exit 2）
2. `socket.gethostname()` を sanitize（lowercase + 英数字 + 16 文字切り詰め）
3. `pc1` / `pc2` / … に fallback（既存 `.kaji/issues/local-*` と衝突しない最小値）

`--default-branch <branch>` を渡すと overlay の `provider.local.default_branch`
に反映される（既定 `main`）。

### 生成される overlay 例

```toml
# .kaji/config.local.toml （gitignored）
[provider]
type = "local"

[provider.local]
machine_id = "pc1"
default_branch = "main"
git_remote = "origin"     # 任意。default `"origin"`。skill 内 `git push` / `git fetch` の対象 remote 名
```

## 3. provider 切替

`.kaji/config.toml` (committed) は repository default を保持し、
`.kaji/config.local.toml` (gitignored) が overlay として個人選択を上書きする。

| 状態 | 効果 |
|------|------|
| overlay なし | `.kaji/config.toml` の `[provider]` がそのまま使われる |
| overlay あり | overlay の `[provider]` セクションがマージされる |
| overlay の `type = "local"` | LocalProvider 経路 |
| overlay 削除 | tracked default に戻る |

> **⚠️ overlay は worktree per-instance**: `.kaji/config.local.toml` は `.gitignore`
> 管理のため、`git worktree add` で作った新規 worktree には**引き継がれない**。新規
> worktree から provider 解決を伴うコマンドを実行すると tracked `config.toml` の
> `[provider]` にフォールバックし、意図と異なる provider に routing され得る。
> kaji はそのズレ（overlay 不在 + メインリポジトリ overlay と `provider.type` が相違）
> を検出して stderr に WARN を出す。新規 worktree で overlay を使う場合は、メイン
> リポジトリから `config.local.toml` をコピーするか当該 worktree で `kaji local init`
> を再実行する。詳細は [`docs/guides/git-worktree.md`](../guides/git-worktree.md)
> § provider overlay を参照。

## 4. Issue / Workflow

```bash
# Issue 作成（--body または --body-file が必須。slug は title から自動導出。
# 明示したい場合は --slug を渡す）
kaji issue create \
  --title "do something" \
  --body "describe the work" \
  --label type:feature

# 本文をファイルから読み込む場合
kaji issue create --title "do something" --body-file issue-body.md --label type:feature

# 一覧
kaji issue list

# context 解決（skill / 自動化スクリプト用、`provider.resolve_issue_context()` の薄いラッパー）
kaji issue context local-pc1-1 --json branch_prefix,branch_name,worktree_dir
# → {"branch_prefix":"feat","branch_name":"feat/local-pc1-1","worktree_dir":"/abs/.../kaji-feat-local-pc1-1"}

# 本文先頭に Worktree メタ情報（> [!NOTE] ブロック）を決定的に追記（/issue-start 用）
kaji issue prepend-note local-pc1-1 --worktree kaji-feat-local-pc1-1 --branch feat/local-pc1-1 --commit

# workflow 起動（local 専用）
kaji run .kaji/wf/dev-local.yaml local-pc1-1
```

`kaji issue prepend-note <id> --worktree <basename> --branch <branch> [--commit]`
は `> [!NOTE]` メタブロックを本文先頭へ合成する provider 共通 subcommand。
合成（NOTE ブロック + **空行ちょうど 1 行** + 既存本文）は `kaji` 内部の決定的な
Python 経路が担うため、エージェントの multi-line 忠実度に依存せず blank line を
保証する（Issue #200）。`--commit` は local provider で `issue.md` を atomic commit
する（`gh issue prepend-note` は存在しないため github では `view_issue` /
`edit_issue` 経路で更新し、`--commit` は silent に無視）。`/issue-start` skill の
Step 4 がこれを呼ぶ。

`kaji issue context` は frontmatter `branch_prefix` 優先 → `type:*` ラベル
mapping → `chore` fallback の優先順で context を解決する（`provider.type='local'`
/ `'github'` のいずれでも利用可能）。`/issue-start` skill が
worktree / branch 名を導出するために使う。

`dev-local.yaml` は `dev.yaml` から GitHub 前提の PR 関連 step（`i-pr` /
`review-poll` / PR review / `pr-fix` / `pr-verify`）を除き、`final-check` の後を
`issue-close` で終端した local provider 版。PR は作らず、`/issue-close`
が `git merge --no-ff` + frontmatter close を行う。

## 5. ID 文法

| 形式 | 意味 |
|------|------|
| `local-pc1-3` | machine_id `pc1` の 3 番目（フル形式） |
| `pc1-3` | 短縮形。provider=local 時のみ受理 |
| `3` | provider=local 時は machine_id を補完して `local-<self>-3` に解決 |
| `gh:153` | GitHub cache 由来の read-only 参照。`kaji sync from-github` で `.kaji/cache/gh-<n>.json` を populate して使う（後述 § 9c）|

## 6. /issue-close の挙動（local）

design.md L972-996 に従う 6 step:

1. Preflight check（uncommitted / branch / base 確認）
2. Base branch 最新化（`git fetch` + `merge --ff-only`）
3. Merge 実行（`git merge --no-ff --no-edit`）
4. Issue frontmatter 更新 + commit（`kaji issue close [issue_id] --reason completed`）
5. Cleanup（`git worktree remove` → `git branch -d`）
6. Push（remote ありなら `git push [git_remote] [default_branch]`）

Step 4 完了で Issue close は確定し、Step 5/6 の失敗は警告のみ。
`--reason` 未指定時の default は `completed`（GitHub Issue API の慣行と整合）。

### `git_remote` を上書きする例

skill 内の `git push` / `git fetch` 等の対象 remote 名は
`provider.local.git_remote` で上書きできる（default `"origin"`）。
例えば `origin` を GitHub に向けつつ、kaji workflow は外部 mirror `backup` 経由で
push したい場合:

```toml
# .kaji/config.local.toml （gitignored）
[provider.local]
machine_id = "pc1"
git_remote = "backup"
```

`git remote get-url backup` が解決できる前提（事前に `git remote add backup …` で
登録しておく）。skill prompt の `[git_remote]` placeholder がここで指定した値に
解決され、`/issue-close` の Step 4.5 / 6 が当該 remote を叩く。
`provider.local.git_remote` の既定値・型仕様は
[設定リファレンス](../reference/configuration.md#providerlocal) を参照。

### `kaji issue {edit,comment} --commit` flag（local）

LocalProvider 配下で `kaji issue edit` / `kaji issue comment` に `--commit`
flag を渡すと、Issue ファイル（`issue.md` / コメントファイル）の書き換えと
git stage + commit を **同一 process 内で atomic に** 行う。実装は
`kaji_harness/cli_main.py` の `_commit_local_issue_change` ヘルパ
（`provider.type='local'` の場合のみ実体動作する）。

挙動の要点:

- commit 対象は CLI が書き換えた path（`issue.md` または新規 comment markdown）
  のみに限定する。`git add -- <対象 path>` で stage したうえで、
  `git commit --only -- <対象 path>` を使うことで HEAD に当該 path だけを
  反映する一時 index を構築して commit する。pre-existing で staged な
  他のファイルはこの commit には含まれず、ユーザの index に残ったまま保護される
  （`man git-commit` § `--only` 準拠）
- commit message は `chore(local): edit for <issue_ref>`
  または `chore(local): comment for <issue_ref>` 形式
  （実装: `kaji_harness/cli_main.py` `_commit_local_issue_change`）
- `kaji issue edit --commit` で実体差分が無い no-op edit の場合は、
  `git diff --cached --quiet` で staged 差分を確認し、空なら commit を skip する
  （`nothing to commit` での exit 1 を避けるため）
- skill が `--commit` を毎呼び出しに付与することで、`/issue-close` の
  base worktree clean 前提（§ 6 Step 2）が成立する
- `provider.type='github'` 配下では `--commit` は **silent strip**
  され、CLI 引数として認識されつつ何もしない（passthrough 経路の冪等性のため）

### main worktree への書き込み固定（main worktree redirection）

`provider.type='local'` 配下では、`kaji issue {create,edit,comment,close}` の
ファイル書き込み・`--commit` 動作は **cwd と無関係に
`provider.local.default_branch` を checkout している worktree (= main worktree)**
に向く。feature worktree (`fix/N` 等) に `cd` した状態で
`kaji issue comment local-pc1-3 --commit` を実行しても、
コメントファイルと commit は main worktree / `default_branch` に着地する。

実装: `kaji_harness/providers/_worktree.py` の `resolve_main_worktree()` が
`git worktree list --porcelain` を解析し、`branch refs/heads/<default_branch>`
に一致する worktree を `LocalProvider.repo_root` として固定する
(`kaji_harness/providers/__init__.py` `get_provider()` 内で 1 度だけ解決)。

トラブルシュート:

| 症状 | 原因 / 対処 |
|------|-------------|
| `LocalProviderError: no worktree found for branch 'main'` | `default_branch` を checkout している worktree が無い。`git worktree add ../main main` を実行するか、`provider.local.default_branch` を実在ブランチに合わせる |
| `LocalProviderError: git CLI not found on PATH ...` | `git` コマンドが PATH 上に無い。`git` をインストールして PATH を通すか、`provider.type` を `local` 以外に切り替える |
| `LocalProviderError: 'git -C ... worktree list' failed (exit ...)` | `provider.type='local'` の設定ディレクトリが git repository でない。対象ディレクトリで `git init` を実行する（または既に init 済みの worktree から起動する）、もしくは `provider.type` を切り替える |
| `warning: multiple worktrees checking out 'main'` (stderr) | 防御的入力に対する警告。最初に見つかった worktree を採用するが、通常 git 操作では発生しない |

GitHub provider の `repo_root` は cwd 起点のまま（`gh` CLI が cwd に依存しないため、影響なし）。

## 7. ファイル / レイアウト

```
.kaji/
├── config.toml          (tracked, repo default)
├── config.local.toml    (gitignored, overlay)
├── counters/<machine>.txt   (gitignored)
├── issues/local-<machine>-<n>-<slug>/
│   ├── issue.md         (frontmatter + body)
│   └── comments/<seq>-<machine>.md
└── cache/gh-<n>.json       (GitHub Issue read-only cache。`kaji sync from-github` で populate)
```

## 8. `kaji pr` の挙動（Phase 4 以降）

`provider.type='local'` 配下では `kaji pr ...` は **bare-provider error** で
exit 2 する（Phase 4 で導入）。PR 概念が無いため、`kaji pr create`
/ `kaji pr list` / `kaji pr review-comments` 等すべてのサブコマンドが
同じ挙動になる。事故 PR を防ぐ目的のガード。

代替手段:

| 旧（GitHub mode） | local mode 代替 |
|------------------|-----------------|
| Code review (`/pr-fix`, `/pr-verify`) | `/issue-review-code` / `/issue-fix-code` / `/issue-verify-code`（PR 概念を介さず Issue 上で回す） |
| Merge & close (`/i-pr` → review → `/issue-close`) | `/issue-close` 直行（`git merge --no-ff <feat-branch>` + `kaji issue close --reason completed`） |
| PR 一覧 | `git branch --list 'feat/local-*'` |

`pr-fix` / `pr-verify` / `i-pr` Skill は手動実行（`/pr-fix <issue_id>`）でも
Step 0 で `provider_type` を確認して ABORT する。手動実行時の解決経路:

```bash
PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
```

GitHub mode に戻したい場合は `.kaji/config.local.toml` の `[provider] type =
"github"` に書き換える（または overlay を削除して tracked
`.kaji/config.toml` を有効化）。

## 9. 既知の制限

- Windows native は現時点では対応対象外。Windows では WSL 上で使う
- `kaji sync from-github` / `kaji sync status` は実装済（後述 § 9c）。`provider.type='local'` 配下から `gh:N` で GitHub Issue を参照する経路を提供する

## 9c. `kaji sync from-github`（GitHub cache populate）

`provider.type='local'` 配下から `gh:N` で GitHub Issue を参照する場合、
あらかじめ `kaji sync from-github` で cache を populate する。cache は
`.kaji/cache/gh-<n>.json`。

> GitHub repo を kaji の primary forge として `provider.type='github'` で運用
> する場合のセットアップ / 認証は [GitHub Mode CLI Guide](github-mode.md) を
> 参照。本節は **`local` mode から read-only で GitHub Issue を参照** する
> ための cache populate 経路のみを扱う。

```bash
# 初回 sync（[provider.github].repo を config に書いておく場合）
$ kaji sync from-github

# repo を CLI 引数で指定する場合
$ kaji sync from-github --repo apokamo/kaji

# cache から read
$ kaji issue view gh:42

# sync 状態の確認
$ kaji sync status
forge        github
repo         apokamo/kaji
cached       47 (gh-*.json under .kaji/cache/)
```

**スコープと前提**:

- 同期対象は **GitHub repo の open Issue 全件**。GitHub REST `/issues` endpoint は PR も返すため、`pull_request` キーを持つ entry は除外される
- `--include-closed` / `--state` / `--since` 等の追加 flag は本 release では未実装で、指定すると `exit 2` で fail-fast する（silent ignore しない）
- ローカル cache に存在するが GitHub 側で取得結果に含まれない Issue は cache に残り、`kaji_local.is_stale=true` フラグが立つ

## 9a. 検証期間運用について

2026-05-08 以降、kaji は **検証期間中 local-mode を SoT として運用**する方針。
forge 通信を要する `kaji sync` 系 / PR context 注入 / `--add-frontmatter` は
すべて [残課題](../../draft/design/local-mode/design.md#残課題) として後送り。

検証期間中の運用手順（複数 PC / コード同期戦略 / forge 移行判断）は
[Local Mode 検証期間運用 Runbook](../operations/local-mode-runbook.md)
を参照。

## 10. Phase 3-e migration（既存 user 向け）

Phase 3-e で `[provider]` セクションは **必須** になった。`.kaji/config.toml`
に `[provider]` が無い repo は `kaji issue` / `kaji pr` / `kaji run` が
exit 2 で停止する。

### A. GitHub 運用継続の場合

`.kaji/config.toml` に以下を追記する:

```toml
[provider]
type = "github"

[provider.github]
repo = "<owner>/<repo>"
```

- `repo` を設定すると `kaji issue` / `kaji pr` は `gh` 起動時に
  `--repo <owner>/<repo>` を自動注入する。worktree の git remote が
  fork を指していても、書き先が意図せずズレない。

### B. local-first 運用に切り替える場合

```bash
kaji local init
```

- `.kaji/config.local.toml`（gitignored）を作成し、`machine_id` /
  `default_branch` を書き込む
- `.gitignore` に `.kaji/config.local.toml` 行を自動追加（既存なら no-op）
- 既存 `.kaji/config.toml` の `[provider]` セクションは尊重されるため、
  user が手動で `type = "local"` を tracked 側に書く場合と overlay で
  上書きする場合の両方を選べる

### C. `.kaji/config.toml` 自体が無い repo

Phase 3-e より前は kaji リポジトリ外で `kaji issue view 1` を呼ぶと
`gh issue view 1` に素通りしていた。Phase 3-e でこの passthrough は
削除された。`.kaji/config.toml` を持たない場所では `gh` を直接呼ぶ。

### D. `provider.local.machine_id` の手書きに注意

`config.local.toml` を手書きする場合、`machine_id` は `[a-z0-9]{1,16}`
を満たす必要がある（lowercase 英数字のみ、ハイフン不可、最大 16 文字）。
Phase 3-e から config 読み込み段で文法 validation が走り、`PC1` /
`pc-1` / 17 文字超は `ConfigLoadError` で fail-fast する。

迷ったら `kaji local init` を使えば候補生成 + 重複検知が走る。
