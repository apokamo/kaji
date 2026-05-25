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

GitHub 側でも同じ marker を用い、`--approve` flag 時に self-PR を検知した場合のみ `gh pr review` 呼び出しを skip して marker 付き comment 投稿のみで成功扱いに切り替える。非 self-PR の `--approve` は従来通り `gh pr review --approve` を実行し既存挙動を維持。`--request-changes` / `--comment` / flag 無しは routing 段で `_github_pr_review` に分岐せず全経路で完全不変（§ Root Cause § scope 境界 / § 方針 § 1）。これにより:

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

`kaji_harness/cli_main.py:770-821` の `_handle_pr` は GitHub mode で `review-comments` / `reviews` / `reply-to-comment` の 3 つを builtin 化するのみで、`review` / `create` / `view` / `list` / `comment` / `merge` 等は `_forward_to_gh("pr", raw_args, repo=repo_override)` で `gh pr <sub>` に素通しする。`review` は GitHub API `POST /repos/{owner}/{repo}/pulls/{N}/reviews` を叩く `gh` 側ロジックに到達する。GitHub API は author による `APPROVE` event を `422 Validation failed: Can not approve your own pull request` で拒否することが本 Issue の実発生ログ (§ OB) で確認できている。`REQUEST_CHANGES` event の self-author 拒否については公開 REST docs (§ 参照情報 § 3) では event 値と generic 422 までしか裏付けられず、本 Issue では推定の域を出ない（詳細は次節 § scope 境界）。

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
| 非 self-PR で `--approve` の preflight 失敗 | （無し、新規経路） | `gh pr view --json author` または `gh api user` が rc≠0 → `EXIT_RUNTIME_ERROR` (3) を返し `gh pr review` は呼ばない（fail-loud） |
| Self-PR で `--approve` | `gh pr review --approve` 委譲 → rc=1 (`Can not approve your own pull request`) | `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<marker+body>` で marker 付き comment を投稿 → 投稿 rc=0 で `_handle_pr` rc=0。`gh pr review` は呼ばない。投稿 rc≠0 → `EXIT_RUNTIME_ERROR` |
| `--request-changes` (self / 非 self いずれも) | `gh pr review --request-changes` 委譲 | **完全不変**（routing で `_github_pr_review` に分岐せず従来の `_forward_to_gh` passthrough を維持。本 Issue では self-PR 時の GitHub API 振る舞いを検証せず未検証として扱う。実発生観測時に別 Issue で fallback 追加） |
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
# → `gh pr review --request-changes --body ...` を素通し、rc=gh の rc
# → self-PR 時の GitHub API 側の振る舞いは本 Issue で検証していない（未検証 / 別 Issue 追跡）
```

## 制約・前提条件

- 修正対象は GitHub provider 経路 (`kaji_harness/cli_main.py` の `_handle_pr` GitHub 分岐) のみ。GitLab / Local provider 経路は無変更
- `build_kaji_review_marker` / `_KAJI_REVIEW_MARKER_PREFIX` / `_REVIEW_STATES_VALID` は `kaji_harness/providers/gitlab.py:613-633` に既に存在。本 Issue では **`gitlab.py` から `providers/_review_marker.py` 等の provider 中立モジュールへ移管せず**、`cli_main.py` の既存 import (`cli_main.py:39`) を流用して GitHub dispatcher 側でも参照する。理由: 関数自体は provider 非依存だが、移管はファイル境界を新設する Scope 拡張になるため別 Issue 推奨（参照: bug.md § 「リファクタ混在を避ける」）
- self-PR 判定は `gh api user --jq .login` (authenticated user) と `gh pr view <pr> --json author --jq .author.login` (PR author) を比較する。両 API は repo 権限以外の追加 scope を要求しない
- marker comment 投稿は `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<text>` を使う（PR の「会話」コメントは Issue comments API に統一されている GitHub 仕様）。`gh pr comment <pr> --body <text>` でも等価だが、`_handle_pr` の他 forward 経路が `gh api` ベースで統一されているのに合わせる
- 本変更は `kaji pr review --approve` の rc semantics のみ変更する（`--request-changes` / `--comment` / flag 無しは routing が `_github_pr_review` に分岐しないため挙動完全不変）。stdout 出力は marker comment 投稿レスポンスを抑止し（既存 `_forward_pr_api_list` パターンに準拠して標準出力に GitHub API レスポンスを流す既定との互換性は GitLab dispatcher と同じく "rc のみ意味があり stdout は best-effort" とする）
- `--body` / `--body-file` 同時指定は既存 `_read_body_arg` 経由で `ValueError` → `EXIT_INVALID_INPUT` 化する（GitLab dispatcher と同じ挙動）
- 単独開発前提（author == authenticated user）を主シナリオに据えるが、CI から bot が approve するケースとの両立も維持する（authenticated user ≠ PR author → 従来 `gh pr review` 委譲）

## 変更スコープ

- `kaji_harness/cli_main.py`:
  - `_handle_pr` (GitHub 分岐) で `args[0] == "review"` かつ `_has_approve_flag(args[1:])` が True のとき新規 `_github_pr_review` に dispatch する。それ以外（`--comment` / `--request-changes` / flag 無し）は従来通り `_forward_to_gh("pr", raw_args, repo=repo_override)` に passthrough
  - 新規 `_has_approve_flag(rest: list[str]) -> bool` を追加（`--` 以降を positional 扱いし、`--approve` / `--approve=*` の存在のみ判定）
  - 新規 `_github_pr_review(rest, *, repo_override)` を追加（`--approve` 専用 dispatcher。`--request-changes` は受理しない。`_gitlab_pr_review` と完全対称ではなく approve fallback 単機能）
  - 補助関数 `_gh_capture_value(args) -> str | None` / `_gh_post_issue_comment_silent(*, repo, pr_id, body) -> int` を `_github_pr_review` 近傍に追加（既存 `_detect_repo` パターン準拠の subprocess wrap）
- `tests/test_cli_main.py`:
  - 新規 3 クラスを追加（既存 `TestPrReviewCommentsBuiltin` と同居、testing-convention.md § patch スコープ表に準拠したテスト境界分離）:
    - `TestHasApproveFlag`: `_has_approve_flag` 純粋関数の単体テスト
    - `TestGithubPrReviewHandler`: `_github_pr_review` 直接呼び出し（`_handle_pr` 非経由）の handler 単体テスト。subprocess 引数 / rc 経路を `cli_main.subprocess.run` mock で検証
    - `TestGithubPrReviewRouting`: `_handle_pr` 経由の routing 振り分けを `_github_pr_review` / `_forward_to_gh` の stub で検証（`cli_main.subprocess.run` namespace patch は使わない）
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

- `tests/test_cli_main.py:665-873` — `TestPrReviewCommentsBuiltin` 等の `_handle_pr` builtin dispatch Small テスト群
- `tests/test_dispatcher.py:788-901` — `TestForwardToGhRepoInjection` 等の Medium 結合テスト
- `tests/test_dispatcher_gitlab.py` — GitLab 経路。本 Issue では無改修

本 Issue では `tests/test_cli_main.py` の `TestPrReviewCommentsBuiltin` 隣に新規クラスを 2 つ追加し、テスト境界を明確に分離する（§ テスト戦略 § Small で詳述）:

- **handler 単体クラス `TestGithubPrReviewHandler`**: `_github_pr_review(rest, repo_override=...)` を直接呼び出す。`_handle_pr` を通さないため、testing-convention.md § patch スコープ表 § 「dispatch / provider 結合（`_handle_pr` 経路）」の禁止対象に **該当しない**。`cli_main.subprocess.run` の namespace patch は本層で使用許容
- **routing クラス `TestGithubPrReviewRouting`**: `_handle_pr` 経由の routing 振り分け（`--approve` / `--comment` / `--request-changes` / flag 無し）を assert。`cli_main.subprocess.run` の namespace patch は **使わず**、`_github_pr_review` / `_forward_to_gh` 自体を `patch(...)` で差し替えて「どちらが何回どの引数で呼ばれたか」のみ assert（subprocess は走らない経路）

非実在の `tests/test_dispatcher_github.py` 言及は前 cycle で削除済み（review 指摘 #3 cycle 1 反映）。`--comment` / `--request-changes` の従来通り passthrough は routing クラス側で新規回帰テストを追加することで保護する。

## テスト戦略

### 変更タイプ
- 実行時コード変更（GitHub provider dispatcher 経路の挙動を変更）

### 実行時コード変更の場合

#### Small テスト

`testing-convention.md:135-143` の patch スコープ表（cycle 1 review 指摘 #3 / cycle 2 verify 指摘 #2 反映）に厳密に従う。`_handle_pr` 経路では `cli_main.subprocess.run` の namespace patch を使わない。代わりに以下 3 クラスにテスト境界を分離する。

##### Small クラス 1: `TestHasApproveFlag` — 純粋関数の単体テスト

対象: `_has_approve_flag(rest: list[str]) -> bool`。subprocess を一切呼ばないため mock 不要。

検証観点:
- `["--approve"]` → True、`["--approve=true"]` → True
- `["--comment"]` / `["--request-changes"]` / `[]` → False
- `["--", "--approve"]` → False（`--` 以降は positional 扱い）
- `["185", "--approve", "--body", "x"]` → True（位置に依存しない）
- `["185", "--body", "--approve-only-not-real"]` → False（`--approve` 完全一致 or `--approve=` prefix のみ true）

##### Small クラス 2: `TestGithubPrReviewHandler` — handler 直接呼び出し

対象: `_github_pr_review(rest: list[str], *, repo_override: str)`。**`_handle_pr` を経由しない**（直接 import して呼ぶ）ため、testing-convention.md § patch スコープ表 § dispatch/provider 結合 の **禁止対象に該当しない**。`cli_main.subprocess.run` の namespace patch は本クラスで使用許容。

境界差し替え:

```python
# _handle_pr / _load_config_for_dispatch / get_provider は経由しない
def _patches(self, repo="owner/repo"):
    which = patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh")
    detect = patch("kaji_harness.cli_main._detect_repo", return_value=repo)
    run = patch("kaji_harness.cli_main.subprocess.run")
    return which, detect, run

def test_self_pr_approve_posts_marker_only(self):
    from kaji_harness.cli_main import _github_pr_review
    which, detect, run = self._patches()
    with which, detect, run as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="apokamo\n"),  # gh pr view --json author -q .author.login
            MagicMock(returncode=0, stdout="apokamo\n"),  # gh api user --jq .login
            MagicMock(returncode=0),                       # gh api POST issues/.../comments
        ]
        rc = _github_pr_review(["185", "--approve", "--body", "LGTM"],
                               repo_override="owner/repo")
        assert rc == 0
        # 3 回目の呼び出しが POST comments で marker 付き body であることを assert
```

検証観点:
- **bug 再現テスト（必須・Red 化）**: PR author == authenticated user の mock 状態:
  - 修正前（routing 未導入時に `_handle_pr` 経由で `gh pr review --approve` 委譲を再現）→ rc=1 (`Can not approve your own pull request` stderr mock)
  - 修正後（`_github_pr_review` 直接呼び出し）→ `gh pr review` 呼び出し 0 回、`gh api --method POST repos/owner/repo/issues/185/comments -f body=<marker+body>` 1 回、rc=0
  - assert: POST `-f body=` 値の先頭行が `build_kaji_review_marker("APPROVED")` (= `<!-- kaji-review: state=APPROVED -->`) と一致
- **非 self-PR `--approve` 回帰防止**: PR author=`"alice"`, authenticated user=`"bob"` の mock で `gh pr review --approve` が委譲 (`_forward_to_gh` 経由 or 同等の `subprocess.run` 呼び出し) → rc=0。`gh api ... POST issues/.../comments` が **0 回**（call_args_list を assert）
- **入力検証**: `<pr_id>` 非 ASCII decimal → `EXIT_INVALID_INPUT`、`--body` と `--body-file` 同時指定 → `EXIT_INVALID_INPUT`
- **空 body**: `--body` も `--body-file` も未指定の self-PR `--approve` で marker のみの body (`"<marker>\n"`) が POST されること
- **失敗ハンドリング（preflight fail-loud）**: `gh pr view --json author` rc≠0 → `EXIT_RUNTIME_ERROR` で以降の subprocess 呼び出し 0 回 / `gh api user` rc≠0 → 同上、author 取得後の呼び出し 0 回 / `gh api POST .../comments` rc≠0 → `EXIT_RUNTIME_ERROR`
- **stdout 抑止契約（改善提案 #1 反映）**: self-PR 経路で `gh api POST` 呼び出しが `subprocess.run(..., capture_output=True)` で行われていることを `call_args[1]["capture_output"] is True` で assert
- **`gh` 未インストール / `_detect_repo` 失敗**: 既存 `_GH_MISSING_GUIDANCE` / repo 未解決エラーが先行し `EXIT_RUNTIME_ERROR`、preflight に到達しない

##### Small クラス 3: `TestGithubPrReviewRouting` — `_handle_pr` routing 振り分け

対象: `_handle_pr` 内の `_has_approve_flag` pre-scan による dispatch 振り分け。**testing-convention.md § patch スコープ表 § dispatch/provider 結合 の禁止規定に違反しないよう、`cli_main.subprocess.run` の namespace patch は使わない**。代わりに dispatch 先関数自体を mock してその呼び出し有無のみ assert する形にする（subprocess は走らない）。

境界差し替え:

```python
@pytest.fixture(autouse=True)
def _isolate_config(self, monkeypatch):
    # 既存 TestPrReviewCommentsBuiltin と同じく provider 解決経路を stub
    monkeypatch.setattr(
        "kaji_harness.cli_main._load_config_for_dispatch",
        _stub_github_config,
    )

def test_approve_flag_routes_to_handler(self, monkeypatch):
    called = []
    monkeypatch.setattr(
        "kaji_harness.cli_main._github_pr_review",
        lambda rest, *, repo_override: called.append(("handler", rest, repo_override)) or 0,
    )
    monkeypatch.setattr(
        "kaji_harness.cli_main._forward_to_gh",
        lambda *a, **kw: called.append(("forward", a, kw)) or 0,
    )
    from kaji_harness.cli_main import _handle_pr
    rc = _handle_pr(["review", "185", "--approve", "--body", "x"])
    assert rc == 0
    assert called == [("handler", ["185", "--approve", "--body", "x"], "owner/repo")]
    # subprocess.run は一切呼ばれていない（testing-convention.md 制約に違反しない）
```

検証観点:
- **`--approve` 経路 → `_github_pr_review` に dispatch**: `_github_pr_review` 1 回呼び出し、`_forward_to_gh` 0 回
- **`--comment` 経路 → 従来通り `_forward_to_gh` passthrough**: `_forward_to_gh` 1 回呼び出し（args=`("pr", ["review", "185", "--comment", ...], repo=...)`、`_github_pr_review` 0 回
- **`--request-changes` 経路 → 従来通り `_forward_to_gh` passthrough**: 同上
- **flag 無し `kaji pr review 185` → 従来通り passthrough**: 同上
- **既存 builtin (`review-comments` / `reviews` / `reply-to-comment`) の dispatch が無回帰**: 既存テスト (`TestPrReviewCommentsBuiltin` 等) で既にカバー済みなので追加不要

> **本クラスが `cli_main.subprocess.run` namespace patch を回避する正当性**: `testing-convention.md:135-143` は `_handle_pr` 経路で `subprocess.run` 名前空間 patch を「`MagicMock != 0` の truthy 評価などで暗黙の分岐依存が忍び込む」リスクから禁止している。本クラスは dispatch 先関数 (`_github_pr_review` / `_forward_to_gh`) を直接 stub し subprocess 呼び出し自体を発生させないため、禁止が意図したリスクは構造的に発生しない。subprocess の引数組み立て / rc 経路は § Small クラス 2 で handler 単体テストとして分離してカバーする。

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
| 9. testing-convention.md § テスト省略の 4 条件 + § `subprocess.run` patch スコープ | `docs/dev/testing-convention.md` § テスト戦略の原則 / § `subprocess.run` patch スコープ (lines 133-146) | Medium / Large 省略の 4 条件、および `_handle_pr` 経路での `cli_main.subprocess.run` namespace patch 禁止規定（例外定義なし）を本設計書 § テスト戦略で参照。本 Issue ではこの禁止規定を遵守し、subprocess 引数 / rc 検証は `_handle_pr` を経由しない `_github_pr_review` 直接呼び出しの handler 単体テストに分離する（§ テスト戦略 § Small クラス 2）。`_handle_pr` 経由の routing 検証は dispatch 先関数自体を stub し subprocess を発生させない設計（§ Small クラス 3）に統一する |
| 10. GitHub CLI manual: `gh pr review` | https://cli.github.com/manual/gh_pr_review | `--approve` / `--comment` / `--request-changes` の 3 flag を正式 option として定義。`--comment` は author でも GitHub API が許容するため現行 `_handle_pr` passthrough が動作している根拠（review 指摘 #1 反映: 既存 `--comment` contract 保護の一次根拠） |
| 11. 既存 GitHub builtin dispatch test 構成 | `tests/test_cli_main.py:665-873` (`TestPrReviewCommentsBuiltin` 等) / `tests/test_dispatcher.py:788-901` (`TestForwardToGhRepoInjection`) | 本 Issue の Small テスト追加先と境界差し替えパターンの前例（review 指摘 #3 反映: `tests/test_dispatcher_github.py` という worktree 非実在ファイル名の訂正） |
