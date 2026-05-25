# [設計] review step を codex auto-review 絵文字 polling 方式に置き換える

Issue: #182

## 概要

`.kaji/wf/review-close.yaml` / `.kaji/wf/review-cycle.yaml` の `review` step を、新規 `review-poll` skill による **codex auto-review の絵文字 polling 方式** に置き換える。auto-review が走らない場合は `BACK_FALLBACK` verdict 経由で既存 `review` skill（codex agent による能動レビュー）へ fallback する。

## 背景・目的

### ユースケース

- **kaji ユーザーとして**、PR 作成済み Issue に対して `kaji run .kaji/wf/review-close.yaml <issue>` または `/review-cycle <issue>` を実行する。**codex の GitHub auto-review が走っている前提**で、kaji 側は絵文字 / review コメントを polling して結論を受け取りたい。kaji 側で `/review` を二重起動してクレジットを浪費したくない。
- **kaji ユーザーとして**、auto-review クレジット不足等で auto-review が起動しない場合にも、一定時間待った後に **ローカル `codex` CLI 経由の `/review` skill** に fallback して workflow が詰まらないようにしたい（auto-review と ローカル `codex` CLI は別クレジット供給源、Issue #182 コメント 1 で確認済）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| A. 現状維持（毎回 `/review` を能動実行） | 二重実行によるクレジット浪費。Issue 本文「現状の問題」1〜2 で記述 |
| B. polling と fallback を 1 つの skill 内に閉じる（fallback 時に skill が `codex` CLI を直接起動） | agent 切替が harness 外に出る → kaji の責務分離（workflow が agent / model を制御）と矛盾。保守性も悪化（model / effort / sandbox flag を skill bash で再現する必要） |
| C. review skill 自体を分岐対応にする（既存 1 skill で polling + 能動レビュー） | 観点（監視 vs 能動レビュー）が異なる責務を 1 skill に混在。テスト戦略の S/M/L マッピングも複雑化 |
| D. **新規 `review-poll` skill + 既存 `review` skill を fallback step として再利用**（採用） | YAML 仕様拡張なし（`BACK_*` プレフィックスを利用）。agent 切替を workflow YAML 側で表現可能（review-poll は claude / review は codex） |

### 採用構造（workflow YAML 概要）

```yaml
steps:
  - id: review-poll        # 新規: codex auto-review の polling
    skill: review-poll
    agent: claude
    on:
      PASS: close            # 👍 検出
      RETRY: pr-fix          # COMMENTED review 検出
      BACK_FALLBACK: review  # auto-review 不在 → fallback
      ABORT: end

  - id: review             # 既存 skill を fallback として再利用
    skill: review
    agent: codex
    on:
      PASS: close
      RETRY: pr-fix
      ABORT: end

  - id: pr-fix / pr-verify / close  # 現行構造を維持
```

`BACK_FALLBACK` の semantic 定義（本 workflow ローカル）: 「polling では結論が出せないため代替経路 (`review` step) に差し戻す」。kaji workflow 仕様 (`docs/dev/workflow-authoring.md` § BACK_* プレフィックス拡張) で `BACK_*` の suffix 意味は **workflow 設計者が定義可能** と明記されている。本 Issue ではこの拡張点を利用し、新規 verdict status の導入は行わない。

## インターフェース

### 1. 新規 skill: `review-poll`

**入力（context 変数）**:

| 変数 | 型 | 必須 | 用途 |
|------|-----|------|------|
| `issue_id` | str | ✅ | PR 解決の検索キー |
| `issue_ref` | str | ✅ | コメント本文 |
| `provider_type` | str | ✅ | Step 0 ガード。`github` 以外は ABORT |
| `git_remote` | str | ✅ | PR の owner/repo 解決 |
| `default_branch` | str | ✅ | branch fallback |

**出力（verdict）**:

| status | 条件 |
|--------|------|
| `PASS` | bot による `+1` reaction を観測 |
| `RETRY` | bot による新規 COMMENTED review (`### 💡 Codex Review` 始まり) を観測 |
| `BACK_FALLBACK` | timeout までいずれの完了シグナルも観測されず |
| `ABORT` | provider mismatch / PR 未解決 / GitHub API 連続エラー |

**運用パラメータ（skill 内で固定値、定数として宣言）**:

| 名前 | 値（推奨） | 用途 |
|------|-----------|------|
| `POLL_INTERVAL_SEC` | `10` | `gh api ... /reactions` の呼び出し間隔 |
| `NO_REACTION_TIMEOUT_SEC` | `60` | bot reaction（`eyes` / `+1`）も bot review も観測されないまま経過した時間 → `BACK_FALLBACK` |
| `IN_PROGRESS_TIMEOUT_SEC` | `1800` (30 min) | `eyes` を観測した後、結論（`+1` / COMMENTED review）が出るまでの全体 cap → `ABORT`（codex hang を想定） |
| `EYES_GRACE_SEC` | `10` | `eyes` 消失後の race 緩衝（`+1` reaction / COMMENTED review の伝搬待ち） |

Issue 本文記載の「30 秒」は user 経験則 (#182 コメント 1 で訂正済) のため、設計段階で 60 秒に再評価。実環境フィードバックを踏まえて将来再調整可能。

### 2. 使用例

```bash
# 1. PR 作成後の典型シナリオ（auto-review が走るケース）
$ kaji run .kaji/wf/review-cycle.yaml 182
# review-poll: eyes 観測 → poll 継続 → +1 観測 → PASS
# workflow PASS → user が手動で /issue-close 182

# 2. auto-review が走らないケース（クレジット不足）
$ kaji run .kaji/wf/review-close.yaml 182
# review-poll: 60 秒経過 eyes 無し → BACK_FALLBACK
# review (codex agent): /review 能動実行 → PASS → close
```

### 3. polling 検出ロジック（仕様）

各 poll 単位で以下 2 つの API を呼ぶ:

1. `gh api repos/{owner}/{repo}/issues/{pr_number}/reactions` — 現在の reactions 一覧
2. `gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews` — review 履歴（state / body / submitted_at / user.login）

**事前取得（skill 起動時に 1 回だけ実施）**:

- `head_sha` ← `gh pr view <pr_number> --json headRefOid --jq .headRefOid` で workflow 起動時点の PR head commit SHA を取得し、polling loop 全体で固定値として保持する
- `head_committed_at` ← `gh pr view <pr_number> --json commits --jq '.commits[-1].committedDate'` で head commit の committedDate (ISO8601 UTC) を取得し、`+1` reaction の freshness 判定に使う（後述 Must Fix 1 対応）

各 poll での状態判定:

| 観測 | 判定 |
|------|------|
| reactions に bot の `eyes` あり | レビュー進行中 → polling 継続（`IN_PROGRESS_TIMEOUT_SEC` cap） |
| reactions に bot の `+1` あり、かつ `+1.created_at >= head_committed_at` | **PASS** → close |
| reviews に bot からの COMMENTED review (body は `body.lstrip().startswith("### 💡 Codex Review")` で判定) があり、かつ `commit_id == head_sha` | **RETRY** → pr-fix |
| いずれも無し（NO_REACTION_TIMEOUT_SEC 経過） | **BACK_FALLBACK** → review（suggestion 必須、後述 § verdict 出力 参照） |

> **完了済み COMMENTED review の検出**: GitHub Pull Request Reviews API は **履歴を返す**（reactions API と違い現在値限定ではない）。workflow 起動前に bot の auto-review が完了して COMMENTED review が投稿済みのケース（reactions は 0 件、reviews API 側に bot review がある状態。PR #176 で実観測）でも、`commit_id == head_sha` を満たす bot review が存在すれば **`RETRY` として扱う**。これにより既存 Codex 指摘を活かして `pr-fix` に直行でき、`/review` 二重起動を回避する。古い commit に対する stale review は `commit_id != head_sha` で自然に除外される。

> **body 判定の lstrip**: 実観測 PR #176 の bot review body は先頭に改行を含む（`\n### 💡 Codex Review\n...`）。`body.startswith(...)` のみだと検出漏れになるため、**`body.lstrip().startswith("### 💡 Codex Review")`** を仕様とする。fixture / small test には先頭改行ありケースを含める。

> **bot 識別**: `chatgpt-codex-connector[bot]` (id `199175422`)。GET reactions / reviews のレスポンス `.user.login` / `.user.id` で identifier 一致を確認する。`login` だけでは prefix `[bot]` の有無差異で誤検出するリスクがあるため、**`id` 一致**を主、`login` を副チェックにする。

> **`+1` reaction の freshness guard (Must Fix 1 対応)**: GitHub Reactions API は issue-level の `content` / `created_at` / `user` のみ返し、PR head commit に紐付く `commit_id` を持たない（一次情報: https://docs.github.com/en/rest/reactions/reactions#list-reactions-for-an-issue）。そのため bot `+1` reaction が存在するだけでは「現在 head への承認」か「過去 head の `+1` が残っているだけ」かを区別できない。`review-close.yaml` では `PASS: close` 直結のため、stale `+1` を拾うと未レビュー head を close する false positive リスクがある。本設計では **bot `+1.created_at >= head_committed_at` を満たす場合のみ PASS** とする freshness guard を必須とする。`head_committed_at` は事前取得で固定し、polling loop 全体で再利用する。実観測 PR #181 では `+1.created_at` (`2026-05-24T08:25:28Z`) > head committedDate (`2026-05-24T08:05:07Z`) でこのガードを通過する。

> **eyes 消失後の race**: `IN_PROGRESS` 状態で `eyes` が消えた直後の 1 poll では結論シグナル（`+1` or COMMENTED review）が GitHub 側に未伝搬の可能性がある。`EYES_GRACE_SEC` (10秒) だけ追加で待ってから再判定する。

### 4. エラーケース

| 状況 | skill の挙動 |
|------|--------------|
| `gh api` が連続 3 回 4xx / 5xx を返す | ABORT verdict（reason: GitHub API error） |
| PR が解決できない（`kaji pr list --search` で 0 件 + branch fallback も 0 件） | ABORT（reason: no open PR for issue） |
| `provider_type` が `github` 以外 | Step 0 で ABORT（reason: provider mismatch、suggestion: 既存 `/review` skill 直接起動 or skip） |
| 過去 commit に対する stale な bot review しか存在しない（`commit_id != head_sha`） | RETRY を返さない（`commit_id == head_sha` フィルタで自然除外）。`eyes` または現在 head 向け新規 review を待つ |
| 現在 head に対する bot COMMENTED review が workflow 起動前から存在（PR #176 シナリオ） | 初回 poll で **RETRY** を即時返却（reactions が 0 件でも reviews API 側で検出可能） |
| 過去 head の bot `+1` reaction だけが残っている（`+1.created_at < head_committed_at`） | PASS を返さず無視（freshness guard）。`eyes` / 現在 head 向け新規 review / 新しい `+1` を待つ。timeout で `BACK_FALLBACK` |

### 5. 既存 review skill (`review`) の扱い

- skill 本体（実行ロジック）は **変更しない**（agent: codex で `/review` 能動実行を続ける）
- フロー上の位置づけのみ変更（auto-review fallback step として呼ばれる）
- 説明文（`description` / "いつ使うか" 表）に「fallback 用途」の文言を追記する

### 6. /review-cycle slash command の挙動

- `review-cycle.yaml` を起動するだけなので skill 内ロジックは不変
- skill 説明の「いつ使うか」表に **「codex auto-review が走っている GitHub 環境」が前提** であることを追記
- exit code → verdict マッピングは不変

## 制約・前提条件

### 技術制約

- **provider = github 限定**: `requires_provider: github` を `review-close.yaml` / `review-cycle.yaml` に明示する。codex auto-review は GitHub App として実装されており、GitLab には現状未対応。`provider.type='gitlab'` 配下で本 workflow を起動すると workflow load 時に exit 2 で fail-fast（既存ガードを利用）
- **bot identifier**: `chatgpt-codex-connector[bot]` (id `199175422`) を skill 内定数として保持。将来 bot rename / re-deploy で id が変わった場合は skill 側を更新する必要あり（影響を `## 影響ドキュメント` に明記）
- **GitHub Reactions API は現在の reactions のみ返す**: 履歴は取得不可。完了済み PR を polling 開始時から再走査しても `eyes` は観測できない（既に削除済み）。本 skill は **常にレビュー進行中のリアルタイム監視** が前提
- **`gh api` 認証**: 既存 `kaji pr` 系コマンドと同じ `gh auth` を流用。新規認証セットアップ不要

### 性能制約

- `POLL_INTERVAL_SEC = 10` で `IN_PROGRESS_TIMEOUT_SEC = 1800` cap → 最大 180 API 呼び出し/run。GitHub primary rate limit (5000 req/h for authenticated user) に対し十分余裕
- `gh api` 1 回あたり 100ms オーダー → poll 1 周あたり 200ms 程度

### 既存機能との互換性

- `pr-fix` / `pr-verify` skill 本体は変更しない（Issue 本文スコープ境界）
- workflow YAML の `cycles` 定義 (`loop: [pr-fix, pr-verify]`) は不変
- 既存 `review` skill の `Step 0: provider check` 仕様は不変（fallback で再利用するため）

## 変更スコープ

### 新規

- `.claude/skills/review-poll/SKILL.md` — polling skill 本体（bash 実装、Python helper を `python -m` で呼ぶ薄い wrapper）
- `kaji_harness/scripts/__init__.py` — module 初期化（既存無しの場合）
- `kaji_harness/scripts/codex_review_poll.py` — Python helper（`classify()` / `run_polling()` / 定数）
- `tests/test_codex_review_poll.py`（small / medium） — `classify()` / `run_polling()` の検証
- `tests/fixtures/codex_review_poll/` — 過去 PR (#181 / #176) の reactions / reviews JSON 固定 fixture

### 変更

- `.kaji/wf/review-close.yaml` — `review` step を `review-poll` + fallback `review` の 2 段構成に。`requires_provider: github` 明示
- `.kaji/wf/review-cycle.yaml` — 同上
- `.claude/skills/review/SKILL.md` — fallback 用途であることを description / "いつ使うか" 表に追記
- `.claude/skills/review-cycle/SKILL.md` — auto-review 前提を "いつ使うか" 表に追記
- `docs/dev/workflow_guide.md` — review-close / review-cycle の step 構造説明を更新

### 不変

- `.claude/skills/pr-fix/SKILL.md` / `.claude/skills/pr-verify/SKILL.md`
- 既存 `kaji_harness/` の Python コード（skill validator / runner / verdict parser 等）
- `workflow-authoring.md` の `BACK_*` 仕様（既に拡張可能）

## 方針（Minimal How）

### 実装言語と分担

skill 本体は **bash 主体** で書く（既存 `review-cycle` / `review` skill と同様の流れ）。ただし `gh api` レスポンスの JSON パース、bot identifier 一致判定、freshness guard 計算、state machine 駆動は **`python -m kaji_harness.scripts.codex_review_poll` で 1 回呼び出す Python helper** に集約して、`jq` 依存と shell quoting hell を避ける。

理由: bash + jq で `eyes` reaction の bot id フィルタ + 経過時間判定 + ISO8601 timestamp 比較 + state machine を表現すると可読性が落ち、テストも書きにくい。Python helper として `kaji_harness/scripts/codex_review_poll.py` に置けば `tests/test_codex_review_poll.py` から直接 import して `classify()` / `run_polling()` を pytest で検証できる（詳細は後述「Python helper の責務と配置」節）。

### state machine

`head_sha` と `head_committed_at` は skill 起動時に 1 回 fetch して固定。各 poll での判定は `(reactions, reviews, head_sha, head_committed_at, bot_id)` のスナップショットに対して `classify()` を呼ぶ。

```
START
  └─ fetch head_sha + head_committed_at → INIT

INIT
  ├─ poll() → 現在 head 向け bot COMMENTED review 検出 → DONE_RETRY
  ├─ poll() → fresh +1 (created_at >= head_committed_at) 検出 → DONE_PASS
  ├─ poll() → eyes 検出 → IN_PROGRESS
  └─ elapsed > NO_REACTION_TIMEOUT_SEC → DONE_FALLBACK

IN_PROGRESS
  ├─ poll() → 現在 head 向け bot COMMENTED review 検出 → DONE_RETRY
  ├─ poll() → fresh +1 (created_at >= head_committed_at) 検出 → DONE_PASS
  ├─ poll() → eyes 消失 → wait(EYES_GRACE_SEC) → 再 poll
  └─ elapsed > IN_PROGRESS_TIMEOUT_SEC → DONE_ABORT

DONE_* → verdict 出力して exit
```

> stale `+1` (`created_at < head_committed_at`) は `INIT` / `IN_PROGRESS` のどちらでも DONE_PASS を返さず state 不変。eyes / 新規 review / fresh `+1` を待つ。

> **判定順序の注意**: `INIT` / `IN_PROGRESS` ともに **COMMENTED review (RETRY) を `+1` (PASS) よりも先に評価する**。bot が一度 COMMENTED review を投稿した後に同 commit へ追加で `+1` を付けることは仕様上想定されないが、過去 commit の `+1` が残っている可能性 (stale reaction) を排除するため、結論として強い「指摘あり」を優先する。

### Python helper の責務と配置

- 配置: **`kaji_harness/scripts/codex_review_poll.py`** に単一配置（repo 内 Python module として import 可能）
- 起動: skill bash から `python -m kaji_harness.scripts.codex_review_poll` で呼び出す
- テスト: `tests/test_codex_review_poll.py` から直接 import して `classify()` を pytest で検証

`.claude/skills/review-poll/` 配下に Python ファイルを置く案は **不採用**。理由: `review-poll` ディレクトリ名にハイフンを含むため `python -m` の module path にできない / `python "$SKILL_DIR/_poll.py"` 直接実行ではテストとの import path が一致しないため。

```python
# kaji_harness/scripts/codex_review_poll.py
from dataclasses import dataclass
from typing import Literal

BOT_ID = 199175422
BOT_LOGIN_PREFIX = "chatgpt-codex-connector"  # "[bot]" suffix 差異を吸収
CODEX_REVIEW_BODY_MARKER = "### 💡 Codex Review"


@dataclass(frozen=True)
class PollResult:
    state: Literal[
        "init", "in_progress", "done_pass", "done_retry",
        "done_fallback", "done_abort",
    ]
    reason: str


def classify(
    reactions_json: list[dict],
    reviews_json: list[dict],
    head_sha: str,
    head_committed_at: str,  # ISO8601 UTC, freshness guard 用
    bot_id: int = BOT_ID,
    prev_state: Literal["init", "in_progress"] = "init",
) -> PollResult:
    """1 poll 分の状態判定。
    - reviews は `commit_id == head_sha` でフィルタし、body は
      `lstrip().startswith(CODEX_REVIEW_BODY_MARKER)` で判定する。
    - `+1` reaction は `created_at >= head_committed_at` を満たす場合のみ
      `done_pass` を返す（freshness guard。stale +1 は state 不変）。"""


def run_polling(
    pr_number: int, owner: str, repo: str,
    head_sha: str, head_committed_at: str, *,
    poll_interval_sec: int = 10,
    no_reaction_timeout_sec: int = 60,
    in_progress_timeout_sec: int = 1800,
    eyes_grace_sec: int = 10,
) -> PollResult:
    """state machine driver。`gh api` を subprocess で呼んで classify() に流す。"""
```

skill bash は Python helper を `python -m kaji_harness.scripts.codex_review_poll` で起動し、stdout の verdict YAML を直接 stdout に転送する。

### verdict 出力

state machine の終端 `DONE_*` から skill stdout に kaji 標準の `---VERDICT--- ... ---END_VERDICT---` ブロックを出す。`BACK_*` プレフィックス verdict は `kaji_harness/verdict.py` および `docs/dev/workflow-authoring.md` § BACK_* 拡張により **non-empty `suggestion` 必須**。本 skill では以下マッピングで `emit_verdict()` を呼ぶ:

| 終端 state | status | reason 例 | suggestion 例 |
|------------|--------|-----------|---------------|
| `done_pass` | `PASS` | `bot +1 reaction (fresh, created_at >= head_committed_at) を検出` | (任意) |
| `done_retry` | `RETRY` | `bot COMMENTED review (commit_id == head_sha) を検出` | (任意) |
| `done_fallback` | `BACK_FALLBACK` | `auto-review シグナル未検出 (NO_REACTION_TIMEOUT_SEC=60 経過)` | **必須**: `kaji_harness/scripts/codex_review_poll.py の NO_REACTION_TIMEOUT_SEC を超えても eyes / fresh +1 / 現在 head 向け COMMENTED review が観測できないため、review skill (codex agent) で能動レビューに切替えてください` |
| `done_abort` | `ABORT` | `gh api 連続失敗` / `IN_PROGRESS_TIMEOUT_SEC 経過` 等 | **必須**: 原因に応じて `gh auth status` 確認 / codex bot 状態確認 等の具体的アクション |

`BACK_FALLBACK` の suggestion 必須要件は `kaji_harness/verdict.py` の parser が enforce する。skill 側で空の suggestion を返すと workflow runner が `VerdictNotFound` 相当で fail-fast するため、上記マッピングで定数として保持する。

### PR 解決 / Worktree 解決

既存 `review` skill (`SKILL.md` Step 1 / Step 2) と同じ手順:
- PR 解決: `kaji pr list --search [issue_id]` → fallback `kaji pr list --head [branch_name]`
- Worktree: `_shared/worktree-resolve.md` 参照

## テスト戦略

### 変更タイプ

実行時コード変更（新規 skill + Python helper + workflow YAML 改修）。

### Small テスト

`kaji_harness.scripts.codex_review_poll.classify()` を直接呼ぶ pytest:

- `+1` reaction のみ存在、`+1.created_at >= head_committed_at` → `done_pass`
- `+1` reaction のみ存在、ただし `+1.created_at < head_committed_at` (過去 head の stale `+1`) → state 不変（**Must Fix 1 対応 / freshness guard 検証**）
- `+1` reaction が head 後の timestamp で複数（古い stale + 新規 fresh が混在） → `done_pass`（fresh な `+1` を優先）
- `eyes` のみ → `in_progress`
- `eyes` 消失 + `+1` あり → `done_pass`
- 現在 head 向け bot COMMENTED review (`### 💡 Codex Review` 始まり、`commit_id == head_sha`、reactions は 0 件) → `done_retry`（**PR #176 シナリオ。Must Fix 1 対応**）
- 現在 head 向け bot COMMENTED review、ただし body 先頭に改行あり (`\n### 💡 Codex Review\n...`) → `done_retry`（**Must Fix 2 対応**）
- 過去 commit に対する bot COMMENTED review (`commit_id != head_sha`) → 無視（state 不変）
- bot COMMENTED review と `+1` reaction が同時に存在 → `done_retry`（RETRY 優先順序の検証）
- bot id 不一致の reaction（apokamo 等の human） → 無視
- bot login 一致 + id 不一致（rename / re-deploy 想定） → 無視（id 優先）
- 空レスポンス → `init` 維持
- bot からの reaction が `+1` でも `eyes` でもない（例: `heart`） → 無視

### Medium テスト

`kaji_harness.scripts.codex_review_poll.run_polling()` を subprocess monkeypatch で `gh api` をモック化した結合テスト:

- timeout シナリオ: `gh api` が常に空レスポンスを返す環境 → `NO_REACTION_TIMEOUT_SEC` 経過で `done_fallback`
- stale `+1` のみ存在シナリオ: `+1.created_at < head_committed_at` の reaction だけ → freshness guard で無視、`NO_REACTION_TIMEOUT_SEC` 経過で `done_fallback`（**Must Fix 1 対応**）
- 起動時 fresh `+1` シナリオ: 初回 poll で `+1.created_at >= head_committed_at` → `done_pass`（PR #181 シナリオ）
- in-progress → pass シナリオ: 最初 N 回は `eyes` 返却、その後 `+1` → `done_pass`
- in-progress → retry シナリオ: 最初 N 回は `eyes` 返却、その後現在 head 向け COMMENTED review → `done_retry`
- 起動時即 retry シナリオ: 初回 poll で reactions 0 件 + 現在 head 向け COMMENTED review あり → `done_retry`（**PR #176 シナリオ**）
- API 連続失敗: 3 回連続 4xx/5xx → `done_abort`
- `IN_PROGRESS_TIMEOUT_SEC` cap: ずっと `eyes` のまま → `done_abort`（codex hang シミュレーション）

### Large テスト

`@pytest.mark.large_forge` で実 GitHub PR を作成して検証する経路は **テスト用 PR の自動生成コストが高い**（既存テストにも `large_forge` E2E は無し）ため、本 Issue では追加しない。代替として:

- **manual 検証**: 本 Issue の PR 自体で実環境動作確認（PR 作成 → `kaji run .kaji/wf/review-close.yaml 182` → polling → close）。`/i-dev-final-check` の手動証跡欄に記録
- **過去 PR 観測**: 既存 PR (#181 / #176 / #173 等) の reactions / reviews JSON を fixture として固定化し、small/medium テストで再生（実 API call は不要）

恒久 E2E を追加しない理由（`docs/dev/testing-convention.md` の 4 条件）:
1. 独自ロジックの追加は `classify()` / `run_polling()` に集中 → small + medium で充分カバー
2. 想定される不具合パターン（bot 識別、state 遷移、timeout）は small/medium で全分岐網羅
3. 実 API E2E を恒久化しても、`+1` / COMMENTED review を 100% 再現する PR 生成が不安定 → 回帰検出情報がノイジー
4. 上記理由を design / implement / review で説明可能

ただし将来 codex bot id / API レスポンス形式が変わったら fixture の更新が必要 → fixture 取得手順を skill 内 docstring に明記する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし（`gh api` は既存スタック内、Python helper は既存方針内） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ層に変更なし（skill 追加のみ） |
| `docs/dev/workflow_guide.md` | **あり** | `review-close.yaml` / `review-cycle.yaml` の step 構造変更を反映 |
| `docs/dev/workflow_overview.md` | なし | 全体俯瞰には影響なし |
| `docs/dev/workflow-authoring.md` | なし | `BACK_*` 拡張点を使うが、仕様自体は不変。実例として `BACK_FALLBACK` の使用例を追記するかは review-design で判断 |
| `docs/dev/development_workflow.md` | なし | dev workflow (`feature-development.yaml`) には影響なし |
| `docs/dev/testing-convention.md` | なし | テスト規約に変更なし |
| `docs/reference/` | なし | Python 規約に変更なし |
| `docs/cli-guides/` | なし | CLI 仕様に変更なし |
| `CLAUDE.md` | なし | 既存規約に追加なし |
| `.claude/skills/review/SKILL.md` | **あり** | fallback 用途追記（description / "いつ使うか" 表） |
| `.claude/skills/review-cycle/SKILL.md` | **あり** | "auto-review 前提" を "いつ使うか" 表に追記 |
| `.kaji/wf/review-close.yaml` | **あり** | step 追加・置換、`requires_provider: github` 明示 |
| `.kaji/wf/review-cycle.yaml` | **あり** | 同上 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| OpenAI Codex GitHub integration | https://developers.openai.com/codex/integrations/github | "Wait for Codex to react (👀) and post a review" — auto-review 起動時に `eyes` reaction が付き、完了時にレビューを post すること、および 👀 は完了で削除される旨 |
| openai/codex#3808 | https://github.com/openai/codex/issues/3808 | "the looking gets removed and an actual code review happens" — 完了で 👀 が削除される挙動。クレジット不足で auto-review がスキップされるケースの user 報告 |
| GitHub REST API: List reactions for an issue | https://docs.github.com/en/rest/reactions/reactions#list-reactions-for-an-issue | `GET /repos/{owner}/{repo}/issues/{issue_number}/reactions` — 現在の reactions のみ返却（履歴なし）。`content` フィールドに `+1` / `eyes` 等の reaction 種別 |
| GitHub REST API: List reviews for a pull request | https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request | `GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews` — `state` (`APPROVED` / `COMMENTED` / `CHANGES_REQUESTED` / `DISMISSED`) と `submitted_at`、`user.login` / `user.id` を返却。`body` でレビュー本文を判定可能 |
| 実観測 PR (#181) | https://github.com/apokamo/kaji/pull/181 reactions API + pr view レスポンス | bot id `199175422`, login `chatgpt-codex-connector[bot]`, content `+1`, `created_at = 2026-05-24T08:25:28Z` を実観測。同 PR head commit (`3155f82...`) の `committedDate = 2026-05-24T08:05:07Z`。`+1.created_at > head_committed_at` で freshness guard を通過する裏付け（Must Fix 1 対応） |
| GitHub REST API: Get a pull request | https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request | `gh pr view --json commits` の `commits[].committedDate` (ISO8601 UTC) で head commit timestamp を取得可能。`+1` reaction の freshness guard の基準値として使用 |
| 実観測 PR (#176) | https://github.com/apokamo/kaji/pull/176 reviews API レスポンス | bot による COMMENTED review、body は **先頭改行ありの `\n### 💡 Codex Review\n...`**（lstrip 必須）。bot author login `chatgpt-codex-connector` を実観測。reviews API は履歴を返すため、workflow 起動前に完了済の review でも `commit_id == head_sha` で検出可能。同 PR の issue reactions は 0 件（`gh api repos/apokamo/kaji/issues/176/reactions --jq 'length'` → `0`） |
| Issue #182 コメント 1（仕様確認結果） | `kaji issue view 182 --comments` 抜粋 | 本文の「絵文字なし → 30 秒待機」「眼の絵文字を polling に使わない」両方の訂正、bot identifier の固定、auto-review と ローカル `codex` CLI のクレジット供給源分離 |
| `docs/dev/workflow-authoring.md` § BACK_* プレフィックス拡張 | `docs/dev/workflow-authoring.md:137-152` | "`BACK_*` の suffix は uppercase 英数字 + アンダースコア (`[A-Z0-9_]+`) に限定" / "suffix の意味は workflow 設計者が定義" — `BACK_FALLBACK` の YAML 採用可否の根拠 |
| `.kaji/wf/review-close.yaml` / `review-cycle.yaml`（現行） | `.kaji/wf/review-close.yaml:16-24` / `.kaji/wf/review-cycle.yaml:16-23` | 改修対象の step 定義。現行は `agent: codex` で `review` skill を能動起動する構造 |
