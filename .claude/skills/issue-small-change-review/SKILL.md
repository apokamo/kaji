---
description: dev-small workflow の独立レビュー・最終確認工程。実装 session と別 context で実 diff と受け入れ条件を確認し、自身で品質検証を実行して PR 前提と完了条件を判定する。tracked file は編集しない。
name: issue-small-change-review
---

# Issue Small Change Review

dev-small（`.kaji/wf/custom/dev/dev-small.yaml`）の `review-change` / `verify-change` step。
実装した session と同じ context ではレビューしない（workflow では別 session で起動される）。
Issue の決定事項・受け入れ条件と実 diff を照合し、HEAD に対して自分で品質検証を 1 回実行し、
PR 前提と完了条件を確認して判定する。修正が必要なら実装側（`fix-change`）へ戻す。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| dev-small の `review-change`（`change` PASS 後）/ `verify-change`（`fix-change` PASS 後） | ✅ |
| 標準 dev のコードレビュー | ❌ `/issue-review-code` |
| PR 上のレビュー | ❌ `/review` / `/pr-verify` |

**ワークフロー内の位置**: change → **review-change** → (fix-change → **verify-change**) → pr → review-poll → close

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 用途 |
|------|------|
| `issue_id` / `issue_ref` / `step_id` / `verdict_path` | 対象 Issue、phase（`review-change` / `verify-change`）、verdict 保存先 |
| `worktree_dir` / `branch_name` / `default_branch` | レビュー対象 |
| `cycle_count` / `max_iterations` | 参照可 |

`design_path` は注入されても使わない。

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

`worktree_dir` が無い場合だけ [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) で絶対パスを得る。
`step_id` が無い場合は verdict marker から phase を決める: 最新 `fix-change` `PASS` が最新の review 系報告より新しければ
`verify-change`、それ以外は `review-change`（`kaji issue resolve-verdict [issue_id] --step <step>` の `created_at` を比較）。

以降の `[worktree_dir]` は注入値または解決した絶対パス。Bash は毎回 `cd [worktree_dir] && ...` で実行する。

## 読み込み条件

| 区分 | 読むもの | 条件 |
|------|----------|------|
| 通常 | Issue 本文・labels（`kaji issue view [issue_id] --json title,body,labels`） | 常時 |
| 通常 | 最新の `change` `PASS` 報告 | review-change |
| 通常 | 最新の `fix-change` `PASS` 報告と、それが対応した直前の最新 review 系 `RETRY` 報告 | verify-change |
| 通常 | `git diff [default_branch]...HEAD` と変更ファイル実体 | review-change |
| 通常 | `git diff <全体レビュー済み SHA>..HEAD` と変更ファイル実体 | verify-change（起点は下記） |
| 通常 | 自身の検証出力（成功時は終了状態・集計・JSON） | 常時 |
| 例外 | `docs/dev/baseline-check.md` | `known_failures` / baseline 不整合 |
| 例外 | 失敗した検証の関連ログ全文 | 検証失敗時 |
| 例外 | `docs/dev/testing-convention.md` / `docs/reference/python/*.md` 該当節 | テストの妥当性・規約逸脱の判断が必要な場合 |
| 例外 | `docs/dev/shared_skill_rules.md` § auto close keyword 回避 | hazard pattern の判定に迷う場合 |
| 例外 | `docs/dev/workflow_completion_criteria.md` § 本文にチェックボックスがない場合 | 完了条件がチェックボックス形式でない場合 |
| 例外 | [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) | 無関係な問題を発見した場合 |

設計書、Pre-Handoff Review 報告、final-check 報告は読まず、要求もしない。Issue の全コメント履歴を無条件に読み直さない。

入力報告の取得（1 行目の verdict marker を厳密照合し最新 1 件）:

```bash
# review-change: 最新の change PASS
kaji issue view [issue_id] --json comments \
  | jq -r '[.comments[] | select(.body | test("^<!-- kaji-verdict: step=change status=PASS -->"))] | last | .body'
# verify-change: 最新の fix-change PASS と、最新の review 系報告（RETRY 指摘と全体レビュー済み範囲）
kaji issue view [issue_id] --json comments \
  | jq -r '[.comments[] | select(.body | test("^<!-- kaji-verdict: step=fix-change status=PASS -->"))] | last | .body'
kaji issue view [issue_id] --json comments \
  | jq -r '[.comments[] | select(.body | test("^<!-- kaji-verdict: step=(review-change|verify-change) status=RETRY -->"))] | last | .body'
```

## 実行手順（review-change）

### Step 1: 前提確認

- `type:*` ラベルがちょうど 1 件であること。0 件・複数・`type:docs` は ABORT。
- 入力報告（最新 `step=change status=PASS` marker）が無ければ ABORT。

### Step 2: 状態確認

- HEAD full SHA を記録する。入力報告の SHA と一致しなければ、追加 commit の範囲を指摘に含める。
- `git status --porcelain` が空でなければ、path ごとの差分を読んで原因（レビュー前停止中の未 commit 変更 / 原因不明 等）を
  指摘に記載する。ファイルは触らない。
- どちらの場合もレビューを打ち切らず、Step 3 の全体レビューを commit 済みの `[default_branch]...HEAD` に対して行う
  （未 commit 変更はレビュー対象外として指摘に残す）。

### Step 3: レビュー観点（`[default_branch]...HEAD` の全体）

| 観点 | 確認内容 |
|------|----------|
| (a) 要件充足 | Issue の決定事項・受け入れ条件・不変条件を満たす |
| (b) Scope 混在 | Issue スコープ外・type 責任範囲外・無関係なついで修正がない |
| (c) 検証の妥当性 | bug の回帰テスト、feature の新挙動テスト、refactor の振る舞い非変更、docs / metadata-only の変更固有検証 |
| (d) docs 整合 | 挙動・運用の変更に対応する docs が更新されている |
| (e) auto-close 規約 | commit message・追加 / 変更ファイル・報告に hazard pattern（closing keyword + Issue 番号）がない |
| (f) 適用条件の維持 | 大きな設計判断（公開互換性・権限境界・データ移行等）が現れていない |
| (g) 報告と実物の対応 | 報告の SHA・変更ファイル・検証内容が実物と一致する |

(b)(e) は標準 dev の Pre-Handoff Review 観点をこの独立レビューへ集約したもの。

hazard pattern の機械確認例:

```bash
cd [worktree_dir] && git log [default_branch]..HEAD --format='%B' | \
  grep -iE '\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*:?\s*#[0-9]'
```

### Step 4: 自身の品質検証（HEAD に対して 1 回）

Step 2 で working tree が dirty だった場合は、結果が HEAD に対応しないため実行せず「未実施（dirty tree）」と記録する
（PASS にはならない。次の verify-change が HEAD で実行する）。

1. scope 評価: `git diff --name-only [default_branch]...HEAD` の各 path を `--scope` に繰り返し渡す。`--worktree` を必ず渡す。

   ```bash
   cd [worktree_dir] && source .venv/bin/activate && \
     python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] \
       --evaluate --scope <path1> --scope <path2>
   ```

   `missing_baseline` / `stale_baseline` / `blocked` / `invalid`、または `known_failures` で `stop: true` か意味的関連がある → ABORT。
2. gate:
   - `clean`: `source .venv/bin/activate && make check`
   - `known_failures`: `ruff check kaji_harness/ tests/ experiments/` / `ruff format --check kaji_harness/ tests/ experiments/` /
     `mypy kaji_harness/` の全 PASS と、
     `python -m kaji_harness.scripts.baseline_precheck --worktree [worktree_dir] --compare` の `verdict: ok` かつ `regressions: []`。
     空配列や終了コードだけで成功扱いしない
3. 変更に応じた追加検証（docs 変更時の `make verify-docs`、workflow YAML 変更時の `make validate-workflows` 等）。

失敗は関連ログを引用して RETRY。検証後に `git status --porcelain` を再確認し、tracked file が変化していれば、変化した path と
「検証（コマンド名）による変更」という原因を指摘に記載して保全し RETRY（戻さない・commit しない）。

### Step 5: 判定

- blocking finding がある → RETRY。指摘は `指摘 N` 形式で file:line・根拠・期待する修正を書く。好み・scope 外改善・
  将来の改善だけでは RETRY にしない（非 blocking 所見として記載可）。
- 適用外（大きな設計判断・公開互換性・権限境界等が必要と判明）→ ABORT。
- finding なし →
  1. `### ワークフロー完了後の確認項目` を除く完了条件を HEAD に対して全件照合する。満たせない項目は RETRY、
     軽量経路で満たせない項目は ABORT。
  2. PR 前提: working tree clean、HEAD == 検証した SHA、`[default_branch]` より先行する commit が 1 件以上。
  3. Issue 本文を再取得し、確認済み項目だけ `[x]` に更新する。事後確認項目は `[ ]` のまま残す。失敗は ABORT。

     ```bash
     kaji issue view [issue_id] --json body -q '.body' > <tmp>/issue-body.md
     # 確認済み項目だけ "- [ ]" を "- [x]" に変更する
     kaji issue edit [issue_id] --commit --body-file <tmp>/issue-body.md
     ```

  4. PASS。

### Step 6: 報告

下記「報告」に従い Issue コメント → stdout → `verdict_path` の順に残す。

## 実行手順（verify-change）

review-change との差分だけを示す。Step 1 の入力報告は最新の `fix-change` `PASS` 報告（無ければ ABORT）。Step 2・4〜6 は共通。

- 確認範囲の起点: 直近の review 系報告に記録された **全体レビュー済み SHA** を使う。
  記録が無い（初回の全体レビューが未完了）、またはその SHA が HEAD の ancestor でない
  （`git merge-base --is-ancestor <SHA> HEAD` が非 0）場合は、Step 3 の全体レビューを `[default_branch]...HEAD` に対して
  review-change と同じ規則（新規指摘可）で行い、完了を報告に記録する。
- 全体レビュー済み SHA がある場合の確認範囲は、直前 RETRY 報告の各指摘の解消（または反論の妥当性）と、
  `git diff <全体レビュー済み SHA>..HEAD` の修正影響（回帰・scope・auto-close 規約）。解消済み指摘を再開しない。
  新たに RETRY にしてよいのは、修正差分起因の問題・HEAD での検証失敗・HEAD で未充足の完了条件・報告と実物の不一致・
  working tree の dirty に限る（レビューサイクル収束のため）。
- 報告の全体レビュー済み範囲は、確認を終えた HEAD に更新する。

## 報告

1 コメントにまとめる。1 行目の verdict marker は `kaji issue comment` が付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step [step_id] --verdict-status <STATUS> --body-file - <<'EOF'
## 小修正 レビュー結果（[step_id]）

(本文)

---VERDICT---
(verdict block)
---END_VERDICT---
EOF
```

本文の項目:

- レビュー対象 full SHA、入力報告の参照（step・created_at）、判定
- 指摘（`指摘 N`: file:line・根拠・期待する修正）または所見。verify-change は前回指摘ごとの解消状況
- 自身の検証: コマンド・終了状態・集計・`--evaluate` / `--compare` JSON の要点、または未実施とその理由
- 完了条件の照合結果と本文更新結果、PR 前提
- **全体レビュー済み範囲**: `[default_branch]...<SHA>` の全体レビューを完了した SHA。完了していなければ「未完了」と未実施の範囲・理由
- 引き継ぎ状態: 報告時の HEAD full SHA と working tree（clean、または dirty path ごとの原因）
- ABORT 時: 停止理由（該当条件と根拠）、人間が決める必要のある事項、branch・worktree path・HEAD full SHA・
  `git status --porcelain` の要約、関連報告の参照、「再実行は初めから」の案内

成功時の検証ログ全文は貼らない。Issue コメント投稿に失敗した場合は ABORT とし、verdict を stdout と `verdict_path` に残す。

## 副作用の境界

Issue コメント投稿と、`PASS` 時の Issue 本文チェックボックス更新のみ。tracked file の編集・commit・push・PR 作成・merge・
label 変更・worktree 作成/削除・session-state 編集・他 workflow 起動はしない。人間レビューはこの工程の検証・判定を代替しない。

## Verdict 出力

作業報告コメント末尾、stdout、最後に `verdict_path` の pure YAML（delimiter なし）へ同じ内容を残す。

```text
---VERDICT---
status: PASS
reason: |
  全体レビューで finding なし、HEAD で自身の検証が成功し、完了条件を確認した
evidence: |
  HEAD <full SHA>、make check exit 0（pytest N passed）、完了条件 N 件を [x] に更新
suggestion: |
---END_VERDICT---
```

| status | 条件（review-change / verify-change 共通） |
|--------|------|
| PASS | `[default_branch]...HEAD` の全体レビューが完了済み、finding なし、working tree clean、HEAD で自身の検証が成功、完了条件を全件確認し本文更新済み、PR 前提を満たす |
| RETRY | 具体的な修正を要する blocking finding（未 commit 変更・SHA 不一致・検証失敗・検証による tracked file の変化・未充足の完了条件を含む） |
| ABORT | 適用外、入力報告の欠落、baseline 停止基準、type ラベル不正、Issue 本文更新・コメント投稿の失敗 |
