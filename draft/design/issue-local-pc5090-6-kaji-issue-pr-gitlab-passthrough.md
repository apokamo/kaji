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

`provider.type='gitlab'` 配下で以下を受理:

- `kaji issue create / view / edit / list / close / comment [args...]` → `glab issue <sub> [args...]` へ subprocess 転送
- `--repo <group/project>` を `provider.gitlab.repo` から強制注入（GitHub mode と同型 guard）
- `--commit` flag は LocalProvider 専用なので silent に剥がす（GitHub mode と同挙動）
- `kaji issue context <id>` は本 Issue では **既存の明示拒否を維持** → 別 Issue (#3) 範囲

#### `kaji pr` dispatcher（Tier B）

`provider.type='gitlab'` 配下で以下を受理:

| sub | 入力例 | 内部呼出 |
|-----|--------|----------|
| `create` | `kaji pr create --title T --body B` | `glab mr create --title T --description B`（field 名差分を kaji が吸収） |
| `view` | `kaji pr view 42 [--json fields] [--jq expr]` | `glab mr view 42 --output json` → GitHub 互換 field に変換して `--json` / `--jq` 適用 |
| `list` | `kaji pr list [--json fields] [--jq expr]` | `glab mr list -F json` → GitHub 互換 field に変換 |
| `merge` | `kaji pr merge 42` | `glab mr merge 42`（`--squash` / `--rebase` flag は付加しない） |
| `merge` (拒否) | `kaji pr merge 42 --squash` / `--rebase` | exit 2 + 明示エラー |
| `comment` | `kaji pr comment 42 --body B` | `glab mr note 42 --message B` |
| `review --approve --body[-file]` | `kaji pr review 42 --approve --body B` | `glab mr note 42 --message B` → `glab mr approve 42` |
| `review --request-changes --body[-file]` | `kaji pr review 42 --request-changes --body B` | `glab mr note 42 --message B` → `glab mr revoke 42`（未 approve 時は revoke skip） |

#### `kaji pr` dispatcher（Tier A）

| sub | 内部呼出 |
|-----|----------|
| `review-comments 42` | `glab api projects/:id/merge_requests/42/discussions` → GitHub `pulls/<N>/comments` 互換 subset に変換 |
| `reviews 42` | `glab api projects/:id/merge_requests/42/approvals`（+ approval_state）→ GitHub `pulls/<N>/reviews` 互換 subset に変換 |
| `reply-to-comment 42 --to <provider-local-id> --body B` | discussion thread reply API。`<provider-local-id>` は kaji が独自に発行する形式（後述「制約」） |

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
- skill 側に GitHub/GitLab 分岐を **入れない**（diff で確認）
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

### 影響しない範囲

- `GitLabProvider` 本体実装（issue CRUD）→ 子 Issue #1 で完了済み前提
- `resolve_pr_context` → 子 Issue #3
- 実 GitLab 通信 E2E → 子 Issue #6
- skill ファイル (`.claude/skills/`) → 修正不要（contract 不変）

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

`_handle_issue` 内で `isinstance(provider, GitLabProvider)` 分岐を追加:

```python
# pseudo-code
if isinstance(provider, GitLabProvider):
    if not config.provider.gitlab.repo: return error
    forwarded = [a for a in raw_args if a != "--commit"]
    return _forward_to_glab("issue", forwarded, repo=config.provider.gitlab.repo)
```

`_forward_to_glab` を `_forward_to_gh` と対称な薄い wrapper として新設。`--repo group/project` を末尾追加し、`glab` の subcommand 群（`create` / `view` / `edit` / `list` / `close`）をそのまま転送する。`comment` は GitLab では `note` だが、GitHub 互換命名を skill 側に出すため kaji 側で sub 名を変換: `args[0] == "comment"` を `note` に書き換えてから forward。

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

- `create` / `merge`: argparse で kaji 側 flag をパースし、`merge` の `--squash`/`--rebase` を guard。`glab mr <sub>` を起動
- `view` / `list`: `glab mr <sub> --output json` の出力を `_GitLabPrShape.to_github(payload)` で変換（field 名 / state / 配列構造を統一）。その後 `_apply_jq` で `--json` / `--jq` を適用
- `comment` → `glab mr note --message <body>` に変換（`comment` 名は skill 側に保つ）
- `review --approve --body[-file]` → `glab mr note --message <body>` を呼び**成功時のみ** `glab mr approve` を起動。note 失敗で approve はスキップ
- `review --request-changes --body[-file]` → `glab mr note --message <body>` を呼び、approval_state を確認した上で approve 済なら `glab mr revoke`、未 approve なら revoke は no-op として skip。skip した場合も rc=0 を返す（contract 上「note + 差し戻し意思表示」は成立）

#### Tier A 詳細

- `review-comments` / `reviews`: `_GitLabProvider.list_discussions(mr_iid)` / `list_approvals(mr_iid)` を呼び、GitHub 互換 subset に変換。`--json` / `--jq` は既存 `_compose_json_and_jq` + `_apply_jq` を再利用
- `reply-to-comment`: kaji が発行する provider-local ID を `(discussion_id, note_id)` に分解し、`glab api projects/:id/merge_requests/<iid>/discussions/<discussion_id>/notes` に POST

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
- **GitHub 互換 shape 変換**:
  - `_GitLabPrShape.to_github({"iid": 42, "state": "opened", "description": "x", ...})` → `{"number": 42, "state": "OPEN", "body": "x", ...}` の単体マッピング
  - `list_discussions` raw payload → GitHub `pulls/<N>/comments` subset

### Medium テスト

- **`kaji issue` GitLab dispatcher**: `subprocess.run` を `monkeypatch` でフックし、`kaji issue create --title T` が `glab issue create --title T --repo group/project --hostname gitlab.com` を起動するか
- **`kaji issue comment` → `glab issue note` 変換**: skill 側 `comment` のまま GitLab 側 `note` に変換されるか
- **`kaji pr review --approve --body-file -` シーケンス**: stdin 経由 body → `glab mr note --message <body>` → `glab mr approve` の **順序** とそれぞれの引数を assertion
- **`kaji pr review --request-changes` 未 approve**: approval_state mock で未 approve を返した時、note のみ起動・revoke skip・rc=0
- **`kaji pr review --request-changes` approve 済**: approval_state mock で approve 済を返した時、note + revoke の順で起動
- **`kaji pr merge --squash` 拒否**: subprocess を起動せず rc=2 で fail-fast
- **`kaji pr <未対応 sub>`**: glab を起動せず rc=2 で reject
- **`kaji pr review --approve` で note 失敗時**: note rc≠0 → approve スキップ → 全体 rc≠0
- **`kaji pr view 42 --json number,state,title --jq '.number'`**: GitLab `view --output json` mock → GitHub field 変換 → jq 適用が連鎖して動作

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
| `glab` CLI ドキュメント | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/mr/ | `glab mr {create, view, list, merge, note, approve, revoke}` の引数体系。`glab mr approve` / `revoke` は body 引数を持たない (note との分離が必要)、`glab mr list -F json` で構造化 JSON が返る |
| GitLab REST API: Discussions | https://docs.gitlab.com/ee/api/discussions.html#merge-requests | `GET /projects/:id/merge_requests/:mr_iid/discussions` で discussion_id + notes 配列が返る。reply は `POST .../discussions/:discussion_id/notes`。GitHub の review-comment と shape が異なるため kaji 側で変換必須 |
| GitLab REST API: Merge Request approvals | https://docs.gitlab.com/ee/api/merge_request_approvals.html | `GET /projects/:id/merge_requests/:mr_iid/approvals` で `approved_by` 配列と approval_state が取れる。`review --request-changes` の未 approve 判定に使用 |
| GitHub CLI ドキュメント | https://cli.github.com/manual/gh_pr_view | `gh pr view --json number,state,title,body,...` の field 名一覧。kaji の正本 shape として参照 |
| `docs/dev/testing-convention.md` | `docs/dev/testing-convention.md` | テストサイズ S/M/L の定義と「変更固有検証で十分な理由を明記」する判定ルール（§ テスト戦略の原則） |
| `CLAUDE.md` Git ルール | `CLAUDE.md` § Git & GitHub | `Merge: --no-ff only (squash merge prohibited)` — `kaji pr merge` の `--squash` / `--rebase` 拒否の根拠 |
