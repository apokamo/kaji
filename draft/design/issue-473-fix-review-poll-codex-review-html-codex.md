# [設計] review-poll: codex-review-state:v1 を判定シグナルに採用し、HTML コメント付き review 本文で RETRY を見逃す不具合を直す

Issue: #473

## 概要

`kaji_harness/scripts/codex_review_poll.py` の `classify()` は、bot review 本文が `### 💡 Codex Review` で
**始まる**ことを RETRY の条件にしている。本文の先頭に `<!-- codex-review-receipt:v1 -->` が付くと完了を見逃し、
`IN_PROGRESS_TIMEOUT_SEC`（1800s）で ABORT する。本修正では summary comment の機械可読 state
`<!-- codex-review-state:v1 {...} -->` を RETRY / PASS の主シグナルにし、state がない summary のときだけ
（先頭 HTML コメントを読み飛ばすよう直した）旧経路で判定する。

## 背景・目的

### Observed Behavior (OB)

kamo2 PR #1837（kaji 0.20.1、workflow `dev-thorough`）で実測した（Issue 本文「現象」）。

- 2026-10-16T17:50:56Z に trusted bot（id `199175422`）が head `dc8eb9cb83b0a48ca51218d919288ae14297120f`
  に `COMMENTED` review を投稿した。本文の先頭は
  `'<!-- codex-review-receipt:v1 -->\n\n\n### 💡 Codex Review\n\n…'`（`gh api repos/apokamo/kamo2/pulls/1837/reviews` で再確認）
- 同じ PR の summary comment（id `6221370648`、updated_at `2026-10-16T17:51:00Z`）には
  `<!-- codex-review-state:v1 {"code_review":{"commit_sha":"dc8eb9cb83b0a48ca51218d919288ae14297120f","has_any_findings":true,"has_unresolved_findings":true,"status":"completed"},"security_review":{"commit_sha":null,"has_any_findings":null,"has_unresolved_findings":null,"status":null}} -->` がある
- それでも poller は 30 分 `state=in_progress` のまま、次で終わった:
  `[review-poll] status: ABORT` / `evidence: IN_PROGRESS_TIMEOUT_SEC (1800s) exceeded`

### Expected Behavior (EB)

- bot が現在 head に指摘つきの review を出したら、本文の先頭に HTML コメントがあっても `done_retry`（verdict `RETRY` → `pr-fix`）
- 根拠: `.kaji/wf/official/dev.yaml:186-192`（`review-poll` の `RETRY: pr-fix`）、`.claude/skills/review-poll/SKILL.md`
  「検出ロジック（仕様）」の「現在 head への bot COMMENTED review → RETRY」、Issue 本文「期待」「完了条件」

### 目的

Issue 本文「目的」のとおり。review 本文の markup 変化に左右されにくい `codex-review-state:v1` を主シグナルにして
RETRY / PASS を安定させる。PASS は #429 の 2 シグナル（SHA を持つ bot シグナル + 完了より後の `+1`）を維持する。
state を出さない repo では、修正した旧経路で判定を続ける。

## 再現手順（steps-to-reproduce）

1. 前提: main `0cbefb1` の `kaji_harness/scripts/codex_review_poll.py`。入力は kamo2 PR #1837 の trusted bot review
   （`commit_id == dc8eb9cb83b0a48ca51218d919288ae14297120f`、本文の先頭が `<!-- codex-review-receipt:v1 -->`）と summary comment
2. 実行: `classify(reactions=[], reviews, "dc8eb9cb83b0a48ca51218d919288ae14297120f", "2026-10-16T17:40:00Z", comments=comments, prev_state="in_progress")`
3. 観測: `PollResult(state='in_progress', reason='ambiguous bot review on head dc8eb9c')`（期待は `done_retry`）
4. 対照: review 本文から先頭の `<!-- codex-review-receipt:v1 -->` だけを除いて 2 を再実行 → `done_retry`

## 根本原因（Root Cause）

- **なぜ壊れるか**: `classify()`（`codex_review_poll.py:279-284`）は `body.lstrip().startswith(CODEX_REVIEW_BODY_MARKER)` で
  RETRY を判定する。`lstrip()` は空白しか除かないため、先頭の HTML コメントがあると偽になり、
  `ambiguous_review = True` に落ちる。ambiguous は PASS を止めるだけで terminal にならず、`eyes` もないので
  `prev_state`（`in_progress`）が続き、1800s で ABORT する
- **根本**: 判定が第三者 bot の**表示用 markup**（review 本文の見出し、summary の表）に依存している。
  bot は表示形式を予告なく変える（receipt の追加）。一方、bot は summary comment に機械可読 state
  （full SHA・`status`・`has_any_findings`）を出すようになった。markup ではなく state を読むのが根本修正（Issue「決定事項」スコープ）
- **いつから**: marker 判定は #182（`67ea565`, 2026-05-25）で導入し、PR #176 の先頭改行対策で `lstrip()` を加えた。
  bot 側の receipt と state は 2026-10-15〜16 に出始めた。kamo2 の summary を新しい順に 25 件調べると、
  PR #1823（2026-10-15T23:44Z 作成）以降の 6 件に state があり、#1822 以前の 19 件にはない。kaji PR #469（2026-10-04）にも state はない
- **同根の他の壊れ箇所**:
  - PASS 経路の `_parse_summary_row()` も表の markup（`Code Review` 行・backtick の短縮 SHA・`datetime` 属性・`Completed`）に依存する。
    現時点では壊れていない（kamo2 PR #1834 / #1826 の表は従来形式）が、同じ理由で将来壊れうる → state 経路で置き換える
  - `parse_review_summary()` は本文が `<!-- codex-pull-request-review-summary -->` で始まることを要求する。現行の summary は 1 行目が
    この marker なので壊れていない（state は 2 行目）。本 Issue ではこの選別規則を変えない
  - 他に bot 本文の先頭一致に依存する箇所はない（`grep -rn "startswith(CODEX_REVIEW_BODY_MARKER\|SUMMARY_MARKER" kaji_harness/`）

## インターフェース

公開 CLI（`kaji pr review-poll`）の引数・終了コード・stdout の verdict 形式は変えない。変わるのは判定の入力と、
PASS 証跡の schema、stderr の deprecation 警告。

### 入力

| 入力 | 変更前 | 変更後 |
|------|--------|--------|
| summary comment の `<!-- codex-review-state:v1 {JSON} -->` | 読まない | **主シグナル**。trusted bot の summary comment（従来の選別規則で選ぶ最新 1 件）の中だけを探す |
| bot review 本文 | `lstrip().startswith(marker)` | state がない場合のみ: 先頭の HTML コメント `<!-- … -->`（0 個以上）と空白を読み飛ばしてから `startswith(marker)` |
| summary の表（`Code Review` 行） | PASS の SHA・完了時刻 | state がない場合のみ使う（旧経路。挙動は不変） |
| `+1` reaction | PASS の必須シグナル | 不変（両経路で必須） |

### 出力

| 出力 | 変更前 | 変更後 |
|------|--------|--------|
| `PollResult` | `state` / `reason` / `approval` | `signal: Literal["state", "legacy"] \| None` を追加（どの経路が terminal を決めたか） |
| `classify()` の戻り | `done_fallback` を返さない | state があるのに解析できない・版が未知 → **`done_fallback`**（即時 `BACK_FALLBACK`） |
| `run_polling()` の terminal 集合 | `done_pass` / `done_retry` / `done_abort` | `done_fallback` を追加（classify 由来の即時 fallback を待たずに返す） |
| PASS 証跡 | `schema_version: 1` | state 経路の PASS は **`schema_version: 2`**。旧経路の PASS は v1 のまま |
| stderr | なし | 旧経路が terminal を決めたとき deprecation 警告を 1 プロセス 1 回 |
| verdict `reason`（`done_fallback`） | `NO_REACTION_TIMEOUT_SEC 経過しても…` 固定 | timeout と state 解析不能の両方を表す文言に一般化。具体的な原因は従来どおり `evidence` に出す |

### 使用例

```python
# state 経路（kamo2 PR #1837 の実データ）
result = classify([], reviews, "dc8eb9cb83b0a48ca51218d919288ae14297120f",
                  "2026-10-16T17:40:00Z", comments=comments, prev_state="in_progress")
assert result.state == "done_retry" and result.signal == "state"

# 旧経路（summary に state がない repo）: 先頭に receipt があっても RETRY
reviews[0]["body"] = "<!-- codex-review-receipt:v1 -->\n\n\n### 💡 Codex Review\n\n…"
result = classify([], reviews, head, committed_at, comments=comments_without_state)
assert result.state == "done_retry" and result.signal == "legacy"
```

## 制約・前提条件

- bot の state は第三者仕様。観測できたのは `code_review` / `security_review` の 2 key、各 `commit_sha`（40 桁 hex or null）/
  `has_any_findings`（bool or null）/ `has_unresolved_findings` / `status`（`"completed"` or null）だけ（kamo2 PR #1823〜#1837 の 6 件）
- state は時刻を持たない。PASS の「完了より後の `+1`」は別の時刻根拠が要る（下記「方針 3」）
- 誤りの方向は fail-closed に揃える。誤って PASS（merge へ進む）方向には壊れず、判定不能なら fallback / 待機に倒す（#429 の方針）
- `classify()` は純粋関数のまま保つ（I/O・stderr 出力は `run_polling()` 側）
- `issue-close` は v1（旧経路 PASS）と v2（state 経路 PASS）の両方を検証する。v1 の受け付けは旧経路と一緒に次の版で撤去する
- 変更スコープ: `kaji_harness/scripts/codex_review_poll.py`、`kaji_harness/review_poll_evidence.py`、
  `.claude/skills/issue-close/SKILL.md`（jq）、`.claude/skills/review-poll/SKILL.md`、`docs/ARCHITECTURE.md`、`CHANGELOG.md`、
  `tests/test_codex_review_poll.py`、`tests/test_review_poll_evidence.py`、`tests/test_issue_close_evidence_jq.py`、
  `tests/test_review_poll_large_local.py`（v2 PASS の 1 経路）、`tests/fixtures/codex_review_poll/`（kamo2 実データ）。
  `review_poll_entry.py` は変更しない見込み

## 方針

### 1. state の抽出と検証（新規・純粋関数）

- summary comment の選別は既存 `parse_review_summary()` と同じ規則（trusted bot・本文が summary marker で始まる・`updated_at` 最大で一意）。
  共通部分を `_latest_summary_comment()` に切り出し、表の解析（旧経路）と state の解析（新経路）の両方が使う
- 選んだ comment 本文から `<!-- codex-review-state:` を探す。結果は 3 値:
  - **absent**: marker がない → 旧経路
  - **invalid**: marker はあるが、版が `v1` でない / 2 個以上ある / JSON として壊れている / 下記の検証に失敗 → `done_fallback`
  - **valid**: `ReviewState`（`code_review` / `security_review` の 2 つの `ReviewStateEntry`、comment id / url / `updated_at`）
- `updated_at` 最大が 2 件以上で一意に選べないとき: そのどれかに state marker があれば invalid（state があるのに判定できない）、なければ従来どおり absent 扱い
- 検証（Pydantic、未知の追加 key は無視。使う key だけ必須）:
  - `code_review` / `security_review` の両方が object として存在する
  - 各 entry: `commit_sha` は `^[0-9a-f]{40}$` か null、`has_any_findings` は bool か null、`status` は空でない文字列か null
  - `status == "completed"` なら `commit_sha` は 40 桁 SHA、`has_any_findings` は bool（null は invalid）
- trusted bot 以外の comment にある state は読まない（なりすまし防止。既存の id 一致規則と同じ）

### 2. classify() の評価順

```
summary = _latest_summary_comment(...);  state = parse_review_state(summary)
if state is invalid:                     return done_fallback (signal="state")
head_review_exists = trusted bot review with commit_id == head が 1 件以上
if state is valid:                       # state 経路（旧経路の review 本文・表は見ない）
    cr, sr = state.code_review, state.security_review
    sr_applies = sr.status is not None and sr.commit_sha in (head, None)
    if cr.for_head and cr.completed and cr.has_any_findings:          return done_retry
    if sr_applies and sr.completed and sr.has_any_findings:          return done_retry
    if (cr.for_head and cr.completed and not cr.has_any_findings
        and (not sr_applies or (sr.completed and not sr.has_any_findings))
        and not head_review_exists):
        approval = fresh trusted +1 (>= head_committed_at and >= floor(summary.updated_at) + 1s)
        if approval:                                                  return done_pass (signal="state")
else:                                    # 旧経路（state absent）
    if any head COMMENTED review whose body, after skipping leading <!-- … --> and whitespace,
       startswith("### 💡 Codex Review"):                             return done_retry (signal="legacy")
    if not head_review_exists: 既存の表 + `+1` 判定                   -> done_pass (signal="legacy")
eyes -> in_progress / else prev_state（従来どおり）
```

- `cr.for_head` は `commit_sha == head_sha`（full SHA の完全一致）。head と異なる state は head の完了として扱わない
- `status` が `completed` でも null でもない値（未観測を含む）は実行中として扱い、PASS にも RETRY にもしない
- state 経路でも「現在 head に trusted bot review があれば PASS にしない」を維持する（state の「指摘なし」と矛盾するため。#429 の I8 `current_head_bot_reviews == 0` と同じ）
- 先頭 HTML コメントの読み飛ばし: `s.lstrip()` → `<!--` で始まる間は対応する最初の `-->` の後ろまで進めて再度 `lstrip()`。
  閉じない `<!--` はそこで打ち切り不一致。「本文のどこかに含まれる」までは緩めない（Issue「修正の方針（案）」の論点。
  先頭以外の一致を認めると、引用や別種の bot 本文を誤って RETRY にしうる。先頭読み飛ばしで実データを満たせる）

### 3. state 経路の PASS の時刻根拠

- `+1` は summary comment の **`updated_at` より後の秒**（`created_at >= floor(updated_at) + 1s`）で、かつ `created_at >= head_committed_at`
- 理由: state は comment 本文の一部なので、観測した state（`completed`・指摘なし）は遅くとも `updated_at` の時点で書かれている。
  その後の秒に付いた `+1` は、観測した state より後だと証明できる。表の `datetime` 属性（markup）に依存せず、
  security review を含む全 review の最後の書き込みも覆う。comment が後から更新されると閾値が後ろにずれて PASS できなくなるだけで、
  誤った PASS にはならない（fail-closed）
- 実データでの成立確認: kamo2 PR #1834（updated_at `11:33:53Z` → `+1` `11:33:56Z`）、PR #1826（`03:44:09Z` → `03:44:12Z`）

### 4. PASS の確定と証跡 v2

- `_finalize_pass()` の head 再確認（判定の直前・直後）と open 確認は両経路で共通
- 短縮 SHA → PR commits の一意性確認（`pulls/{n}/commits`）は旧経路だけで行う。state 経路は full SHA の完全一致で済む
- state 経路の証跡（`ReviewPollEvidenceV2`、`extra="forbid"`・strict・frozen）:

```text
schema_version: 2, kind, result: "PASS", provider: "github", repository, pull_request, reviewed_head   # v1 と同じ
approval:
  bot: {id, login}                                   # v1 と同じ
  reaction: {id, content: "+1", created_at, api_path} # v1 と同じ
  review_state:
    comment_id, url, comment_updated_at, state_version: "v1"
    code_review:     {commit_sha, status, has_any_findings}
    security_review: {commit_sha, status, has_any_findings}   # 観測値をそのまま（null 可）
checks: {head_sha_at_start, head_sha_before_decision, head_sha_after_decision, current_head_bot_reviews}
fetched_at, decided_at
```

- v2 の不変条件（v1 の I1〜I9 と対応づける。I2〜I5・I9 は v1 と同じ）:
  - I1(v2): 上記キー集合の完全一致、`schema_version == 2`
  - I6(v2): `review_state.comment_id` は正の整数、`url` は `#issuecomment-<comment_id>` で終わる、`state_version == "v1"`、
    `code_review.commit_sha == reviewed_head.sha`・`status == "completed"`・`has_any_findings == false`、
    `security_review` は「`status == null`」「`commit_sha` が head と異なる 40 桁 SHA」「`status == "completed"` かつ `has_any_findings == false`」のいずれか
  - I7(v2): `reaction.created_at >= floor(comment_updated_at) + 1s`、`reaction.created_at >= reviewed_head.committed_at`
  - I8(v2): 3 つの head SHA がすべて `reviewed_head.sha`、`current_head_bot_reviews == 0`（`short_sha_matching_pr_commits` は持たない）
- `review_poll_evidence.py`: 既存 model を v1 として残し、v2 model を追加する。`load_evidence()` は `schema_version` で v1 / v2 を選ぶ
  （discriminated union）。`EVIDENCE_SCHEMA_VERSION` 定数は v1 / v2 の 2 つにする

### 5. issue-close の検証

- 2.5.3 の jq プログラムを 1 本のまま、`schema_version` で v1 / v2 の検査式を切り替える（`1` / `2` 以外は I1 違反）。
  `tests/test_issue_close_evidence_jq.py` の抽出規則（`VERIFIED=$(jq -r '…' "$EVIDENCE")`）を保つ
- 2.5.4 の現在 PR 照合（number / url / `headRefOid` / `OPEN`）と Step 3 の `--match-head-commit` は v1 / v2 共通の key だけを使うので変えない

### 6. deprecation 警告

- `run_polling()` が terminal の `PollResult` を返す直前、`signal == "legacy"` なら stderr に 1 回だけ書く
  （例: `kaji: deprecation: review-poll used the legacy codex review markup (no codex-review-state:v1 in the summary comment); this path will be removed in the next release`）
- stdout（verdict / heartbeat）には出さない。`---` で始めない。exec step の stderr は `script_exec` が `stderr.log` に流すだけで verdict 抽出に使わない
- 書き込みの `OSError` は握りつぶす（heartbeat と同じく観測用の副作用）

### 7. 文書

- `CHANGELOG.md` `[Unreleased]`: `### Fixed`（receipt 付き本文での RETRY 見逃し、state 採用）、`### Changed`（PASS 証跡 v2）、
  `### Deprecated`（旧経路。次の版で撤去。Broken contract / How to check / Migration の形式は 0.18.0 の `inject_verdict` に倣う）
- 撤去を追跡する Issue は implement で起票し、`### Deprecated` から参照する（Issue「旧経路の寿命」の「別 Issue で追跡」）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| スコープ | marker 修正にとどめず `codex-review-state:v1` を判定シグナルに採用 | 人間決定: Issue「決定事項 / 人間決定」スコープ（grill-me Q1「根本修正したい」） | 方針 1・2 の state 抽出・評価順 |
| 適用範囲 | state を RETRY と PASS の両方に使う。PASS は `+1` 必須の 2 シグナル。証跡 v2 と issue-close 改修 | 人間決定: 同「適用範囲」（grill-me Q2） | 方針 3〜5（時刻根拠・v2 schema・jq の版分岐） |
| state がない / 壊れている | absent → 旧経路（先頭 HTML コメントを読み飛ばす RETRY + 既存の表と `+1`）。present だが解析不能・未知版 → 即時 `BACK_FALLBACK` | 人間決定: 同「state がない場合」（grill-me Q3・Q4） | invalid の具体条件（版・個数・JSON・型・completed 時の必須値）、tie 時の扱い、`run_polling` の terminal 集合に `done_fallback` を追加 |
| 旧経路の寿命 | 本版で残し `### Deprecated` で「次の版で撤去」と告知。撤去は別 Issue | 人間決定: 同「旧経路の寿命」（grill-me Q3・Q4 (a)） | 撤去追跡 Issue を implement で起票し CHANGELOG から参照 |
| `security_review` | head 向けで未完了なら PASS しない、完了 + 指摘ありなら RETRY、null は無視 | 人間決定: 同「`security_review` の扱い」（grill-me Q5） | 「null」を `status == null` と定義。`commit_sha` が null で status 非 null は head 向けとみなす（安全側） |
| 指摘の有無に使う field | `has_any_findings` を使い `has_unresolved_findings` は使わない | AI の仮定（Issue「AI の仮定」1 行目）。根拠: `pr-fix` は thread を resolve しない。検査先: review-design | 検証でも `has_unresolved_findings` を必須にしない |
| 未観測の `status` | `completed` / null 以外は実行中（PASS にも RETRY にもしない） | AI の仮定（Issue「AI の仮定」2 行目）。検査先: review-design / Small テスト | 方針 2 の評価順に反映 |
| 証跡 v1 / v2 の併存 | issue-close は両方受け付ける。v1 受け付けは旧経路と同時に撤去 | AI の仮定（Issue「AI の仮定」3 行目、人間決定の帰結）。検査先: review-design | jq を 1 本のまま版で分岐 |
| deprecation 警告 | 旧経路が terminal を決めたとき stderr に 1 回 | AI の仮定（Issue「AI の仮定」4 行目、`inject_verdict` の前例）。検査先: review-code | 「判定に使われた」を「terminal を決めた」と定義。毎 poll では出さない（state を出す repo でも summary 出現前の poll は state absent になり、誤った警告になるため） |
| state 経路 PASS の時刻根拠 | summary comment の `updated_at` より後の秒の `+1` | AI の仮定（Issue「AI の仮定」5 行目で設計に委任）。根拠: 方針 3。検査先: review-design | 表の `datetime` は使わない。stale `+1` で PASS しない不変条件（#429）を維持 |
| head 以外の commit の `security_review` | 判定に影響させない（null と同じ） | AI の仮定。根拠: 人間決定は「現在の head に対する」と限定しており、`code_review` の「head と異なる state は head の完了として扱わない」と対称。検査先: review-design | I6(v2) で許容する組み合わせとして明記 |
| state 経路での head 上の bot review | 1 件でもあれば PASS にしない（RETRY にもしない） | AI の仮定。根拠: #429 の「head 上の他の bot review は PASS を止める」と I8 を維持する保守側の選択。検査先: review-design | 例: 同じ head で再 review して指摘なしになった場合は PASS せず timeout で fallback する |
| state 経路の RETRY で review 本文を併用するか | 併用しない（state が決める） | AI の仮定。根拠: 人間決定は「state がない場合に旧経路」。review 投稿から state 更新までの数秒（#1837: 17:50:56 → 17:51:00）は次の poll で拾える。検査先: review-design | 旧経路の撤去時に state 経路へ手を入れずに済む |
| 「本文のどこかに含まれる」まで緩めるか | 緩めない（先頭の HTML コメントだけ読み飛ばす） | 人間決定（同「state がない場合」が「先頭の HTML コメントを読み飛ばしてから判定」と指定） | 閉じない `<!--` は不一致 |
| `done_fallback` の verdict reason 文言 | timeout と state 解析不能の両方を表す文言に一般化 | AI の仮定。根拠: 固定文言が即時 fallback の原因と食い違う。検査先: review-code | 原因の詳細は `evidence`（`PollResult.reason`）に出す |

one-way door の未決はない。公開 CLI の引数・終了コード・stdout 形式は不変。証跡 schema の変更（v2 追加）と issue-close 改修は
人間決定「適用範囲」に含まれる。

## テスト戦略

### 変更タイプ

- 実行時コード変更（判定ロジック、証跡 schema、consumer の jq）

### 再現テスト（bug 必須）

- kamo2 PR #1837 の bot review と summary comment を fixture にして `classify()` を呼び、`done_retry` を assert する。
  修正前は `in_progress`（Issue「再現」と OB のログ）。実ログがあるため escape clause により実装前 Red の代替証跡とし、
  implement でも修正前に Red を確認する
- 同じ review を state なしの summary と組み合わせ、旧経路で `done_retry` になることも固定する（receipt 付き本文の旧経路の再現）

### Small テスト

- **旧経路の本文判定**: receipt 付き → `done_retry`、HTML コメントなし → `done_retry`（従来互換）、先頭コメント複数 → `done_retry`、
  閉じない `<!--` → 不一致、marker が先頭以外 → 不一致（ambiguous）、`commit_id != head` → `done_retry` にしない
- **state の抽出・検証**: 正常 v1、版 `v2` → invalid、marker 2 個 → invalid、壊れた JSON → invalid、`code_review` 欠落 → invalid、
  `completed` で `commit_sha` null / `has_any_findings` null → invalid、未知の追加 key → valid、trusted bot 以外の comment の state → 無視、
  `updated_at` tie で片方に state → invalid
- **state 経路の classify**: head + completed + 指摘あり → `done_retry`、指摘なし + 新しい `+1` → `done_pass`（`signal == "state"`）、
  `+1` なし / `updated_at` と同じ秒 / それ以前 / head commit より前 → PASS しない、`commit_sha != head` → RETRY・PASS しない、
  `status` が `in_progress` などの未観測値 → どちらにもしない、`security_review` が head 向け未完了 → PASS しない、
  head 向け完了 + 指摘あり → `done_retry`、null → 影響しない、head 以外の commit → 影響しない、head に bot review あり → PASS しない、
  state があるとき表が head・Completed を示しても旧経路の PASS を使わない
- **invalid state** → `classify()` が `done_fallback`
- **証跡 v2 model**: 正常 payload、I1(v2)/I6(v2)/I7(v2)/I8(v2) の各違反、v1 payload が引き続き valid、`load_evidence()` の版振り分け、未知の版を拒否
- **verdict**: `done_fallback` の reason 文言、`signal` が既存 verdict 形式を変えないこと
- **kamo2 実データ fixture**: PR #1837（指摘あり）→ `done_retry`。PR #1834（指摘なし + `+1`）の summary と reaction で `done_pass`

### Medium テスト

- `run_polling()`（`_FakeGh` + fake clock）: state 経路 PASS で v2 証跡が書かれ、`pulls/{n}/commits` を呼ばない。旧経路 PASS は v1 のまま
- invalid state の初回 poll で `NO_REACTION_TIMEOUT_SEC` を待たずに `done_fallback` を返す
- 旧経路が terminal を決めたとき stderr に警告が 1 回だけ出て stdout（heartbeat・verdict）に混ざらない。state 経路では出ない
- `tests/test_issue_close_evidence_jq.py`（実 `jq` subprocess）: v2 の正常 payload で `OK`、各違反で producer（Pydantic）と consumer（jq）の判定が一致、v1 の既存ケースが不変

### Large テスト

- `tests/test_review_poll_large_local.py`（stub `gh` を PATH に置いた実 subprocess）に state 経路 PASS を 1 本足し、
  stdout verdict PASS と v2 証跡の保存までを通す
- 実 GitHub（bot の非同期応答）を使う確認は恒久テストにしない。bot の応答は CI で再現できず（物理的に作成不可の正当理由）、
  判定入力は実データ fixture で固定できる

### 長時間 acceptance の配置

- 該当なし。全検証は fixture と stub で決定的に完結し、nested `kaji run` や外部 agent を使わない。
  所要は既存の `make check` の範囲（秒〜数十秒）で、implement step の timeout の 25% を超えない

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（既存の review-poll 方式の入力変更） |
| docs/ARCHITECTURE.md | あり | 「review-poll の承認証跡」節: 承認の根拠（state 経路と旧経路）、schema v2、不変条件 I1/I6/I7/I8 の v2 版、consumer の版分岐 |
| docs/dev/ | なし | workflow 遷移と開発手順は変わらない（`grep -rn "review-poll" docs/dev/` で判定ロジックの記述がないことを確認） |
| docs/reference/ | なし | Python 規約に変更なし |
| docs/cli-guides/ | なし | `github-mode.md` は `review-poll` を名前で挙げるだけで判定ロジックを書いていない |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| .claude/skills/review-poll/SKILL.md | あり | 「検出ロジック（仕様）」の判定表、確定確認（短縮 SHA 確認は旧経路のみ）、証跡 v1/v2、`BACK_FALLBACK` の条件に state 解析不能を追加、deprecation |
| .claude/skills/issue-close/SKILL.md | あり | 2.5.3 の jq を v1/v2 対応に、説明文の I1〜I9 を版ごとに |
| CHANGELOG.md | あり | `[Unreleased]` に Fixed / Changed / Deprecated |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行の判定ロジック | `kaji_harness/scripts/codex_review_poll.py:251-304`（main `0cbefb1`） | `body.lstrip().startswith(CODEX_REVIEW_BODY_MARKER)` で RETRY。不一致の head review は `ambiguous_review` で PASS を止め、terminal にならない |
| 現行の PASS 判定 | `kaji_harness/scripts/codex_review_poll.py:146-248` | summary の表の `Code Review` 行（短縮 SHA・`Completed`・`datetime`）と `floor(completed_at)+1s` 以降の `+1` |
| 証跡 v1 | `kaji_harness/review_poll_evidence.py`、`docs/ARCHITECTURE.md`「review-poll の承認証跡」 | `schema_version: Literal[1]`、`extra="forbid"`、不変条件 I1〜I9（I8 は `short_sha_matching_pr_commits == 1`・`current_head_bot_reviews == 0`） |
| consumer の検証 | `.claude/skills/issue-close/SKILL.md` 2.5.3〜2.5.4 | 1 本の jq で key 集合まで厳密検証し、`schema_version == 1` を要求。2.5.4 は number / url / headRefOid / state だけで照合 |
| jq と producer の一致テスト | `tests/test_issue_close_evidence_jq.py:23` | `VERIFIED=$(jq -r '…' "$EVIDENCE")` を正規表現で抽出して実行する |
| 実データ（指摘あり） | `gh api repos/apokamo/kamo2/pulls/1837/reviews`、`gh api repos/apokamo/kamo2/issues/1837/comments`（private。repo owner の gh 認証で取得可。判定に使う箇所は本書「OB」「方針 3」に引用済み。implement で bot の review / summary だけを fixture として repo に置く） | review 5551610965 の本文先頭が `<!-- codex-review-receipt:v1 -->`。summary 6221370648 の 2 行目に state（`commit_sha` full・`completed`・`has_any_findings: true`）、updated_at `17:51:00Z` |
| 実データ（指摘なし） | `gh api repos/apokamo/kamo2/issues/1834/comments`・`…/1834/reactions`、同 1826 | state `has_any_findings: false`・`completed`。`+1` は summary の updated_at の 3 秒後（1834: 11:33:53Z → 11:33:56Z、1826: 03:44:09Z → 03:44:12Z）。表は従来形式 |
| 新形式の出現時期 | kamo2 の最近 25 PR の summary、kaji PR #469 | PR #1823（2026-10-15T23:44Z）以降の 6 件だけが state を持つ。段階展開の可能性 → 旧経路の併存が必要（人間決定 Q4 (a) の理由） |
| bot の挙動説明 | kamo2 PR #1837 summary 本文の「About Codex in GitHub」 | 「Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings.」→ `+1` は全 review 完了後 |
| `pr-fix` は resolve しない | `.claude/skills/pr-fix/SKILL.md` | inline comment へ thread で返信するだけ → `has_unresolved_findings` は再 review 後も true になりうる |
| workflow 遷移 | `.kaji/wf/official/dev.yaml:186-192` | `review-poll`: `PASS: close` / `RETRY: pr-fix` / `BACK_FALLBACK: review` / `ABORT: end` |
| 前例（Deprecated） | `CHANGELOG.md` 0.18.0「Deprecated」（`inject_verdict`）、0.21.0（local provider） | stderr 警告 + Broken contract / How to check / Migration の書式 |
| 関連設計 | `draft/design/issue-429-feat-review-poll-pr-head-sha.md`、Issue #182 | 2 シグナル PASS・stale `+1` 不可・fail-closed の方針、marker 判定と `lstrip` の導入経緯 |
| exec step の stderr | `kaji_harness/script_exec.py:117-150` | stderr は別 pipe で `stderr.log` に流す。verdict は stdout から抽出 |
