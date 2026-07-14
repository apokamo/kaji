---
description: review-ready の RETRY 指摘に基づき Issue 本文を修正する。
name: issue-fix-ready
---

# Issue Fix Ready

`issue-review-ready` が RETRY を返した指摘事項に基づき、Issue 本文を修正する。
指摘を盲目的に受け入れるのではなく、妥当性を検討し、修正と反論を使い分ける。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-ready` で RETRY 後 | ✅ 必須 |
| PASS 済みの Issue | ❌ 不要 |

**ワークフロー内の位置**: create → review-ready → (**fix-ready** → review-ready) → start → ...

worktree 不要（メインリポジトリから実行可能）。

## 引数

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. **Issue 本文の取得**:
   ```bash
   kaji issue view [issue_id] --json title,body,labels --jq '{title: .title, body: .body, labels: [.labels[].name]}'
   ```

2. **レビュー指摘の取得**:
   ```bash
   kaji issue view [issue_id] --comments
   ```
   最新の「レディネスレビュー」コメントから RETRY の指摘事項を抽出する。

### Step 2: 対応方針の検討

各指摘事項について **1つずつ** 検討する。

- **A: 修正する (Agree)**
  - 指摘が正しく、Issue 本文の改善につながる場合
  - 不足している 1 次情報を補う: 対象ファイルパス、`docs/` パス、関連 Issue/PR 番号、CLI 出力、ログ等
  - 主観的表現を客観的・検証可能な記述に置き換える

- **B: 反論する (Disagree)**
  - 指摘が誤解に基づいている場合
  - 現状の記述で review-ready の観点を実質的に満たしている場合
  - **必須**: 反論する場合は明確な論理的根拠を用意する

### Step 3: Issue 本文の修正

`kaji issue edit` で Issue 本文を更新する。

```bash
kaji issue edit [issue_id] --commit --body "[updated-body]"
```

**注意事項**:
- 既存の本文構造（概要・目的・完了条件）を維持する
- 指摘に対応する箇所のみ修正する。無関係なセクションは変更しない
- 1 次情報の追加では、推測で埋めず事実確認してから記載する

#### workflow 内判定可能性（観点 14）の修正

通常完了条件に workflow 外の確認が混在しているという指摘は、次の手順で修正する。

1. `docs/dev/workflow_completion_criteria.md` § workflow 内完了条件と事後確認の分離を読む
2. 各項目について、workflow を RETRY して環境非依存で同じ結果を得られるか判定する
3. No の項目だけを `## 完了条件` の末尾サブセクション
   `### ワークフロー完了後の確認項目` へ移す
4. Yes の項目が事後確認欄に誤って置かれていれば通常完了条件へ戻す
5. 事後確認がなければサブセクションを削除するか、チェックボックスではない `- なし` にする

項目の文言とチェック状態は移動時に維持する。通常完了条件と事後確認の両方へ複製しない。

### Step 4: Issue コメント投稿

修正内容と反論を Issue コメントに記録する。

```bash
kaji issue comment [issue_id] --commit --body-file - <<'EOF'
## レディネス指摘への対応報告

### 対応済み

- **[観点名]**: (指摘内容の要約)
  - 修正内容: (どう修正したか)

### 見送り・反論

- **[観点名]**: (指摘内容の要約)
  - 理由: (なぜ対応しなかったか。根拠となるロジック)

### 次のステップ

`/issue-review-ready [issue_id]` で再レビューを実施してください。
EOF
```

### Step 5: 完了報告

以下の形式で報告すること。

```
## レディネス指摘対応完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| 対応済み | N 件 |
| 見送り | M 件 |

### 次のステップ

`/issue-review-ready [issue_id]` で再レビューを実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること。

```
---VERDICT---
status: PASS
reason: |
  指摘対応完了
evidence: |
  全指摘事項に対応済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | Issue 自体が不適切で修正不可能 |
