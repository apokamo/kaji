---
id: local-p1-23
title: gitlab provider が glab の --hostname を全 subcommand に注入し issue/mr 経路が不通
state: open
slug: glab-hostname-flag-incompat
labels:
- type:bug
created_at: '2026-05-10T14:25:34Z'
---
> [!NOTE]
> **Worktree**: `../kaji-fix-local-p1-23`
> **Branch**: `fix/local-p1-23`

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] gitlab provider が glab に注入する `--hostname` を撤去し `GITLAB_HOST` 環境変数経路に切替える

Issue: local-p1-23

## 概要

`kaji_harness` の GitLab 経路 3 箇所が `glab` 起動時に第 1 グローバル位置で `--hostname gitlab.com` を注入しているが、`glab` v1.36 / v1.95 双方で `--hostname` は `glab api` / `glab auth` 系の sub-flag としてのみ実装されており、`glab issue` / `glab mr` 配下では `Unknown flag` で reject される。引数注入を全廃し、`subprocess.run(..., env={**os.environ, "GITLAB_HOST": "gitlab.com"})` で hostname を渡す形に統一する。

## 背景・目的

### Observed Behavior（OB）

`.kaji/config.local.toml` を `provider.type='gitlab'` (`apokamo/kaji`) に切替えた smoke test:

```text
$ kaji issue list --limit 5
unknown flag: --hostname
Usage:  glab issue list [flags]
exit: 1
```

`kaji sync from-gitlab` のみ exit 0 で成功する（後述「根本原因」§ 偶発成功の理由）。

直接 `glab` を叩いた切り分け（WSL2 Ubuntu, glab 1.95.0、本設計時点で再確認済）:

```text
$ glab api --hostname gitlab.com user                       → 200 OK
$ glab --hostname gitlab.com api user                       → 200 OK
$ glab --hostname gitlab.com issue list --repo apokamo/kaji → ❌ Unknown flag: --hostname
$ glab issue list --hostname gitlab.com --repo apokamo/kaji → ❌ Unknown flag: --hostname
```

→ `--hostname` を受理するのは「最終的な subcommand が `api` / `auth` 系の場合」のみ。`issue` / `mr` 系では位置に関係なく reject される。

### Expected Behavior（EB）

`provider.type='gitlab'` 配下で `kaji issue {list,create,note,close,edit}` / `kaji pr {create,view,list,merge,note,approve,revoke,...}` が `Unknown flag` を出さずに `glab` を起動でき、`gitlab.com` を target host として API を叩く。

根拠（一次情報）— 詳細は § 参照情報（Primary Sources）の三主張別表を参照:

| 主張 | 情報源（要約） |
|------|---------------|
| `--hostname` は global flag ではない | `glab --help` (v1.95.0)：`FLAGS` 節は `-h --help` / `-v --version` のみ |
| `--hostname` は `api` の sub-flag | `glab api --help` (v1.95.0)：`To override the GitLab hostname, use --hostname.` を `api` の説明文中に記載 |
| `GITLAB_HOST` は subcommand 非依存の host 指定手段 | `glab auth status --help` (v1.95.0) + GitLab CLI 公式 docs (https://docs.gitlab.com/cli/) |
| GitLab mode で `glab issue` / `glab pr` は skill 互換 contract で起動成功する責務 | 本リポジトリ `docs/cli-guides/gitlab-mode.md` § 2 |

### 再現手順（steps-to-reproduce）

最小再現環境:

- `glab` v1.36 以上 (本確認は 1.95.0)
- `glab auth login` 済 もしくは `GITLAB_TOKEN` 設定済（PAT scope: `api`）
- `gitlab.com:apokamo/kaji` への到達性

手順:

1. `.kaji/config.local.toml` を以下に切替える:
   ```toml
   [provider]
   type = "gitlab"

   [provider.gitlab]
   repo = "apokamo/kaji"
   default_branch = "main"
   ```
2. `kaji config provider-type` が `gitlab` を返すことを確認
3. `kaji issue list --limit 5` を実行 → `unknown flag: --hostname` で exit 1（OB 再現）
4. 比較として `kaji sync from-gitlab` を実行 → exit 0（偶発成功）

### 同根の他経路の調査結果

`grep -n '"--hostname"' kaji_harness/` の網羅:

| ファイル | 関数 | 起動形 | 現状の挙動 | 修正要否 |
|----------|------|--------|-----------|----------|
| `kaji_harness/providers/gitlab.py:94` | `_run_glab` | `glab --hostname gitlab.com {issue,mr,api,...}` | `issue/mr` 系全 mutating で Unknown flag。`api` 経由（discussions/notes/approvals 等の read 系）は偶発的に動作 | **要** |
| `kaji_harness/cli_main.py:1384` | `_forward_to_glab` | `glab --hostname gitlab.com {issue,pr} ...` | dispatcher 全経路（`kaji issue`/`kaji pr`）で Unknown flag | **要** |
| `kaji_harness/sync.py:114` | `_glab_api_get` | `glab --hostname gitlab.com api <endpoint>` | `api` のみ起動するため偶発成功（exit 0）。Issue 仕様（GitLab CLI が hostname を `--hostname` global では受理しない）に整合しないため、整合性のため同様修正 | **要**（整合性） |

issue 起票時に列挙された 3 箇所と一致。これ以外の `--hostname` ハードコードは存在しない（テスト fixture と既存設計書 markdown を除く）。

## 根本原因（Root Cause）

### 何が間違っていたか

`glab` の `--hostname` は **特定 subcommand の sub-flag**（`api` / `auth login` / `auth status` 等）であり、global flag ではない。にもかかわらず実装は `glab` の第 1 引数位置 (`["glab", "--hostname", "gitlab.com", ...subcommand]`) に注入していた。`issue` / `mr` 系 subcommand の flag parser は `--hostname` を知らず、Unknown flag で reject する。

### いつから壊れているか

- 導入: `4a89ec8 fix: pin gitlab.com hostname in all glab invocations (local-pc5090-5)` （`local-p1-5` の review fix）
- 同時に 3 箇所（providers / dispatcher / sync）に同パターンで挿入
- 後続 commit ではテストも `--hostname` の存在を assert する形で固定された（`tests/test_providers_gitlab.py::TestHostnamePinning` ほか）

### 偶発成功の理由（sync.py のみ exit 0）

`glab` の flag parser は「`api` subcommand に到達したケース」では、global 位置の `--hostname` を `api` の sub-flag として吸収する（実機で `glab --hostname gitlab.com api user` が 200 OK になることを再確認済）。`sync._glab_api_get` は `glab api` 専用 helper のため、この副作用で偶発的に通っていた。`providers.gitlab._run_glab` 経由でも `glab api` を呼ぶ経路（discussions / notes / approvals 等の read 系）は同じ理由で動作していたため、自動テスト・large_gitlab テストの一部はこのバグを検出できなかった。

### なぜ正規仕様（環境変数経路）に揃えるか

3 つの一次情報主張から導かれる:

1. **`--hostname` は subcommand ごとに有無が異なる**（`api` / `auth login` / `auth status` 等にのみ存在し、`issue` / `mr` には存在しない。§ 参照情報の主張 ① + ②）→ 全 subcommand 共通の hostname pin を引数で表現する手段は **存在しない**
2. **`GITLAB_HOST` は subcommand 非依存の host 指定手段**（§ 参照情報の主張 ③：`glab auth status --help` が `git remote` / `GITLAB_HOST` / config の 3 経路を host 解決要素として明示。GitLab CLI 公式 docs にも `GITLAB_HOST or GL_HOST` 環境変数が記載）
3. 環境変数経路は `glab` の hostname 解決優先順位の中で「config を上書きし、コマンドラインでは見えない pin」として安定に機能する（subcommand を選ばない）

なお、本セクションは「`glab --help` の global flag 領域に `GITLAB_HOST` が記載されている」とは主張しない。`glab --help` の `FLAGS` 節は `-h --help` / `-v --version` のみで、`GITLAB_HOST` への言及は無い。`GITLAB_HOST` の正当性根拠は `glab auth status --help` の host 解決説明と公式 docs の 2 つに依拠する。

## インターフェース

公開 IF（CLI 出力 / Provider Python API）は不変。subprocess 起動側のみ:

| 関数 | 変更前 args / env | 変更後 args / env |
|------|-------------------|-------------------|
| `GitLabProvider._run_glab` | `["glab", "--hostname", "gitlab.com", *args]`, env=`os.environ` | `["glab", *args]`, env=`{**os.environ, "GITLAB_HOST": "gitlab.com"}` |
| `cli_main._forward_to_glab` | `["glab", "--hostname", "gitlab.com", group, *args]`, env=`os.environ` | `["glab", group, *args]`, env=`{**os.environ, "GITLAB_HOST": "gitlab.com"}` |
| `sync._glab_api_get` | `["glab", "--hostname", "gitlab.com", "api", endpoint]`, env=`os.environ` | `["glab", "api", endpoint]`, env=`{**os.environ, "GITLAB_HOST": "gitlab.com"}` |

後方互換: ユーザに見える挙動は「`provider.type='gitlab'` 配下で `kaji issue/pr` が起動失敗しなくなる」という前進のみ。互換 break は無し。

## 変更スコープ

### 修正対象（プロダクション）

- `kaji_harness/providers/gitlab.py` — `_run_glab` の `cmd` 構築と `subprocess.run(env=...)` 注入。docstring（`--hostname` 注入の説明）を `GITLAB_HOST` env 注入に書き換える
- `kaji_harness/cli_main.py` — `_forward_to_glab` の `cmd` 構築と `subprocess.run(env=...)` 注入。`_GITLAB_HOSTNAME_FOR_DISPATCH` 定数の用途 docstring を更新
- `kaji_harness/sync.py` — `_glab_api_get` の `cmd` 構築と `subprocess.run(env=...)` 注入

### 修正対象（テスト）

`grep -rn '"--hostname"' tests/` で機械抽出:

- `tests/test_providers_gitlab.py` — `TestHostnamePinning`（7 件）、`TestViewIssue` の cmd assert（4 件）、`TestCreateIssue` の cmd assert（1 件）、`TestRunGlabApi` の cmd assert（1 件）
- `tests/test_phase4_dispatcher_gitlab.py` — `cmd` assert（1 件）

これらの assertion を「`--hostname` が cmd に **含まれない** こと」「`subprocess.run` の `env` kwarg に `GITLAB_HOST=gitlab.com` が **含まれる** こと」に書き換える。具体パターンは下記「テスト戦略 § 再現テスト」を参照。

### 修正対象外

- `tests/test_large_gitlab/conftest.py` / `tests/test_large_gitlab/test_*.py` — 自身の verification 用に `glab api --hostname` / `glab auth status --hostname` を呼ぶが、いずれも **`--hostname` を sub-flag として正しい位置（subcommand の後）** で使っている、または偶発成功する経路（`glab --hostname X api ...`）。本 fix とは独立で、別 Issue で `GITLAB_HOST` 経由に揃えるかは判断（本 fix のスコープ外）
- `docs/cli-guides/gitlab-mode.md` の `glab auth login --hostname gitlab.com` 記述 — `auth login` は `--hostname` を正規 sub-flag として持つため、修正不要

## 方針（修正アプローチ）

### A. 共通 helper の導入

3 箇所で同一の env 構築が必要なため、moduleスコープの小さな helper を 1 箇所に置き、3 サイトから参照する。

候補配置: `kaji_harness/providers/gitlab.py` に `_glab_env(extra: Mapping[str, str] | None = None) -> dict[str, str]` を `module-level` で定義し、cli_main / sync からも import する。

```python
# kaji_harness/providers/gitlab.py
def _glab_env() -> dict[str, str]:
    """``GITLAB_HOST`` を pin した env を返す。subprocess.run(..., env=_glab_env()) で使う。

    `glab` の hostname 解決優先順位は git directory > config > env > default のため、
    ``GITLAB_HOST=gitlab.com`` の env 注入は config で別 host を持つ workstation でも
    ``provider.gitlab.repo`` が指す project （= gitlab.com）に強制 pin する。
    """
    return {**os.environ, "GITLAB_HOST": _GITLAB_HOSTNAME}
```

代替案: 各サイトで `env={**os.environ, "GITLAB_HOST": "..."}` を直書きする。helper 化のメリット（一貫性 + テスト集約）が小さいコストで得られるため helper 案を採用する。

### B. 各サイトの修正

最小侵襲で `cmd` から `--hostname` 2 要素を除去し、`subprocess.run` 呼び出しに `env=_glab_env()` を追加する。docstring は「`--hostname` を default 注入する」旨を「`GITLAB_HOST` env を注入する」旨に書き換える。それ以外のロジックは触らない（リファクタ混在を避ける）。

### C. テストの整理

既存 `TestHostnamePinning` は「`cmd[:3] == ["glab", "--hostname", "gitlab.com"]`」を assert していた → 「`cmd[:1] == ["glab"]` かつ `--hostname` not in cmd」「`subprocess.run` 呼び出しの `env` kwarg に `GITLAB_HOST=gitlab.com`」を assert する形に書き換える。クラス名は `TestHostnameEnvPinning` 等に rename（または `TestGitlabHostPinning` に統一）し、何を保証するテストかを意図と合わせる。

その他 `TestViewIssue` / `TestCreateIssue` / `TestRunGlabApi` 等の `cmd[:N]` assert も、`--hostname` を含まない subcommand 直結の slice に調整する（`cmd[:3] == ["glab", "issue", "create"]` 等）。

### D. 同根欠陥の予防

回帰防止: `--hostname` を引数注入しないことを **3 サイトすべてで** assert する Small テストを残す。env 側は `GITLAB_HOST` が `subprocess.run` に渡されることを **3 サイトすべてで** assert する。これにより「将来また global 位置に `--hostname` を生やす」回帰を即座に検出できる。

## テスト戦略

### 変更タイプ
実行時コード変更（subprocess 起動 args / env の変更）

### Small テスト（必須・再現テスト含む）

`tests/test_providers_gitlab.py` / `tests/test_phase4_dispatcher_gitlab.py` の既存 `subprocess.run` mock パターンを踏襲する。**修正前 FAIL → 修正後 PASS** を満たすこと。

#### 再現テスト（regression test）

| サイト | 検証観点 | 修正前の挙動 |
|--------|----------|--------------|
| `GitLabProvider._run_glab` | view_issue / list_issues / list_labels / create_issue / edit_issue / comment_issue / close_issue 起動時、`subprocess.run` に渡される `cmd` に `"--hostname"` が **含まれない**、かつ `env` kwarg に `GITLAB_HOST="gitlab.com"` が **含まれる** | `cmd` に `--hostname gitlab.com` が含まれ、`env` 未指定。実機で `glab issue {list,create,...}` が `Unknown flag` で reject される |
| `cli_main._forward_to_glab` | `kaji issue create` / `kaji pr create` 等の dispatcher 経路で `subprocess.run` に渡される `cmd` に `"--hostname"` が **含まれない**、かつ `env` kwarg に `GITLAB_HOST="gitlab.com"` が **含まれる** | 同上 |
| `sync._glab_api_get` | `kaji sync from-gitlab` 経路で `subprocess.run` に渡される `cmd` に `"--hostname"` が **含まれない**、かつ `env` kwarg に `GITLAB_HOST="gitlab.com"` が **含まれる** | `cmd` に `--hostname gitlab.com` が含まれる（実機は偶発成功するが内部仕様としては不正） |

mock 実装パターン:

```python
captured: list[tuple[list[str], dict[str, str] | None]] = []

def fake_run(cmd, **kw):
    captured.append((cmd, kw.get("env")))
    return _ok(stdout="...")  # endpoint に応じた stub 返却
```

assert 例:

```python
cmd, env = captured[0]
assert "--hostname" not in cmd
assert env is not None and env.get("GITLAB_HOST") == "gitlab.com"
```

#### 既存テストの更新

- `TestHostnamePinning` 系（既存 7 メソッド + dispatcher 1 メソッド）: assertion を上記再現テストの形に書き換え、テスト目的を「`--hostname` 引数注入 → `GITLAB_HOST` env 注入」への切替の保証に再定義する
- `TestViewIssue` / `TestCreateIssue` / `TestRunGlabApi` 等の `cmd[:N]` slice assert: `--hostname` を含まない slice に調整（例: `cmd[:1] == ["glab"]`、`cmd[1:3] == ["issue", "create"]`）

### Medium テスト

不要。subprocess の起動 args / env 変更のみで、ファイル I/O や DB 結合の構造は変わらない。Small で `subprocess.run` の cmd / env 引数を完全に観測できるため Medium で追加する観点が無い。

### Large テスト

`tests/test_large_gitlab/test_*.py` は実 `glab` を呼ぶため、本 fix の挙動変化（GITLAB_HOST 経路へ）を結果的に通過する。新規 Large テスト追加は不要だが、**実機 smoke 再現** を完了条件として実施する:

1. 修正後 HEAD で `.kaji/config.local.toml` を gitlab に再切替
2. `kaji issue list --limit 5` → `Unknown flag` を出さずに glab が起動し、API レスポンス由来のリスト（または権限不足等の別 error）になることを確認
3. `kaji sync from-gitlab` が引き続き exit 0 で動作することを確認（regression がないこと）
4. `provider.type='local'` に戻して通常運用を継続

実機 mutating smoke（`kaji issue create` ～ `kaji pr merge` の通し）は Issue § スコープ外で別途実施。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定はない（既存 EPIC `local-p1-4` 確定事項 #3「gitlab.com 固定」を別実装手段で再達成するのみ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ層構成は不変 |
| docs/dev/ | なし | ワークフロー・開発手順への影響なし |
| docs/reference/ | なし | API 仕様は subprocess 起動の内部実装のみ。Provider の Python API は不変 |
| docs/cli-guides/gitlab-mode.md | **なし** | 全文確認済（本設計時点）。`--hostname` への言及は § 1.2 / § 1.5 / § 5.2 の `glab auth login --hostname gitlab.com` の 3 箇所のみで、すべて `auth login` の正規 sub-flag 用法（修正不要）。§ 1.4 の `hostname フィールドは持たない（gitlab.com 固定。self-hosted 非対応）` は config schema の説明であり、kaji が `glab` に `--hostname` を注入する旨の記述は存在しない。実装メカニズム（引数注入 → env 注入）が変わってもユーザに見える運用手順・config 仕様は不変のため更新不要 |
| docs/cli-guides/local-mode.md | なし | local mode は本変更と無関係 |
| CHANGELOG.md | あり | bug fix エントリを追加（`fix:` 区分） |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

設計判断は以下 3 つの主張に依拠する。各主張ごとに一次情報を分離して列挙する。

### 主張 ①: `--hostname` は `glab` の global flag ではない

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `glab --help` (v1.95.0 ローカル実行出力) | 本設計時点で WSL2 上で実行確認（再現コマンド: `glab --help`） | `FLAGS` 節は以下 2 件のみ — `-h --help: Show help for this command.` / `-v --version: Show glab version information.`。`--hostname` は global flag に存在しない。`COMMANDS` 節に `api` / `auth` / `issue` / `mr` 等の subcommand のみ列挙 |

### 主張 ②: `--hostname` は `glab api` (および `glab auth login` / `glab auth status` 等) の sub-flag

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `glab api --help` (v1.95.0 ローカル実行出力) | 本設計時点で WSL2 上で実行確認（再現コマンド: `glab api --help`） | `api` subcommand の説明文中に `If the current directory is a Git directory, uses the GitLab authenticated host in the current directory. Otherwise, gitlab.com will be used. To override the GitLab hostname, use --hostname.` と記載 |
| `glab auth status --help` (v1.95.0 ローカル実行出力) | 同上（再現コマンド: `glab auth status --help`） | `FLAGS` 節に `--hostname    Check a specific instance's authentication status.` が `auth status` の sub-flag として明示 |
| 実機切り分け（本設計時点で再確認） | 設計書 § OB のコマンド出力 | `glab api --hostname gitlab.com user` → 200 OK / `glab --hostname gitlab.com api user` → 200 OK（`api` に到達するケースのみ吸収）/ `glab --hostname gitlab.com issue list --repo apokamo/kaji` → ❌ Unknown flag（`issue` の parser は `--hostname` を知らない） |

### 主張 ③: `GITLAB_HOST` は subcommand 非依存の host 指定手段（正規ルート）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `glab auth status --help` (v1.95.0 ローカル実行出力) | 本設計時点で WSL2 上で実行確認 | `By default, this command checks the authentication state of the GitLab instance determined by your current context (git remote, GITLAB_HOST environment variable, or configuration).` — host 解決の 3 経路（`git remote` / `GITLAB_HOST` / config）の 1 つとして `GITLAB_HOST` を明示 |
| GitLab CLI 公式 docs | https://docs.gitlab.com/cli/ | `GITLAB_HOST` / `GL_HOST` 環境変数による host 指定がドキュメント化されている（reviewer 検証済の公開 URL） |

### 補助情報（背景・経緯）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 本リポジトリ過去 commit | `4a89ec8 fix: pin gitlab.com hostname in all glab invocations (local-pc5090-5)` | 本バグの混入元。`--hostname` を 3 サイトに同時注入した review fix |
| 本リポジトリ既存 docs | `docs/cli-guides/gitlab-mode.md` § 2 | `provider.type='gitlab'` 配下で `kaji issue` / `kaji pr` は GitHub mode と同じ skill 互換 contract で動作する責務（`Unknown flag` で起動失敗してはならない契約の出所） |


</details>


## 概要

`kaji_harness` の GitLab provider が `glab` 起動時に `--hostname gitlab.com` を全 subcommand に強制注入しているが、`glab` v1.36.0 / v1.95.0 双方で `--hostname` は `glab api` 専用 sub-flag として実装されている。結果として `kaji issue list` / `kaji issue create` 等の `glab issue` / `glab mr` を経由する全 mutating パスが起動直後に `Unknown flag: --hostname` で reject され、`provider.type='gitlab'` 配下では `glab api` を直叩きする `kaji sync from-gitlab` のみ偶然動作する。

## 目的

### Observed Behavior（OB）

`.kaji/config.local.toml` overlay を `provider.type = "gitlab"` (`apokamo/kaji`) に切り替え、smoke test を実施した結果:

```
$ kaji issue list --limit 5
unknown flag: --hostname

Usage:  glab issue list [flags]

Flags:
  -A, --all                    Get all issues
  -a, --assignee string        Filter issue by assignee <username>
  ...
exit: 1
```

```
$ kaji sync from-gitlab
Fetching open issues from gitlab.com:apokamo/kaji ...
Wrote 0 issues to .kaji/cache/ (0 newly added, 0 updated, 0 unchanged signature).
Sync completed at 2026-05-10T14:07:20Z (0 issues, 0 pages, 1.4s).
exit: 0
```

`glab` を直接叩いて切り分けた結果（再現環境: WSL2 Ubuntu, glab v1.95.0）:

```
$ glab api --hostname gitlab.com user                       → 200 OK (apiは受理)
$ glab issue list --hostname gitlab.com --repo apokamo/kaji → ❌ Unknown flag: --hostname
$ glab --hostname gitlab.com issue list --repo apokamo/kaji → ❌ Unknown flag: --hostname
```

glab 1.36.0 → 1.95.0（公式最新、2026-05-08 リリース）に bump しても再現する一貫した挙動。

### Expected Behavior（EB）

`provider.type = "gitlab"` 配下で `kaji issue list` / `kaji issue create` / `kaji issue note` / `kaji issue close` / `kaji pr ...` が `Unknown flag` を出さずに `glab` を起動でき、`glab` が `gitlab.com` を target host として API を叩く。

根拠:

- `docs/cli-guides/gitlab-mode.md` § 1.4 に `provider.type='gitlab'` 配下では `glab issue` / `glab pr` が「GitHub mode と同じ skill 互換 contract」で動作することが明記されている
- `glab --help` 出力に `GITLAB_HOST or GL_HOST: Specify the URL of the GitLab server if self-managed.` と記載されており、hostname 切替は **環境変数経由が正しい仕様**
- `glab api --help` でのみ `--hostname` フラグが定義されている（global flag ではなく api 専用 sub-flag）

### 再現手順（Steps to Reproduce）

1. 前提環境: `glab` CLI が install 済（v1.36 以上で同一挙動）、`glab auth login` 済（PAT scope `api`）、`gitlab.com:<group>/<project>` への SSH 疎通済
2. `.kaji/config.local.toml` を以下に切替:
   ```toml
   [provider]
   type = "gitlab"

   [provider.gitlab]
   repo = "apokamo/kaji"
   default_branch = "main"
   ```
3. `kaji config provider-type` で `gitlab` が解決されることを確認
4. `kaji issue list --limit 5` を実行
5. 観測される出力: `unknown flag: --hostname` で exit 1

別経路の確認:

- `kaji sync from-gitlab` は exit 0 で成功する（`glab api projects/...` 経路のため `--hostname` を受理）
- 同 overlay 配下で `kaji issue create` / `kaji issue note` / `kaji issue close` / `kaji pr create` / `kaji pr merge` / `kaji pr note` / `kaji pr approve` も同じ `unknown flag` でエラーになると見込まれる（dispatcher が `_forward_to_glab` または `_run_glab` 経由で `glab issue` / `glab mr` を起動するため）

## 完了条件

- [x] 設計書で根本原因（`--hostname` の glab 仕様誤認 = `api` 専用 sub-flag を global と誤って扱った）と修正方針（引数注入を停止し、環境変数 `GITLAB_HOST` 経由で hostname を渡す）が特定されている
- [x] 同根の他の壊れ箇所の調査結果が設計書に記載されている（`kaji_harness/{providers/gitlab.py:94, cli_main.py:1384, sync.py:114}` の 3 箇所が同パターン。3 つ目は `glab api` 起動のため偶然動作している点も含めて記載）
- [x] `kaji_harness/providers/gitlab.py:_run_glab` / `kaji_harness/cli_main.py:_forward_to_glab` / `kaji_harness/sync.py:_glab_api_get` の 3 箇所から `--hostname <host>` の引数注入を削除し、`subprocess.run(..., env={**os.environ, "GITLAB_HOST": _GITLAB_HOSTNAME})` で hostname を渡している
- [x] 再現テストが 1 本以上追加され、修正前は FAIL（subprocess 起動 args に `--hostname` が含まれる、または `glab issue list` が `Unknown flag` を返す挙動を fake で再現）、修正後は PASS することを確認
- [x] 影響モジュール（`tests/` の gitlab provider / cli_main dispatcher / sync 関連既存テスト）が green。subprocess args/env を assert している既存テストは `--hostname` 参照を削除し env 注入を assert する形に更新する
- [x] `make check` 通過
- [x] 実機 smoke 再現: 修正後の HEAD で `provider.type=gitlab` overlay を再適用し、`kaji issue list` が `Unknown flag` を出さずに（権限/ラベル等の別要因を除き）正常に `glab issue list` を起動できることを確認

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - `kaji_harness/providers/gitlab.py:_run_glab`（GitLabProvider 全 mutating: issue create/edit/note/close、mr review 経由の approve/revoke、その他）
  - `kaji_harness/cli_main.py:_forward_to_glab`（`kaji issue` / `kaji pr` の GitLab dispatcher 全経路: create/view/list/edit/close/comment/merge/note/approve/revoke）
  - `kaji_harness/sync.py:_glab_api_get`（`glab api` 経路。現状偶然動作しているが、引数注入の整合性のため同様に env に統一する）
- 深刻度: 中〜高（gitlab provider の write 系全経路が起動直後に reject されるため、`provider.type='gitlab'` 配下での運用が成立しない。dev 検証フェーズ中で運用 blocker ではないが、gitlab mode 本格採用の前提条件）
- 回避策の有無: なし。`provider.type='gitlab'` 配下では `glab` を別途手動で叩くしかなく、kaji の skill ワークフローを通せない。`provider.type='local'` に戻せば作業継続は可能（現状の運用形態）

## スコープ外

- self-managed GitLab への対応（既存 `docs/cli-guides/gitlab-mode.md` § 1.4 で明示的に non-goal、本 fix とは独立）
- `glab` CLI バージョンの最低要件文書化（別 docs 改善 Issue で扱う）
- gitlab provider の他の glab 仕様誤認（実装中に発見次第、別 Issue に切り出す）
- write 系 smoke test 自体（`kaji issue create` 〜 `kaji pr merge` の実機通し検証は本 fix のマージ後に別作業として実施）

## 参考

- 関連 Issue: `local-p1-22`（kaji step runner の独立した別 bug。同じ検証セッション中に発見）
- 関連実装:
  - `kaji_harness/providers/gitlab.py:78-104`（`_run_glab` / `_glab_api_get`）
  - `kaji_harness/cli_main.py:1365-1389`（`_forward_to_glab`）
  - `kaji_harness/sync.py:104-128`（`_glab_api_get`）
- glab CLI 仕様:
  - `glab --help` 出力の `GITLAB_HOST or GL_HOST` 環境変数記載（hostname 切替の正規ルート）
  - `glab api --help` で `--hostname` が sub-flag として定義されている（他 subcommand には無い）
- 既存 docs: `docs/cli-guides/gitlab-mode.md` § 1.4 で「`hostname` フィールドは持たない（`gitlab.com` 固定。self-hosted 非対応）」と明記
- 観測環境: WSL2 Ubuntu, glab 1.95.0 (公式 .deb 最新), `apt-mark hold glab` 済（apt 自動 downgrade を抑止）
- 検証実施日: 2026-05-10。`.kaji/config.local.toml` を gitlab に切替 → smoke test → 障害発見 → local 切り戻しの順で実施。本 Issue 作成時点で provider は local に復帰済み
