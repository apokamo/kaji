---
id: local-p1-8
title: kaji sync from-gitlab + sync status + cache 自動 populate
state: closed
slug: kaji-sync-from-gitlab
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:02:13Z'
closed_at: '2026-05-10T02:02:06Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-p1-8`
> **Branch**: `feat/local-p1-8`

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] kaji sync from-gitlab + sync status + cache 自動 populate

Issue: local-p1-8
EPIC: local-p1-4 (GitLab 対応検証 EPIC)

## 概要

`provider.type='local'` 配下から GitLab Issue を `gl:N` で参照できるようにするため、
`kaji sync from-gitlab` で GitLab project の open Issue 全件を `.kaji/cache/gl-<iid>.json`
に atomic write し、`kaji sync status` で cache 状態（件数 / 最終 sync 時刻 / 経過時間）を
表示する。`kaji issue list` も local Issue + cached GitLab Issue を統合表示する。

## 背景・目的

EPIC `local-p1-4` 子 Issue #4。`local-p1-5`（GitLabProvider 実装）/
`local-p1-6`（`kaji issue/pr` GitLab passthrough + `gl:N` 規約）が完了済みで、
`provider.type='gitlab'` 直下では `glab` 経由で Issue 操作が可能になっているが、
**`provider.type='local'` 配下から `gl:42` を参照する経路は未整備**。

現状:

- `LocalProvider.view_cached_issue()` は `.kaji/cache/issues/<n>.json` を読むが、
  この path は **`gh:` 経路の cache 専用** であり、`gl:` の cache 配置は決まっていない。
- `normalize_id()` は `gl:N` を `provider=local` 配下で `ResolvedId(kind="remote_cache",
  value="<iid>", raw="gl:42")` に正規化する。が、cache 自体が populate されないため
  `kaji issue view gl:42` は常に `IssueNotFoundError` で停止する
  （`kaji_harness/providers/local.py:737`）。
- `kaji sync from-github` も同等に未実装で、forge 採用先確定時に再評価する残課題として
  `docs/cli-guides/local-mode.md:197` に記載。

本 Issue の責務はこのうち **GitLab 側だけ**を先取りして実装すること。

### ユーザーストーリー

- **kaji ユーザーとして**、`provider.type='local'` 配下で `kaji sync from-gitlab`
  を 1 度実行すれば、その後 `kaji issue view gl:42` / `kaji issue list` が cache 経由で
  GitLab Issue を読める状態にしたい。
- **kaji ユーザーとして**、cache の同期状態を `kaji sync status` で確認し、最終 sync が
  いつ行われたかと cache 件数を把握したい。
- **kaji ユーザーとして**、`kaji issue list` が local Issue + cached GitLab Issue を
  統合表示し、両者を `gl:` prefix で区別できる状態にしたい。
- **kaji ユーザーとして**、`--include-closed` / `--state` / `--since` 等の追加 flag は
  本 Issue では未実装だが、**silently ignore されず exit 2 で fail-fast** されることで
  「指定したつもりが効いていない」事故を避けたい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 既存 `.kaji/cache/issues/<n>.json` 配下に `gl:` cache も詰め込む（`<iid>.json` で同名衝突） | `gh:42` と `gl:42` が同 issue 番号を持つと file path が衝突する。`gh-` / `gl-` prefix で論理的分離が必須 |
| 既存 `.kaji/cache/issues/` の内側に prefix 付き `gl-<iid>.json` を置く | Issue 本文が `.kaji/cache/gl-<iid>.json` を明示しているため、それに従う。`issues/` subdir は `gh:` 専用の既存 layout として残し、新 cache layout は forge 別 prefix を採る |
| `glab` CLI の `glab issue list --output-format details` を使う | `--output-format` は `details / ids / urls` のみで構造化 JSON を返さない（`local-p1-5` 設計書 § Primary Sources 参照）。安定 parse のため `glab api projects/<repo>/issues` REST を採用する |
| pagination 未実装で per_page=100 上限の 1 回呼び切りに留める | Issue 本文が「open Issue 全件」を完了条件にしているため、`>100` 件の project で silent truncate が発生すると要件未達になる。pagination ループ必須 |
| `--include-closed` / `--state` / `--since` を未実装のまま silently ignore | Issue 完了条件で「fail-fast する」と明記。silent ignore は完了条件違反 |
| `[provider.gitlab].repo` を `--repo group/project` flag に置換える | `provider.type='local'` 配下では `[provider.gitlab]` セクションがあれば config-driven、なければ flag override 可、の **両対応** が user-friendly。flag を必須にすると毎回タイプする負担になる |
| `httpx` で GitLab REST を直接叩く | `pyproject.toml:21` の `[project.dependencies]` に `httpx` 不在。CLI 戦略 (`local-p1-5` § 確定事項 #1) 通り `glab` subprocess 経由で統一する |

## インターフェース

### 1. `kaji sync` subcommand 新設

#### `kaji sync from-gitlab`

```
kaji sync from-gitlab [--repo <group/project>] [--quiet]

GitLab project の open Issue 全件を cache に同期する。

Options:
  --repo <group/project>  GitLab repo を明示指定。未指定なら
                          [provider.gitlab].repo を使用（必須のいずれか）。
  --quiet                 進捗ログを抑制（最終サマリのみ）。

Exit codes:
  0  成功（fetch/write エラーなし）
  2  invalid args / config 不在 / glab CLI 不在 / GitLab API エラー
  3  cache 書き込み失敗（partial write が残らないよう atomic write は使用済）
```

期待される標準出力（人間可読 / `--quiet` 未指定時）:

```text
Fetching open issues from gitlab.com:owner-group/repo-name ...
  page 1: 100 issues
  page 2: 47 issues
Wrote 147 issues to .kaji/cache/ (3 newly added, 144 updated, 0 unchanged signature).
Sync completed at 2026-05-10T08:42:13Z (147 issues, 2 pages, 1.8s).
```

期待される `.kaji/cache/.sync-meta.json`:

```json
{
  "schema_version": 1,
  "forge": "gitlab",
  "repo": "owner-group/repo-name",
  "last_sync_at": "2026-05-10T08:42:13Z",
  "issue_count": 147,
  "pages_fetched": 2
}
```

#### `kaji sync status`

```
kaji sync status [--json]

cache の最終 sync 状態を表示する。

Options:
  --json   table 形式の代わりに JSON で出力する。

Exit codes:
  0  成功
  2  invalid args
```

table 出力例（既定）:

```text
forge        gitlab
repo         owner-group/repo-name
last_sync    2026-05-10T08:42:13Z
elapsed      1h 23m 12s (4992s)
cached       147 (gl-*.json under .kaji/cache/)
```

未 sync の場合（`.sync-meta.json` 不在）:

```text
forge        (none)
repo         (none)
last_sync    (never)
elapsed      n/a
cached       0
```

JSON 出力（`--json`）:

```json
{
  "forge": "gitlab",
  "repo": "owner-group/repo-name",
  "last_sync_at": "2026-05-10T08:42:13Z",
  "elapsed_seconds": 4992,
  "elapsed_human": "1h 23m 12s",
  "issue_count": 147
}
```

未 sync 時の JSON:

```json
{
  "forge": null,
  "repo": null,
  "last_sync_at": null,
  "elapsed_seconds": null,
  "elapsed_human": null,
  "issue_count": 0
}
```

#### 受理しない flag（fail-fast）

`kaji sync from-gitlab` で以下の flag は **存在を検知して exit 2 で停止**する
（argparse の choices ではなく、明示的な error message を返す）:

| flag | 受理しない理由 | 期待 stderr |
|------|----------------|-------------|
| `--include-closed` | 本 Issue では closed Issue は cache に保持するのみ（後述）で、明示的な「closed も追加 fetch」は OUT scope | `error: --include-closed is not implemented in this release; reopen tracking issue to add it.` |
| `--state <s>` | open 全件のみが本 Issue のシンプル契約 | `error: --state is not implemented in this release; this command always fetches state=opened.` |
| `--since <iso>` | 本 Issue は full sync のみ | `error: --since is not implemented in this release; this command always performs a full sync.` |

「未実装の flag を silently ignore しない」が完了条件。argparse は未知 flag に対し
既定で `unrecognized arguments` を返すが、上記 3 つは「将来予約済」として **明示的に
add_argument(... action="store_true") + 採用拒否**することで、user に意図的に
拒否されている旨を伝える。

### 2. cache 配置

```
.kaji/cache/
├── gl-<iid>.json          # GitLab Issue の cache（本 Issue で新設）
├── .sync-meta.json        # 最終 sync 時刻 / repo / forge（本 Issue で新設）
└── issues/
    └── <n>.json           # GitHub Issue の cache（既存。本 Issue では触らない）
```

#### `.kaji/cache/gl-<iid>.json` の schema

GitLab REST `GET /projects/:id/issues/:iid` の応答 + 内部 wrapper を最小限に保つ。
**`kaji_local`** wrapper field で kaji 側の同期メタを保持し、GitLab 由来 field
（`issue.state` 等）は **書き換えない**（嘘をつかない invariant）:

```json
{
  "schema_version": 1,
  "forge": "gitlab",
  "fetched_at": "2026-05-10T08:42:11Z",
  "kaji_local": {
    "is_stale": false,
    "last_seen_at": "2026-05-10T08:42:11Z",
    "staled_at": null
  },
  "issue": {
    "iid": 42,
    "id": 12345,
    "title": "Add foo bar",
    "description": "...",
    "state": "opened",
    "labels": ["type:feature", "priority:high"],
    "web_url": "https://gitlab.com/owner-group/repo-name/-/issues/42",
    "created_at": "2026-04-30T10:00:00.000Z",
    "updated_at": "2026-05-09T22:11:33.000Z",
    "author": {"username": "alice", "id": 1},
    "milestone": null
  }
}
```

#### `kaji_local` field の意味

| field | 値域 | 意味 |
|-------|------|------|
| `is_stale` | bool | 直近 sync で fetch 結果に **含まれなかった** 場合 `true`。GitLab 側で closed 化された / 削除された / `state=opened` の検索結果から外れた、のいずれかを意味する（kaji は区別しない） |
| `last_seen_at` | UTC ISO-8601 | 直近で fetch 結果に含まれていた時刻。`is_stale=true` でも保持される（最後に open だった時刻） |
| `staled_at` | UTC ISO-8601 \| null | `is_stale=true` に **遷移した時刻**。`is_stale=false` の間は `null`。stale → fresh 復帰時（GitLab 側で再 open 等）は `null` に戻す |

> `issue.state` は GitLab 由来の値をそのまま保持する（`"opened"` / `"closed"`）。
> kaji 側で書き換えると「cache の正本性」が壊れる（user が `kaji issue view gl:42` で
> 元の GitLab state を読めなくなる）。`kaji issue list` は `kaji_local.is_stale` と
> `issue.state` を **両方** 見て表示 state を決定する（後述 § kaji issue list での
> cache 統合）。

> **本 Issue では comments を cache しない**。cache は list / view 用途であり、
> notes 取得まで行うと sync 時間が線形オーダーで増える（issue 数 × notes API 1 往復）。
> comments が必要な user 経路は将来の拡張（OUT scope）。

#### `.sync-meta.json` の schema

```json
{
  "schema_version": 1,
  "forge": "gitlab",
  "repo": "owner-group/repo-name",
  "last_sync_at": "2026-05-10T08:42:13Z",
  "issue_count": 147,
  "pages_fetched": 2
}
```

`schema_version` を最初から持たせるのは将来 schema 拡張時の forward-compat
（reader 側が version mismatch を fail-fast できる）。

### 3. `LocalProvider` 拡張

#### 新規 method

```python
def view_cached_gitlab_issue(self, iid: str) -> Issue:
    """``.kaji/cache/gl-<iid>.json`` から read-only に Issue を組み立てる。

    iid は 1 以上の正整数文字列。frontend は ``gl:42`` → ``"42"`` を渡す。
    cache fixture が無ければ ``IssueNotFoundError``（`kaji sync from-gitlab` を
    案内する error message）。
    """

def view_cached_issue_by_resolved(self, rid: ResolvedId) -> Issue:
    """``ResolvedId(kind="remote_cache", raw="gh:42" | "gl:42", value="42")`` から
    forge を判定して該当 cache reader を呼ぶ薄い dispatcher。

    既存 ``view_cached_issue(number)`` は GitHub 用 reader として残す
    （後方互換）。
    """
```

#### 既存 `list_issues()` 拡張

`provider.type='local'` 配下の `LocalProvider.list_issues()` は cache 配下の
`.kaji/cache/gl-*.json` も統合する。表示形式:

```text
local-p1-1  open    Title of local issue
local-p1-3  closed  Another local issue
gl:42           open    GitLab issue 42 title
gl:43           open    GitLab issue 43 title
```

実装方針:

```python
def list_issues(self, *, state="open", labels=None, limit=None) -> list[Issue]:
    out = self._list_local_issues(state, labels, limit)  # 既存ロジック
    if self._cache_dir_root.exists():  # .kaji/cache/
        out.extend(self._list_cached_gitlab_issues(state, labels))
    if limit is not None:
        out = out[:limit]
    return out
```

cache 由来の `Issue.id` は `gl:<iid>` 形式（例 `Issue(id="gl:42", ...)`）。
`Issue` model 自体は `id: str` なので変更なし。`labels` フィルタは local
issue と同じ全件 AND マッチ。`state="open"` の場合 cache JSON の
`issue.state="opened"` を `"open"` に正規化して比較。

#### 新規 path helper

```python
@property
def _cache_dir_root(self) -> Path:
    return self.repo_root / ".kaji" / "cache"

@property
def _sync_meta_path(self) -> Path:
    return self.repo_root / ".kaji" / "cache" / ".sync-meta.json"

def _gitlab_cache_path(self, iid: str) -> Path:
    return self.repo_root / ".kaji" / "cache" / f"gl-{iid}.json"
```

既存 `_cache_dir`（`= .kaji/cache/issues`、`gh:` 用）は **改名しない**。
GitHub と GitLab の cache layout を分離するための既存方針として残す。

### 4. 新規モジュール `kaji_harness/sync.py`

`cli_main.py` を肥大化させないため、`kaji sync` のロジック本体を `sync.py`
に分離する。CLI 層からは `cmd_sync_from_gitlab(args)` / `cmd_sync_status(args)`
を呼ぶ薄いラッパーのみ。

```python
@dataclass(frozen=True)
class SyncResult:
    issue_count: int
    pages_fetched: int
    elapsed_seconds: float
    last_sync_at: str  # UTC ISO-8601

def sync_from_gitlab(
    *,
    config: KajiConfig,
    repo_override: str | None,
    quiet: bool,
    logger: RunLogger | None = None,
) -> SyncResult:
    """GitLab project から open Issue を全件 fetch して cache を populate する。

    Raises:
        SyncError: glab CLI 不在 / GitLab API エラー / repo 未設定。
        OSError: cache 書き込み失敗（atomic write の失敗パス）。
    """

@dataclass(frozen=True)
class SyncStatus:
    forge: str | None
    repo: str | None
    last_sync_at: str | None
    elapsed_seconds: float | None
    issue_count: int

def read_sync_status(*, config: KajiConfig) -> SyncStatus:
    """cache 状態を `.sync-meta.json` + `gl-*.json` の数から組み立てる。

    `.sync-meta.json` 不在時は forge=None / issue_count=0 を返す
    （error にしない。未 sync 状態は正常状態の 1 種）。
    """
```

`SyncError` は `RuntimeError` サブクラスとして `sync.py` 内に定義（`GitLabProviderError`
と分離。GitLab provider 経由ではない sync 固有のエラー）。

### 5. CLI 層 (`cli_main.py`) 拡張

#### subparsers 登録

```python
def _register_sync(subparsers):
    p = subparsers.add_parser("sync", help="Cache synchronization commands")
    sync_subs = p.add_subparsers(dest="sync_command", required=True)

    fg = sync_subs.add_parser(
        "from-gitlab",
        help="Sync open issues from a GitLab project into local cache",
    )
    fg.add_argument("--repo", default=None, type=str,
                    help="GitLab repo (group/project). Defaults to [provider.gitlab].repo")
    fg.add_argument("--quiet", action="store_true")
    # 将来予約 flag (本 Issue では fail-fast)
    fg.add_argument("--include-closed", action="store_true",
                    help=argparse.SUPPRESS)
    fg.add_argument("--state", default=None, type=str,
                    help=argparse.SUPPRESS)
    fg.add_argument("--since", default=None, type=str,
                    help=argparse.SUPPRESS)

    st = sync_subs.add_parser("status", help="Show local cache sync status")
    st.add_argument("--json", action="store_true")
```

#### dispatcher

```python
elif args.command == "sync":
    if args.sync_command == "from-gitlab":
        return cmd_sync_from_gitlab(args)
    if args.sync_command == "status":
        return cmd_sync_status(args)
```

#### `cmd_sync_from_gitlab` 概略

```python
def cmd_sync_from_gitlab(args) -> int:
    # 1. 将来予約 flag を fail-fast
    if args.include_closed:
        sys.stderr.write(
            "error: --include-closed is not implemented in this release; "
            "reopen tracking issue to add it.\n"
        )
        return EXIT_INVALID_INPUT  # 2
    if args.state is not None:
        ...  # 同等
    if args.since is not None:
        ...

    # 2. config resolution
    config = KajiConfig.discover()  # cwd 起点

    # 3. sync 本体
    try:
        result = sync_from_gitlab(
            config=config,
            repo_override=args.repo,
            quiet=args.quiet,
        )
    except SyncError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_INVALID_INPUT
    except OSError as exc:
        sys.stderr.write(f"error: cache write failed: {exc}\n")
        return EXIT_RUNTIME_ERROR  # 3

    # 4. 完了サマリ（quiet 時も最低 1 行は出す）
    sys.stdout.write(
        f"Sync completed at {result.last_sync_at} "
        f"({result.issue_count} issues, {result.pages_fetched} pages, "
        f"{result.elapsed_seconds:.1f}s).\n"
    )
    return EXIT_OK
```

### 6. 入力 / 出力 / 副作用サマリ

#### 入力

- `[provider.gitlab].repo`（config）または `--repo <group/project>` flag
- `glab` CLI（PATH 上）+ 認証（`glab auth login` 済 or `GITLAB_TOKEN`）
- 既存 `.kaji/cache/gl-*.json`（あれば overwrite、なければ新規 write）

#### 出力（副作用）

- `.kaji/cache/gl-<iid>.json` を全 open Issue 分 atomic write
- `.kaji/cache/.sync-meta.json` を atomic write
- stdout: 進捗 / サマリ（`--quiet` で抑制）
- stderr: 警告 / エラー（fail-fast 経路）

#### 出力（戻り値 / IO）

- exit code: 0 / 2 / 3
- 副作用は冪等（同 issue を何度 sync しても結果は同じ。closed 化された entry は
  cache に残るのみで delete はしない）

### 7. 使用例

```bash
# 初回 sync
$ kaji sync from-gitlab
Fetching open issues from gitlab.com:owner-group/repo-name ...
  page 1: 47 issues
Wrote 47 issues to .kaji/cache/ (47 newly added, 0 updated, 0 unchanged signature).
Sync completed at 2026-05-10T08:42:13Z (47 issues, 1 pages, 1.2s).

# cached issue を read
$ kaji issue view gl:42
# Add foo bar

(description body...)

# 統合表示
$ kaji issue list
local-p1-8  open    kaji sync from-gitlab + sync status + cache 自動 populate
gl:42           open    Add foo bar
gl:43           open    Wire baz

# status 確認
$ kaji sync status
forge        gitlab
repo         owner-group/repo-name
last_sync    2026-05-10T08:42:13Z
elapsed      0h 5m 12s (312s)
cached       47 (gl-*.json under .kaji/cache/)

# 将来予約 flag（fail-fast）
$ kaji sync from-gitlab --include-closed
error: --include-closed is not implemented in this release; reopen tracking issue to add it.
$ echo $?
2
```

### 8. エラーケース

| 想定失敗 | 戻り値 / 例外 / message |
|---------|-------------------------|
| `provider.gitlab.repo` が config 不在 + `--repo` flag も無し | exit 2 / `error: 'kaji sync from-gitlab' requires either --repo or [provider.gitlab].repo in config.` |
| `glab` CLI 不在 | exit 2 / `error: 'glab' CLI not found in PATH. Install glab to use 'kaji sync from-gitlab'.` |
| `glab auth login` 未済（401） | exit 2 / glab stderr を `error: glab API authentication failed: ...` で wrap |
| GitLab project 不在（404） | exit 2 / `error: GitLab project 'group/project' not found or inaccessible.` |
| GitLab API rate limit 等の 5xx | exit 2 / `error: GitLab API failed (HTTP 5xx): {body抜粋}` |
| pagination 中（fetch phase）の失敗 | **cache には何も書かない**（fetch 全成功まで write phase に入らない all-or-nothing 契約。`.sync-meta.json` も書かない） + exit 2 |
| write phase（fetch 全成功後の cache file 書き込み）中の OS エラー | atomic write 単位で部分書き込みは残らないが、複数 file の途中で失敗するとそれまでに書いた entry は残る。`.sync-meta.json` は **書かない**（最終 sync 時刻が嘘にならない）+ exit 3。user は再実行で復旧する |
| `.kaji/cache/` 書き込み権限なし | exit 3 / OSError wrap |
| `--include-closed` / `--state` / `--since` 指定 | exit 2 / 個別 error message（前述） |
| `kaji sync status` で cache が壊れた JSON | exit 2 / `error: .sync-meta.json malformed: ...`（破損 detection は人間が手で消すか re-sync する想定）|
| `kaji issue list` で `gl-X.json` が壊れている | 警告ログ（stderr）を出した上で **当該 entry をスキップ** して継続。1 件の corrupted entry で list 全体を落とさない |

## 制約・前提条件

### 依存

- `glab` CLI が PATH にある（`local-p1-5` で既に依存として確立）
- `[provider.gitlab].repo` が設定済 OR `--repo` flag が渡される
- `provider.type` の値は **問わない**。`local` 配下が主用途だが、`gitlab` でも `github`
  でも config に `[provider.gitlab].repo` があれば動作する（mental model: sync は
  「GitLab → ローカル cache」の単方向。reader 側の provider.type に依存しない）

### 互換性保持

- 既存 `.kaji/cache/issues/<n>.json`（`gh:` 経路）は touch しない。reader である
  `view_cached_issue(number)` の signature / 挙動も維持
- `kaji issue list` の既存出力（local issue のみ）は cache が空なら **完全互換**。
  cache 投入後のみ `gl:` 行が追加される
- `[provider.gitlab]` を持たない既存 config は本 Issue で **破壊されない**
  （`kaji sync` を呼ばなければ何も起きない）

### パフォーマンス

- 1 ページ 100 issue / pagination ループあり。500 issues の project で 5 ページ ×
  ~1s = 5s 程度を目安。ローカル disk への atomic write は 1 issue あたり < 10ms
- 並行実行は想定しない（複数の `kaji sync from-gitlab` を同時起動した場合、tmp
  file 名衝突回避のため tmp 名に PID を含める。`os.replace` 自体は atomic）
- per_page=100 が GitLab REST API の上限（後述 § Primary Sources / `local-p1-5` 設計書
  の同節と同根拠）

### スコープ境界

#### IN（本 Issue で実装）

**コード**:

- `kaji_harness/sync.py`（新設）— `sync_from_gitlab()` / `read_sync_status()` /
  `SyncResult` / `SyncStatus` / `SyncError`
- `kaji_harness/cli_main.py` 拡張 — `_register_sync()` / `cmd_sync_from_gitlab()`
  / `cmd_sync_status()` / dispatcher 分岐
- `kaji_harness/providers/local.py` 拡張 — `view_cached_gitlab_issue()` /
  `view_cached_issue_by_resolved()` / `list_issues()` の cache 統合 /
  `_cache_dir_root` / `_sync_meta_path` / `_gitlab_cache_path` helper
- `kaji_harness/cli_main.py` の `kaji issue view` dispatcher（`:1102`） — `rid.raw`
  prefix 判定で gh: vs gl: を分岐。GitLab cache reader を呼ぶ経路を追加

**テスト**:

- `tests/test_sync_from_gitlab.py`（新設）— Small / Medium テスト
- `tests/test_local_cache_gitlab.py`（新設） — `view_cached_gitlab_issue` /
  `list_issues` 統合表示の Small / Medium テスト
- 既存 `tests/test_phase3c_dispatcher.py` の `gh:` cache テストが回帰しないことを確認

**docs**:

- `docs/cli-guides/local-mode.md` 9 章「既知の制限」の `kaji sync from-github` 行を
  更新（`from-gitlab` は実装済 / `from-github` は引き続き残課題と区別する）
- `docs/cli-guides/local-mode.md` に `kaji sync from-gitlab` / `kaji sync status`
  の使用例を追加（`gl:N` cache 経路の最小手順）
- `docs/operations/local-mode-runbook.md` の `kaji sync` 言及箇所（`:190`）を
  「from-gitlab は実装済」に更新

#### OUT（後続子 Issue / EPIC OUT）

- `kaji sync from-github` → 検証期間中は forge 採用先確定後の対応（`local-p1-12`）
- `kaji sync local-to-gitlab-plan` → bucket `local-p1-1` 残務
- closed issue の追加 fetch / state filter / since filter → 採用確定後の運用要件に応じて
  別 Issue で対応（本 Issue では fail-fast のみ）
- comments の cache → 将来拡張（`gl:N` の view で notes が必要になった時点）
- `kaji issue list` の `--limit` 適用順 / sort 順の精緻化 → 既存 local list の慣習に
  従い、cache 由来 entry は **末尾に追加** する単純戦略を採用。再ソート要件が出れば
  別 Issue
- `kaji sync from-gitlab --watch` 等の常駐モード

## 方針（Minimal How）

### sync の主要データフロー（all-or-nothing）

**契約**: fetch phase が全 page 成功するまで cache file に **一切書き込まない**。
fetch 中の失敗時は既存 cache を一切変更しない。これにより MF-1 で指摘された
「2 ページ目失敗時も 1 ページ目 cache は残す」という partial-write 由来の不整合を
排除する。

```
config.discover()
  └─> KajiConfig (provider.gitlab.repo)
       │
       ▼
cli args (--repo override)
       │
       ▼
sync_from_gitlab(config, repo_override, quiet)
  │
  ├─[ phase 0: 前提チェック ]
  │  ├─> _resolve_repo()                       # config or override 必須チェック
  │  └─> _check_glab_present()                 # shutil.which("glab")
  │
  ├─[ phase 1: fetch (全 page 完了まで in-memory) ]
  │  └─> _fetch_open_issues_paginated(repo)    # glab api projects/<enc>/issues?state=opened&per_page=100&page=N
  │       └─> page=1, 2, ... until empty array / <100件
  │       └─> 任意 page の失敗 → SyncError、cache は触らずに return
  │
  ├─[ phase 2: stale 判定 ]
  │  ├─> _list_existing_cache(cache_dir)       # 既存 gl-*.json の iid set
  │  ├─> fetched_iids = {entry["iid"] for entry in issues}
  │  ├─> stale_iids = existing_iids - fetched_iids   # fetch 結果に無い既存 entry
  │  └─> fresh_iids  = fetched_iids                  # fetch 結果に含まれる
  │
  ├─[ phase 3: write (atomic per file) ]
  │  ├─> for entry in issues:                       # fresh entry を overwrite
  │  │     _write_fresh_cache_file(entry, cache_dir, now)
  │  ├─> for iid in stale_iids:                     # stale 化（issue 本体は触らない）
  │  │     _mark_cache_stale(cache_dir / f"gl-{iid}.json", now)
  │  └─> _write_sync_meta(meta, sync_meta_path)     # 最後に meta を書く
  │
  └─> SyncResult(issue_count, pages_fetched, elapsed_seconds, last_sync_at)
```

phase 1 が失敗した時点で `.kaji/cache/` は **完全に変更されない**（既存 file の
mtime も含めて）。phase 3 の途中失敗は OS エラー（権限 / 書き込み不能）に限られる。
phase 3 失敗時は既書き込み分は残るが `.sync-meta.json` は書かれず、user は
再実行で完全状態に戻せる。

### pagination 戦略

GitLab REST API は `?page=N` (1-indexed) + `?per_page=100`（上限 100）で pagination
する。本実装は **page を 1 から増やしながら、空配列または `< per_page` 件が返るまで
ループ**する単純戦略を採用:

```python
def _fetch_open_issues_paginated(repo: str) -> tuple[list[dict], int]:
    encoded = quote(repo, safe="")
    pages_fetched = 0
    issues: list[dict] = []
    page = 1
    MAX_PAGES = 200  # 暴走防止: 100 issues × 200 pages = 20000 issues 上限
    while page <= MAX_PAGES:
        endpoint = f"projects/{encoded}/issues?state=opened&per_page=100&page={page}"
        payload = _glab_api_get(endpoint)
        if not isinstance(payload, list):
            raise SyncError(f"glab api returned non-array JSON for issue list (page {page})")
        if not payload:
            break
        issues.extend(payload)
        pages_fetched += 1
        if len(payload) < 100:
            break
        page += 1
    if page > MAX_PAGES:
        raise SyncError(
            f"sync aborted after {MAX_PAGES} pages (>{MAX_PAGES * 100} issues). "
            f"Check repo or contact maintainer."
        )
    return issues, pages_fetched
```

`MAX_PAGES=200` は暴走防止のサニティリミット（issue 本文の「open Issue 全件」とは
矛盾しない。20000 issues / 1 project は事実上現れない）。実需要が出れば設定可能化する。

### `glab api` 起動方針

`local-p1-5` の `_glab_api_get()` パターンを **再利用しない**。本 Issue の sync は
`provider.type='local'` 配下でも動かしたいため、`GitLabProvider` インスタンスを
hard 依存にしない:

```python
def _glab_api_get(repo: str, endpoint: str) -> object:
    """Standalone な glab api wrapper (sync 用)。

    GitLabProvider._glab_api_get と機能等価だが、provider instance に依存しない。
    将来 GitLabProvider と統合する余地は残す（共通化リファクタは後送り）。
    """
    if shutil.which("glab") is None:
        raise SyncError(
            "'glab' CLI not found in PATH. Install glab to use 'kaji sync from-gitlab'."
        )
    cmd = ["glab", "api", "--hostname", "gitlab.com", endpoint]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SyncError(f"glab failed (exit {proc.returncode}): {proc.stderr or proc.stdout}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(f"glab returned invalid JSON: {exc}") from exc
```

`--hostname gitlab.com` は `local-p1-5` の方針（self-hosted 非対応）と整合。

### atomic write 戦略

既存 `kaji_harness/providers/local.py:_atomic_write()` を **`sync.py` から再利用**する
（同 module の private 関数を `sync.py` で import するか、`_atomic_write` を
`kaji_harness/_atomic_io.py` 等に切り出す）。後者の方が module 結合度を下げるが、
本 Issue ではまず前者（local.py から re-export）でスコープを最小化し、再利用箇所が
3 箇所を超えた時点で `_atomic_io.py` への抽出を検討する（ファイル分割は後送り）:

```python
# kaji_harness/providers/local.py
__all__ = [..., "_atomic_write"]  # 既存

# kaji_harness/sync.py
from .providers.local import _atomic_write
```

fresh entry の write:

```python
def _write_fresh_cache_file(entry: dict, cache_dir: Path, now_iso: str) -> None:
    iid = entry["iid"]
    path = cache_dir / f"gl-{iid}.json"
    wrapped = {
        "schema_version": 1,
        "forge": "gitlab",
        "fetched_at": now_iso,
        "kaji_local": {
            "is_stale": False,
            "last_seen_at": now_iso,
            "staled_at": None,
        },
        "issue": entry,
    }
    _atomic_write(path, json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n")
```

### stale 化された entry の扱い（MF-2 対応）

**Issue 完了条件**: 「cache に存在するが GitLab 側で open でなくなった Issue は
cache に残る（削除されない）」。一方で `kaji issue list` の既定（`--state open`）
で stale entry が `open` と表示されるのは誤情報になる（MF-2 の指摘）。

**戦略**: fetch 結果に **無い** 既存 cache entry を **削除せず**、wrapper の
`kaji_local.is_stale` を `true` に更新する。`issue.state` 等の GitLab 由来 field は
**触らない**（cache の正本性を壊さない）。`list_issues` は `kaji_local.is_stale` と
`issue.state` を組み合わせて表示 state を決定する（後述 § kaji issue list での
cache 統合）。

```python
def _mark_cache_stale(path: Path, now_iso: str) -> None:
    """既存 cache entry を stale 化する。issue 本体は触らない。

    既に is_stale=true なら staled_at は変更せず（最初に stale 化した時刻を保持）、
    last_seen_at も変更しない（is_stale=false の最後の sync 時刻を保持）。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 壊れた cache file は触らない（list 経路の skip と同方針）
        return
    if not isinstance(payload, dict):
        return
    kl = payload.get("kaji_local") or {}
    if kl.get("is_stale"):
        # 既に stale 化済 → 何もしない
        return
    kl["is_stale"] = True
    kl["staled_at"] = now_iso
    # last_seen_at は前回 fresh sync 時刻のまま保持（書き換えない）
    payload["kaji_local"] = kl
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
```

**stale → fresh 復帰**: GitLab 側で再 open された issue が次回 sync の fetch 結果に
含まれた場合、`_write_fresh_cache_file` が wrapper を上書きして
`is_stale=false / staled_at=null / last_seen_at=now` に戻す。明示的な復帰ロジックは
不要（fresh write が wrapper を完全に置換する自然な帰結）。

**完了条件との整合**:
- 「cache に残る（削除されない）」→ ✅ delete はしない
- 「kaji issue list で stale を open と誤表示しない」→ ✅ `is_stale=true` は open
  filter から除外

### `view_cached_issue` 経路の分岐

```python
# cli_main.py:1102 周辺
if rid.kind == "remote_cache":
    if rid.raw.startswith("gl:"):
        issue = provider.view_cached_gitlab_issue(rid.value)
    else:
        # gh: prefix or 数値（既存 GitHub cache 経路）
        issue = provider.view_cached_issue(rid.value)
```

`ResolvedId.raw` が `"gl:42"` で始まれば GitLab、そうでなければ既存 GitHub 経路。
`raw` を判定材料にすることで `ResolvedId` の signature 変更を回避する。

### `kaji issue list` での cache 統合（MF-2 対応）

**表示 state の決定ルール**: cache entry の `kaji_local.is_stale` と GitLab 由来
`issue.state` を組み合わせて、`list_issues()` が user に見せる state を
**1 つの値** に正規化する:

| `kaji_local.is_stale` | `issue.state` (GitLab) | 表示 state | 解釈 |
|-----------------------|-----------------------|-----------|------|
| `false` | `"opened"` | `"open"` | 直近 sync で open として確認できた issue |
| `false` | `"closed"` | `"closed"` | （理論上は `state=opened` filter で来ないはずだが、防御的に closed として扱う） |
| `true` | `"opened"` | `"closed"` | **stale 化された issue**。GitLab 側で closed / 削除された可能性。kaji list では closed として扱う |
| `true` | `"closed"` | `"closed"` | （sync 中に opened → closed 化したケース。closed） |

つまり **`is_stale=true` は無条件に "closed" 扱い**。`issue.state` の値に依らない。
これにより MF-2 の「stale entry が open と誤表示される」問題が解消する。

`LocalProvider.list_issues()` の末尾に cache 由来 issue を append:

```python
def _list_cached_gitlab_issues(self, state: str, labels: list[str] | None) -> list[Issue]:
    if not self._cache_dir_root.exists():
        return []
    out: list[Issue] = []
    for path in sorted(self._cache_dir_root.glob("gl-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(
                f"warning: skipping malformed cache entry {path.name}: {exc}\n"
            )
            continue
        issue_payload = payload.get("issue") or {}
        kl = payload.get("kaji_local") or {}
        is_stale = bool(kl.get("is_stale", False))
        # 表示 state の正規化:
        #   is_stale=true → 無条件 "closed"
        #   is_stale=false → GitLab 'opened' → 'open' / 'closed' → 'closed'
        gl_state = issue_payload.get("state", "")
        if is_stale:
            display_state = "closed"
        elif gl_state == "opened":
            display_state = "open"
        else:
            display_state = "closed"
        if state != "all" and display_state != state:
            continue
        label_names = issue_payload.get("labels") or []
        if labels and not all(label in label_names for label in labels):
            continue
        out.append(Issue(
            id=f"gl:{issue_payload.get('iid')}",
            title=str(issue_payload.get("title") or ""),
            body=str(issue_payload.get("description") or ""),
            state=display_state,
            labels=[Label(name=str(name)) for name in label_names],
            comments=[],
        ))
    return out
```

`Issue.id="gl:42"` 形式で詰めることで、出力経路（`{issue.id}\t...`）が自然に
`gl:` prefix で表示される。

> **後方互換**: 旧 schema（`kaji_local` field を持たない、本 Issue 着手前に user が
> 手動投入した cache file）に対しては `is_stale=false` として扱う（`kl.get("is_stale",
> False)` の default 値）。新 sync が動けば自然に新 schema に上書きされる。

### sync_meta.json の atomic 書き込み + 失敗時 invariant

`.sync-meta.json` は **phase 3 の全 write 完了後にのみ書く**（前述データフロー）。
- **phase 1（fetch）失敗時**: cache file は一切触れていないので `.sync-meta.json`
  も更新しない。古い meta が残る。
- **phase 3（write）失敗時**: 一部 cache file は更新済み / 一部は古いまま、という
  混在状態になりうるが、`.sync-meta.json` を更新しないことで「user は **古い**
  最終 sync 時刻を見る」状態に留まる。再実行で完全状態に復帰可能。

代替として「失敗時は `.sync-meta.json` を削除」も考えられるが、user が "elapsed
= n/a" と "elapsed = 数日前" のどちらに驚くかを天秤にかけて、**「数日前」の方が
正しい情報量** と判断（最後に成功した sync を保持）。

### config 解決の経路

`KajiConfig.discover()` を `cwd` 起点で呼ぶ。`provider.type` が `local` でも `gitlab`
でも `github` でも、`[provider.gitlab].repo` が解決できれば sync は動く。
`--repo <override>` は config 値より優先される（CLI > config の慣例）。

config も flag も無ければ exit 2 で fail-fast。error message:

```
error: 'kaji sync from-gitlab' requires a GitLab repo. Either:
  - set [provider.gitlab].repo = "group/project" in .kaji/config.toml, or
  - pass --repo group/project on the command line.
```

### 「未実装 flag を fail-fast」の実装方法

argparse で `add_argument("--include-closed", action="store_true",
help=argparse.SUPPRESS)` のように **登録だけ行い、`help` を抑制**することで、
user が `--include-closed` と打った場合に **`unrecognized arguments`** ではなく、
`cmd_sync_from_gitlab` の冒頭で `args.include_closed is True` を見て個別 error
を出せる。help を SUPPRESS する理由は「未実装の flag を doc に出すと user が使える
と勘違いする」ため。

## テスト戦略

### 変更タイプ

**実行時コード変更**（新規 CLI subcommand / 新規 sync module / LocalProvider 拡張）。

#### Small テスト（mock 完結 / 純粋ロジック）

- `_resolve_repo(config, override)` が config / override / どちらも欠の 3 ケースで
  正しく動く（欠ケースは `SyncError`）
- `_fetch_open_issues_paginated()` のページング終了条件:
  - 1 ページ目で 50 件 → 1 page で終了
  - 1 ページ目で 100 件 + 2 ページ目で空配列 → 2 page で終了（空が来れば終わる）
  - 1 ページ目で 100 件 + 2 ページ目で 30 件 → 2 page で終了（< per_page で終わる）
  - 200 page で MAX_PAGES に到達 → `SyncError`
- `_write_fresh_cache_file()` が `gl-<iid>.json` を atomic write し、
  `schema_version=1` / `forge="gitlab"` / `fetched_at` / `kaji_local.is_stale=false`
  / `kaji_local.staled_at=null` / `kaji_local.last_seen_at=now` を含む wrapper を
  生成する
- `_mark_cache_stale()` が既存 cache の `kaji_local.is_stale` を `false` → `true`
  に遷移させ、`staled_at` を now に設定する。`issue.state` は **書き換えない**
- `_mark_cache_stale()` が **既に is_stale=true** の entry を **再 write しない**
  （`staled_at` / `last_seen_at` を上書きしない invariant）
- `_mark_cache_stale()` が壊れた cache file (`json.JSONDecodeError`) に対して
  silently skip する（list 経路の警告と同方針）
- 新 sync で fresh entry の wrapper が完全に上書きされ、stale → fresh の復帰時に
  `is_stale=false / staled_at=null / last_seen_at=now` に戻る
- `_write_sync_meta()` が `last_sync_at` を UTC ISO-8601 で書く（zone offset / 小数秒
  なし）
- `read_sync_status()` が `.sync-meta.json` 不在時に `SyncStatus(forge=None, ...)` を
  返す（error にしない）
- `read_sync_status()` が cache dir 配下の `gl-*.json` を数えて `issue_count` に詰める
- `view_cached_gitlab_issue("42")` が cache JSON を `Issue` に正規化する
  （`state="opened"` → `"open"` / `description` → `body` / `labels: string[]` →
  `Label[]`）
- `view_cached_gitlab_issue` が cache 不在時に `IssueNotFoundError` を投げる
  （error message に `kaji sync from-gitlab` 案内が含まれる）
- `list_issues()` が cache 由来の `gl:42` entry を末尾に append し、`Issue.id` が
  `"gl:42"` 形式である
- `list_issues(state="open")` の表示 state 決定ルール（MF-2 対応）:
  - `is_stale=false` + `issue.state="opened"` → 表示 state = `"open"` で採用
  - `is_stale=true` + `issue.state="opened"` → **表示 state = `"closed"` で弾かれる**
    （open filter で出ない）
  - `is_stale=false` + `issue.state="closed"` → 表示 state = `"closed"` で弾かれる
  - `is_stale=true` + `issue.state="closed"` → 表示 state = `"closed"` で弾かれる
- `list_issues(state="closed")` で stale entry が出る（`is_stale=true` の entry
  は GitLab `issue.state` に依らず closed として採用される）
- `list_issues(state="all")` で stale + non-stale の全 entry が出る
- 旧 schema（`kaji_local` field を持たない） cache file が `is_stale=false` 扱い
  になる（後方互換）
- `list_issues(labels=["type:feature"])` が cache の labels を AND マッチで filter
- `list_issues()` が malformed cache file を skip（stderr に warning を出して継続）
- `cli_main` の `cmd_sync_from_gitlab` が `--include-closed` / `--state` /
  `--since` を受けたら exit 2 + 個別 error message
- `cmd_sync_status` が `--json` の有無で出力 format を切り替える

#### Medium テスト（subprocess 結合 / file I/O 結合 / CLI 経由）

- `glab` を `subprocess.run` の monkeypatch で mock し、`sync_from_gitlab()` の
  end-to-end:
  - 50 件の open issues を 1 page で fetch → `.kaji/cache/gl-1.json` 〜 `gl-50.json`
    と `.sync-meta.json` が atomic write される（tmp ファイルが残らない）
  - 250 件を 3 page (100/100/50) で fetch → 3 ページ分が正しく書かれる
  - 既存 `gl-99.json`（前回 sync 時 fresh）が 100 件の fetch 結果に **含まれない**
    場合に:
    - file は **delete されない**
    - `kaji_local.is_stale` が `false` → `true` に遷移する
    - `kaji_local.staled_at` が今回 sync 時刻に設定される
    - `issue.state` / `issue.title` / `issue.description` 等の本体は **変更されない**
  - 既に `is_stale=true` の entry が引き続き fetch 結果に **含まれない** 場合に、
    `staled_at` / `last_seen_at` が **更新されない** invariant
  - 既に `is_stale=true` の entry が今回の fetch 結果に **含まれた** 場合に、
    wrapper が `is_stale=false / staled_at=null / last_seen_at=now` に **復帰**する
  - `glab` が exit != 0 を返す（fetch phase 失敗）→ `SyncError` で停止し、
    既存 cache file は **mtime 含めて 1 byte も触られない**（all-or-nothing 検証）
    + `.sync-meta.json` は書かれない
  - 1 ページ目成功 + 2 ページ目で `glab` 失敗 → **1 ページ目分の cache も書かれない**
    （phase 1 が all-or-nothing なので write phase に入らない。`.sync-meta.json` も
    書かれない）。MF-1 で指摘された矛盾の解消確認
- `kaji sync from-gitlab` の CLI 経由（subprocess test）で:
  - exit code 0 + stdout に sync summary
  - `--quiet` で進捗が抑制されつつ最終 1 行は出る
  - `--include-closed` で exit 2 + stderr に error
- `kaji sync status` の CLI 経由で:
  - 未 sync 時の table 出力 / `--json` 出力
  - sync 後の table 出力 / `--json` 出力（elapsed 秒数 / 人間可読が両方含まれる）
- `kaji issue list` の CLI 経由で:
  - cache が空 → 既存挙動（local issue のみ）と完全一致
  - cache に 5 件 fresh GitLab issue 投入 → 既定 (`--state open`) で末尾に
    `gl:1` 〜 `gl:5` 行が出る
  - cache に 3 件 fresh + 2 件 stale (`is_stale=true`、ただし `issue.state` は
    `opened` のまま) を投入 → 既定で **fresh の 3 件のみ** が出る（MF-2 検証:
    stale entry は open filter から除外）
  - 同状態で `--state closed` → local closed + **stale 化された 2 件** が出る
  - 同状態で `--state all` → 全 5 件が出る
  - `--label type:feature` で両者から filter
- `kaji issue view gl:42` の CLI 経由で cache から issue が読める
- `kaji issue view gh:42` の CLI 経由で **既存挙動が回帰なし**（regression check）

#### Large テスト

- 本 Issue 範囲では **追加しない**。実 GitLab 通信 E2E（`make test-large-gitlab`）
  は子 Issue #6 (`local-p1-10`) の責務（EPIC OUT スコープ）。
- 不要理由（`testing-convention.md` 4 条件 vs 「子 Issue でカバー」）:
  - 1: 「独自ロジックの追加・変更をほぼ含まない」→ **満たさない**（pagination ループや
    cache 書き込みは新規ロジック）。だが Medium テストで subprocess mock 経由で同等の
    end-to-end 検証が可能で、子 Issue #6 が実通信を恒久化するため重複を避ける
  - 2: 想定不具合パターン（API 失敗 / partial sync / atomic write 失敗）は
    Medium テストで網羅可能
  - 3: 本 Issue で Large テストを追加しても、子 Issue #6 が同じ通信経路を恒久テスト化
    するため検出情報が増えない
  - 4: テスト未追加の理由は本設計書および子 Issue #6 の参照で説明可能

### baseline failure 既知事項

なし（本 Issue 起点）。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/cli-guides/local-mode.md` | **あり（本 Issue で更新必須）** | `:197` の「`kaji sync from-github` は残課題」記述を、`from-gitlab` のみ実装済 / `from-github` は引き続き残課題、と書き分ける必要がある。`gl:N` cache 経路の使用例を追加 |
| `docs/operations/local-mode-runbook.md` | **あり（本 Issue で更新必須）** | `:190` の `kaji sync` 言及箇所を「from-gitlab は実装済」に更新 |
| `draft/design/local-mode/design.md` § 残課題 | **あり（本 Issue で更新必須）** | 「`kaji sync` 系は残課題」記述から `from-gitlab` を実装済へ移行 |
| `docs/cli-guides/gitlab-mode.md`（子 Issue #5 で新設予定） | **なし（本 Issue では触らない）** | 子 Issue #5 (`local-p1-9`) の主成果物。本 Issue では先行追加しない |
| `docs/dev/workflow_guide.md` | **なし** | sync は workflow 概念に直接影響しない（CLI utility） |
| `docs/adr/` | **なし** | 既存 ADR の枠組み内（cache layout / glab CLI 戦略は `local-p1-5` で確立済） |
| `docs/ARCHITECTURE.md` | **なし** | provider 抽象には影響しない。新規モジュール `sync.py` の追加だけ |
| `docs/reference/python/` | **なし** | コーディング規約には影響しない |
| `CLAUDE.md` | **なし** | 規約は変わらない |
| `kaji_harness/providers/local.py` の docstring | **あり** | `view_cached_issue` の docstring に「`gl:` cache reader は `view_cached_gitlab_issue`」を追記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| **GitLab REST API: Issues (List project issues)** | https://docs.gitlab.com/ee/api/issues.html#list-project-issues | `GET /projects/:id/issues`、`?state=opened\|closed\|all`、`?labels=...`、`?per_page=` (default 20, max 100)、`?page=N` (1-indexed)。**本設計の pagination ループの正本** |
| **GitLab REST API: Pagination** | https://docs.gitlab.com/ee/api/rest/index.html#pagination | "Default per_page: 20, Maximum: 100" / "page: page index, default 1"。次ページ判定は応答 array 長 + `X-Next-Page` header だが、本実装は応答長のみで判定（`< per_page` で終了）。**MAX_PAGES=200 サニティリミットの根拠**（"keyset pagination" は不要、"offset pagination" の単純実装で十分） |
| **GitLab REST API: namespaced path encoding** | https://docs.gitlab.com/ee/api/rest/index.html#namespaced-paths | "URL-encoded path: `diaspora/diaspora` → `diaspora%2Fdiaspora`"。`:id` は URL-encoded path / numeric ID 両方を受ける。**`urllib.parse.quote(repo, safe="")` の正本** |
| **glab CLI: api command** | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/api.md | `glab api <endpoint>` は GitLab REST v4 を起動し JSON を stdout に出す。`--hostname gitlab.com` で対象 GitLab を固定可能。**本設計の `_glab_api_get` の正本**（`local-p1-5` 設計書と同根拠） |
| **既存 `LocalProvider`** | `kaji_harness/providers/local.py:308-756` | `_atomic_write` / `_cache_dir` / `view_cached_issue` の structure を本設計が踏襲。`view_cached_gitlab_issue` / `_gitlab_cache_path` を sibling 構造として追加 |
| **既存 `_cached_issue_from_payload`** | `kaji_harness/providers/local.py:763` | GitHub cache JSON → `Issue` 変換の reference 実装。GitLab cache JSON 用の正規化（`state=opened` → `open` / `description` → `body` 等）はこれと対称形 |
| **既存 `normalize_id` の `gl:` 経路** | `kaji_harness/providers/__init__.py:155-207` | `gl:42` → `ResolvedId(kind="remote_cache", value="42", raw="gl:42")` の正本。`raw` で gh/gl 識別する本設計の根拠 |
| **既存 `kaji issue view` dispatcher** | `kaji_harness/cli_main.py:1090-1122` | `rid.kind == "remote_cache"` 経路に gl: 分岐を追加する箇所の正本 |
| **既存 `GitLabProvider._glab_api_get` / `list_issues`** | `kaji_harness/providers/gitlab.py:105-119, 331-366` | `glab api projects/<encoded>/issues?...` 起動の reference 実装。本設計の sync 用 standalone helper `_glab_api_get(repo, endpoint)` の機能等価性の根拠 |
| **既存 `_validate_workflow_provider_match`** | `kaji_harness/cli_main.py:358-382` | `provider.type` 整合 fail-fast の慣例。本設計では sync は provider.type に依存しないため、この経路は通らないが「invalid args は exit 2」の共通慣例として参照 |
| **テスト規約** | `docs/dev/testing-convention.md` | Small / Medium / Large 区分の正本。本 Issue では Medium まで実装し、Large は子 Issue #6 に委ねる根拠 |
| **EPIC `local-p1-4` 確定事項** | `.kaji/issues/local-p1-4-epic-gitlab-validation/issue.md` § 確定事項 | 本設計の前提として参照。特に #1（CLI subprocess 必須化）/ #3（`gitlab.com` 固定 / `gl:N` 規約）/ #5（本格実装） |
| **`local-p1-5` 設計書** | `draft/design/issue-local-p1-5-gitlab-provider-impl.md` | GitLabProvider 既存実装の構造的整合点。本 Issue は `local-p1-5` が確立した glab CLI 戦略 / cache layout の方針 / config schema を再利用する |

</details>

## 概要

`.kaji/cache/` を GitLab Issue から populate する `kaji sync from-gitlab` と、cache 状態を表示する `kaji sync status` を実装する。`kaji issue list` の local + cache 統合表示にも対応する。

## 目的

- `provider.type='local'` 配下から GitLab Issue を `gl:N` で参照できるよう cache を自動 populate する
- `local-p1-1` bucket の forge 連携項目（`kaji sync from-github` の GitLab 版対称実装）を先取りする

## ユーザーストーリー

- kaji ユーザーとして、`provider.type='local'` 配下で `kaji issue view gl:42` を打てば cache 経由で GitLab issue 42 が読める状態にしたい
- kaji ユーザーとして、cache の同期状態を `kaji sync status` で確認し、stale な entry が無いかわかる状態にしたい
- kaji ユーザーとして、`kaji issue list` が local issue + cached gitlab issue を統合表示してほしい

## スコープ

### IN

#### `kaji sync from-gitlab` CLI 実装

- **同期対象は GitLab project の open Issue 全件**（初期実装スコープ）
- `glab issue list --state opened --output json` 等で取得（`--repo` 明示指定）
- 取得した各 Issue を `.kaji/cache/gl-<iid>.json` に **atomic write**（`tmp` → `os.rename`）
- ローカル cache に存在するが GitLab 側で取得結果に含まれない Issue（= 既に closed 化された）は cache に残す（削除しない、参照履歴として保持）
- 最終 sync 時刻を `.kaji/cache/.sync-meta.json`（または同等の単一ファイル）に記録

#### `kaji sync status` CLI 実装

- cache 件数（`.kaji/cache/gl-*.json` の数）
- 最終 sync 時刻（UTC ISO-8601）
- 経過時間（秒 / 人間可読）
- 出力は table / JSON 切替（`--json` flag）

#### closed Issue / 詳細フィルタ（OUT — 将来拡張）

- `--include-closed` / `--state` / `--since` 等の追加 flag は **本 Issue では実装しない**。GitLab 採用が確定し運用上の必要が出た段階で別 Issue として起票
- 初期実装は open 全件取得のシンプルな mental model に留める

#### cache 統合表示

- `.kaji/cache/` の自動初期化（`kaji local init` または初回 sync 時）
- `LocalProvider.list_issues()` 拡張: cache 配下の `gl-*.json` も統合表示
- 表示形式: `gl:42  open  ...` のように `gl:` prefix で local issue と区別

### OUT

- `kaji sync local-to-gitlab-plan` → bucket (`local-p1-1`) 残務として残す
- Issue 一括転記支援 → bucket 残務として残す
- `kaji sync from-github`（GitHub 復帰判断後実装）→ `local-p1-12` として deferred
- `--include-closed` / `--state` / `--since` 等の追加 flag → 採用確定後の運用要件に応じて別 Issue で対応

## 完了条件

- [x] `kaji sync from-gitlab` 実行で **GitLab project の open Issue 全件** が `.kaji/cache/gl-<iid>.json` に atomic write される
- [x] `kaji sync from-gitlab` 完了時、最終 sync 時刻が `.kaji/cache/.sync-meta.json`（または同等）に UTC ISO-8601 で記録される
- [x] cache に存在するが GitLab 側で open でなくなった Issue は cache に残る（削除されない）
- [x] `kaji sync status` が以下を表示する: cache 件数 / 最終 sync 時刻 (UTC) / 経過時間 / `--json` 切替
- [x] `kaji issue list` が local + cache (`gl:*`) を統合表示し、`gl:` prefix で区別される
- [x] `--include-closed` / `--state` / `--since` 等の追加 flag は **未実装の状態で fail-fast** する（silent ignore しない）
- [x] Medium テストで以下が緑: open 全件取得 → cache 書き込み round-trip / closed 化された entry の保持 / `sync status` の出力検証 / `kaji issue list` 統合表示
- [x] `make check` 緑

## 依存

- `local-p1-5`（`GitLabProvider` 実装）— 完了必須

## 参照

- 既存 cached read 経路: `kaji_harness/providers/local.py:493`、`kaji_harness/providers/local.py:753` (`view_cached_issue`)
- 既存 `LocalProvider.list_issues()`: `kaji_harness/providers/local.py`
- design.md § 残課題: `draft/design/local-mode/design.md`
- bucket Issue: `local-p1-1`
