# [設計] review-poll: security_review の completed / has_any_findings=null を条件付きで受け入れる

Issue: #557

## 概要

`review-poll` は、Codex bot の summary comment の `codex-review-state:v1` で `security_review` が
`status: "completed"`・`has_any_findings: null` のとき、parse の段階で検証エラーにしている。このため
2026-11-01 以降のすべての PR で即時 `BACK_FALLBACK` になる。保守者が決めた条件をすべて満たす場合に限ってこの状態を
「security の指摘なし」として受け入れ、state による PASS（証跡 schema v3）と、`issue-close` の照合付き merge を通す。

## 背景・目的

### Observed Behavior（OB）

- series `535-skill-hardening` の 5 run（PR #550 / #552 / #553 / #555 / #556）の review-poll verdict はすべて次のとおり
  （Issue 本文の OB 表）:

  ```text
  status: BACK_FALLBACK
  evidence: codex-review-state is present but unusable: codex-review-state in comment 6724593064 failed validation: 1 error(s)
  ```

- 設計時に REST で取得した実 payload（PR #552 comment 6724593064、2026-11-12 取得）:

  ```text
  code_review:     {"commit_sha":"eb159264fee25b39e68b5009139a65ec2b5e8766","has_any_findings":false,"status":"completed"}
  security_review: {"commit_sha":"eb159264fee25b39e68b5009139a65ec2b5e8766","has_any_findings":null,"status":"completed"}
  summary updated_at=2026-11-08T17:24:07Z / bot +1 (id 636370349) created_at=2026-11-08T17:24:09Z
  bot review（REST pulls/552/reviews）0 件 / reviewThreads（GraphQL）nodes=[] / summary 以外の Issue comment は人間の 1 件のみ
  ```

  PR #550（comment 6723531598）も同じ形（head `8971eda…`、summary 15:47:52Z、+1 15:47:54Z）。
- PR #559（comment 6818198704）は `code_review.has_any_findings: true`・`security_review` が head の `completed/null`。
  現行 parser は security の null で先に失敗するので、本来 `RETRY` になる経路も `BACK_FALLBACK` になる
  （Issue 追加調査コメント 2026-11-12）。
- 影響: PR ごとに fallback の `review` skill が走る（1 回あたり約 4 分）。`issue-close` の `MODE=verify`
  （`--match-head-commit` 付き merge）が実 run で一度も通らない。

### Expected Behavior（EB）

Issue 本文 EB と「重要判断」（保守者決定 2026-11-09 / 2026-11-12）を正本とする。

- head 向けの `security_review` が `completed`・`has_any_findings: null` で、既存の PASS 条件（code_review が head で完了・
  指摘なし、summary の `updated_at` より後の秒かつ head commit 以降の bot 👍、head 上に bot review なし）に加えて
  (a) head に未解決の bot 指摘スレッドがない、(b) trusted bot による summary 以外の Issue comment が 0 件、
  をすべて満たすときだけ PASS にし、`review-poll-evidence.json` を保存する。
- 条件を 1 つでも満たさない null は PASS にしない（fail-closed）。
- `has_any_findings: true` は従来どおり RETRY。code_review の判定は変えない。
- `issue-close` の I6 は上の条件で保存された証跡を受け入れ、それ以外の null を拒否する。ほかの不変条件は変えない。

### 再現手順（steps-to-reproduce）

1. worktree（基点 `6a53e29`）の `.venv` を有効にする。
2. `gh api repos/apokamo/kaji/issues/comments/6724593064` の JSON（`id` / `html_url` / `updated_at` / `user` / `body`）を
   `kaji_harness.scripts.codex_review_poll._parse_state_body` に渡す。
3. `InvalidReviewState("codex-review-state in comment 6724593064 failed validation: 1 error(s)")` が返る。同じ comment を
   `classify(reactions=[bot +1 17:24:09Z], reviews=[], head_sha="eb15926…", head_committed_at=<head の committedDate>, comments=[comment])`
   に渡すと `PollResult(state="done_fallback", signal="state")` になる（readiness review で HEAD `4b073fb` 上でも再現済み）。

## 根本原因（Root Cause）

### なぜ壊れるか

上流 bot の出力が 2026-11-01 ごろから security_review について `completed` かつ `has_any_findings: null` を返すように
変わった（Issue 本文の期間表: 〜10-24 は 18/18 件が `false`、11-01 以降は 8/8 件が `null`）。null の意味は公式に定義されて
いない（[Security Review](https://learn.chatgpt.com/docs/security/security-review)）。kaji 側は次の 4 か所が
「`completed` なら `has_any_findings` は bool」を前提にしている。

1. **parse**: `ReviewStateEntry._validate_completed`（`kaji_harness/scripts/codex_review_poll.py:86-92`）が code / security に
   同じ規則を適用し、security の `completed/null` を `ValidationError` → `InvalidReviewState` → `classify` の先頭で即時
   `done_fallback` にする。RETRY 判定（`_classify_state`）にも到達しない（#559 の code `true` も巻き込む）。
2. **判定**: parse を緩めても `_classify_state`（同 `:336`）は head 向け security が `has_any_findings is False` でないと
   PASS 候補にしない。
3. **producer 証跡**: `ReviewPollEvidenceV2._validate_invariants`（`kaji_harness/review_poll_evidence.py:276-288`）が head の
   security に `has_any_findings is False` を要求する。
4. **consumer 証跡**: `issue-close` の jq I6（`.claude/skills/issue-close/SKILL.md:429-433`）が同じ条件を要求する。契約の正本
   `docs/ARCHITECTURE.md` の I6 行も同じ。

### いつから

4 か所とも #473（commit `4db458d` "use codex-review-state:v1 as the review-poll signal"）で導入された。導入時点の bot 出力は
`completed` なら bool だったので正しく動いており、上流の出力変化（11-01〜）で顕在化した。

### 同根の他の箇所

`has_any_findings` / `codex-review-state` を参照するのは `codex_review_poll.py`・`review_poll_evidence.py`・
`issue-close/SKILL.md`（jq）・`review-poll/SKILL.md`・`docs/ARCHITECTURE.md`・`docs/dev/workflow_guide.md`・`CHANGELOG.md`
（履歴）とそのテストだけ（`grep -rln "has_any_findings\|codex-review-state"` で確認）。`review` / `pr-verify` などほかの skill は
state を読まない。したがって修正対象は上記 1〜4 と docs の同期で尽きる。

## インターフェース

公開 CLI（`kaji pr review-poll`）の引数・終了コード・verdict の status 集合は変えない。変わるのは判定規則と証跡の版。

### 入力

- 既存: PR の REST `issues/{n}/reactions` / `pulls/{n}/reviews` / `issues/{n}/comments`（全ページ）、`pulls/{n}`。
- 追加（null 経路の確定時だけ）: GraphQL `repository.pullRequest(number).reviewThreads`（全ページ）と、確定直前の
  REST `issues/{n}/comments` の再取得。

### 出力

| 観測（state 経路） | 変更前 | 変更後 |
|---|---|---|
| code_review が head で `completed/true`（security が null でも） | 即時 BACK_FALLBACK（parse 失敗） | `RETRY` |
| security が head で `completed/true`（code は null でない） | `RETRY` | `RETRY`（不変） |
| security が `completed/null` で `commit_sha` が null（#519 型） | 即時 BACK_FALLBACK | 即時 BACK_FALLBACK（不変） |
| code_review が `completed/null` | 即時 BACK_FALLBACK | 即時 BACK_FALLBACK（不変） |
| security が `completed/null` で `commit_sha` が head 以外 | 即時 BACK_FALLBACK | 即時 BACK_FALLBACK（不変） |
| security が head で `completed/null`、trusted bot の summary 以外の Issue comment が 1 件以上 | 即時 BACK_FALLBACK | 即時 BACK_FALLBACK（👍 を待たない） |
| security が head で `completed/null`、code は head で `completed/false`、bot review が head にない、fresh な 👍 がある | 即時 BACK_FALLBACK | 確定確認へ。確定時に (a)(b) を再取得し、未解決の head bot thread または bot の summary 以外の comment があれば即時 `BACK_FALLBACK`、なければ `PASS`（証跡 v3） |
| 同上で 👍 がない / stale / 同じ秒、または head に bot review がある | 即時 BACK_FALLBACK | terminal にしない（false 経路と同じ。既存 timeout で終わる） |
| security が head で `completed/false`（従来の経路） | PASS 候補 | PASS 候補（不変。(a)(b) は課さない。証跡は v3 で `security_null_checks: null`） |

証跡 `review-poll-evidence.json`:

- producer は常に **schema v3** を書く。v3 は v2 と同じ構造に `checks.security_null_checks` を 1 キー足したもの:

  ```json
  "checks": {
    "head_sha_at_start": "<40hex>", "head_sha_before_decision": "<40hex>", "head_sha_after_decision": "<40hex>",
    "current_head_bot_reviews": 0,
    "security_null_checks": null | {"unresolved_head_bot_threads": 0, "bot_non_summary_issue_comments": 0}
  }
  ```

- `security_null_checks` は security_review が「head の `commit_sha`・`completed`・`has_any_findings: null`」のときだけ
  オブジェクト（2 つの件数がともに 0）で、それ以外は null。
- consumer（`issue-close` jq / `load_evidence`）は v2（既存の I1〜I9 のまま。null は拒否）と v3 を受け付け、それ以外の版は I1 違反。

### 使用例

```python
# PR #552 の実 summary（security が head の completed/null）+ 2 秒後の bot +1
result = classify(reactions, [], head_sha=HEAD_552, head_committed_at=AT, comments=[summary_552])
assert result.state == "done_pass"                       # 変更前は done_fallback
assert result.approval.review_state.security_review.has_any_findings is None

# PR #559 型: code true + security null → RETRY（変更前は done_fallback）
assert classify([], [bot_review], HEAD_559, AT, comments=[summary_559]).state == "done_retry"

# 確定時（run_polling → _finalize_pass）に GraphQL で未解決の head bot thread が見つかれば PASS にしない
assert run_polling(...).state == "done_fallback"  # 証跡は書かない
```

## 制約・前提条件

- fail-closed を崩さない: 判定できない・取得できない・形が想定外のときは PASS にしない（取得失敗は既存の連続失敗カウンタ → `ABORT`、
  構造的に受け入れられない null は即時 `BACK_FALLBACK`）。
- false 経路（security `completed/false`）・RETRY 経路・state のない summary の挙動は変えない。(a)(b) は null 経路だけに課す。
- 通常の poll の API 呼び出しは増やさない。GraphQL と comments の再取得は null 経路の PASS 候補の確定時だけ行う。
- bot の識別は既存の trust anchor（`BOT_ID = 199175422`、REST `user.id`）に揃える。GraphQL では `author` が `Bot` で
  `databaseId == BOT_ID` のときだけ trusted bot とする（PR #559 の実応答で `databaseId: 199175422` を確認済み）。
- 本修正は文書化されていない bot 挙動（👍 の意味、指摘時に inline review が出ること）に依存する。上流が null の意味を
  定義・修正したら見直す（保守者決定）。
- `issue-close` は I6（と v3 の I1 キー集合）以外の不変条件・照合手順を変えない。close 時の (a)(b) 再取得は行わない（スコープ外）。
- GitHub 限定（`requires_provider: github`）。GraphQL 呼び出しは既存の `gh` 認証で行う。

## 方針

### 1. parse（`codex_review_poll.py`）

- `ReviewStateEntry` は「`completed` なら `commit_sha` 必須」を維持し、`has_any_findings` の必須化を外す。
- `_ReviewStatePayload` 側の validator で「`code_review` が `completed` なら `has_any_findings` は bool」を検査する
  （code の null は従来どおり `InvalidReviewState`）。
- 結果: security の `completed/null` は `commit_sha` があれば parse を通り、`commit_sha` が null（#519 型）は従来どおり失敗する。

### 2. 判定（`_classify_state`）

評価順（RETRY を最優先にして #559 型を回復する）:

```text
1. code が head で completed/true                               -> done_retry（既存）
2. security が head 向けで completed/true                       -> done_retry（既存）
3. security が completed/null で commit_sha != head             -> done_fallback（即時。変更前の verdict を維持）
4. code が head で completed/false でない                        -> None（既存）
5. security が head 向けで未完了                                 -> None（既存）
6. security が head の completed/null（null 経路）:
     comments 中の trusted bot の summary 以外の comment が 1 件以上 -> done_fallback（即時）
7. head に bot review がある                                     -> None（既存）
8. fresh な bot +1 がない                                        -> None（既存）
9. PASS 候補（Approval）
```

- 「summary 以外の comment」: `_is_bot(user, bot_id)` で、`body` が文字列で `lstrip()` 後に `SUMMARY_MARKER` で始まる、
  に当てはまらない comment。`body` が文字列でないものも数える（fail-closed）。trusted bot 以外の comment は数えない。
  古い summary（`updated_at` が最新でない summary）は summary として扱う。
- 手順 6 を 👍 より前に置くのは、エラーコメントがあるのに 👍 が来ない run を timeout まで待たせず、変更前と同じ即時
  `BACK_FALLBACK` にするため。

### 3. 確定（`_finalize_pass`）

null 経路の PASS 候補では、判定直前の `pulls/{n}` 確認（既存）と判定直後の確認（既存）の **間** で次を取得する。
head 確認で挟むので、観測が固定 head に対するものであることを保証する。

1. REST `issues/{n}/comments`（全ページ）を再取得し、手順 2 と同じ定義で bot の summary 以外の comment を数える。
2. GraphQL `reviewThreads` を全ページ取得し、未解決の head bot thread を数える（下記）。
3. どちらかが 1 以上なら `done_fallback`（証跡を書かない）。理由に件数を含める。
4. 0 なら `security_null_checks = {"unresolved_head_bot_threads": 0, "bot_non_summary_issue_comments": 0}` を証跡に入れる。

false 経路では 1〜2 を行わず `security_null_checks: null`。取得失敗・形状違反は例外のまま呼び出し側の連続失敗カウンタへ
伝播させる（既存の `pulls/{n}` 再取得と同じ扱い。3 回連続で `ABORT`）。

#### 未解決の head bot thread の判定（重要判断 (a) の具体化）

- 取得: `gh api graphql` を Python 側で cursor ループする（`reviewThreads(first: 100, after: $cursor)`、
  各 thread の `isResolved`、`comments(first: 100) { pageInfo { hasNextPage } nodes { author { __typename ... on Bot { databaseId } } commit { oid } originalCommit { oid } } }`）。
  `gh --paginate` は使わない（nested な `pageInfo` を持つ query で cursor の取り違えを避けるため）。
- 応答に `errors` がある、`data.repository.pullRequest.reviewThreads` が無い、`nodes` が list でない、`isResolved` が bool でない、
  `hasNextPage` が true なのに `endCursor` が無い・前ページと同じ、などは `ValueError`（取得失敗扱い）。
- 数える thread: `isResolved == false` で、次のいずれか。
  - trusted bot（`__typename == "Bot"` かつ `databaseId == BOT_ID`）の comment のうち、`commit.oid` と `originalCommit.oid` が
    **ともに非 null かつ head 以外** と言えないものが 1 つでもある（= head に付いている、または付いていないと証明できない）。
  - `comments.pageInfo.hasNextPage == true`（thread 内の comment を全件見られないので fail-closed で数える）。
- `isOutdated` 単独では除外しない。GitHub は outdated でない comment の `commit` を現在の head に付け替える
  （PR #559 の実応答: `originalCommit=e144b2b…`・`commit=a52f66e…`（その時点の head）・`isOutdated=false`）。
  古い commit で付いた指摘でも head にまだ当たっていれば数える。outdated で `commit`・`originalCommit` がともに古い commit の
  thread は「head の指摘」ではないので数えない。
- resolved な thread と、trusted bot の comment を含まない thread は数えない。

### 4. 証跡（`review_poll_evidence.py`）

- `EVIDENCE_SCHEMA_VERSION_V3 = 3`、`SecurityNullChecks`（`unresolved_head_bot_threads: int` / `bot_non_summary_issue_comments: int`、
  strict・extra forbid）、`ChecksV3`（v2 の 4 キー + `security_null_checks: SecurityNullChecks | None`）、`ReviewPollEvidenceV3` を追加する。
- v3 の security 不変条件（I6 v3）: 次のいずれか。
  - `status == null` かつ `security_null_checks == null`
  - `commit_sha` が head 以外の 40 桁 SHA で、「`status == "completed"` かつ `has_any_findings == null`」ではなく、`security_null_checks == null`
  - head の `commit_sha`・`completed`・`has_any_findings == false` かつ `security_null_checks == null`
  - head の `commit_sha`・`completed`・`has_any_findings == null` かつ `security_null_checks` の 2 件数がともに 0
- code の条件・I2〜I5・I7〜I9 は v2 と同じ（`_check_common_invariants` を共用）。
- `ReviewPollEvidenceV2` と v2 の不変条件は変えない（v2 は null を拒否したまま）。upgrade 前に作られた attempt の証跡を
  close できるよう、consumer は v2 を受け付け続ける。
- `load_evidence()` は `schema_version` で V2 / V3 を選んで検証する（それ以外は `ValidationError`）。`save_evidence()` は V3 を受け取る。
  producer は V3 だけを書く。

### 5. consumer（`issue-close/SKILL.md` 2.5.3 の jq）

- `if $v == 2 then [既存のまま] elif $v == 3 then [v3] else ["I1"] end`。
- v3 の I1: top-level キー集合は v2 と同じ、`checks` のキー集合に `security_null_checks` を追加、値は null か
  キー集合 `["unresolved_head_bot_threads","bot_non_summary_issue_comments"]` のオブジェクトで各値が整数。
- v3 の I6: 上記「I6 v3」を jq で表現する。I7・I8・common_checks は v2 と共用。
- 本文の「受け付ける版は `schema_version: 2` だけ」を「2 と 3」に改める。

### 6. docs

`review-poll/SKILL.md` の判定表（69・71〜73 行目）と箇条書き・証跡の説明、`docs/ARCHITECTURE.md` の承認証跡節（v3 と I1 / I6）、
`docs/dev/workflow_guide.md` の review-poll 前提の段落、各モジュール docstring を同期する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| security の `completed/null` の扱い | 条件付きで受け入れ、条件外は PASS にしない（fail-closed） | Issue 本文「重要判断」1 行目（保守者決定 2026-11-09） | parse は security だけ null を許し、code の null・`commit_sha` null は従来どおり拒否。判定順を RETRY 優先に並べる |
| 受け入れ条件の構成 | 既存 PASS 条件 + (a) 未解決 head bot thread なし + (b) bot エラーコメントなし | 同上 | 既存条件は false 経路と同一実装を共有。(a)(b) は null 経路だけに課す |
| (a) の判定方法 | GraphQL `reviewThreads` 全ページ。`isResolved=false` で、trusted bot の comment が head に付いている（`commit`/`originalCommit` が head、または head でないと証明できない）thread、または comment を全件見られない thread を数える | Issue 本文「重要判断」1 行目の未決事項「(a) の具体的な判定方法は設計で決める」で設計に委任。AI の仮定: 「head に」を `commit`/`originalCommit` の一致で表す（PR #559 の実応答で付け替えを確認）。検査先: review-design、Small テスト | `isOutdated` 単独で除外しない。bot は `Bot.databaseId == BOT_ID` で識別。thread 内ページ超過は fail-closed |
| (b) の判定方法 | REST `issues/{n}/comments` 全ページで trusted bot の summary 以外の comment が 1 件でもあれば PASS にしない。文面では判定しない | Issue 本文「重要判断」3 行目（保守者決定 2026-11-12） | summary の定義を既存 `SUMMARY_MARKER` 判定に揃え、`body` が文字列でないものも数える。poll 時と確定時の 2 回評価する |
| 条件外 null の verdict | 構造的に受け入れられない null（`commit_sha` null・head 以外・bot の summary 以外の comment・未解決 head bot thread）は即時 `BACK_FALLBACK`。時間で満たされうる条件（👍・bot review）は false 経路と同じく待つ | AI の仮定。根拠: EB「今と同じく PASS にしない」に対し、変更前の観測 verdict（即時 `BACK_FALLBACK`）を保ち、待機後の `ABORT` へ悪化させない。検査先: review-design | `_classify_state` の手順 3・6 と `_finalize_pass` の戻り値に割り当て |
| 証跡の版 | schema v3 を新設し、producer は常に v3、consumer は v2 と v3 を受け付ける | Issue 追加調査コメント（2026-11-12）が v3 / v2 拡張の 2 案を挙げ「設計レビューで決める」とし、実行経路コメントが「fail-closed のまま revert でき、設計レビューで扱える粒度」と分類。AI の仮定（two-way door）。根拠: 証跡は attempt ごとの短命 artifact で移行対象の蓄積データがない。revert しても未消費の v3 証跡は旧 close で I1 違反（停止）になるだけで誤 merge しない。v2 拡張は I1 のキー集合完全一致を同一版内で条件分岐させる。検査先: review-design | `checks.security_null_checks`（null かオブジェクト）でキー集合を固定。v2 の不変条件は不変 |
| close 時の (a)(b) 再取得 | 行わない | Issue 本文 EB「`issue-close` の I6 は…受け入れる。ほかの不変条件は変えない」 | 追加調査コメントの「close 直前の再取得」案はスコープ外として記録のみ |
| 根拠の性質（未文書の bot 挙動への依存） | 上流が null の意味を定義・修正したら見直す | Issue 本文「重要判断」2 行目（保守者決定 2026-11-09） | 本設計書・SKILL.md・ARCHITECTURE.md に依存と見直し条件を明記する |

## テスト戦略

### 変更タイプ

- 実行時コード変更（判定ロジック・証跡 model・consumer jq）。bug 修正なので、修正前に Red になる回帰テストを含める。

### 実行時コード変更の場合

#### Small テスト

- **実 payload 回帰（Red → Green）**: PR #550（comment 6723531598）と PR #552（comment 6724593064）の REST 応答
  （`id` / `html_url` / `updated_at` / `user` / `body` をそのまま）と各 bot +1 を fixture（`tests/fixtures/codex_review_poll/kaji_pr550_*.json` /
  `kaji_pr552_*.json`）に固定する。`_parse_state_body` が `ReviewState` を返し（修正前は `InvalidReviewState`）、`classify` が
  `done_pass` 候補を返す（修正前は `done_fallback`）ことを確認する。
- **#559 型（Red → Green）**: code `completed/true` + security head `completed/null` + head の bot review → `done_retry`。
- **fail-closed**（いずれも PASS しない）: 👍 なし / stale 👍 / summary 更新と同じ秒の 👍 → terminal にしない。head に bot review →
  terminal にしない。trusted bot の summary 以外の Issue comment（エラー文面・受領文面・`body` 非文字列）→ 即時 `done_fallback`
  （👍 がなくても）。security `completed/null` で `commit_sha` null（#519 型）・head 以外 → 即時 `done_fallback`。code `completed/null` → 即時 `done_fallback`。
- **既存挙動の維持**: security head `completed/true` → `done_retry`。false 経路は trusted bot 以外の comment や bot の summary 以外の comment が
  あっても従来どおり PASS 候補（(a)(b) を false 経路に課していないことを固定）。trusted bot 以外のユーザーの comment は null 経路でも数えない。
- **thread 判定の純粋関数**: 未解決 + bot comment の `commit` が head → 1。`originalCommit` だけ head → 1。`commit` / `originalCommit` が null → 1。
  outdated で両方古い commit → 0。resolved → 0。人間だけの thread → 0。`databaseId` が異なる Bot → 0。未解決で `comments.hasNextPage` → 1。
  `isResolved` 非 bool・`nodes` 非 list・`errors` あり → `ValueError`。
- **証跡 model（`test_review_poll_evidence.py`）**: v3 の false 経路（`security_null_checks: null`）と null 経路（件数 0）を受理。
  null 経路で checks が null・件数が 1・キー過不足・件数が bool / 文字列 → 拒否。false 経路で checks オブジェクト → 拒否。
  security `completed/null` で head 以外・`commit_sha` null → 拒否。v2 で security head `completed/null` → 従来どおり拒否。
  `load_evidence` が v2 / v3 を版で振り分け、4 などは拒否する。

#### Medium テスト

- **run_polling（`_FakeGh` に GraphQL と呼び出し記録を追加）**: null 経路の PASS で v3 証跡（`security_null_checks` の件数 0）を書いてから
  `done_pass` を返す。GraphQL と comments 再取得が判定直前・直後の `pulls/{n}` の間で呼ばれ、false 経路と非候補 poll では GraphQL を呼ばない。
  確定時に未解決 head bot thread、または再取得した comments に bot の summary 以外の comment がある → `done_fallback`・証跡なし。
  GraphQL / comments の取得失敗が 1〜2 回なら回復、3 回連続で `ABORT`・証跡なし。reviewThreads の 2 ページ目にある thread を検出する。
  cursor が進まない応答は取得失敗として扱う。
- **`_gh_graphql`（subprocess を fake）**: `gh api graphql` の argv（query と変数）、`errors` を含む応答・object でない応答を `ValueError` にする。
- **producer / consumer 一致（`test_issue_close_evidence_jq.py`）**: v3 の受理・拒否境界（上記 Small の証跡ケース全件）と v2 の既存ケースで、
  `issue-close` SKILL.md の jq と `ReviewPollEvidenceV3` / `V2` の判定が一致する。未知の版のテストは `schema_version: 4` に変える
  （3 は v3 として受理されるため）。v3 の null 証跡を checks なしに改変したものは jq でも I6 違反になる。

#### Large テスト

- **`test_review_poll_large_local.py`（`large_local`、stub `gh`）**: entry module をサブプロセスで実行し、security head `completed/null` の summary と
  空の reviewThreads を返す stub で PASS・v3 証跡、未解決 head bot thread を返す stub で `BACK_FALLBACK`・証跡なしになることを確認する。
  stub `gh` に `api graphql` の応答を追加する。
- **実 GitHub API（`large_forge`）は追加しない**: 判定の入力は bot の出力に依存し、CI で同じ bot 状態を再現できない（`docs/dev/testing-convention.md`
  の「物理的に作成不可」に当たる）。実 payload は Small の固定 fixture で、API 形状は設計時の実取得（本設計書 OB・(a) の節）で確認した。
  実環境での確認は Issue の「ワークフロー完了後の確認項目」（merge 後の最初の PR）で行う。

### 長時間 acceptance の配置

該当なし。追加する検証はすべて pytest（Small / Medium / `large_local`）で数秒〜数十秒に収まり、nested workflow・外部 agent・
fresh 環境での反復を含まない。実 bot での確認は workflow 外の事後確認項目に分離済み（Issue 本文）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（証跡の版上げは既存の schema 運用の範囲） |
| docs/ARCHITECTURE.md | あり | 「review-poll の承認証跡」節: 承認の根拠に null 経路、schema v3、I1（v3 のキー集合）・I6（v3 の security 条件）を追記 |
| docs/dev/ | あり | `workflow_guide.md` の review-poll 前提の段落に null 経路の条件付き PASS を一文追記 |
| docs/reference/ | なし | Python 規約・API 仕様の変更はない |
| docs/cli-guides/ | なし | `kaji pr review-poll` の引数・出力契約は不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更はない |
| .claude/skills/review-poll/SKILL.md | あり | 判定表（69・71〜73 行目）、箇条書き、証跡（v3・`security_null_checks`）の説明 |
| .claude/skills/issue-close/SKILL.md | あり | 2.5.3 の jq（v3 分岐・I1 / I6）と本文の受付版の記述 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #557 本文（OB・EB・重要判断） | https://github.com/apokamo/kaji/issues/557 | 条件付き受け入れの保守者決定（2026-11-09）、(b) の判定方法の保守者決定（2026-11-12）、(a) の判定方法は設計で決めるとの委任 |
| Issue #557 追加調査コメント（2026-11-12） | 同上（コメント「追加調査（2026-11-12）と修正案」） | 原因の連鎖 4 段、#559 の code true も parse で止まること、v2/v3 の選択を設計レビューに委ねたこと |
| PR #552 summary comment（実 payload） | https://github.com/apokamo/kaji/pull/552#issuecomment-6724593064 | security `completed/null`・head `eb15926…`、updated_at 17:24:07Z。本文に "Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings." |
| PR #550 summary comment（実 payload） | https://github.com/apokamo/kaji/pull/550#issuecomment-6723531598 | 同形の `completed/null`（head `8971eda…`）、+1 は 15:47:54Z |
| PR #559 summary と reviewThreads | https://github.com/apokamo/kaji/pull/559#issuecomment-6818198704 | code `completed/true` + security `completed/null`。GraphQL で bot comment の `author.databaseId=199175422`、`originalCommit=e144b2b…`・`commit=a52f66e…`・`isOutdated=false` を確認（2026-11-12 取得） |
| Codex Security Review | https://learn.chatgpt.com/docs/security/security-review | `codex-review-state` の定義はない。"Check the task status and full report before interpreting an empty comment result as a completed review with no findings." → null の意味は公式未定義で、受け入れの根拠は保守者決定 |
| 上流の不具合報告 openai/codex#57326 | https://github.com/openai/codex/issues/57326 | 同じ `completed/null` の報告（open）。OpenAI 側の回答なし |
| GitHub GraphQL: PullRequestReviewThread / PullRequestReviewComment / Bot | https://docs.github.com/en/graphql/reference/objects#pullrequestreviewthread , https://docs.github.com/en/graphql/reference/objects#pullrequestreviewcomment , https://docs.github.com/en/graphql/reference/objects#bot | `isResolved`「Whether this thread has been resolved」、`isOutdated`「Indicates whether this thread was outdated by newer changes」、`commit`「Identifies the commit associated with the comment」、`originalCommit`「Identifies the original commit associated with the comment」（2026-11-12 に schema introspection で同じ記述を確認）、Bot の `databaseId` |
| GitHub REST: Issue comments | https://docs.github.com/en/rest/issues/comments | PR の Issue comment 一覧（`user.id`・`body`・ページング）。(b) の取得元 |
| `gh api` マニュアル | https://cli.github.com/manual/gh_api | GraphQL の `--paginate` は `$endCursor` 変数と `pageInfo{hasNextPage, endCursor}` を前提にする → nested `pageInfo` を持つ本 query は Python 側で cursor を回す |
| 既存実装（変更対象） | `kaji_harness/scripts/codex_review_poll.py:77-104, 301-361, 503-599`、`kaji_harness/review_poll_evidence.py:193-327`、`.claude/skills/issue-close/SKILL.md:348-449`、`.claude/skills/review-poll/SKILL.md:54-136` | 4 か所の null 拒否と、確定確認・証跡保存の流れ |
| 承認証跡の契約 | `docs/ARCHITECTURE.md`「review-poll の承認証跡」節（I1〜I9 の表） | producer / consumer 共通契約。I6 の security 条件を v3 で拡張する |
| 導入 commit | `4db458d`（#473） | 4 か所の検証の導入元 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義、`large_local` / `large_forge` の区分、「物理的に作成不可」の省略理由 |
