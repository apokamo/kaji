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

各 poll での状態判定:

| 観測 | 判定 |
|------|------|
| reactions に bot の `eyes` あり | レビュー進行中 → polling 継続（`IN_PROGRESS_TIMEOUT_SEC` cap） |
| reactions に bot の `+1` あり | **PASS** → close |
| reviews に bot からの新規 COMMENTED review (body が `### 💡 Codex Review` で始まる) あり、かつ `submitted_at` が skill 起動時刻以降 | **RETRY** → pr-fix |
| いずれも無し（NO_REACTION_TIMEOUT_SEC 経過） | **BACK_FALLBACK** → review |

> **bot 識別**: `chatgpt-codex-connector[bot]` (id `199175422`)。GET reactions のレスポンス `.user.login` / `.user.id` で identifier 一致を確認する。`login` だけでは prefix `[bot]` の有無差異で誤検出するリスクがあるため、**`id` 一致**を主、`login` を副チェックにする。

> **eyes 消失後の race**: `IN_PROGRESS` 状態で `eyes` が消えた直後の 1 poll では結論シグナル（`+1` or COMMENTED review）が GitHub 側に未伝搬の可能性がある。`EYES_GRACE_SEC` (10秒) だけ追加で待ってから再判定する。

### 4. エラーケース

| 状況 | skill の挙動 |
|------|--------------|
| `gh api` が連続 3 回 4xx / 5xx を返す | ABORT verdict（reason: GitHub API error） |
| PR が解決できない（`kaji pr list --search` で 0 件 + branch fallback も 0 件） | ABORT（reason: no open PR for issue） |
| `provider_type` が `github` 以外 | Step 0 で ABORT（reason: provider mismatch、suggestion: 既存 `/review` skill 直接起動 or skip） |
| skill 起動時刻以前の bot review しか存在しない（過去 PR commit 時の auto-review） | RETRY を返さない（`submitted_at >= skill 起動時刻` フィルタ）。新規シグナル待ち |

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

- `.claude/skills/review-poll/SKILL.md` — polling skill 本体（bash 実装）
- `tests/test_review_poll.py`（small / medium） — polling helper の検証
- 必要に応じて `kaji_harness/scripts/codex_review_poll.py`（Python helper、small/medium テスト可能化のため。bash で完結する場合は不要）

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

skill 本体は **bash 主体** で書く（既存 `review-cycle` / `review` skill と同様の流れ）。ただし `gh api` レスポンスの JSON パースと bot identifier 一致判定は **Python 1-liner（`python -c`）** で実行して、`jq` 依存と shell quoting hell を避ける。

理由: bash + jq で `eyes` reaction の bot id フィルタ + 経過時間判定 + state machine を表現すると可読性が落ちる。Python helper を `.claude/skills/review-poll/_poll.py` として置けば small test で直接呼べる。

### state machine

```
INIT
  ├─ poll() → eyes 検出 → IN_PROGRESS
  ├─ poll() → +1 検出 → DONE_PASS
  ├─ poll() → COMMENTED review (>= start_time) 検出 → DONE_RETRY
  └─ elapsed > NO_REACTION_TIMEOUT_SEC → DONE_FALLBACK

IN_PROGRESS
  ├─ poll() → +1 検出 → DONE_PASS
  ├─ poll() → COMMENTED review (>= start_time) 検出 → DONE_RETRY
  ├─ poll() → eyes 消失 → wait(EYES_GRACE_SEC) → 再 poll
  └─ elapsed > IN_PROGRESS_TIMEOUT_SEC → DONE_ABORT

DONE_* → verdict 出力して exit
```

### 主要関数（Python helper の責務）

```python
# .claude/skills/review-poll/_poll.py
@dataclass
class PollResult:
    state: Literal["init", "in_progress", "done_pass", "done_retry", "done_fallback", "done_abort"]
    reason: str

def classify(reactions_json: list[dict], reviews_json: list[dict], start_time: datetime, bot_id: int) -> PollResult:
    """各 poll 単位での状態判定。"""

def run_polling(pr_number: int, owner: str, repo: str, start_time: datetime, ...) -> PollResult:
    """state machine driver。gh api を subprocess で呼ぶ。"""
```

skill bash は Python helper を `python -m` で起動し、stdout の verdict YAML を直接 stdout に転送する。

### PR 解決 / Worktree 解決

既存 `review` skill (`SKILL.md` Step 1 / Step 2) と同じ手順:
- PR 解決: `kaji pr list --search [issue_id]` → fallback `kaji pr list --head [branch_name]`
- Worktree: `_shared/worktree-resolve.md` 参照

## テスト戦略

### 変更タイプ

実行時コード変更（新規 skill + Python helper + workflow YAML 改修）。

### Small テスト

`_poll.py` の `classify()` を直接呼ぶ pytest:

- `+1` reaction のみ存在 → `done_pass`
- `eyes` のみ → `in_progress`
- `eyes` 消失 + `+1` あり → `done_pass`
- `eyes` 消失 + COMMENTED review (`### 💡 Codex Review` 始まり、`submitted_at >= start_time`) → `done_retry`
- 過去 commit 由来の古い COMMENTED review (`submitted_at < start_time`) → 無視（state 不変）
- bot id 不一致の reaction（apokamo 等の human） → 無視
- bot login 一致 + id 不一致（rename / re-deploy 想定） → 無視（id 優先）
- 空レスポンス → `init` 維持
- bot からの reaction が `+1` でも `eyes` でもない（例: `heart`） → 無視

### Medium テスト

`run_polling()` を subprocess monkeypatch で `gh api` をモック化した結合テスト:

- timeout シナリオ: `gh api` が常に空レスポンスを返す環境 → `NO_REACTION_TIMEOUT_SEC` 経過で `done_fallback`
- in-progress → pass シナリオ: 最初 N 回は `eyes` 返却、その後 `+1` → `done_pass`
- in-progress → retry シナリオ: 最初 N 回は `eyes` 返却、その後 COMMENTED review → `done_retry`
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
| 実観測 PR (#181) | https://github.com/apokamo/kaji/pull/181 reactions API レスポンス | bot id `199175422`, login `chatgpt-codex-connector[bot]`, content `+1`（PASS シグナル）を実観測（本設計フェーズで gh api で確認）。設計時刻時点での挙動裏付け |
| 実観測 PR (#176) | https://github.com/apokamo/kaji/pull/176 reviews API レスポンス | bot による COMMENTED review、body 先頭が `### 💡 Codex Review`（RETRY シグナル）。bot author login `chatgpt-codex-connector` を実観測 |
| Issue #182 コメント 1（仕様確認結果） | `kaji issue view 182 --comments` 抜粋 | 本文の「絵文字なし → 30 秒待機」「眼の絵文字を polling に使わない」両方の訂正、bot identifier の固定、auto-review と ローカル `codex` CLI のクレジット供給源分離 |
| `docs/dev/workflow-authoring.md` § BACK_* プレフィックス拡張 | `docs/dev/workflow-authoring.md:137-152` | "`BACK_*` の suffix は uppercase 英数字 + アンダースコア (`[A-Z0-9_]+`) に限定" / "suffix の意味は workflow 設計者が定義" — `BACK_FALLBACK` の YAML 採用可否の根拠 |
| `.kaji/wf/review-close.yaml` / `review-cycle.yaml`（現行） | `.kaji/wf/review-close.yaml:16-24` / `.kaji/wf/review-cycle.yaml:16-23` | 改修対象の step 定義。現行は `agent: codex` で `review` skill を能動起動する構造 |
