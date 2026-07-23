# [設計] self-PR の未解決 review thread で issue-close が条件付き admin merge する

Issue: #368

## 概要

GitHub provider の `issue-close` に、通常 merge が base branch policy で block された後の
**fail-closed な条件付き admin merge recovery** を追加する。self-PR marker（`kaji-review:
state=APPROVED`）を review 判定の source of truth とし、安全条件をすべて満たす場合のみ
`kaji pr merge <branch> --admin`（= `gh pr merge --admin --merge`）で merge する。Codex review
thread の Resolve も `--auto` fallback も要求しない。

## 背景・目的

### Observed Behavior（OB）

run `260723023106`（Issue #331 / PR #366）の close step verdict（`.kaji-artifacts/331/runs/
260723023106/steps/close/attempt-001/verdict.yaml`）に記録された実世界障害:

```text
kaji pr merge chore/331
X Pull request apokamo/kaji#366 is not mergeable: the base branch policy prohibits the merge

kaji pr merge chore/331 --auto
GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)

status: ABORT
reason: PR #366 が GitHub の base branch policy によりブロックされ、auto-merge も
        リポジトリ設定で無効なため、マージおよび Issue クローズを実行できない
```

verdict evidence の一次観測: `mergeStateStatus=BLOCKED`, `mergeable=MERGEABLE`,
`reviewDecision` 空, `statusCheckRollup` 空配列。結果として PR #366 は OPEN、Issue #331 は
OPEN、worktree と `chore/331` ブランチは未削除のまま停止した。

### Expected Behavior（EB）

単独ユーザーの self-PR では、Issue #186 / #199 で確立した `<!-- kaji-review: state=... -->`
marker を review 判定の source of truth とする（Issue #367 owner 調査コメント 2026-07-23
08:04:05Z が本方針を確定）。Codex review thread は修正・返信・`pr-verify` PASS 後も Resolve を
要求せず、次の安全条件をすべて満たす場合に限り管理者 bypass で merge commit を作成する。

1. PR author と authenticated user が一致する self-PR
2. 現在 HEAD に対応する最新の `kaji-review` marker が `APPROVED`（stale でない）
3. PR が `MERGEABLE`（block 原因が repository ruleset / branch policy）
4. authenticated user が repository admin（= ruleset bypass 権限）を持つ

この条件では `kaji pr merge <branch> --admin`（= `gh pr merge --admin --merge`）を用いる。review
thread の Resolve や auto-merge 有効化は要求しない。いずれかの条件を満たさない、または判定に
必要な情報の取得に失敗した場合は admin merge せず ABORT する（fail-closed）。

### 目的

close step が「レビュー修正・検証が完了しているのに未解決 thread のせいで ABORT する」状態を
解消し、merge・Issue close・cleanup を完遂させる。同時に、admin bypass という危険な操作を
prose 即興ではなく決定的な安全条件判定に従わせる（ADR 008 決定 3: cross-skill / 安全契約は
SKILL.md 散文でなく CLI / harness 層に置く）。

## 再現手順（steps-to-reproduce）

1. `required_review_thread_resolution=true`、admin role bypass 有効（`current_user_can_bypass=
   always`）、repository auto-merge 無効な GitHub repository で self-PR を作成する。
2. Codex inline review 指摘を修正して thread 内へ返信し、`pr-verify` を PASS させ、最新 HEAD
   commit 後に `kaji pr review <pr> --approve`（self-PR fallback）で `kaji-review:
   state=APPROVED` marker を投稿する。thread の `isResolved` は `false` のままにする。
3. `issue-close` を実行する。
4. 現行実装では `kaji pr merge [branch_name]`（非 admin）が base branch policy で失敗し、close
   agent は文書化されていない `gh pr merge --auto` を即興で試し、auto-merge 無効を理由に ABORT
   する（= OB）。

実世界再現は run `260723023106` / PR #366 に保存済みで、bug.md § escape clause の「実ログに
よる実装前 Red 代替」として用いる。

## 根本原因（Root Cause）

### なぜ壊れているか

現行 `.claude/skills/issue-close/SKILL.md`（github provider）Step 3 は次の 1 行のみで、merge が
block された後の **recovery 分岐が存在しない**:

```text
kaji pr merge [branch_name]
```

`kaji pr merge` は `kaji_harness/commands/pr.py::_forward_to_gh` で `gh pr merge <branch>
--merge`（**非 admin**）に転送される。ruleset `required_review_thread_resolution=true` が
enforce され unresolved thread が残っていると、非 admin merge は base branch policy で BLOCKED に
なる。SKILL.md に recovery が書かれていないため、agent は未文書の `gh pr merge --auto` を即興し、
repository auto-merge 無効で ABORT した。

CLI 側は既に正しい: `_FORGE_METHOD_FLAGS = {"--merge", "--squash", "--rebase"}` は `--admin` を
含まないため、`kaji pr merge <branch> --admin --squash` は `gh pr merge <branch> --admin --merge`
へ転送される（本設計で実測確認済み）。つまり**必要な merge 機構は既に存在し、欠けているのは
「いつ admin merge してよいかの安全条件判定」と「それを issue-close へ文書化すること」**である。

### いつから壊れているか

`required_review_thread_resolution` を含む ruleset `main_ruleset`（id `19180677`）が適用された
時点から、unresolved thread を持つ self-PR の自動 close が壊れる。issue-close の当該 Step 3 は
recovery 分岐を最初から持たないため、「特定コミットからの degradation」ではなく「ruleset
enforcement 環境で顕在化した設計欠落」である。

### PR #365 と #366 で挙動が分かれた根本原因

両 PR は同条件（self-PR / `isResolved=false` / APPROVED marker 済み）だが結果が分かれた。差は
PR 側ではなく **merge 実行パスの結果**にある:

- 現行 issue-close は非 admin merge のみで、これは PR が merge 時点で policy-block されていない
  場合に限り成功する。
- PR #365 は block されない経路で merge された（Issue #367 は「self-PR marker 承認後に merge
  済み」とだけ記録。Issue #368 § 回避策が記す管理者手動 `gh pr merge <pr> --admin --merge`、
  または #365 merge 時点で thread-resolution 要件が未 enforce だった可能性）。
- PR #366 は enforce 済み ruleset で block され、recovery 分岐が無いため agent が `--auto` を
  即興し ABORT した。

したがって根本原因は「PR 間の差」ではなく「issue-close が非 admin merge の成否に暗黙依存し、
block 時の決定的 recovery を持たないこと」である。#365 が具体的にどの経路で merge されたかは
残存 run artifact（#331/#366 分のみ保持）からは確定できないが、**この不確実性は修正に影響しない**
（修正は block 時に決定的 recovery を与えるものであり、過去の #365 merge 経路に依存しない）。

### 同根の close / merge 経路の調査（Issue 完了条件「同根経路調査」）

| 経路 | 判定 | 根拠 |
|------|------|------|
| `issue-close/SKILL.md` github Step 3（`kaji pr merge`）| **対象** | 本 bug の発生箇所 |
| `kaji_harness/commands/pr.py::_forward_to_gh`（pr merge 転送）| **対象（契約 lock）** | `--admin` 保持 / `--squash`・`--rebase` 除去 / `--merge` 固定を回帰テストで保証 |
| `issue-close/SKILL.md` local Step 3（`git merge --no-ff`）| **非対象** | local mode は PR / branch protection / admin bypass の概念が無い。ruleset block は発生しない |
| `review-cycle` / `review-poll` / `pr-fix` | **非対象** | review シグナル収束系であり merge を実行しない。marker source-of-truth は本設計と共有するが merge 経路ではない |
| その他の `kaji pr merge` 呼び出し | **なし** | skill/docs 全体で `kaji pr merge` の実呼び出しは issue-close Step 3 の 1 箇所のみ（grep 確認済み） |

## インターフェース

### 入力

#### 新規 CLI サブコマンド `kaji pr admin-merge-check <branch>`（read-only, GitHub-only）

- 引数: `<branch>`（feature branch 名。issue-close の `[branch_name]` をそのまま渡す）
- 副作用: なし（merge しない。GitHub への write を一切行わない read-only 判定）
- provider: `provider.type='github'` 専用。bare provider 配下では既存 `_handle_pr` の
  bare-provider ガードで fail-fast（挙動不変）

#### `kaji pr merge <branch> --admin`（既存機構、契約を明文化）

- `_forward_to_gh` が `--admin` を保持し、`--squash` / `--rebase` を除去し、`--merge` を固定する
  （既存挙動。本設計で回帰テストにより契約を lock する。コード変更は原則不要）

### 出力

#### `kaji pr admin-merge-check` の出力契約

- **ALLOW**: exit code `0`。安全条件をすべて満たす。stdout に `ALLOW`（+ 判定サマリ 1 行）
- **DENY**: exit code 非 0（`EXIT_RUNTIME_ERROR`）。条件不成立、または判定情報の取得失敗。
  stderr に `DENY: <理由>`（不成立条件を明示）
- fail-closed: 途中のいずれかの `gh` 呼び出しが非 0 / JSON parse 失敗 / PR が 0 件・複数件など
  曖昧な場合はすべて DENY（exit 非 0）

exit code をシェル分岐に使うことで SKILL.md 側の prose を最小化する。

### 使用例（issue-close/SKILL.md github Step 3 の新フロー）

```bash
# 1. まず通常 merge を試す（非 admin。block されない PR は従来どおりここで完了）
if kaji pr merge [branch_name]; then
    : # 成功。pr_merge_result = "マージ済み"
else
    # 2. block された場合のみ安全条件判定（read-only, fail-closed）
    if kaji pr admin-merge-check [branch_name]; then
        # 3. 全条件成立 → 条件付き admin merge。--auto は使わない。thread Resolve もしない
        kaji pr merge [branch_name] --admin || {
            echo "ABORT: admin merge failed"; exit 1
        }
        # pr_merge_result = "admin merge 済み（条件付き bypass）"
    else
        # 4. 条件不成立 or 判定失敗 → fail-closed ABORT。--auto へ fallback しない
        echo "ABORT: admin merge preconditions not met (see admin-merge-check output)"
        exit 1
    fi
fi
```

## 制約・前提条件

- merge method は `--merge`（no-ff）固定を維持する（AGENTS.md / `docs/guides/git-commit-flow.md`
  / 現行 `kaji pr merge` 契約）。`--admin` は method flag ではなく bypass flag であり、`--merge`
  と併用される
- admin bypass の実権限は GitHub server 側で強制される。repository admin 権限が無い場合、
  `gh pr merge --admin` 自体が失敗する（= fail-closed の最終防壁）
- `kaji-review` marker は `kaji pr review <pr> --approve`（self-PR fallback）が
  `repos/<repo>/issues/<pr>/comments` に投稿する。PR timeline（`gh pr view <pr> --json comments`）
  から読める（`docs/cli-guides/github-mode.md` § marker comment 観測経路）
- marker format（`build_kaji_review_marker`）は state のみを埋め、HEAD SHA を含まない。freshness
  は timestamp 比較で判定する（marker への SHA 埋め込みは #186/#199 契約変更となり本 Issue の
  scope 外）
- 本判定は single-user self-PR 運用が前提。check→act 間の TOCTOU は無視できる範囲であり、
  `gh pr merge --admin` は server 側で mergeability を再検証する

## 方針

### 全体構成

1. **`kaji_harness/commands/pr.py`**: read-only 判定サブコマンド `admin-merge-check` を追加する。
   `_handle_pr` の dispatch に分岐を足し、新ハンドラ `_pr_admin_merge_check(branch, *,
   repo_override)` へ委譲する。既存 dispatch（`review` approve/request-changes、`review-poll`、
   `_PR_BUILTIN_SUBCOMMANDS`、`_forward_to_gh`）は不変。
2. **`.claude/skills/issue-close/SKILL.md`**: github Step 3 を上記「使用例」の条件付き recovery
   フローへ書き換える。`--auto` を使わない・thread を Resolve しない・fail-closed を明記する。
3. **`tests/`**: CLI `--admin` 契約 lock、`admin-merge-check` の ALLOW/DENY 判定（PR #366 状態
   再現を含む）、SKILL.md 不変条件（`--auto` fallback 不在）の回帰テストを追加する。
4. **`docs/cli-guides/github-mode.md` / `.ja.md`**: 条件付き admin merge 契約と
   `admin-merge-check` の判定条件を追記する。

### `_pr_admin_merge_check` の判定ロジック（擬似コード）

```python
def _pr_admin_merge_check(branch, *, repo_override) -> int:
    repo = _detect_repo(override=repo_override)          # 失敗 → DENY(非0)
    # 1. branch → open PR を一意解決（gh pr list --head <branch> --state open）
    #    0 件 / 複数件 → DENY（曖昧を fail-closed）
    pr = resolve_single_open_pr(repo, branch)            # 失敗 → DENY

    # 2. PR 詳細を 1 回で取得
    #    gh pr view <pr> --repo <repo> --json \
    #      author,mergeable,mergeStateStatus,headRefOid,commits,comments
    data = gh_pr_view_json(pr)                           # 失敗/parse 不能 → DENY

    me = gh_api_user_login()                             # 失敗 → DENY
    admin = gh_api_repo_permissions_admin(repo)          # 失敗 → DENY

    # 条件 A: self-PR
    if data.author.login != me: return DENY("not a self-PR")
    # 条件 B: mergeable（block 原因が policy であること。CONFLICTING は除外）
    if data.mergeable != "MERGEABLE": return DENY(f"not MERGEABLE: {data.mergeable}")
    # 条件 C: 現在 HEAD に対応する最新 kaji-review marker が APPROVED
    if not fresh_approved_marker(data.commits, data.comments):
        return DENY("no fresh APPROVED marker for current HEAD")
    # 条件 D: repository admin（bypass 権限）。取得失敗も含め非 true は DENY
    if admin is not True: return DENY("authenticated user lacks repo admin bypass")

    return ALLOW
```

### freshness 判定（`fresh_approved_marker`）

```python
def fresh_approved_marker(commits, comments) -> bool:
    head_dt = max(c.committedDate for c in commits)      # 現在 HEAD commit 時刻
    # comments のうち 1 行目が `<!-- kaji-review: state=(APPROVED|CHANGES_REQUESTED) -->`
    # の marker のみ抽出し createdAt 昇順で最新を採る
    markers = [m for m in comments if is_review_marker(m.body.splitlines()[0])]
    if not markers: return False
    latest = max(markers, key=lambda m: m.createdAt)
    return latest.state == "APPROVED" and latest.createdAt > head_dt
```

- 最新 marker が `CHANGES_REQUESTED` → DENY（未承認）
- 最新 APPROVED marker の `createdAt` が HEAD commit 時刻以前 → DENY（stale。HEAD 更新後に
  再承認されていない）
- marker parse は既存 `_KAJI_REVIEW_MARKER_PREFIX`（`providers/github.py`）を再利用し、1 行目
  厳密照合とする（本文中の marker 引用を誤検出しない。verdict marker 検出と同じ設計）

### bypass 権限判定（条件 D）

- `gh api repos/<owner>/<repo> --jq '.permissions.admin'` が `true` であることを要求する。
  repository admin は `gh pr merge --admin` が branch policy を bypass するための必要権限であり、
  これを直接シグナルにする。
- Issue #367 が観測した ruleset `current_user_can_bypass=always` は、この admin 権限に対応する
  ruleset 側の表現である。ruleset id を事前に知る必要のない `permissions.admin` を primary
  signal に採る（AI 詳細化。review-design / review-code で検査）。
- 取得失敗（rc≠0）や `true` 以外はすべて DENY（fail-closed）。仮に admin=true でも ruleset が
  bypass を拒否する稀ケースでは、最終的に `kaji pr merge --admin` 自体が失敗し ABORT する
  （二重の fail-closed）。

### fail-closed の徹底

- いずれの `gh` 呼び出しも `_gh_capture_value` 相当（rc≠0 は stderr 中継 + `None` 返却）で扱い、
  `None` は即 DENY へ縮約する。silent fallthrough を作らない（本 bug の教訓: 未定義経路で agent
  が即興する failure mode を、コードでは「情報不足 → DENY」に固定する）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| self-PR の review source of truth | GitHub 正式 APPROVE でなく `kaji-review` marker を使う | Issue #186 / #199、Issue #367 owner コメント（人間決定） | marker を PR comments から 1 行目厳密照合で抽出し、最新 marker の state で判定 |
| `isResolved=false` の扱い | 単独ユーザー運用では thread Resolve を要求しない | Issue #367 owner 方針、PR #365 運用実績（人間決定） | recovery フローで Resolve 操作を一切行わないことを SKILL.md に明記 |
| branch policy block の回復方法 | 安全条件 + bypass 権限を確認して admin merge。`--auto` は使わない | Issue #367 owner 方針、ruleset `current_user_can_bypass=always`（人間決定） | 4 条件判定を `admin-merge-check` へ集約し、SKILL.md は exit code で分岐 |
| merge method | `--merge`（no-ff）固定。squash / rebase 不許可 | AGENTS.md、`docs/guides/git-commit-flow.md`、現行 `kaji pr merge` 契約（人間決定） | `--admin` を method flag に含めず `--merge` と併用する契約を回帰テストで lock |
| 判定ロジックの配置（prose か CLI か）| read-only CLI helper `admin-merge-check` に集約する | AI の仮定。根拠: ADR 008 決定 3（安全契約は CLI/harness 層）、本 bug が「安全ロジックを prose 即興に委ねた」ことに起因。Issue #368 重要判断「具体的な API / CLI は設計で詳細化」で design 委任。検査先: review-design / review-code | 代替案（純 prose + skill-invariant grep のみ）を § 代替案で棄却理由と共に記録 |
| marker freshness の判定方式 | 最新 APPROVED marker の createdAt > 現在 HEAD commit 時刻 | AI の仮定。根拠: marker format が SHA を含まず timestamp のみ利用可。検査先: review-design / Medium テスト（gh JSON fixture） | HEAD commit 時刻 = commits[].committedDate の max、marker は 1 行目厳密照合 |
| bypass 権限の判定シグナル | `permissions.admin == true`（fail-closed）+ `--admin` 実行による二重防壁 | AI の仮定。根拠: `gh pr merge --admin` が必要とする権限は repo admin。Issue 観測 `current_user_can_bypass=always` に対応。検査先: review-code / Medium テスト | ruleset id 事前解決を避け repo permissions を直接シグナルに |

> source of truth の格下げ・出典のない one-way door・AI 仮定の人間決定への偽装は行っていない。
> 人間が決定済みの WHAT（marker source of truth / 条件付き admin merge / no `--auto` / no Resolve
> / `--merge` 固定 / 不成立時 fail-closed）を保持し、HOW（判定の配置・freshness/bypass の具体
> 方式）のみを two-way door として詳細化した（Issue #368 重要判断表が HOW を design 委任）。

### 代替案（判定ロジックの配置）と棄却理由

- **案 A（採用）**: read-only CLI helper `admin-merge-check` に 4 条件を集約。
  - 長所: 安全条件が unit テスト可能（PR #366 状態を mock 再現し ALLOW/DENY を検証 = OB 回帰）。
    fail-closed をコードで強制。ADR 008 決定 3 と整合。本 bug の再発（安全ロジックの prose 即興）を
    構造的に抑止。
- **案 B（棄却）**: 純 prose（agent が `gh`/`kaji` を並べて 4 条件を判定）+ skill-invariant grep。
  - 棄却理由: 本 bug の根本原因が「安全ロジックを prose に委ね、未定義経路で agent が即興した」
    ことである以上、prose を prose で直すと fragility を温存する。freshness の timestamp 比較を
    毎 run prose で正しく実装する保証が弱く、OB 回帰が grep 止まりで faithful でない。

## テスト戦略

### 変更タイプ

実行時コード変更（`kaji_harness/commands/pr.py` に新サブコマンド）+ instruction 変更
（`issue-close/SKILL.md`）+ docs-only（github-mode）。

### 実行時コード変更の場合

#### Small テスト（mock 完結・純ロジック）

- **CLI `--admin` 契約 lock（Issue 完了条件「`--admin` 保持 / squash・rebase 除去 / `--merge`
  固定」）**: `_forward_to_gh("pr", ["merge", "<b>", "--admin", "--squash"])` →
  `gh pr merge <b> --admin --merge`。`--admin --rebase`、素の `--admin` も検証。`--admin` が
  除去されず `--merge` が 1 個だけ付くこと。
- **`admin-merge-check` ALLOW 判定 = OB 回帰（Issue 完了条件「run 260723023106 の OB 回帰」）**:
  PR #366 状態を mock 再現（author==me、mergeable=MERGEABLE、mergeStateStatus=BLOCKED、
  APPROVED marker createdAt > HEAD committedDate、permissions.admin=true）→ exit 0（ALLOW）。
  修正前は該当ロジックが存在しない＝実装前 Red は run 260723023106 の実ログで代替（bug.md §
  escape clause）。
- **`admin-merge-check` DENY 判定（fail-closed 分岐を網羅）**: 各々 exit 非 0 を検証。
  - non-self-PR（author != me）
  - 最新 marker が `CHANGES_REQUESTED`／marker 不在
  - stale APPROVED（createdAt ≤ HEAD committedDate）
  - `mergeable=CONFLICTING`
  - `permissions.admin` が `false` / 取得失敗
  - 途中の `gh` 呼び出し rc≠0（fail-closed）
  - open PR が 0 件 / 複数件（曖昧 → DENY）
- **freshness 純関数**: `fresh_approved_marker` に対し marker 抽出・最新選択・timestamp 比較・
  1 行目厳密照合（本文引用の非誤検出）を境界値で検証。

#### Medium テスト（subprocess orchestration / SKILL.md I/O）

- **SKILL.md 不変条件（instruction 回帰）**: `issue-close/SKILL.md` github merge 節が
  (a) `kaji pr admin-merge-check` を参照し、(b) `kaji pr merge [branch_name] --admin` を条件成立時
  に呼び、(c) merge recovery で `--auto` を **使わない**、(d) fail-closed で ABORT することを
  静的検査する（既存 `tests/test_skill_migration.py` の `_scan` パターンを踏襲）。
- 判定は mock 完結だが subprocess 呼び出し列（`gh pr list` → `gh pr view` → `gh api user` →
  `gh api repos`）の順序・引数を検証する統合寄りテストは Medium 相当。

#### Large テスト（実 GitHub API）

- 追加しない。判定は ruleset enforce 済み・admin bypass 可・auto-merge 無効という live repo 状態と
  admin 資格情報を要し、CI で決定的に再現不能（testing-convention 判定基準「外部 API / 実サービス
  疎通あり → Large」かつ「恒久テストは CI で再現できる構成」を満たせない）。OB の実証は run
  `260723023106` の実ログ（保存済み一次情報）で代替する（bug.md § escape clause）。mock による
  Small/Medium が判定ロジックと契約を完全に覆う。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規 ADR は起こさない。ADR 008 決定 3 の既存方針に沿う配置 |
| docs/ARCHITECTURE.md | なし | アーキテクチャの構造変更なし |
| docs/dev/ | なし | workflow lifecycle は不変（close step の内部手順のみ変更） |
| docs/reference/ | なし | Python 規約変更なし |
| docs/cli-guides/github-mode.md | **あり** | `kaji pr merge --admin` 契約と `admin-merge-check` 判定条件を追記 |
| docs/cli-guides/github-mode.ja.md | **あり** | 同上（日本語） |
| AGENTS.md / CLAUDE.md | なし | merge 規約（`--merge` 固定）は不変 |
| .claude/skills/issue-close/SKILL.md | **あり（instruction 本体）** | github Step 3 に条件付き admin merge recovery を明記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| run 260723023106 close verdict | `.kaji-artifacts/331/runs/260723023106/steps/close/attempt-001/verdict.yaml` | OB の一次証跡。`kaji pr merge chore/331` が base branch policy block、`--auto` が auto-merge 無効で失敗、mergeStateStatus=BLOCKED / mergeable=MERGEABLE |
| Issue #367 owner 調査コメント | `gh issue view 367`（2026-07-23 08:04:05Z）| self-PR marker を source of truth とし、条件付き `--admin --merge`、Resolve 不要、`--auto` 不使用、不成立時 fail-closed を決定（人間決定の出典） |
| Issue #186 / #199 | 本 repo Issue | self-PR の APPROVED / CHANGES_REQUESTED を marker comment で表現する契約 |
| 現行 merge 転送 | `kaji_harness/commands/pr.py::_forward_to_gh`（L42-87）| `_FORGE_METHOD_FLAGS` は `--admin` を含まず、`gh pr merge <b> --admin --merge` へ転送される（実測確認） |
| self-PR marker 実装 | `kaji_harness/providers/github.py::build_kaji_review_marker` / `_github_pr_review`（`commands/pr.py`）| marker は state のみ埋め、`issues/<pr>/comments` に投稿。HEAD SHA を含まない |
| merge method 規約 | `docs/guides/git-commit-flow.md`、AGENTS.md | `--merge`（no-ff）固定。squash / rebase 禁止 |
| gh pr merge --admin 仕様 | https://cli.github.com/manual/gh_pr_merge | `--admin`: Use administrator privileges to merge a PR that does not meet requirements（repository admin 権限が前提） |
| GitHub repo permissions | https://docs.github.com/en/rest/repos/repos#get-a-repository | `permissions.admin`: 認証ユーザーの当該 repo に対する admin 権限（bypass 権限のシグナル） |
| ADR 008 決定 3 | 本 repo（verdict marker 設計）| cross-skill / 安全契約は SKILL.md 散文でなく CLI/harness 層に置く |
