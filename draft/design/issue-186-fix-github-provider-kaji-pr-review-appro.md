# [設計] GitHub provider の `kaji pr review --approve` を self-PR でも成立させる（marker comment fallback）

Issue: #186

## 概要

GitHub provider 配下の `kaji pr review <pr> --approve` を、PR author 本人が実行しても rc=0 を返すよう拡張する。`gh pr review --approve` は GitHub API 制約 (`Can not approve your own pull request`) により self-PR では失敗するため、GitLab provider と同じ `<!-- kaji-review: state=APPROVED -->` marker 付き comment を投稿することで approve シグナルを表現する。

> **scope 縮小（レビュー review #1 反映）**: 本 Issue では実発生ログで OB が確定している `--approve` 経路のみを self-PR fallback 対象とする。`--request-changes` 経路は公式 REST docs で self-author 個別拒否文言を裏付けられず、実発生ログも無いため、本 Issue では従来通り `gh pr review --request-changes` を素通しし、self-PR で実発生したら別 Issue で fallback を追加する（§ Root Cause § scope 境界 参照）。`--comment` は `gh pr review --comment` の正式 flag であり、author でも GitHub API が許容するため**無変更で従来通り `gh pr` passthrough**（routing 詳細は § 方針 § 1 参照）。

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

同根の他壊れ箇所と scope 境界:

- **`kaji pr review --request-changes`**: GitHub REST docs (§ 参照情報 3) で確認できたのは event 値 (`APPROVE` / `REQUEST_CHANGES` / `COMMENT`) と generic `422 Validation failed` までで、self-author に対する `REQUEST_CHANGES` 個別拒否文言は公開ページから裏付けられなかった。実発生ログ (§ OB) も `--approve` のみ。よって本 Issue では `--request-changes` を fallback 対象に **含めない**（routing 上は従来通り `gh pr review --request-changes` を素通す）。`--request-changes` で実際に self-PR 拒否が観測された時点で別 Issue を起こし、本 Issue の `_github_pr_review` 関数に対称ケースを追加する形で拡張する
- **`kaji pr review --comment`**: GitHub CLI manual (§ 参照情報 10) は `--comment` を `gh pr review` の正式 flag として定義しており、GitHub API も author の `COMMENT` event を許容する。現行 `_handle_pr` (`cli_main.py:816-821`) は未 builtin の `review` を `gh pr` へ素通しするため、`--comment` 経路は現状動作しており回帰させない。本 Issue の routing は **`--approve` flag を含むときのみ専用 dispatcher に分岐**し、`--comment` / 何もフラグを与えない / `--request-changes` 等は **従来通り `gh pr review` へ passthrough** する（§ 方針 § 1）

## インターフェース

### 入力（外部契約は変更なし、内部 routing のみ追加）

外部から見た `kaji pr review` の引数体系・受理 flag セットは **無変更**。`--approve` / `--request-changes` / `--comment` / `--body` / `--body-file` を含む既存 `gh pr review` の全 flag セットを引き続き受理する（routing 詳細は § 方針 § 1）。

| flag 経路 | 動作（修正後） |
|-----------|--------------|
| `--approve` 含む | 新規 dispatcher `_github_pr_review` に分岐 |
| `--request-changes` 含む | **従来通り `gh pr review` へ passthrough**（scope 外） |
| `--comment` 含む | **従来通り `gh pr review` へ passthrough**（無変更） |
| flag 無し / その他 | **従来通り `gh pr review` へ passthrough**（無変更） |

新規 dispatcher `_github_pr_review` が argparse で受理する flag:

- `<pr_id>`: ASCII decimal の PR 番号
- `--approve` (required; `_handle_pr` 側の routing で flag 検出済みのため argparse 必須化)
- `--body BODY` / `--body-file PATH`（相互排他）
- body 解決は GitLab dispatcher と同じく `_read_body_arg(ns.body, ns.body_file)` を流用

### 出力（変更点と契約差分の精緻化）

| ケース | 既存挙動 (GitHub mode) | 修正後 |
|--------|----------------------|--------|
| 非 self-PR で `--approve` | `gh pr review --approve` 委譲 → rc=0 | 委譲前に `gh pr view --json author` + `gh api user` の preflight が走り、両 API 成功 + author≠me で従来 `gh pr review --approve` 委譲。**rc は不変（0 / gh の rc を素通し）だが、新規 preflight 失敗経路が増える**（次行参照） |
| 非 self-PR で `--approve` の preflight 失敗 | （無し、新規経路） | `gh pr view --json author` または `gh api user` が rc≠0 → `EXIT_RUNTIME_ERROR` (5) を返し `gh pr review` は呼ばない（fail-loud） |
| Self-PR で `--approve` | `gh pr review --approve` 委譲 → rc=1 (`Can not approve your own pull request`) | `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<marker+body>` で marker 付き comment を投稿 → 投稿 rc=0 で `_handle_pr` rc=0。`gh pr review` は呼ばない。投稿 rc≠0 → `EXIT_RUNTIME_ERROR` |
| `--request-changes` (self / 非 self いずれも) | `gh pr review --request-changes` 委譲 | **不変**（passthrough 経路に到達せず routing が `_github_pr_review` を起動しないため、preflight も発生しない。本 Issue scope 外） |
| `--comment` | `gh pr review --comment` 委譲 | **不変** |

> **契約差分の明示（レビュー review #1 反映）**: 「非 self-PR で `--approve`」は従来「`gh pr review` 委譲」一段のみだったが、修正後は preflight (`gh pr view --json author -q .author.login` + `gh api user --jq .login`) が先行する。両 API が成功する限り rc は不変 (0 / gh の rc を素通し) だが、**preflight 自体が失敗した場合に `EXIT_RUNTIME_ERROR` を返す新規経路が増える**。これは silent fallthrough 防止のために意図した契約（§ 方針 § 4）であり、回帰テストで明示的に assert する

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
# 1. author 本人による approve (CI / 単独開発 / review-close.yaml 経路から呼ばれる代表ケース)
kaji pr review 185 --approve --body-file - <<'EOF'
## PR レビュー修正確認結果
（pr-verify skill が出す表 etc.）
EOF
# → rc=0
# → issue comments API に `<!-- kaji-review: state=APPROVED -->` + 本文 が 1 件 POST される
# → `gh pr review` は呼ばれない

# 2. 第三者 reviewer (従来挙動)
kaji pr review 185 --approve --body-file - <<'EOF'
LGTM
EOF
# → preflight (gh pr view --json author / gh api user) で author != me を確認
# → `gh pr review --approve --body-file -` を委譲、rc=0

# 3. `--comment` 経路（無変更、従来通り passthrough）
kaji pr review 185 --comment --body "merge前の最終確認お願いします"
# → `_handle_pr` は `--approve` flag を含まないため _github_pr_review に分岐せず
# → `gh pr review --comment --body ...` を素通し、rc=gh の rc

# 4. `--request-changes` 経路（本 Issue scope 外、従来通り passthrough）
kaji pr review 185 --request-changes --body "X を修正してください"
# → `_handle_pr` は `--approve` flag を含まないため _github_pr_review に分岐せず
# → `gh pr review --request-changes --body ...` を素通し
# → self-PR の場合は従来通り gh 側で rc≠0（本 Issue では未対応、別 Issue 追跡）
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
  - `_handle_pr` (GitHub 分岐) で `args[0] == "review"` かつ `_has_approve_flag(args[1:])` が True のとき新規 `_github_pr_review` に dispatch する。それ以外（`--comment` / `--request-changes` / flag 無し）は従来通り `_forward_to_gh("pr", raw_args, repo=repo_override)` に passthrough
  - 新規 `_has_approve_flag(rest: list[str]) -> bool` を追加（`--` 以降を positional 扱いし、`--approve` / `--approve=*` の存在のみ判定）
  - 新規 `_github_pr_review(rest, *, repo_override)` を追加（`--approve` 専用 dispatcher。`--request-changes` は受理しない。`_gitlab_pr_review` と完全対称ではなく approve fallback 単機能）
  - 補助関数 `_gh_capture_value(args) -> str | None` / `_gh_post_issue_comment_silent(*, repo, pr_id, body) -> int` を `_github_pr_review` 近傍に追加（既存 `_detect_repo` パターン準拠の subprocess wrap）
- `tests/test_cli_main.py`:
  - 新規 `TestGithubPrReviewApprove` クラスを追加（既存 `TestPrReviewCommentsBuiltin` パターン準拠）。self-PR / 非 self-PR の `--approve`、`--comment` / `--request-changes` / flag 無しの routing 非破壊、preflight 失敗の fail-loud、`_has_approve_flag` 単体、stdout 抑止を assert
- `docs/cli-guides/github-mode.md:87`: `review` sub の self-PR 挙動と `--comment` / `--request-changes` は従来通り passthrough である旨を追記
- GitLab provider / Local provider / skill / workflow YAML: **無改修**

GitLab / Local 経路、`pr-verify` skill、`.kaji/wf/review-close.yaml` / `review-cycle.yaml` は変更しない。

## 方針

### 1. dispatcher 分岐（`--comment` / `--request-changes` 非破壊 routing）

`_handle_pr` 内、GitHub 分岐の末尾（`_PR_BUILTIN_SUBCOMMANDS` チェックの直後）に `review` 専用分岐を追加。ただし **subcommand 名で奪わず、`--approve` flag の有無を pre-scan して分岐**する。`--approve` 以外の flag（`--comment` / `--request-changes` / flag 無し）は従来通り `gh pr review` へ passthrough し、既存契約を完全保存する:

```python
# _handle_pr (GitHub 分岐) 末尾の置換イメージ
if args and args[0] == "review" and _has_approve_flag(args[1:]):
    return _github_pr_review(args[1:], repo_override=repo_override)
if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
    return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
return _forward_to_gh("pr", raw_args, repo=repo_override)


def _has_approve_flag(rest: list[str]) -> bool:
    """rest 中に '--approve' / '--approve=...' が含まれるかを pre-scan する。

    '--approve' 単独 flag は値を取らないので boolean 存在判定で十分。
    '--' 以降は positional として扱う（gh の慣習に合わせる）。
    """
    for tok in rest:
        if tok == "--":
            return False
        if tok == "--approve" or tok.startswith("--approve="):
            return True
    return False
```

設計判断:

- **subcommand 単位で奪わない理由**: `kaji pr review --comment ...` / `kaji pr review --request-changes ...` は GitHub CLI manual (§ 参照情報 10) の正式 flag であり、現行 `_handle_pr` (`cli_main.py:816-821`) で動作している。subcommand 名 (`review`) だけで奪い、新 dispatcher が `--approve` / `--request-changes` 以外を拒否する設計は、本 Issue で破壊するべきでない既存契約に副作用を与える。flag 単位の pre-scan で **`--approve` 経路のみ**を狙い撃ちで奪う
- **`--request-changes` の取り扱い**: § Root Cause § 同根の他壊れ箇所 で述べた通り、self-author に対する `REQUEST_CHANGES` 個別拒否を一次情報で裏付けられない / 実発生ログ無しのため、本 Issue scope 外。`_has_approve_flag` は `--approve` のみを true にする
- **`_PR_BUILTIN_SUBCOMMANDS` に追加しない理由**: 既存 builtin は read-only な `gh api .../<suffix>` forward が共通形だが、`_github_pr_review` は marker 投稿 + 条件付き forward と構造が異なるため、`_dispatch_pr_builtin` の argparse 形に乗らない
- **pre-scan の副作用**: `args[1:]` を 2 回スキャンする（pre-scan + `_github_pr_review` 内 argparse）。args 件数は実用上数個レベルで重複コスト無視可能

### 2. `_github_pr_review` の構造（疑似コード）

`--approve` 専用 dispatcher。`--request-changes` / `--comment` は本関数に到達しない（routing 段で pre-scan 済み）。

```python
def _github_pr_review(rest: list[str], *, repo_override: str | None) -> int:
    # 2.1 argparse （--approve のみ受理。本関数に到達した時点で --approve 含む保証あり）
    p = argparse.ArgumentParser(prog="kaji pr review", add_help=True)
    p.add_argument("pr_id", type=str)
    p.add_argument("--approve", action="store_true", required=True)
    p.add_argument("--body", default=None)
    p.add_argument("--body-file", dest="body_file", default=None)
    ns = p.parse_args(rest)

    # 2.2 入力検証
    if not _is_ascii_decimal(ns.pr_id): → EXIT_INVALID_INPUT
    try:
        body = _read_body_arg(ns.body, ns.body_file) or ""
    except ValueError: → EXIT_INVALID_INPUT  # --body と --body-file 同時指定

    # 2.3 gh CLI 存在チェック + repo 解決
    if shutil.which("gh") is None: → _GH_MISSING_GUIDANCE / EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=repo_override)
    if repo is None: → 既存 error message / EXIT_RUNTIME_ERROR

    # 2.4 self-PR 判定（preflight、fail-loud）
    #   _gh_capture_value は `subprocess.run(["gh", *args], capture_output=True, text=True)`
    #   を実行し、rc==0 なら stdout.strip() を返し、rc≠0 なら stderr を中継して
    #   None を返す最小ヘルパ（_detect_repo と同じパターン）
    pr_author = _gh_capture_value(["pr", "view", ns.pr_id, "--repo", repo,
                                   "--json", "author", "--jq", ".author.login"])
    if pr_author is None: → EXIT_RUNTIME_ERROR
    me = _gh_capture_value(["api", "user", "--jq", ".login"])
    if me is None: → EXIT_RUNTIME_ERROR
    is_self = (pr_author == me)

    # 2.5 marker + body
    marker = build_kaji_review_marker("APPROVED")
    marked_body = f"{marker}\n{body}"

    if is_self:
        # 2.6a marker comment 投稿のみで成功扱い
        # stdout 抑止: capture_output=True で gh の JSON response を捨て、
        #              本コマンドの stdout contract は「空 + rc のみ」と定義する
        return _gh_post_issue_comment_silent(repo=repo, pr_id=ns.pr_id, body=marked_body)
    # 2.6b 非 self-PR: 従来通り gh pr review に委譲（marker は付けない）
    #      gh の stdout はそのまま中継（capture せず）
    gh_args = ["pr", "review", ns.pr_id, "--repo", repo, "--approve"]
    if body:
        gh_args.extend(["--body", body])
    return _forward_to_gh_passthrough(gh_args)


def _gh_post_issue_comment_silent(*, repo: str, pr_id: str, body: str) -> int:
    """`gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<body>` 実行。

    `gh api` は POST レスポンス JSON を stdout に書く既定挙動。本関数は
    `capture_output=True` で stdout を捨て、rc のみを返す。skill / workflow は
    rc=0 を PASS source-of-truth として使うため、stdout contract は意図的に
    「空 + rc のみ」とする（改善提案 #1 反映）。

    Returns:
        0: 投稿成功
        EXIT_RUNTIME_ERROR: 投稿失敗（stderr に gh の出力を中継）
    """
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
| `gh pr view --json author` が rc≠0 | stderr に gh の出力を中継、`EXIT_RUNTIME_ERROR`（preflight 失敗、self/非 self の判定不能のため fail-loud） |
| `gh api user --jq .login` が rc≠0 | 同上 |
| Self-PR 経路の `gh api POST issues/<N>/comments` が rc≠0 | stderr 中継、`EXIT_RUNTIME_ERROR`（marker 投稿失敗は approve 不成立として扱う） |
| 非 self-PR 経路の `gh pr review --approve` が rc≠0 | 既存挙動と同じく rc をそのまま返す |

self-PR 判定で「どちらかの取得に失敗 → 安全側に倒して non-self として扱い `gh pr review` 委譲」のような silent fallthrough は採用しない。author / authenticated user 取得失敗は dispatcher 失敗として明示的に伝える（fail-loud）。

### 5. 既存テストとの整合（既存 GitHub dispatcher テスト構成への接続）

実在する関連テストは以下:

- `tests/test_cli_main.py:665-873` — `TestPrReviewCommentsBuiltin` 等の `_handle_pr` builtin dispatch Small テスト群。`_load_config_for_dispatch` を `_stub_github_config` で差し替え、`shutil.which` / `_detect_repo` / `cli_main.subprocess.run` を patch する確立パターン
- `tests/test_dispatcher.py:788-901` — `TestForwardToGhRepoInjection` 等の `_handle_issue` / `_handle_pr` 結合 Medium テスト。`_write_repo` で実 config ファイルを作成し `KajiConfig.discover` 実経路 + `cli_main.subprocess.run` patch
- `tests/test_dispatcher_gitlab.py` — GitLab 経路。本 Issue では無改修

本 Issue では `tests/test_cli_main.py` の `TestPrReviewCommentsBuiltin` 隣に `TestGithubPrReviewApprove` クラスを新設し、`_load_config_for_dispatch` を `_stub_github_config` で差し替えるパターンに準拠する（§ テスト戦略 § 配置で詳述）。`tests/test_dispatcher_github.py` という名称のファイルは worktree に **存在しない**（review 指摘 #3 反映）。

既存 `kaji pr review` 経路を直接 assert している test が `tests/test_cli_main.py` / `tests/test_dispatcher.py` に存在しないことは `grep` 確認済み。`--comment` / `--request-changes` の従来通り passthrough は本 Issue で新規回帰テストを追加することで保護する（§ テスト戦略 § Small）。

## テスト戦略

### 変更タイプ
- 実行時コード変更（GitHub provider dispatcher 経路の挙動を変更）

### 実行時コード変更の場合

#### Small テスト

**配置**: `tests/test_cli_main.py` に新規 `TestGithubPrReviewApprove` クラスを追加（既存 `TestPrReviewCommentsBuiltin` と同居）。

**境界差し替え方針**: 既存 `TestPrReviewCommentsBuiltin` パターンに完全準拠する:

```python
# 既存パターン（tests/test_cli_main.py:670-687 抜粋、本 Issue でも採用）
@pytest.fixture(autouse=True)
def _isolate_config(self, monkeypatch):
    monkeypatch.setattr(
        "kaji_harness.cli_main._load_config_for_dispatch",
        _stub_github_config,
    )

def _patches(self, repo="owner/repo"):
    which = patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh")
    detect = patch("kaji_harness.cli_main._detect_repo", return_value=repo)
    run = patch("kaji_harness.cli_main.subprocess.run")
    return which, detect, run
```

この差し替えは `_load_config_for_dispatch` を stub することで `get_provider()` 経路を bypass する形であり、testing-convention.md § `subprocess.run` patch スコープ表 § dispatch/provider 結合の **禁止対象（実 `get_provider()` 経路）に該当しない**。実在 patch パターン (`tests/test_cli_main.py:665-873`) が本規約と矛盾なく成立している前例に従う（review 指摘 #3 反映）。実 `get_provider()` 経路を通す結合テストが必要になった時点では系統 A/B に従い別途設計するが、本 Issue の Small 観点は handler 単位で完結するため不要。

**検証観点**:

- **bug 再現テスト（必須・Red 化）**: `kaji pr review <pr> --approve` 実行時、`subprocess.run` の sequence mock で:
  - PR author 取得 (`gh pr view --json author -q .author.login`): stdout=`"apokamo"`, rc=0
  - authenticated user 取得 (`gh api user --jq .login`): stdout=`"apokamo"`, rc=0
  - 修正前: `gh pr review --approve` が rc=1 (`Can not approve your own pull request` stderr mock) → `_handle_pr` rc=1
  - 修正後: `gh pr review` は呼ばれず、`gh api --method POST repos/owner/repo/issues/185/comments -f body=<marker+body>` が 1 回呼ばれ、`_handle_pr` rc=0
  - assert: POST 呼び出しの `-f body=...` 値の先頭行が `build_kaji_review_marker("APPROVED")` (= `<!-- kaji-review: state=APPROVED -->`) と一致
- **非 self-PR `--approve` 回帰防止**: PR author=`"alice"`, authenticated user=`"bob"` の mock で `gh pr review --approve` が委譲され rc=0。`gh api ... POST issues/.../comments` 呼び出しが **0 回** であること（call_args_list を assert）
- **`--comment` 経路 routing 非破壊回帰テスト（必須・review 指摘 #1 反映）**: `kaji pr review 185 --comment --body "..."` を `_handle_pr` に渡したとき、`_github_pr_review` 内部の preflight (`gh pr view --json author`) が **呼ばれない**（routing 段で `_has_approve_flag` が false を返し dispatcher に到達しない）。呼ばれる subprocess は `gh pr review --comment --body ...` 1 回のみ。call_args_list で assert
- **`--request-changes` 経路 routing 非破壊回帰テスト**: 同上、preflight 0 回 + `gh pr review --request-changes ...` 1 回
- **flag 無し routing 非破壊回帰テスト**: `kaji pr review 185` （flag 無し）も従来通り `gh pr review 185` に passthrough。preflight 0 回
- **`--has_approve_flag` 単体テスト**: `["--approve"]` / `["--approve=true"]` → True、`["--comment"]` / `["--request-changes"]` / `["--", "--approve"]` / `[]` → False
- **入力検証**: `<pr_id>` 非 ASCII decimal → `EXIT_INVALID_INPUT`、`--body` と `--body-file` 同時指定 → `EXIT_INVALID_INPUT`
- **空 body**: `--body` も `--body-file` も未指定の self-PR `--approve` で marker のみの body (`"<marker>\n"`) が POST されること
- **失敗ハンドリング（preflight fail-loud）**: `gh pr view --json author` rc≠0 → `EXIT_RUNTIME_ERROR` で `gh pr review` も `gh api POST` も呼ばれない / `gh api user` rc≠0 → 同上 / `gh api POST .../comments` rc≠0 → `EXIT_RUNTIME_ERROR`
- **stdout 抑止契約（改善提案 #1 反映）**: self-PR 経路で `gh api POST` を `subprocess.run(..., capture_output=True)` で呼んでいることを assert（`call_args[1]["capture_output"] is True` を確認、または `capfd` で `_handle_pr` の stdout が空であること）

#### Medium テスト

不要。理由（review 指摘 #3 反映: 既存テストファイル名を実在に合わせて訂正）:

- ファイル I/O / DB 結合は本変更に含まれない（subprocess を介す `gh` 呼び出しは Small で `cli_main.subprocess.run` を mock 化して観測する設計）
- worktree / config discovery の medium 検証は既存 `tests/test_dispatcher.py:788-901` (`TestForwardToGhRepoInjection`) が `_handle_issue` 経由で `KajiConfig.discover` 実経路 + `cli_main.subprocess.run` patch のパターンを既に確立しており、本 Issue の差分（`--approve` flag pre-scan + 専用 dispatcher への分岐）は handler 内部ロジックの追加であり、config discovery / provider 解決経路には影響しない
- 本 Issue の routing 変更（`--approve` flag 検出時のみ分岐）は Small で `_handle_pr` 経由で `_has_approve_flag` の真偽 → 期待 subprocess sequence の対応を直接検証可能。Medium で実 `KajiConfig.discover` を通しても assert 対象は同じ
- testing-convention.md § 「不要理由」§ 4 条件: ① 独自ロジック（pre-scan + preflight + marker post）は Small で完結 ② 想定不具合パターン（`--comment` 破壊 / preflight 失敗 silent fallthrough / marker body 不正）は Small mock で捕捉 ③ Medium を増やしても assert 対象に新規シグナルが増えない ④ 不要理由を本セクションで説明 — を満たす

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
| 3. GitHub REST API: Create / Submit a review for a pull request | https://docs.github.com/en/rest/pulls/reviews?apiVersion=2022-11-28#create-a-review-for-a-pull-request | `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` の "event" parameter (`APPROVE` / `REQUEST_CHANGES` / `COMMENT`) と generic `422 Validation failed` を確認した。**self-author に対する `APPROVE` / `REQUEST_CHANGES` 個別拒否文言は公開ページから直接は確認できなかった**（review 指摘 #2 反映）。本 Issue では `APPROVE` 経路の self-PR 拒否を実発生ログ (§ OB) で固定し、`REQUEST_CHANGES` の対称化は本 Issue scope 外とする |
| 4. GitHub REST API: Create an issue comment | https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28#create-an-issue-comment | `POST /repos/{owner}/{repo}/issues/{issue_number}/comments`。PR の会話 comment は Issue comments API を共有する仕様。author 制約はなく self-PR でも POST 可能。本 Issue の marker fallback はこの endpoint を使用 |
| 5. `pr-verify` skill PASS 条件 | `.claude/skills/pr-verify/SKILL.md:218` | `kaji pr review [pr_id] --approve --body-file -` を rc=0 で完了することが PASS の source of truth。skill 側は provider 種別 / self-PR を判定しないため、GitHub provider 側で吸収する必要がある |
| 6. `review-close` workflow 構造 | `.kaji/wf/review-close.yaml:49-57` | `pr-verify` step の `PASS: close` / `ABORT: end` 遷移。`pr-verify` が ABORT を返すと `close` (= `/issue-close`) は実行されない |
| 7. 既存 `_handle_pr` GitHub 分岐構造 | `kaji_harness/cli_main.py:770-821` | `_PR_BUILTIN_SUBCOMMANDS = {"review-comments", "reviews", "reply-to-comment"}` の 3 つだけ builtin 化、それ以外は `_forward_to_gh("pr", raw_args, repo=repo_override)` 素通し。`review` を仲間に加える形ではなく専用 dispatcher 関数で受ける根拠 |
| 8. `_detect_repo` 補助関数 | `kaji_harness/cli_main.py:568-590` | `gh repo view --json nameWithOwner -q .nameWithOwner` を `subprocess.run` で叩いて 1 値抽出する既存パターン。self-PR 判定の `gh pr view --json author` / `gh api user` も同じ形で実装する |
| 9. testing-convention.md § テスト省略の 4 条件 + § `subprocess.run` patch スコープ | `docs/dev/testing-convention.md` § テスト戦略の原則 / § `subprocess.run` patch スコープ (lines 133-146) | Medium / Large 省略の 4 条件、および `_handle_pr` 経路で `cli_main.subprocess.run` の namespace patch を許容する境界（`_load_config_for_dispatch` を stub し `get_provider()` 経路を bypass するパターン、既存 `tests/test_cli_main.py:670-687` 前例）を本設計書 § テスト戦略で参照 |
| 10. GitHub CLI manual: `gh pr review` | https://cli.github.com/manual/gh_pr_review | `--approve` / `--comment` / `--request-changes` の 3 flag を正式 option として定義。`--comment` は author でも GitHub API が許容するため現行 `_handle_pr` passthrough が動作している根拠（review 指摘 #1 反映: 既存 `--comment` contract 保護の一次根拠） |
| 11. 既存 GitHub builtin dispatch test 構成 | `tests/test_cli_main.py:665-873` (`TestPrReviewCommentsBuiltin` 等) / `tests/test_dispatcher.py:788-901` (`TestForwardToGhRepoInjection`) | 本 Issue の Small テスト追加先と境界差し替えパターンの前例（review 指摘 #3 反映: `tests/test_dispatcher_github.py` という worktree 非実在ファイル名の訂正） |
