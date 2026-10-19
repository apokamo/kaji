# [設計] review-poll: 旧経路（codex review markup 判定）と承認証跡 schema v1 を撤去する

Issue: #478

## 概要

`kaji_harness/scripts/codex_review_poll.py` の旧経路（review 本文の見出し・summary の表による判定）と、
`kaji_harness/review_poll_evidence.py` の承認証跡 schema v1 を撤去する。`review-poll` の判定と承認証跡を
`codex-review-state:v1` / schema v2 の 1 経路に揃える。

## 背景・目的

### 現状の問題（観測可能な形）

#473 で summary comment の `<!-- codex-review-state:v1 {...} -->` を主シグナルにした。state を出さない summary の
ために旧経路を残し、v0.23.0 の CHANGELOG `[0.23.0]` `### Deprecated` で「次の版で撤去」と告知した。現状は次のとおり。

- `classify()` に state 経路と旧経路の 2 経路がある（`codex_review_poll.py:543-572`）。旧経路は第三者 bot の
  表示用 markup（`### 💡 Codex Review` 見出し、summary の表の `Code Review` 行・backtick の短縮 SHA・
  `datetime` 属性・`Completed`）に依存する。#473 の根本原因は、この markup が予告なく変わったこと
- `_finalize_pass()` は経路で 2 系統に分かれる。旧経路だけが `pulls/{n}/commits` で短縮 SHA を照合し、
  schema v1 の証跡を書く（`codex_review_poll.py:697-711, 765-786`）
- 証跡 model は v1 / v2 の discriminated union（`review_poll_evidence.py:413-417`）。consumer の
  `issue-close` 2.5.3 の jq も `schema_version` で v1 / v2 を分岐する

### 改善指標

| 指標 | ベースライン（`d4ae9ef`） | 目標 |
|------|---------------------------|------|
| 旧経路・v1 のシンボル出現数（下記コマンド A） | `codex_review_poll.py`: 19、`review_poll_evidence.py`: 14、`issue-close/SKILL.md`: 2 | すべて 0 |
| 文書・skill の旧経路記述（下記コマンド B の一致行数） | 29 | 0（撤去の経緯を述べる行だけ許容。下記参照） |
| `classify()` の判定経路 | state / legacy の 2 経路 | state の 1 経路 |
| 証跡 schema の受け付け | producer / consumer とも v1・v2 | v2 のみ（v1 は producer が生成せず、`load_evidence()` と jq が拒否） |
| 維持項目の既存テスト | 全 pass | 期待値を変えずに全 pass（旧経路・v1 固有のケースの削除と、入力 fixture の移行を除く） |

### ベースライン計測

実装フェーズの冒頭（変更前）と完了時に同じコマンドで計測し、実装報告に記録する。

```bash
# A. 旧経路・v1 のシンボル出現数（ファイルごと）
grep -cE "_is_codex_review_body|_skip_leading_html_comments|parse_review_summary|_parse_summary_row|_find_approval|LEGACY_DEPRECATION_WARNING|EVIDENCE_SCHEMA_VERSION_V1|ReviewPollEvidence\b|ReviewSummaryEvidence|short_sha_matching_pr_commits" \
  kaji_harness/scripts/codex_review_poll.py kaji_harness/review_poll_evidence.py .claude/skills/issue-close/SKILL.md

# B. 文書・skill の旧経路記述（完了時に 0 件。CHANGELOG と draft/ は除外）
grep -rnE "旧経路|deprecated の旧経路|schema v1|Codex Review\`|review_summary" \
  docs/ARCHITECTURE.md docs/dev/workflow_guide.md .claude/skills/review-poll/SKILL.md .claude/skills/issue-close/SKILL.md

# C. 行数（参考値）
wc -l kaji_harness/scripts/codex_review_poll.py kaji_harness/review_poll_evidence.py   # 1081 + 432 = 1513

# D. 品質ゲート
source .venv/bin/activate && make check
```

コマンド B は、撤去と無関係な語（例: ARCHITECTURE の `legacy fallback`、`legacy/` ディレクトリ）を拾わないように
語を絞っている。完了時に残る一致は、撤去の経緯を説明する 1 行（「v0.23.0 まであった旧経路は撤去した」等）だけを許す。
その場合は実装報告に該当行を列挙する。

## インターフェース

### 公開 IF（不変）

- 公開 CLI `kaji pr review-poll`（`review_poll_entry` → `codex_review_poll.main`）の引数・終了コード・
  stdout の verdict 形式（4 フィールド、status 集合 `PASS` / `RETRY` / `BACK_FALLBACK` / `ABORT`）は変えない
- `run_polling()` の timeout 定数（`POLL_INTERVAL_SEC` / `NO_REACTION_TIMEOUT_SEC` / `IN_PROGRESS_TIMEOUT_SEC` /
  `EYES_GRACE_SEC` / `API_FAILURE_LIMIT`）と、それらによる遷移は変えない
- 証跡 schema v2（`ReviewPollEvidenceV2`）のキー集合・不変条件 I1〜I9（v2）・ファイル名・書き込み順序は変えない

### 告知済みの互換性変更（v0.23.0 で予告した Broken contract）

| 対象 | Before | After |
|------|--------|-------|
| `codex-review-state:v1` がない summary comment | 旧経路で PASS / RETRY を判定 | **PASS / RETRY にしない**。「terminal シグナルなし」として扱う（下記「方針 2」） |
| stderr の deprecation 警告 | 旧経路が terminal を決めたとき 1 回 | 出さない（旧経路がないため） |
| 証跡 `schema_version: 1` | producer が生成、consumer が受け付け | producer は生成しない。`load_evidence()` は `ValidationError`、`issue-close` の jq は I1 違反（ABORT） |

### 内部 IF の変更（`kaji_harness` 内部。公開 CLI ではない）

| シンボル | 変更 |
|----------|------|
| `codex_review_poll.ReviewSummary` / `parse_review_summary` / `_parse_summary_row` / `_skip_leading_html_comments` / `_is_codex_review_body` / `_find_approval` | 削除 |
| `CODEX_REVIEW_BODY_MARKER` / `HTML_COMMENT_OPEN` / `HTML_COMMENT_CLOSE` / `LEGACY_DEPRECATION_WARNING` / `_SUMMARY_COMMIT_CELL_RE` / `_SUMMARY_DATETIME_RE` / `_SUMMARY_COMPLETED_RE` / `_MARKUP_RE` | 削除（他の参照がないことを grep で確認してから） |
| `_default_warn` と `run_polling(..., emit_warning=...)` | 削除（警告の出し先がなくなる） |
| `Approval` | `summary` を削除し、`review_state: ReviewState` を必須にする |
| `PollResult.signal` | `Literal["state"] \| None` に縮める（`"legacy"` を削除）。field 自体は残す（下記 provenance） |
| `_finalize_pass()` | 旧経路分岐（`pulls/{n}/commits` の照合と v1 生成）を削除し、v2 だけを書く |
| `review_poll_evidence.ReviewSummaryEvidence` / `Approval`（v1）/ `Checks`（v1）/ `ReviewPollEvidence`（v1）/ `EVIDENCE_SCHEMA_VERSION_V1` | 削除 |
| `review_poll_evidence.AnyReviewPollEvidence` / `_EVIDENCE_ADAPTER` / `load_evidence()` | v2 だけを受け付ける。`load_evidence()` の戻り型は `ReviewPollEvidenceV2`。`schema_version` が 2 以外なら `pydantic.ValidationError` |
| `save_evidence()` | 引数型を `ReviewPollEvidenceV2` に |

v1 / v2 共通の不変条件 helper（`_check_common_invariants`）は v2 から使い続けるので残す。v2 の class 名
（`ReviewPollEvidenceV2` / `ApprovalV2` / `ChecksV2`）と `EVIDENCE_SCHEMA_VERSION_V2` は改名しない（改名は撤去に
付随しない変更で、diff と参照箇所を増やすだけのため）。

### 使用例

```python
# state がない summary（旧経路の表 Completed + 完了後の +1）: PASS にならない
result = classify([plus_one_after_completion], [], HEAD, HEAD_AT, comments=[legacy_summary_without_state])
assert result.state == "init"           # prev_state のまま（terminal ではない）

# state がない summary + 現在 head の Codex Review 見出し付き COMMENTED review: RETRY にならない
result = classify([], [codex_review_on_head], HEAD, HEAD_AT,
                  comments=[legacy_summary_without_state], prev_state="in_progress")
assert result.state == "in_progress"

# v1 の証跡は読めない
with pytest.raises(ValidationError):
    load_evidence(path_to_schema_v1_json)
```

## 制約・前提条件

- スコープは Issue「スコープ境界」に従う。変えるのは旧経路・v1 の撤去と、state がない summary の扱いの再定義だけ
- 維持する振る舞い（Issue「スコープ境界」の維持項目）:
  - `_latest_summary_comments` の選別規則、`parse_review_state` / `_parse_state_body` / `_classify_state` の判定、
    state が解析不能・版が未知・選別不能のときの即時 `done_fallback`
  - PASS の 2 シグナル（full SHA が head と一致する `completed`・指摘なしの state ＋ summary の `updated_at` より後の秒かつ
    head commit 以降の trusted bot `+1`）、現在 head に bot review があれば PASS にしない規則
  - `eyes` → `in_progress`、それ以外 → `prev_state` という `classify()` の残りの評価順
  - `run_polling()` の状態機械と timeout 定数、`_finalize_pass()` の head 再確認（判定の直前・直後）と
    「証跡の atomic write → PASS」の順序、取得失敗の連続失敗カウンタへの伝播
- `classify()` は純粋関数のまま保つ
- 誤りの方向は fail-closed（#429 / #473 の方針）。判定不能なら PASS / RETRY にせず、fallback / 待機に倒す
- 作業中に見つけた無関係な問題は別 Issue に起票する（`_shared/report-unrelated-issues.md`）

## 方針

### 1. Before / After の構造

```text
Before: classify()
  review_state = parse_review_state(...)
  invalid  -> done_fallback
  valid    -> _classify_state(...)                    # state 経路
  None     -> 旧経路: Codex Review 見出し -> done_retry / head review -> ambiguous /
              表 Completed + 後の +1 -> done_pass
  eyes -> in_progress / ambiguous -> prev_state / state 非 terminal -> prev_state / else prev_state

After: classify()
  review_state = parse_review_state(...)
  invalid  -> done_fallback                           # 不変
  valid    -> _classify_state(...)                    # 不変
  None     -> 何も判定しない（下記 2）
  eyes -> in_progress / state 非 terminal -> prev_state / else prev_state
```

```text
Before: _finalize_pass()
  head 再確認(前) -> [旧経路のみ] commits で短縮 SHA 照合 -> head 再確認(後) -> v1 or v2 を検証・保存 -> done_pass
After:  _finalize_pass()
  head 再確認(前) -> head 再確認(後) -> v2 を検証・保存 -> done_pass
```

旧経路の削除で `classify()` から `ambiguous_review` と旧経路分岐がなくなる。`head_reviews` は
`_classify_state()` の `head_review_exists` にだけ使う。

### 2. state がない summary の扱い（Issue で設計に委ねられた事項）

`parse_review_state()` が `None` を返す場合（summary comment がない、最新の summary に state marker がない、
最新の summary が複数で state marker を持たない）は、**どれも「terminal シグナルなし」として扱う**。
review 本文・summary の表は読まない。`classify()` は `eyes` があれば `in_progress`、なければ `prev_state` を返す。

この結果、`run_polling()` の既存の遷移でこう終わる（timeout 定数・状態機械は変えない）:

| 状況 | 結果 |
|------|------|
| `eyes` を一度も観測しない（init のまま） | `NO_REACTION_TIMEOUT_SEC`（60s）経過で `done_fallback` → `BACK_FALLBACK` |
| `eyes` を観測した後、state を持たない summary のまま | `IN_PROGRESS_TIMEOUT_SEC`（1800s）経過で `done_abort` → `ABORT` |
| 待機中に summary へ state が付いた | 以後は state 経路で判定（即時 `done_fallback` / RETRY / PASS / 待機） |

「即時 `BACK_FALLBACK`」は採らない。理由:

- state を出す repo でも、summary comment がまだ state を持たない瞬間がありうるかを確認できていない
  （#473 で観測できたのは完了後の state だけ。`draft/design/issue-473-...md`「制約・前提条件」）。
  その瞬間に即時 fallback すると、bot の結論を待たずに fallback review を走らせる
- 即時 fallback は `classify()` に新しい terminal 分岐を足すことになり、Issue「スコープ境界」の
  「撤去に付随しない feat / fix を含めない」に対して変更が広がる
- 待機は、state がない間を「state が非 terminal のとき」と同じに扱うだけで、新しい判定規則を作らない

トレードオフ: state を出さない bot 環境で `eyes` が観測された場合、`ABORT` まで最大 1800s 待つ
（#473 以前に receipt で起きた待ち方と同じ）。v0.23.0 の CHANGELOG で、state がない repo は撤去前に #478 へ
報告するよう告知済みで、報告はない（Issue「着手条件」の 2026-10-19 人間判断で、展開状況は着手条件にしない）。
CHANGELOG の撤去項目に「state がない summary では PASS / RETRY にならず、`BACK_FALLBACK`（60s）か
`ABORT`（1800s）で終わる」ことと確認方法を書き、利用者が症状から原因を辿れるようにする。

### 3. 削除と置換の順序（実装の移行ステップ）

1. ベースライン計測（コマンド A〜D）
2. safety net: 維持項目を検証する既存テストのうち、入力に旧経路の fixture（`_pass_snap()` の表 summary、
   `reviews_commented_current_head.json` の COMMENTED review 等）を使うものを、state を持つ同等の入力へ移す
   （下記「テスト戦略」）。この時点ではコード未変更で、移した後も全 pass することを確認する
3. state がない summary の扱いを固定する Small / Medium テストを追加する（この時点では旧経路が判定するので Red）
4. `codex_review_poll.py` から旧経路・警告・`_finalize_pass` の v1 分岐を削除する（3 が Green になる）
5. `review_poll_evidence.py` から v1 を削除し、`load_evidence()` を v2 のみにする
6. `issue-close/SKILL.md` 2.5.3 の jq から `$v == 1` 分岐と v1 の説明を削除する（`1` は既存の `else ["I1"]` に落ちる）
7. 旧経路・v1 に固有のテストを削除する
8. 文書（review-poll SKILL.md、ARCHITECTURE、workflow_guide、モジュール docstring、`_VERDICT_MAP` の reason）と
   CHANGELOG を更新する
9. 再計測（コマンド A〜D）

### 4. 文書・文言の更新

- `_VERDICT_MAP` の reason から旧経路の語を除く（PASS: `trusted bot approval bound to PR head SHA (codex-review-state + 👍)`、
  RETRY: `codex auto-review が現在 head に指摘ありと判定（codex-review-state）`）。`BACK_FALLBACK` の reason
  （`NO_REACTION_TIMEOUT_SEC` と `codex-review-state` を含む）は変えない。verdict の形式・status は変えない
- モジュール docstring（`codex_review_poll.py` / `review_poll_evidence.py`）から旧経路・v1 の記述を除く
- `docs/ARCHITECTURE.md`「review-poll の承認証跡」: 旧経路の箇条と「旧経路だけは短縮 SHA を照合」を削除する。
  不変条件表を v2 の 1 表にまとめる（I2〜I5・I9 は現行の文言、I1・I6〜I8 は現行の v2 の文言。consumer は
  `schema_version` が `2` 以外なら I1 違反）
- `.claude/skills/review-poll/SKILL.md`: 「旧経路」節、旧経路の ABORT 行（`SHA mapping ambiguous`）、確定確認の
  「（旧経路のみ）commits 照合」、schema v1 の箇条、PASS / RETRY 行の旧経路の説明を削除し、state がない summary の
  扱い（方針 2 の表）を追記する
- `docs/dev/workflow_guide.md` の review-poll の前提: 「state がない summary では deprecated の旧経路として表の
  `Completed`」を削除し、state がない summary では PASS にならないことに置き換える
- `CHANGELOG.md` `[Unreleased]` に `### BREAKING CHANGE`（または `### Removed`。既存の版の書式に合わせる）として、
  v0.23.0 の Deprecated 項目と同じ形式（Broken contract / How to check / Migration）で撤去を記載し、#473 / #478 を参照する

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 旧経路と証跡 v1 を撤去する | 次の版で撤去（本 Issue） | 人間決定: #473「決定事項」と grill-me provenance（https://github.com/apokamo/kaji/issues/473#issuecomment-6228107296）、v0.23.0 CHANGELOG `[0.23.0]` `### Deprecated`（Broken contract を予告） | 撤去するシンボル・分岐・テストを「インターフェース」「方針 3」で列挙 |
| 撤去の着手時期 | v0.23.0 公開済みで着手可。利用側の展開状況は着手条件にしない | 人間決定: Issue「着手条件」（2026-10-19 人間判断） | なし |
| スコープ境界 | 撤去と state なし summary の再定義だけ。維持項目は振る舞いを変えない | 人間決定: Issue「スコープ境界」 | 維持項目を「制約・前提条件」で関数単位に展開 |
| state がない summary を PASS / RETRY にしない | PASS / RETRY にしない | 人間決定: Issue「スコープ境界」「変えるもの」、v0.23.0 CHANGELOG の Broken contract | なし |
| state がない summary の待機・fallback | 「terminal シグナルなし」として扱い、既存の timeout で終える（init → 60s で `BACK_FALLBACK`、`eyes` 観測後 → 1800s で `ABORT`）。即時 fallback は採らない | AI の仮定（Issue で設計に委任。two-way door: 内部判定で、後の版で即時 fallback に変えても公開 IF・証跡は変わらない）。根拠: 方針 2。検査先: review-design、Small / Medium テスト | 3 状況の結果を方針 2 の表に固定。CHANGELOG で症状と確認方法を告知 |
| `PollResult.signal` を残す | `Literal["state"] \| None` に縮めて残す | AI の仮定。根拠: Issue 完了条件 3「維持項目の既存テストが期待値を変えずに通る」。state 経路の既存テストは `result.signal == "state"` を assert している（`tests/test_codex_review_poll.py` の TestKamo2Reproduction / TestClassifyState / TestVerdictReasons）。検査先: review-design / review-code | `"legacy"` だけを型から除く |
| `run_polling(emit_warning=...)` を削除する | 引数と `_default_warn` を削除する | AI の仮定。根拠: 警告の唯一の出し先が旧経路で、Issue「対象」が `LEGACY_DEPRECATION_WARNING` の撤去を明記。state 経路のテストが渡している `emit_warning=warnings.append` と `warnings == []` の assert は旧経路の警告に関わる部分として削除する（完了条件 3 の除外に当たる）。検査先: review-code | 呼び出し側は `review_poll_entry.py` を含め `emit_warning` を渡していないことを grep で確認する |
| v2 の class 名を改名しない | `ReviewPollEvidenceV2` 等をそのまま使う | AI の仮定。根拠: 改名は撤去に付随しない変更（Issue「含めないもの」）。検査先: review-design | なし |
| 公開 CLI・timeout・証跡 v2 を変えない | 変えない | 人間決定: Issue「スコープ境界」「維持するもの」 | `_VERDICT_MAP` の reason 文言は旧経路の語だけを除く（形式・status は不変） |

one-way door の未決はない。公開 CLI・証跡 schema・互換性に関わる判断は、すべて上表の人間決定（#473 / v0.23.0 告知 /
Issue 本文）に出典がある。

## テスト戦略

### 変更タイプ

- 実行時コード変更（refactor。告知済みの互換性撤去を含む）

### 振る舞い非変更の保証（safety net と bridging）

- 維持項目のうち state 経路の判定（`TestParseReviewState` / `TestClassifyState` / `TestKamo2Reproduction` の state ケース /
  `TestRunPollingStatePath` の state ケース / `TestVerdictReasons` / `TestEmitVerdict` / heartbeat / `TestGhApi`）は、
  入力も期待値も変えずにそのまま通す
- 維持項目を検証しているが入力が旧経路に依存するテストは、**入力だけ** state を持つ同等の入力へ移し、期待値
  （`state` / verdict / reason の部分文字列 / 証跡の有無）は変えない。対象:
  - `_pass_snap()`（`TestRunPolling` / `TestHeadBinding` / `TestEvidenceUnavailable` / `TestProviderFailures` /
    `TestPaginationEndToEnd` / heartbeat / `TestMainPass` が使う）: state 付き summary（`completed`・指摘なし・head の full SHA）＋
    その `updated_at` より後の秒の `+1` にする。`summary_comment=...#issuecomment-501` を assert するテストがあるので
    comment id は 501 を保つ
  - `test_in_progress_then_retry` / `test_immediate_retry_pr_176_scenario` / `test_current_head_review_on_second_page_is_retry`:
    RETRY の根拠を state（`completed`・`has_any_findings: true`）に移す
  - `test_stale_plus_one_only_returns_done_fallback` / `test_same_second_plus_one_only_degrades_to_done_fallback`:
    state 付き summary の `updated_at` を基準にした stale / 同秒の `+1` に移す
  - `tests/test_review_poll_large_local.py` の `_summary_comment(with_state=False)` を使う PASS ケース: state 付きに移す
- 期待値を変えるのは v1 固有の assert だけ: `test_initial_pass_returns_done_pass_and_writes_evidence` の
  `approval.review_summary` と `checks.short_sha_matching_pr_commits` は v2 の `approval.review_state` と v2 の
  `checks`（4 key）に置き換える。large_local の `evidence.approval.review_summary` も同様
- 確定確認の取得失敗が連続失敗カウンタに合算されること（維持項目）は、現在 `commits` の取得失敗で検証している
  （`test_commits_fetch_failure_in_finalization_recovers` / `test_persistent_commits_failure_aborts_instead_of_looping`）。
  commits の取得は旧経路と一緒になくなるので、確定確認の `pulls/{n}` 再取得の失敗で同じ性質を検証する bridging test
  （2 回失敗で回復して PASS、継続失敗で `gh api failed 3 times` の ABORT・証跡なし）に置き換える。`_FakeGh` に
  「何回目の `pulls` 取得を失敗させるか」を指定する手段を足す

### Small テスト

- **state がない summary の扱い（新規。完了条件 2 を固定）**: `classify()` に state のない summary を与えたとき
  - 表が `Completed`（head の短縮 SHA）＋完了後の秒の trusted bot `+1` → `done_pass` にならず `prev_state`
  - 現在 head への COMMENTED review（`### 💡 Codex Review` 見出し、先頭 receipt あり / なし）→ `done_retry` にならず
    `prev_state`（`init` / `in_progress` の両方）
  - 上記 ＋ bot `eyes` → `in_progress`
  - kamo2 PR #1837 の receipt review を state なし summary と組み合わせた実データ → `done_retry` にならない
    （既存 `test_pr1837_receipt_review_without_state_is_legacy_retry` を、期待値を反転した撤去確認テストに置き換える）
- **証跡 v1 の拒否**: `load_evidence()` が `schema_version: 1` の JSON（現行 v1 の妥当な payload）を
  `ValidationError` で拒否する。`schema_version: 2` は従来どおり読める
- **jq の v1 拒否**: `tests/test_issue_close_evidence_jq.py` で、現行 v1 の妥当な payload を jq に通すと `I1` になる
  （既存の `test_unknown_schema_version_is_i1` に `1` を加える形でよい）。v2 の既存ケースは変えない
- **シンボル撤去**: 旧経路の公開シンボル（`parse_review_summary` / `LEGACY_DEPRECATION_WARNING` / `ReviewPollEvidence` /
  `EVIDENCE_SCHEMA_VERSION_V1`）を import するテストは削除する。撤去そのものは計測コマンド A（grep）と mypy で確認し、
  「存在しないこと」を assert する恒久テストは足さない（回帰情報が増えないため）

### Medium テスト

- **state がない summary の終わり方（新規）**: `run_polling()`（fake gh・fake clock）で
  - state なし summary ＋完了後の `+1` が続く → `NO_REACTION_TIMEOUT_SEC` 経過で `done_fallback`、証跡なし、
    `pulls/{n}/commits` を取得しない
  - `eyes` の後に state なし summary ＋ `+1` が続く → `IN_PROGRESS_TIMEOUT_SEC` 経過で `done_abort`、証跡なし
  - state なし summary の後に state 付き summary（`completed`・指摘なし）＋ `+1` になる → `done_pass`、v2 証跡
- **確定確認の取得失敗（bridging）**: 上記「振る舞い非変更の保証」の `pulls/{n}` 再取得失敗ケース
- 既存の移行済み Medium テスト（head binding・証跡なしの ABORT・provider failure・pagination・heartbeat・main の
  書き込み順序）が期待値を変えずに通ること

### Large テスト

- `tests/test_review_poll_large_local.py`（`large_local`: 実 subprocess で `kaji pr review-poll` を起動、gh は fake）:
  state 経路の PASS（v2 証跡と verdict）、head 移動の ABORT、証跡パスなしの ABORT を、入力移行後に期待値を変えずに通す。
  `test_legacy_pass_warns_on_stderr_only` は旧経路固有なので削除する。state 経路の PASS で stderr に deprecation 警告が
  出ないという既存 assert はそのまま残す（警告文言が消えるので常に満たされるが、回帰検知として害はない）
- 実 GitHub API への疎通（`large_forge`）は追加しない。判定は fake gh の入力で決まり、実 API 疎通は既存の
  `gh api` ラッパー（`TestGhApi`、変更なし）以上の情報を増やさないため。実 bot の state 出力は第三者仕様で、
  CI で再現できる fixture（kamo2 実データ）で固定している

### 長時間 acceptance の配置

- 該当なし。受入は Small / Medium / `large_local`（fake gh・fake clock で秒単位）と `make check` で完結する。
  nested `kaji run`、外部 agent を含む dogfood、実 bot を待つ検証は含めない

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない。撤去は #473 で決定・告知済み |
| docs/ARCHITECTURE.md | あり | 「review-poll の承認証跡」の旧経路の箇条・短縮 SHA 照合・v1 不変条件表を削除し、v2 の 1 表にまとめる |
| docs/dev/ | あり | `docs/dev/workflow_guide.md` の review-poll の前提から旧経路の記述を削除 |
| docs/reference/ | なし | 規約・API 仕様の変更なし |
| docs/cli-guides/ | なし | 公開 CLI の引数・出力形式は不変。review-poll の記述なし（grep で確認済み） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| .claude/skills/review-poll/SKILL.md | あり | 旧経路節・v1・旧経路 ABORT 行・commits 照合を削除し、state がない summary の扱いを追記 |
| .claude/skills/issue-close/SKILL.md | あり | 2.5.3 の jq の v1 分岐と v1 の説明を削除（`1` は I1 違反） |
| CHANGELOG.md | あり | `[Unreleased]` に撤去（BREAKING CHANGE / Removed）を記載 |
| README.md / llms.txt | なし | review-poll の旧経路・証跡 schema の記述なし（grep で確認済み） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #478 本文 | https://github.com/apokamo/kaji/issues/478 | 撤去対象、スコープ境界（変える / 維持する / 含めない）、着手条件の人間判断（2026-10-19）、完了条件 |
| #473 決定事項・grill-me provenance | https://github.com/apokamo/kaji/issues/473#issuecomment-6228107296 | 旧経路を残して告知し、次の版で撤去するという人間決定 |
| v0.23.0 Release | https://github.com/apokamo/kaji/releases/tag/v0.23.0 | 2026-10-17T13:42:32Z 公開。旧経路撤去と証跡 v1 拒否の予告を含む |
| CHANGELOG `[0.23.0]` `### Deprecated` | `CHANGELOG.md`（「review-poll legacy codex review markup path」項） | Broken contract:「a summary comment without `codex-review-state:v1` will no longer produce PASS / RETRY, and `issue-close` will reject `schema_version: 1` approval evidence」 |
| #473 設計書 | `draft/design/issue-473-fix-review-poll-codex-review-html-codex.md` | 「制約・前提条件」: 観測できた state は完了後の 6 件だけ。fail-closed（判定不能なら fallback / 待機）。「重要判断 provenance」: 証跡 v1 受け付けは旧経路と同時に撤去 |
| 現行実装 | `kaji_harness/scripts/codex_review_poll.py:500-584`（`classify`）、`:663-806`（`_finalize_pass`）、`:819-987`（`run_polling`） | 2 経路の分岐、旧経路だけの commits 照合と v1 生成、timeout による終わり方（init → `done_fallback`、in_progress → `done_abort`） |
| 現行実装 | `kaji_harness/review_poll_evidence.py:199-248, 413-432` | v1 model と v1 / v2 の discriminated union、`load_evidence()` |
| 現行 consumer | `.claude/skills/issue-close/SKILL.md` 2.5.3 | jq が `schema_version` で v1 / v2 を分岐し、それ以外は `["I1"]` |
| 証跡契約の正本 | `docs/ARCHITECTURE.md`「review-poll の承認証跡」 | 不変条件 I1〜I9（v1）と I1・I6〜I8（v2）の表 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義、恒久テストの追加基準 |
| refactor 設計指針 | `.claude/skills/_shared/design-by-type/refactor.md` | 改善指標・ベースライン計測・safety net / bridging test |
