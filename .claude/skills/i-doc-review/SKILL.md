---
description: docs-only の変更をレビューし、事実整合性・実装整合性・運用整合性の観点から判定する。
name: i-doc-review
---

# I Doc Review

> **重要**: このスキルは更新を行ったセッションとは別のセッションで実行することを推奨します。

> **CRITICAL**: このレビューの目的は文章を整えることではない。現行実装との差異、古い手順、誤誘導となる記述を発見すること。

docs-only の変更をレビューする。新規指摘を行ってよい。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-doc-update` 完了後 | ✅ 必須 |

**ワークフロー内の位置**: update-doc → **review-doc** → (fix-doc → verify-doc) → i-doc-final-check → i-pr

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`） |
| `step_id` | str | 現在のステップ ID |
| `git_remote` | str | git remote 名（`provider.<type>.git_remote` config から解決）。差分範囲の基点 `[git_remote]/[default_branch]` に使用 |
| `default_branch` | str | default branch 名（`provider.<type>.default_branch` config から解決）。差分範囲の基点に使用 |
| `cycle_count` | int | 現在のイテレーション |
| `max_iterations` | int | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から `#<issue_id>` を導出する。

## 前提知識の読み込み

1. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
2. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)

## 実行手順

### Step 1: コンテキストの取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得
2. Issue コメントから直近の docs-only 更新報告を確認
3. 設計書を確認:
   ```bash
   cat [worktree_dir]/draft/design/issue-[issue_id]-*.md
   ```
4. 差分を確認:
   ```bash
   cd [worktree_dir] && git diff [git_remote]/[default_branch]...HEAD
   ```

### Step 2: レビュー

[docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
の 3 観点（事実整合性・実装整合性・運用整合性）で厳格にレビューする。

1. 現行実装と一致しているか
2. CLI コマンド例が現行仕様と一致するか
3. `AGENTS.md` / `CLAUDE.md` の運用方針と矛盾しないか
4. workflow / skill / docs 間で記述が矛盾しないか
5. リンク切れ、古いパス、読者導線の破綻がないか

### Step 3: 変更ファイル限定リンクチェック

変更された Markdown ファイルに絞って以下を実行する。

```bash
cd [worktree_dir] && python3 scripts/check_doc_links.py [changed-markdown-files...]
```

### Step 4: 結果を Issue にコメント

Must Fix / Should Fix を整理して Issue にコメントする。

**verdict マーカーの無条件付与（必須）**: 判定コメントには **常に** `--verdict-step doc-review --verdict-status <STATUS>` を付ける。`<STATUS>` は本 skill が「Verdict 出力」で返す status（`PASS` / `RETRY` / `ABORT`）に置き換える。CLI が body 1 行目に `<!-- kaji-verdict: step=doc-review status=<STATUS> -->` を付け、`kaji issue resolve-verdict --step doc-review` と runner の comment fallback はこの marker で発行元 step を特定する。PASS でも省略しない（条件付きの付与は禁止。[docs/dev/shared_skill_rules.md § verdict マーカー契約](../../../docs/dev/shared_skill_rules.md#verdict-マーカー契約producer-consumer)）。投稿前に判定を確定してから status を決める。末尾の `---VERDICT---` ブロックの status も `<STATUS>` と同じ値にする。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step doc-review --verdict-status <STATUS> \
  --body-file - <<'EOF'
# docs レビュー結果

## 概要

(一言で言うとどうだったか)

## 指摘事項 (Must Fix)

- [ ] **[対象ファイル:行]**: (指摘内容と根拠)

## 改善提案 (Should Fix)

- (任意の改善提案。なければ「なし」)

## リンクチェック結果

(Step 3 の `check_doc_links.py` の出力)

---VERDICT---
status: <STATUS>
reason: |
  (判定理由)
evidence: |
  (具体的根拠)
suggestion: |
  (次のアクション)
---END_VERDICT---
EOF
```

## Verdict 出力

```text
---VERDICT---
status: RETRY
reason: |
  docs の整合性レビューで修正事項が見つかった
evidence: |
  実装との差異、運用方針との不一致、または読者を誤誘導する記述を確認した
suggestion: |
  Issue コメントの指摘に従って `i-doc-fix` で修正すること
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正不要で `i-doc-final-check` へ進める |
| RETRY | docs 修正で解決可能 |
| ABORT | docs-only の範囲を超える重大な問題 |
