---
description: dev-small workflow の方針確認・実装・検証工程。Issue の決定事項を要件の正本として小修正を実装し、commit 前の必須検証を通して commit・報告する。設計書は要求しない。
name: issue-small-change-execute
---

# Issue Small Change Execute

dev-small（`.kaji/wf/custom/dev/dev-small.yaml`）の `change` / `fix-change` step。
Issue 本文と人間の決定事項を要件の正本とし、短い方針 → baseline scope 評価 → 実装・docs →
差分確認 → commit 前の必須検証 → commit → 報告 を 1 工程で行う。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| dev-small の `change`（初回・RETRY 再入）/ `fix-change`（review 系 RETRY 後） | ✅ |
| 標準 dev（`dev.yaml` / `dev-thorough*`）の実装 | ❌ `/issue-implement` |
| docs-only（`type:docs`） | ❌ docs workflow |

**ワークフロー内の位置**: start → baseline → **change** → review-change → (**fix-change** → verify-change) → pr → close

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 用途 |
|------|------|
| `issue_id` / `issue_ref` / `step_id` / `verdict_path` | 対象 Issue、phase（`change` / `fix-change`）、verdict 保存先 |
| `worktree_dir` / `branch_name` / `default_branch` | 作業対象 |
| `cycle_count` / `max_iterations` | 参照可 |

`design_path` は注入されても使わない。

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

`worktree_dir` が無い場合だけ [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) で絶対パスを得る。
`step_id` が無い場合は verdict marker から phase を決める: `review-change` / `verify-change` の最新 `RETRY` が
最新 `fix-change` 報告より新しければ `fix-change`、それ以外は `change`
（`kaji issue resolve-verdict [issue_id] --step <step>` の `created_at` を比較）。

以降の `[worktree_dir]` は注入値または解決した絶対パス。Bash は毎回 `cd [worktree_dir] && ...` で実行する。

## 読み込み条件

| 区分 | 読むもの | 条件 |
|------|----------|------|
| 通常 | Issue 本文・labels（`kaji issue view [issue_id] --json title,body,labels`） | 常時 |
| 通常 | `git status --porcelain` / `git log --oneline [default_branch]..HEAD` | 常時 |
| 通常 | 直近の review 系 `RETRY` 報告 1 件 | `fix-change` |
| 通常 | 直近の自 step `RETRY` 報告 1 件 | `change` の RETRY 再入 |
| 条件付き | `docs/reference/python/*.md` | Python コードを書く場合 |
| 条件付き | `docs/dev/testing-convention.md` § テストサイズ定義 / § テスト戦略の原則 | テストを追加・変更する場合 |
| 例外 | `docs/dev/baseline-check.md` | `--evaluate` が `clean` 以外、または artifact 不整合 |
| 例外 | Issue 本文が参照する人間コメント | 本文が参照している、または決定の特定に不確実さがある場合 |
| 例外 | `docs/dev/workflow_completion_criteria.md` § type 別に追加で確認する項目 | bug で実ログを修正前 Red の代替にする場合 |
| 例外 | `docs/dev/documentation_update_criteria.md` 該当節 | docs 影響の判断に迷う場合 |
| 例外 | 失敗した検証の関連ログ全文 | 検証失敗時 |
| 例外 | [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) | 無関係な問題を発見した場合 |

設計書（`draft/design/**`）、標準 dev の実装手順・type 別ガイド・Pre-Handoff Review 資料は読まず、要求もしない。
Issue の全コメント履歴を無条件に読み直さない。

直近の `RETRY` 報告の取得（1 行目の verdict marker を厳密照合し最新 1 件）:

```bash
# fix-change: review 系の最新 RETRY
kaji issue view [issue_id] --json comments \
  | jq -r '[.comments[] | select(.body | test("^<!-- kaji-verdict: step=(review-change|verify-change) status=RETRY -->"))] | last | .body'
# change の RETRY 再入: 自 step の最新 RETRY
kaji issue view [issue_id] --json comments \
  | jq -r '[.comments[] | select(.body | test("^<!-- kaji-verdict: step=change status=RETRY -->"))] | last | .body'
```

## 実行手順（change）

### Step 1: 前提確認

- worktree と branch が存在すること。
- `type:*` ラベルがちょうど 1 件であること。0 件・複数は ABORT。`type:docs` は docs workflow を案内して ABORT
  （type を付け替えない）。
- 下記「入場時の working tree 判定」を満たすこと。

### Step 2: 適用判定

次をすべて満たさなければ適用外として ABORT する（正本: `docs/dev/workflow_guide.md` § dev-small の適用条件）。

- 期待動作・対象・受け入れ条件が Issue で明確
- 大きな設計判断が残らない（公開互換性・権限境界・データ移行・再開処理・状態永続化・merge 条件等の判断を伴わない）
- 影響範囲を説明できる
- 既存検証または局所的な回帰テストで確認できる
- 通常の revert で戻せる

### Step 3: 短い方針（編集前に確定。承認待ちにしない）

変更内容 / 維持する不変条件 / 変更対象 path / 確認方法（追加・変更するテストと実行する検証コマンド）/ docs 影響。
根拠とした Issue の決定事項を示す。

### Step 4: baseline scope 評価（1 回）

変更対象 path ごとに `--scope` を繰り返し、`--worktree` を必ず渡す。

```bash
cd [worktree_dir] && source .venv/bin/activate && \
  python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] \
    --evaluate --scope <path1> --scope <path2>
```

- `verdict` が `missing_baseline` / `stale_baseline`、または `baseline_status` が `blocked` / `invalid` → ABORT
- `known_failures` で `stop: true`、または `stop: false` でも既知 failure が変更対象と意味的に関連 → ABORT
- `clean`、または無関係な `known_failures` → 継続。`baseline_status` で Step 7 の gate を決める

### Step 5: 実装

- bug: 修正前に失敗する回帰テストを置き、修正後の成功を確認する（実ログ代替は既存 escape clause の範囲内）
- feature: 新しい挙動のテストを追加する
- refactor: 既存テストで振る舞い非変更を確認する。測定方法が決まっていない改善指標が必要なら適用外 ABORT
- その他の type: feature と同等
- テストには `small` / `medium` / `large` marker を付ける。docs 更新は本工程で完了させる

### Step 6: 実装者の差分確認

`git status` と `git diff`（未 commit 分を含む）で、Issue scope 外の変更・一時ファイル・秘密情報がないことを確認する。
commit message・報告に auto-close hazard pattern（closing keyword + Issue 番号）を書かない。

### Step 7: commit 前の必須検証

成功するまで commit しない。検証後に編集したら再検証する。

- baseline `clean`: `source .venv/bin/activate && make check`
- baseline `known_failures`: 次の全 PASS と、`--compare` の `verdict: ok` かつ `regressions: []`。空配列や終了コードだけで成功扱いしない

  ```bash
  cd [worktree_dir] && source .venv/bin/activate && \
    ruff check kaji_harness/ tests/ experiments/ && \
    ruff format --check kaji_harness/ tests/ experiments/ && \
    mypy kaji_harness/ && \
    python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --compare
  ```

- 変更に応じた追加検証（docs を変更したら `make verify-docs`、workflow YAML を変更したら `make validate-workflows` 等）

### Step 8: commit

対象 path を明示して stage し、Issue type に対応する Conventional Commits prefix（feat / fix / refactor / test / docs / chore）で
commit する。commit 後に `git status --porcelain` が空であることと HEAD の full SHA を確認する。

### Step 9: 報告

下記「報告」に従い Issue コメント → stdout → `verdict_path` の順に残す。

## 実行手順（fix-change）

change との差分だけを示す。Step 1 と Step 6〜9 は共通。

- 入力は直近の review 系 `RETRY` 報告 1 件。特定できなければ ABORT。指摘ごとに修正するか、根拠を示して反論する。指摘外の改善を混ぜない。
- 「報告 SHA と HEAD の不一致」指摘は、追加 commit の差分を確認し、Step 6〜8 を行って報告に含める。
- 引き継いだ dirty path は「入場時の working tree 判定」で受け入れた場合だけ扱う。
  - Issue scope 内の実装変更（例: レビュー前停止中の未 commit 変更）: 差分確認・検証の上で commit する
  - review の検証が生成・変更した path: 原因（テストや設定が tracked file を書き換える等）を修正したうえで、
    報告で検証由来と特定された path に限り HEAD の内容へ戻す
  - scope 外・意図を判断できない変更: 保全して ABORT
  - どの扱いにしたかを指摘対応表に記録する
- 修正で大きな設計判断が必要と判明したら適用外 ABORT。検証失敗を解消できない場合も ABORT（`fix-change` は `RETRY` を持たない）。
- Step 4 の scope 評価は、修正で変更対象 path が増えた場合だけ増えた path で再実行する。

## 入場時の working tree 判定

dirty tree を無条件に取り込まない。

| 入場の種類 | 入力報告 | working tree が dirty の場合 |
|------------|----------|------------------------------|
| change 初回（自 step の報告なし） | なし | 不明な変更として保全し ABORT |
| change の RETRY 再入 | 直近の自 step `RETRY` 報告 | 下記 3 条件をすべて満たす場合だけ引き継ぐ |
| fix-change | 直近の review 系 `RETRY` 報告 | 下記 3 条件をすべて満たす場合だけ引き継ぐ |

引き継ぐ条件:

1. 現在の HEAD full SHA が入力報告の記載 SHA と一致する
2. 現在の `git status --porcelain` の全 path が入力報告の dirty path 一覧に含まれる
3. 各 path の実際の差分が報告記載の原因（作業途中の実装変更 / レビュー前停止中の未 commit 変更 / 検証による変更 等）と矛盾しない

1 つでも満たさなければ、restore・stash・commit のいずれもせず保全し ABORT する（該当 path と不一致の内容を報告）。
working tree が clean の場合は、入力報告がある入場で記載 SHA と HEAD の一致だけを確認し、不一致なら同様に ABORT する。

## 報告

1 コメントにまとめる。1 行目の verdict marker は `kaji issue comment` が付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step [step_id] --verdict-status <STATUS> --body-file - <<'EOF'
## 小修正 実装報告（[step_id]）

(本文)

---VERDICT---
(verdict block)
---END_VERDICT---
EOF
```

本文の項目:

- change: 方針（Step 3）。fix-change: 指摘対応表（`指摘 N` → 修正 / 反論と根拠 / 引き継いだ dirty path の扱い）
- 対象 commit（full SHA）と変更ファイル要約
- 検証: コマンド・終了状態・pytest 集計・`--evaluate` / `--compare` JSON の要点。成功時はログ全文を貼らず、失敗時は関連部分を引用
- docs 更新、未解決事項
- 引き継ぎ状態: 報告時の HEAD full SHA と working tree（clean、または dirty path ごとの原因）
- ABORT 時: 停止理由（該当条件と根拠）、人間が決める必要のある事項、完了済み / 未完了の作業、branch・worktree path・
  HEAD full SHA・`git status --porcelain` の要約、関連報告の参照、「再実行は初めから」の案内

同じ説明を他工程・PR へ全文複製しない。Issue コメント投稿に失敗した場合は ABORT とし、verdict を stdout と `verdict_path` に残す。

## 副作用の境界

対象 worktree 内の編集・commit と Issue コメント投稿のみ。既存 commit の書き換え（amend / rebase / reset）はしない。
push・PR 作成・merge・label 変更・worktree 作成/削除・Issue 本文編集・session-state 編集・他 workflow 起動はしない。
適用外 ABORT でも worktree・branch・dirty 変更は保全する。

## Verdict 出力

作業報告コメント末尾、stdout、最後に `verdict_path` の pure YAML（delimiter なし）へ同じ内容を残す。

```text
---VERDICT---
status: PASS
reason: |
  方針どおり実装し、必須検証を通して commit した
evidence: |
  HEAD <full SHA>、make check exit 0（pytest N passed）、working tree clean
suggestion: |
---END_VERDICT---
```

| step | status | 条件 |
|------|--------|------|
| change | PASS | 実装・必須検証・commit・報告が完了し、working tree が clean |
| change | RETRY | 新しい session で解消できる実装・検証・報告の失敗が残る（dirty path は原因付きで報告） |
| change | ABORT | 適用外、type ラベル不正、baseline 前提違反・停止、引き継ぎ条件を満たさない dirty tree・HEAD 不一致、provider 障害 |
| fix-change | PASS | 全指摘に対応（修正または反論）し、必須検証・commit・報告が完了し、working tree が clean |
| fix-change | ABORT | 適用外、検証失敗を解消できない、入力の RETRY 報告を特定できない、引き継ぎ条件を満たさない dirty tree・HEAD 不一致、scope 外・意図不明の未 commit 変更、provider 障害 |
