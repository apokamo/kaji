# [設計] starter sync tracking を starter ごとに統合する

Issue: #423

## 概要

managed starter ごとの starter-sync tracking Issue を「kaji Release ごとに 1 件」から
「未完了期間ごとに 1 件」へ変更し、未完了の open tracking Issue があれば新規 Issue を作らず
同じ Issue に後続 Release の追随タスクを追加する。追加・選択・完了・close の判定は
`kaji starter tracking-plan` / `kaji starter task-plan` の 2 つの決定的 CLI に置き、
skill は判定結果を適用するだけにする。

## 背景・目的

### 現状の問題

`/release` は managed starter ごとに tracking Issue を 1 件作る
（`.claude/skills/release/SKILL.md` Step 8 / `docs/operations/release/runbook.md` の手順 10）。
starter の追随が複数 release にまたがると、同じ starter の未完了 Issue が release 数だけ増える
（背景 Issue #401 / #413 / #419）。一方 `update-starter` は
「同じ starter に古い `PENDING` があれば fallback せず ABORT」
（`.claude/skills/update-starter/SKILL.md` 実行順 2、
`docs/operations/release/starter-sync-runbook.md` 50-52 行）であるため、
最新 snapshot へ一括追随する経路が存在せず、個別 snapshot が不要な release でも
release 順に candidate / review / publish を繰り返す必要がある。

### ユースケース

- managed starter maintainer として、未同期の kaji Release が続いても tracking Issue を増やさず、
  既存の open tracking Issue に追随タスクを足していきたい。
- maintainer として、candidate 作成済み・独立 review 済み・publish 待ちの進行中タスクがあるとき、
  その target / base / candidate 証跡を壊さずに、新しい Release を後続タスクとして積みたい。
- maintainer として、未着手 Release が複数溜まったとき、最新 snapshot から最新 target までの
  全差分を 1 回の追随として処理し、統合された過去 Release には理由付き `N/A` を残したい。
- maintainer として、同じ starter に未完了 Issue が 2 件以上見つかったときは、自動選択されずに
  停止し、自分でどの Issue を使うか指定したい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| starter ごとに恒久的に 1 件の Issue を再利用する | 完了済み作業の履歴が 1 Issue に無限に積み上がり、close 可能な単位が消える。2026-09-01 interview で maintainer が不採用と決定 |
| Release ごとの Issue 作成を維持し、古い Issue を自動 close する | 未完了作業の証跡を根拠なく破棄する。Issue #423 のスコープ境界で明示的に禁止 |
| 判定を skill の散文（SKILL.md）に書く | producer / consumer 契約を散文で二重管理すると silent な不一致が起きる（ADR 008 決定 3・Issue #261 の実例）。判定は CLI 層に置く |
| tracking Issue を title 文字列検索で発見する | title 編集で silent に under-collect し、重複 Issue 作成（本 Issue が解こうとしている失敗）を再発させる。ラベルを発見キーにする |

## 変更スコープ

| 区分 | 対象 |
|------|------|
| 新規 | `kaji_harness/starter_tracking.py`、`tests/test_starter_tracking.py` |
| 変更 | `kaji_harness/starter_release.py`、`kaji_harness/errors.py`、`kaji_harness/commands/starter.py`、`kaji_harness/commands/parser.py`、`kaji_harness/commands/main.py` |
| 変更（skill） | `.claude/skills/release/SKILL.md`、`.claude/skills/update-starter/SKILL.md`、`.claude/skills/review-starter-update/SKILL.md`、`.claude/skills/release-starter/SKILL.md`、`.claude/skills/release-starter/references/preflight-and-recovery.md` |
| 変更（docs / 設定） | `docs/operations/release/starter-sync-runbook.md`、`docs/operations/release/runbook.md`、`docs/dev/labels.md`、`.github/labels.yml`、`CHANGELOG.md`（BREAKING エントリ） |
| 変更（tests） | `tests/test_starter_tracking.py`、`tests/test_starter_release_plan.py`、`tests/test_starter_skills.py`、`tests/test_starter_cli_large_local.py`、`tests/test_layer_imports.py` |
| 対象外 | starter の実追随・snapshot 公開、#401 / #413 / #419 の統合・移行・close、既存 tag / Release / PyPI の rollback、starter-sync 以外の release workflow |

## インターフェース

### 入力 1: tracking Issue 本文 schema（BREAKING）

title: `[starter-sync]: <owner>/<repo> の kaji 追随`（version を含めない）。
label: `starter-sync`（発見キー）。本文:

```markdown
<!-- kaji-starter-sync: v1 -->
starter_repo: apokamo/kaji-starter-python
starter_path: ../kaji-starter-python

## Sync tasks

| target_kaji_release | status | batch | result |
|---|---|---|---|
| v0.20.0 | done | b1 | N/A |
| v0.20.1 | done | b1 | kaji-v0.20.1 |
| v0.20.2 | syncing | b2 | - |
| v0.20.3 | open | - | - |
```

| 要素 | 型・語彙 | 必須 | 制約 |
|------|----------|:---:|------|
| 1 行目 schema marker | `<!-- kaji-starter-sync: v1 -->` | ✅ | 完全一致。無い本文は fail-loud（後述） |
| `starter_repo` | `owner/repo` | ✅ | `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` |
| `starter_path` | 文字列 | ❌ | 既存契約どおり任意 |
| `## Sync tasks` 表 | 4 列 | ✅ | 1 行以上 |
| `target_kaji_release` | `vX.Y.Z` | ✅ | `^v[0-9]+\.[0-9]+\.[0-9]+$`、重複禁止 |
| `status` | `open` \| `syncing` \| `done` | ✅ | それ以外は parse error |
| `batch` | `b<N>`（`^b[1-9][0-9]*$`）\| `-` | ✅ | `open` 行は `-` 固定。`syncing` / `done` 行は batch id 必須 |
| `result` | starter tag（`kaji-vX.Y.Z[-rN]`）\| `N/A` \| `-` | ✅ | `open` / `syncing` 行は `-` 固定。`done` 行は `-` 禁止 |

**`batch` = 束ねた追随対象の永続表現**: 1 回の追随タスクが包含する Release 集合を `batch` 列で
記録する。`syncing` 行の集合がそのまま `covered_targets` であり、candidate 固定後・後続 Release の
APPEND 後・publish 後・部分失敗後の再実行でも、本文だけから同じ集合を復元できる。集合が
active target 1 件へ縮退しないことが、統合された過去 Release へ理由付き `N/A` を書ける前提になる。
batch id は「本文中の既存 batch id の最大値 + 1」で採番し、再利用しない。

**進行中の判定は本文 `status` を単一の正本とする**: 追随中かどうかは `syncing` 行の有無だけで
決まり、Issue コメントに恒久的に残る verdict marker からは導出しない。完了した batch の marker は
履歴として無視され、`done` 行を指す marker が後続タスクの開始を妨げない。`promote_next_task` の
決定的な境界は「`syncing` 行が 0 件かつ `open` 行が 1 件以上」であり、marker の有無ではない。

**verdict marker の役割（既存契約の維持）**: target / base / candidate の SHA レベル証跡は引き続き
verdict marker（`--verdict-meta target=... base=... candidate=...`）を正本とし、`release-starter` の
`kaji issue resolve-verdict` による pre-flight も現行どおりとする。`meta.target` は当該 batch の
`active_target`（= batch 内 target の最大値、`vX.Y.Z` 書式）と完全一致させる。本文は「どの Release
集合を、どの段階まで進めたか」だけを保持し、SHA を二重管理しない。

**旧 schema の扱い（BREAKING / ADR 008 準拠）**: `target_kaji_release:` 単一 key 形式の本文は
互換読み取りをせず、`parse_tracking_issue_body` が fail-loud で拒否する。CHANGELOG の
BREAKING エントリに (a) 壊れる契約 = 旧 tracking Issue 本文、(b) 判定方法 =
`gh issue list --label starter-sync --state open` の本文に `<!-- kaji-starter-sync: v1 -->` があるか、
(c) 適用指針 = 本文を新 schema へ手で書き換える、を記載する。既存 #401 / #413 / #419 の
書き換えは本 Issue の対象外（実行は maintainer の別作業）。

### 入力 2: `kaji starter tracking-plan`（release 時）

stdin に JSON、stdout に JSON（`kaji starter release-plan` と同じ形）。

```json
{
  "starter_repo": "apokamo/kaji-starter-python",
  "new_target": "v0.20.2",
  "open_tracking_issues": [{"issue_id": 424, "body": "<raw issue body>"}],
  "selected_issue_id": null
}
```

`open_tracking_issues` は `kaji issue list --label starter-sync --state open --json number,body`
の全件。`selected_issue_id` は人間が明示指定したときだけ設定する（既定 `null`）。

### 入力 3: `kaji starter task-plan`（sync 時）

```json
{
  "issue_id": 424,
  "issue_state": "open",
  "body": "<raw issue body>",
  "completion": null
}
```

verdict marker は入力に取らない（進行判定は本文 `status` が正本。marker は `release-starter` の
SHA 検証で従来どおり使う）。`completion` は publish 成功後の bookkeeping 時のみ設定する:

```json
{"batch": "b1", "completed_targets": ["v0.20.0", "v0.20.1"], "published_tag": "kaji-v0.20.1"}
```

| field | 制約 |
|-------|------|
| `batch` | 完了させる batch id。本文の `syncing` batch（再適用時は対象 `done` batch）と一致必須 |
| `completed_targets` | 昇順の `vX.Y.Z` 集合。対象 batch の target 集合と **完全一致**。部分集合・上位集合・batch 外 target・未知 target はすべて ABORT |
| `published_tag` | `^kaji-v[0-9]+\.[0-9]+\.[0-9]+(-r[1-9][0-9]*)?$`。`null` は無変更（`base == candidate`）完了を表す |

### 出力

`TrackingPlan`（`tracking-plan`）:

| field | 型 | 説明 |
|-------|-----|------|
| `route` | `1..5` | 判定経路 |
| `decision` | `CREATE` \| `APPEND` \| `IDEMPOTENT` \| `ABORT` | 実行すべき操作 |
| `issue_id` | `int \| None` | APPEND / IDEMPOTENT の対象 |
| `next_body` | `str \| None` | CREATE / APPEND で適用する本文全文（決定的レンダリング） |
| `required_labels` | `list[str]` | CREATE 時に付与する label（`["starter-sync"]`） |
| `reason` | `str` | 人間可読の理由 |

`TaskPlan`（`task-plan`）:

| field | 型 | 説明 |
|-------|-----|------|
| `route` | `1..7` | 判定経路（「task-plan の route」表） |
| `decision` | `SYNC` \| `COMPLETED` \| `CLOSABLE` \| `ABORT` | `SYNC` = 追随を実行 / `COMPLETED` = `completion` 適用（新規または再適用）/ `CLOSABLE` = 全 task 完了で close 可 / `ABORT` = fail-closed |
| `batch` | `str \| None` | 対象 batch id |
| `active_target` | `str \| None` | 今回追随する kaji Release（batch 内 target の最大値） |
| `covered_targets` | `list[str]` | batch が包含する Release（昇順）。本文の `batch` 列から復元する |
| `coalesced` | `bool` | `len(covered_targets) > 1` |
| `remaining_targets` | `list[str]` | 適用後に残る `open`（昇順） |
| `close_allowed` | `bool` | 適用後に `open` も `syncing` も残らないとき `true` |
| `state_table_updates` | `list[StateTableUpdate]` | `decision == "COMPLETED"` のときのみ非空 |
| `next_body` | `str \| None` | 本文更新が必要なときだけ非 `null`（route 2 の batch 開始、route 4 の completion 適用）。route 1 / 3 / 5 は `null` |
| `reason` | `str` | 人間可読の理由 |

`StateTableUpdate`: `{"kaji_release": "v0.20.0", "status": "N/A", "starter_release": null,
"reason": "kaji-v0.20.1 の snapshot に統合。個別 snapshot なし", "tracking_issue": 424}`。

### 使用例

```python
from kaji_harness.starter_tracking import build_tracking_plan, TrackingPlanInput

plan = build_tracking_plan(TrackingPlanInput.model_validate(observation))
if plan.decision == "ABORT":
    stop_and_report(plan.reason)          # 人間の明示指定を待つ
elif plan.decision == "APPEND":
    edit_issue(plan.issue_id, plan.next_body)
```

```bash
# release 時（/release Step 8）
kaji starter tracking-plan < observation.json
# sync 時（/update-starter 冒頭、/release-starter の完了 bookkeeping）
kaji starter task-plan < observation.json
```

### エラーと終了コード

| 事象 | 挙動 |
|------|------|
| stdin が非 JSON / pydantic 検証失敗 | stderr に `Error: invalid starter <cmd> input: ...`、exit 2（`EXIT_INVALID_INPUT`、`release-plan` と同一） |
| 本文 parse 失敗（schema marker 無し、未知 status、target 重複、版形式違反、batch id 書式違反、status と batch / result の組み合わせ違反） | 例外を CLI 内で捕捉し、`decision: ABORT` の plan を exit 0 で返す（観測矛盾は plan で表現する `release-plan` の慣習に合わせる） |
| 観測矛盾（複数の open tracking Issue、`syncing` batch が 2 種類、closed Issue に `open` / `syncing` 残存、`completion` の集合不一致、`done` batch に starter tag 行が 2 件以上） | `decision: ABORT` + 期待値と観測値を含む `reason`、exit 0 |
| 正常判定 | JSON 1 行、exit 0（`EXIT_OK`） |

## 制約・前提条件

- provider は GitHub 限定（`.kaji/wf/custom/operations/starter-sync.yaml` の `requires_provider: github`）。
- 外部入力（Issue 本文・観測 JSON）は Pydantic で検証する（AGENTS.md Always-Apply Rules）。
- GitHub 上の複数更新（Issue 本文更新・Release 状態表更新・close）に単一トランザクションはない。
  各操作は観測から plan を再計算して同じ結果になる（冪等）ことを前提に、部分失敗後は再実行で収束させる。
- `update-starter` / `review-starter-update` / `release-starter` の安全契約は不変:
  target / base / candidate の固定、別 session の独立 review、publish 前の人間承認、
  `git push --atomic`、force push / tag 上書き禁止。
- starter の失敗で公開済み kaji tag / GitHub Release / PyPI を rollback しない。
  tracking-plan の ABORT は post-release handoff に閉じ、状態表行は `PENDING` のまま維持する。
- 後方互換レイヤを書かない（ADR 008）。旧 schema 読み取りは実装しない。
- 本文更新は candidate 作成に先行させる（`update-starter` の route 2 適用 → candidate 作成 →
  marker 付き報告）。marker が本文より先行する観測を作らないための順序制約。

## 方針

### モジュール構成

新規 `kaji_harness/starter_tracking.py`（`starter_release.py` と同じ層・同じ書式）:

| 名前 | 責務 |
|------|------|
| `parse_tracking_issue_body(text) -> TrackingIssueBody` | 本文 → 構造化。違反は `TrackingBodyError` を raise |
| `render_tracking_issue_body(body) -> str` | 構造化 → 本文。target 昇順で決定的に整形 |
| `build_tracking_plan(input) -> TrackingPlan` | release 時の CREATE / APPEND / IDEMPOTENT / ABORT 判定 |
| `build_task_plan(input) -> TaskPlan` | sync 時の batch 継続 / 開始 / 完了 / 再適用 / close 判定と状態表更新の生成 |
| `parse_release_version(value) -> tuple[int, int, int]` | `vX.Y.Z` の順序付け |

CLI は `commands/starter.py` に `cmd_starter_tracking_plan` / `cmd_starter_task_plan` を追加し、
`parser.py` の `_register_starter` と `main.py` の dispatch に接続する（`release-plan` と同形）。

### tracking-plan の route

1. 同じ `starter_repo` の open Issue が 0 件 → `CREATE`。`new_target` 1 行（`open`）の本文を返す。
2. 1 件、`new_target` が未掲載 → `APPEND`。既存行を変更せず末尾に `open` 行を足した本文を返す。
3. 1 件、`new_target` が掲載済み → `IDEMPOTENT`。本文変更なし（release 再実行で行を増やさない）。
4. 2 件以上かつ `selected_issue_id` が `null` → `ABORT`（自動選択・自動統合をしない）。
   `selected_issue_id` が候補集合に含まれていれば route 2 / 3 と同じ処理を行う。
5. 観測矛盾（本文 parse 失敗、`selected_issue_id` が候補外、`starter_repo` 不一致）→ `ABORT`。

### task-plan の route

進行中判定は本文の `status` 列だけを使う（verdict marker は参照しない）。`completion` の
有無で「追随フェーズ」と「完了フェーズ」に分岐する。

| route | 前提 | decision | 出力の要点 |
|:---:|------|----------|-----------|
| 1 | `completion` なし・`syncing` batch が 1 件 | `SYNC` | 既存 batch を継続。`active_target` / `covered_targets` を本文の `batch` 列から復元し、`next_body` は `null`（本文を変更しない = 固定済み証跡と束ね集合を保護する） |
| 2 | `completion` なし・`syncing` なし・`open` が 1 件以上 | `SYNC` | 新 batch を開始。`covered_targets` = 全 `open`（昇順）、`active_target` = その最大値。`next_body` は当該行を `syncing` + 新 batch id にしたもの |
| 3 | `completion` なし・`syncing` なし・`open` なし | `CLOSABLE` | 全 task 完了。`close_allowed: true`、`next_body` は `null`（本文変更なしで Issue close へ） |
| 4 | `completion` あり・一致する `syncing` batch がある | `COMPLETED` | 集合一致検証を通過。batch 行を `done` にし `result` を埋めた `next_body` と `state_table_updates` を返す |
| 5 | `completion` あり・`syncing` なし・同じ batch id と target 集合の `done` batch があり、`result` が再計算値と一致 | `COMPLETED` | 部分失敗後の再適用。route 4 と同一の `state_table_updates` を返し、`next_body` は `null`（本文は既に適用済み） |
| 6 | `completion` あり・集合不一致（部分集合 / 上位集合 / batch 外 target / batch id 不一致）、または `done` batch の `result` が再計算値と不一致 | `ABORT` | 期待集合と入力集合を `reason` に列挙する fail-closed |
| 7 | 本文 parse 失敗 / `syncing` batch が 2 種類 / `issue_state == "closed"` かつ `open` または `syncing` 残存 / `done` batch に starter tag 行が 2 件以上 | `ABORT` | 観測矛盾 |

route 1 が `next_body` を返さないことが、「進行中タスクでは active target と固定済み証跡を変更
しない」完了条件の機械的な担保になる。route 4 と route 5 は同じ `state_table_updates` を返すため、
状態表更新や close が部分失敗しても、再実行で同じ結果へ収束する。

#### 書き込み順序（部分失敗時の安全性）

`update-starter` は「route 2 の `next_body` 適用（batch を `syncing` にする）」→「candidate 作成」→
「verdict marker 付きの報告」の順に実行する。本文更新が先行するため、candidate 作成前に落ちれば
route 1 が同じ batch を再開し、本文更新前に落ちれば route 2 が同じ集合で batch を開始し直す。
どちらでも束ね集合は変わらず、「marker だけが先行して残り本文が追随していない」観測は生じない。

#### 状態遷移（束ね → candidate 固定 → 後続 Release 追加 → publish → 次 task 昇格）

| 時点 | 本文の状態 | task-plan の判定 |
|------|-----------|-----------------|
| v0.20.0 / v0.20.1 が未着手 | 両行 `open` | route 2: batch `b1`、`covered=[v0.20.0, v0.20.1]`、`active=v0.20.1` |
| `update-starter` が batch 開始を適用 | 両行 `syncing b1` | — |
| candidate 作成 → 独立 review（marker 付与） | 変化なし | route 1: `covered=[v0.20.0, v0.20.1]` を本文から復元（active 1 件へ縮退しない） |
| `/release` が v0.20.2 を APPEND | `v0.20.2` が `open` で追加 | route 1: `syncing b1` が優先され、`b1` の集合は不変 |
| publish 成功 → completion 適用 | `b1` の 2 行が `done`（`N/A` / `kaji-v0.20.1`） | route 4: `state_table_updates` 2 件、`remaining=[v0.20.2]`、`close_allowed: false` |
| 状態表更新が部分失敗 → 再実行 | 変化なし | route 5: route 4 と同一の `state_table_updates`（冪等） |
| 次タスクへ昇格（`promote_next_task`） | `v0.20.2` が `syncing b2` | route 2: 完了済み `b1` の marker は履歴として無視される |
| v0.20.2 も完了 | 全行 `done` | route 3: `close_allowed: true` → Issue close |

### 状態表の更新規則

`state_table_updates` は publish 成功後（`completion` 指定時）にだけ生成する。成功前は各 kaji
Release の状態表行を `PENDING` のまま維持する。生成規則は `done` batch の本文から決定的に
再計算でき、route 4 と route 5 で同一の出力になる。

- `published_tag` がある場合: `active_target` の行 → `PASS` + `starter_release = published_tag`。
  同じ batch の他の行 → `N/A` + reason「`<tag>` の snapshot に統合。個別 snapshot なし」。
- `published_tag` が `null`（`base == candidate`）の場合: batch の全行 → `N/A` +
  reason「変更なし。starter Release を作らない」。
- 再適用時は `done` batch 内で `result` が starter tag の行を `PASS`、`N/A` の行を統合分と判定して
  同じ出力を再構成する。tag 行が 2 件以上ある batch は観測矛盾として route 7 の ABORT にする。

### `starter_release.py` の変更

- `ReleaseAction` に `"promote_next_task"` を追加する。
- `ReleasePlanInput` に `tracking_issue_has_pending_tasks: bool = False` を追加する。
- `_remaining_bookkeeping`: pending task があるときは末尾を `close_tracking_issue` ではなく
  `promote_next_task` にする（全タスク完了時のみ close する完了条件の機械化）。
- `_observation_contradiction`: `tracking_issue_state == "closed"` かつ pending task ありは ABORT。

### skill / docs の変更（判定は CLI、skill は適用のみ）

| 対象 | 変更内容 |
|------|---------|
| `release/SKILL.md` Step 8 | starter ごとに `kaji starter tracking-plan` を実行し、`CREATE` は label 付き新規 Issue、`APPEND` は既存 Issue 本文更新、`ABORT` は handoff のみ停止（状態表 `PENDING` 維持、kaji release は rollback しない） |
| `update-starter/SKILL.md` | 実行順 2 の「古い `PENDING` があれば ABORT」を削除し、`kaji starter task-plan` の `active_target` / `covered_targets` を追随対象にする。route 2 の `next_body`（batch を `syncing` にする本文更新）を candidate 作成より前に適用する順序を明記する。開始点は最新公開 snapshot、target は `active_target` |
| `review-starter-update/SKILL.md` | target を tracking Issue の `active_target`（batch 内最大 target）から解決する旨と `meta.target` 書式を明記 |
| `release-starter/SKILL.md` / `references/preflight-and-recovery.md` | 完了 bookkeeping を `task-plan` の `completion`（`batch` + `completed_targets` + `published_tag`）経由にし、`covered_targets` と完全一致する集合だけを完了できることを明記。`state_table_updates` の各行を適用し、`close_allowed` が真のときだけ close。部分失敗時は同じ `completion` を再実行（route 5）する復旧手順と `promote_next_task` route を追記 |
| `starter-sync-runbook.md` | tracking Issue schema（新 v1）、未完了期間ごとに 1 件、複数候補の fail-closed、束ね規則、close 条件、状態表遷移を正本として記述 |
| `runbook.md` | 全体像の図と手順 10 を tracking-plan 経由の handoff に更新 |
| `.github/labels.yml` / `docs/dev/labels.md` | meta ラベルに `starter-sync` を追加（meta 9 → 10、合計 28 → 29） |

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 未完了同期の管理単位 | 同じ starter の未完了作業は未完了期間ごとに 1 件の open tracking Issue へ集約する | Issue #423「決定事項 / 人間決定」（2026-09-01 `/grill-me 423` interview、Issue コメント "grill-me provenance"） | `## Sync tasks` 表で 1 Issue に複数 target を保持し、全 `done` で close する構造へ分解 |
| 新しい Release の追加 | 未完了 Issue があれば新規 Issue を作らず同じ Issue に後続タスクを追加する | 同上（人間決定） | `tracking-plan` route 2（APPEND）/ route 3（IDEMPOTENT）として機械化 |
| 進行中タスクと後続タスク | active target と固定済み証跡を維持し、進行中タスクを先に完了する | 同上（人間決定） | `task-plan` route 1 で `syncing` batch を継続し、`next_body: null` で本文を変更しない。後続 Release は `open` 行として APPEND され、batch 完了後に route 2 で昇格する |
| Issue の終端 | 全タスク完了後に close。close 後の新 Release は新規 Issue | 同上（人間決定） | `close_allowed` と `promote_next_task` action で close 条件を機械化 |
| 複数の未完了 Issue | 自動選択・自動統合せず人間の明示指定を要求する | 同上（人間決定） | `tracking-plan` route 4 の fail-closed ABORT + `selected_issue_id` の明示入力 |
| 既存 #401 / #413 / #419 | 背景参照に限定し、移行・実追随・公開・close を行わない | Issue #423「スコープ境界」「決定事項」（人間決定） | 実装・テストは合成データのみを使い、既存 Issue へ書き込む手順を skill に追加しない |
| 未着手 Release の束ね | latest snapshot から最新 target までの全差分を 1 タスクとして明示する | Issue #423「完了条件」5 番目（人間決定） | `task-plan` route 2 で全 `open` を 1 batch にまとめ、`batch` 列へ永続化して candidate 固定後も `covered_targets` を復元できる構成へ分解 |
| 過去 Release の状態表 | 公開成功前は `PENDING` 維持、成功後に理由付き `N/A` | Issue #423「完了条件」6 番目（人間決定） | `state_table_updates` を `completion` 指定時のみ生成する規則へ分解 |
| tracking Issue 本文 schema | 機械可読かつ人間可読な `## Sync tasks` 表 + schema marker | AI の仮定。根拠: Issue #423「AI が設計で詳細化する事項」1 行目。検査先: issue-review-design / tests / issue-review-code | 4 列表（`target_kaji_release` / `status` / `batch` / `result`）・語彙・必須制約・レンダリング順序を確定 |
| 束ね集合の保持方法 | 本文の `batch` 列に永続化し、marker からは再構成しない | AI の仮定。根拠: verdict marker は `target/base/candidate` しか持たず、束ねた集合を表現できない（`markers.py` の meta 文法）。2026-09-01 の review-design 指摘 1 で marker 由来の導出が破綻することを確認。検査先: issue-review-design / Small tests / issue-review-code | route 1 の `covered_targets` 復元、batch id の採番規則、APPEND による非破壊追加として分解 |
| 進行状態の判定 | 本文 `status`（`syncing` 行の有無）を単一の正本とし、verdict marker は進行判定に使わない | AI の仮定（**初版の仮定を撤回して差し替え**）。初版は marker を進行判定に使う仮定だったが、marker は完了後も Issue コメントに残り「`done` 行を指す marker」と「後続 task の開始」を区別できないため、2026-09-01 の review-design 指摘 2 を受けて改訂した。根拠: `markers.py` の marker は永続コメント。検査先: issue-review-design / Small tests | 進行判定を本文 `status` に一元化し、marker は `release-starter` の SHA 検証専用（`meta.target == active_target`）として残す構成へ分解 |
| completion の検証 | 固定済み batch の target 集合と完全一致する completion だけを受理する | AI の仮定。根拠: 部分集合・上位集合を許すと状態表更新が欠落・過剰になる。検査先: issue-review-design / Small tests / issue-review-code | route 4（新規適用）/ route 5（冪等再適用）/ route 6（不一致 ABORT）の分岐として分解 |
| 部分失敗の再試行 | 観測から plan を再計算する冪等な CLI 判定にする | AI の仮定。根拠: Issue #423「AI が設計で詳細化する事項」3 行目、`build_release_plan` の route 2 部分成功再実行の先例。検査先: issue-review-design / Small tests / issue-review-code | `next_body` 全文レンダリング、tracking-plan の IDEMPOTENT route、task-plan route 5 の再適用として分解 |
| 旧 schema の扱い | 互換読み取りを実装せず fail-loud + CHANGELOG BREAKING で通知する | ADR 008「後方互換レイヤを提供しない」決定 1・2（既存契約） | schema marker 必須化と BREAKING 3 要素の記載内容を確定 |
| 判定ロジックの置き場所 | SKILL.md 散文ではなく CLI / harness 層 | ADR 008 決定 3（既存契約） | 2 CLI subcommand + 純関数モジュールとして分解 |
| tracking Issue の発見キー | `starter-sync` label | AI の仮定。根拠: title 検索は編集で under-collect し重複作成を再発させる。検査先: issue-review-design / issue-review-code | labels.yml への meta ラベル追加と `required_labels` 出力として分解 |

one-way door の未決は検出していない。人間決定は Issue #423 `## 決定事項` と 2026-09-01 の
`/grill-me 423` provenance コメントで固定されており、本設計はその範囲内の詳細化に留めている。
2026-09-01 の `review-design` RETRY（指摘 1: 束ね集合の消失、指摘 2: 完了済み marker と昇格境界、
Should Fix: `COMPLETE` の多義性）を受けた改訂も、人間決定を変えずに AI の仮定を差し替えた
範囲に留まる。

## テスト戦略

### 変更タイプ

実行時コード変更（新規モジュール + CLI subcommand 2 件 + 既存 plan ロジック変更）
に skill / docs 変更を伴う。恒久回帰テストを追加する。

### Small テスト

`tests/test_starter_tracking.py`（`@pytest.mark.small`）で純関数を検証する。

- **本文 parse / render の往復**: 正常本文の parse → render で意味が保存されること。
  `open` 行の `batch` / `result` が `-` 固定、`syncing` 行が batch id + `result: -`、`done` 行が
  batch id + 非 `-` の `result` を持つこと。target 昇順に決定的整形されること。
- **parse の異常系**: schema marker 欠落（= 旧 schema 本文）、未知 status、target 重複、
  版形式違反、`## Sync tasks` 欠落、`done` 行の `result` が `-`、`syncing` 行に `result` あり、
  `open` 行に batch id あり、batch id 書式違反、`starter_repo` 書式違反。
- **tracking-plan の route 1〜5**: 0 件 / 1 件未掲載 / 1 件掲載済み / 2 件以上 + 未指定 /
  2 件以上 + 明示指定 / 候補外 ID / starter_repo 不一致。APPEND が既存行の target・status・
  batch・result を保存すること（`syncing` batch を壊さない非破壊追加）。
- **task-plan の route 1〜7**: route 1（`syncing` 継続・`next_body` が `null`）/ route 2（束ね、
  `active_target == max(open)`、`covered_targets == 全 open`、batch id = 既存最大 + 1）/
  route 3（`CLOSABLE`）/ route 4（`done` 化・`result` 充填・`remaining_targets`）/ route 5
  （再適用の冪等）/ route 6（集合不一致 4 種）/ route 7（観測矛盾 4 種）。
- **束ね集合の非縮退（review 指摘 1 の回帰テスト）**: `v0.20.0` + `v0.20.1` を route 2 で束ね →
  本文適用 → candidate/review 相当の時間経過 → route 1 を再評価し、`covered_targets` が
  2 件のまま復元されること。同 batch 継続中に `v0.20.2` を APPEND しても route 1 の
  `covered_targets` が変化しないこと。
- **完了 → 昇格の一連（review 指摘 2 の回帰テスト）**: route 4 で `b1` を完了 → 状態表更新の
  部分失敗を模して route 5 を再評価（同一 `state_table_updates`）→ route 2 が `v0.20.2` を
  `b2` として開始し、完了済み `b1` の存在が ABORT を引き起こさないこと。全 batch 完了後に
  route 3 が `close_allowed: true` を返すこと。
- **状態表更新の生成**: `published_tag` あり（active は `PASS`、統合分は理由付き `N/A`）と
  `null`（全件 `N/A`）の 2 系統。生成が `completion` 指定時のみであること。`done` batch からの
  再計算が route 4 の初回出力と一致すること。
- **`starter_release.py` の変更**: pending task ありで `close_tracking_issue` ではなく
  `promote_next_task` が返ること、closed + pending の観測矛盾が route 5 ABORT になること、
  既定値 `False` で既存 route 1〜4 の出力が不変であること（既存 `tests/test_starter_release_plan.py` に追加）。

版順序は `v0.9.0 < v0.10.0` を含め、文字列比較では誤る境界を検証する。

### Medium テスト

`tests/test_starter_skills.py`（`@pytest.mark.medium`、repo ファイル I/O）を拡張する。

- skill / runbook が新契約を参照していること: `update-starter` から
  「古い `PENDING` で ABORT」の記述が消え `task-plan` を参照していること、
  `release-starter` が `close_allowed` / `covered_targets` を参照していること、
  `release/SKILL.md` が `tracking-plan` と ABORT 時の `PENDING` 維持を明記していること。
- `starter-sync-runbook.md` に新 schema（`<!-- kaji-starter-sync: v1 -->`）、複数候補 fail-closed、
  束ね規則、close 条件が記載されていること。
- `.github/labels.yml` に `starter-sync` が存在し、`docs/dev/labels.md` の件数記述と整合すること。
- docs リンク整合は既存 `make verify-docs` / `test_check_doc_links.py` が担保する（重複追加しない）。

### Large テスト

`tests/test_starter_cli_large_local.py`（`@pytest.mark.large` + `large_local`、subprocess・ネットワークなし）を拡張する。

- `kaji starter tracking-plan` / `kaji starter task-plan` の subprocess dispatch が
  stdin JSON を受けて JSON 1 行 + exit 0 を返すこと（ABORT decision でも exit 0）。
- 不正 JSON / schema 違反入力で stderr 出力 + exit 2 になること。
- `kaji starter` の未知 subcommand で help + 非 0 になること（既存 dispatch 契約の維持）。

実 GitHub API 疎通（`large_forge`）は追加しない。本変更は決定的な純関数と CLI dispatch であり、
GitHub 側の状態は skill が観測 JSON として渡す境界に閉じている。実 starter repository への
push / tag / Release は Issue #423 のスコープ外であり、実追随の forward test は
`### ワークフロー完了後の確認項目` として Issue 側に分離済み。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定はない。ADR 008（後方互換なし / 契約は CLI 層）を適用するだけ |
| docs/ARCHITECTURE.md | なし | 層構成・モジュール境界は不変（`starter_release.py` と同層に 1 モジュール追加） |
| docs/operations/release/starter-sync-runbook.md | あり | tracking Issue schema・運用規則の正本 |
| docs/operations/release/runbook.md | あり | 全体像の図と手順 10 の handoff 記述 |
| docs/dev/labels.md | あり | meta ラベル `starter-sync` の追加と件数 |
| docs/dev/ (その他) | なし | workflow / テスト規約自体は変更しない |
| docs/reference/ | なし | Python 規約・設定リファレンスに変更なし |
| docs/cli-guides/ | なし | `kaji starter` 系は maintainer 専用 helper で cli-guides に節を持たない（既存 `release-plan` も同様） |
| AGENTS.md / CLAUDE.md | なし | 開発規約・skill lifecycle は不変 |
| CHANGELOG.md | あり | tracking Issue 本文 schema の BREAKING エントリ（3 要素） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #423 本文「決定事項 / 人間決定」 | https://github.com/apokamo/kaji/issues/423 | 集約単位・後続タスク追加・進行中の証跡維持・close 条件・複数候補 fail-closed・既存 3 件のスコープ外を人間決定として固定 |
| Issue #423 コメント "grill-me provenance"（2026-08-31T15:31Z） | https://github.com/apokamo/kaji/issues/423 | 「永久に 1 件を再利用する方式ではなく、未完了作業の重複だけを防ぐ」「複数候補は fail-closed」など不採用案を含む決定経緯 |
| Managed Starter Sync Runbook | `docs/operations/release/starter-sync-runbook.md`（18, 28-34, 50-52 行） | 変更前契約: 「kaji Release ごと・managed starter ごとに kaji 側へ一件作る」「各行が独立に `PENDING -> PASS` または `N/A` へ遷移する」「同じ starter の sync は release 順で処理し、新しい update は ABORT する」 |
| Release Runbook | `docs/operations/release/runbook.md`（30-33, 65-66 行） | 変更前契約: 「managed starter ごとの tracking Issue を作成し、Release 状態表へリンクして `/update-starter` へ handoff」 |
| `/release` skill | `.claude/skills/release/SKILL.md`（211-221, 263-268, 355-359 行） | 状態表の初期化（全行 `PENDING`）と、handoff 失敗時に「`PENDING` のまま維持し、tracking Issue 作成または状態表リンク更新だけを再試行する」復旧規則 |
| `/update-starter` skill | `.claude/skills/update-starter/SKILL.md`（実行順 2） | 変更対象の記述「同じ starter に古い `PENDING` があれば fallback せず ABORT」 |
| `/release-starter` skill | `.claude/skills/release-starter/SKILL.md`（Pre-flight 2・4、Publish 節） | 維持すべき安全契約: `resolve-verdict` による独立 review PASS 確認、`meta.candidate == local main HEAD`、人間の明示承認、`git push --atomic`、route 2 部分成功再実行 |
| 既存 release plan 実装 | `kaji_harness/starter_release.py`（102-233 行） | 変更対象の `remaining_actions` 末尾 `close_tracking_issue` と、観測矛盾を route 5 ABORT で fail-closed にする既存パターン |
| 既存 CLI 実装 | `kaji_harness/commands/starter.py` / `commands/parser.py`（41-51 行） | 追加する 2 subcommand が従う先例: stdin JSON → pydantic 検証 → JSON 出力、検証失敗は exit 2 |
| verdict marker 契約 | `kaji_harness/providers/markers.py`（26-40 行） | `meta` の key/value 文法（`^[a-z][a-z0-9_]*$` / `^[A-Za-z0-9][A-Za-z0-9._/-]*$`）。`target=vX.Y.Z` がこの文法に適合することの裏付け |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md`（決定 1-3、帰結） | 「後方互換レイヤを書かない」「破壊的変更は CHANGELOG / Release notes の BREAKING で 3 要素を明示」「スキルを跨ぐ契約は SKILL.md の散文ではなく CLI / harness 層に置く」 |
| テスト規約 | `docs/dev/testing-convention.md`（S/M/L 定義、省略 4 条件） | 実行時コード変更は原則 S/M/L の観点を定義する。`large_local` は subprocess あり・ネットワークなし |
| Critical Decision Checklist | `.claude/skills/_shared/critical-decision-checklist.md` | provenance 4 列形式、two-way door の仮定は根拠と後段検査先を明記して進む |
| starter-sync workflow | `.kaji/wf/custom/operations/starter-sync.yaml` | `requires_provider: github`、`update-starter` → `review-starter-update` → `release-starter` の遷移（本設計で変更しない前提） |
