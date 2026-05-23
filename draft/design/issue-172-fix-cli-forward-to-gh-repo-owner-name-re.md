# [設計] `_forward_to_gh` の `--repo` インライン形式検出修正

Issue: #172

## 概要

`_forward_to_gh` (`kaji_harness/cli_main.py`) の repo 注入ガードを、`--repo` / `-R` の **独立トークン形式** だけでなく **インライン代入形式**（`--repo=owner/name` / `-R=owner/name` / `-Rowner/name`）も検出するよう修正し、docstring に明記された「user `--repo` wins」契約を全形式で守る。

## 背景・目的

### Observed Behavior (OB)

`kaji_harness/cli_main.py:498`（PR #170 時点 commit `f492b7d`）:

```python
if repo and "--repo" not in args and "-R" not in args:
    # gh は --repo を sub の前後どちらでも受理する。末尾追加で副作用最小
    args = [*args, "--repo", repo]
```

`"--repo" not in args` は文字列 `--repo` の完全一致トークンのみ検出する。`args` に `--repo=apokamo/other-repo` が含まれていても、要素として文字列 `--repo` 自体は存在しないため条件が真になり、`["pr", "list", "--repo=apokamo/other-repo", "--repo", "kamo/kaji"]` が生成され、`gh` 側に `--repo` が 2 回渡る。

`-R=owner/name` / `-Rowner/name` 形式も同様に取りこぼす。

### Expected Behavior (EB)

`_forward_to_gh` の docstring（`kaji_harness/cli_main.py:475`）:

> 既に user が `--repo` を渡している場合は user 値を尊重し触らない

この契約は argv トークン形式に依存しない。ユーザが以下のいずれかの形式で `--repo` / `-R` を渡した場合、config 由来 repo の注入をスキップする:

| 形式 | 例 |
|------|-----|
| 独立トークン (long) | `--repo owner/name` |
| 独立トークン (short) | `-R owner/name` |
| インライン代入 (long) | `--repo=owner/name` |
| インライン代入 (short, `=` 付き) | `-R=owner/name` |
| 短縮連結 (`=` なし) | `-Rowner/name` |

`gh` CLI は内部で cobra/pflag を使用しており、pflag は POSIX 短縮形式と GNU long インライン形式の両方を受理する（参照: 「参照情報」§ pflag）。よって、ユーザは上記すべての形式を等価に使える前提に立ち、kaji 側のガードもそれに揃える必要がある。

### Bettenburg et al. (2008) — OB + EB + steps-to-reproduce

OB と EB を分離し、再現手順は次節で固定する。

## 再現手順

1. `provider.type='github'` かつ `[provider.github] repo = "kamo/kaji"` が設定された config を用意
2. `kaji pr list --repo=apokamo/other-repo` を実行
3. 観測: `_forward_to_gh` が `--repo kamo/kaji`（config 値）を末尾に追加し、subprocess には
   `gh pr list --repo=apokamo/other-repo --repo kamo/kaji` が渡る
4. `gh` 側で「`--repo` が 2 回指定された」エラーになる、または first-wins / last-wins のいずれかで意図しないリポジトリ操作になる（pflag 動作。kaji 視点では仕様未定義扱い）

短縮形（`-R=...` / `-Rapokamo/other-repo`）も同様に再現する。

## 根本原因 (Root Cause)

### なぜ間違っているか

`"--repo" not in args` / `"-R" not in args` は **list element equality** による完全一致判定。argv の構造が「flag と value が独立トークン」前提でしか機能しない。argparse / pflag が許容する `--flag=value` 形式・短縮連結形式は、要素文字列が `--repo=owner/name` のように「flag 名そのもの」と異なるため、ガードが false negative になる。

ガードに必要な意味的判定は「ユーザが意図的に repo を指定したか」であり、「特定の文字列リテラルが argv list に含まれるか」ではない。両者がたまたま独立トークン形式でだけ一致していたのが原因。

### いつから壊れているか

PR #170 (GitHub #169) で `[provider.github] repo` を `--repo` で gh に伝搬する機能を追加した時点から（`kaji_harness/cli_main.py:498` 周辺）。Codex 自動レビューが P2 として指摘し、本 Issue で fix する。

### 同じ原因で他にも壊れている箇所がないか

同様の「`X not in args`」パターンで argv flag を検出している箇所がないか、`kaji_harness/` 配下を grep する（実装フェーズ序盤に確認）。現時点で観測されている同種の bug は `_forward_to_gh` のみだが、もし他にあれば設計を補完するか、別 Issue として切り出すかを判断する。

`_FORGE_METHOD_FLAGS` の `a not in _FORGE_METHOD_FLAGS` フィルタ（`cli_main.py:495`）も同形式だが、こちらは `--merge` / `--squash` / `--rebase` という **値を取らない bool フラグ** が対象であり、`--merge=true` のような形式は gh の `pr merge` で意味を持たない（pflag では boolean は `--merge` 単独で true）。よってインライン形式の取りこぼしは実害なし。本 Issue のスコープ外。

## インターフェース

`_forward_to_gh(group: str, raw_args: list[str], *, repo: str | None = None) -> int` の **signature は変更しない**。

振る舞いの変更点は内部判定のみ:

- 変更前: `repo and "--repo" not in args and "-R" not in args`
- 変更後: `repo and not _user_specified_repo(args)`（仮称。実装フェーズで命名確定）

`_user_specified_repo` は引数列を走査し、上記 EB の 5 形式いずれかに該当する要素が 1 つでもあれば True を返す内部 helper。module-private（先頭 `_`）。

### 後方互換性

既存の独立トークン形式 (`--repo owner/name` / `-R owner/name`) はそのまま検出され続けるため、後方互換。インライン形式は **これまで誤注入されていた** ので、修正後は config 由来 repo が injection されなくなる方向の振る舞い変更で、これはバグ修正としての意図そのもの。

## 変更スコープ

| ファイル | 変更内容 |
|---------|----------|
| `kaji_harness/cli_main.py` | `_forward_to_gh` 内の repo 判定を helper 経由に置換。helper 定義を同モジュールに追加 |
| `tests/test_dispatcher.py` | `TestForwardToGhRepoInjection` クラスに 4 形式の検出テストを追加（後述「テスト戦略」） |

`docs/` 変更は不要（docstring 内のみ更新する可能性あり）。

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
4. 既存テスト 2 本（`test_github_provider_passthrough_injects_repo` / `test_user_repo_flag_takes_precedence`）はそのまま green を維持

リファクタは混ぜない（cli_main.py 内の他箇所への一般化は本 Issue 対象外）。

### 補足: `-Rfoo` と `-R foo` の文字列上の区別

`-R` 単独トークン形式は `a == "-R"` で先に拾うため、`len(a) > 2` 条件で `-R=` / `-Rowner/name` のみ拾えば十分。`-R` 単独で来た場合は次のトークンが value（独立トークン形式）。

### エッジケース

- `args` に `--` (POSIX double-dash 区切り) が含まれる場合: `_forward_to_gh` 冒頭で先頭 `--` のみ除去している（`cli_main.py:488`）。中間の `--` 以降は positional 扱いだが、`gh` 側がこれをどう扱うかは pflag 仕様任せ。ガード対象は `--` 以前の flag 領域に限るべきだが、現状コードは全域走査している。本 Issue ではこの挙動を踏襲する（中間 `--` 以降に `--repo=...` を書くユースケースは想定外。発見次第別 Issue）。
- 空 args (`[]`): for ループが回らず False。既存挙動と一致（config 由来 repo が末尾追加される）。

## テスト戦略

### 変更タイプ
実行時コード変更（`_forward_to_gh` のガード判定変更）

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
| `--repository` 等の他フラグ名 | `["--repository", "o/n"]` | False（注: `gh` は `--repo` のみ受理。`--repository` は別フラグ扱い。誤検出しないことを確認） |

#### Medium テスト

既存の `TestForwardToGhRepoInjection`（`tests/test_dispatcher.py:788`、`subprocess.run` を patch して `_handle_issue` / `_handle_pr` 経路で `gh` 呼び出し引数を assert する Medium 結合テスト）に、以下 3 ケースを追加:

| 追加ケース | assert |
|-----------|--------|
| `_handle_issue(["view", "42", "--repo=user/explicit"])` | `cmd.count("--repo")` で重複検出 → `--repo=user/explicit` がそのまま渡り、config 由来の追加 `--repo` が **存在しない** こと（`"--repo"` 単独 token が 0 件、`--repo=` で始まる token が 1 件） |
| `_handle_issue(["view", "42", "-R=user/explicit"])` | 同様。`-R=user/explicit` が 1 件、`-R` 単独 token と `--repo` 単独 token が 0 件 |
| `_handle_issue(["view", "42", "-Ruser/explicit"])` | 同様。`-Ruser/explicit` が 1 件、`-R` / `--repo` 単独 token が 0 件 |

> assert ヘルパー: 「`cmd.count("--repo")` が常に独立トークンしかカウントしない」点に注意し、`sum(1 for c in cmd if c.startswith("--repo"))` / `sum(1 for c in cmd if c.startswith("-R"))` 型の prefix 集計で検証する。

既存の独立トークン形式テスト (`test_user_repo_flag_takes_precedence`) は無変更で残し、後方互換を担保する。

#### Large テスト

不要。理由:

- 修正範囲が `_forward_to_gh` の argv 判定ロジック単体で、外部 API（gh / GitHub REST）の振る舞い変更を伴わない
- 「`gh` 側が `--repo` 重複を実際にどう扱うか」は pflag の挙動であり kaji の責務外
- Small + Medium で「kaji が正しい argv を生成する」ことを完全に検証可能

`docs/dev/testing-convention.md` の Large 省略 4 条件（独自ロジック追加なし / 既存ゲートで捕捉 / 新規回帰検出情報が増えない / 説明可能）すべてに該当。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定の変更なし（pflag 仕様の追従に過ぎない） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ層の変更なし |
| `docs/dev/` | なし | 開発ワークフロー変更なし |
| `docs/reference/` | なし | 公開 API 仕様変更なし |
| `docs/cli-guides/` | なし | `kaji issue` / `kaji pr` の **CLI 仕様は不変**。docstring に書かれた契約を遵守させるバグ修正であり、ユーザ向け仕様変更ではない |
| `CLAUDE.md` | なし | 規約変更なし |

`kaji_harness/cli_main.py:474` 周辺の docstring に「インライン形式含む」旨を明示する小修正を実装フェーズで検討する（必須ではない）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| spf13/pflag README（GoDoc / GitHub） | https://github.com/spf13/pflag#setting-no-option-default-values-for-flags | `gh` が内部で使う Go の flag ライブラリ pflag は GNU long インライン (`--flag=x`)、POSIX short 独立 (`-f x`)、short インライン (`-f=x`)、short 連結 (`-fx`) の 4 形式を受理する。README "Command line flag syntax" 節および同リポジトリの `flag.go` における `parseLongArg` / `parseShortArg` の実装に対応 |
| GitHub CLI manual — `gh pr list` | https://cli.github.com/manual/gh_pr_list | `-R, --repo <[HOST/]OWNER/REPO>` は global flag。short `-R` と long `--repo` の両形式を CLI として公開しており、pflag の各形式が利用可能 |
| `_forward_to_gh` docstring | `kaji_harness/cli_main.py:464-477`（issue commit 時点） | 「既に user が `--repo` を渡している場合は user 値を尊重し触らない」契約 — 本 Issue で守るべき仕様の source of truth |
| 発見元 PR | GitHub PR #170（kamo/kaji の Codex 自動レビュー P2 指摘） | バグ発見の出所。Codex が `"--repo" not in args` のリテラル一致の脆弱性を指摘 |
| 既存 Medium テスト | `tests/test_dispatcher.py:783-868`（`TestForwardToGhRepoInjection`） | 後方互換を担保すべき既存検証群。本 Issue では追加ケースをこのクラスに同居させる |
