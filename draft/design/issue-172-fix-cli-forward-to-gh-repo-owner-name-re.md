# [設計] `_forward_to_gh` / `_forward_to_glab` の `--repo` インライン形式検出修正

Issue: #172

## 概要

`_forward_to_gh` および同根の `_forward_to_glab` (`kaji_harness/cli_main.py`) の repo 注入ガードを、`--repo` / `-R` の **独立トークン形式** だけでなく **インライン代入形式**（`--repo=owner/name` / `-R=owner/name` / `-Rowner/name`）も検出するよう修正し、両 forwarder の docstring に明記された「user `--repo` wins」契約を全形式で守る。

> **スコープ拡張の根拠**: review-design で `_forward_to_glab` (`cli_main.py:1399-1421`) が `_forward_to_gh` の symmetric wrapper として同一 guard・同一契約を持つことが判明した。Issue 本文タイトルは `_forward_to_gh` のみだが、同根 bug を片方だけ直して片方を残すことは type:bug 設計ガイド §4「同じ原因で他にも壊れている箇所がないか」の趣旨に反するため、本 Issue で両方を fix する。GitLab dispatcher の回帰テストも本 Issue で追加する。

## 背景・目的

### Observed Behavior (OB)

`kaji_harness/cli_main.py:498`（`_forward_to_gh`、PR #170 時点 commit `f492b7d`）:

```python
if repo and "--repo" not in args and "-R" not in args:
    # gh は --repo を sub の前後どちらでも受理する。末尾追加で副作用最小
    args = [*args, "--repo", repo]
```

同じパターンが `kaji_harness/cli_main.py:1420`（`_forward_to_glab`）にも存在:

```python
if "--repo" not in args and "-R" not in args:
    args = [*args, "--repo", repo]
```

両者とも、`"--repo" not in args` は文字列 `--repo` の完全一致トークンのみ検出する。`args` に `--repo=apokamo/other-repo` が含まれていても、要素として文字列 `--repo` 自体は存在しないため条件が真になり、`["pr", "list", "--repo=apokamo/other-repo", "--repo", "kamo/kaji"]` が生成され、`gh` / `glab` 側に `--repo` が 2 回渡る。

`-R=owner/name` / `-Rowner/name` 形式も同様に取りこぼす。

### Expected Behavior (EB)

`_forward_to_gh` の docstring（`kaji_harness/cli_main.py:475`）:

> 既に user が `--repo` を渡している場合は user 値を尊重し触らない

`_forward_to_glab` の docstring（`kaji_harness/cli_main.py:1405-1406`）:

> user が `--repo` を渡している場合は user 値を尊重して触らない（`_forward_to_gh` と同方針）

この契約は argv トークン形式に依存しない。ユーザが以下のいずれかの形式で `--repo` / `-R` を渡した場合、config 由来 repo の注入をスキップする:

| 形式 | 例 |
|------|-----|
| 独立トークン (long) | `--repo owner/name` |
| 独立トークン (short) | `-R owner/name` |
| インライン代入 (long) | `--repo=owner/name` |
| インライン代入 (short, `=` 付き) | `-R=owner/name` |
| 短縮連結 (`=` なし) | `-Rowner/name` |

`gh` / `glab` CLI は両方とも Go 製で cobra/pflag を flag parser に使用しており、pflag は POSIX 短縮形式と GNU long インライン形式の両方を受理する（参照: 「参照情報」§ pflag / § cli/cli go.mod / § gitlab-org/cli go.mod）。よって、ユーザは上記すべての形式を等価に使える前提に立ち、kaji 側のガードもそれに揃える必要がある。

### Bettenburg et al. (2008) — OB + EB + steps-to-reproduce

OB と EB を分離し、再現手順は次節で固定する。

## 再現手順

### gh 経路

1. `provider.type='github'` かつ `[provider.github] repo = "kamo/kaji"` が設定された config を用意
2. `kaji pr list --repo=apokamo/other-repo` を実行
3. 観測: `_forward_to_gh` が `--repo kamo/kaji`（config 値）を末尾に追加し、subprocess には
   `gh pr list --repo=apokamo/other-repo --repo kamo/kaji` が渡る
4. `gh` 側で「`--repo` が 2 回指定された」エラーになる、または first-wins / last-wins のいずれかで意図しないリポジトリ操作になる（pflag 動作。kaji 視点では仕様未定義扱い）

短縮形（`-R=...` / `-Rapokamo/other-repo`）も同様に再現する。

### glab 経路

1. `provider.type='gitlab'` かつ `[provider.gitlab] repo = "group/project"` が設定された config を用意
2. `kaji pr list --repo=group/other-project` を実行
3. 観測: `_forward_to_glab` が `--repo group/project`（config 値）を末尾に追加し、subprocess には
   `glab mr list --repo=group/other-project --repo group/project` が渡る
4. `glab` 側で同様の二重指定 / 意図しないプロジェクト操作になる

## 根本原因 (Root Cause)

### なぜ間違っているか

`"--repo" not in args` / `"-R" not in args` は **list element equality** による完全一致判定。argv の構造が「flag と value が独立トークン」前提でしか機能しない。pflag が許容する `--flag=value` 形式・短縮連結形式は、要素文字列が `--repo=owner/name` のように「flag 名そのもの」と異なるため、ガードが false negative になる。

ガードに必要な意味的判定は「ユーザが意図的に repo を指定したか」であり、「特定の文字列リテラルが argv list に含まれるか」ではない。両者がたまたま独立トークン形式でだけ一致していたのが原因。

### いつから壊れているか

- `_forward_to_gh`: PR #170 (GitHub #169) で `[provider.github] repo` を `--repo` で gh に伝搬する機能を追加した時点から（`kaji_harness/cli_main.py:498` 周辺）。Codex 自動レビューが P2 として指摘し、本 Issue で fix
- `_forward_to_glab`: GitLab provider 実装時点から（`_forward_to_gh` の symmetric として導入された経緯。docstring に「同方針」と明記）

### 同じ原因で他にも壊れている箇所がないか

`kaji_harness/` 配下を `--repo|--R` / `not in args` の組み合わせで再 grep した結果:

| 箇所 | 評価 |
|------|------|
| `_forward_to_gh` (`cli_main.py:498`) | **本 Issue 対象** |
| `_forward_to_glab` (`cli_main.py:1420`) | **本 Issue 対象**（review-design で発見、同根 bug） |
| `_FORGE_METHOD_FLAGS` フィルタ (`cli_main.py:495`、`a not in _FORGE_METHOD_FLAGS`) | **対象外**。`--merge` / `--squash` / `--rebase` の bool フラグ用で、`gh pr merge` でこれらは値を取らない（pflag では bool は `--merge` 単独で true）。`--merge=true` 等のインライン形式は意味を持たず実害なし |

他の `not in args` パターンで repo / 同種の取りこぼしを起こしうる箇所は本 grep では検出されなかった。実装フェーズ序盤に同 grep を再実行し、新規ヒットがあれば設計を補完するか別 Issue として切り出す。

## インターフェース

`_forward_to_gh(group: str, raw_args: list[str], *, repo: str | None = None) -> int` および `_forward_to_glab(group: str, raw_args: list[str], *, repo: str) -> int` の **どちらも public signature は変更しない**。

両者の if 条件を共通 helper 呼び出しに置換:

- 変更前 (`_forward_to_gh`): `repo and "--repo" not in args and "-R" not in args`
- 変更前 (`_forward_to_glab`): `"--repo" not in args and "-R" not in args`
- 変更後 (両者共通): `not _user_specified_repo(args)`（`_forward_to_gh` は `repo and not _user_specified_repo(args)`）

`_user_specified_repo` は引数列を走査し、EB の 5 形式いずれかに該当する要素が 1 つでもあれば True を返す内部 helper。module-private（先頭 `_`）。`_forward_to_gh` / `_forward_to_glab` の両方から呼び出す。

### 後方互換性

両者とも、既存の独立トークン形式 (`--repo owner/name` / `-R owner/name`) はそのまま検出され続けるため、後方互換。インライン形式は **これまで誤注入されていた** ので、修正後は config 由来 repo が injection されなくなる方向の振る舞い変更で、これはバグ修正としての意図そのもの。

## 変更スコープ

| ファイル | 変更内容 |
|---------|----------|
| `kaji_harness/cli_main.py` | `_user_specified_repo(args)` helper を追加し、`_forward_to_gh` (`:498`) と `_forward_to_glab` (`:1420`) の repo 判定を helper 経由に置換 |
| `tests/test_dispatcher.py` | `TestForwardToGhRepoInjection` に gh 側 3 形式の Medium 検出テストを追加。`_user_specified_repo` の Small 単体テスト群を追加 |
| `tests/test_dispatcher_gitlab.py` | glab 側 3 形式の Medium 検出テスト（gh 側と対称）を追加 |

`docs/` 変更は不要（docstring 内の小修正のみ実装フェーズで検討）。

## 方針（修正アプローチ）

最小侵襲方針:

1. module-private helper `_user_specified_repo(args: list[str]) -> bool` を追加
2. 走査ロジック:
   ```python
   def _user_specified_repo(args: list[str]) -> bool:
       for a in args:
           if a in ("--repo", "-R"):
               return True
           if a.startswith("--repo="):
               return True
           if a.startswith("-R") and len(a) > 2:
               # "-R=owner/name" / "-Rowner/name" の両方を含む
               return True
       return False
   ```
3. `_forward_to_gh` の if 条件を `if repo and not _user_specified_repo(args):` に置換
4. `_forward_to_glab` の if 条件を `if not _user_specified_repo(args):` に置換
5. 既存テスト（`test_github_provider_passthrough_injects_repo` / `test_user_repo_flag_takes_precedence` および GitLab 側の対応テスト）はそのまま green を維持

リファクタは混ぜない（cli_main.py 内の他箇所への一般化は本 Issue 対象外）。

### 補足: `-Rfoo` と `-R foo` の文字列上の区別

`-R` 単独トークン形式は `a == "-R"` で先に拾うため、`len(a) > 2` 条件で `-R=` / `-Rowner/name` のみ拾えば十分。`-R` 単独で来た場合は次のトークンが value（独立トークン形式）。

### エッジケース

- `args` に `--` (POSIX double-dash 区切り) が含まれる場合: `_forward_to_gh` / `_forward_to_glab` 冒頭で先頭 `--` のみ除去している（`cli_main.py:488` / `:1418`）。中間の `--` 以降は positional 扱いだが、`gh` / `glab` 側がこれをどう扱うかは pflag 仕様任せ。ガード対象は `--` 以前の flag 領域に限るべきだが、現状コードは全域走査している。本 Issue ではこの挙動を踏襲する（中間 `--` 以降に `--repo=...` を書くユースケースは想定外。発見次第別 Issue）。
- 空 args (`[]`): for ループが回らず False。既存挙動と一致（config 由来 repo が末尾追加される）。

## テスト戦略

### 変更タイプ
実行時コード変更（`_forward_to_gh` / `_forward_to_glab` のガード判定変更）

### 再現テスト（必須）

bug 設計ガイド (`design-by-type/bug.md` §8) に従い、修正前に Red になる回帰テストを追加する。

#### Small テスト

`_user_specified_repo` 単体を直接テストする Small ユニットを追加（純粋関数・外部依存なし）。

検証観点:

| ケース | 入力 args 例 | 期待戻り値 |
|--------|-------------|------------|
| 独立トークン (long) | `["pr", "list", "--repo", "o/n"]` | True |
| 独立トークン (short) | `["pr", "list", "-R", "o/n"]` | True |
| インライン代入 (long) | `["pr", "list", "--repo=o/n"]` | True |
| インライン代入 (short, `=` 付き) | `["pr", "list", "-R=o/n"]` | True |
| 短縮連結 (`=` なし) | `["pr", "list", "-Ro/n"]` | True |
| 未指定 | `["pr", "list"]` | False |
| `--repository` 等の他フラグ名 | `["--repository", "o/n"]` | False（注: `gh` / `glab` は `--repo` のみ受理。`--repository` は別フラグ扱い。誤検出しないことを確認） |

#### Medium テスト

**gh 側**: 既存の `TestForwardToGhRepoInjection`（`tests/test_dispatcher.py:788`、`subprocess.run` を patch して `_handle_issue` / `_handle_pr` 経路で `gh` 呼び出し引数を assert する Medium 結合テスト）に、以下 3 ケースを追加:

| 追加ケース | assert |
|-----------|--------|
| `_handle_issue(["view", "42", "--repo=user/explicit"])` | `--repo=user/explicit` がそのまま渡り、config 由来の追加 `--repo` が **存在しない** こと（`"--repo"` 単独 token が 0 件、`--repo=` で始まる token が 1 件） |
| `_handle_issue(["view", "42", "-R=user/explicit"])` | 同様。`-R=user/explicit` が 1 件、`-R` 単独 token と `--repo` 単独 token が 0 件 |
| `_handle_issue(["view", "42", "-Ruser/explicit"])` | 同様。`-Ruser/explicit` が 1 件、`-R` / `--repo` 単独 token が 0 件 |

**glab 側**: `tests/test_dispatcher_gitlab.py` に gh 側と対称の 3 ケースを追加:

| 追加ケース | assert |
|-----------|--------|
| `_handle_issue(["view", "42", "--repo=group/explicit"])`（`provider.type='gitlab'` + `[provider.gitlab] repo = "group/project"`） | `--repo=group/explicit` が 1 件、config 由来 `--repo` 単独 token が 0 件 |
| `_handle_issue(["view", "42", "-R=group/explicit"])` | 同様 |
| `_handle_issue(["view", "42", "-Rgroup/explicit"])` | 同様 |

> assert ヘルパー: 「`cmd.count("--repo")` が常に独立トークンしかカウントしない」点に注意し、`sum(1 for c in cmd if c.startswith("--repo"))` / `sum(1 for c in cmd if c.startswith("-R"))` 型の prefix 集計で検証する。

既存の独立トークン形式テスト (`test_user_repo_flag_takes_precedence` および GitLab 側対応テスト) は無変更で残し、後方互換を担保する。

#### Large テスト

不要。理由（`docs/dev/testing-convention.md` の Large 観点判断）:

- 修正範囲は `_forward_to_gh` / `_forward_to_glab` の **argv 生成責務** 単体に閉じる。Small（純粋関数）+ Medium（dispatcher 結合）で「kaji が正しい argv を生成する」ことを完全に検証できる
- 「`gh` / `glab` 側が `--repo` 重複を実際にどう扱うか」は pflag および各 CLI の責務であり kaji の責務外。実 API 疎通で確認すべき kaji 側の振る舞いは存在しない
- 実 GitHub / GitLab API 疎通の Large レイヤ（`large_forge` / `large_gitlab`）は本 Issue の bug 検出シグナルを増やさない（Small/Medium で同じ回帰を捕捉できるため）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定の変更なし（pflag 仕様の追従に過ぎない） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ層の変更なし |
| `docs/dev/` | なし | 開発ワークフロー変更なし |
| `docs/reference/` | なし | 公開 API 仕様変更なし |
| `docs/cli-guides/` | なし | `kaji issue` / `kaji pr` の **CLI 仕様は不変**。docstring に書かれた契約を遵守させるバグ修正であり、ユーザ向け仕様変更ではない |
| `CLAUDE.md` | なし | 規約変更なし |

`kaji_harness/cli_main.py:474` / `:1405` 周辺の docstring に「インライン形式含む」旨を明示する小修正を実装フェーズで検討する（必須ではない）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| spf13/pflag README "Command line flag syntax" | https://github.com/spf13/pflag#command-line-flag-syntax | pflag は GNU long インライン (`--flag=x`)、POSIX short 独立 (`-f x`)、short インライン (`-f=x`)、short 連結 (`-fx`) の 4 形式を受理する。本節に `--flag=x` / `-n 1234` / `-n=1234` / `-n1234` の例が明示されている |
| GitHub CLI `cli/cli` go.mod | https://github.com/cli/cli/blob/trunk/go.mod | `github.com/spf13/cobra` / `github.com/spf13/pflag` 依存が記載されており、`gh` が pflag 系 parser を使用する一次根拠 |
| GitLab CLI `gitlab-org/cli` go.mod | https://gitlab.com/gitlab-org/cli/-/blob/main/go.mod | 同じく `github.com/spf13/cobra` / `github.com/spf13/pflag` 依存が記載されており、`glab` が pflag 系 parser を使用する一次根拠 |
| GitHub CLI manual — `gh pr list` | https://cli.github.com/manual/gh_pr_list | `-R, --repo <[HOST/]OWNER/REPO>` は inherited / global flag。short `-R` と long `--repo` の両形式を CLI として公開しており、pflag の各形式が利用可能 |
| `_forward_to_gh` docstring | `kaji_harness/cli_main.py:464-477`（issue commit 時点） | 「既に user が `--repo` を渡している場合は user 値を尊重し触らない」契約 — 本 Issue で守るべき仕様の source of truth (gh 側) |
| `_forward_to_glab` docstring | `kaji_harness/cli_main.py:1399-1407`（issue commit 時点） | 「user が `--repo` を渡している場合は user 値を尊重して触らない（`_forward_to_gh` と同方針）」契約 — 同 source of truth (glab 側) |
| 発見元 PR | GitHub PR #170（kamo/kaji の Codex 自動レビュー P2 指摘） | バグ発見の出所。Codex が `"--repo" not in args` のリテラル一致の脆弱性を指摘 |
| 既存 Medium テスト (gh) | `tests/test_dispatcher.py:783-868`（`TestForwardToGhRepoInjection`） | 後方互換を担保すべき既存検証群（gh 側）。本 Issue では追加ケースをこのクラスに同居させる |
| 既存 Medium テスト (glab) | `tests/test_dispatcher_gitlab.py`（`_forward_to_glab` 経路） | 後方互換を担保すべき既存検証群（glab 側）。本 Issue では同ファイルに対称ケースを追加 |
