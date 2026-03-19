---
description: レビュー指摘事項に対し、技術的妥当性を検討した上で修正対応（または反論）を行う
name: issue-fix-code
---

# Issue Fix Code

実装に対するレビュー指摘事項に基づき、修正対応を行います。
指摘を盲目的に受け入れるのではなく、技術的な妥当性を検討し、必要な修正と反論を使い分けます。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` で Changes Requested 後 | ✅ 必須 |
| 人間からのレビューコメントへの対応 | ✅ 使用可 |

**ワークフロー内の位置**: implement → review-code → (**fix** → verify) → doc-check → pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `previous_verdict` | str | resume 指定ステップのみ | 前ステップの verdict |
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/workflow_feature_development.md`
2. **テスト規約**: `docs/dev/testing-convention.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

2. **レビュー結果の取得**:
   1. コンテキスト変数 `previous_verdict` が存在する場合はそれを確認（ハーネス経由）
   2. 存在しない場合は Issue コメントから最新のレビュー結果を取得（手動実行時）

3. **レビュー内容の取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   最新の「コードレビュー結果」を取得。

4. **現状把握**:
   指摘されている該当コード周辺を確認。

### Step 2: 対応方針の検討

各指摘事項について、以下の基準で**1つずつ**検討してください。

- **A: 対応する (Agree)**
  - 指摘が正しく、修正により品質・安全性が向上する場合。
  - 改善提案 (Should Fix) の場合: メリットが明確なら積極的に採用

- **B: 対応しない/反論する (Disagree/Discuss)**
  - 指摘が誤解に基づいている場合
  - 修正による副作用やコストがメリットを上回る場合
  - CLAUDE.md の方針や既存の設計思想と矛盾する場合
  - **必須**: 反論する場合は、明確な論理的根拠を用意

### Step 3: 修正の実行

1. **コード修正**: 採用した指摘事項に基づきコードを修正

2. **品質チェック（コミット前必須）**:

   以下を実行し、**すべてパスするまでコミットしてはならない**。失敗した場合は原因を修正して再実行すること。

   CLAUDE.md の「Pre-Commit (REQUIRED)」セクションに記載されたコマンドを実行すること。

### Step 4: コミット

```bash
cd [worktree-absolute-path] && git add . && git commit -m "fix: address review feedback for #[issue-number]"
```

### Step 5: 結果報告

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
# レビュー指摘への対応報告

レビューありがとうございます。以下の通り検討・対応を行いました。

## 対応済み

- **(指摘内容の要約)**
  - 修正内容: (どう修正したか、ファイル名など)

## 見送り・反論

- **(指摘内容の要約)**
  - 理由: (なぜ対応しなかったか。根拠となるロジック)

## 次のステップ

`/issue-verify-code [issue-number]` で修正確認をお願いします。
EOF
)"
```

### Step 6: 完了報告

```
## コード修正完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 対応済み | N 件 |
| 見送り | M 件 |

### 次のステップ

`/issue-verify-code [issue-number]` で修正確認を実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  修正完了
evidence: |
  全指摘事項に対応済み
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | 修正不可能 |
