---
description: docs review の指摘に対応し、ドキュメントのみを修正する。コードやテストは変更しない。
name: i-doc-fix
---

# I Doc Fix

docs review の指摘事項に対応する。このスキルでも **コード、設定、テストは変更しない**。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-doc-review` または `i-doc-verify` が RETRY のとき | ✅ 必須 |

**ワークフロー内の位置**: update-doc → review-doc → **fix-doc** → verify-doc

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`） |
| `step_id` | str | 現在のステップ ID |
| `previous_verdict` | str | 前ステップの verdict |

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

1. `previous_verdict` または Issue コメントから最新レビュー結果を取得
2. Must Fix を 1 件ずつ検討
3. docs のみ修正
4. 実装 / CLI / AGENTS.md / CLAUDE.md / 関連 docs との整合を再確認
5. 修正対象ファイルに絞って以下を実行:
   ```bash
   cd [worktree_dir] && python3 scripts/check_doc_links.py [changed-markdown-files...]
   ```
6. docs のみコミット
7. 対応内容を Issue にコメント

   **verdict マーカーの無条件付与（必須）**: 判定コメントには **常に** `--verdict-step doc-fix --verdict-status <STATUS>` を付ける。`<STATUS>` は本 skill が「Verdict 出力」で返す status（`PASS` / `ABORT`）に置き換える。CLI が body 1 行目に `<!-- kaji-verdict: step=doc-fix status=<STATUS> -->` を付け、`kaji issue resolve-verdict --step doc-fix` と runner の comment fallback はこの marker で発行元 step を特定する。PASS でも省略しない（条件付きの付与は禁止。[docs/dev/shared_skill_rules.md § verdict マーカー契約](../../../docs/dev/shared_skill_rules.md#verdict-マーカー契約producer-consumer)）。投稿前に判定を確定してから status を決める。末尾の `---VERDICT---` ブロックの status も `<STATUS>` と同じ値にする。

   ```bash
   kaji issue comment [issue_id] --commit \
     --verdict-step doc-fix --verdict-status <STATUS> \
     --body-file - <<'EOF'
   # docs レビュー指摘への対応報告

   ## 対応済み

   - **[指摘項目]**: (指摘内容の要約)
     - 修正: (どう修正したか。対象ファイル)

   ## 議論/見送り

   - **[指摘項目]**: (見送った理由と根拠。なければ「なし」)

   ## リンクチェック結果

   (手順 5 の `check_doc_links.py` の出力)

   ## 次のステップ

   `/i-doc-verify [issue_id]` で修正の確認をお願いします。

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
status: PASS
reason: |
  docs review の指摘に対応した
evidence: |
  Must Fix 項目を修正し、関連する docs と実装の整合を再確認した
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | docs 修正だけでは解決不能 |
