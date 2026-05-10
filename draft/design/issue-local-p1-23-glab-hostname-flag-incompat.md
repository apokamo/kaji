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

根拠（一次情報）:

| 情報源 | 引用/要約 |
|--------|-----------|
| `glab --help` (1.95.0 ローカル実行) | global flag 一覧に `--hostname` は存在しない（subcommand 一覧のみ） |
| `glab api --help` (1.95.0 ローカル実行) | `If the current directory is a Git directory, uses the GitLab authenticated host in the current directory. Otherwise, gitlab.com will be used. To override the GitLab hostname, use --hostname.` — `--hostname` は **`api` の sub-flag 説明** に置かれている |
| GitLab CLI ドキュメント `glab` 認証 | `GITLAB_HOST` / `GL_HOST` 環境変数で host を指定する。https://docs.gitlab.com/editor_extensions/gitlab_cli/ |
| 本リポジトリ `docs/cli-guides/gitlab-mode.md` § 1.4 | `provider.type='gitlab'` 配下で `glab issue` / `glab pr` は GitHub mode と同じ skill 互換 contract で動作する（= `Unknown flag` で起動失敗してはならない） |

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

- `glab --help` / `glab api --help` の双方で **hostname 切替の正規ルートは `GITLAB_HOST` 環境変数** と明記されている
- `--hostname` の sub-flag は subcommand ごとに有無が異なる → 全 subcommand 共通の hostname pin を引数で表現する手段は **存在しない**
- 環境変数経路は subcommand に依存せず effective、`glab` の hostname 解決優先順位（git directory > config > env > default）の中で「config を上書きし、コマンドラインでは見えない pin」として安定に機能する

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
| docs/cli-guides/gitlab-mode.md | **要確認** | § 1.2 `glab auth login --hostname gitlab.com` は `auth login` の正規 sub-flag のため不変。§ 1.4 「self-hosted 非対応」のメカニズム説明に「`--hostname` 注入で pin」のような記述がある場合は「`GITLAB_HOST` env 注入で pin」に書き換える必要がある（実装コミット時に確認） |
| docs/cli-guides/local-mode.md | なし | local mode は本変更と無関係 |
| CHANGELOG.md | あり | bug fix エントリを追加（`fix:` 区分） |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `glab --help`（v1.95.0 ローカル実行出力） | 本設計時点で WSL2 上で実行確認 | global flag 領域に `--hostname` は存在しない。`api` / `auth` / `issue` / `mr` 等の subcommand 一覧のみ |
| `glab api --help`（v1.95.0 ローカル実行出力） | 同上 | `If the current directory is a Git directory, uses the GitLab authenticated host in the current directory. Otherwise, gitlab.com will be used. To override the GitLab hostname, use --hostname.` — `--hostname` は **`api` の sub-flag** として記載 |
| GitLab CLI 公式 docs | https://docs.gitlab.com/editor_extensions/gitlab_cli/ | hostname 切替は `GITLAB_HOST` / `GL_HOST` 環境変数で行うのが正規ルート |
| 実機切り分け（本設計時点で再確認） | コマンド出力上記「OB」セクション | `glab api --hostname X user` → 200 OK / `glab --hostname X api user` → 200 OK / `glab --hostname X issue list --repo R` → ❌ Unknown flag。`--hostname` は subcommand `api` に到達するケースで吸収され、`issue`/`mr` では reject される |
| 本リポジトリ過去 commit | `4a89ec8 fix: pin gitlab.com hostname in all glab invocations (local-pc5090-5)` | 本バグの混入元。`--hostname` を 3 サイトに同時注入した review fix |
| 本リポジトリ既存 docs | `docs/cli-guides/gitlab-mode.md` § 1.2, § 1.4 | `provider.type='gitlab'` 配下で `glab issue` / `glab pr` が GitHub mode 互換で動作することが skill 契約。Unknown flag で起動失敗してはならない |
