---
description: codex auto-review (chatgpt-codex-connector[bot]) の reactions / reviews / review summary comment を polling して PASS / RETRY / BACK_FALLBACK を判定し、PASS 時は PR head SHA に紐づく承認証跡を保存する。GitHub 限定。
name: review-poll
exec_script: kaji_harness.scripts.review_poll_entry
---

# Review Poll

GitHub `chatgpt-codex-connector[bot]` (id `199175422`) の auto-review シグナルを polling し、
verdict を出力する skill。`review` skill との二重起動を避け、auto-review クレジット不足時のみ
`BACK_FALLBACK` 経由で既存 `review` skill (codex agent) に fallback させる。

本 skill は LLM agent を起動しない deterministic skill であり、`kaji_harness.scripts.review_poll_entry`
module を直接 subprocess 実行する。entry module が env から PR 情報を解決し、polling 本体
`kaji_harness.scripts.codex_review_poll` に委譲する。

起動経路は 2 系統あり、いずれも同じ entry module に合流する:

- **builtin workflow（`dev` / `dev-thorough` / `docs`）**:
  各 YAML が `exec: [kaji, pr, review-poll]` で installed `kaji` CLI 経由で起動し、
  CLI 側 (`kaji pr review-poll`) が同 entry module（`review_poll_entry.main`）へ委譲する。
  dispatch の正本は YAML 側の `exec` step であり、agent / model / effort はスキーマレベルで
  指定不可（指定すると `kaji validate` が reject）。対象 repo の Python 環境ではなく installed
  `kaji` CLI 経由で起動するため可搬性が高い。
- **skill 単体起動・再利用**: frontmatter の `exec_script: kaji_harness.scripts.review_poll_entry`
  が契約となり、harness が同 entry module を ``python -m`` で subprocess 実行する。

どちらの経路でも、入力（env）/ verdict 仕様の正本は entry module（`review_poll_entry`）であり、
本ファイルの記述はその契約のミラーである。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| PR 作成後、codex auto-review が走っている GitHub 環境 | ✅ 必須 |
| `provider.type='local'` 配下 | ❌ ABORT verdict |
| `review-poll` で `BACK_FALLBACK` を受けた場合 | 既存 `review` skill (codex agent) に進む |

**ワークフロー内の位置**: i-pr → [PR 作成] → **review-poll**（builtin workflow では `exec` step として起動）→ (PASS=close / RETRY=pr-fix / BACK_FALLBACK=review fallback)

## 入力（harness が env として注入）

| env 変数 | 必須 | 説明 |
|---------|------|------|
| `KAJI_ISSUE_ID` | ✅ | PR 解決の検索キー |
| `KAJI_PROVIDER_TYPE` | ✅ | `github` 以外は ABORT |
| `KAJI_GIT_REMOTE` | ✅ | owner/repo 解決 (`git remote get-url`) |
| `KAJI_WORKTREE_DIR` | ✅ | `git remote get-url` 実行 cwd |
| `KAJI_PR_ID` | 任意 | harness 側で解決済みの場合のみ。未設定なら `kaji pr list` で取得 |
| `KAJI_VERDICT_PATH` | PASS には必須 | runner が注入する `verdict.yaml` の絶対パス。承認証跡の保存先 `<dirname>/review-poll-evidence.json` の導出に使う。未設定のまま PASS 条件が成立すると `ABORT`（`evidence unavailable`） |

これらの env は entry module（`review_poll_entry`）が正本として解釈する。builtin workflow の
`exec` step は `agent` / `model` / `effort` をスキーマで拒否するため、これらを指定する余地はない。

## 検出ロジック（仕様）

`+1` reaction は commit SHA を持たないため、**`+1` 単独では承認にしない**（Issue #429）。
SHA を持つ bot シグナルは review summary comment（本文先頭 `<!-- codex-pull-request-review-summary -->`、
表の `Code Review` 行に状態・完了時刻・短縮 commit SHA）だけなので、これと `+1` を組み合わせる。

| 観測 | verdict |
|------|---------|
| bot による COMMENTED review (body は `body.lstrip().startswith("### 💡 Codex Review")` で判定) かつ `commit_id == head_sha` | `RETRY`（`PASS` より優先） |
| 上記以外の bot review が `commit_id == head_sha` にある | `PASS` にしない（未解決の指摘があり得る曖昧なシグナル） |
| summary comment の `Code Review` 行が **`Completed`** かつ短縮 SHA が現在 head に前方一致、かつ bot の `+1` が `created_at >= head_committed_at` **かつ** `created_at >= floor(Completed 時刻) + 1 秒`（完了秒より後の秒） | `PASS` 候補（下記「確定確認」を通れば `PASS`） |
| `NO_REACTION_TIMEOUT_SEC` (60s) 経過しても上記いずれも観測されず（stale `+1` のみ・summary なしの `+1` のみ・完了と同じ秒の `+1` のみ も含む） | `BACK_FALLBACK` |
| `IN_PROGRESS_TIMEOUT_SEC` (1800s) 経過しても結論が出ない、または GitHub API 連続失敗 | `ABORT` |
| polling 中・確定の前後で PR head が固定 head と変わった / PR が open でない（`head changed`） | `ABORT` |
| 短縮 SHA に一致する PR commit が 0 件・2 件以上、または head でない（`SHA mapping ambiguous`） | `ABORT` |
| `--head-sha` が 40 桁小文字 hex でない、証跡の検証・書き込み失敗、`KAJI_VERDICT_PATH` 未設定（`evidence unavailable`） | `ABORT` |

- **完了と同じ秒の `+1`**: reaction は秒精度、完了時刻はマイクロ秒精度のため、同じ秒だと前後が決まらない。
  その場合は承認にせず、既存 timeout で `BACK_FALLBACK`（`review` skill）へ縮退する（安全側）。
- **summary comment の形式は第三者（bot）の仕様**: 形式が変わると PASS が出なくなり `BACK_FALLBACK` へ縮退する。
  誤って PASS する方向には壊れない（fail-closed）。
- **head が変わったら再評価せず停止する**: 評価対象を黙って入れ替えない。`kaji run <workflow> <issue> --from review-poll` で再実行する。
- 完了済み COMMENTED review は reactions API では検出できない（reactions は現在値のみ）が、
  reviews API は履歴を返すため `commit_id == head_sha` で workflow 起動前の auto-review を
  検出可能（PR #176 シナリオ）。
- リスト系 API は全ページ（paginate + slurp）を取得して平坦化する（2 ページ目以降のシグナルも拾う）。

bot 識別は **id 一致**（`199175422`）を主、login を副チェックにする。

### 確定確認と承認証跡

`PASS` 候補を得たら、次の順で確定する。どこかで失敗したら `PASS` ではなく `ABORT`。

1. `pulls/{n}` を再取得して head が固定 head のままであることを確認する（判定直前）
2. `pulls/{n}/commits` で短縮 SHA に一致する commit がちょうど 1 件かつ head であることを確認する
3. `pulls/{n}` をもう一度取得して head を確認する（判定直後）
4. 証跡を検証して `dirname(KAJI_VERDICT_PATH)/review-poll-evidence.json` へ atomic write する
5. その後で `PASS` verdict を stdout へ出力する（`verdict.yaml` が PASS なら同じ attempt に証跡が必ずある）

`PASS` 以外では証跡を書かない。証跡は schema v1 の JSON（`kaji_harness/review_poll_evidence.py` の
`ReviewPollEvidence`）で、`schema_version` / `kind` / `result` / `provider` / `repository` /
`pull_request`（番号・URL）/ `reviewed_head`（40 桁 SHA・commit 時刻）/ `approval`（bot identity・
`+1` reaction の ID と時刻・summary comment の ID / URL / 完了時刻）/ `checks` / `fetched_at` /
`decided_at` を持つ。整合不変条件 I1〜I9 は producer（Pydantic）と consumer（`issue-close` の jq）の
共通契約で、正本は `docs/ARCHITECTURE.md` の「review-poll の承認証跡」節。

PASS 確定後に head が動く隙間は、`issue-close` が証跡を現在の PR と照合し、
`--match-head-commit` 付きで merge することで閉じる。

## 運用パラメータ

`kaji_harness/scripts/codex_review_poll.py` の定数:

| 名前 | 値 | 用途 |
|------|-----|------|
| `POLL_INTERVAL_SEC` | 10 | GitHub API 呼び出し間隔 |
| `NO_REACTION_TIMEOUT_SEC` | 60 | bot reaction 無しのまま経過 → `BACK_FALLBACK` |
| `IN_PROGRESS_TIMEOUT_SEC` | 1800 | `eyes` 観測後の全体 cap → `ABORT` |
| `EYES_GRACE_SEC` | 10 | `eyes` 消失後の伝搬待ち |

## Verdict 出力

entry module / polling 本体が `---VERDICT---` ブロックを stdout に出力する:

| status | 条件 |
|--------|------|
| PASS | summary comment（現在 head で `Completed`）の後に付いた bot `+1` を観測し、確定確認と証跡保存を完了 |
| RETRY | 現在 head に対する bot COMMENTED review を観測 |
| BACK_FALLBACK | timeout までいずれも観測されず → `review` step に fallback |
| ABORT | provider mismatch / PR 未解決 / head 情報欠落 / remote url parse 失敗 / GitHub API 連続失敗 / IN_PROGRESS_TIMEOUT 超過 / `head changed` / `SHA mapping ambiguous` / `evidence unavailable` |

PASS の `evidence` には人間向けの補助として `pr=<owner>/<repo>#<n>` / `head_sha=<40桁>` / `reaction_id=<id>` /
`summary_comment=<url>` / `evidence_path=<絶対パス>` を含める。機械照合の正本は JSON 証跡である。

deterministic script のため verdict 出力後は **必ず `return 0`** で終了する。catastrophic 失敗
（`gh` CLI 不在 / 通信不能等）は raise させ、harness が `ScriptExecutionError` で fail-loud
扱いとする（Issue #204 設計書 § exit code と verdict の優先順位）。

> **規約**: 本 skill 出力に auto-close hazard pattern（`Clos(e[sd]?|ing)` /
> `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を
> 書かない。
