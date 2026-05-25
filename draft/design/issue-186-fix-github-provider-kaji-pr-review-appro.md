# [設計] GitHub provider の `kaji pr review --approve` を self-PR でも成立させる（marker comment fallback）

Issue: #186

## 概要

GitHub provider 配下の `kaji pr review <pr> --approve|--request-changes` を、PR author 本人が実行しても rc=0 を返すよう拡張する。`gh pr review` は GitHub API 制約により self-PR では失敗するため、GitLab provider と同じ `<!-- kaji-review: state=... -->` marker 付き comment を投稿することで approve / changes-requested シグナルを表現する。

## 背景・目的

### Observed Behavior (OB)

`provider.type = "github"` 構成で `kaji pr review <pr> --approve` を PR author（= `gh auth` ユーザ）が実行すると、`gh pr review --approve` が GitHub API から拒否されてプロセスが exit 1 となる。

実発生ログ（PR #185 / Issue #182、2026-05-25）:

```
$ kaji pr review 185 --approve --body-file <(...)
GraphQL: Review Can not approve your own pull request (addPullRequestReview)
$ echo $?
1
```

その結果 `kaji run .kaji/wf/review-close.yaml 182` の `pr-verify` step は ABORT verdict を返し、`pr-verify → close` 遷移に進まない。`close` (= `/issue-close`) が実行されないため、PR merge / worktree 削除 / branch 削除が手動運用に逆戻りする。

呼び出し経路は `kaji_harness/cli_main.py:770-821` の `_handle_pr`: GitHub mode では `review` を含む任意の sub を `gh pr <args>` へ素通しする。intermediate handler は無く、self-PR でも検証無しで `gh pr review --approve` が叩かれる。

### Expected Behavior (EB)

`kaji pr review <pr> --approve` は provider に関係なく rc=0 を返し、後続 skill / workflow から「approve シグナルが残った」と観測可能であるべき。GitLab provider 側は既に同等の動作を marker comment 機構で実現している:

- `kaji_harness/cli_main.py:1899-1953` (`_gitlab_pr_review`) … `glab mr note --message <marker+body>` → `glab mr approve` / 条件付き `revoke`
- `kaji_harness/providers/gitlab.py:613-633` (`build_kaji_review_marker`) … `<!-- kaji-review: state=APPROVED -->` 等の不可視 marker を生成
- `kaji_harness/providers/gitlab.py:636-649` (`_parse_kaji_review_marker`) … note body 先頭行から state を逆引き
- 既存テスト: `tests/test_dispatcher_gitlab.py:607` / `tests/test_providers_gitlab.py:781-894` で `APPROVED` / `CHANGES_REQUESTED` / `COMMENTED` の round-trip を assert

GitHub 側でも同じ marker を用い、self-PR 検知時には `gh pr review` 呼び出しを skip して marker 付き comment 投稿のみで成功扱いに切り替える。非 self-PR 時は従来通り `gh pr review --approve|--request-changes` を実行し既存挙動を維持する。これにより:

- `pr-verify` skill (`.claude/skills/pr-verify/SKILL.md:218`) は PASS 経路（`kaji pr review --approve`）の rc=0 だけを依存条件として扱えば良く、skill 側に provider 分岐や self-PR 分岐を持ち込まない
- `.kaji/wf/review-close.yaml:49-57` の `pr-verify → close` 遷移が author==reviewer な構成でも閉じる

### 再現手順 (Steps to Reproduce)

1. 前提環境:
   - `.kaji/config.toml` で `[provider] type = "github"`、`[provider.github] repo = "owner/name"` が解決済み
   - `gh auth status` の login が当該 PR の author と一致（典型: 単独開発リポジトリ）
2. 任意の Issue で worktree + commit + PR を作成（`kaji issue start` → `kaji pr create` 等）
3. `kaji run .kaji/wf/review-close.yaml <issue_id>` を実行
4. `pr-fix` PASS 後、`pr-verify` step が `kaji pr review <pr_id> --approve --body-file -` を呼び出した時点で `GraphQL: Review Can not approve your own pull request` が stderr に出力され、rc=1 → ABORT verdict が発行され `close` step に進まない

実発生事例: PR #185 / Issue #182 (2026-05-25)

### Root Cause

`kaji_harness/cli_main.py:770-821` の `_handle_pr` は GitHub mode で `review-comments` / `reviews` / `reply-to-comment` の 3 つを builtin 化するのみで、`review` / `create` / `view` / `list` / `comment` / `merge` 等は `_forward_to_gh("pr", raw_args, repo=repo_override)` で `gh pr <sub>` に素通しする。`review` は GitHub API `POST /repos/{owner}/{repo}/pulls/{N}/reviews` を叩く `gh` 側ロジックに到達し、GitHub API は author による `APPROVE` / `REQUEST_CHANGES` イベントを 422 で拒否する（公式仕様: 後述「参照情報」§ 3）。

GitLab provider 側は同じ制約を回避するため Phase で marker comment + 別 API（`approve` / `revoke`）に分けて構造化済みだが、GitHub provider 側は phase 2 時点でこの fallback が未実装のまま、`pr-verify` skill / `review-close.yaml` の PASS 条件と非互換になっていた。

同根の他壊れ箇所:

- `kaji pr review --request-changes` も同じ理由で self-PR では `gh pr review --request-changes` が拒否される（GitHub API は author による `REQUEST_CHANGES` も拒否）。よって fallback は `--approve` 単独ではなく `--request-changes` も対称に処理する
- `kaji pr review --comment` は GitHub API が author の `COMMENT` イベントは許容するため壊れていない。本 Issue の scope 外。fallback 対称性も不要

## インターフェース

### 入力（変更なし、契約のみ明示）

```
kaji pr review <pr_id> --approve [--body BODY | --body-file PATH]
kaji pr review <pr_id> --request-changes [--body BODY | --body-file PATH]
```

- `<pr_id>`: ASCII decimal の PR 番号（既存制約と同じ）
- `--approve` と `--request-changes` は相互排他
- どちらも未指定なら `EXIT_INVALID_INPUT` (既存挙動と同じ。GitLab dispatcher が argparse で拒否しているのと対称に GitHub dispatcher でも明示)
- body 解決は GitLab dispatcher と同じく `_read_body_arg(ns.body, ns.body_file)` を流用

### 出力（変更点）

| ケース | 既存挙動 (GitHub mode) | 修正後 |
|--------|----------------------|--------|
| 非 self-PR で `--approve` | `gh pr review --approve` 委譲 → rc=0 | **不変**（`gh pr review --approve` 委譲 → rc=0） |
| Self-PR で `--approve` | `gh pr review --approve` 委譲 → rc=1 (`Can not approve your own pull request`) | `gh api ... POST issues/<N>/comments` で marker 付き comment を投稿 → rc=0。`gh pr review` は呼ばない |
| 非 self-PR で `--request-changes` | `gh pr review --request-changes` 委譲 → rc=0 | **不変**（`gh pr review --request-changes` 委譲 → rc=0） |
| Self-PR で `--request-changes` | 同 rc=1 | marker (`CHANGES_REQUESTED`) 付き comment 投稿 → rc=0。`gh pr review` は呼ばない |

side effect として self-PR 経路では Issue/PR 上に以下の comment が 1 件追加される（GitLab side と完全に同形式）:

```
<!-- kaji-review: state=APPROVED -->
<user-supplied body, 空可>
```

`<!-- ... -->` HTML コメントは GitHub UI 上で不可視のため、PR 体験を壊さない。

### `kaji pr reviews <pr_id>` (GitHub passthrough) との関係（非変更スコープ）

GitHub mode の `kaji pr reviews` は `cli_main.py:629-643` (`_forward_pr_reviews`) で `gh api repos/<repo>/pulls/<N>/reviews` を直接 forward する。marker comment は issue comments API 側（`/issues/<N>/comments`）に投稿されるため、`kaji pr reviews` の出力には **現れない**。

これは GitLab provider 側で `list_pr_reviews` が notes + approvals を join して GitHub 互換 list を合成しているのと**非対称**だが、本 Issue では以下の理由から `kaji pr reviews` の forward 形式は **変更しない**:

- `pr-verify` / `pr-fix` skill (`.claude/skills/pr-verify/SKILL.md:153` / `.claude/skills/pr-fix/SKILL.md:140`) は `kaji pr reviews` を「前回の review body 一覧」の入力として使うのみで、`state == "APPROVED"` を PASS 判定の source of truth にしていない（PASS 判定は `kaji pr review --approve` の rc=0 が source）
- `.kaji/wf/review-close.yaml` も `pr-verify` step の verdict にのみ依存し `kaji pr reviews` を直接読まない
- `kaji pr reviews` 出力に marker comment を「合成 review entry」として混ぜ込むと GitHub native review との区別が必要になり、shape 拡張範囲が広がる（GitLab 側で実装した `_GitLabPrShape.to_github_reviews` 相当の synthesizer を GitHub 側でも導入することになる）

`kaji pr reviews` 合成は本 Issue では明示的に scope 外。skill / workflow 非対称が顕在化した時点で別 Issue を切る判断を採る。

### 使用例

```bash
# author 本人による review (CI / 単独開発 / review-close.yaml 経路から呼ばれる代表ケース)
kaji pr review 185 --approve --body-file - <<'EOF'
## PR レビュー修正確認結果
（pr-verify skill が出す表 etc.）
EOF
# → rc=0
# → issue comments API に `<!-- kaji-review: state=APPROVED -->` + 本文 が 1 件 POST される
# → `gh pr review` は呼ばれない

# 第三者 reviewer (従来挙動)
kaji pr review 185 --approve --body-file - <<'EOF'
LGTM
EOF
# → `gh pr review --approve --body-file -` を委譲、rc=0
```

## 制約・前提条件

- 修正対象は GitHub provider 経路 (`kaji_harness/cli_main.py` の `_handle_pr` GitHub 分岐) のみ。GitLab / Local provider 経路は無変更
- `build_kaji_review_marker` / `_KAJI_REVIEW_MARKER_PREFIX` / `_REVIEW_STATES_VALID` は `kaji_harness/providers/gitlab.py:613-633` に既に存在。本 Issue では **`gitlab.py` から `providers/_review_marker.py` 等の provider 中立モジュールへ移管せず**、`cli_main.py` の既存 import (`cli_main.py:39`) を流用して GitHub dispatcher 側でも参照する。理由: 関数自体は provider 非依存だが、移管はファイル境界を新設する Scope 拡張になるため別 Issue 推奨（参照: bug.md § 「リファクタ混在を避ける」）
- self-PR 判定は `gh api user --jq .login` (authenticated user) と `gh pr view <pr> --json author --jq .author.login` (PR author) を比較する。両 API は repo 権限以外の追加 scope を要求しない
- marker comment 投稿は `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<text>` を使う（PR の「会話」コメントは Issue comments API に統一されている GitHub 仕様）。`gh pr comment <pr> --body <text>` でも等価だが、`_handle_pr` の他 forward 経路が `gh api` ベースで統一されているのに合わせる
- 本変更は `kaji pr review --approve` / `--request-changes` の rc semantics のみ変更する。stdout 出力は marker comment 投稿レスポンスを抑止し（既存 `_forward_pr_api_list` パターンに準拠して標準出力に GitHub API レスポンスを流す既定との互換性は GitLab dispatcher と同じく "rc のみ意味があり stdout は best-effort" とする）
- `--body` / `--body-file` 同時指定は既存 `_read_body_arg` 経由で `ValueError` → `EXIT_INVALID_INPUT` 化する（GitLab dispatcher と同じ挙動）
- 単独開発前提（author == authenticated user）を主シナリオに据えるが、CI から bot が approve するケースとの両立も維持する（authenticated user ≠ PR author → 従来 `gh pr review` 委譲）

## 変更スコープ

- `kaji_harness/cli_main.py`:
  - `_handle_pr` (GitHub 分岐) で `args[0] == "review"` のとき新規 `_github_pr_review` に dispatch する
  - 新規 `_github_pr_review(rest, *, repo_override)` を追加（GitLab 側 `_gitlab_pr_review` と対称な構造、ただし provider 引数ではなく `repo_override` を取る形）
  - 補助関数: `_resolve_pr_author(pr_id, *, repo)` / `_resolve_authenticated_user()` を追加（または `_github_pr_review` 内 inline。後者を選ぶ）
- `tests/`:
  - `tests/test_dispatcher_github.py` 等の既存 GitHub dispatcher test ファイルに self-PR / 非 self-PR の review ケースを追加
- `docs/cli-guides/github-mode.md:87`: `review` sub の self-PR 挙動を追記
- GitLab provider / Local provider / skill / workflow YAML: **無改修**

GitLab / Local 経路、`pr-verify` skill、`.kaji/wf/review-close.yaml` / `review-cycle.yaml` は変更しない。

## 方針

### 1. dispatcher 分岐

`_handle_pr` 内、GitHub 分岐の末尾（`_PR_BUILTIN_SUBCOMMANDS` チェックの直後）に `review` 専用分岐を追加:

```python
# _handle_pr (GitHub 分岐) 末尾の置換イメージ
if args and args[0] == "review":
    return _github_pr_review(args[1:], repo_override=repo_override)
if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
    return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
return _forward_to_gh("pr", raw_args, repo=repo_override)
```

`_PR_BUILTIN_SUBCOMMANDS` に `review` を追加しない理由: 既存 builtin は read-only な `gh api .../<suffix>` forward が共通形だが、`review` は marker 投稿 + 条件付き approve forward と構造が異なるため、`_dispatch_pr_builtin` の argparse 形に乗らない。

### 2. `_github_pr_review` の構造（疑似コード）

```python
def _github_pr_review(rest: list[str], *, repo_override: str | None) -> int:
    # 2.1 argparse （GitLab 側と同じ shape）
    p = argparse.ArgumentParser(prog="kaji pr review", add_help=True)
    p.add_argument("pr_id", type=str)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--request-changes", dest="request_changes", action="store_true")
    p.add_argument("--body", default=None)
    p.add_argument("--body-file", dest="body_file", default=None)
    ns = p.parse_args(rest)

    # 2.2 排他チェック + body 解決（GitLab 側と同じ）
    if ns.approve and ns.request_changes: → EXIT_INVALID_INPUT
    if not (ns.approve or ns.request_changes): → EXIT_INVALID_INPUT
    if not _is_ascii_decimal(ns.pr_id): → EXIT_INVALID_INPUT
    body = _read_body_arg(ns.body, ns.body_file) or ""

    # 2.3 gh CLI 存在チェック + repo 解決
    if shutil.which("gh") is None: → _GH_MISSING_GUIDANCE / EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=repo_override)
    if repo is None: → 既存 error message / EXIT_RUNTIME_ERROR

    # 2.4 self-PR 判定
    pr_author = _gh_json_field(["pr", "view", ns.pr_id, "--repo", repo,
                                "--json", "author", "--jq", ".author.login"])
    me = _gh_json_field(["api", "user", "--jq", ".login"])
    is_self = (pr_author is not None and me is not None and pr_author == me)

    # 2.5 state + marker
    state = "APPROVED" if ns.approve else "CHANGES_REQUESTED"
    marker = build_kaji_review_marker(state)
    marked_body = f"{marker}\n{body}"

    if is_self:
        # 2.6a marker comment 投稿のみで成功扱い
        return _gh_post_issue_comment(repo=repo, pr_id=ns.pr_id, body=marked_body)
    # 2.6b 非 self-PR: 従来通り gh pr review に委譲（marker は付けない）
    gh_args = ["pr", "review", ns.pr_id, "--repo", repo,
               "--approve" if ns.approve else "--request-changes"]
    if body:
        gh_args.extend(["--body", body])
    return _forward_to_gh_with_args(gh_args)
```

### 3. self-PR 判定の補助関数

GitHub 側に inline で実装する。GitLab 側 `_gitlab_pr_review` が `provider.get_mr_approval_state(iid)` を呼んでいるのとは違い、GitHub 側は `gh` CLI 経由で十分（`GitHubProvider` のメソッドにせず `cli_main` 側 inline）。理由:

- self-PR 判定は本 dispatcher 以外で再利用見込みがない
- provider class の責務を最小限に保つ（既存 `GitHubProvider` は issue 中心の CRUD で、`pr review` 用 API は持っていない）
- `gh pr view --json author -q .author.login` / `gh api user --jq .login` は既存ヘルパ `_run_gh` 系列で叩ける形

ただし `_gh_json_field` のような 1 値抽出ヘルパが既存に無ければ、`subprocess.run` を直接呼んで `stdout.strip()` で取る最小実装に留める（既存 `_detect_repo` (`cli_main.py:568-590`) と同じパターン）。新規 provider メソッド追加はしない。

### 4. 失敗ハンドリング

| 失敗箇所 | 動作 |
|---------|------|
| `gh` 未インストール | 既存 `_GH_MISSING_GUIDANCE` を stderr → `EXIT_RUNTIME_ERROR` |
| `gh pr view --json author` が rc≠0 | stderr に gh の出力を中継、`EXIT_RUNTIME_ERROR` |
| `gh api user --jq .login` が rc≠0 | 同上 |
| Self-PR 経路の `gh api POST issues/<N>/comments` が rc≠0 | stderr 中継、`EXIT_RUNTIME_ERROR`（marker 投稿失敗は approve 不成立として扱う） |
| 非 self-PR 経路の `gh pr review` が rc≠0 | 既存挙動と同じく rc をそのまま返す |

self-PR 判定で「どちらかの取得に失敗 → 安全側に倒して non-self として扱い `gh pr review` 委譲」のような silent fallthrough は採用しない。author / authenticated user 取得失敗は dispatcher 失敗として明示的に伝える（fail-loud）。

### 5. 既存テストとの整合

- 既存 GitLab dispatcher test (`tests/test_dispatcher_gitlab.py`) には触れない
- 既存 GitHub dispatcher で `kaji pr review` が `gh pr review` に素通しされる前提のテストがあれば、self-PR 否定モック（authenticated user ≠ PR author）下で通る形に修正する。素通しが「PR author 取得 → 異なる → forward」と 2 段になるため、`_run_gh` / `subprocess.run` mock 設定の調整が必要

## テスト戦略

### 変更タイプ
- 実行時コード変更（GitHub provider dispatcher 経路の挙動を変更）

### 実行時コード変更の場合

#### Small テスト

`@pytest.mark.small` で `_github_pr_review` 関数を直接 / `_handle_pr` 経由で叩き、`subprocess.run` / `shutil.which` / `KajiConfig.discover` を mock 化する単体テスト。検証観点:

- **bug 再現テスト（必須・Red 化）**: `kaji pr review <pr> --approve` 実行時に PR author == authenticated user である mock 状態で:
  - 修正前: `gh pr review --approve` が rc=1 (`Can not approve your own pull request` を stderr に流す mock) → `_handle_pr` rc=1
  - 修正後: `gh pr review` は呼ばれず、`gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<marker+body>` が 1 回呼ばれ、`_handle_pr` rc=0
  - assert: 投稿 body の先頭行が `build_kaji_review_marker("APPROVED")` と一致
- **`--request-changes` 対称ケース**: 同じく self-PR で marker `CHANGES_REQUESTED` 付き comment 投稿のみで rc=0。`gh pr review --request-changes` が呼ばれないこと
- **非 self-PR `--approve` 回帰防止**: authenticated user ≠ PR author の mock で `gh pr review --approve` が委譲され rc=0。marker comment 投稿が **行われない** こと（POST issues/<N>/comments の subprocess 呼び出しが 0 回）
- **非 self-PR `--request-changes` 回帰防止**: 同上、`gh pr review --request-changes` 委譲
- **入力検証**: `--approve` と `--request-changes` 同時指定 → `EXIT_INVALID_INPUT`、両方未指定 → `EXIT_INVALID_INPUT`、`<pr_id>` 非 ASCII decimal → `EXIT_INVALID_INPUT`、`--body` と `--body-file` 同時指定 → `EXIT_INVALID_INPUT`
- **空 body**: `--body` も `--body-file` も未指定の self-PR `--approve` で marker のみの body (`"<marker>\n"`) が投稿されること
- **失敗ハンドリング**: `gh pr view --json author` rc≠0 / `gh api user` rc≠0 / `gh api POST .../comments` rc≠0 がそれぞれ `EXIT_RUNTIME_ERROR` に変換され silent fallthrough しないこと

`subprocess.run` patch スコープは [testing-convention.md § `subprocess.run` patch スコープ](../../docs/dev/testing-convention.md) に従い、`_handle_pr` 経路では名前空間 patch を避け、`cli_main.subprocess.run` 単体 mock + `KajiConfig.discover` / `get_provider` の return mock を組み合わせる（既存 `tests/test_dispatcher_github.py` パターンに準拠）。

#### Medium テスト

不要。理由:

- `make verify-packaging` 系の packaging-only 検証は対象外（コード変更）
- ファイル I/O / DB 結合は本変更に含まれない（subprocess を介す `gh` 呼び出しは Small で `subprocess.run` を mock 化して観測する設計）
- worktree / config discovery の medium 検証は既存 `tests/test_dispatcher_github.py` が `_handle_pr` 全体で既にカバー済みで、本 Issue の差分は分岐 1 つの追加のため Medium に独自観点が増えない
- testing-convention.md § 「不要理由」§ 4 条件: ① 独自ロジックは Small で完結 ② 想定不具合パターンは Small mock + 既存 GitHub mode integration test で捕捉 ③ Medium を増やしても回帰検出情報はほぼ増えない（subprocess mock を実 process に置き換えるだけ） ④ 不要理由を本セクションで説明 — を満たす

#### Large テスト

不要。理由:

- 「self-PR で GitHub API が `APPROVE` を拒否する」仕様は GitHub 側の固定値であり、kaji 側が観測すべき動的振る舞いではない。実 API 疎通させても assert すべき新規挙動はない
- `make test-large-forge` に追加すると `gh auth` ユーザ名と test PR の author を一致させる前提が CI で再現不能（CI 上で実 PR を都度作成する設計を本 Issue で導入することになり scope 拡張）
- testing-convention.md § 「不要理由」§ 4 条件: ① self-PR detection / marker comment 構成は Small mock で 100% カバー ② GitHub API 仕様変更は kaji 側で検知すべき責務でなく、`pr-verify` 経由で発見すれば良い ③ Large テストを足しても CI 上の再現が困難で false negative リスクが増す ④ 不要理由を本セクションで説明 — を満たす

### 恒久回帰テストと変更固有検証の切り分け

恒久回帰テスト（Small）を Required。本変更はランタイムコードの分岐追加であり、`docs-only / metadata-only / packaging-only` には該当しない。変更固有の一時検証は不要（mock 化された Small で再現可能）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | provider 抽象や design pattern の新規選定ではなく、既存 GitLab 側 marker 機構の対称適用 |
| docs/ARCHITECTURE.md | なし | provider 境界 / dispatcher 構造は不変 |
| docs/dev/ | なし | workflow / skill 契約は不変（skill / yaml は無改修） |
| docs/reference/ | なし | python style / type-hints / logging 規約は不変 |
| docs/cli-guides/github-mode.md | **あり** | `kaji pr review` 行 (line 87) に self-PR 時の marker comment fallback 挙動を追記。「`gh pr` への純粋 passthrough」ではなくなった旨を明示 |
| docs/cli-guides/gitlab-mode.md | なし | GitLab 側は無改修 |
| docs/cli-guides/local-mode.md | なし | local provider は `kaji pr` 系を `EXIT_INVALID_INPUT` で拒否しているため影響なし |
| CLAUDE.md | なし | 規約変更なし |

`docs/cli-guides/github-mode.md` の追記は実装 PR に含める（doc / code が同期するため別 Issue 化しない）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 1. GitLab provider 側既存実装 `_gitlab_pr_review` | `kaji_harness/cli_main.py:1899-1953` | `glab mr note --message <marker+body>` → `glab mr approve` の 2 段構成。`state = "APPROVED" if ns.approve else "CHANGES_REQUESTED"` で marker を切替。`--request-changes` 経路は approve 済か API で確認してから `revoke`。GitHub 側 fallback はこの 2 段構成のうち「note 投稿のみで成功扱い」を採用 |
| 2. marker 仕様 `build_kaji_review_marker` | `kaji_harness/providers/gitlab.py:613-633` | `_KAJI_REVIEW_MARKER_PREFIX = "<!-- kaji-review: state="` / `_REVIEW_STATES_VALID = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}` / `f"{prefix}{state}{suffix}"`（1 行目 = marker、2 行目以降 = user body）。本 Issue の GitHub 側 fallback はこの marker をそのまま流用 |
| 3. GitHub REST API: Submit a review for a pull request | https://docs.github.com/en/rest/pulls/reviews?apiVersion=2022-11-28#submit-a-review-for-a-pull-request | `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` の "event" parameter (`APPROVE` / `REQUEST_CHANGES` / `COMMENT`) に対し、Validation Failed: `Can not approve your own pull request` / `Can not request changes on your own pull request` を返す仕様。`gh pr review --approve` / `--request-changes` はこの API を経由する |
| 4. GitHub REST API: Create an issue comment | https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28#create-an-issue-comment | `POST /repos/{owner}/{repo}/issues/{issue_number}/comments`。PR の会話 comment は Issue comments API を共有する仕様。author 制約はなく self-PR でも POST 可能。本 Issue の marker fallback はこの endpoint を使用 |
| 5. `pr-verify` skill PASS 条件 | `.claude/skills/pr-verify/SKILL.md:218` | `kaji pr review [pr_id] --approve --body-file -` を rc=0 で完了することが PASS の source of truth。skill 側は provider 種別 / self-PR を判定しないため、GitHub provider 側で吸収する必要がある |
| 6. `review-close` workflow 構造 | `.kaji/wf/review-close.yaml:49-57` | `pr-verify` step の `PASS: close` / `ABORT: end` 遷移。`pr-verify` が ABORT を返すと `close` (= `/issue-close`) は実行されない |
| 7. 既存 `_handle_pr` GitHub 分岐構造 | `kaji_harness/cli_main.py:770-821` | `_PR_BUILTIN_SUBCOMMANDS = {"review-comments", "reviews", "reply-to-comment"}` の 3 つだけ builtin 化、それ以外は `_forward_to_gh("pr", raw_args, repo=repo_override)` 素通し。`review` を仲間に加える形ではなく専用 dispatcher 関数で受ける根拠 |
| 8. `_detect_repo` 補助関数 | `kaji_harness/cli_main.py:568-590` | `gh repo view --json nameWithOwner -q .nameWithOwner` を `subprocess.run` で叩いて 1 値抽出する既存パターン。self-PR 判定の `gh pr view --json author` / `gh api user` も同じ形で実装する |
| 9. testing-convention.md § テスト省略の 4 条件 | `docs/dev/testing-convention.md` (本リポジトリ root から相対) | Medium / Large を省略する場合に「① 独自ロジックなし ② 既存ゲートで捕捉 ③ 情報量増えない ④ 説明可能」の 4 条件を満たす必要。本設計書 § テスト戦略の Medium / Large 不要理由はこれに沿って記述 |
