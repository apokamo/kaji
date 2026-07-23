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
2. authenticated user が投稿し、かつ現在 HEAD SHA に束縛された最新の `kaji-review` marker が
   `APPROVED`（第三者投稿 / sha 不一致 / stale はすべて除外）
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

`required_review_thread_resolution=true` を含む ruleset `main_ruleset`（id `19180677`）は
**2026-07-20 から active**（review-design の ruleset API 確認）。PR #365 の merge は 2026-07-21
であり、ruleset は #365 merge より前に適用済みだった。したがって「#365 merge 時点で
thread-resolution 要件が未 enforce だった」という仮説は事実と矛盾する。issue-close の当該
Step 3 は recovery 分岐を最初から持たないため、これは「特定コミットからの degradation」ではなく
「ruleset enforcement 環境で顕在化した設計欠落」である。

### PR #365 と #366 で挙動が分かれた根本原因（保存済み close log に基づく訂正）

両 PR は同条件（self-PR / `isResolved=false` / APPROVED marker 済み / ruleset enforce 済み）で
あり、**通常 merge の失敗経路まで同一**である。保存済み close log の一次証跡:

- **PR #365**（`.kaji-artifacts/352/runs/260722015746/steps/close/attempt-001/terminal.log`）:
  1. `kaji pr merge chore/352` → `X ... the base branch policy prohibits the merge`
  2. `kaji pr merge chore/352 --auto` → `GraphQL: Auto merge is not allowed for this repository`
  3. agent が **`kaji pr merge chore/352 --admin --merge` を即興実行 → 成功**（merge commit
     `28b73c5`。`git log` の `28b73c5 chore: ... (#352) (#365)` と一致）
- **PR #366**（`.kaji-artifacts/331/runs/260723023106/steps/close/attempt-001/verdict.yaml`）:
  1. `kaji pr merge chore/331` → base branch policy block（同上）
  2. `kaji pr merge chore/331 --auto` → auto-merge 無効（同上）
  3. agent は `--admin` を試さず **ABORT**

したがって根本原因は「PR 間の状態差」でも「#365 が block されない経路で merge された」ことでも
**ない**。両 PR とも同じ policy block + `--auto` 失敗に到達し、差は **未定義 recovery 下での
agent の非決定的な即興**だけである。#365 agent はたまたま `--admin --merge` を思いつき成功し、
#366 agent は思いつかず ABORT した。さらに #365 の即興 admin merge は self-PR・marker freshness・
policy-block 適格性・HEAD 拘束を一切検査していない（危険な bypass がたまたま無害だった）。この
証跡は「安全条件を検査した決定的な admin merge recovery を issue-close に固定する」という本設計の
必要性をむしろ強める。OB 回帰 fixture もこの実態（両 PR 同一の block → `--auto` 失敗）へ合わせる。

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

#### `kaji pr admin-merge-check` の出力契約（machine-readable）

- **ALLOW**: exit code `0`。安全条件をすべて満たす。**stdout に判定した HEAD SHA（40 桁 hex、
  1 行のみ、末尾改行）だけを出力する**。この SHA を後続 admin merge の `--match-head-commit` に
  そのまま渡すことで、check→act の HEAD 拘束を成立させる（MF2 対応 / Should Fix「機械可読出力」
  対応）。診断メッセージは stderr に出し、stdout は SHA 専用に保つ。
- **DENY**: exit code 非 0（`EXIT_RUNTIME_ERROR`）。条件不成立、または判定情報の取得失敗。
  stdout は空。stderr に `DENY: <理由>`（不成立条件を明示）。
- fail-closed: 途中のいずれかの `gh` 呼び出しが非 0 / JSON parse 失敗 / PR が 0 件・複数件 /
  `headRefOid` が 40 桁 hex でないなど曖昧・不備な場合はすべて DENY（exit 非 0、stdout 空）。

exit code で分岐し、stdout の SHA を `--match-head-commit` に渡すことで SKILL.md 側の prose を
最小化しつつ HEAD 拘束を CLI 契約に固定する。

### 使用例（issue-close/SKILL.md github Step 3 の新フロー）

```bash
# 1. まず通常 merge を試す（非 admin。block されない PR は従来どおりここで完了）
if kaji pr merge [branch_name]; then
    : # 成功。pr_merge_result = "マージ済み"
else
    # 2. 通常 merge 失敗 → 安全条件判定（read-only, fail-closed）。
    #    admin-merge-check 自身が mergeStateStatus=BLOCKED 等の policy-block 適格性を
    #    検査するため、transient / auth / 非 policy 失敗は check 側で DENY され elevated
    #    merge に進まない（MF3 対応）。ALLOW 時は判定した HEAD SHA を stdout で受け取る。
    if HEAD_SHA=$(kaji pr admin-merge-check [branch_name]); then
        # 3. 全条件成立 → 判定した SHA に拘束した条件付き admin merge。
        #    --auto は使わない。thread Resolve もしない。check 後に HEAD が動いていれば
        #    --match-head-commit が gh 側で merge write を拒否し、非 0 で ABORT する（MF2）。
        kaji pr merge [branch_name] --admin --match-head-commit "$HEAD_SHA" || {
            echo "ABORT: admin merge rejected (HEAD moved since check, or bypass denied)"
            exit 1
        }
        # pr_merge_result = "admin merge 済み（条件付き bypass, HEAD 拘束）"
    else
        # 4. 条件不成立 or 判定失敗 → fail-closed ABORT。--auto へ fallback しない
        echo "ABORT: admin merge preconditions not met (see admin-merge-check stderr)"
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
  `repos/<repo>/issues/<pr>/comments` に投稿する。marker 全件を漏れなく読むため、判定は
  `gh api --paginate --slurp repos/<repo>/issues/<pr>/comments`（既存 `list_issue_comments_all`
  と同じ pagination 経路）で全 issue comments を取得する。`gh pr view --json comments` は
  長期 PR で全件を保証せず最新 marker を見落として false DENY になり得るため使わない（Should
  Fix「comments 取得上限」対応）
- marker format（`build_kaji_review_marker`）は state と review 対象 head SHA
  （`<!-- kaji-review: state=<S> sha=<40-hex> -->`）を埋める。admin-merge-check は
  marker.sha == 現在の `headRefOid`、かつ marker 投稿者 == authenticated user の marker だけを
  承認判断の候補とする。**当初は state のみを埋め timestamp 比較で freshness を判定する設計だった
  が、PR #370 の review（P1/P2）で 2 つの bypass が判明したため改めた**:
  - **P1（投稿者詐称）**: issue comment は collaborator / public commenter も投稿できるため、
    第三者が `<!-- kaji-review: state=APPROVED ... -->` を投稿すると承認扱いされ admin bypass merge
    に到達する。→ marker 投稿者を authenticated user（= self-PR author）に限定して防ぐ
  - **P2（stale approval bypass）**: `committedDate` は git commit 側で任意設定可能（改竄可能）な
    ため、backdated commit へ force-push すると HEAD の commit 時刻が過去になり、旧 head 向けの
    古い APPROVED marker が「fresh」と誤判定される。→ marker に review 時点の head SHA を埋め、
    現在 `headRefOid` と厳密一致する marker だけを承認候補とする（timestamp 非依存）
  - marker への SHA 埋め込みは `kaji pr review --approve/--request-changes` の self-PR fallback
    が posting 時に現在の `headRefOid` を取得して行う。sha 無しの旧形式 marker は
    `parse_kaji_review_marker` が非一致（`None`）に落とすため承認扱いされない（fail-closed）
- 本判定は single-user self-PR 運用が前提。check→act 間の HEAD 更新は無視せず、admin merge に
  `--match-head-commit <判定 SHA>` を渡して gh 側で拒否させることで原子的に拘束する（HEAD が
  動けば merge write は行われず非 0 で ABORT。MF2 対応）。`gh pr merge --admin` は server 側でも
  mergeability を再検証する

## 方針

### 全体構成

1. **`kaji_harness/providers/github.py`**: **public** parser `parse_kaji_review_marker(line: str)
   -> tuple[str, str] | None`（1 行目が `state=<S> sha=<40-hex>` 形式の review marker なら
   `(state, sha)` を返し、そうでなければ `None`）を追加する。`build_kaji_review_marker(state, sha)`
   も sha 引数を取り marker に埋め込むよう拡張する（PR #370 review P2）。sha 無しの旧形式は
   非一致（`None`）に落とす。private `_KAJI_REVIEW_MARKER_PREFIX` は github.py
   内に留め、外部へは公開しない（MF4 対応。既存 `providers/markers.py` の
   `parse_kaji_verdict_marker` と同じ「build と対の public parser」パターン）。
2. **`kaji_harness/commands/pr.py`**: read-only 判定サブコマンド `admin-merge-check` を追加する。
   `_handle_pr` の dispatch に分岐を足し、新ハンドラ `_pr_admin_merge_check(branch, *,
   repo_override)` へ委譲する。marker 解析は上記 **public** `parse_kaji_review_marker` を import
   して使う（既存 line 12 が public `build_kaji_review_marker` を `providers.github` から import
   済みで、public symbol の cross-package import は ADR 009 で許容。private import は行わない）。
   既存 dispatch（`review` approve/request-changes、`review-poll`、`_PR_BUILTIN_SUBCOMMANDS`、
   `_forward_to_gh`）は不変。`_forward_to_gh` は `--match-head-commit <SHA>` を（method flag では
   ないため）そのまま `gh pr merge` へ転送する（`kaji pr merge <b> --admin --match-head-commit
   <SHA>` → `gh pr merge <b> --admin --match-head-commit <SHA> --merge`）。
3. **`.claude/skills/issue-close/SKILL.md`**: github Step 3 を上記「使用例」の条件付き recovery
   フローへ書き換える。`--auto` を使わない・thread を Resolve しない・`--match-head-commit` で
   HEAD 拘束する・fail-closed を明記する。
4. **`tests/`**: CLI `--admin` / `--match-head-commit` 契約 lock、`admin-merge-check` の
   ALLOW/DENY 判定（PR #366 状態再現・HEAD mismatch・非 policy failure を含む）、public parser
   の単体、SKILL.md 不変条件（`--auto` fallback 不在 / `--match-head-commit` 使用）の回帰テストを
   追加する。
5. **`docs/cli-guides/github-mode.md` / `.ja.md`**: 条件付き admin merge 契約と
   `admin-merge-check` の判定条件（policy-block 適格性・HEAD 拘束）を追記する。

### `_pr_admin_merge_check` の判定ロジック（擬似コード）

```python
# 許可する mergeStateStatus は BLOCKED のみ（= mergeable だが branch policy / ruleset で
# block）。CLEAN(=通常 merge 可能なので admin 不要) / DIRTY / BEHIND / DRAFT / HAS_HOOKS /
# UNKNOWN はすべて DENY（fail-closed）。
_ELIGIBLE_MERGE_STATE = "BLOCKED"
# check を通過してよい statusCheckRollup 状態（成功・中立のみ）。他 check の失敗/保留を
# admin bypass しないため、FAILURE / PENDING / ERROR / EXPECTED を 1 つでも含めば DENY。
_OK_CHECK_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}

def _pr_admin_merge_check(branch, *, repo_override) -> int:
    repo = _detect_repo(override=repo_override)          # 失敗 → DENY(非0)
    # 1. branch → open PR を一意解決（gh pr list --head <branch> --state open）
    #    0 件 / 複数件 → DENY（曖昧を fail-closed）
    pr = resolve_single_open_pr(repo, branch)            # 失敗 → DENY

    # 2. PR 詳細を取得（HEAD SHA / merge 適格性 / check / 直近 commit 時刻）
    #    gh pr view <pr> --repo <repo> --json \
    #      author,mergeable,mergeStateStatus,headRefOid,commits,statusCheckRollup
    data = gh_pr_view_json(pr)                           # 失敗/parse 不能 → DENY
    # comments は全件 pagination で別取得（gh api --paginate --slurp、Should Fix 対応）
    comments = gh_issue_comments_all(repo, pr)           # 失敗 → DENY

    me = gh_api_user_login()                             # 失敗 → DENY
    admin = gh_api_repo_permissions_admin(repo)          # 失敗 → DENY

    head_sha = data.headRefOid
    if not is_40_hex(head_sha): return DENY("headRefOid missing/invalid")

    # 条件 A: self-PR
    if data.author.login != me: return DENY("not a self-PR")
    # 条件 B: policy-block 適格性（MF3）。BLOCKED かつ MERGEABLE かつ他 check 非失敗のみ。
    #   これにより transient / auth / conflict / 非 policy 失敗を elevated merge から除外する。
    if data.mergeStateStatus != _ELIGIBLE_MERGE_STATE:
        return DENY(f"mergeStateStatus not {_ELIGIBLE_MERGE_STATE}: {data.mergeStateStatus}")
    if data.mergeable != "MERGEABLE": return DENY(f"not MERGEABLE: {data.mergeable}")
    if any(c.state not in _OK_CHECK_STATES for c in data.statusCheckRollup):
        return DENY("failing/pending status checks present (not bypassed)")
    # 条件 C: authenticated user が投稿し、現在 HEAD SHA に束縛された最新 kaji-review marker が
    #   APPROVED（PR #370 review P1: 投稿者を me に限定 / P2: marker.sha == head_sha を要求）
    if not fresh_approved_marker(head_sha, comments, me):
        return DENY("no fresh APPROVED marker bound to current HEAD")
    # 条件 D: repository admin（bypass 権限）。取得失敗も含め非 true は DENY
    if admin is not True: return DENY("authenticated user lacks repo admin bypass")

    print(head_sha)                                      # ALLOW: stdout に判定 SHA のみ
    return EXIT_OK
```

### freshness 判定（`fresh_approved_marker`）

```python
def fresh_approved_marker(head_sha, comments, trusted_login) -> bool:
    decisions = []
    for m in comments:
        # 条件1（P1）: 投稿者が authenticated user（= self-PR author）であること
        if m.user is None or m.user.login != trusted_login: continue
        parsed = parse_kaji_review_marker(m.body.splitlines()[0])  # (state, sha) | None
        if parsed is None: continue
        state, marker_sha = parsed
        if state not in {"APPROVED", "CHANGES_REQUESTED"}: continue
        # 条件2（P2）: marker が review した head SHA が現在 HEAD と厳密一致すること
        if marker_sha != head_sha: continue
        decisions.append((state, m.createdAt))
    if not decisions: return False
    latest_state, _ = max(decisions, key=lambda pair: pair[1])   # createdAt 最新の decision
    return latest_state == "APPROVED"
```

- 承認候補は「authenticated user 投稿」かつ「marker.sha == 現在 head_sha」を満たす decision marker
  のみ。そのうち最新（`createdAt` max）が `APPROVED` の場合だけ ALLOW
- 最新候補が `CHANGES_REQUESTED` → DENY（未承認）
- **HEAD SHA 束縛**により、force-push（backdated commit 含む）後に残る旧 head 向けの APPROVED は
  sha 不一致で候補から除外される。改竄可能な `committedDate` の timestamp 比較には依存しない（P2）
- **投稿者限定**により、第三者（collaborator / public commenter）が詐称投稿した marker は候補から
  除外される（P1）。`user` 欠落（ghost 等）も untrusted 扱いで除外（fail-closed）
- marker parse は **public** `parse_kaji_review_marker`（`providers/github.py`、MF4 で新設。
  戻り値は `(state, sha)` タプル）を用い、1 行目厳密照合とする（本文中の marker 引用を誤検出
  しない。verdict marker 検出と同じ設計）。private `_KAJI_REVIEW_MARKER_PREFIX` を `commands`
  から import しない（ADR 009）

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
| `isResolved=false` の扱い | 単独ユーザー運用では thread Resolve を要求しない | Issue #367 owner 方針（人間決定）。PR #365 close log は Resolve せず admin merge 済みを示す | recovery フローで Resolve 操作を一切行わないことを SKILL.md に明記 |
| branch policy block の回復方法 | 安全条件 + bypass 権限を確認して admin merge。`--auto` は使わない | Issue #367 owner 方針、ruleset `current_user_can_bypass=always`（人間決定） | 4 条件判定を `admin-merge-check` へ集約し、SKILL.md は exit code で分岐 |
| policy-block 適格性の識別（MF3）| `mergeStateStatus==BLOCKED` かつ `mergeable==MERGEABLE` かつ他 check 非失敗のみ admin 対象 | AI の仮定。根拠: `MERGEABLE` は conflict 無しを示すだけで block 原因を証明しない。review 指摘（transient/auth/非 policy 失敗を除外）。検査先: review-code / Small テスト | 通常 merge の任意失敗を一律 recovery 対象にせず、check 側で BLOCKED 等を要求。failing/pending checks を bypass しない |
| check→act の HEAD 拘束（MF2）| 判定した HEAD SHA を admin merge に `--match-head-commit` で拘束する | 人間決定（Issue #368 EB「現在 HEAD に対する最新 review cycle」）+ 一次情報 `gh pr merge --match-head-commit`（公式）。検査先: Small テスト（mismatch で merge write 無し） | `admin-merge-check` が ALLOW 時に SHA を stdout 出力し、SKILL.md がそれを `--match-head-commit` に渡す |
| merge method | `--merge`（no-ff）固定。squash / rebase 不許可 | AGENTS.md、`docs/guides/git-commit-flow.md`、現行 `kaji pr merge` 契約（人間決定） | `--admin` / `--match-head-commit` を method flag に含めず `--merge` と併用する契約を回帰テストで lock |
| marker parser の配置境界（MF4）| public `parse_kaji_review_marker` を新設し `commands` から public import する | 人間規約 ADR 009 / `tests/test_private_imports.py`（package 跨ぎ private import 禁止）。検査先: `make check`（fitness test）/ Small テスト | private `_KAJI_REVIEW_MARKER_PREFIX` の cross-package 再利用を避け、build と対の public parser を github.py に追加 |
| 判定ロジックの配置（prose か CLI か）| read-only CLI helper `admin-merge-check` に集約する | AI の仮定。根拠: ADR 008 決定 3（安全契約は CLI/harness 層）、本 bug が「安全ロジックを prose 即興に委ねた」ことに起因。Issue #368 重要判断「具体的な API / CLI は設計で詳細化」で design 委任。検査先: review-design / review-code | 代替案（純 prose + skill-invariant grep のみ）を § 代替案で棄却理由と共に記録 |
| marker freshness の判定方式 | 最新 APPROVED marker の createdAt > 現在 HEAD commit 時刻 | AI の仮定。根拠: marker format が SHA を含まず timestamp のみ利用可。検査先: review-design / Small テスト | HEAD commit 時刻 = commits[].committedDate の max、marker は public parser で 1 行目厳密照合 |
| comments 取得範囲 | 全 issue comments を pagination 取得する | AI の仮定。根拠: `gh pr view --json comments` は長期 PR で全件非保証（review Should Fix）。検査先: review-code | `gh api --paginate --slurp`（既存 `list_issue_comments_all` 経路）で最新 marker 見落としを防ぐ |
| bypass 権限の判定シグナル | `permissions.admin == true`（fail-closed）+ `--admin` 実行による二重防壁 | AI の仮定。根拠: `gh pr merge --admin` が必要とする権限は repo admin。Issue 観測 `current_user_can_bypass=always` に対応。検査先: review-code / Small テスト | ruleset id 事前解決を避け repo permissions を直接シグナルに |

> source of truth の格下げ・出典のない one-way door・AI 仮定の人間決定への偽装は行っていない。
> 人間が決定済みの WHAT（marker source of truth / 条件付き admin merge / 現在 HEAD に対する最新
> review cycle / no `--auto` / no Resolve / `--merge` 固定 / 不成立時 fail-closed / ADR 009
> module 境界）を保持し、HOW（判定の配置・policy-block 適格性の識別法・HEAD 拘束の実現手段・
> freshness/bypass の具体方式・parser の公開形）のみを two-way door として詳細化した（Issue
> #368 重要判断表が HOW を design 委任）。

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

- **CLI `--admin` / `--match-head-commit` 契約 lock（Issue 完了条件「`--admin` 保持 /
  squash・rebase 除去 / `--merge` 固定」+ MF2）**:
  `_forward_to_gh("pr", ["merge", "<b>", "--admin", "--squash"])` → `gh pr merge <b> --admin
  --merge`。`--admin --rebase`、素の `--admin` も検証。加えて
  `["merge", "<b>", "--admin", "--match-head-commit", "<sha>"]` →
  `gh pr merge <b> --admin --match-head-commit <sha> --merge`（`--match-head-commit` と SHA 値が
  除去されず、`--admin` 保持、`--merge` が 1 個）。
- **`admin-merge-check` ALLOW 判定 = OB 回帰（Issue 完了条件「run 260723023106 の OB 回帰」）**:
  PR #366 状態を mock 再現（author==me、mergeable=MERGEABLE、**mergeStateStatus=BLOCKED**、
  statusCheckRollup 空、APPROVED marker createdAt > HEAD committedDate、permissions.admin=true）→
  exit 0 かつ **stdout == headRefOid**。PR #365 close log が示す「両 PR 同一の block → `--auto`
  失敗」実態に fixture を合わせる。修正前は該当ロジックが存在しない＝実装前 Red は run
  260723023106 / PR #365 close log の実ログで代替（bug.md § escape clause）。
- **`admin-merge-check` DENY 判定（fail-closed 分岐を網羅）**: 各々 exit 非 0 かつ **stdout 空**。
  - non-self-PR（author != me）
  - policy-block 非適格（MF3）: `mergeStateStatus != BLOCKED`（例 CLEAN / DIRTY / BEHIND /
    UNKNOWN）／`mergeable=CONFLICTING`／statusCheckRollup に FAILURE / PENDING / ERROR を含む
  - 最新 marker が `CHANGES_REQUESTED`／marker 不在
  - stale APPROVED（createdAt ≤ HEAD committedDate）
  - `permissions.admin` が `false` / 取得失敗
  - `headRefOid` が 40 桁 hex でない
  - 途中の `gh` 呼び出し rc≠0（fail-closed）
  - open PR が 0 件 / 複数件（曖昧 → DENY）
- **HEAD mismatch で merge write 無し（MF2）**: SKILL.md フローに沿い、check が返した SHA と
  異なる HEAD に対する `kaji pr merge <b> --admin --match-head-commit <old_sha>` が
  `gh pr merge` の非 0 を返し、merge が行われず ABORT に落ちることを、`subprocess.run` の gh 応答
  mock で検証（gh が `--match-head-commit` 不一致時に非 0 を返す契約を stub）。
- **public parser 単体（MF4）**: `parse_kaji_review_marker` が 1 行目 marker から state を返し、
  非 marker 行・本文中引用・不正 state で `None` を返すことを検証。`build_kaji_review_marker` の
  出力と round-trip 一致。
- **freshness 純関数**: `fresh_approved_marker` に対し marker 抽出・最新選択・timestamp 比較・
  1 行目厳密照合（本文引用の非誤検出）を境界値で検証。

#### Medium テスト（subprocess orchestration / SKILL.md I/O）

- **SKILL.md 不変条件（instruction 回帰）**: `issue-close/SKILL.md` github merge 節が
  (a) `kaji pr admin-merge-check` を参照し、(b) 条件成立時に
  `kaji pr merge [branch_name] --admin --match-head-commit "$HEAD_SHA"` を呼び、(c) merge
  recovery で `--auto` を **使わない**、(d) fail-closed で ABORT することを静的検査する（既存
  `tests/test_skill_migration.py` の `_scan` パターンを踏襲）。
- 判定は mock 完結だが subprocess 呼び出し列（`gh pr list` → `gh pr view` →
  `gh api ...issues/<pr>/comments` → `gh api user` → `gh api repos`）の順序・引数を検証する
  統合寄りテストは Medium 相当。
- **private import 境界（MF4）**: `make check` に含まれる既存 `tests/test_private_imports.py`
  が、追加コードで `commands` → `providers` の private import を導入していないことを保証する
  （新規テストは不要。既存 fitness test がバックストップ）。

#### Large テスト（実 GitHub API）

- 追加しない。判定は ruleset enforce 済み・admin bypass 可・auto-merge 無効という live repo 状態と
  admin 資格情報を要し、CI で決定的に再現不能（testing-convention 判定基準「外部 API / 実サービス
  疎通あり → Large」かつ「恒久テストは CI で再現できる構成」を満たせない）。OB の実証は run
  `260723023106` の実ログ（保存済み一次情報）で代替する（bug.md § escape clause）。mock による
  Small/Medium が判定ロジックと契約を完全に覆う。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規 ADR は起こさない。ADR 008 決定 3 / ADR 009 module 境界の既存方針に沿う配置 |
| docs/ARCHITECTURE.md | なし | アーキテクチャの構造変更なし |
| docs/dev/ | なし | workflow lifecycle は不変（close step の内部手順のみ変更） |
| docs/reference/ | なし | Python 規約変更なし |
| docs/cli-guides/github-mode.md | **あり** | `kaji pr merge --admin --match-head-commit` 契約と `admin-merge-check` 判定条件（policy-block 適格性・HEAD 拘束）を追記 |
| docs/cli-guides/github-mode.ja.md | **あり** | 同上（日本語） |
| AGENTS.md / CLAUDE.md | なし | merge 規約（`--merge` 固定）は不変 |
| kaji_harness/providers/github.py | **あり（コード）** | public `parse_kaji_review_marker` を追加（MF4） |
| kaji_harness/commands/pr.py | **あり（コード）** | `admin-merge-check` サブコマンド追加 |
| .claude/skills/issue-close/SKILL.md | **あり（instruction 本体）** | github Step 3 に条件付き admin merge recovery（HEAD 拘束・fail-closed）を明記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| run 260723023106 close verdict（PR #366）| `.kaji-artifacts/331/runs/260723023106/steps/close/attempt-001/verdict.yaml` | OB の一次証跡。`kaji pr merge chore/331` が base branch policy block、`--auto` が auto-merge 無効で失敗、mergeStateStatus=BLOCKED / mergeable=MERGEABLE、agent は admin を試さず ABORT |
| run 260722015746 close log（PR #365）| `.kaji-artifacts/352/runs/260722015746/steps/close/attempt-001/terminal.log`（L69-145 相当）| #366 と同一の policy block → `--auto` 失敗の後、agent が `kaji pr merge chore/352 --admin --merge` を即興して成功（merge commit `28b73c5`）。PR #365/#366 の分岐は非決定的即興であることを実証（MF1） |
| ruleset active 期間 | review-design の ruleset `19180677` API 確認 | `main_ruleset` は 2026-07-20 から active、`required_review_thread_resolution=true`。PR #365 merge（2026-07-21）より前に適用済み（「enforce 前」仮説を否定、MF1）|
| Issue #367 owner 調査コメント | `gh issue view 367`（2026-07-23 08:04:05Z）| self-PR marker を source of truth とし、条件付き `--admin --merge`、現在 HEAD に対する最新 review cycle、Resolve 不要、`--auto` 不使用、不成立時 fail-closed を決定（人間決定の出典） |
| Issue #186 / #199 | 本 repo Issue | self-PR の APPROVED / CHANGES_REQUESTED を marker comment で表現する契約 |
| 現行 merge 転送 | `kaji_harness/commands/pr.py::_forward_to_gh`（L42-87）| `_FORGE_METHOD_FLAGS` は `--admin` / `--match-head-commit` を含まず、そのまま `gh pr merge` へ転送される（`gh pr merge <b> --admin --merge` を実測確認） |
| self-PR marker 実装 | `kaji_harness/providers/github.py::build_kaji_review_marker` / `_github_pr_review`（`commands/pr.py`）| marker は state のみ埋め、`issues/<pr>/comments` に投稿。HEAD SHA を含まない。public `build_kaji_review_marker` は既に `commands/pr.py` L12 が cross-package import 済み（ADR 009 で public は許容）|
| merge method 規約 | `docs/guides/git-commit-flow.md`、AGENTS.md | `--merge`（no-ff）固定。squash / rebase 禁止 |
| gh pr merge 仕様 | https://cli.github.com/manual/gh_pr_merge | `--admin`: Use administrator privileges to merge a PR that does not meet requirements（repo admin 権限が前提）。`--match-head-commit <SHA>`: Commit SHA that pull request head must match to allow merge（HEAD 拘束、MF2）|
| GitHub PR mergeStateStatus | https://docs.github.com/en/graphql/reference/enums#mergestatestatus | `BLOCKED`: merge is blocked（review/policy 由来）。`MERGEABLE`（mergeable enum）は conflict 無しのみを示す。policy-block 適格性を `BLOCKED` で識別（MF3）|
| GitHub repo permissions | https://docs.github.com/en/rest/repos/repos#get-a-repository | `permissions.admin`: 認証ユーザーの当該 repo に対する admin 権限（bypass 権限のシグナル） |
| ADR 008 決定 3 / ADR 009 | `docs/adr/008-no-backward-compat-layer.md` / `docs/adr/009-module-boundary-private-import.md` / `tests/test_private_imports.py` | 安全契約の CLI/harness 配置、および package 跨ぎ private import 禁止（public は許容）。MF4 の parser 公開判断の根拠 |
