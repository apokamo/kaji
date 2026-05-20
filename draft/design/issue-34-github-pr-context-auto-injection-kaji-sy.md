# [設計] GitHub 復帰対応: PR context auto-injection + `kaji sync from-github` + ドキュメント整備

Issue: gl:34

## 概要

GitHub アカウント復旧 (2026-05-21) を受けて、`GitHubProvider` を GitLab 同等まで対称化する。`GitHubProvider.resolve_pr_context()` の本実装、`kaji sync from-github` の追加、`docs/cli-guides/github-mode.md` の新設、および `pr-fix` / `pr-verify` / `i-pr` skill の暫定記述切替を 1 Issue で揃え、skill 側から provider 分岐を排除する。

## 背景・目的

### 経緯

- 2026-05-08 方針転換で GitHub 復旧前提を一旦放棄し、GitLab 側を full 実装した（`local-p1-5` / `local-p1-6` / `local-p1-8`）。
- 結果として `GitHubProvider` に意図的なギャップが残った:
  - `GitHubProvider.resolve_pr_context()` が no-op で `None` 固定（`kaji_harness/providers/github.py:311-322`）
  - `kaji sync from-github` 未実装、`from-gitlab` のみ存在（`kaji_harness/cli_main.py:88-123` / `kaji_harness/sync.py:271`）
  - GitHub mode 用 CLI ガイド (`docs/cli-guides/github-mode.md`) 不在
- 2026-05-21 に GitHub アカウントが復旧し、`local-p1-12` (`[deferred] kaji sync from-github`) の trigger 条件が成立した。`local-p1-1` § PR context 注入の Phase 4 申し送りと併せて、本 Issue で対称化を完了させる。

### ユースケース

#### UC-1: GitHub mode での `/pr-fix` / `/pr-verify` 自動 PR 特定

- **Role**: GitHub リポジトリで kaji を運用する開発者
- **Goal**: feature branch (worktree) 上で `/pr-fix` / `/pr-verify` を呼び出した際、対応する PR を手動指定せず自動解決させたい
- **Action**: `runner.py` が `GitHubProvider.resolve_pr_context()` を呼び、現在ブランチに紐づく open PR を 1 件特定して `pr_id` / `pr_ref` を prompt に注入する
- **Value**: GitLab mode で既に成立している「ブランチから MR を逆引きする」体験が GitHub でも揃い、skill 側に `provider.type` 分岐が残らない

#### UC-2: GitHub Issue を local cache に取り込む

- **Role**: GitHub mode を併用、もしくは `provider.type='local'` 配下から `gh:N` で GitHub Issue を参照する開発者
- **Goal**: GitHub Issue 本文を AI agent コンテキストにオフライン参照可能な cache に取り込みたい
- **Action**: `kaji sync from-github [--repo owner/repo]` を実行し、`gh api --paginate repos/<owner>/<repo>/issues?state=open` 経由で Issue データを取得 → atomic write で `.kaji/cache/gh-<n>.json` を populate する
- **Value**: `kaji issue view gh:<n>`（cache reader）が GitLab 側の `gl:<iid>` と対称になり、provider 切替時の挙動差がなくなる

#### UC-3: GitHub mode 利用者向けセットアップ手順の参照

- **Role**: GitHub mode を新規に立ち上げる開発者
- **Goal**: `gitlab-mode.md` と同じ粒度で GitHub mode の前提・設定・命名規約・トラブルシュートを参照したい
- **Action**: `docs/cli-guides/github-mode.md` を読み、`[provider.github]` config 最小例、`gh auth login` 前提、`.github/labels.yml` 連動などを把握する
- **Value**: GitHub mode セットアップで、GitLab mode との差分を都度コードや過去 commit から類推せずに済む

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `resolve_pr_context` 実装のみ先行 / sync は別 Issue | `local-p1-12` trigger が同タイミングで成立しており、対称化を 1 commit chain でまとめる方が cache layout 議論を 1 度で済ませられる |
| skill 側で provider 分岐 (`if provider == 'github'`) を残し続ける | 既存設計原則「skill は provider 非依存」に反する。Phase 4 申し送りの目的が分岐排除なので原則上採用不可 |
| `gh-<n>.json` 採用ではなく現行 `.kaji/cache/issues/<n>.json` レイアウトを sync 側で踏襲 | gl/gh で cache layout が非対称になり、`sync.py` の `_list_existing_cached_iids` / `read_sync_status` が forge 毎に分岐する。設計判断点として § 制約 で扱う |

## インターフェース

### IF-1: `GitHubProvider.resolve_pr_context`

```python
class GitHubProvider:
    def resolve_pr_context(self, branch_name: str) -> PRContext | None: ...
```

| 項目 | 内容 |
|------|------|
| 入力 | `branch_name: str`（worktree の現在ブランチ。空文字や非 ASCII は呼び出し側責務、defensive validation はしない） |
| 出力 (成功 1 件) | `PRContext(pr_id="<number>", pr_ref="gh:<number>")` |
| 出力 (0 件) | `None`（branch 未 push / PR 未作成。skill 側 fallback 経路がそのまま動く） |
| 出力 (複数件) | `GitHubProviderError("multiple open pull requests found for head branch <branch>: [...]")` を raise |
| 出力 (`gh` 不在 / 非 0 exit / JSON parse 失敗) | 既存 `_run_gh` / `_run_gh_json` 経由で `GitHubProviderError` を raise |
| 内部実装 | `gh pr list --head <branch> --state open --json number,headRefName` を `_run_gh_json` で起動 |

#### `pr_ref` の値: `gh:<n>` を採用する

- **採用**: `pr_ref = f"gh:{n}"` を採用する。
- **理由**: `PRContext` は `provider_type` を持たず、prompt に展開された段階では provider 区別が消える。GitLab 側 `gl:<iid>` と対称な prefix にしておくと、skill / agent ログ・コメント内で出自が判別可能。Issue 本文 § IN.1 の表記とも整合する。
- **影響**: 現行 `.claude/skills/pr-fix/SKILL.md` および `pr-verify/SKILL.md` の手動 fallback では `pr_ref="#${pr_id}"` を組み立てている（GitHub 慣習）。スコープ IN.4 で「テンプレート変数参照 (`{{pr_id}}` / `{{pr_ref}}`) への切替」と同時に、fallback パスでの `pr_ref` 文字列構築も `gh:<n>` 統一に揃える（PR URL 表記としての `#<n>` は GitHub 標準だが、skill / prompt 内表現としては `gh:<n>` に統一する）。
- **互換性ノート**: Issue / PR 本文に表示される `pr_ref` は AI agent 内部参照用であり、GitHub 上で `#<n>` の clickable リンクとして必要な箇所は別途 `[#<n>](<pr_url>)` 等で構築する（既存 `i-pr` SKILL.md の `kaji pr create` 出力 URL から導出する経路はそのまま）。

#### runner 側の取り扱い

- `runner._resolve_pr_context_safe`（`kaji_harness/runner.py:170-189`）の `except` 節に `GitHubProviderError` を追加する。GitLab と同様に「known provider error は WARN + None、それ以外は raise」の原則を維持する。
- 「該当 0 件は None / 複数件は error」という分岐は provider 内に閉じる。runner 側で別経路を作らない。

### IF-2: `kaji sync from-github` CLI

```bash
kaji sync from-github [--repo OWNER/REPO] [--quiet]
```

| 引数 | 必須 | 解決順 |
|------|------|--------|
| `--repo OWNER/REPO` | optional | (1) `--repo` flag → (2) `[provider.github].repo` → (3) 未設定なら `SyncError`（exit 2 で fail-fast） |
| `--quiet` | optional | 進捗ログを抑制。最終サマリ 1 行は出す（`from-gitlab` 同形） |
| 将来予約 flag | `--include-closed` / `--state` / `--since` | `from-gitlab` と同じく **未対応として exit 2** で reject（silent ignore 禁止） |

| 副作用 | 内容 |
|--------|------|
| cache file 書き込み | `.kaji/cache/gh-<n>.json`（atomic write、既存 entry は overwrite） |
| stale marking | fetch 結果に含まれない既存 `gh-*.json` を `kaji_local.is_stale=true` でマーク（`from-gitlab` § phase 2 と同形） |
| meta 書き込み | `.kaji/cache/.sync-meta.json` の `forge="github"` / `repo=OWNER/REPO` / `last_sync_at` / `issue_count` / `pages_fetched` を上書き |
| stdout | `"Sync completed at <iso> (<n> issues, <p> pages, <s>s).\n"`（`--quiet` でも出す） |
| stderr (失敗時) | `gh` 不在 / 認証エラー / 非配列 JSON / repo 未設定 → `SyncError` → CLI 層で exit 2 |

### IF-3: `.kaji/cache/gh-<n>.json` schema

`gl-<iid>.json` と完全対称（`forge` フィールドの値のみ差異）:

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
  "issue": { /* gh api repos/.../issues/<n> raw payload */ }
}
```

### IF-4: `view_cached_issue` の cache layout 移行

> **設計判断点**: 現行 `LocalProvider.view_cached_issue` (`kaji_harness/providers/local.py:763-800`) は `.kaji/cache/issues/<n>.json` を読む。Issue 本文「現行 `view_cached_issue` が読む layout を維持」は前提誤りで、現行 layout は `gl-<iid>.json` と非対称である。

採用案: **`.kaji/cache/gh-<n>.json` への正準化**（GitLab と対称）。

- `view_cached_issue(number)` を `self._cache_dir_root / f"gh-{number}.json"` に変更する。
- 旧 layout (`.kaji/cache/issues/<n>.json`) のサポートは削除する。検証期間中 (`provider.type='local'`) の手動投入 fixture は廃棄 / 移行が必要。`tests/test_dispatcher.py:329-365` 等の `gh:N` cached read を検証するテストは新 layout (`gh-<n>.json`) に書き換える。
- `_list_existing_cached_iids` / `_list_cached_gitlab_issues` / `read_sync_status` 等の forge 横断 helper は **gl / gh の prefix 分岐** で対称化する（後述 § 方針 § 3）。

> 旧 layout を維持する保守的代替案も検討したが、`sync.py` の forge 分岐コードと、後続「`gh:` / `gl:` 統合 list view」設計の負債が膨らむため不採用。本 Issue 内で `tests/` を一括移行する。

### 使用例

```python
# UC-1: runner からの呼び出し（既存 _resolve_pr_context_safe 経路、変更なし）
provider: IssueProvider = get_provider(config)
ctx = provider.resolve_pr_context("feat/34")
# → PRContext(pr_id="56", pr_ref="gh:56") もしくは None
```

```bash
# UC-2: cache populate
$ kaji sync from-github --repo apokamo/kaji
Fetching open issues from github.com:apokamo/kaji ...
  page 1: 47 issues
Wrote 47 issues to .kaji/cache/ (47 newly added, 0 updated, 0 unchanged signature).
Sync completed at 2026-05-21T12:34:56Z (47 issues, 1 pages, 1.4s).

$ kaji issue view gh:42
# → .kaji/cache/gh-42.json から read-only に Issue を組み立てて表示
```

```bash
# UC-3: github-mode.md の最小設定例
[provider]
type = "github"

[provider.github]
repo = "apokamo/kaji"
default_branch = "main"
git_remote = "origin"
```

### エラー仕様まとめ

| 経路 | 失敗パターン | 動作 |
|------|------------|------|
| `resolve_pr_context` | branch 未 push | `gh pr list` が空配列 → `None` |
| `resolve_pr_context` | open PR が 2 件以上 | `GitHubProviderError` raise（runner WARN + None） |
| `resolve_pr_context` | `gh` 不在 / 認証エラー | `GitHubProviderError` raise（既存 `_run_gh` の振る舞いを継承） |
| `sync from-github` | `[provider.github].repo` 未設定かつ `--repo` 無し | `SyncError` → exit 2 |
| `sync from-github` | `gh api` 非 0 / 認証エラー | `SyncError` → exit 2、cache は **一切触らない**（all-or-nothing） |
| `sync from-github` | 取得途中で page 失敗 | `SyncError` → exit 2、cache は触らない |
| `view_cached_issue` | `gh-<n>.json` 不在 | `IssueNotFoundError("Run 'kaji sync from-github' to populate the cache.")` |

## 制約・前提条件

- **対称構造の維持**: `sync_from_github` は `sync_from_gitlab` の構造（3 phase all-or-nothing / `_list_existing_cached_iids` / `_mark_cache_stale` / `_write_sync_meta`）を踏襲する。schema_version は共通の `1` を流用する。
- **pagination 方式**: `gh api --paginate` を採用する（`from-gitlab` の `?page=N` 手動ループとは異なる）。`gh` 1.14+ は array response を自動連結する。`--paginate` 内部の page 数は最終 array length と `--paginate` の出力から逆算するため、進捗表示の粒度は `from-gitlab` の page 単位に対して 1 page 粒度（または `gh` の rate-limit page 単位）まで落ちる可能性がある。
- **page 数の安全弁**: `from-gitlab` の `_MAX_PAGES=200` (= 20,000 issues) と同等の上限を `from-github` でも維持する。`--paginate` が暴走しないよう、取得後の array length で `_MAX_PAGES * _PER_PAGE` を超えたら `SyncError` を raise する（pre-flight チェックは不可能なので post-fetch チェック）。
- **`gh api` の `state=open` filter**: `gh api repos/<owner>/<repo>/issues?state=open` を使う。GitHub REST API は `pull_requests` も `issues` endpoint から返るため、`pull_request` キーを持つ entry は除外する（GitHub 公式仕様）。
- **forge 切替時の cache 残骸**: `.sync-meta.json` の `forge` が前回 `gitlab` のときに `from-github` を走らせると、`gl-*.json` を「stale 化対象」と誤判定する。これを防ぐため、stale 判定は **同 forge prefix (`gh-*.json` or `gl-*.json`)** にスコープを絞る（後述 § 方針 § 3）。`.sync-meta.json` 自体は forge 単位で上書きする。
- **`PRContext` model 変更なし**: 既存 `kaji_harness/providers/models.py` の `PRContext(pr_id, pr_ref)` を流用する。新フィールドは追加しない。
- **後方互換**: `provider.type='github'` の既存 user は `resolve_pr_context` の no-op 挙動に依存していない（呼び出し経路は runner と一部 skill だけで、いずれも `None` を許容する設計）。skill 側暫定記述切替は本 Issue 内で同時実施するため、`pr_ref` 表記変更による外部影響はない。

## 変更スコープ

| 影響モジュール | 変更内容 |
|---------------|----------|
| `kaji_harness/providers/github.py` | `resolve_pr_context` no-op → `gh pr list --head` 経路へ書き換え |
| `kaji_harness/providers/local.py` | `view_cached_issue` の cache path を `gh-<n>.json` に変更。`_cache_dir` プロパティ（`issues/` サブディレクトリ）を撤去、`_cache_dir_root` へ統合 |
| `kaji_harness/sync.py` | `sync_from_github` 関数追加 / `_list_existing_cached_iids` を forge prefix で分岐 / `_write_sync_meta` に `forge` 引数追加 / `read_sync_status` で gh-*.json も count |
| `kaji_harness/cli_main.py` | `_register_sync` に `from-github` subcommand 追加 / `cmd_sync_from_github` dispatcher 追加 |
| `kaji_harness/runner.py` | `_resolve_pr_context_safe` の `except` に `GitHubProviderError` を追加 |
| `.claude/skills/pr-fix/SKILL.md` | provider 分岐記述削除、`{{pr_id}}` / `{{pr_ref}}` テンプレート参照に切替 |
| `.claude/skills/pr-verify/SKILL.md` | 同上 |
| `.claude/skills/i-pr/SKILL.md` | provider 分岐記述削除、`pr_ref` 表記を `gh:<n>` に統一 |
| `docs/cli-guides/github-mode.md` | 新規ファイル（`gitlab-mode.md` を雛形に章立て踏襲） |
| `docs/README.md` | cli-guides 索引に github-mode を追加 |
| `tests/test_providers_github.py` | `resolve_pr_context` の Small/Medium テスト追加（`subprocess.run` patch 系統） |
| `tests/test_sync.py` (or 同等) | `sync_from_github` の Small/Medium テスト追加 |
| `tests/test_dispatcher.py` | `gh:N` cached read を新 layout (`gh-<n>.json`) に書き換え |
| `.kaji/cache/.gitignore` 等の運用ファイル | 既存 fixture と矛盾しない範囲で必要なら更新 |

## 方針（Minimal How）

### 1. `GitHubProvider.resolve_pr_context`

`GitLabProvider.resolve_mr_iid_from_branch` + `resolve_pr_context` の二段構成と異なり、GitHub は `gh pr list --head <branch>` 1 発で number を取れるので 1 メソッドに閉じる。

```python
def resolve_pr_context(self, branch_name: str) -> PRContext | None:
    payload = self._run_gh_json(
        "pr", "list",
        "--head", branch_name,
        "--state", "open",
        "--json", "number,headRefName",
    )
    if not isinstance(payload, list):
        raise GitHubProviderError("gh pr list returned non-array JSON")
    numbers = [str(entry["number"]) for entry in payload if isinstance(entry, dict)]
    if not numbers:
        return None
    if len(numbers) > 1:
        raise GitHubProviderError(
            f"multiple open pull requests found for head branch {branch_name!r}: {numbers}"
        )
    return PRContext(pr_id=numbers[0], pr_ref=f"gh:{numbers[0]}")
```

- `--repo` の自動注入は `_run_gh` 側で `[provider.github].repo` を解決する既存経路を流用する（既存 issue CRUD 経路と同じ）。
- `gh pr list` は default で open のみ返すが、明示的に `--state open` を付け、将来の `gh` default 変更に耐える形にする。

### 2. `sync_from_github`

`sync_from_gitlab` を構造的に踏襲し、forge 差分のみを分離する。

```python
def sync_from_github(*, config: KajiConfig, repo_override: str | None, quiet: bool) -> SyncResult:
    repo = _resolve_repo_github(config, repo_override)  # provider.github.repo を見る
    cache_dir = _cache_dir_root(config.repo_root)

    issues = _fetch_open_issues_github(repo)        # gh api --paginate
    pages_fetched = 1  # gh --paginate は内部 page 数を露出しないため "1 (paginated)" 扱い

    # phase 2: stale 判定（forge prefix を gh-*.json にスコープ）
    fetched_numbers = {str(e["number"]) for e in issues}
    existing = _list_existing_cached_numbers(cache_dir, prefix="gh-")
    stale = existing - fetched_numbers

    # phase 3: write
    for entry in issues:
        _write_fresh_github_cache_file(entry, cache_dir, now_iso)
    for number in sorted(stale):
        _mark_cache_stale(cache_dir / f"gh-{number}.json", now_iso)
    _write_sync_meta(forge="github", repo=repo, ...)
    return SyncResult(...)
```

- `_fetch_open_issues_github(repo)` の擬似コード:
  ```python
  cmd = ["gh", "api", "--paginate",
         f"repos/{repo}/issues?state=open&per_page={_PER_PAGE}"]
  proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
  if proc.returncode != 0:
      raise SyncError(f"gh api failed (exit {proc.returncode}): ...")
  payload = json.loads(proc.stdout)  # gh --paginate concatenates arrays
  # pull_request key を持つ entry を除外
  issues = [e for e in payload if isinstance(e, dict) and "pull_request" not in e]
  if len(issues) > _MAX_PAGES * _PER_PAGE:
      raise SyncError(f"sync aborted after {len(issues)} issues ...")
  return issues
  ```
- 共通 helper（`_atomic_write` / `_mark_cache_stale` / `_now_iso` / `_write_sync_meta`）は gitlab 側と同居させる（重複実装を避ける）。`_write_sync_meta` は `forge` を引数化する。
- `_resolve_repo_github(config, override)` は `from-gitlab` の `_resolve_repo` と同形だが `provider.github.repo` を参照する独立関数とする（型を別 dataclass で持つため共通化は不要）。

### 3. `_list_existing_cached_iids` の forge 対応

既存実装は `gl-*.json` 固定。これを以下の汎用 helper に置き換える:

```python
def _list_existing_cached_numbers(cache_dir: Path, *, prefix: str) -> set[str]:
    """cache_dir/<prefix><n>.json から番号集合を返す。prefix='gl-' or 'gh-'."""
    ...
```

- `read_sync_status` 側は **forge を `.sync-meta.json` から復元** し、その forge の prefix で `_list_existing_cached_numbers` を呼ぶ。
- `forge=None`（未 sync 状態）のときは gl-*.json + gh-*.json の和集合の件数を返す（status 表示用 count として両 forge を合算）。

### 4. CLI 登録

`_register_sync` に `from-github` を追加し、`cmd_sync_from_github` を新設する。`cmd_sync_from_gitlab` とほぼ同形（将来予約 flag の reject 含む）。`main()` の dispatcher に `from-github` 分岐を追加。

### 5. skill SKILL.md の暫定記述切替

対象: `.claude/skills/pr-fix/SKILL.md` L47 / L121-135、`pr-verify/SKILL.md` L51 / L135-148、`i-pr/SKILL.md` L55 / L215-216 / L241。

切替方針:

- 「`provider.type='github'` 配下では `kaji pr list --search` から取得する」の暫定記述を削除し、「`{{pr_id}}` / `{{pr_ref}}` がハーネスから注入される。manual fallback は `kaji pr list --head [branch_name]` を使う」に統一。
- fallback 経路の `pr_ref` 文字列構築を `pr_ref="#${pr_id}"` から `pr_ref="gh:${pr_id}"` に変更（provider.type='gitlab' の `gl:${pr_id}` と対称）。
- `i-pr` SKILL.md の `pr_url` から `pr_id` を取り出す経路はそのまま（`pr_url##*/` で末尾数値抽出）。`pr_ref="#${pr_id}"` を `pr_ref="gh:${pr_id}"` に書き換える。

### 6. `docs/cli-guides/github-mode.md`

`gitlab-mode.md` の章立てを 1:1 で踏襲:

1. 前提（`gh` CLI、`gh auth login`、SSH 鍵）
2. `kaji issue` / `kaji pr` の挙動（既存 gh passthrough 経路）
3. `kaji sync from-github` の使い方（cache populate 経路）
4. `make test-large` 等の実 API 疎通テスト前提（既存ターゲットの再掲）
5. トラブルシューティング（`gh: not found` / 認証 / labels.yml 連動）
6. 参照

GitLab 側にあって GitHub 側にないもの:

- merge method 制約セクション → GitHub は `gh pr merge --merge` で明示指定できるため Project 設定依存が薄い。`--no-ff` 規約は kaji 側 guard で吸収済を明記する程度に短縮。
- auto-close keyword 回避 → GitHub の close pattern は `Fixes #N` 等で同様に発火する。`docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避が GitHub にもそのまま適用される旨を明記する（既存 docs は GitLab 文脈で書かれているため見出し改名は別 Issue で扱う）。

`docs/README.md` の cli-guides 索引（L34）に `github-mode.md` への明示リンクを追加する。

## テスト戦略

> **CRITICAL**: 本 Issue は実行時コード変更を伴う feature 追加であり、Small / Medium / Large の各サイズで検証観点を定義する。docs-only 部分（`github-mode.md` / `docs/README.md`）は変更固有検証（`make verify-docs`）で別途確認する。

### 変更タイプ
- 実行時コード変更（`GitHubProvider.resolve_pr_context` / `sync_from_github` / `view_cached_issue` layout 変更）
- docs 追加（`docs/cli-guides/github-mode.md`）
- skill 暫定記述切替（markdown のみだが、後段 workflow の挙動に影響するため変更固有検証 + 既存 skill テストで担保）

### 実行時コード変更の場合

#### Small テスト（`@pytest.mark.small`）

- `GitHubProvider.resolve_pr_context`:
  - 0 件 → `None`
  - 1 件 → `PRContext(pr_id, pr_ref="gh:<n>")` 構築（`pr_ref` 文字列の正確性を含む）
  - 複数件 → `GitHubProviderError` raise + メッセージに branch 名 / number list を含む
  - `gh` の非配列 JSON / 非 dict 要素 → `GitHubProviderError`
  - `subprocess.run` の patch スコープは `_run_gh_json` の戻り値 mock に閉じる（dispatcher 経路の名前空間 patch は禁止、`testing-convention.md` § subprocess.run patch スコープ準拠）
- `sync.py` の helper:
  - `_list_existing_cached_numbers(prefix='gh-')` が `gl-*.json` を含めない
  - `_write_sync_meta(forge='github', ...)` の payload 形状（`forge` field の正確性）
  - GitHub `issues` endpoint の `pull_request` キー entry を除外する fanout（fixture 5 件中 2 件が `pull_request` 持ち → 3 件のみ採用）
- `view_cached_issue` の新 layout:
  - `gh-<n>.json` 存在時に Issue を組み立てる
  - 不在時に `IssueNotFoundError`（メッセージに `kaji sync from-github` 案内）
  - 旧 layout (`issues/<n>.json`) はサポートしない（移行漏れ検出用 negative test）

#### Medium テスト（`@pytest.mark.medium`）

- `sync_from_github` の 3 phase all-or-nothing:
  - fixture `gh api` (= `subprocess.run` mock) で 3 件 issue + 1 件 PR を返す → `pull_request` 除外で 3 件 cache 化、`.sync-meta.json` の `forge='github'`、`gh-*.json` が atomic に揃う
  - 既存 `gh-99.json` が fetch 結果に含まれない → `kaji_local.is_stale=true` でマーク
  - fetch 失敗（`gh api` exit 非 0）→ cache 一切触らず `SyncError`
  - `_MAX_PAGES * _PER_PAGE` 超過 → `SyncError`、cache 触らず
- CLI 層 (`cmd_sync_from_github`):
  - `--include-closed` / `--state` / `--since` → exit 2 (`EXIT_INVALID_INPUT`)
  - `--repo` flag が `[provider.github].repo` を上書きする
  - config 不在 → exit 2
- `runner._resolve_pr_context_safe`:
  - `GitHubProviderError` を投げる mock provider → WARN を stderr に出し `None` を返す
  - 既存の `GitLabProviderError` 経路と並存する

#### Large テスト（`@pytest.mark.large_forge`）

- 実 GitHub API に対する E2E は **デフォルトで実行しない**。`make test-large-gitlab` と同様に opt-in target を将来追加する余地はあるが、本 Issue では追加しない。
- 理由: `make check` のデフォルト挙動を変えない（GitLab 側 E2E は `make test-large-gitlab` で opt-in 化されている）。GitHub 側も Phase 2 で独立 target を切る形が望ましいが、`local-p1-10` 同等の trakcing を本 Issue では受け持たない。
- 代替: `kaji sync from-github --repo apokamo/kaji` を実際の repo で手動実行し、cache populate / `kaji issue view gh:<n>` までを動作確認する手順を `github-mode.md` § 動作確認に記載する。

#### 省略しないサイズ判定

- Small / Medium は両方とも実装必須（4 条件のうち「既存ゲートで捕捉可能」が成立しないため）。
- Large（実 GitHub API 疎通）は省略する。省略理由:
  1. 同等の E2E カバレッジを `make test-large-gitlab` 経路で確立済（GitLab 側 provider）、本 Issue は対称実装で構造差が小さい
  2. CI への組み込みは独立 Issue（`local-p1-10` GitHub 版）で扱う方が境界が明確
  3. Medium テストで `gh api` の stdout 形状（page 連結 / `pull_request` 除外）を fixture で再現できるため、本 Issue の振る舞いは Medium で十分検証可能
  4. ローカル手動疎通手順を `github-mode.md` に明記し、E2E カバレッジ不在を可視化する

### docs-only / metadata-only / packaging-only 部分

#### 変更固有検証
- `docs/cli-guides/github-mode.md` 追加と `docs/README.md` 索引更新 → `make verify-docs` でリンク整合チェック
- skill SKILL.md 切替 → 既存 `i-pr` / `pr-fix` / `pr-verify` の skill smoke test があれば green 維持。なければ手動で `/i-pr` (dry-run 相当) を `provider.type='github'` 配下で実行確認

#### 恒久テストを追加しない理由（docs / skill 部分）
1. docs と skill SKILL.md は実行時ロジックを持たない（変更固有検証で十分）
2. skill 経由の挙動回帰は実装側 (`resolve_pr_context` / `sync_from_github`) の Small / Medium で間接的に捕捉される
3. `make verify-docs` がリンク・参照の継続検証を担う
4. テスト未追加の理由は本セクションに明記

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし（`gh` CLI / `--paginate` は既存採用済技術） |
| docs/ARCHITECTURE.md | なし | provider 抽象境界は不変、`resolve_pr_context` の本実装化は契約レベルの変更ではない |
| docs/dev/development_workflow.md | なし | workflow flow / phase 構造に変化なし |
| docs/dev/shared_skill_rules.md | あり（軽微） | GitLab auto-close keyword 回避規約が GitHub にも適用される旨の脚注追加余地（最小） |
| docs/reference/ | なし | API 仕様規約変更なし |
| docs/cli-guides/github-mode.md | **新規作成** | UC-3 の中心成果物 |
| docs/cli-guides/gitlab-mode.md | あり（最小） | `kaji sync from-github` の存在を相互参照する 1 行追加 |
| docs/cli-guides/local-mode.md | あり（最小） | `gh:N` cache populate 経路として `kaji sync from-github` を案内する 1 セクション追加 |
| docs/README.md | あり | cli-guides 索引に github-mode を追加 |
| CLAUDE.md | なし | 規約変更なし |
| `.claude/skills/pr-fix/SKILL.md` | あり | 暫定記述切替（IN.4） |
| `.claude/skills/pr-verify/SKILL.md` | あり | 暫定記述切替（IN.4） |
| `.claude/skills/i-pr/SKILL.md` | あり | 暫定記述切替（IN.4） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| GitHub CLI `gh pr list` reference | https://cli.github.com/manual/gh_pr_list | `--head <branch>` で head branch 指定の filter、`--state open` で open のみ、`--json number,headRefName` で構造化出力。複数件返却の場合は array length > 1 で識別可能（公式 manual） |
| GitHub CLI `gh api --paginate` reference | https://cli.github.com/manual/gh_api | `--paginate` flag が複数 page を自動で取得し JSON array を連結する旨を明記。GitHub REST 標準の `Link: rel="next"` を内部で辿る |
| GitHub REST API: List repository issues | https://docs.github.com/en/rest/issues/issues#list-repository-issues | `GET /repos/{owner}/{repo}/issues` は PR も含めて返す。`pull_request` プロパティの存在で PR と issue を区別する旨が公式 description に明記（"Note: GitHub's REST API considers every pull request an issue ... you can identify pull requests by the pull_request key") |
| GitHub REST API: List pull requests | https://docs.github.com/en/rest/pulls/pulls#list-pull-requests | `head` query param に `branch` を渡すと head branch 指定の絞り込みが効く（`gh pr list --head` の裏側） |
| Conventional Commits 1.0.0 | https://www.conventionalcommits.org/en/v1.0.0/ | `feat:` prefix の使用根拠（本 Issue は new feature 追加） |
| 既存実装: `sync_from_gitlab` | `kaji_harness/sync.py:271-363` | 対称実装の構造リファレンス（3 phase all-or-nothing、`_list_existing_cached_iids` / `_mark_cache_stale` / `_write_sync_meta` の利用パターン） |
| 既存実装: `GitLabProvider.resolve_pr_context` | `kaji_harness/providers/gitlab.py:466-485` | `pr_ref=f"gl:{iid}"` という prefix 形式の前例。本 Issue の `gh:<n>` 採用根拠 |
| 既存実装: `view_cached_gitlab_issue` | `kaji_harness/providers/local.py:804-826` | `.kaji/cache/gl-<iid>.json` layout のリファレンス。本 Issue で `gh-<n>.json` 化する整合先 |
| 既存実装: `runner._resolve_pr_context_safe` | `kaji_harness/runner.py:170-189` | `GitHubProviderError` 追加位置と「known provider error は WARN + None」原則 |
| 既存仕様: GitLab auto-close keyword 回避 | `docs/cli-guides/gitlab-mode.md:273-364` | GitHub mode でも同等の hazard pattern が発生する旨を `github-mode.md` で参照する根拠 |
| Phase 4 申し送り（PR context 注入） | `.kaji/issues/local-p1-1-*/issue.md` § PR context 注入 | 本 Issue の trigger 文脈。GitHub mode で `resolve_pr_context` の本実装が deferred されていた経緯 |
| deferred Issue | `.kaji/issues/local-p1-12-deferred-kaji-sync-from-github/issue.md` | 本 Issue が trigger を吸収する deferred tracking。完了条件に本文へのコメント追加が含まれる |
