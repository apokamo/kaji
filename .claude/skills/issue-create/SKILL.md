---
description: Issue作成とラベル付与を行う。開発ワークフローの起点。
name: issue-create
---

# Issue Create

GitHub Issueを作成し、適切なラベルを付与します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 新機能・バグ修正・リファクタの着手前 | ✅ 必須 |
| 既存Issueがある場合 | ❌ 不要 |

## 引数

```
$ARGUMENTS = <title> [type] [description]
```

- `title` (必須): Issueタイトル
- `type` (任意): `feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` (デフォルト: feat)
- `description` (任意): 詳細説明。省略時は対話で収集。

## type → ラベル マッピング

| type | ラベル | 用途 |
|------|--------|------|
| `feat` | `enhancement` | 新機能追加 |
| `fix` | `bug` | バグ修正 |
| `refactor` | `refactoring` | リファクタリング |
| `docs` | `documentation` | ドキュメント |

## 実行手順

### Step 1: 引数の解析

`$ARGUMENTS` から `title`, `type`, `description` を取得します。

- `type` が未指定の場合は `feat` をデフォルトとする
- `description` が未指定の場合は、ユーザーに詳細を確認する

### Step 2: Issue本文の作成

以下の構成でIssue本文を作成します:

```markdown
## 概要

(description の内容)

## 目的

(なぜこの変更が必要か)

## 完了条件

- [ ] (達成すべき条件)
```

### Step 3: Issue作成とラベル付与

```bash
gh issue create --title "[title]" --body "[body]" --label "[label]"
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## Issue作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| タイトル | [title] |
| Type | [type] |
| ラベル | [label] |
| URL | [issue-url] |

### 次のステップ

作業を開始するには `/issue-start [issue-number]` を実行してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  Issue 作成成功
evidence: |
  Issue #XX を作成
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Issue 作成成功 |
| ABORT | 作成失敗 |
