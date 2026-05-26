---
description: codex auto-review (chatgpt-codex-connector[bot]) の reactions / reviews を polling して PASS / RETRY / BACK_FALLBACK を判定する。GitHub 限定。
name: review-poll
---

# Review Poll

GitHub `chatgpt-codex-connector[bot]` (id `199175422`) の auto-review シグナルを polling し、
verdict を出力する skill。`review` skill との二重起動を避け、auto-review クレジット不足時のみ
`BACK_FALLBACK` 経由で既存 `review` skill (codex agent) に fallback させる。

実装は **`kaji_harness.scripts.codex_review_poll`** に集約しており、本 skill は薄い bash
wrapper として PR 情報 / head SHA を解決して Python helper を起動する。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| PR 作成後、codex auto-review が走っている GitHub 環境 | ✅ 必須 |
| `provider.type='local'` 配下 | ❌ Step 0 で ABORT |
| `review-poll` で `BACK_FALLBACK` を受けた場合 | 既存 `review` skill (codex agent) に進む |

**ワークフロー内の位置**: i-pr → [PR 作成] → **review-poll** → (PASS=close / RETRY=pr-fix / BACK_FALLBACK=review fallback)

## 入力（context 変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | PR 解決の検索キー |
| `issue_ref` | str | 人間可読の Issue 参照（コメント用） |
| `provider_type` | str | `github` 必須。それ以外は ABORT |
| `git_remote` | str | PR の owner/repo 解決 |
| `default_branch` | str | branch fallback |

## 実行手順

### Step 0: provider check

```bash
PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
if [ "$PROVIDER_TYPE" != "github" ]; then
    cat <<'VERDICT_EOF'
---VERDICT---
status: ABORT
reason: |
  review-poll requires provider.type='github' (codex auto-review is GitHub-only).
evidence: |
  provider_type was not 'github'.
suggestion: |
  Run /review directly instead, or set provider.type to github.
---END_VERDICT---
VERDICT_EOF
    exit 0
fi
```

### Step 1: PR の解決と head 情報の取得

`review` skill Step 1 と同型に PR を解決し、加えて **head commit の committedDate** を取得する
（`+1` reaction の freshness guard 用、polling loop 全体で固定値として利用）。

```bash
PR_JSON=$(kaji pr list --search "[issue_id]" --json number,headRefName,headRefOid --jq '.[0]')
pr_id=$(echo "$PR_JSON" | jq -r '.number')
head_sha=$(echo "$PR_JSON" | jq -r '.headRefOid')
head_committed_at=$(kaji pr view "$pr_id" --json commits --jq '.commits[-1].committedDate')
```

`headRefOid` が空文字 / null の場合は `kaji pr view <pr_id> --json headRefOid --jq .headRefOid` で
明示再取得する。`head_committed_at` が空の場合も同様に ABORT。両経路で PR が解決できない場合は
ABORT。

### Step 2: Worktree パスの解決

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従う。

### Step 3: owner / repo の解決

```bash
ORIGIN=$(cd "[worktree_dir]" && git remote get-url "[git_remote]")
# git@github.com:owner/repo.git or https://github.com/owner/repo[.git]
OWNER=$(echo "$ORIGIN" | sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#')
REPO=$(echo "$ORIGIN" | sed -E 's#.*[:/][^/]+/([^/]+?)(\.git)?$#\1#')
```

### Step 4: polling 起動

```bash
cd "[worktree_dir]" && source .venv/bin/activate
python -m kaji_harness.scripts.codex_review_poll \
    --pr "$pr_id" \
    --owner "$OWNER" \
    --repo "$REPO" \
    --head-sha "$head_sha" \
    --head-committed-at "$head_committed_at"
```

Python helper が verdict ブロックを stdout に出力する。skill は exit code を観察せず stdout
をそのまま流す。

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

Python helper が `---VERDICT---` ブロックを stdout に出力する:

| status | 条件 |
|--------|------|
| PASS | bot `+1` reaction を観測 |
| RETRY | 現在 head に対する bot COMMENTED review を観測 |
| BACK_FALLBACK | timeout までいずれも観測されず → `review` step に fallback |
| ABORT | provider mismatch / PR 未解決 / GitHub API 連続失敗 / IN_PROGRESS_TIMEOUT 超過 |

> **規約**: 本 skill 出力に auto-close hazard pattern（`Clos(e[sd]?|ing)` /
> `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を
> 書かない。
