# [設計] GitHub provider の `kaji pr review --request-changes` を self-PR でも成立させる（marker comment fallback）

Issue: #199

## 概要

GitHub provider 配下の `kaji pr review <pr> --request-changes` を、PR author 本人が実行しても rc=0 を返すよう拡張する。`gh pr review --request-changes` は GitHub API 制約 (`Can not request changes on your own pull request`) により self-PR では失敗するため、Issue #186 で `--approve` 用に導入した `<!-- kaji-review: state=CHANGES_REQUESTED -->` marker 付き comment を Issue Comments API に投稿することで Changes Requested シグナルを表現する。`--approve` 経路（Issue #186 で実装済み）と完全対称な fallback を `_github_pr_review` 内に追加する。

> **scope（Issue #186 設計の延長）**: 本 Issue は Issue #186 設計書 § Root Cause § scope 境界 で「`--request-changes` で実発生が観測された時点で別 Issue を起こし、`_github_pr_review` 関数に対称ケースを追加する形で拡張する」と明示された後継 Issue にあたる。実発生は 2026-05-27 JST に PR #198 / Issue #190 の `review` step で確認済み（§ Observed Behavior）。`--comment` / flag 無し経路は引き続き `gh pr review` への passthrough を維持し、無変更。

## 背景・目的

### Observed Behavior (OB)

`provider.type = "github"` 構成で `kaji pr review <pr> --request-changes` を PR author（= `gh auth` ユーザ）が実行すると、`gh pr review --request-changes` が GitHub API から拒否されてプロセスが exit 1 となる。

実発生ログ（PR #198 / Issue #190、2026-05-27 JST、`review` skill 経路）:

```text
$ kaji pr review 198 --request-changes --body-file -
failed to create review: GraphQL: Review Can not request changes on your own pull request (addPullRequestReview)
$ echo $?
1
```

呼び出し経路は `kaji_harness/cli_main.py:929-930` の `_handle_pr` GitHub 分岐: `args[0] == "review"` かつ `_has_approve_flag(args[1:])` が True のときのみ `_github_pr_review` に dispatch し、それ以外（`--request-changes` を含む）は `_forward_to_gh("pr", raw_args, repo=repo_override)` で `gh pr review` へ passthrough される。self-PR 検出は走らない。

この結果、`.claude/skills/review/SKILL.md` Step 6 の Changes Requested 経路（line 249, `kaji pr review [pr_id] --request-changes --body-file -`）が rc=1 で失敗し、`review` skill が要求する正式な Changes Requested review が作成できない。今回は `--comment` review に本文を投稿する手動 fallback で凌いだが、本来 `review-cycle.yaml` / `review-close.yaml` 経路では `review` → `pr-fix` → `pr-verify` ループに進めなくなる。

### Expected Behavior (EB)

`kaji pr review <pr> --request-changes` は provider と self/非 self に関係なく rc=0 を返し、後続 skill / workflow から「Changes Requested シグナルが残った」と観測可能であるべき。具体的には:

- self-PR (PR author == authenticated user) → `<!-- kaji-review: state=CHANGES_REQUESTED -->` marker 付き comment を Issue Comments API に投稿して rc=0
- 非 self-PR → 従来通り `gh pr review --request-changes` を委譲して rc=gh の rc（既存契約）

GitLab provider 側は既に同等の動作を marker comment 機構で実現している（[Issue #186 設計書 § 参照情報 1](./issue-186-fix-github-provider-kaji-pr-review-appro.md)）。GitHub 側は Issue #186 で `--approve` のみ対応済みで、`--request-changes` を対称に追加することで provider 間の挙動差を解消する。

skill / workflow 契約上の整合性:

- `.claude/skills/review/SKILL.md:249` — `kaji pr review [pr_id] --request-changes --body-file -` の rc=0 が Changes Requested 投稿の source of truth
- `.claude/skills/pr-verify/SKILL.md:244` — RETRY 判定時に同コマンドを呼ぶ。skill 側に provider 分岐や self-PR 分岐を持ち込まない方針は `--approve` 経路（Issue #186）と一貫
- `.kaji/wf/review-cycle.yaml` / `.kaji/wf/review-close.yaml` — `review` step ABORT で workflow が止まる構造のため、self-PR で `review` skill が rc=1 を踏むと cycle 全体が停止する

### 再現手順 (Steps to Reproduce)

1. 前提環境:
   - `.kaji/config.toml` で `[provider] type = "github"`、`[provider.github] repo = "owner/name"` が解決済み
   - `gh auth status` の login が当該 PR の author と一致（典型: 単独開発リポジトリ）
2. 任意の Issue で worktree + commit + PR を作成（`kaji issue start` → `kaji pr create` 等）
3. `kaji pr review <pr_id> --request-changes --body-file -` を実行（heredoc または stdin で body を渡す）
4. `gh pr review --request-changes` が呼ばれた時点で `GraphQL: Review Can not request changes on your own pull request` が stderr に出力され、rc=1

実発生事例: PR #198 / Issue #190（2026-05-27 JST、`review` skill 経路）。Issue #186 でも `--request-changes` を fallback 対象に含めなかった理由として「実発生待ち」と明示されており、本 Issue がその実発生に該当する。

### Root Cause

Issue #186 で導入した `_github_pr_review` (`kaji_harness/cli_main.py:810-880`) は `--approve` 専用 dispatcher として設計され、`_handle_pr` の routing (`cli_main.py:929-930`) は `_has_approve_flag(args[1:])` で `--approve` 経路のみを奪う。`--request-changes` 経路は routing 段でフィルタされず `_forward_to_gh("pr", raw_args, repo=repo_override)` 経由で素通しになるため、self-PR 検出が走らず GitHub API の 422 拒否 (`Can not request changes on your own pull request`) がそのまま rc=1 として伝播する。

GitHub REST API 仕様（`POST /repos/{owner}/{repo}/pulls/{N}/reviews` の `event` parameter [§ 参照情報 1](#参照情報primary-sources)）は `APPROVE` / `REQUEST_CHANGES` / `COMMENT` を取り、`APPROVE` / `REQUEST_CHANGES` を author 自身が投げた場合に 422 を返す。`COMMENT` は author でも許容される。実発生ログ（§ OB）で `REQUEST_CHANGES` の self-author 拒否文言を確定したため、Issue #186 設計書で「公開ページから直接は確認できなかった」と保留していた裏付けは本 Issue で完結した（参照: [Issue #186 設計書 § 参照情報 3](./issue-186-fix-github-provider-kaji-pr-review-appro.md)）。

同根の他壊れ箇所と scope 境界:

- **`--comment`**: GitHub API は author の `COMMENT` event を許容する（[`gh pr review` manual](#参照情報primary-sources) § 参照情報 6 / Issue #186 § 参照情報 10）。現行 `_handle_pr` (`cli_main.py:931-933`) は `--comment` を `_forward_to_gh` 経由で素通すため正しく動作する。**本 Issue では `--comment` 経路を改修しない**（routing 上 `_github_pr_review` に分岐しないため挙動完全不変）
- **`--approve`**: Issue #186 で実装済み。本 Issue では `_github_pr_review` 内部の state 切替で `--request-changes` を追加するが、`--approve` 経路の挙動 / 出力契約 / preflight は完全不変
- **`gh pr review` の `--approve` と `--request-changes` 同時指定**: `gh pr review` は両 flag を mutually exclusive として扱う（同時指定で usage error）。本 Issue でも argparse の `mutually_exclusive_group(required=True)` で `--approve` または `--request-changes` のいずれか必須として `EXIT_INVALID_INPUT` を返す（既存 `--approve` 単独契約は `parse_known_args` の `required=True` から `mutually_exclusive_group(required=True)` への移行で維持される。詳細は § 方針 § 2）

## インターフェース

### 入力（外部契約: 受理 flag セットは無変更、内部 routing と argparse のみ拡張）

外部から見た `kaji pr review` の引数体系・受理 flag セットは **無変更**。`--approve` / `--request-changes` / `--comment` / `--body` / `--body-file` / `-R/--repo` を含む既存 `gh pr review` の全 flag セットを引き続き受理する（routing 詳細は § 方針 § 1）。

| flag 経路 | 動作（修正後） |
|-----------|--------------|
| `--approve` 含む | 既存 `_github_pr_review` に分岐（Issue #186 で実装済み、本 Issue で挙動不変） |
| `--request-changes` 含む | **新規: `_github_pr_review` に分岐**（本 Issue で追加） |
| `--comment` 含む | **従来通り `gh pr review` へ passthrough**（無変更） |
| flag 無し / その他 | **従来通り `gh pr review` へ passthrough**（無変更） |

`_github_pr_review` が argparse で受理する flag（拡張後）:

- `<pr_id>`: ASCII decimal の PR 番号（`nargs="?"` のまま。URL/branch target / current branch 解決は passthrough に fallback）
- `--approve` / `--request-changes`: **mutually_exclusive_group(required=True)** で 1 つ必須
  - 短形 alias: `-a` (`--approve` の `gh pr review` 正式 short flag、`gh pr review --help` で確認)、`-r` (`--request-changes` の `gh pr review` 正式 short flag、同上)
- `--body BODY` / `--body-file PATH`（相互排他、`_read_body_arg` 経由）
- `-R/--repo`（既存通り、user 明示は `repo_override` より優先）

`_has_request_changes_flag` を新規追加し、`_handle_pr` の routing 行を `_has_approve_flag(args[1:]) or _has_request_changes_flag(args[1:])` に拡張する。`_has_approve_flag` は無変更（`_has_request_changes_flag` を対称形で並べる）。

### 出力（変更点と契約差分の精緻化）

| ケース | 既存挙動 (GitHub mode) | 修正後 |
|--------|----------------------|--------|
| Self-PR で `--request-changes` | `gh pr review --request-changes` 委譲 → rc=1 (`Can not request changes on your own pull request`) | `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<marker+body>` で `<!-- kaji-review: state=CHANGES_REQUESTED -->` 付き comment を投稿 → 投稿 rc=0 で `_handle_pr` rc=0。`gh pr review` は呼ばない。投稿 rc≠0 → `EXIT_RUNTIME_ERROR` |
| 非 self-PR で `--request-changes` | `gh pr review --request-changes` 委譲 → rc=gh の rc | 委譲前に `gh pr view --json author` + `gh api user` の preflight が走り、両 API 成功 + author≠me で従来 `gh pr review --request-changes` 委譲。**rc は不変（0 / gh の rc を素通し）だが、新規 preflight 失敗経路が増える** |
| 非 self-PR で `--request-changes` の preflight 失敗 | （無し、新規経路） | `gh pr view --json author` または `gh api user` が rc≠0 → `EXIT_RUNTIME_ERROR` を返し `gh pr review` は呼ばない（fail-loud、`--approve` 経路と完全対称） |
| `--approve` (self / 非 self) | Issue #186 実装通り | **完全不変** |
| `--comment` | `gh pr review --comment` 委譲 | **不変**（routing で `_github_pr_review` に分岐せず passthrough） |
| flag 無し / その他 | passthrough | **不変** |

side effect として self-PR + `--request-changes` 経路では Issue/PR 上に以下の comment が 1 件追加される（GitLab side / `--approve` 経路と完全に同形式の marker）:

```
<!-- kaji-review: state=CHANGES_REQUESTED -->
<user-supplied body, 空可>
```

`<!-- ... -->` HTML コメントは GitHub UI 上で不可視のため、PR 体験を壊さない。

### 後方互換性

- 既存 `_github_pr_review` の挙動: `--approve` 経路は完全不変（preflight / marker 付き POST / 委譲 / 失敗ハンドリングはすべて同一バイナリ動作を維持）
- 既存 `_has_approve_flag` の挙動: 不変（新規追加は `_has_request_changes_flag`）
- `--comment` / flag 無し / URL target / current branch 解決経路: 不変
- 既存テスト `tests/test_cli_main.py` の `TestHasApproveFlag` / `TestGithubPrReviewHandler` / `TestGithubPrReviewRouting`: 既存ケースの assert は無変更（新規ケースを追加するのみ）

### 使用例

```bash
# 1. author 本人による Changes Requested (review skill / pr-verify skill 経路の代表ケース)
kaji pr review 199 --request-changes --body-file - <<'EOF'
## 初回コードレビュー結果
### 指摘事項 (Must Fix)
- [ ] point 1: ...
EOF
# → rc=0
# → issue comments API に `<!-- kaji-review: state=CHANGES_REQUESTED -->` + 本文 が 1 件 POST される
# → `gh pr review` は呼ばれない

# 2. 第三者 reviewer (従来挙動、preflight のみ新規追加)
kaji pr review 199 --request-changes --body-file - <<'EOF'
LGTM ではない、ここを修正してほしい
EOF
# → preflight (gh pr view --json author / gh api user) で author != me を確認
# → `gh pr review --request-changes --body-file -` を委譲、rc=0

# 3. `--approve` 経路（Issue #186 で実装済み、本 Issue で挙動不変）
kaji pr review 199 --approve --body "LGTM"
# → 既存通り marker (state=APPROVED) + 本文 POST or gh 委譲

# 4. `--comment` 経路（無変更、従来通り passthrough）
kaji pr review 199 --comment --body "merge前の最終確認お願いします"
# → `_handle_pr` は `--approve` / `--request-changes` flag を含まないため _github_pr_review に分岐せず
# → `gh pr review --comment --body ...` を素通し、rc=gh の rc

# 5. `--approve` と `--request-changes` 同時指定（誤入力）
kaji pr review 199 --approve --request-changes --body "x"
# → routing 段で _has_approve_flag が True のため _github_pr_review に分岐
# → argparse mutually_exclusive_group で usage error
# → EXIT_INVALID_INPUT (rc=2)
```

## 制約・前提条件

- 修正対象は GitHub provider 経路 (`kaji_harness/cli_main.py` の `_handle_pr` GitHub 分岐 + `_github_pr_review` + `_has_request_changes_flag`) のみ。GitLab / Local provider 経路は無変更
- `build_kaji_review_marker` / `_KAJI_REVIEW_MARKER_PREFIX` / `_REVIEW_STATES_VALID` (`kaji_harness/providers/github.py:39-56`) は Issue #186 で provider 中立的に既設置済み。本 Issue では provider 中立モジュールへの移管はせず、既存 import (`cli_main.py:34`) を流用する。理由: Issue #186 § 制約・前提条件と同じく、移管はファイル境界を新設する Scope 拡張になるため別 Issue 推奨
- self-PR 判定は `gh api user --jq .login` (authenticated user) と `gh pr view <pr> --json author --jq .author.login` (PR author) の文字列一致で行う。Issue #186 の `--approve` 経路と同じ preflight ロジックを流用するため、追加 scope は要求しない
- marker comment 投稿は `gh api --method POST repos/<repo>/issues/<pr>/comments -f body=<text>` を使う（`--approve` 経路と完全同一の `_gh_post_issue_comment_silent`）
- self-PR + `--request-changes` 経路の stdout 出力は `_gh_post_issue_comment_silent` の `capture_output=True` で抑止し、本コマンドの stdout contract は「空 + rc のみ」とする（`--approve` 経路と同一契約）
- `--body` / `--body-file` 同時指定は既存 `_read_body_arg` 経由で `ValueError` → `EXIT_INVALID_INPUT`
- `--approve` と `--request-changes` 同時指定は argparse `mutually_exclusive_group(required=True)` で usage error → `EXIT_INVALID_INPUT`
- 非 self-PR 経路で `--request-changes` の body が空の場合、`gh pr review --request-changes` は GitHub API 側で 422 (`Body cannot be blank`) を返す。これは既存契約（gh の rc 素通し）と整合させ、kaji 側で空 body validation は追加しない。skill 側 (`review` / `pr-verify`) は body 必須の運用契約を持つため実用上問題にならない
- 単独開発前提（author == authenticated user）が主シナリオ。CI から bot が `--request-changes` を投げるケース（authenticated user ≠ PR author）も従来 `gh pr review` 委譲経路で対応

## 変更スコープ

- `kaji_harness/cli_main.py`:
  - 新規 `_has_request_changes_flag(rest: list[str]) -> bool` を追加（`_has_approve_flag` の対称形、`--request-changes` / `--request-changes=` / `-r` を検出。`--` 以降は positional 扱い）
  - `_github_pr_review` 内の argparse を拡張: `--approve` を `mutually_exclusive_group(required=True)` 化し `--request-changes` / `-r` を並列追加。state 判定変数 `state = "APPROVED" if ns.approve else "CHANGES_REQUESTED"` を導入し `build_kaji_review_marker(state)` に渡す
  - `_github_pr_review` 内の非 self-PR 委譲経路: `gh_args = ["review", ns.pr_id, "--approve" if ns.approve else "--request-changes"]` に拡張。body 付与ロジックは既存通り
  - `_handle_pr` の routing 行 (`cli_main.py:929`) を `_has_approve_flag(args[1:]) or _has_request_changes_flag(args[1:])` に拡張
  - 関数 docstring を `--approve` 専用 → `--approve` / `--request-changes` 兼用に更新
- `tests/test_cli_main.py`:
  - 既存 `TestHasApproveFlag` 隣に新規 `TestHasRequestChangesFlag` クラスを追加（純粋関数の単体テスト、subprocess 不要）
  - 既存 `TestGithubPrReviewHandler` に `--request-changes` 系の test method を追加（self-PR で marker post / 非 self-PR で `gh pr review --request-changes` 委譲 / preflight 失敗 / 空 body marker / `--approve` + `--request-changes` 同時指定エラー）
  - 既存 `TestGithubPrReviewRouting` に `--request-changes` 経路の routing test を追加（`_github_pr_review` に dispatch / `--comment` は passthrough のまま）
- `docs/cli-guides/github-mode.md`: `kaji pr review` セクションで `--approve` self-PR fallback の記述に `--request-changes` を併記。`--comment` は引き続き passthrough である旨を明示
- GitLab provider / Local provider / skill / workflow YAML: **無改修**

GitLab / Local 経路、`review` / `pr-verify` skill、`.kaji/wf/review-cycle.yaml` / `review-close.yaml` は変更しない。

## 方針

### 1. dispatcher 分岐（`--comment` / flag 無し非破壊 routing）

`_handle_pr` 内、GitHub 分岐の `_has_approve_flag` チェックを `_has_approve_flag(args[1:]) or _has_request_changes_flag(args[1:])` に拡張する。`--approve` / `--request-changes` 以外（`--comment` / flag 無し / その他）は従来通り `gh pr review` へ passthrough し、既存契約を完全保存する:

```python
# _handle_pr (GitHub 分岐) の置換イメージ
if args and args[0] == "review" and (
    _has_approve_flag(args[1:]) or _has_request_changes_flag(args[1:])
):
    return _github_pr_review(args[1:], repo_override=repo_override)
if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
    return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
return _forward_to_gh("pr", raw_args, repo=repo_override)


def _has_request_changes_flag(rest: list[str]) -> bool:
    """``rest`` 中に ``--request-changes`` / ``--request-changes=...`` / ``-r`` が含まれるかを pre-scan する。

    ``gh pr review`` の ``-r`` は ``--request-changes`` の正式 short alias（``gh pr review --help``）。
    ``--`` 以降は positional 扱いし無視する。``_has_approve_flag`` と完全対称の構造。
    """
    for tok in rest:
        if tok == "--":
            return False
        if tok == "--request-changes" or tok.startswith("--request-changes=") or tok == "-r":
            return True
    return False
```

設計判断:

- **`_has_request_changes_flag` を独立関数として追加**: `_has_approve_flag` と並列に新規追加。共通実装に括り出す（例: `_has_review_state_flag(rest, flags)`）案もあるが、Scope 最小化と Issue #186 設計との対称性を優先し、別関数で並べる。将来 `--comment` も同パターンで fallback 対象になった時点でリファクタを検討（別 Issue）
- **routing の `or` 拡張は両方 True でも安全**: `--approve` と `--request-changes` 同時指定 (`-a -r` 等) は両 pre-scan が True を返し `_github_pr_review` に分岐するが、argparse の `mutually_exclusive_group(required=True)` で内側で `EXIT_INVALID_INPUT` に倒れる。silent fallthrough は発生しない
- **pre-scan の副作用**: `args[1:]` を最大 2 回 (`_has_approve_flag` / `_has_request_changes_flag`) スキャンし、`_github_pr_review` 内 argparse でさらに 1 回。args 件数は実用上数個レベルで重複コスト無視可能

### 2. `_github_pr_review` の構造拡張（疑似コード）

`--approve` / `--request-changes` 兼用 dispatcher。Issue #186 で導入した構造を最小拡張する。

```python
def _github_pr_review(rest: list[str], *, repo_override: str | None) -> int:
    """``kaji pr review <pr_id> --approve|--request-changes`` 専用 dispatcher（GitHub mode）。

    self-PR (PR author == authenticated user) では ``gh pr review --approve``
    / ``--request-changes`` が GitHub API ``Can not approve your own pull request``
    / ``Can not request changes on your own pull request`` で 422 拒否されるため、
    ``<!-- kaji-review: state=APPROVED|CHANGES_REQUESTED -->`` marker 付き comment
    を Issue comments API に投稿することで review シグナルを表現する。

    非 self-PR では従来通り ``gh pr review --approve|--request-changes`` を委譲する。
    """
    p = argparse.ArgumentParser(prog="kaji pr review", add_help=True)
    p.add_argument("pr_id", type=str, nargs="?", default=None)
    state_group = p.add_mutually_exclusive_group(required=True)
    state_group.add_argument("-a", "--approve", action="store_true")
    state_group.add_argument("-r", "--request-changes",
                             dest="request_changes", action="store_true")
    p.add_argument("-b", "--body", default=None, type=str)
    p.add_argument("-F", "--body-file", dest="body_file", default=None, type=str)
    p.add_argument("-R", "--repo", dest="repo", default=None, type=str)
    ns, unknown = p.parse_known_args(rest)

    # self-PR fallback は ASCII decimal PR 番号 + 既知 flag のみで成立する
    if ns.pr_id is None or not _is_ascii_decimal(ns.pr_id) or unknown:
        return _forward_to_gh("pr", ["review", *rest], repo=repo_override)

    effective_repo_override = ns.repo if ns.repo else repo_override
    try:
        body = _read_body_arg(ns.body, ns.body_file)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    if body is None:
        body = ""

    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo(override=effective_repo_override)
    if repo is None:
        sys.stderr.write(...)  # 既存 error message
        return EXIT_RUNTIME_ERROR

    # preflight: self-PR 判定（既存 --approve 経路と同一）
    pr_author = _gh_capture_value(
        ["pr", "view", ns.pr_id, "--repo", repo, "--json", "author",
         "--jq", ".author.login"]
    )
    if pr_author is None:
        return EXIT_RUNTIME_ERROR
    me = _gh_capture_value(["api", "user", "--jq", ".login"])
    if me is None:
        return EXIT_RUNTIME_ERROR
    is_self = pr_author == me

    # state 判定（追加箇所）
    state = "APPROVED" if ns.approve else "CHANGES_REQUESTED"
    marker = build_kaji_review_marker(state)
    marked_body = f"{marker}\n{body}"

    if is_self:
        return _gh_post_issue_comment_silent(repo=repo, pr_id=ns.pr_id, body=marked_body)

    # 非 self-PR: 従来通り gh pr review に委譲（marker は付けない）
    flag = "--approve" if ns.approve else "--request-changes"
    gh_args = ["review", ns.pr_id, flag]
    if body:
        gh_args.extend(["--body", body])
    return _forward_to_gh("pr", gh_args, repo=repo)
```

設計判断:

- **`mutually_exclusive_group(required=True)`**: 既存 `_github_pr_review` は `--approve` を `required=True` で個別追加していた（`cli_main.py:823`）。本 Issue で `--request-changes` を並列追加する際、両方 `required=True` にすると argparse が両方必須化してしまうため、`add_mutually_exclusive_group(required=True)` に変更する。`_handle_pr` 段の routing で `_has_approve_flag or _has_request_changes_flag` の少なくとも一方が True と保証されているため、argparse 到達時点で `required=True` 違反は通常発生しない（routing が True を返すのに argparse が両方 False と判定するケースは pre-scan の差異で起こりうるため、明示的に `required=True` で守る）
- **`dest="request_changes"`**: argparse は `--request-changes` を自動的に `request_changes` に変換するが、明示する。`ns.request_changes` は本 dispatcher 内では参照せず、`ns.approve` の真偽で state を分岐する（mutually exclusive なので `not ns.approve ⇒ ns.request_changes == True`）
- **passthrough fallback の発火条件**: `ns.pr_id is None or not _is_ascii_decimal(ns.pr_id) or unknown` の判定は既存通り。`--request-changes` 経路でも URL target / branch target / 未認識 flag が来た場合は `_forward_to_gh("pr", ["review", *rest], repo=repo_override)` で素通しに倒れ、self-PR fallback はかけない（既存契約を保つ）
- **preflight の対称性**: `--approve` / `--request-changes` のどちらでも GitHub API は author を拒否するため、preflight ロジックは完全共通。本 Issue で preflight 順序や fail-loud 規則は変更しない
- **flag の選択ロジック**: 非 self-PR 委譲時の `gh_args` 構築で `flag = "--approve" if ns.approve else "--request-changes"` とする。short alias (`-a`/`-r`) は argparse が long form に正規化するため `ns.approve`/`ns.request_changes` で受ければ十分

### 3. 失敗ハンドリング

`--approve` 経路（Issue #186）と完全対称:

| 失敗箇所 | 動作 |
|---------|------|
| `--approve` と `--request-changes` 同時指定 | argparse `mutually_exclusive_group` usage error → `EXIT_INVALID_INPUT` |
| `--approve` / `--request-changes` のいずれも未指定 | routing 段で `_github_pr_review` に分岐しないので発生しない（`_handle_pr` が `_forward_to_gh` に流す） |
| `gh` 未インストール | 既存 `_GH_MISSING_GUIDANCE` を stderr → `EXIT_RUNTIME_ERROR` |
| `gh pr view --json author` が rc≠0 | stderr 中継、`EXIT_RUNTIME_ERROR`（preflight 失敗 fail-loud、--approve 経路と同一） |
| `gh api user --jq .login` が rc≠0 | 同上 |
| Self-PR + `--request-changes` の `gh api POST issues/<N>/comments` が rc≠0 | stderr 中継、`EXIT_RUNTIME_ERROR`（marker 投稿失敗は Changes Requested 不成立として扱う） |
| 非 self-PR + `--request-changes` の `gh pr review --request-changes` が rc≠0 | 既存挙動と同じく rc をそのまま返す（body 空による 422 もここに含む） |

self-PR 判定で「どちらかの取得に失敗 → 安全側に倒して non-self として扱い `gh pr review` 委譲」のような silent fallthrough は採用しない（`--approve` 経路と同一規約）。

### 4. 既存テストとの整合（Issue #186 で確立した境界の継承）

Issue #186 設計書 § 5 で確立したテスト境界（`TestHasApproveFlag` / `TestGithubPrReviewHandler` / `TestGithubPrReviewRouting`）を踏襲する:

- `TestHasApproveFlag` (`tests/test_cli_main.py` 既存) — 無変更
- `TestHasRequestChangesFlag` — 新規。`_has_request_changes_flag` の純粋関数テスト。subprocess 不要
- `TestGithubPrReviewHandler` — 既存に `--request-changes` 系 test method を追加（`testing-convention.md` § patch スコープ表 § dispatch/provider 結合 の禁止対象に該当しないため、`cli_main.subprocess.run` namespace patch 使用可）
- `TestGithubPrReviewRouting` — 既存に `--request-changes` 経路の routing test を追加（`_github_pr_review` / `_forward_to_gh` を直接 stub し subprocess は走らせない）

非実在ファイルへの言及は行わない（Issue #186 の verify cycle で確認済みの規約を継承）。

## テスト戦略

### 変更タイプ
- 実行時コード変更（GitHub provider dispatcher 経路の挙動を変更）

### 実行時コード変更の場合

#### Small テスト

`testing-convention.md:135-143` の patch スコープ表に厳密に従い、`_handle_pr` 経路では `cli_main.subprocess.run` の namespace patch を使わない。Issue #186 で確立した 3 クラス構成を踏襲し、新規 1 クラス + 既存 2 クラスへの test method 追加で網羅する。

##### Small クラス 1 (新規): `TestHasRequestChangesFlag` — 純粋関数の単体テスト

対象: `_has_request_changes_flag(rest: list[str]) -> bool`。subprocess を一切呼ばないため mock 不要。

検証観点（`TestHasApproveFlag` と完全対称）:
- `["--request-changes"]` → True、`["--request-changes=true"]` → True、`["-r"]` → True
- `["--approve"]` / `["-a"]` / `["--comment"]` / `[]` → False
- `["--", "--request-changes"]` → False（`--` 以降は positional 扱い）
- `["199", "--request-changes", "--body", "x"]` → True（位置に依存しない）
- `["199", "--body", "--request-changes-not-real"]` → False（完全一致 or `--request-changes=` prefix のみ true）
- `["199", "-rx"]` → False（`-r` 単独 short flag のみ。short flag bundling は対象外）

##### Small クラス 2 (既存): `TestGithubPrReviewHandler` — handler 直接呼び出し

既存 `--approve` 系 test method は無変更で残し、以下を追加:

検証観点:
- **bug 再現テスト（必須・Red 化）**: PR author == authenticated user の mock で `--request-changes` を呼ぶ:
  - 修正前（`_has_request_changes_flag` 未導入 / `_github_pr_review` が `--request-changes` を受理しない時）→ `_handle_pr` 経由で `gh pr review --request-changes` 委譲を再現 → rc=1 (`Can not request changes on your own pull request` stderr mock)
  - 修正後（`_github_pr_review` 直接呼び出し）→ `gh pr review` 呼び出し 0 回、`gh api --method POST repos/owner/repo/issues/199/comments -f body=<marker+body>` 1 回、rc=0
  - assert: POST `-f body=` 値の先頭行が `build_kaji_review_marker("CHANGES_REQUESTED")` (= `<!-- kaji-review: state=CHANGES_REQUESTED -->`) と一致
- **非 self-PR `--request-changes` 委譲**: PR author=`"alice"`, authenticated user=`"bob"` の mock で `gh pr review --request-changes` が委譲 → rc=0。`gh api ... POST issues/.../comments` が **0 回**（call_args_list を assert）
- **mutually exclusive 違反**: `["199", "--approve", "--request-changes", "--body", "x"]` で argparse SystemExit → rc=2 / `EXIT_INVALID_INPUT`
- **空 body**: `--body` も `--body-file` も未指定の self-PR `--request-changes` で marker のみの body (`"<marker>\n"`) が POST されること
- **失敗ハンドリング（preflight fail-loud、--approve 経路と対称）**: `gh pr view --json author` rc≠0 → `EXIT_RUNTIME_ERROR` / `gh api user` rc≠0 → 同上 / self-PR + `gh api POST .../comments` rc≠0 → `EXIT_RUNTIME_ERROR`
- **stdout 抑止契約**: self-PR + `--request-changes` 経路で `gh api POST` 呼び出しが `subprocess.run(..., capture_output=True)` で行われていることを `call_args[1]["capture_output"] is True` で assert
- **既存 `--approve` 経路の回帰防止**: 既存 test method は assert 値を変更しない（mock シーケンスや POST body の marker は `APPROVED` のまま）。`mutually_exclusive_group(required=True)` への変更が `--approve` 単独入力を許容することを再 assert（routing 経由で `--approve` のみ渡した場合に従来通り rc=0）

##### Small クラス 3 (既存): `TestGithubPrReviewRouting` — `_handle_pr` routing 振り分け

既存 `--approve` 系 test method は無変更で残し、以下を追加:

検証観点:
- **`--request-changes` 経路 → `_github_pr_review` に dispatch**: `_github_pr_review` 1 回呼び出し、`_forward_to_gh` 0 回（`-r` short alias も同じく dispatch される）
- **`--approve` + `--request-changes` 同時指定経路**: `_has_approve_flag` または `_has_request_changes_flag` のどちらかが True で `_github_pr_review` に分岐すること（実 mutex error は handler 層で発生するため routing 層では発生しない）
- **`--comment` 経路 → 従来通り `_forward_to_gh` passthrough**: 既存 test を維持（無変更）
- **flag 無し `kaji pr review 199` → 従来通り passthrough**: 既存 test を維持（無変更）

#### Medium テスト

不要。理由（`docs/dev/testing-convention.md` § 「不要理由」§ 4 条件）:

- ファイル I/O / DB 結合は本変更に含まれない。subprocess を介す `gh` 呼び出しは Small で `cli_main.subprocess.run` を mock 化して観測する設計
- worktree / config discovery の medium 検証は既存 `tests/test_dispatcher.py:788-901` (`TestForwardToGhRepoInjection`) が `_handle_issue` 経由でカバー済み。本 Issue の差分（`_has_request_changes_flag` の routing 追加 + `_github_pr_review` の state 拡張）は handler 内部ロジックであり config discovery / provider 解決経路には影響しない
- testing-convention.md § 4 条件: ① 独自ロジック（pre-scan + state 分岐 + marker post）は Small で完結 ② 想定不具合パターン（mutex 違反 / `--comment` 破壊 / preflight 失敗 silent fallthrough / marker body 不正）は Small mock で捕捉 ③ Medium を増やしても assert 対象に新規シグナルが増えない ④ 不要理由を本セクションで説明 — を満たす

#### Large テスト

不要。理由:

- 「self-PR で GitHub API が `REQUEST_CHANGES` を拒否する」仕様は GitHub 側の固定値（実発生ログ § OB で確定）であり、kaji 側が観測すべき動的振る舞いではない
- `make test-large-forge` に追加すると `gh auth` ユーザ名と test PR の author を一致させる前提が CI で再現困難（Issue #186 と同じ事情）
- testing-convention.md § 4 条件: ① self-PR detection / marker comment 構成は Small mock で 100% カバー ② GitHub API 仕様変更は kaji 側で検知すべき責務でなく `review` / `pr-verify` 経由で発見すれば良い ③ Large テストを足しても CI 上の再現が困難で false negative リスクが増す ④ 不要理由を本セクションで説明 — を満たす

### 恒久回帰テストと変更固有検証の切り分け

恒久回帰テスト（Small）を Required。本変更はランタイムコードの分岐追加であり、`docs-only / metadata-only / packaging-only` には該当しない。変更固有の一時検証は不要（mock 化された Small で再現可能）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | provider 抽象や design pattern の新規選定ではなく、Issue #186 で導入した marker 機構の対称適用 |
| docs/ARCHITECTURE.md | なし | provider 境界 / dispatcher 構造は不変 |
| docs/dev/ | なし | workflow / skill 契約は不変（`review` / `pr-verify` skill は無改修） |
| docs/reference/ | なし | python style / type-hints / logging 規約は不変 |
| docs/cli-guides/github-mode.md | **あり** | `kaji pr review` セクションで `--approve` self-PR fallback の記述に `--request-changes` を併記。`--comment` は引き続き passthrough である旨を明示 |
| docs/cli-guides/gitlab-mode.md | なし | GitLab 側は無改修 |
| docs/cli-guides/local-mode.md | なし | local provider は `kaji pr` 系を `EXIT_INVALID_INPUT` で拒否しているため影響なし |
| CLAUDE.md | なし | 規約変更なし |

`docs/cli-guides/github-mode.md` の追記は実装 PR に含める（doc / code が同期するため別 Issue 化しない）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 1. GitHub REST API: Create / Submit a review for a pull request | https://docs.github.com/en/rest/pulls/reviews?apiVersion=2022-11-28#create-a-review-for-a-pull-request | `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` の "event" parameter は `APPROVE` / `REQUEST_CHANGES` / `COMMENT` を取る。author 自身が `APPROVE` / `REQUEST_CHANGES` を投げた場合は generic `422 Validation failed`。Issue #186 設計で「公開ページから直接は確認できなかった」と保留していた `REQUEST_CHANGES` の self-author 拒否文言は、本 Issue § OB の実発生ログ (`Can not request changes on your own pull request`) で完結 |
| 2. GitHub REST API: Create an issue comment | https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28#create-an-issue-comment | `POST /repos/{owner}/{repo}/issues/{issue_number}/comments`。PR の会話 comment は Issue comments API を共有する仕様。author 制約はなく self-PR でも POST 可能。本 Issue の marker fallback はこの endpoint を使用（Issue #186 の `--approve` 経路と同一） |
| 3. GitHub CLI manual: `gh pr review` | https://cli.github.com/manual/gh_pr_review | `--approve` / `--comment` / `--request-changes` の 3 flag を正式 option として定義。short alias は `-a` / `-c` / `-r`。`--comment` は author でも GitHub API が許容するため現行 `_handle_pr` passthrough が動作している根拠。`-r` short alias を `_has_request_changes_flag` で検出する根拠 |
| 4. Issue #186 設計書（先行 Issue） | [`draft/design/issue-186-fix-github-provider-kaji-pr-review-appro.md`](./issue-186-fix-github-provider-kaji-pr-review-appro.md) | 本 Issue が拡張する `_github_pr_review` / `_has_approve_flag` の設計根拠と、`--request-changes` を scope 外とした明示的な保留判断（§ Root Cause § scope 境界 / § 方針 § 1）。本 Issue はその保留を実発生ログで解除し対称ケースを追加 |
| 5. marker 仕様 `build_kaji_review_marker` | `kaji_harness/providers/github.py:39-56` | `_KAJI_REVIEW_MARKER_PREFIX = "<!-- kaji-review: state="` / `_REVIEW_STATES_VALID = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}` / `f"{prefix}{state}{suffix}"`。本 Issue では `state="CHANGES_REQUESTED"` を渡して同関数を再利用 |
| 6. 既存 `_github_pr_review` / `_has_approve_flag` 実装 | `kaji_harness/cli_main.py:743-880` / `cli_main.py:929-933` | 本 Issue が拡張する関数と routing 行の現行コード。新規 `_has_request_changes_flag` の対称性、`mutually_exclusive_group(required=True)` への移行、state 分岐の追加箇所を本実装に対する diff として記述する根拠 |
| 7. `review` skill PASS / Changes Requested 条件 | `.claude/skills/review/SKILL.md:248-288` | `kaji pr review [pr_id] --request-changes --body-file -` を rc=0 で完了することが Changes Requested 投稿の source of truth。skill 側は provider 種別 / self-PR を判定しないため、GitHub provider 側で吸収する必要がある |
| 8. `pr-verify` skill RETRY 条件 | `.claude/skills/pr-verify/SKILL.md:243-280` | `pr-verify` の RETRY 判定時に `kaji pr review [pr_id] --request-changes --body-file -` を呼ぶ。skill 側に provider / self-PR 分岐を持ち込まない方針は `--approve` 経路と一貫 |
| 9. testing-convention.md § テスト省略の 4 条件 + § `subprocess.run` patch スコープ | [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) § テスト戦略の原則 / § `subprocess.run` patch スコープ (lines 133-146) | Medium / Large 省略の 4 条件と、`_handle_pr` 経路での `cli_main.subprocess.run` namespace patch 禁止規定。本 Issue の Small テスト構成はこれに従い、handler 直接呼び出し（patch 許容）と routing stub（patch 不使用）を分離 |
| 10. shared_skill_rules.md § auto close keyword 回避 | [`docs/dev/shared_skill_rules.md`](../../docs/dev/shared_skill_rules.md) § auto close keyword 回避 | 設計書・コメント・テスト assert 文字列内で `Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#` + 数字を書かない規約。本設計書では Issue 参照を `Issue #186` の `#186` 形式（close keyword 非隣接）で記述し違反しない |
