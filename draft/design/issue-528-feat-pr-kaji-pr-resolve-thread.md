# [設計] kaji pr resolve-thread: インラインコメント ID からレビュースレッドを解決する

Issue: #528

## 概要

`kaji pr resolve-thread <pr_id> --comment <comment_id>` を `kaji pr` の builtin に追加する。
REST のインラインレビューコメント ID（`reply-to-comment --to` と同じ ID）から、そのコメントが属する
GitHub のレビュースレッドを GraphQL で特定し、`resolveReviewThread` で解決する。`pr-verify` は、
「解消」または「反論 OK」と判定した指摘のスレッドをこのコマンドで閉じる。

## 背景・目的

### ユースケース

- **pr-verify agent** として、`pr-fix` が直した指摘を「解消」と判定したとき（または反論を受け入れたとき）に、
  GitHub GraphQL のスレッド ID（`PRRT_…`）を扱わず、すでに控えているインラインコメント ID を渡すだけで
  そのスレッドを解決にしたい。
- **人間のメンテナ** として、確認済みのスレッドが `isResolved: false` のまま残らないようにしたい。
  実例: apokamo/kamo2#2047 では指摘 3 件すべてに返信と確認を済ませた後も 3 スレッドが未解決のまま残った
  （readiness review で GraphQL 照会し `isResolved=false` / `viewerCanResolve=true` を確認済み）。

### 現状

- `kaji pr` の builtin は `review-comments` / `reviews` / `reply-to-comment`（`kaji_harness/commands/pr.py`
  の `_PR_BUILTIN_SUBCOMMANDS`）と `review-poll` だけで、スレッドを解決する手段がない。
- REST API にはレビュースレッドを解決する endpoint がなく、GraphQL の `resolveReviewThread` mutation だけが
  手段である（GraphQL の `PullRequestReviewThread` は REST に対応するリソースを持たない）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| skill が `gh api graphql` を直接呼ぶ | skill が `PRRT_…` ID とクエリ文字列を抱え、kaji の provider 層（`kaji pr`）から外れる。Issue 本文「現状の問題」3 点目で否定済み |
| `gh pr` の既存サブコマンドへ passthrough | `gh` CLI にスレッド解決のサブコマンドはない（`gh pr review` は review 投稿のみ） |
| 引数にスレッド ID（`PRRT_…`）を取る | skill がスレッド ID を取得する別手段を要し、目的（ID を意識しない）に反する |
| REST `GET /pulls/comments/{id}` の `in_reply_to_id` で根コメントを特定してから GraphQL で照合 | REST 呼出しが 1 回増えるうえ、根コメントだけを照合する前提（返信の返信が平坦化される挙動）に依存する。スレッド内の全コメントを GraphQL で照合すれば同じ目的を 1 系統の API で満たせる |

## インターフェース

### コマンド

```
kaji pr resolve-thread <pr_id> --comment <comment_id>
kaji pr resolve-thread --help
```

### 入力

| 引数 | 型 | 必須 | 説明 |
|------|----|------|------|
| `pr_id` | ASCII 10 進数字列（positional） | 必須 | PR 番号。`_is_ascii_decimal` で検証（既存 builtin と同じ） |
| `--comment` | ASCII 10 進数字列 | 必須 | インラインレビューコメントの REST ID（`kaji pr review-comments` の `.id`）。スレッドの根コメントでも返信でもよい |
| config `[provider.github] repo` | `owner/name` | 必須（既存の `_handle_pr` で検証済み） | 対象 repo。既存 builtin と同じく `_detect_repo(override=repo_override)` で解決する |

`--repo` / `-R` は受け付けない（既存 builtin `reply-to-comment` 等と同じ。argparse の unknown 引数として rc=2）。

### 出力

**stdout**（成功時のみ。1 行の JSON）:

```json
{"comment_id": "4432690867", "thread_id": "PRRT_kwDOPlQLMc6yTJlL", "result": "resolved"}
```

| フィールド | 値 |
|-----------|----|
| `comment_id` | 入力の `--comment`（文字列。GraphQL `fullDatabaseId` が BigInt を文字列で返すのに合わせる） |
| `thread_id` | 特定したスレッドの GraphQL node ID |
| `result` | `resolved`（今回 mutation で解決した） / `already_resolved`（解決済みだったため mutation を呼ばずに成功） |

**副作用**: `result=resolved` のときだけ `resolveReviewThread` mutation を 1 回呼ぶ。`already_resolved` では
書き込みを一切行わない（冪等）。

**終了コードと stderr**（`kaji_harness/commands/exit_codes.py` の規約に従う。stderr は `Error: ` で始まる 1 行以上）:

| 状況 | rc | stderr（要旨） |
|------|----|----------------|
| 解決した / 解決済みだった | 0 (`EXIT_OK`) | なし |
| 引数不足・未知の引数（argparse） | 2 | argparse の usage |
| `pr_id` が ASCII 10 進でない | 2 (`EXIT_INVALID_INPUT`) | `Error: PR_ID must be ASCII decimal, got: <v>` |
| `--comment` が ASCII 10 進でない | 2 | `Error: --comment COMMENT_ID must be ASCII decimal, got: <v>` |
| PR の全レビュースレッドを辿っても該当コメントがない（存在しない ID、別 PR のコメント、PR 会話欄のコメントや review 本文の ID など） | 2 | `Error: review comment <id> was not found in any review thread of PR #<pr> (<repo>).` |
| config 不在・不正 / provider 不正 | 2 | 既存 `_handle_pr` のメッセージ（変更なし） |
| `gh` が PATH にない | 3 (`EXIT_RUNTIME_ERROR`) | 既存 `_GH_MISSING_GUIDANCE`（`resolve-thread` を列挙に追加） |
| repo を特定できない | 3 | 既存 builtin と同文 |
| `gh api graphql` が非 0 終了（認証失敗、ネットワーク、PR が存在しない `Could not resolve to a PullRequest` 等の GraphQL エラー） | 3 | gh の stderr を中継し、続けて `Error: GitHub GraphQL request failed (<phase>).`（`<phase>` は `reviewThreads lookup` / `thread comments lookup` / `resolveReviewThread`） |
| GraphQL 応答が期待する形でない（JSON でない、`pullRequest` が null、必須フィールド欠落） | 3 | `Error: unexpected GitHub GraphQL response (<phase>): <要約>` |
| スレッドは未解決で `viewerCanResolve` が false | 3 | `Error: cannot resolve review thread <thread_id> for comment <id>: viewerCanResolve is false (insufficient permission).` |
| mutation 応答の `thread.isResolved` が true でない | 3 | `Error: resolveReviewThread did not resolve thread <thread_id> for comment <id>.` |

「コメントが見つからない」を 2 とするのは、原因が呼出側の指定した ID であり、同じ入力で再試行しても結果が
変わらないため。権限不足・API 失敗は環境・実行時の状態に依存するため 3 とする。

### 使用例

```bash
# pr-fix が控えた inline comment ID でスレッドを解決する
$ kaji pr resolve-thread 2047 --comment 4432690867
{"comment_id": "4432690867", "thread_id": "PRRT_kwDOPlQLMc6yTJlI", "result": "resolved"}

# もう一度実行しても書き込みはせず成功する（冪等）
$ kaji pr resolve-thread 2047 --comment 4432690867
{"comment_id": "4432690867", "thread_id": "PRRT_kwDOPlQLMc6yTJlI", "result": "already_resolved"}

# 返信コメントの ID からでも同じスレッドを引ける
$ kaji pr resolve-thread 2047 --comment 4434206283; echo "rc=$?"
{"comment_id": "4434206283", "thread_id": "PRRT_kwDOPlQLMc6yTJlI", "result": "already_resolved"}
rc=0

# 見つからない ID
$ kaji pr resolve-thread 2047 --comment 1; echo "rc=$?"
Error: review comment 1 was not found in any review thread of PR #2047 (apokamo/kamo2).
rc=2
```

pr-verify からの呼び出し（失敗しても verdict は変えない。D4）:

```bash
if out=$(kaji pr resolve-thread "$pr_id" --comment "$cid" 2>"$err_file"); then
  echo "$cid: $(echo "$out" | jq -r .result)"
else
  echo "$cid: 失敗 (rc=$?): $(head -n 3 "$err_file")"   # 確認結果コメントと完了報告に転記
fi
```

## 制約・前提条件

- **GitHub のみ**（D1）。ADR 004 により GitLab forge は削除済みで、`get_provider` は `github` だけを扱う。
  GitLab の discussion resolve も、未対応エラーの分岐も実装しない。
- **unresolve は追加しない**（D3）。
- 実行経路は既存 builtin と同じく `gh` CLI のサブプロセス（`gh api graphql`）。新しい依存ライブラリは追加しない。
- `gh api graphql` は `-f`（文字列）/ `-F`（型付き。`number` を Int として渡す）で GraphQL 変数を渡す。
  クエリ文字列にユーザー入力を埋め込まない（インジェクション防止）。
- コメント ID の照合には `PullRequestReviewComment.fullDatabaseId`（BigInt、JSON では文字列）を使う。
  `databaseId`（Int）は GitHub スキーマで deprecated（「64-bit を扱えないため `fullDatabaseId` を使う」）。
  2026-11-01 に introspection で確認し、apokamo/kamo2#2047 で REST の `id` と `fullDatabaseId` が一致することを確認した。
- ページ送り: `reviewThreads(first: 100)` と各スレッドの `comments(first: 100)` の両方で
  `pageInfo { hasNextPage endCursor }` を辿り、全件を照合する。100 件超でも「見つからない」と誤判定しない。
  1 クエリのノード数は 100 × 100 = 10,000 で、GitHub GraphQL の 1 クエリ上限（500,000 ノード）に収まる。
- GraphQL 応答は外部入力のため Pydantic v2 で検証する（AGENTS.md 常時適用ルール、
  `docs/reference/python/type-hints.md` § Pydantic と dataclass の使い分け）。
- `resolveReviewThread` の任意入力 `resolutionReason`（`ADDRESSED` / `WONT_FIX` / `INVALID`）は渡さない。
  Issue のスコープにない公開契約を増やさないため（必要になれば別 Issue）。
- review-poll と issue-close の判定は変えない（review-poll は `has_unresolved_findings` を使っていない。
  #368 で「Codex review thread の Resolve は要求しない」と決定済み）。
- starter repo（`kaji-starter-python` / `kaji-starter-typescript`）はこの PR で編集しない（D2）。

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/commands/pr_review_thread.py`（新規） | GraphQL 応答の Pydantic モデル、スレッド特定とページ送り、resolve、結果型 |
| `kaji_harness/commands/pr.py` | `_PR_BUILTIN_SUBCOMMANDS` に `resolve-thread` を追加し、`_dispatch_pr_builtin` で argparse 定義と新モジュールへの dispatch を足す。`_GH_MISSING_GUIDANCE` に `resolve-thread` を追加 |
| `tests/test_pr_resolve_thread.py`（新規） | Small（モック）テスト |
| `tests/test_pr_resolve_thread_large_local.py`（新規） | stub `gh` を PATH に置いた CLI E2E |
| `tests/test_skill_pr_verify_resolve_thread.py`（新規） | pr-verify SKILL.md の静的検証（Medium） |
| `.claude/skills/pr-verify/SKILL.md` | スレッド解決手順を追加（下記「方針」） |
| `docs/cli-guides/github-mode.md` / `github-mode.ja.md` | § 2 に `resolve-thread` を追記 |
| `CHANGELOG.md` | `[Unreleased]` の `Added` に追記 |

## 方針

### データフロー

```
_handle_pr(["resolve-thread", pr, "--comment", cid])
  → 既存: config 読込 / provider 検証 / repo_override 取得
  → _dispatch_pr_builtin("resolve-thread", ...)
      → argparse（pr_id, --comment 必須）
      → ASCII 10 進検証（pr_id, comment_id）           … 失敗 rc=2
      → which gh / _detect_repo(override)                … 失敗 rc=3
      → find_review_thread(repo, pr, cid)
          loop thread pages (after=None → endCursor):
            gh api graphql reviewThreads(first:100, after) { id isResolved viewerCanResolve
                                                             comments(first:100){ pageInfo nodes{fullDatabaseId} } }
            for thread in page:
              cid が comments の 1 ページ目にあれば → thread を返す
              comments.hasNextPage なら node(id: thread.id) { ... on PullRequestReviewThread
                                         { comments(first:100, after) } } を辿って照合
          全件で該当なし → NotFound                       … rc=2
      → thread.isResolved なら already_resolved          … rc=0（mutation なし）
      → not viewerCanResolve なら PermissionDenied       … rc=3
      → gh api graphql resolveReviewThread(input:{threadId}) { thread { id isResolved } }
          isResolved が true でない                      … rc=3
      → stdout に JSON 1 行                              … rc=0
```

### 新規モジュールの責務（名前と責務のみ）

- `pr_review_thread.py`
  - Pydantic モデル（`extra="ignore"`, `frozen=True`）: `ReviewThreadsPage`（`reviewThreads` の 1 ページ）、
    `ThreadCommentsPage`（`node.comments` の 1 ページ）、`ResolveReviewThreadPayload`（mutation 応答）。
    クエリで要求したフィールドだけを必須として宣言する。
  - `find_review_thread(repo, pr_id, comment_id) -> ReviewThreadRef`: ページを辿ってスレッドを特定する。
    該当なしは専用例外（例 `ReviewThreadNotFound`）。
  - `resolve_review_thread_for_comment(repo, pr_id, comment_id) -> ResolveOutcome`: 特定 → 冪等判定 →
    権限判定 → mutation → 結果検証。権限不足・mutation 不成立も専用例外で表す。
  - `gh api graphql` の呼出しを 1 か所（例 `_run_graphql(query, variables, phase)`）に寄せ、
    `capture_output=True` で JSON を受け取り、非 0 終了は gh の stderr を保持した例外にする。
  - 例外 → 終了コード・stderr への変換は CLI handler（`pr.py` 側の dispatch 先関数）だけが行う。
    ドメイン関数は print しない。
- 例外の基底は `docs/reference/python/error-handling.md` に従い既存の例外階層との関係を実装時に決める
  （CLI 内で完結し外へ出ないため、モジュール内の例外クラスでよい）。

### pr-verify SKILL.md の変更（D4・D5・到達したい状態）

1. **Step 1-3**: `kaji pr review-comments` の `--jq` に `id` と `in_reply_to_id` を加える
   （どの指摘がどのスレッドかを対応付けるため）。
2. **新 Step 3.5「確認済みスレッドの解決」** を Step 3（品質チェック）と Step 4（確認結果の投稿）の間に置く。
   - 対象: インラインレビューコメントに対応する指摘のうち、Step 2.1 で「✅ OK（解消）」と判定したもの、
     Step 2.2 で「受け入れる」と判定した反論。
   - 対象外（開いたまま）: 「❌ 要再修正」「再反論する」「一部受け入れ」、およびインラインでない指摘
     （PR 会話欄・review 本文。スレッドが存在しない）。
   - レビュアー（Codex bot / `/review` / 人間）で区別しない（D5）。
   - 判定全体が Approve でも Changes Requested でも、対象のスレッドは解決する（判定は指摘ごと）。
   - 実行: 各対象の根コメント ID で `kaji pr resolve-thread [pr_id] --comment [comment_id]`。
   - 失敗時（rc≠0）: verdict は変えない（D4）。comment ID と stderr の要旨を控え、Step 4 と Step 5 に転記する。
     再試行やループはしない。
3. **Step 4**: 確認結果コメント（Approve / Changes Requested の両テンプレート）に
   「### レビュースレッドの解決」表（comment ID / 判定 / 結果: `resolved` / `already_resolved` /
   `失敗: <理由>` / `未解決のまま: <理由>`）を追加する。
4. **Step 5**: 完了報告に解決できなかったスレッドの comment ID と理由を書く欄を追加する。
5. **status の選択基準**: 「スレッド解決の成否は status に影響しない」と明記する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 対象 forge | GitHub のみ。GitLab 分岐・未対応エラーは実装しない | Issue 本文 決定事項 D1（grill-me Q1、人間決定）。根拠 `docs/adr/004-remove-gitlab-forge.md` | 「制約・前提条件」に明記。provider 分岐を新設しない |
| starter への反映 | kaji 本体の `.claude/skills/pr-verify/SKILL.md` のみ変更。starter は Release 後の starter-sync | Issue 本文 決定事項 D2（grill-me Q2、人間決定）。根拠 `docs/operations/release/starter-sync-runbook.md` | 変更スコープ表から starter repo を除外 |
| unresolve | 追加しない | Issue 本文 決定事項 D3（grill-me Q3、人間決定） | スコープ外に記載 |
| 解決失敗時の pr-verify verdict | verdict は変えず、comment ID と理由を確認結果コメントと完了報告に残す | Issue 本文 決定事項 D4（grill-me Q4、人間決定） | Step 3.5 で失敗を控え、Step 4 の表と Step 5 に転記。再試行しない。status 基準に「影響しない」と明記 |
| 解決する対象 | レビュアーで区別せず、「解消」または「反論 OK」と判定したスレッドだけ解決 | Issue 本文 決定事項 D5（grill-me Q5、人間決定）と「到達したい状態」 | 「一部受け入れ」「要再修正」「再反論」、非インライン指摘は開いたままと具体化 |
| コマンド名と引数 | `kaji pr resolve-thread <pr_id> --comment <comment_id>` | Issue 本文「設計に委ねること」（候補名の委任）、完了条件 1 | Issue の候補をそのまま採用。ID は既存 builtin と同じ ASCII 10 進検証。`--repo` は受けない（既存 builtin と同じ） |
| 終了コードと stderr | 0 = 成功（解決済みを含む）、2 = 入力不正（形式不正・コメントが PR のスレッドに無い）、3 = 実行時エラー（gh 不在・API 失敗・応答不正・権限不足・mutation 不成立） | Issue 本文「設計に委ねること」（`exit_codes.py` の規約 2/3 に沿って設計で決める）、完了条件 3 | 状況ごとの rc と stderr 文面を「出力」表で固定。「見つからない」を 2 にした理由（同じ入力で再試行しても変わらない）を記載 |
| stdout | 成功時のみ JSON 1 行（`comment_id` / `thread_id` / `result`） | AI の仮定。根拠: skill から機械判定しやすく、冪等成功（`already_resolved`）と新規解決を区別できる。公開 CLI 契約のため issue-review-design で検査 | フィールドと値域を定義 |
| どのコメント ID からでもスレッドを引く | スレッド内の全コメントの `fullDatabaseId` と照合する（根でも返信でも可） | Issue 本文「AI の仮定」1（検査先 issue-review-design / テスト） | REST `in_reply_to_id` 経由の案を代替案として不採用理由付きで記録。テスト観点に返信 ID のケースを含める |
| ページ送り | `reviewThreads` と各スレッドの `comments` の両方を全ページ辿る | Issue 本文「AI の仮定」2（検査先 issue-review-design / テスト） | スレッド側は `reviewThreads(after:)`、コメント側は `node(id:){... on PullRequestReviewThread{comments(after:)}}` で辿る。該当が見つかった時点で打ち切る |
| ID の照合フィールド | `fullDatabaseId`（文字列）で照合し、`databaseId` は使わない | AI の仮定（設計で追加）。根拠: GitHub GraphQL スキーマで `databaseId` は deprecated（introspection 2026-11-01）、kamo2#2047 で REST `id` と一致を確認。検査先 issue-review-design / テスト | 比較は文字列同士（入力の ASCII 10 進文字列と一致比較） |
| `repo_override` | 既存 builtin と同じく `_detect_repo(override=repo_override)` を使う | Issue 本文「AI の仮定」3（検査先 issue-review-code）。既存 `_dispatch_pr_builtin` のパターン | owner / name は `repo` を `/` で分割して GraphQL 変数に渡す |
| review-poll / issue-close | 判定を変えない | Issue 本文「AI の仮定」4 とスコープ外。#368 の決定 | 変更スコープに含めない |
| GraphQL 応答の検証 | Pydantic v2 で必要フィールドを検証し、不一致は rc=3 | AGENTS.md「外部入力は Pydantic で検証する」（既存規約） | `extra="ignore"`（GraphQL は要求フィールドのみ返すが、将来の追加に対して壊れないため）。AI の仮定として issue-review-code で検査 |
| `resolutionReason` | 渡さない | AI の仮定。根拠: Issue のスコープ外で、公開契約を増やさない。検査先 issue-review-design | 制約に記載 |

one-way door の未決: なし。公開 CLI の名前・引数・終了コード・stderr は Issue 本文で設計に委任済み（分類 1）。
stdout 形式は委任範囲に明記がないが、追加的な出力で既存契約を壊さず、review-design で修正できるため
two-way door（分類 2）として記録した。

## テスト戦略

### 変更タイプ

- 実行時コード変更（新しい CLI builtin）＋ skill 手順の変更

### Small テスト（`tests/test_pr_resolve_thread.py`、`subprocess.run` / `shutil.which` / `_detect_repo` をモック）

worktree 解決に届かない gh 転送境界の検証であり、`testing-convention.md` § patch スコープで許可される層。
各テストは `rc` / 呼出し回数 / argv を必ず assert する。

- **入力検証**: `pr_id` と `--comment` の非数字・全角数字を rc=2 で拒否し、`gh` を呼ばない。`--comment` 欠落は argparse rc=2。
- **GraphQL 呼出しの組み立て**: argv が `gh api graphql` で、`owner` / `name` が `repo_override` 由来、`number` が
  `-F`（Int）で渡ること。クエリ文字列にコメント ID が埋め込まれていないこと。
- **正常系（未解決 → 解決）**: 1 回の lookup と 1 回の mutation、stdout の JSON が `result=resolved`、rc=0。
- **冪等**: `isResolved=true` なら mutation を呼ばず `result=already_resolved`、rc=0
  （`viewerCanResolve=false` でも解決済みなら成功することを含む）。
- **返信 ID**: スレッドの 2 番目以降のコメント ID でも同じスレッドを特定する。
- **ページ送り（スレッド）**: 1 ページ目に無く 2 ページ目のスレッドにある場合、`after` に `endCursor` を渡して見つける。
- **ページ送り（コメント）**: スレッドのコメントが 100 件超で 2 ページ目にある場合、`node(id:)` クエリで見つける。
- **見つからない**: 全ページを辿って該当なしなら rc=2、stderr にコメント ID・PR 番号・repo、mutation は呼ばない。
- **権限不足**: 未解決かつ `viewerCanResolve=false` で rc=3、mutation を呼ばない。
- **gh 失敗**: lookup / mutation の gh 非 0 終了で rc=3、gh の stderr を中継し `phase` を含む。
- **応答不正**: 非 JSON、`pullRequest: null`、必須フィールド欠落で rc=3（未処理例外を出さない）。
- **mutation 不成立**: mutation 応答の `isResolved=false` で rc=3。
- **gh 不在 / repo 解決失敗**: rc=3（既存 builtin と同じ）。

### Medium テスト（`tests/test_skill_pr_verify_resolve_thread.py`、ファイル読込の静的検証）

既存 `tests/test_skill_provider_guard.py` と同じく SKILL.md を読み、手順の欠落を検出する。

- pr-verify SKILL.md に `kaji pr resolve-thread [pr_id] --comment` の呼出しがある。
- 解決失敗で verdict を変えない旨（D4）と、確認結果コメントにスレッド解決の結果を書く欄がある。
- `review-comments` の取得で `id` を含める。

文面の言い回しに過度に依存しない最小限の assert に留める（キー文字列の存在確認）。

### Large テスト（`tests/test_pr_resolve_thread_large_local.py`、`large` + `large_local`）

stub `gh`（Python スクリプト）を PATH の先頭に置き、`.kaji/config.toml` を持つ一時 project で
実際の `kaji` CLI エントリポイントをサブプロセス起動する（`tests/test_recovery_incident_large_local.py` と同じ方式）。

- config の `[provider.github] repo` が GraphQL 変数 `owner` / `name` に届く（config 発見 → dispatch → gh 境界の結合）。
- 正常系の stdout JSON と rc=0、stub に記録された argv が lookup → mutation の順。
- 解決済みスレッドで mutation が呼ばれない（argv ログに mutation が無い）。
- 見つからない場合の rc=2 と stderr。
- `kaji pr resolve-thread --help` が usage を表示して rc=0。

**実 GitHub API（`large_forge`）は追加しない**: `resolveReviewThread` は実 PR のスレッド状態を書き換える破壊的
mutation で、kaji には書き込みに使える隔離 repo / sandbox がない（`testing-convention.md`「物理的に作成不可」）。
既存 `tests/test_recovery_incident_large_local.py` も同じ理由で `large_forge` を置いていない。スキーマとの整合は、
設計時に read-only の introspection で `PullRequestReviewThread` の `id` / `isResolved` / `viewerCanResolve` /
`comments`、`PullRequestReviewComment.fullDatabaseId`、`ResolveReviewThreadInput.threadId`、
`ResolveReviewThreadPayload.thread` を確認し、apokamo/kamo2#2047 の照会で応答形と ID の一致を確認した。
stub はこの実応答の形をそのまま使う。

### 長時間 acceptance の配置

該当なし。nested workflow・外部 agent・fresh 環境での反復 acceptance はなく、追加するテストはすべて
モックか stub `gh` で完結し秒単位で終わる。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（`gh api graphql` は既存の `gh` CLI の機能） |
| docs/ARCHITECTURE.md | なし | モジュール構成・フローの変更なし（`kaji pr` builtin の追加のみ） |
| docs/dev/ | なし | 開発ワークフローの step 構成は変わらない。pr-verify の手順変更は SKILL.md が正本 |
| docs/reference/ | なし | 設定キーの追加なし |
| docs/cli-guides/github-mode.md / github-mode.ja.md | あり | § 2 の builtin 列挙に `resolve-thread` を追加し、引数・冪等性・終了コード・stdout を追記 |
| CHANGELOG.md | あり | `[Unreleased]` `### Added` に追記（完了条件） |
| .claude/skills/pr-verify/SKILL.md | あり | スレッド解決手順（完了条件、D2・D4・D5） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| README.md / README.ja.md | なし | `kaji pr` の builtin を列挙していない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| GitHub GraphQL: `resolveReviewThread` mutation | https://docs.github.com/en/graphql/reference/mutations#resolvereviewthread | 入力 `threadId: ID!`（任意 `clientMutationId`）、戻り値 `thread: PullRequestReviewThread`。スレッド解決の唯一の API |
| GitHub GraphQL: `PullRequestReviewThread` | https://docs.github.com/en/graphql/reference/objects#pullrequestreviewthread | `id` / `isResolved` / `viewerCanResolve` / `comments(first, after)` を持つ。冪等判定と権限判定に使う |
| GitHub GraphQL: `PullRequestReviewComment` | https://docs.github.com/en/graphql/reference/objects#pullrequestreviewcomment | `fullDatabaseId: BigInt` を照合に使う。`databaseId` は deprecated |
| GitHub GraphQL: `PullRequest.reviewThreads` | https://docs.github.com/en/graphql/reference/objects#pullrequest | `reviewThreads(first, after, ...)` の Connection。`pageInfo { hasNextPage endCursor }` で辿る |
| GitHub GraphQL: ページ送り | https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api | `first` は最大 100。`after: endCursor` で次ページ |
| GitHub GraphQL: リソース制限 | https://docs.github.com/en/graphql/overview/rate-limits-and-node-limits-for-the-graphql-api | 1 クエリ 500,000 ノード上限。本設計の最大 100 × 100 = 10,000 は範囲内 |
| GitHub GraphQL: グローバルノード ID | https://docs.github.com/en/graphql/guides/using-global-node-ids | `node(id:)` でスレッドを直接引き、コメントの続きのページを取る |
| GitHub REST: Pull request review comments | https://docs.github.com/en/rest/pulls/comments | `kaji pr review-comments` / `reply-to-comment` が扱う `id` が入力の comment ID |
| gh CLI: `gh api` | https://cli.github.com/manual/gh_api | `graphql` endpoint、`-f`（string）/ `-F`（型変換して Int 等）で変数を渡す |
| スキーマ introspection（2026-11-01 実施、read-only） | `gh api graphql` による `__type(name: ...)` 照会（本設計作業中に実行） | `PullRequestReviewComment.databaseId` は `isDeprecated: true`、理由「does not support 64-bit signed integer identifiers. Use fullDatabaseId instead」。`ResolveReviewThreadInput` は `threadId` / `clientMutationId` / `resolutionReason`、`ResolveReviewThreadPayload` は `thread` / `clientMutationId` |
| 実例 PR の照会（2026-11-01、read-only） | apokamo/kamo2#2047（`reviewThreads` と REST `pulls/2047/comments`） | 3 スレッドとも `isResolved=false` / `viewerCanResolve=true`。REST `id` と `fullDatabaseId` が一致（例 4432690867）。返信の `in_reply_to_id` は根コメント ID |
| 既存 builtin 実装 | `kaji_harness/commands/pr.py`（`_PR_BUILTIN_SUBCOMMANDS`, `_dispatch_pr_builtin`, `_forward_pr_reply_to_comment`, `_detect_repo`, `_is_ascii_decimal`） | ID 検証・repo_override・gh 不在時の扱いを踏襲する |
| 終了コード定数 | `kaji_harness/commands/exit_codes.py` | `EXIT_INVALID_INPUT = 2`, `EXIT_RUNTIME_ERROR = 3` |
| GitLab 撤去 | `docs/adr/004-remove-gitlab-forge.md` | GitLab forge は完全撤去、GitHub 単独前提（D1 の根拠） |
| starter 追随手順 | `docs/operations/release/starter-sync-runbook.md` | starter の変更は Release 後の starter-sync で行う（D2 の根拠） |
| 対象 skill | `.claude/skills/pr-verify/SKILL.md`, `.claude/skills/pr-fix/SKILL.md` | pr-fix は inline comment の `id` を控え `reply-to-comment --to` で返信する。pr-verify Step 2.1 / 2.2 の判定が解決対象の条件になる |
| Pydantic 規約 | `docs/reference/python/type-hints.md` § Pydantic と dataclass の使い分け | 外部入力は Pydantic v2 で検証。パース済みオブジェクトは `model_validate()` |
| テスト patch 規約・large_local 方式 | `docs/dev/testing-convention.md` § patch スコープ、`tests/test_recovery_incident_large_local.py` | gh 転送境界の mock は許可。stub gh を PATH に置く large_local の既存方式。破壊的副作用で隔離 repo が無い場合 `large_forge` を置かない前例 |
| 関連 Issue | #368 | 「Codex review thread の Resolve は要求しない」。close 条件は変えない |
