# [設計] kaji issue / kaji pr passthrough gitlab 対応 + ID 規約 (gl:N) 拡張

Issue: local-pc5090-6

## 概要

`kaji issue` / `kaji pr` の CLI dispatcher を `provider.type='gitlab'` でも動作させ、`normalize_id` に `gl:N` 形式（GitLab project-local IID）を追加する。OQ-2 決定文書 `kaji-pr-mr-bridge.md` の互換 contract を実装し、skill 側に GitHub/GitLab 分岐を持ち込まずに skill 群が GitLab project 上で動くようにする。

## 背景・目的

EPIC `local-pc5090-4` の OQ-2 で、`kaji pr` は「skill 互換性に必要な subcommand に限定し、確定事項 #7 と同じ原則で kaji 側の contract に揃える」方針が決定済み（`kaji-pr-mr-bridge.md`）。本 Issue は確定事項を実装に落とすステップで、以下を満たす必要がある:

- skill 群 (`i-pr` / `issue-close` / `pr-fix` / `pr-verify` / `issue-start` 等) を **GitHub/GitLab 非依存** に保つ
- `gl:N` を `gh:N` と並列の ID 形式として `normalize_id` に統合し、`provider=gitlab` 配下 / `provider=local` 配下の cache 参照の双方を扱えるようにする
- `kaji pr review --approve / --request-changes` の body 付きレビューを GitLab で再現する（note 投稿 → approve / revoke のシーケンス、未 approve 時の revoke no-op）
- `kaji pr merge` で `--squash` / `--rebase` を拒否し CLAUDE.md の `--no-ff only` 原則を保つ

### ユーザーストーリー

- kaji ユーザーとして、`provider.type='gitlab'` 配下で `kaji issue {create / view / edit / list / close / comment}` がそのまま動作してほしい
- kaji ユーザーとして、`kaji pr {create / view / list / merge / comment / review (--approve / --request-changes) / review-comments / reviews / reply-to-comment}` が GitHub 互換 shape で動作してほしい
- kaji ユーザーとして、`kaji issue view gl:42` で GitLab issue IID 42 を表示し、`provider=local` 配下では cache JSON を read-only に参照したい

### 代替案と不採用理由

- **(a) URL/番号系のみ passthrough**: skill が `list` / `view` / `comment` / `review` を使うため不足
- **(b) 全 sub silent passthrough**: `glab mr approvers` / `for` / `subscribe` 等を skill が将来「動くはず」と誤用するリスク。skill ↔ provider 依存が暗黙化する
- **(c) 引数体系の純粋吸収検証のみ**: `create` / `merge` 以外の sub は出力 shape 変換が必須で、純粋 passthrough では不十分（OQ-2 § 採用根拠）

## インターフェース

### 入力

#### `normalize_id(raw, *, provider_name, machine_id)`

新規受理形式:

| `raw` | `provider_name` | 結果 (`kind`, `value`) |
|-------|-----------------|------------------------|
| `"42"` | `"gitlab"` | `("gitlab", "42")` |
| `"gl:42"` | `"gitlab"` | `("gitlab", "42")` |
| `"gl:42"` | `"local"` | `("remote_cache", "42")` |
| `"gl:42"` | `"github"` | `ValueError` (cross-provider 参照不可) |
| `"gh:42"` | `"gitlab"` | `ValueError` (同上) |
| `"local-..."` | `"gitlab"` | `ValueError` (既存契約踏襲) |
| `"42"` | `"github"` (既存) | `("github", "42")` |
| 空文字 / leading-zero / 0 | 任意 | `ValueError` |

`provider_name` の許容集合に `"gitlab"` を追加（現状は `{"github", "local"}`）。

#### `kaji issue` dispatcher

`provider.type='gitlab'` 配下で以下を受理。skill 群が呼ぶ gh 互換引数 → glab 引数の写像は kaji 側で吸収する（skill には GitHub 命名のまま見せる）:

| kaji 引数 (gh 互換正本) | 内部呼出 (glab) | 備考 |
|-------------------------|-----------------|------|
| `kaji issue create --title T --body B` | `glab issue create --title T --description B` | `--body` → `--description`、`--label` / `--assignee` はそのまま受理 |
| `kaji issue view <id> [--json fields] [--jq expr]` | `glab issue view <id> --output json` | 既存 `GitLabProvider.view_issue` を再利用し GitHub shape に正規化、その後 `_apply_jq` |
| `kaji issue edit <id> --body B` | **`glab issue update <id> --description B`** | sub 名 `edit` → `update` 変換、`--body` → `--description` 変換 |
| `kaji issue list [--json fields] [--jq expr]` | `glab issue list --output json` | GitHub field 命名に変換 |
| `kaji issue close <id>` | `glab issue close <id>` | sub 名一致 |
| `kaji issue comment <id> --body B` | **`glab issue note <id> --message B`** | sub 名 `comment` → `note` 変換、`--body` → `--message` 変換 |
| `--commit` flag | （silent に剥がす） | LocalProvider 専用 flag。GitHub mode と同挙動 |
| `kaji issue context <id>` | **本 Issue では既存の明示拒否を維持** | 別 Issue (#3) で `resolve_issue_context` 拡張時に対応 |

`provider.gitlab.repo` を `--repo <group/project>` で強制注入（GitHub mode と同型 guard）。`--hostname gitlab.com` は既存 `GitLabProvider._run_glab` と同方針で全 invocation に default 注入。

#### `kaji pr` dispatcher（Tier B + skill 実呼び出し引数）

`provider.type='gitlab'` 配下で以下を受理。skill が呼ぶ gh 互換引数 → glab 引数 の写像は kaji 側で吸収する。**skill は GitHub 命名のまま使う**（OQ-2 § 設計原則 #3）:

| kaji 引数 (gh 互換正本) | 内部呼出 (glab) | 備考 |
|-------------------------|-----------------|------|
| `kaji pr create --title T --body B --base BR` | `glab mr create --title T --description B --target-branch BR` | `--body` → `--description`、**`--base` → `--target-branch`**。skill `i-pr` Step 4 で `--base main` を渡す前提（`.claude/skills/i-pr/SKILL.md` L163） |
| `kaji pr view 42 [--json fields] [--jq expr]` | `glab mr view 42 --output json` | GitHub `pulls/<N>` field 互換 shape に変換後、`_apply_jq` |
| **`kaji pr view 42 --comments`** | `glab mr view 42 --comments` | skill `pr-fix`/`pr-verify` Step 1 が呼ぶ（`.claude/skills/pr-fix/SKILL.md` L139, `.claude/skills/pr-verify/SKILL.md` L153）。glab の `--comments` は人間可読出力をそのまま流す（JSON 経路ではない） |
| `kaji pr list [--json fields] [--jq expr]` | `glab mr list -F json` | GitHub `pulls` array shape に変換 |
| **`kaji pr list --search Q`** | `glab mr list --search Q -F json` | skill `pr-fix`/`pr-verify` Step 1 が `--search "[issue_id]"` で issue 番号一致 PR を引く（`.claude/skills/pr-fix/SKILL.md` L120） |
| **`kaji pr list --head BR`** | `glab mr list --source-branch BR -F json` | skill が branch 名から PR を引く fallback。**`--head` → `--source-branch` 変換** |
| `kaji pr merge 42` | `glab mr merge 42` | `--squash` / `--rebase` flag は付加しない |
| `kaji pr merge 42 --squash` / `--rebase` | （subprocess 起動せず） | exit 2 + 明示エラー |
| `kaji pr merge <branch_name>` | `glab mr merge <iid>` | `issue-close` Step 3 が `kaji pr merge [branch_name]` で呼ぶ（`.claude/skills/issue-close/SKILL.md` L105）。kaji 側で branch 名 → MR IID 解決後に glab に渡す |
| `kaji pr comment 42 --body B` | `glab mr note 42 --message B` | sub 名 `comment` → `note`、`--body` → `--message` |
| `kaji pr review 42 --approve --body[-file] B` | `glab mr note 42 --message <marked B>` → `glab mr approve 42` | body は捨てない / 順序保証 / note 失敗時 approve skip。後述 § Tier A `reviews` contract の marker 仕様参照 |
| `kaji pr review 42 --request-changes --body[-file] B` | `glab mr note 42 --message <marked B>` → 必要なら `glab mr revoke 42` | 未 approve 時は revoke skip + rc=0。注入する body marker により `reviews` 列挙時に `state="CHANGES_REQUESTED"` として再構成可能（後述） |

#### `kaji pr` dispatcher（Tier A）

| kaji 引数 | 内部呼出 |
|-----------|----------|
| `kaji pr review-comments 42 [--json fields] [--jq expr]` | `glab api projects/:id/merge_requests/42/discussions` → GitHub `pulls/<N>/comments` 互換 subset に変換 |
| `kaji pr reviews 42 [--json fields] [--jq expr]` | 後述「`reviews` contract の合成方法」参照（approvals API + body-marked notes の join） |
| `kaji pr reply-to-comment 42 --to <provider-local-id> --body B` | discussion thread reply API。`<provider-local-id>` は kaji が独自に発行する `<discussion_id>:<note_id>` opaque 形式 |

#### `reviews` contract の合成方法（指摘 #3 への対応）

GitHub `gh api repos/.../pulls/<N>/reviews` は各 review について `{user, state, body, submitted_at, ...}` を返す。skill `pr-fix` / `pr-verify` は最低限 `.user.login` / `.state` / `.body` を参照する（`.claude/skills/pr-fix/SKILL.md` L140 / `.claude/skills/pr-verify/SKILL.md` L154）。

GitLab には GitHub の review state（`APPROVED` / `CHANGES_REQUESTED` / `COMMENTED`）に対応する正規 entity が存在せず、approvals API だけでは body と state の組を再構成できない（OQ-2 § 設計原則 #2 / 確定事項 #7）。本設計では以下の方式で contract を成立させる:

1. **書き込み側（`kaji pr review`）**: `--approve` / `--request-changes` で kaji が note 投稿する際、body 先頭に **kaji 専用 marker** を埋め込む:
   - `<!-- kaji-review: state=APPROVED -->\n<body>` （`--approve` 時）
   - `<!-- kaji-review: state=CHANGES_REQUESTED -->\n<body>` （`--request-changes` 時）

   marker は HTML コメントなので GitLab UI 表示時には見えない。同 note の本文部分は元の body をそのまま保持する（body は捨てない原則を遵守）。

2. **読み込み側（`kaji pr reviews`）**: 以下を join する:
   - `glab api projects/:id/merge_requests/:iid/notes`（system note を除外）から、body が `<!-- kaji-review: state=... -->` で始まる note を抽出
   - 各 note の `{author.username, body（marker を剥がしたもの）, created_at, marker から復元した state}` を GitHub `reviews` 互換 entry に整形
   - approvals API (`/approvals`) を別途取得し、`approved_by[]` のうち kaji marker note を **持たない** approver は `state="APPROVED"` / `body=""` の暗黙 review として補完する（GitLab UI で直接 approve した場合の救済路。skill が `.body` を空文字として扱うことは確認済み）

3. **state 一覧**: `APPROVED` / `CHANGES_REQUESTED` / `COMMENTED`（marker 付きで `state=COMMENTED` の note も将来拡張用に予約。本 Issue では `--approve` / `--request-changes` の 2 経路のみ生成する）

4. **kaji 単独契約**: 本 marker 仕様は kaji 内部の契約であり、GitLab UI / API のどこにも自然に存在しない。skill は marker を直接見ず、`kaji pr reviews` の正規化済み出力 (`.state` / `.body`) のみを参照する。

##### 代替案検討（採用しない理由）

- **(α) skill 側を `review-comments` + top-level note 中心に書き換える**: skill 修正範囲が広く、本 Issue のスコープを超える。`pr-fix` / `pr-verify` の他に CLAUDE.md に skill 設計原則も書き換えが必要
- **(β) approvals API のみで `state` を再構成し `body` を空にする**: `pr-fix` の指摘内容が空配列としか見えず人間レビューの内容を skill が消失させる。OQ-2 § body 取り扱い原則「body は捨てない」に反する
- **(γ) discussions API から先頭 note を全部 `state="COMMENTED"` で返す**: approve / request-changes の意思表示が見えなくなり contract 不成立

#### 未対応 sub

`approvers` / `checkout` / `diff` / `for` / `issues` / `rebase` / `reopen` / `revoke`（直接呼び出し）/ `subscribe` / `todo` / `update` / `unsubscribe` / `delete` は **silent passthrough せず** `EXIT_INVALID_INPUT` (rc=2) で明示エラー化。

### 出力

| 経路 | 出力 |
|------|------|
| `kaji issue *` (gitlab) | `glab issue <sub>` の stdout/stderr/exit code をそのまま伝播。`view` の `--json` / `--jq` は既存 `_apply_jq` を経由しつつ、`description` → `body` / `iid` → `number` 等のマッピング後に適用 |
| `kaji pr view 42 --json number,state,title` | GitHub `gh pr view --json` 互換 shape の JSON。GitLab `iid` / `state='opened'` / `description` は kaji 内で正規化 |
| `kaji pr review --approve --body B` | 成功時 rc=0、note 投稿失敗時は note 段階で fail（approve は実行しない）、approve 失敗時は note は残るが rc≠0 |
| `kaji pr merge 42 --squash` | rc=2、stderr に `Error: 'kaji pr merge' rejects --squash/--rebase under provider.type='gitlab'`（GitHub mode と同様の guard） |
| `kaji pr <未対応 sub>` | rc=2、stderr に `Error: 'kaji pr <sub>' is not supported under provider.type='gitlab'.` + 代替案（あれば） |
| `normalize_id`（GitLab 拡張） | `ResolvedId(kind="gitlab" \| "remote_cache", value=<digits>, raw=<input>)` |

### 使用例

```bash
# gitlab provider 配下
$ kaji issue view gl:42 --json title,body,labels
{"title": "...", "body": "...", "labels": [...]}

$ kaji pr review 42 --request-changes --body-file review.md
# → glab mr note 42 --message "$(cat review.md)" を実行
# → 続けて glab mr revoke 42 を試行（未 approve なら skip）

$ kaji pr merge 42 --squash
Error: 'kaji pr merge' rejects --squash/--rebase under provider.type='gitlab'
# rc=2

$ kaji pr approvers 42
Error: 'kaji pr approvers' is not supported (only Tier A/B subcommands).
# rc=2

# local provider 配下（cache 読み取り）
$ kaji issue view gl:42  # .kaji/cache/gl-42.json を read-only で参照
```

### エラー

| 条件 | 戻り値 | stderr |
|------|--------|--------|
| `normalize_id("gh:N", provider="gitlab")` | `ValueError` | cross-provider id 不可 |
| `normalize_id("gl:N", provider="github")` | `ValueError` | 同上 |
| `kaji issue *` で `provider.gitlab.repo` 不在 | rc=2 | `provider.gitlab.repo` 設定要求 |
| `kaji pr review --approve` の note 投稿失敗 | rc≠0 | note 段階で fail。approve は実行しない |
| `kaji pr merge --squash`/`--rebase` | rc=2 | 明示拒否メッセージ |
| `kaji pr <未対応 sub>` | rc=2 | 未対応エラー（silent passthrough しない） |
| `glab` CLI 不在 | rc=3 (`EXIT_RUNTIME_ERROR`) | install 案内（GitHub mode の `_GH_MISSING_GUIDANCE` と同型） |
| `gl:N` への write（edit/comment/close）を `provider=local` 下で実行 | rc=2 | read-only 拒否（`gh:N` と同型） |

## 制約・前提条件

- 子 Issue #1 (`GitLabProvider` 本体実装) が **完了済み** であることが前提（依存関係は Issue 本文に明記済み）
- `glab` CLI v1.x が PATH にあることをユーザー責任で前提とする（`shutil.which("glab")` で fail-fast）
- `provider.gitlab.repo` (`group/project` 形式) が config に設定されていること
- GitLab project の merge method = `Merge commit`、squash = `Do not allow` または `Allow` であることをユーザー責任で前提とする（`kaji-pr-mr-bridge.md` § merge method 保証範囲。`kaji` 側で preflight しない）
- GitHub mode の挙動は **bit-exact に維持** する（既存テストを破らない）
- skill 側に **CLI コマンド/引数レベルの GitHub/GitLab 分岐を入れない**（OQ-2 § 設計原則 #4）。ただし以下 2 種類の skill 修正は本 Issue スコープに含む（指摘 #1 への対応）:
  1. **`provider_type` 受理拡張**: `i-pr` / `issue-close` の Step 0 が `github` / `local` の 2 値しか受理しない箇所を `gitlab` も許容するように拡張する（kaji pr / kaji issue が gitlab で動くようになるため、`i-pr` の ABORT は不要になる）
  2. **PR URL 正規表現の forge 対応**: `i-pr` Step 4 が `https://github.com/.../pull/<N>` 固定の regex を持つ箇所を `provider_type` に応じた検証（gitlab.com の MR URL 形式 `https://gitlab.com/.../merge_requests/<N>` も許容）に置き換える
  - これらは「provider 別分岐の本質を CLI に閉じ込める」設計原則と矛盾しない。skill 側に残るのは **provider 種別の受理判定** だけで、引数体系や出力 shape の分岐は持ち込まない
- `reply-to-comment` の `comment_id` は **GitLab 側で discussion_id + note_id を復元できる provider-local ID** として kaji が独自フォーマットを発行する。GitHub 数値 ID をそのまま再利用しない。具体的フォーマットは provider 内部関数に閉じ、本 Issue 内で決定する（候補: `<discussion_id>:<note_id>` 形式の opaque string）
- Tier A の出力 shape は GitHub の **正本 subset** とする。GitLab 固有 field（`discussion_id` / `note_id` / `resolved` / `position`）は internal で保持し、JSON の top-level には**出さない**

## 変更スコープ

### 影響モジュール・ファイル

| ファイル | 変更内容 |
|---------|----------|
| `kaji_harness/providers/__init__.py` | `ResolvedKind` Literal に `"gitlab"` 追加。`normalize_id` に `gl:N` パターンと `provider_name="gitlab"` 受理を追加 |
| `kaji_harness/providers/gitlab.py` | PR contract 用 helper を追加（`pr_view` / `pr_list` / `pr_create` / `pr_merge` / `pr_comment` / `pr_review_approve` / `pr_review_request_changes` / `pr_review_comments` / `pr_reviews` / `pr_reply_to_comment`）。GitHub 互換 field 変換と note → approve/revoke シーケンスを provider 内に閉じる |
| `kaji_harness/cli_main.py` | `_handle_issue` の GitLab 分岐 (`glab issue` 転送 + `--repo` 注入)。`_handle_pr` の GitLab 分岐 (Tier B sub の glab 呼出 + Tier A の discussion API + 未対応 sub の明示エラー + merge guard)。`_PR_BUILTIN_SUBCOMMANDS` を provider 別に dispatch する構造に再編 |
| `tests/test_providers_normalize_id.py` | `gl:N` / 数値 / cross-provider rejection の Small テスト追加 |
| `tests/test_providers_gitlab.py` | PR contract 各 sub の Small テスト追加（subprocess を `monkeypatch` でモック） |
| `tests/test_phase3c_dispatcher.py` または新規 `tests/test_phase4_dispatcher_gitlab.py` | `kaji issue` / `kaji pr` GitLab dispatcher の Medium テスト |
| `.claude/skills/i-pr/SKILL.md` | Step 0 の `provider_type` 受理を `github` / `gitlab` に拡張。Step 4 の PR URL 正規表現を gitlab.com MR URL 形式 `https://gitlab.com/.../merge_requests/<N>` も許容するように置き換える。エラー時 suggestion の `gh auth status` を provider に応じた表現（`gh auth status` / `glab auth status`）に振り分け |
| `.claude/skills/issue-close/SKILL.md` | Step 0 / 実行手順分岐を `github` / `gitlab` / `local` の 3 分岐に拡張。`gitlab` 分岐は `github` 分岐と同形（`kaji pr merge` / worktree 削除 / branch 削除 / `git pull` / `kaji issue close`）で動作する |

### 影響しない範囲

- `GitLabProvider` 本体実装（issue CRUD）→ 子 Issue #1 で完了済み前提
- `resolve_pr_context` → 子 Issue #3
- 実 GitLab 通信 E2E → 子 Issue #6
- `pr-fix` / `pr-verify` skill の本文 → **修正不要**。`kaji pr list --search` / `--head` / `view --comments` / `reviews` / `review-comments` / `reply-to-comment` の引数体系と出力 shape は本 Issue の CLI 写像表で gh 互換に固定されるため、skill 側は GitHub mode と同コマンドで動く（指摘 #2 で具体化済み）

## 方針（Minimal How）

### 1. `normalize_id` 拡張

`_GH_PREFIX_RE` と並列に `_GL_PREFIX_RE = re.compile(rf"^gl:({_POS_INT})$")` を追加。判定順序:

```python
# pseudo-code
if provider_name not in {"github", "local", "gitlab"}: raise
m = _GL_PREFIX_RE.match(raw)
if m:
    if provider_name == "gitlab":
        return ResolvedId(kind="gitlab", value=m.group(1), raw=raw)
    if provider_name == "local":
        return ResolvedId(kind="remote_cache", value=m.group(1), raw=raw)
    raise ValueError(f"gl:N requires provider in {{gitlab, local}}, got {provider_name!r}")
m = _GH_PREFIX_RE.match(raw)
if m:
    if provider_name == "gitlab":
        raise ValueError(f"gh:N is not accepted under provider.type='gitlab'")
    # 既存ロジック...
# numeric only
if _NUMERIC_RE.match(raw):
    if provider_name == "gitlab":
        return ResolvedId(kind="gitlab", value=raw, raw=raw)
    # 既存ロジック...
# local-form / short-form は gitlab では reject
```

`ResolvedKind = Literal["github", "local", "remote_cache", "gitlab"]`。`gh:N` の `provider=gitlab` rejection と `gl:N` の `provider=github` rejection は対称な error message にする。

### 2. `kaji issue` dispatcher

`_handle_issue` 内で `isinstance(provider, GitLabProvider)` 分岐を追加。**純粋な subprocess passthrough ではなく、sub 名 / flag 名の写像層を kaji 内に持つ**（指摘 #2 への対応）:

```python
# pseudo-code
ISSUE_SUB_MAP = {
    "create":  ("create",  {"--body": "--description"}),
    "view":    ("view",    {}),                        # JSON shape は別途 normalize
    "edit":    ("update",  {"--body": "--description"}),
    "list":    ("list",    {}),
    "close":   ("close",   {}),
    "comment": ("note",    {"--body": "--message"}),
}
if isinstance(provider, GitLabProvider):
    if not config.provider.gitlab.repo: return error
    sub = args[0]
    if sub == "context": return _handle_issue_context(...)  # 既存の明示拒否を維持（別 Issue #3）
    if sub not in ISSUE_SUB_MAP: return _reject_unsupported_issue_sub(sub)
    glab_sub, flag_map = ISSUE_SUB_MAP[sub]
    forwarded = _rewrite_flags(args[1:], flag_map)
    forwarded = [a for a in forwarded if a != "--commit"]  # silent strip
    return _forward_to_glab("issue", [glab_sub, *forwarded], repo=config.provider.gitlab.repo)
```

`_forward_to_glab` を `_forward_to_gh` と対称な薄い wrapper として新設し、`--repo group/project` と `--hostname gitlab.com` を末尾追加する（`GitLabProvider._run_glab` と同方針）。

**指摘 #4「passthrough を選ぶ理由」への回答**: `kaji issue view` の JSON 正規化など出力 shape 変換が必要な経路では既存 `GitLabProvider.view_issue` を再利用して GitHub 互換 dict に詰め、そこから `_apply_jq` で `--json` / `--jq` を適用する（`_handle_issue_context` と同じ責務分離）。一方 `create` / `edit` / `close` / `comment` などの mutating 系は **既存 `GitLabProvider` の create/edit/close/comment_issue メソッドを再利用** しても良いが、本 Issue では skill 側 contract（exit code / 出力 / `--commit` 等の flag 受理）と既存 `_forward_to_gh` 構造の対称性を優先し、**dispatch 時に CLI subprocess を直接呼ぶ薄いラッパー** を採用する。理由:

- `_forward_to_gh` は引数を pass-through するだけで例外を握りつぶさず、exit code 透過性を確保している。`GitLabProvider` の各メソッド経由にすると `GitLabProviderError` を CLI exit code に変換するレイヤを別途作る必要がある
- 既存テスト (`test_phase3c_dispatcher.py`) が subprocess 直叩き前提でモックしている構造と一致させやすい
- 出力 shape 変換が必要な `view` / `list` のみ provider 経由（既存実装再利用）にし、それ以外は薄い passthrough にすることで責務を分離できる

### 3. `kaji pr` dispatcher

`_handle_pr` の構造を以下のように再編:

```python
# pseudo-code
if isinstance(provider, GitLabProvider):
    sub = args[0] if args else None
    if sub in TIER_B_SUBS:           # create/view/list/merge/comment/review
        return _dispatch_pr_gitlab_tier_b(sub, args[1:], provider)
    if sub in TIER_A_SUBS:           # review-comments/reviews/reply-to-comment
        return _dispatch_pr_gitlab_tier_a(sub, args[1:], provider)
    return _reject_unsupported_pr_sub(sub)  # rc=2
# GitHub mode は既存パスに変更なし
```

#### Tier B 詳細

- `create`: argparse で `--title` / `--body` / `--base` を受理し、`--body` → `--description`、**`--base` → `--target-branch`** に変換して `glab mr create` を起動。skill `i-pr` Step 4 の `kaji pr create --base main` 呼出に対応
- `merge`: `--squash`/`--rebase` を guard（subprocess 起動前に rc=2）。引数が branch 名の場合は `_GitLabProvider.resolve_mr_iid_from_branch(branch)` で MR IID に解決してから `glab mr merge <iid>`。`issue-close` Step 3 の `kaji pr merge [branch_name]` 呼出に対応
- `view <id>`: `--comments` flag が指定されたら `glab mr view <id> --comments` をそのまま起動して人間可読出力を流す（GitHub mode の `gh pr view --comments` と対称）。それ以外は `glab mr view <id> --output json` → `_GitLabPrShape.to_github(payload)` → `_apply_jq` で `--json` / `--jq` を適用
- `list`: `--search` / `--head` を受理。**`--head BR` → `--source-branch BR`** に変換して `glab mr list --output json` を起動。出力を `_GitLabPrShape.to_github_list(payload)` で GitHub `pulls` array shape に変換
- `comment`: sub 名 `comment` → `note` 変換、`--body` → `--message` 変換して `glab mr note --message <body>` を起動
- `review --approve --body[-file]`: body 先頭に kaji marker `<!-- kaji-review: state=APPROVED -->\n` を付与した文字列で `glab mr note --message <marked>` を起動。**成功時のみ** `glab mr approve` を起動（note 失敗で approve はスキップ）
- `review --request-changes --body[-file]`: 同様に `<!-- kaji-review: state=CHANGES_REQUESTED -->\n` marker を付与して `glab mr note` を起動。approval_state を確認した上で approve 済なら `glab mr revoke`、未 approve なら revoke は no-op として skip。skip した場合も rc=0 を返す（contract 上「note + 差し戻し意思表示」は成立）

#### Tier A 詳細

- `review-comments`: `_GitLabProvider.list_discussions(mr_iid)` を呼び、GitHub `pulls/<N>/comments` 互換 subset (`{id, path, line, body, user.login}`) に変換。`id` は `<discussion_id>:<note_id>` opaque 形式
- `reviews`（指摘 #3 への対応）: 以下の合成を `_GitLabProvider.list_reviews(mr_iid)` 内に閉じる:
  1. `glab api .../notes` から body が `<!-- kaji-review: state=... -->` で始まる note を抽出
  2. 各 note を `{user.login, state, body（marker を剥がしたもの）, submitted_at}` に変換
  3. approvals API (`/approvals`) の `approved_by[]` のうち kaji marker note を持たない approver は `state="APPROVED"` / `body=""` の暗黙 review として補完
  4. 結果配列を `submitted_at` 昇順に並べて返す
- `reply-to-comment`: kaji が発行する provider-local ID `<discussion_id>:<note_id>` を分解し、`glab api projects/:id/merge_requests/<iid>/discussions/<discussion_id>/notes` に POST。`comment_id` の形式バリデーションは subprocess 起動前に行う（rc=2 fail-fast）

`_GitLabPrShape` は `kaji_harness/providers/gitlab.py` に新設する pure 変換層。`to_github(view_payload)` / `to_github_list(list_payload)` / `to_github_review_comments(discussions_payload)` / `to_github_reviews(notes_payload, approvals_payload)` の 4 メソッドを Small テスト対象とする。

### 4. 未対応 sub の明示エラー化

`_GLAB_MR_SUPPORTED = {"create", "view", "list", "merge", "comment", "review", "review-comments", "reviews", "reply-to-comment"}` を whitelist として持ち、それ以外を即座に rc=2 で reject。stderr に `Tier A/B subcommands` の一覧を案内する。

## テスト戦略

### 変更タイプ

実行時コード変更（`normalize_id` / dispatcher / provider helper の追加）。docs-only / metadata-only / packaging-only ではない。

### Small テスト

- **`normalize_id` 拡張**:
  - `gl:42` × `provider=gitlab` → `kind="gitlab"`
  - `gl:42` × `provider=local` → `kind="remote_cache"`
  - `gl:42` × `provider=github` → `ValueError`
  - `gh:42` × `provider=gitlab` → `ValueError`
  - 数値 × `provider=gitlab` → `kind="gitlab"`
  - `local-pc1-3` × `provider=gitlab` → `ValueError`
  - leading-zero (`gl:042`) / `gl:0` / 空文字 → `ValueError`
- **`ResolvedKind` Literal**: mypy で `"gitlab"` が型としても通ること（typecheck pass）
- **`kaji pr` 引数バリデーション**:
  - `merge --squash` / `--rebase` の早期 reject（subprocess 起動前に rc=2）
  - 未対応 sub (`approvers` / `checkout` 等 12 種) すべてが rc=2 + 明示エラー
  - `review --approve` と `--request-changes` の同時指定 reject
  - `--body` と `--body-file` の同時指定 reject（既存 `_read_body_arg` 再利用）
- **gh → glab 引数写像**:
  - `kaji pr create --base main` → glab argv に `--target-branch main` が含まれる
  - `kaji pr list --head feat/x` → glab argv に `--source-branch feat/x` が含まれる
  - `kaji pr list --search foo` → glab argv に `--search foo` が含まれる
  - `kaji issue edit 42 --body B` → glab argv に `update 42 --description B` が含まれる
  - `kaji issue comment 42 --body B` → glab argv に `note 42 --message B` が含まれる
- **GitHub 互換 shape 変換**:
  - `_GitLabPrShape.to_github({"iid": 42, "state": "opened", "description": "x", ...})` → `{"number": 42, "state": "OPEN", "body": "x", ...}` の単体マッピング
  - `to_github_review_comments(discussions_payload)` → GitHub `pulls/<N>/comments` subset（`id` は `<discussion_id>:<note_id>` 形式）
  - `to_github_reviews(notes_payload, approvals_payload)`:
    - kaji marker `<!-- kaji-review: state=APPROVED -->` 付き note → `{state: "APPROVED", body: "<marker 剥がし後>", user.login: ..., submitted_at: ...}`
    - kaji marker `<!-- kaji-review: state=CHANGES_REQUESTED -->` 付き note → `state: "CHANGES_REQUESTED"`
    - approvals API の `approved_by[]` のうち marker note を持たない approver → `{state: "APPROVED", body: "", ...}` 補完
    - marker 形式不正 / state 不明値 → 該当 note は無視して system note 同等扱い（fail-fast しない）

### Medium テスト

- **`kaji issue` GitLab dispatcher**: `subprocess.run` を `monkeypatch` でフックし、`kaji issue create --title T` が `glab issue create --title T --repo group/project --hostname gitlab.com` を起動するか
- **`kaji issue comment` → `glab issue note` 変換**: skill 側 `comment` のまま GitLab 側 `note` に変換されるか
- **`kaji pr review --approve --body-file -` シーケンス**: stdin 経由 body → marker `<!-- kaji-review: state=APPROVED -->` 付与 → `glab mr note --message <marked>` → `glab mr approve` の **順序** とそれぞれの引数を assertion
- **`kaji pr review --request-changes` 未 approve**: approval_state mock で未 approve を返した時、marker 付き note のみ起動・revoke skip・rc=0
- **`kaji pr review --request-changes` approve 済**: approval_state mock で approve 済を返した時、marker 付き note + revoke の順で起動
- **`kaji pr merge --squash` 拒否**: subprocess を起動せず rc=2 で fail-fast
- **`kaji pr merge feat/local-pc5090-6`**: branch 名から MR IID 解決 → `glab mr merge <iid>` の起動引数を assertion
- **`kaji pr <未対応 sub>`**: glab を起動せず rc=2 で reject
- **`kaji pr review --approve` で note 失敗時**: note rc≠0 → approve スキップ → 全体 rc≠0
- **`kaji pr view 42 --json number,state,title --jq '.number'`**: GitLab `view --output json` mock → GitHub field 変換 → jq 適用が連鎖して動作
- **`kaji pr view 42 --comments`**: `glab mr view 42 --comments` がそのまま起動され、stdout が pass-through される
- **`kaji pr list --head feat/x --json number`**: glab argv に `--source-branch feat/x` が含まれ、出力が GitHub `pulls` array shape に変換される
- **`kaji pr reviews 42` の合成**: notes mock + approvals mock を入力に、kaji marker note → review entry 変換、approvals 単独 approver → 暗黙 `state="APPROVED"` 補完、marker 不正 note → 無視、の 3 ケースを統合した GitHub 互換 shape を返す
- **skill `i-pr` provider 拡張**: `provider_type=gitlab` を Step 0 で受理し ABORT verdict を出さない（手動実行 + 簡易 e2e）。PR URL regex が `https://gitlab.com/.../merge_requests/42` を受理する

### Large テスト

本 Issue では **追加しない**。理由:

- 子 Issue #6 が `make test-large-gitlab` + 実 GitLab project への E2E ラウンドトリップを担当する責務として明示されている
- 本 Issue で実 `glab` 疎通テストを書くと、子 Issue #6 と二重実装になり、CI 上での実 GitLab 認証情報依存も増える
- `docs/dev/testing-convention.md` の 4 条件のうち「想定不具合パターンが既存テストまたは既存品質ゲートで捕捉済み」を子 Issue #6 が担保する関係にある
- ただし「追加しない」=「Large テスト不要」ではなく、子 Issue #6 への引き継ぎ事項として本設計書 § 影響ドキュメント / Issue 完了条件で明記する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新ライブラリ採用なし。`glab` 採用は EPIC `local-pc5090-4` で既決 |
| `docs/ARCHITECTURE.md` | あり | provider 別 dispatch 方針が `kaji pr` にも拡張される旨を反映（既に GitLab provider が存在するため軽微） |
| `docs/dev/` | なし | 開発ワークフローへの影響なし（skill 側に分岐入らない設計） |
| `docs/reference/` | なし | コーディング規約変更なし |
| `docs/cli-guides/` | あり | `kaji pr` / `kaji issue` の GitLab mode 挙動を子 Issue #5 が新設する `gitlab-mode.md` に統合する責務。本 Issue では `kaji pr review --approve/--request-changes` の note + approve/revoke シーケンスと未対応 sub 一覧を `gitlab-mode.md` に組み込む情報源として記述メモを残す（`draft/notes/` に切り出し） |
| `CLAUDE.md` | なし | プロジェクト規約変更なし（`Merge: --no-ff only` を kaji 側 guard で守るのみ） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| OQ-2 決定文書 | `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md` | 「skill 側 contract は GitHub 互換 subset を正本」「GitLab 固有のコマンド名差分（`comment` ↔ `note`、`review --approve/--request-changes` ↔ `approve`/`revoke`）は provider 内部で吸収」「skill が使わない `glab mr` 固有 sub は silent passthrough せず明示的な未対応エラーで失敗」（§ 設計原則） |
| 既存 `_handle_pr` 実装 | `kaji_harness/cli_main.py:681-729` | GitHub mode の dispatch 構造 (`_PR_BUILTIN_SUBCOMMANDS` + `_forward_to_gh`)。本 Issue で provider 別 dispatch に拡張する起点 |
| 既存 `normalize_id` 実装 | `kaji_harness/providers/__init__.py:125-234` | `gh:N` / 数値 / `local-...` / 短縮形の判定順序。`gl:N` 拡張で対称構造を維持 |
| 既存 `GitLabProvider` 実装 | `kaji_harness/providers/gitlab.py:1-336` | `glab issue note` の起動方法（`_run_glab` + `--hostname gitlab.com` 強制注入）。`kaji pr` 用 helper も同型で追加する |
| `glab` CLI ドキュメント | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/mr/ | `glab mr {create, view, list, merge, note, approve, revoke}` の引数体系。`glab mr approve` / `revoke` は body 引数を持たない (note との分離が必要)、`glab mr list -F json` で構造化 JSON が返る。`glab mr create --target-branch BR`、`glab mr list --source-branch BR`、`glab mr view <iid> --comments` の引数名 |
| `glab issue` CLI ドキュメント | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/issue/ | sub 名は `create / view / list / update / close / note`（GitHub の `edit` / `comment` と命名差。`update --description`、`note --message` で body 系 flag が異なる） |
| skill `i-pr` | `.claude/skills/i-pr/SKILL.md` L37, L73-L118, L163-L235 | `provider_type` が `github` 以外で ABORT する Step 0 と、`kaji pr create --base main`、PR URL `https://github.com/.../pull/<N>` 正規表現の前提。本 Issue で `gitlab` を許容するように拡張 |
| skill `pr-fix` / `pr-verify` | `.claude/skills/pr-fix/SKILL.md` L120-L141, `.claude/skills/pr-verify/SKILL.md` L134-L155 | `kaji pr list --search` / `--head`、`kaji pr view --comments`、`kaji pr reviews --jq '.[].state, .body'`、`kaji pr review-comments` の呼び出しパターン。本 Issue の CLI 写像表が要求する gh 互換引数の正本 |
| skill `issue-close` | `.claude/skills/issue-close/SKILL.md` L62-L69, L102-L110 | `provider_type` 分岐が `github` / `local` の 2 値のみ、`kaji pr merge [branch_name]` で branch 名から merge する呼び出し方。本 Issue で `gitlab` 分岐を追加 |
| GitLab REST API: Discussions | https://docs.gitlab.com/ee/api/discussions.html#merge-requests | `GET /projects/:id/merge_requests/:mr_iid/discussions` で discussion_id + notes 配列が返る。reply は `POST .../discussions/:discussion_id/notes`。GitHub の review-comment と shape が異なるため kaji 側で変換必須 |
| GitLab REST API: Merge Request approvals | https://docs.gitlab.com/ee/api/merge_request_approvals.html | `GET /projects/:id/merge_requests/:mr_iid/approvals` で `approved_by` 配列と approval_state が取れる。`review --request-changes` の未 approve 判定に使用 |
| GitHub CLI ドキュメント | https://cli.github.com/manual/gh_pr_view | `gh pr view --json number,state,title,body,...` の field 名一覧。kaji の正本 shape として参照 |
| `docs/dev/testing-convention.md` | `docs/dev/testing-convention.md` | テストサイズ S/M/L の定義と「変更固有検証で十分な理由を明記」する判定ルール（§ テスト戦略の原則） |
| `CLAUDE.md` Git ルール | `CLAUDE.md` § Git & GitHub | `Merge: --no-ff only (squash merge prohibited)` — `kaji pr merge` の `--squash` / `--rebase` 拒否の根拠 |
