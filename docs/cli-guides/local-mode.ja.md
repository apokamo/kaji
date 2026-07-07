# Local Mode CLI Guide

Language: [English](local-mode.md) | 日本語

`kaji` を GitHub なしで運用するための最小ガイド。`kaji local init` で overlay を
作り、local 専用 workflow（`dev-local.yaml` / `docs-local.yaml`）で回す。

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

`[provider]` セクションも必須で、tracked / overlay のいずれにも無ければ exit 2
で停止する（config 不在時に `gh` へ素通りする legacy passthrough は存在しない）。
`machine_id` 等の文法違反も config 読み込み時点で `ConfigLoadError` として
fail-fast する。

最小テンプレート:

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
type = "local"
```

`agent_runner = "interactive_terminal"` は tmux pane 上で通常 `claude` / `codex` を起動する
runner backend。設定方法・CLI option・手動検証手順は
[Interactive Terminal Runner ガイド](./interactive-terminal-runner.md) を参照。

各 key（`[paths]` / `[execution]` / `[provider.local]` 等）の型 / 既定 / 検証規則の
網羅的な仕様は [設定リファレンス](../reference/configuration.md) を正本とする。本ガイドは
local mode の運用 how-to に責務を絞る。`type = "github"` 運用のセットアップは
[GitHub Mode CLI Guide](github-mode.md) を参照。

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
```

`kaji local init` が書くのは上記 3 値のみ。`git_remote`（skill 内 `git push` /
`git fetch` の対象 remote 名、既定 `"origin"`）は必要なら手動で追記する（§ 6 参照）。

## 3. provider 切替

`.kaji/config.toml` (committed) は repository default を保持し、
`.kaji/config.local.toml` (gitignored) が overlay として個人選択を上書きする。

| 状態 | 効果 |
|------|------|
| overlay なし | `.kaji/config.toml` の `[provider]` がそのまま使われる |
| overlay あり | overlay の `[provider]` セクションがマージされる |
| overlay の `type = "local"` | LocalProvider 経路 |
| overlay 削除 | tracked default に戻る |

> **⚠️ overlay は worktree per-instance**: gitignored のため新規 worktree には
> **引き継がれず**、tracked `config.toml` の `[provider]` にフォールバックする
> （ズレを検出すると stderr に WARN）。メインリポジトリから `config.local.toml` を
> コピーするか、当該 worktree で `kaji local init` を再実行する。詳細は
> [Git Worktree ガイド](../guides/git-worktree.md) § provider overlay を参照。

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

# workflow 起動（local 専用。docs-only Issue は docs-local.yaml）
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
`review-poll` / PR review / `pr-fix` / `pr-verify`）に加え、着手前 step
（`review-ready` / `fix-ready` / `start`）も除いた local provider 版。`design` から
始まり、`/issue-create` / `/issue-start` は事前の手動実行が前提。`final-check` の
後は `issue-close` で終端し、PR は作らず `/issue-close` が `git merge --no-ff` +
frontmatter close を行う。docs-only Issue には同構成の `docs-local.yaml` を使う。

## 5. ID 文法

| 形式 | 意味 |
|------|------|
| `local-pc1-3` | machine_id `pc1` の 3 番目（フル形式） |
| `pc1-3` | 短縮形。provider=local 時のみ受理 |
| `3` | provider=local 時は machine_id を補完して `local-<self>-3` に解決 |
| `gh:153` | GitHub cache 由来の read-only 参照。`kaji sync from-github` で `.kaji/cache/gh-<n>.json` を populate して使う（後述 § 10）|

## 6. /issue-close の挙動（local）

`draft/design/local-mode/design.md` § 「local mode における `/issue-close` の手順」に従う 6 step:

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
解決され、`/issue-close` の Step 2 / 6 が当該 remote を叩く。
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

### `kaji issue comment` の verdict マーカー付与（local）

`kaji issue comment` に `--verdict-step <step> --verdict-status <STATUS>` を渡すと、
CLI が comment body の **1 行目**に決定的な HTML マーカー
`<!-- kaji-verdict: step=<step> status=<STATUS> -->` を付与してから
`.kaji/issues/<id>/comments/<timestamp>-<machine>.md` に永続化する。cross-skill 契約
（`issue-design` の BACK 再入検出）を CLI 層に置くための機構（ADR 008 決定 3）。

- **両フラグ同時必須**: 片方のみは exit 2。両方なしの従来呼び出しは body を一切変更しない
- **語彙検証（fail-loud）**: `--verdict-step` は `^[a-z][a-z0-9_-]*$`、`--verdict-status`
  は `PASS` / `RETRY` / `ABORT` / `BACK` / `BACK_<UPPER>`（`BACK_[A-Z0-9_]+`）。不正値は exit 2
- `--commit` との併用可（atomic commit の挙動は不変。marker は commit されるファイルの 1 行目に入る）
- github / local 両 provider で同一の振る舞い（marker 形式・付与位置・語彙検証）
- 実装: `kaji_harness/providers/markers.py` `build_kaji_verdict_marker`（契約の正本）

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
│   └── comments/<timestamp>-<machine>.md   (compact ISO 8601、例: 20260521T123456Z-pc1.md)
└── cache/gh-<n>.json       (GitHub Issue read-only cache。`kaji sync from-github` で populate)
```

## 8. `kaji pr` の挙動

`provider.type='local'` 配下では `kaji pr ...` は **bare-provider error** で
exit 2 する。PR 概念が無いため、`kaji pr create`
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

## 10. `kaji sync from-github`（GitHub cache populate）

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

# sync 状態の確認（出力は抜粋。実際は last_sync / elapsed も表示される）
$ kaji sync status
forge        github
repo         apokamo/kaji
cached       47 (gh-*.json under .kaji/cache/)
```

**スコープと前提**:

- 同期対象は **GitHub repo の open Issue 全件**。GitHub REST `/issues` endpoint は PR も返すため、`pull_request` キーを持つ entry は除外される
- `--include-closed` / `--state` / `--since` 等の追加 flag は本 release では未実装で、指定すると `exit 2` で fail-fast する（silent ignore しない）
- ローカル cache に存在するが GitHub 側で取得結果に含まれない Issue は cache に残り、`kaji_local.is_stale=true` フラグが立つ

## 11. 緊急時 fallback 運用

このリポジトリの通常運用は GitHub provider であり、local mode は GitHub 障害・
不通時の一時退避先。fallback への切替・複数 PC 運用・GitHub 復帰判断は
[Local Mode 緊急時 Fallback Runbook](../operations/local-mode-runbook.md) を参照。
