# Managed Starter Sync Runbook

kaji Release 後に managed starter を追随、独立 review、snapshot 公開する運用の正本。
starter repository は kaji が所有する配布 repository とし、作業・不具合は kaji Issue で管理する。

## Managed starters

| repository | default local path | quality gate source |
|---|---|---|
| `apokamo/kaji-starter-python` | kaji main worktree の sibling `../kaji-starter-python` | starter repository の実体 |
| `apokamo/kaji-starter-typescript` | kaji main worktree の sibling `../kaji-starter-typescript` | starter repository の実体 |

新しい言語の starter はこの表へ追加する。skill は tracking Issue の `starter_repo` を入力とし、
manifest / lockfile / quality gate を repository から解決する。

## Tracking Issue

同じ starter の未完了な追随作業は、**未完了期間ごとに 1 件**の open tracking Issue へ集約する
（kaji Release ごとには作らない）。starter 側 Issue は使わない。発見キーは `starter-sync` label
（title の owner/repo 文字列検索はしない。title 編集による under-collect と重複 Issue 作成を
防ぐため）。

```markdown
# [starter-sync]: owner/repo の kaji 追随

<!-- kaji-starter-sync: v1 -->
starter_repo: owner/repo
starter_path: /optional/non-standard/path

## Sync tasks

| target_kaji_release | status | batch | result |
|---|---|---|---|
| vX.Y.Z | open | - | - |
```

`## Sync tasks` 表が状態の正本。行ごとの語彙:

| 列 | 語彙 | 意味 |
|---|---|---|
| `target_kaji_release` | `vX.Y.Z` | この行が追随する kaji Release。重複禁止・版順序で管理する |
| `status` | `open` / `syncing` / `done` | 未着手 / 進行中の batch に属する / 完了（公開または統合済み） |
| `batch` | `-` または `bN` | 1 回の追随タスクが束ねる Release 集合の永続表現。`open` 行は常に `-`、`syncing` / `done` 行は必須。既存最大値 + 1 で採番し再利用しない |
| `result` | `-` / `N/A` / starter tag | `open` / `syncing` 行は常に `-`。`done` 行は公開した starter tag、または束ねた他行へ統合された場合は理由付きで `N/A` |

進行中かどうかは本文の `status`（`syncing` 行の有無）だけで判定する。verdict marker
（`target=` / `base=` / `candidate=` の SHA レベル証跡）は完了後も Issue コメントに恒久的に残る
ため、進行判定には使わない（target/base/candidate の SHA 証跡としては引き続き正本）。

旧 schema（`target_kaji_release:` 単一 key 形式、schema marker なし）は互換読み取りをせず、
CLI が fail-loud で拒否する（BREAKING、ADR 008）。既存の旧 schema tracking Issue の書き換えは
運用者が個別に行う。

kaji GitHub Release 本文の repository 別状態表を状態正本とする。

| repository | status | tracking Issue | starter Release / N/A 理由 |
|---|---|---|---|
| owner/repo | PENDING | #123 | - |

単一の集約 status は置かない。各行が独立に `PENDING -> PASS` または `N/A` へ遷移する。公開成功前は
対象行を `PENDING` のまま維持し、公開成功後にだけ `PASS`（+ starter Release）または理由付き
`N/A` へ更新する。

### 判定 CLI

判定は skill の散文ではなく決定的 CLI に置く（ADR 008 決定 3）。skill は判定結果を適用するだけ。

- `kaji starter tracking-plan`（release 時、`/release` Step 8）: 同じ starter の open tracking
  Issue が 0 件なら新規作成（`CREATE`）、1 件なら既存行を壊さず末尾に `open` 行を追加
  （`APPEND`）、追加済みなら無変更（`IDEMPOTENT`）、2 件以上見つかったら自動選択・自動統合せず
  停止する（`ABORT`。人間が `selected_issue_id` を明示指定してから再実行する）。
- `kaji starter task-plan`（sync 時、`/update-starter` 冒頭・`/release-starter` の完了
  bookkeeping）: 進行中 batch の継続（`SYNC`、本文不変）、未着手 `open` 全件を束ねた新 batch の
  開始（`SYNC`、`covered_targets` = 全 `open`）、全 task 完了の報告（`CLOSABLE`）、公開成功後の
  bookkeeping 適用（`COMPLETED`）を判定する。`completion`（`batch` + `completed_targets` +
  `published_tag`）は固定済み batch の target 集合と完全一致するときだけ受理し、部分集合・
  上位集合・batch 外 target・batch id 不一致は `ABORT` にする。

### 束ね規則

未着手の `open` Release が複数あるとき、次の batch は**全 `open` を 1 回の追随対象として束ねる**
（latest 公開済み snapshot から最新 target までの全差分を明示する）。束ねた集合は `batch` 列へ
永続化するため、candidate 固定・独立 review・後続 Release の追加を経ても縮退しない。公開後、
束ねた Release のうち実際に公開した最新版だけが `PASS`、それ以外は理由付き `N/A`（例:
「`kaji-v0.20.1` の snapshot に統合。個別 snapshot なし」）になる。

### close 条件

tracking Issue は `open` も `syncing` も残っていないとき（全行 `done`）に限り close する
（`task-plan` の `close_allowed`）。close 後に新しい kaji Release が来たら新規 tracking Issue を
作る。close 前に新しい Release が来た場合は、同じ Issue に `open` 行として追加する
（`tracking-plan` の `APPEND`）。

### 複数候補の fail-closed

同じ starter に未完了の open tracking Issue が 2 件以上見つかった場合、`tracking-plan` は
自動選択・自動統合せず `ABORT` する。人間がどちらを使うか `selected_issue_id` で明示指定して
から再実行する。

## Workflow

1. `/release` が PyPI publish 確認後、starter ごとに `kaji starter tracking-plan` を実行し、
   `CREATE`（新規 tracking Issue 作成）または `APPEND`（既存の open tracking Issue へ追随タスクを
   追加）を適用して kaji Release 状態表へリンクする。
2. `/update-starter <id>` が `kaji starter task-plan` の `active_target`（batch 内 target の
   最大値） / `covered_targets` を対象に、全変更を 3 区分し、remote main と同期した local main へ
   直接 commit する。feature branch / worktree / PR / merge は使わず、review 前には push しない。
3. 別 session の `/review-starter-update <id>` が target（= `active_target`） / base / candidate に
   固定した独立 review を行う。
4. `/release-starter <id>` が最新 PASS と SHA、quality gate、release-plan を確認し、ref push が
   ある場合は workflow 外の人間承認後に annotated tag と main を atomic push する。公開後は
   `kaji starter task-plan` の `completion` で状態表更新と（`close_allowed` が真のときだけ）
   tracking Issue close を行う。

初回 tag は `kaji-vX.Y.Z`。同じ kaji version の公開済み snapshot から candidate が変わる修正版だけ
`kaji-vX.Y.Z-rN`（最大 N + 1）を使う。force push / tag 上書きは禁止。Release 作成の再試行では同じ
tag を使う。N/A は独立 review PASS かつ remote main == 検証済み base/candidate を確認した後だけ
完了 bookkeeping（状態表更新、`close_allowed` が真なら Issue close）を行い、remote main が
前進していれば stale review evidence として ABORT する。starter Release は作らない。

未完了の open tracking Issue があっても新しい kaji Release 自体は止めず、`tracking-plan` の
`APPEND` で同じ Issue に追随タスクを積む（release 順に Issue を増やさない）。starter failure から
kaji tag / Release / PyPI を rollback しない。

### Workflow による起動

上記 3 skill は `.kaji/wf/custom/operations/starter-sync.yaml` で接続されている。
`update-starter` → `review-starter-update` の正常系遷移、RETRY による `update-starter` への
差し戻し（`max_iterations: 3` で `on_exhaust: ABORT`）、`release-starter` までの遷移を宣言する。

publish は workflow 外の人間承認を要求するため、通常は 2 回に分けて起動する。

```bash
# Phase 1: candidate 作成 → 独立 review（release-starter の手前で停止）
kaji run .kaji/wf/custom/operations/starter-sync.yaml <tracking_issue_id> --before release-starter

# --- workflow 外で人間が candidate SHA と tag を確認し、明示承認する ---

# Phase 2: 承認後の publish
kaji run .kaji/wf/custom/operations/starter-sync.yaml <tracking_issue_id> --from release-starter
```

`--before release-starter` は `release-starter` を dispatch する直前の exclusive barrier で停止する。
承認後は `--from release-starter` で同じ tracking Issue に対して再開し、`release-starter` から実行する。

> **警告**: `--before release-starter` を省略して起動した場合、`execution_policy: auto` の下で
> review PASS 直後に `release-starter` へ自動遷移する。publish 前の人間承認は `release-starter`
> skill 内の instruction（Publish 節）にのみ依存しており、workflow 自体に機械可読な承認 gate は
> ない。tracking Issue に対する起動は必ず 2 phase（`--before` → 人間承認 → `--from`）で行うこと。

## One-time bootstrap after Issue 341

Issue 341 の merge / close 後、別の kaji tracking Issue と有人手順で現在の starter main を
`kaji-v0.12.1` annotated tag + GitHub Release として固定する。remote identity、dependency pin、quality
gate、人間承認を確認して atomic push する。通常 skill に初回 fallback を追加せず、bootstrap 完了前の
`update-starter` は Release 不在として ABORT する。managed starter の GitHub Issues は無効化し、報告先を
kaji Issue tracker にする。

## Verification boundary and follow-up Issue

Issue 341 では skill / docs / CLI の静的・決定的テストだけを行い、実 starter を変更しない。
bootstrap 後に別の follow-up Issue を作り、`v0.12.1 -> v0.15.0` の実追随で 3 skill の forward test を
行う。実 repository の branch / push / tag / Release はその Issue の明示スコープなしに実行しない。
