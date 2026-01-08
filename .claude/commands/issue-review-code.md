---
description: 実装完了後の成果物に対し、設計整合性とコード品質の観点から厳格なレビューを実施する
---

# Issue Review Code

実装コードに対して、設計書を基に厳格なコードレビューを実施します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 実装完了後、PR作成/マージ前 | ✅ 必須 |
| 実装途中 | ⚠️ 任意（中間レビューとして） |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 実行手順

### Step 1: コンテキストの取得

1. **Issue本文からWorktree情報を取得して移動**

2. **設計情報の取得**:
   ```bash
   cat draft/design/issue-[number]-*.md
   ```

3. **実装サマリーの取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   直近の「実装完了報告」を確認。

4. **実装差分の取得**:
   ```bash
   git diff main...HEAD
   ```
   変更内容を把握。差分が大きい場合は主要ファイルを個別に確認。

### Step 2: コードレビューの実施

以下の観点で厳格なレビューを行ってください。

1. **設計との整合性**:
   - 設計書の要件を完全に満たしているか？
   - 勝手な仕様変更や、未実装の機能はないか？

2. **安全性と堅牢性**:
   - エラーハンドリングは適切か？（握りつぶし、汎用Exceptionの禁止）
   - 境界値（Boundary Value）やNull安全性の考慮はあるか？

3. **コード品質**:
   - 型ヒントは具体的か？ (`Any` の乱用禁止)
   - 命名は適切で説明的か？
   - CLAUDE.md のコーディング規約に準拠しているか？

4. **テスト**:
   - 追加された機能に対するテストは十分か？
   - 設計書の「検証観点」がカバーされているか？

### Step 3: レビュー結果のコメント投稿

```bash
gh issue comment [issue-number] --body "..."
```

**コメント本文構成:**

```markdown
# コードレビュー結果

## 概要

(一言で言うとどうだったか)

## 指摘事項 (Must Fix)

- [ ] **ファイル名:行数**: 具体的な指摘内容
- [ ] ...

## 改善提案 (Should Fix)

- **ファイル名**: より良い実装パターンの提案

## 良い点

- (特筆すべき良い実装があれば記載)

## 判定

[ ] Approve (修正なしでマージ可)
[ ] Changes Requested (要修正)
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## コードレビュー完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |
| Must Fix | N 件 |
| Should Fix | M 件 |

### 次のステップ

- Approve: `/issue-pr [issue-number]` でPR作成
- Changes Requested: `/issue-fix-code [issue-number]` で修正
```
