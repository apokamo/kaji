# [設計] review-poll: PR 承認と完全な HEAD SHA を照合できる構造化証跡

Issue: #429

## 概要

`review-poll` が PASS を返すとき、承認した PR identity・40 桁の reviewed head SHA・承認シグナル（bot identity / シグナル ID・URL / 時刻）を、schema version 付きの JSON 証跡として step attempt に保存する。承認シグナルと SHA の対応付けは、SHA を含まない `+1` reaction 単独ではなく、bot の review summary comment が示す「対象 commit + Completed」と組み合わせて行う。後続 close はこの証跡を現在の PR head と照合し、`--match-head-commit` 付きで merge する。

## 背景・目的

### ユースケース

- **close agent（`issue-close`）として**、review-poll PASS 後に merge する前に、「どの PR のどの full SHA が、どの bot シグナルで承認されたか」を構造化データで照合したい。文章の解釈や Issue verdict marker の有無に頼らないためである。
- **workflow 運用者として**、過去時刻の commit を後から push した場合など、前 head への古い `+1` が新 head の承認として扱われないことを保証したい。

### 現状の制約（Issue 本文「現行実装で確認した制約」と現行 main `067a217` のコードで確認）

1. `codex_review_poll.classify()` は trusted bot の `+1` を `created_at >= head_committed_at` だけで PASS とする（`kaji_harness/scripts/codex_review_poll.py` の `classify`）。reaction には commit SHA がなく、commit 時刻は push 時刻もレビュー対象も表さない。
2. PASS verdict の `evidence` は `bot +1 reaction (fresh, <t1> >= <t2>)` という文章だけで、PR 番号も完全な SHA も含まない。
3. polling 中に PR head が変わっても、再取得も検知もしない（`head_sha` は entry が 1 回だけ解決する）。
4. `_gh_api` は `gh api --paginate` の stdout を `json.loads` 1 回で読む。gh の公式マニュアルによれば `--paginate` は「Each page is a separate JSON array or object」で、ページごとに別々の JSON 値を出力する。そのため 2 ページ目以降があると `JSONDecodeError` になり、API failure 扱いになる。
5. `issue-close`（GitHub 経路）は承認を確認せずに `kaji pr merge [branch_name]` を実行する。

### 一次観測（依頼元 PR の bot シグナル）

依頼元 PR（apokamo/fullstack-agent-template#163）で、bot（id `199175422`）は SHA を含まない `+1` reaction のほかに、次の **review summary issue comment** を残している（`gh api repos/apokamo/fullstack-agent-template/issues/comments/5577710898` で確認）。

- 本文の先頭は `<!-- codex-pull-request-review-summary -->`。同じ comment が更新され続ける（`created_at 01:30:05Z` / `updated_at 01:33:33Z`）
- 表の 1 行: `| 📝 **Code Review** | ✅ **Completed** <relative-time datetime="2026-09-08T01:33:32.391219Z">…</relative-time> | \`91d11b1\` | PR opened |`
- 同じ comment 内の説明文: "Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings."
- `+1` reaction は `01:33:35Z` で、Completed（`01:33:32.39Z`）の後に付いている

bot シグナルのうち commit SHA（短縮形）を持つのは、PASS 側ではこの summary comment だけである。そこで本設計は summary comment を SHA 対応付けの根拠にする。

### 代替案と不採用理由

| 案 | 内容 | 不採用理由 |
|----|------|-----------|
| A | 現在の head SHA を既存の `+1` に添付して証跡化する | Issue「設計で決定する事項」が明示的に禁止している。reaction 自体は SHA を持たない |
| B | push 時刻（timeline / GraphQL `pushedDate`）と `+1` 時刻を比べる | `pushedDate` は GitHub 側で非推奨で、REST timeline には通常 push の時刻がない。時刻比較という本質的な弱点も残る |
| C | polling 開始時に既存 reaction の ID を記録し、開始後に新しく付いた `+1` だけを承認とみなす | 同じ user の `+1` は 1 件しか持てず、残り続ける。既存の `+1` が新 head 用に付け直される保証はない。polling 開始前に完了した正当な承認も失う。どちらにしても SHA には紐づかない |
| D（採用） | summary comment の「対象 commit（短縮 SHA）+ Completed + 完了時刻」と、完了後の `+1` を組み合わせる。さらに PR commits の中で短縮 SHA が head ただ 1 件にだけ一致することを確認する | SHA を持つ bot 自身のシグナルに承認を結び付けられる。曖昧な場合は PASS にしない（fail-closed） |

## インターフェース

### 入力

| 経路 | 項目 | 型 | 必須 | 説明 |
|------|------|----|------|------|
| env（runner 注入。既存） | `KAJI_VERDICT_PATH` | str（絶対パス） | PASS には必須 | `<artifacts>/<issue>/runs/<run_id>/steps/review-poll/attempt-NNN/verdict.yaml`。証跡の保存先を決めるのに使う |
| env（既存） | `KAJI_ISSUE_ID` / `KAJI_PROVIDER_TYPE` / `KAJI_GIT_REMOTE` / `KAJI_WORKTREE_DIR` / `KAJI_PR_ID` | str | 既存どおり | 変更なし |
| `codex_review_poll.main` argv（追加） | `--evidence-path` | str（パス） | 任意 | entry は `KAJI_VERDICT_PATH` がある場合だけ `<dirname>/review-poll-evidence.json` を渡す。未指定のまま PASS 条件が成立した場合は、PASS ではなく ABORT にする |
| `codex_review_poll.main` argv（既存） | `--head-sha` | str | 既存 | **40 桁の小文字 hex でなければ即 ABORT**（追加の検証） |
| GitHub REST API（追加取得） | `GET repos/{o}/{r}/pulls/{n}` | object | — | 毎 poll で `head.sha` / `state` / `html_url` を取得する |
| 〃 | `GET repos/{o}/{r}/issues/{n}/comments`（paginate） | list | — | 毎 poll で summary comment を探す |
| 〃 | `GET repos/{o}/{r}/pulls/{n}/commits`（paginate） | list | — | PASS 候補の確定時にだけ取得し、短縮 SHA が一意かを確認する |

既存の reactions（`issues/{n}/reactions`）と reviews（`pulls/{n}/reviews`）の取得は維持する。リスト系 API はすべて `gh api --paginate --slurp` で取得し、ページの配列を平坦化する。`--slurp` は gh の公式マニュアルにある機能で（"Use with --paginate to return an array of all pages of either JSON arrays or objects"）、kaji が最低要件とする gh 2.50.0（README）で使える。

### 出力

#### 1. verdict（stdout。形式は不変）

`---VERDICT---` ブロックの `status` / `reason` / `evidence` / `suggestion` の 4 フィールドと status 語彙（PASS / RETRY / BACK_FALLBACK / ABORT）は変えない。変えるのは文言だけである。

- PASS の `reason` を `trusted bot approval bound to PR head SHA (review summary Completed + 👍)` 相当に更新する（旧文言の `created_at >= head_committed_at` は判定条件の一部でしかなくなるため）
- PASS の `evidence` は複数行にし、`pr=<owner>/<repo>#<n>`、`head_sha=<40桁>`、`reaction_id=<id>`、`summary_comment=<html_url>`、`evidence_path=<絶対パス>` を含める（人間が読むための補助。機械照合の正本は JSON 証跡）
- ABORT の理由に「head changed」「SHA mapping ambiguous」「evidence unavailable」の区別を追加する

#### 2. 構造化証跡ファイル（PASS 時のみ。新規）

- **保存場所**: `dirname(KAJI_VERDICT_PATH)/review-poll-evidence.json`（例: `.kaji-artifacts/429/runs/<run_id>/steps/review-poll/attempt-001/review-poll-evidence.json`）
- **書き込み順序**: (1) PASS 候補の確定（下記「方針 4」の確認がすべて通る）→ (2) Pydantic model で検証 → (3) `fsio.atomic_write` で書き込む → (4) stdout に PASS verdict を出力 → (5) runner がプロセス終了後に stdout から `verdict.yaml` を書く（既存）。したがって `verdict.yaml` が PASS なら、同じ attempt に証跡ファイルが必ずある。(2) か (3) が失敗したら PASS を出さず ABORT を出す
- **PASS 以外**（RETRY / BACK_FALLBACK / ABORT）では証跡ファイルを書かない。attempt dir は attempt ごとに新しく作られるため、古い証跡が残る経路はない
- **schema v1**（`extra="forbid"`、frozen）:

```json
{
  "schema_version": 1,
  "kind": "kaji.review-poll.approval",
  "result": "PASS",
  "provider": "github",
  "repository": {"owner": "apokamo", "name": "fullstack-agent-template"},
  "pull_request": {
    "number": 163,
    "url": "https://github.com/apokamo/fullstack-agent-template/pull/163"
  },
  "reviewed_head": {
    "sha": "91d11b151e50a90ff02ffd427205f78017205bb4",
    "committed_at": "2026-09-08T01:23:50Z"
  },
  "approval": {
    "bot": {"id": 199175422, "login": "chatgpt-codex-connector[bot]"},
    "reaction": {
      "id": 123456789,
      "content": "+1",
      "created_at": "2026-09-08T01:33:35Z",
      "api_path": "repos/apokamo/fullstack-agent-template/issues/163/reactions"
    },
    "review_summary": {
      "comment_id": 5577710898,
      "url": "https://github.com/apokamo/fullstack-agent-template/pull/163#issuecomment-5577710898",
      "commit_short_sha": "91d11b1",
      "status": "Completed",
      "completed_at": "2026-09-08T01:33:32.391219Z",
      "comment_updated_at": "2026-09-08T01:33:33Z"
    }
  },
  "checks": {
    "head_sha_at_start": "91d11b1…(40桁)",
    "head_sha_before_decision": "91d11b1…(40桁)",
    "head_sha_after_decision": "91d11b1…(40桁)",
    "short_sha_matching_pr_commits": 1,
    "current_head_bot_reviews": 0
  },
  "fetched_at": "2026-09-08T01:33:41Z",
  "decided_at": "2026-09-08T01:33:42Z"
}
```

Issue 完了条件の各フィールドとの対応は次のとおり。repo / provider / PR 番号 → `repository` / `provider` / `pull_request.number`。完全な reviewed head SHA（40 桁）→ `reviewed_head.sha`（pattern `^[0-9a-f]{40}$`）。承認元 bot identity → `approval.bot`。シグナルの ID・URL → `approval.reaction.id` と `approval.review_summary.comment_id` / `url`。承認時刻 → `approval.reaction.created_at` と `review_summary.completed_at`。取得時刻 → `fetched_at`（確定確認の取得時刻）。判定結果 → `result`。

reaction には html URL がないため、ID と取得元の API path を記録する。時刻はすべて GitHub が返した文字列をそのまま保存し、`fetched_at` / `decided_at` だけを kaji が UTC の `Z` 形式で生成する。

#### 3. close 系 skill の照合手順（`issue-close` GitHub 経路。新規 Step）

方針 6 を参照。

### 使用例

```python
# review-poll 側（runner 経由。利用者のコードは変わらない）
# exec: [kaji, pr, review-poll]
#   → review_poll_entry.main()  # KAJI_VERDICT_PATH から --evidence-path を導出
#   → codex_review_poll.main([... "--evidence-path", ".../attempt-001/review-poll-evidence.json"])
#   → PASS なら証跡を atomic write した後に ---VERDICT--- status: PASS を出力

# 後続 close 側（skill の bash 手順を擬似コードで示す）
resolved = run("kaji issue resolve-verdict 429 --step review-poll --current-verdict-path <close の verdict_path>")
if resolved.source == "artifact" and resolved.status == "PASS":
    ev = load_json(dirname(resolved.verdict_path) / "review-poll-evidence.json")  # 無ければ停止
    pr = run("kaji pr view feat/429 --json number,headRefOid,url,state")
    assert ev.schema_version == 1 and ev.result == "PASS"
    assert (pr.number, pr.url, pr.headRefOid) == (ev.pull_request.number, ev.pull_request.url, ev.reviewed_head.sha)
    run(f"kaji pr merge feat/429 --match-head-commit {ev.reviewed_head.sha}")
```

### エラー（PASS にしない条件と扱い）

| 条件 | 扱い |
|------|------|
| `--head-sha` が 40 桁の小文字 hex でない | 即 ABORT（SHA が曖昧） |
| 毎 poll の `pulls/{n}.head.sha` が固定 head と違う | 即 ABORT（`head changed during polling`）。古い head の PASS は出さない |
| `pulls/{n}.state` が `open` でない | 即 ABORT |
| 確定時の再取得（判定直前・直後）で head が固定 head と違う | ABORT。証跡は書かない |
| PR commits の中で短縮 SHA に前方一致する commit が 0 件・2 件以上、または一致した commit が head でない | ABORT（`SHA mapping ambiguous`） |
| summary comment がない、形式を解釈できない、Commit が head に一致しない、Completed でない | PASS にしない（非 terminal のまま。既存の timeout で BACK_FALLBACK / ABORT へ） |
| trusted bot 以外の reaction / comment | 無視する（既存の id 照合を summary comment にも適用する） |
| trusted bot の review で `commit_id == head` かつ Codex marker 付き COMMENTED | RETRY（既存。PASS より優先） |
| trusted bot の review で `commit_id == head` だが上の形に合わない | PASS にしない（未解決の current-head 指摘があり得る、曖昧なシグナルとして扱う） |
| gh API の失敗（新しく加える取得や確定時の取得を含む） | 既存の連続失敗カウンタに合算する。3 回連続で ABORT |
| `--evidence-path` がない、証跡の検証や書き込みが失敗した | ABORT（`evidence unavailable`） |

## 制約・前提条件

- **既存 verdict 契約を維持する**: 4 フィールドと status 語彙、`return 0`、catastrophic 失敗は raise する、という方針（Issue #204）を変えない。`dev.yaml` 系の遷移（`PASS: close` など）も変えない
- **追加の外部依存なし**: `gh` CLI（≥2.50.0、`--slurp` 対応）と Pydantic（既存の依存）だけを使う
- **API 呼び出し数**: 毎 poll の呼び出しが 2 本（reactions / reviews）から 4 本（+ pulls / comments）に増え、確定時に commits と pulls（2 回）が加わる。poll 間隔 10 秒、in-progress 上限 1800 秒の既存設定では最大でおよそ 720 回。GitHub REST の認証済み上限（5,000 回/時）に収まる
- **summary comment の形式は第三者（bot）の仕様である**: 形式が変わると PASS が出なくなり、BACK_FALLBACK（`review` skill）へ縮退する。誤って PASS する方向には壊れない（fail-closed）
- **PR commits API は最大 250 件**: これを超える PR では head が一覧に出ない可能性があり、その場合は「曖昧」として ABORT する（公式 docs の制約）
- **Issue verdict marker を必須にしない**: review-poll は Issue コメントを投稿しない（既存どおり）。close の照合は artifact だけで完結する
- **既存モジュールへの依存**: `kaji_harness.fsio.atomic_write`、`kaji issue resolve-verdict`（Issue #426 の artifact fallback）、`gh pr merge --match-head-commit`（`kaji pr merge` は method flag 以外の引数をそのまま gh へ渡す。`kaji_harness/commands/pr.py` `_forward_to_gh`）

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/scripts/codex_review_poll.py` | summary comment の解析、`classify()` の PASS 条件の強化、毎 poll の head 照合、確定確認、`--evidence-path`、`_gh_api` の `--slurp` 平坦化、PR object の取得 |
| `kaji_harness/scripts/review_poll_entry.py` | `KAJI_VERDICT_PATH` がある場合に `--evidence-path` を追加するだけ |
| `kaji_harness/review_poll_evidence.py`（新規） | 証跡の Pydantic model（schema v1）、ファイル名定数、保存関数 |
| `.claude/skills/review-poll/SKILL.md` | 検出ロジック表、証跡契約（mirror）、head 変更時の挙動 |
| `.claude/skills/issue-close/SKILL.md` | GitHub 経路に「review-poll 承認証跡の照合」Step を追加し、merge に `--match-head-commit` を付ける |
| `tests/test_codex_review_poll.py` ほか | 下記「テスト戦略」 |
| docs | 下記「影響ドキュメント」 |

## 方針

### 1. 責務の配置

- `review_poll_entry`（env → argv の shim）: `--evidence-path` を導出するだけ。PR / head の解決は既存のまま
- `codex_review_poll`（polling の核）: 取得、分類、状態遷移、確定確認、証跡の生成、verdict の出力
- `review_poll_evidence`（新規・純粋なデータ契約）: `ReviewPollEvidence` model と `save_evidence(path, evidence)`。producer（poller）がこの model で検証する。consumer（close skill）は同じ schema を jq で照合する

### 2. 承認シグナルの解析（純粋関数）

- `parse_review_summary(comments, bot_id) -> ReviewSummary | None`
  - 対象: `user.id == bot_id` で、本文を `lstrip()` すると `<!-- codex-pull-request-review-summary -->` で始まる comment
  - 該当する comment が複数ある場合は `updated_at` が最大のものを採る。最大値が同じものが複数あれば曖昧として `None`
  - 表の行（`|` で始まり、セル数が 4 以上）を分解する。review 名のセルに `Code Review` を含む行を対象にする。status セルの `Completed` と `datetime="…"`、commit セルの `` `([0-9a-f]{7,40})` `` を取り出す
  - `Code Review` 行がない、または複数ある場合は `None`
- `classify(reactions, reviews, head_sha, head_committed_at, *, comments=(), bot_id=..., prev_state=...)`。`comments` は keyword-only で追加し、省略時は「summary なし」になる。評価順は次のとおり。
  1. trusted bot の COMMENTED review（Codex marker 付き、`commit_id == head`）→ `done_retry`（既存どおり）
  2. trusted bot の review で `commit_id == head` だが 1 に当たらないもの → PASS を阻止する（`prev_state` を維持し、理由に `ambiguous bot review on head` を記録）
  3. PASS 候補になるのは次をすべて満たす場合
     - summary が解析でき、Code Review 行が `Completed`、`head_sha.startswith(commit_short_sha)`、完了時刻が解析可能
     - trusted bot の `+1` で `created_at >= head_committed_at`（既存のガード）かつ `created_at >= floor_to_second(completed_at)`（「すべてのレビューが終わってから 👍」という bot の説明文に基づく）
     - 条件を満たせば `done_pass`。`PollResult` に追加する optional フィールド `approval` に reaction と summary の詳細を持たせる
  4. trusted bot の `eyes` → `in_progress`（既存）
  5. それ以外 → `prev_state`（既存）
- 時刻は `datetime.fromisoformat` で比較する（`Z` とマイクロ秒を正規化する）。解析できない時刻は PASS 候補から外す

### 3. 毎 poll の head 照合

`run_polling` は poll のたびに `pulls/{n}` を最初に取得する。`head.sha != fixed head` か `state != "open"` なら、その場で `done_abort` を返す。どちらの場合も最終的な PASS は出ない。

**停止を選び、新 head での再評価をしない理由**: 再評価するには `head_committed_at` と PR identity の再解決（entry の責務）が要り、step の途中で評価対象が黙って入れ替わる。停止しても `kaji run … --from review-poll` で安く再実行できる。

### 4. PASS 候補の確定確認

`classify` が `done_pass` を返したら、すぐに PASS を返さず次を行う。

1. `pulls/{n}` を再取得し、`head.sha == fixed head` を確認する（判定直前）
2. `pulls/{n}/commits` を paginate で取得し、`commit_short_sha` に前方一致する sha が 1 件だけで、それが fixed head であることを確認する
3. 証跡 model を組み立てて検証する（`fetched_at` は 1 の取得時刻）
4. `pulls/{n}` をもう一度取得し、`head.sha == fixed head` を確認する（判定直後）
5. `--evidence-path` へ atomic write する。その後で `done_pass` を返し、`main` が verdict を出力する

1・4 で head が違えば `done_abort`（head changed）、2 が成立しなければ `done_abort`（ambiguous）、3・5 で失敗すれば `done_abort`（evidence unavailable）とする。取得自体の失敗は連続失敗カウンタに合算し、次の poll で最初からやり直す。

証跡を書いた後に head が変わる隙間は残る。これは close 側の `--match-head-commit`（方針 6）で閉じる。証跡は「この SHA が承認された」ことを示し、merge 時点の一致は GitHub 側が原子的に保証する、という二段構えにする。

### 5. pagination

`_gh_api(path)` を `gh api --paginate --slurp <path>` に変え、外側の配列（ページ列）の各要素が list であることを検証してから平坦化する。object の単発取得には `_gh_api_object(path)`（`--paginate` なし、dict であることを検証）を新設する。

### 6. close の照合手順（`issue-close` GitHub 経路、Step 2 と Step 3 の間に挿入）

1. **適用判定**: harness 経由でプロンプトに `[verdict_path]` がある場合に限り、`kaji issue resolve-verdict [issue_id] --step review-poll --current-verdict-path [verdict_path]` を実行する
   - exit 0 で `.source == "artifact"` かつ `.status == "PASS"` → **照合対象**（2 へ）
   - exit 0 で status が PASS 以外、または exit 4（現在の run と recovery 復旧元に review-poll attempt がない）→ 照合対象外。承認は `review` / `pr-verify` 経路か人間の判断によるもので、既存手順のまま Step 3 へ進む
   - exit 0 で `source` key がない（marker で解決された）→ review-poll は marker を投稿しないため矛盾。ABORT
   - それ以外の非 0（5 / 7 など）→ ABORT（fail-closed）
   - `[verdict_path]` がない手動起動 → 照合対象外（人間が承認者）
2. **証跡の所在**: `dirname(.verdict_path)/review-poll-evidence.json`。ファイルがなければ旧形式の artifact とみなす。PR 状態から証跡を組み立てず（捏造しない）、ABORT する。suggestion は「`kaji run <workflow> [issue_id] --from review-poll` で再確認する」
3. **schema の照合**（jq）: `schema_version == 1`、`kind == "kaji.review-poll.approval"`、`result == "PASS"`、`provider == "github"`、`reviewed_head.sha` が `^[0-9a-f]{40}$`、`pull_request.number` が整数。1 つでも満たさなければ ABORT（未知の schema_version も ABORT）
4. **現在の PR との照合**: `kaji pr view [branch_name] --json number,headRefOid,url,state` の結果が、`number == pull_request.number`、`url == pull_request.url`（owner / repo / 番号の照合を兼ねる）、`headRefOid == reviewed_head.sha`、`state == "OPEN"` をすべて満たすこと。違えば ABORT
5. **merge**: 照合対象なら Step 3 を `kaji pr merge [branch_name] --match-head-commit <reviewed_head.sha>` で実行する。照合と merge の間に head が動いても GitHub が merge を拒否する。拒否されたら ABORT
6. Issue verdict marker の有無は、どの段階でも判定に使わない（明記する）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既存 verdict 形式と consumer の互換性 | 4 フィールドと status 語彙、遷移を維持する。文言だけを変える | Issue 本文「完了条件」2 項目目（人間決定） | PASS の reason / evidence の文言を変える。機械照合の正本を別ファイルに分離する |
| SHA を含まない reaction を SHA 承認として扱わない | `+1` 単独では PASS にしない。SHA を持つ bot の summary comment と組み合わせる | Issue 本文「設計で決定する事項」2 項目目（人間決定）。SHA を持つシグナルが summary comment だけであることは依頼元 PR の一次観測による | 案 D を採用する（代替案表）。短縮 SHA の一意性は PR commits で確認する |
| summary comment の形式解析（marker、`Code Review` 行、`Completed`、datetime 属性、backtick の短縮 SHA） | 上記の形式だけを PASS 候補として受け入れ、それ以外は fail-closed | **AI の仮定**。根拠: 依頼元 PR #163 の実 comment 1 件と、同 comment 内の bot 自身の説明文。検査先: review-design（形式の妥当性）、実装の Small テスト、ワークフロー完了後の確認項目（実 PR での確認） | 解析不能なら非 terminal → BACK_FALLBACK へ縮退する |
| `+1` は Completed の後に付く | `+1.created_at >= floor_to_second(completed_at)` を PASS の必要条件にする | **AI の仮定**。根拠: bot の説明文 "reacts with 👍 once all reviews finish with no findings" と実測（Completed 01:33:32.39Z → 👍 01:33:35Z）。検査先: review-design、実 PR での事後確認 | 古い `+1` が残ったまま、新 head のレビューが指摘付きで完了するまでの競合窓を閉じる。新 head でも正当な無指摘承認の 👍 が付け直されない場合は BACK_FALLBACK へ縮退する（安全側） |
| polling 中や判定前後の head 変化 | 再評価せず ABORT で停止する | Issue「設計で決定する事項」3 項目目は「再評価または停止」の選択を設計に委ねている（人間決定の範囲内）。停止を選んだのは **AI の判断**。根拠: 評価対象の暗黙の入れ替えを避けられ、`--from review-poll` で安く再実行できる。検査先: review-design | 毎 poll の head 照合と、確定確認の直前・直後の再取得 |
| 証跡の保存場所と書き込み順序 | `dirname(KAJI_VERDICT_PATH)/review-poll-evidence.json`。証跡を atomic write してから verdict を stdout へ出す | Issue「設計で決定する事項」1 項目目が設計に委ねている。具体値は **AI の仮定**。根拠: attempt ごとに新しく作られる dir なので古い証跡が残らない。consumer は既存の `kaji issue resolve-verdict` の `verdict_path` から辿れる（Issue #426 の契約）。検査先: review-design / review-code | baseline のように worktree 直下には置かない（attempt に紐づかないため） |
| `KAJI_VERDICT_PATH` がない手動実行での PASS | ABORT にする | Issue 完了条件 6 項目目「必要証跡の欠落を PASS にしない」（人間決定）の適用 | workflow 外で `kaji pr review-poll` を手動実行するのは想定外の経路であり、明示的に停止させる |
| close の照合を適用する範囲 | review-poll の最新 attempt が PASS の場合だけ照合する。`review` / `pr-verify` 経路と手動 close は既存手順のまま | Issue「対象外」（close の recovery policy 変更、すべての PR への fallback review / pr-verify の強制は対象外）と完了条件 8 項目目（人間決定）。経路判定に resolve-verdict を使うのは **AI の詳細化**。検査先: review-design | 人間が `--from close` で新しい run を起こした場合は exit 4 で照合対象外になる。これは人間による再開判断を承認とみなす既存運用の範囲であり、recovery 自動再開では `recovery-chain.json` を辿るので照合される |
| 旧 artifact（証跡なし）の扱い | 証跡を捏造せず ABORT し、`--from review-poll` での再確認を案内する | Issue 完了条件 8 項目目「証跡を捏造せず再確認または停止へ進む」（人間決定） | close 内で review-poll を再実行する案は採らない（close の attempt dir に別 step の証跡が混ざるため） |
| merge の原子性 | `--match-head-commit` を付ける | **AI の詳細化**。根拠: gh の公式マニュアル。`kaji pr merge` は method flag 以外をそのまま渡すため CLI 変更は不要。検査先: review-code | 公開 CLI は追加・変更しない |
| Issue verdict marker の必須化 | 必須にしない | Issue 本文「目的」3 項目目と完了条件 9 項目目（人間決定） | close 手順に「marker を判定に使わない」と明記する |
| pagination の実装 | `--paginate --slurp` を使い、ページを平坦化する | **AI の詳細化**。根拠: gh の公式マニュアル（ページは別々の JSON 値として出力される。`--slurp` で外側の配列に包む）と README の gh 2.50.0 要件。検査先: review-code と Medium テスト | 既存 `_gh_api` の暗黙の前提（単一の配列が返る）を是正する |

one-way door の未決: なし。公開 CLI の引数と終了コード、verdict 形式、workflow 遷移は変えない。証跡ファイルは新規の追加契約で、schema_version によって将来の非互換変更を検出できる。summary comment への依存は fail-closed で、誤っても BACK_FALLBACK への縮退にとどまり、後段の review や事後確認で安く直せる（two-way door）。

## テスト戦略

### 変更タイプ

- 実行時コード変更（review-poll の判定ロジック、証跡の生成）と skill 手順の変更（issue-close）

### 実行時コード変更の場合

#### Small テスト（`tests/test_codex_review_poll.py`、新規 `tests/test_review_poll_evidence.py`）

- **summary の解析**: 実 comment（PR #163）の本文を fixture にして、短縮 SHA / Completed / completed_at が取れること。trusted でない user の marker 付き comment は無視されること。marker なし、`Code Review` 行なし、In progress 相当、commit セル不正（6 桁、非 hex）、datetime 欠落 → `None` または PASS 候補外になること。複数の summary は `updated_at` 最大のものを採り、同値なら曖昧になること
- **classify の PASS 条件**:
  - formal review 0 件 + summary Completed(head) + `+1`（完了後）→ `done_pass`。`approval` に reaction id と summary の詳細が入ること（完了条件 3）
  - 古い `+1` が残り、新 head の commit 時刻がそれより古い（つまり `+1.created_at >= head_committed_at` は成り立つ）。summary は旧 head の短縮 SHA で Completed → PASS にならないこと（完了条件 4）
  - 同じ条件で summary が新 head に対して未完了 → PASS にならないこと
  - summary は新 head で Completed だが `+1` が完了より前 → PASS にならないこと
  - 信頼外 user の `+1` だけ → PASS にならないこと（完了条件 6）
  - current-head の bot COMMENTED review がある → `done_retry` が PASS より優先されること。marker なしの bot review が head にある → PASS にならないこと（完了条件 6「未解決の current-head 指摘」）
- **時刻比較**: `Z` / マイクロ秒付き / 秒精度が混在しても正しく比較できること
- **証跡 model**: 全必須フィールドが揃うと検証が通ること。SHA 39 桁・大文字・非 hex、`schema_version` 不正、余分なフィールドは `ValidationError` になること
- **verdict の互換性**: 各 status で `parse_verdict_block` が 4 フィールドを解釈できること（既存テストを維持）。PASS の evidence に PR、full SHA、evidence_path が含まれること
- **head SHA の事前検証**: `main` に 40 桁でない `--head-sha` を渡すと ABORT verdict になること

#### Medium テスト（`run_polling` / `main` / `_gh_api` を fake `_gh_api` / fake subprocess と `tmp_path` で結合）

- **正常 PASS と証跡の保存**: `main([... "--evidence-path", tmp])` → stdout が PASS で、証跡 JSON に repo / provider / PR 番号 / 40 桁 SHA / bot / reaction id / summary url / 時刻 / result が保存されていること。証跡を書き終えてから verdict が出力されること（emit を spy して順序を確認）（完了条件 1・3）
- **head 変化**: polling 開始時の head と違う head を `pulls/{n}` が返す（poll 中・確定直前・確定直後の各タイミング）→ `done_abort` で、証跡ファイルが作られないこと（完了条件 5）
- **SHA の曖昧さ**: PR commits に同じ短縮 SHA の commit が 2 件ある、または一致した commit が head でない → ABORT、証跡なし（完了条件 6）
- **証跡の欠落・書き込み失敗**: `--evidence-path` なし、または書き込みで `OSError` → ABORT、PASS は出さない（完了条件 6）
- **provider failure**: 新しく加えた取得（pulls / comments / commits）の失敗も連続 3 回で ABORT になり、2 回までなら回復すること（完了条件 6）
- **pagination**: fake の subprocess が `--slurp` 形式（`[[page1…],[page2…]]`）を返し、2 ページ目にある current-head の bot review を RETRY として検出すること。同じく 2 ページ目にある summary comment で PASS になり、2 ページ目にある信頼外の reaction は無視されること。`gh api` の argv に `--paginate` と `--slurp` が含まれること（完了条件 7）
- **entry**: `KAJI_VERDICT_PATH` があれば `--evidence-path <dir>/review-poll-evidence.json` が argv の末尾に付き、なければ argv が既存と完全に同じであること（`tests/test_review_poll_entry.py`）

#### Large テスト

- **large_local（新規 1 本）**: stub の `gh` / `kaji` / `git` 実行ファイルを `PATH` の先頭に置く（fixture JSON を返す）。`KAJI_VERDICT_PATH` を tmp にして、`python -m kaji_harness.scripts.review_poll_entry` を実際に subprocess 起動する。stdout の PASS verdict と `review-poll-evidence.json` の生成、内容の整合を確認する。argv の受け渡し、env の解釈、ファイル I/O、stdout の契約を端から端まで検証するのが目的
- **large_forge（実 GitHub API で bot の実応答を確認）は追加しない**: chatgpt-codex-connector[bot] の非同期応答とクレジットに依存するため、CI で再現できる恒久テストにできない（testing-convention の正当な理由「物理的に作成不可」）。Issue の `### ワークフロー完了後の確認項目` で、merge・release 後の実 PR で確認する

#### 既存テストの期待値変更（完了条件 2 で求められる正当化）

| 既存テスト | 変更 | 正当化 |
|-----------|------|--------|
| `TestClassify` / `TestRunPolling` で、`+1` だけの fixture から `done_pass` を期待するテスト（`test_fresh_plus_one_only_returns_done_pass`、`test_initial_fresh_plus_one_returns_done_pass`、`test_in_progress_then_pass` など） | 入力に summary comment（head、Completed）を追加する。期待値の `done_pass` は維持する | `+1` 単独での PASS を禁じる Issue の要件そのもの。summary を加えない場合の「PASS にならない」側は新規テストで固定する |
| `_gh_responses` helper（path → 応答の sequence） | pulls / comments / commits の応答を追加する | 新しく API を取得するため。reactions / reviews の扱いは不変 |
| PASS の reason / evidence 文言を検証するテスト（あれば） | 新しい文言に更新する | verdict の 4 フィールドと status は不変。文言は人間向けの補助（方針「出力 1」） |
| RETRY / BACK_FALLBACK / ABORT / heartbeat 系 | 期待値は変えない（helper の拡張だけ） | 判定順序は不変 |

#### close skill（`issue-close` SKILL.md）の検証

skill 手順は LLM 向けの文書なので、実行時コードの恒久テストは追加しない。代わりに次を行う。

- 実装時に、手順に書く jq / `kaji` コマンドを、fixture の証跡 JSON と resolve-verdict の出力例に対して実際に実行し、照合が成功・失敗の両方で期待どおりに分岐することを確認し、証跡を残す（変更固有の検証）
- 既存の `make verify-docs`（リンク検査）と `tests/test_skill_metadata.py`（frontmatter）が pass すること

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `.claude/skills/review-poll/SKILL.md` | あり | 検出ロジック表（PASS 条件、head 変化時の ABORT）、証跡契約（保存場所・schema・書き込み順序）、ABORT 理由の追加 |
| `.claude/skills/issue-close/SKILL.md` | あり | GitHub 経路に照合 Step を追加、`--match-head-commit`、旧 artifact のときの停止、marker を使わないことの明記（完了条件 8・9） |
| `docs/dev/workflow_guide.md` | あり | 「review-poll の前提」注記に、PASS が SHA に紐づく証跡を残すことと、close がそれを照合することを 1〜2 文追記 |
| `docs/ARCHITECTURE.md` | あり | review-poll の構造化証跡（artifact 配置と consumer）を 1 節追記（resolve-verdict 節の近く） |
| `docs/adr/` | なし | 新しい技術選定はない（gh `--slurp` は既存依存の機能） |
| `docs/reference/` | なし | 規約の変更はない |
| `docs/cli-guides/` | なし | `kaji pr review-poll` / `kaji pr merge` の CLI 仕様は不変（`--match-head-commit` は gh 側の既存 flag を素通しするだけ） |
| `docs/dev/skill-authoring.md` | なし | exec_script 契約は不変 |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約の変更はない |
| `CHANGELOG.md` | なし（本 Issue では） | release 時に `/release` が更新する |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 poller | `kaji_harness/scripts/codex_review_poll.py`（main `067a217`） | `classify()` は trusted bot の `+1` を `created_at >= head_committed_at` で PASS とする。`_gh_api` は `--paginate` の出力を `json.loads` 1 回で読む |
| 現行 entry | `kaji_harness/scripts/review_poll_entry.py` | PR と head を 1 回だけ解決して argv で poller に渡す。`KAJI_VERDICT_PATH` は使っていない |
| runner の env 注入 | `kaji_harness/runner.py` `_build_context_env` | exec / exec_script step に `KAJI_VERDICT_PATH`（attempt dir の `verdict.yaml`）を注入する。verdict の正本は stdout と `KAJI_VERDICT_PATH`（Issue #449） |
| resolve-verdict の artifact fallback | `docs/ARCHITECTURE.md` § `kaji issue resolve-verdict` の解決規則、`kaji_harness/commands/issue.py` | marker がない場合は `--current-verdict-path` の run の最新 attempt の `verdict.yaml` を採用し、`source: "artifact"` / `verdict_path` を返す。attempt がない場合は exit 4 |
| merge の転送 | `kaji_harness/commands/pr.py` `_forward_to_gh` | `pr merge` は `--merge/--squash/--rebase` だけを除去・強制し、それ以外の引数を gh へ渡す |
| 依頼元 PR の bot summary comment | https://github.com/apokamo/fullstack-agent-template/pull/163#issuecomment-5577710898 | 本文の先頭が `<!-- codex-pull-request-review-summary -->`。表の行は `Code Review | ✅ Completed <relative-time datetime="2026-09-08T01:33:32.391219Z"> | \`91d11b1\``。"Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings." |
| 依頼元 Issue | https://github.com/apokamo/fullstack-agent-template/issues/164 | Kaji 側で構造化証跡と head 照合を強化するよう依頼している。利用側の close 修正とは分離されている |
| gh api マニュアル | https://cli.github.com/manual/gh_api | "In `--paginate` mode … Each page is a separate JSON array or object. Pass `--slurp` to wrap all pages of JSON arrays or objects into an outer JSON array." |
| gh pr merge マニュアル | https://cli.github.com/manual/gh_pr_merge | "`--match-head-commit SHA`: Commit SHA that the pull request head must match to allow merge" |
| GitHub REST: Reactions | https://docs.github.com/en/rest/reactions/reactions#list-reactions-for-an-issue | reaction オブジェクトは `id` / `user` / `content` / `created_at` を持つ。commit SHA は持たない |
| GitHub REST: Issue comments | https://docs.github.com/en/rest/issues/comments#list-issue-comments | comment は `id` / `html_url` / `user` / `body` / `created_at` / `updated_at` を持つ |
| GitHub REST: Pull requests | https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request / https://docs.github.com/en/rest/pulls/pulls#list-commits-on-a-pull-request | `head.sha` / `state` / `html_url` を持つ。commits 一覧は "a maximum of 250 commits" |
| GitHub REST rate limit | https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api | 認証済みユーザーの primary rate limit は 5,000 回/時 |
| README の gh 要件 | `README.md`（"`gh` 2.50.0 or newer"） | 最低要件の時点で `--slurp` が使える |
| gh v2.48.0 release notes | https://github.com/cli/cli/releases/tag/v2.48.0 | "Added support for `--slurp`ing JSON responses in `gh api`"（cli/cli#8620）。最低要件 2.50.0 より前に入っている |
| 証跡の先例 | `kaji_harness/baseline.py`（`BASELINE_SCHEMA_VERSION`、`extra="forbid"`、`save_artifact` → `atomic_write`） | schema version 付きの Pydantic artifact を atomic write する既存パターンを踏襲する |
