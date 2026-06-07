---
description: codex auto-review (chatgpt-codex-connector[bot]) の reactions / reviews を polling して PASS / RETRY / BACK_FALLBACK を判定する。GitHub 限定。
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

- **builtin workflow（`review-cycle` / `review-close` / `full-cycle` / `full-cycle-xhigh`）**:
  各 YAML が `exec: [uv, run, --no-sync, python, -m, kaji_harness.scripts.review_poll_entry]` で
  entry module を直接起動する。dispatch の正本は YAML 側の `exec` step であり、agent / model /
  effort はスキーマレベルで指定不可（指定すると `kaji validate` が reject）。
  `uv` が PATH にある前提で起動し、`.venv` の鮮度は `uv sync` の責務（本 skill は関与しない）。
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

これらの env は entry module（`review_poll_entry`）が正本として解釈する。builtin workflow の
`exec` step は `agent` / `model` / `effort` をスキーマで拒否するため、これらを指定する余地はない。

## 検出ロジック（仕様）

| 観測 | verdict |
|------|---------|
| bot による `+1` reaction かつ `+1.created_at >= head_committed_at` (freshness guard) | `PASS` |
| bot による COMMENTED review (body は `body.lstrip().startswith("### 💡 Codex Review")` で判定) かつ `commit_id == head_sha` | `RETRY` |
| `NO_REACTION_TIMEOUT_SEC` (60s) 経過しても上記いずれも観測されず（stale `+1` のみ存在も含む） | `BACK_FALLBACK` |
| `IN_PROGRESS_TIMEOUT_SEC` (1800s) 経過しても結論が出ない、または GitHub API 連続失敗 | `ABORT` |

完了済み COMMENTED review は reactions API では検出できない（reactions は現在値のみ）が、
reviews API は履歴を返すため `commit_id == head_sha` で workflow 起動前の auto-review を
検出可能（PR #176 シナリオ）。

bot 識別は **id 一致**（`199175422`）を主、login を副チェックにする。

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
| PASS | bot `+1` reaction を観測 |
| RETRY | 現在 head に対する bot COMMENTED review を観測 |
| BACK_FALLBACK | timeout までいずれも観測されず → `review` step に fallback |
| ABORT | provider mismatch / PR 未解決 / head 情報欠落 / remote url parse 失敗 / GitHub API 連続失敗 / IN_PROGRESS_TIMEOUT 超過 |

deterministic script のため verdict 出力後は **必ず `return 0`** で終了する。catastrophic 失敗
（`gh` CLI 不在 / 通信不能等）は raise させ、harness が `ScriptExecutionError` で fail-loud
扱いとする（Issue #204 設計書 § exit code と verdict の優先順位）。

> **規約**: 本 skill 出力に auto-close hazard pattern（`Clos(e[sd]?|ing)` /
> `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を
> 書かない。
