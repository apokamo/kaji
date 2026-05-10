# Local Mode CLI Guide

`kaji` を GitHub なしで運用するための最小ガイド。Phase 3-d で追加された
`kaji local init` と `feature-development-local.yaml` が前提。

## いつ使うか

- GitHub 不通 / 個人開発で issue / PR を立てたくない / GitLab 移行検討中
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

[execution]
default_timeout = 1800

[provider]
type = "local"
```

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

# workflow 起動（local 専用）
kaji run .kaji/wf/feature-development-local.yaml local-pc1-1
```

`kaji issue context` は frontmatter `branch_prefix` 優先 → `type:*` ラベル
mapping → `chore` fallback の優先順で context を解決する（`provider.type='local'`
/ `'github'` の両方で利用可能。`'gitlab'` は本コマンドでは未対応で `EXIT_INVALID_INPUT`
を返す）。`/issue-start` skill が worktree / branch 名を導出するために使う。

`feature-development-local.yaml` は `feature-development.yaml` の最終 step
（`i-pr`）を `issue-close` に差し替えたもの。PR は作らず、`/issue-close`
が `git merge --no-ff` + frontmatter close を行う。

## 5. ID 文法

| 形式 | 意味 |
|------|------|
| `local-pc1-3` | machine_id `pc1` の 3 番目（フル形式） |
| `pc1-3` | 短縮形。provider=local 時のみ受理 |
| `3` | provider=local 時は machine_id を補完して `local-<self>-3` に解決 |
| `gh:153` | GitHub cache 由来の read-only 参照。検証期間中は cache 自動 populate 未実装のため、必要時のみ手動で JSON 投入 |

## 6. /issue-close の挙動（local）

design.md L972-996 に従う 6 step:

1. Preflight check（uncommitted / branch / base 確認）
2. Base branch 最新化（`git fetch` + `merge --ff-only`）
3. Merge 実行（`git merge --no-ff --no-edit`）
4. Issue frontmatter 更新 + commit（`kaji issue close [issue_id] --reason completed`）
5. Cleanup（`git worktree remove` → `git branch -d`）
6. Push（remote ありなら `git push origin [default_branch]`）

Step 4 完了で Issue close は確定し、Step 5/6 の失敗は警告のみ。
`--reason` 未指定時の default は `completed`（GitHub Issue API の慣行と整合）。

## 7. ファイル / レイアウト

```
.kaji/
├── config.toml          (tracked, repo default)
├── config.local.toml    (gitignored, overlay)
├── counters/<machine>.txt   (gitignored)
├── issues/local-<machine>-<n>-<slug>/
│   ├── issue.md         (frontmatter + body)
│   └── comments/<seq>-<machine>.md
└── cache/issues/<n>.json    (GitHub の read-only キャッシュ。検証期間中は手動投入)
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
- `kaji sync from-gitlab` / `kaji sync status` は実装済（issue `local-p1-8`、後述 § 9b）。`provider.type='local'` 配下から `gl:N` で GitLab Issue を参照する経路を提供する
- `kaji sync from-github` は引き続き残課題（forge 採用先確定時に再評価）。GitHub 由来 cache (`gh:N` / `.kaji/cache/issues/N.json`) は手動投入のみ

## 9b. `kaji sync from-gitlab` / `kaji sync status`（GitLab cache populate）

`provider.type='local'` 配下から `gl:N` で GitLab Issue を参照する場合、
あらかじめ `kaji sync from-gitlab` で cache を populate する:

> GitLab project を kaji の primary forge として `provider.type='gitlab'` で
> 運用する場合のセットアップ / 必須前提（merge method / 認証）/ `glab` 認証は
> [GitLab Mode CLI Guide](gitlab-mode.md) を参照。本節は **`local` mode から
> read-only で GitLab Issue を参照** するための cache populate 経路のみを扱う。

```bash
# 初回 sync（[provider.gitlab].repo を config に書いておく場合）
$ kaji sync from-gitlab
Fetching open issues from gitlab.com:owner-group/repo-name ...
  page 1: 47 issues
Wrote 47 issues to .kaji/cache/.
Sync completed at 2026-05-10T08:42:13Z (47 issues, 1 pages, 1.2s).

# repo を CLI 引数で指定する場合
$ kaji sync from-gitlab --repo owner-group/repo-name

# cache から read
$ kaji issue view gl:42

# 統合表示
$ kaji issue list
local-pc1-1  open    Local issue 1
gl:42        open    Add foo bar
gl:43        open    Wire baz

# sync 状態の確認
$ kaji sync status
forge        gitlab
repo         owner-group/repo-name
last_sync    2026-05-10T08:42:13Z
elapsed      0h 5m 12s (312s)
cached       47 (gl-*.json under .kaji/cache/)
```

**スコープ**:

- 同期対象は **GitLab project の open Issue 全件**（初期実装スコープ）
- `--include-closed` / `--state` / `--since` 等の追加 flag は本 release では
  未実装で、指定すると `exit 2` で fail-fast する（silent ignore しない）
- ローカル cache に存在するが GitLab 側で取得結果に含まれない（= closed 化された）
  Issue は cache に残り、`kaji_local.is_stale=true` フラグが立つ。`kaji issue list`
  の既定 (`--state open`) では出ず、`--state closed` または `--state all` で
  確認できる

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
