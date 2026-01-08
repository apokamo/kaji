---
description: 設計ドキュメントに対し、汎用的なソフトウェア設計原則に基づいてレビューを行う。
---

# Issue Review Design

実装フェーズに入る前に、設計ドキュメントの品質を検証します。
特定の実装詳細（How）に依存せず、要件（What）、制約（Constraints）、および利用者視点（UX）が明確に定義されているかを確認します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計完了後、実装開始前 | ✅ 必須 |
| 仕様変更時の再レビュー | ⚠️ 推奨 |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 実行手順

### Step 1: 設計情報の取得

1. **Issue本文からWorktree情報を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Worktreeへ移動**

3. **設計書の読み込み**:
   ```bash
   cat draft/design/issue-[number]-*.md
   ```

### Step 2: 設計レビュー基準

以下の汎用的な原則に基づいてレビューしてください。

1. **抽象化と責務の分離 (Abstraction & Scope)**:
   - **What & Why**: 「何を作るか」と「なぜ作るか」が明確か？
   - **No Implementation Details**: 特定の言語やライブラリの内部実装（How）に過度に踏み込んでいないか？（疑似コードはOK）
   - **Constraints**: システムの制約条件（性能、セキュリティ、依存関係）が明記されているか？

2. **インターフェース設計 (Interface Design)**:
   - **Usage Sample**: 利用者が実際に使用する際のコード例やAPI定義が含まれているか？
   - **Idiomatic**: そのインターフェースは、対象言語やプラットフォームの慣習（Idioms）に適合しているか？
   - **Naming**: 直感的で意図が伝わる命名がなされているか？

3. **信頼性とエッジケース (Reliability)**:
   - **Source of Truth**: 外部仕様がある場合、コピペではなく一次情報への参照があるか？
   - **Error Handling**: 正常系だけでなく、異常系（エラー、境界値）の挙動が定義されているか？

4. **検証可能性 (Testability)**:
   - テストケースの羅列ではなく、**「検証すべき観点（What to test）」**が言語化されているか？

### Step 3: レビュー結果のコメント

レビュー結果をGitHub Issueにコメントします。

```bash
gh issue comment [issue-number] --body "..."
```

**コメント本文構成:**

```markdown
# 設計レビュー結果

## 概要

(設計の明確さと、実装着手の可否判定)

## 指摘事項 (Must Fix)

- [ ] **項目**: 指摘内容
  - (要件の欠落、論理的な矛盾、不明確なインターフェースなど)

## 改善提案 (Should Fix)

- **項目**: 提案内容
  - (より良い命名、将来性を考慮した構造の提案など)

## 判定

[ ] Approve (実装着手可)
[ ] Changes Requested (設計修正が必要)
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## 設計レビュー完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-implement [issue-number]` で実装を開始
- Changes Requested: `/issue-fix-design [issue-number]` で修正
```
