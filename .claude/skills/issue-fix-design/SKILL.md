---
description: 設計レビューの指摘事項に基づき、設計ドキュメントを修正または議論する。
name: issue-fix-design
---

# Issue Fix Design

設計レビューで指摘された内容に対し、論理的な妥当性を検討した上で、設計ドキュメントを更新します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-design` で Changes Requested 後 | ✅ 必須 |
| 一次情報の記載を求められた後 | ✅ 必須 |

**ワークフロー内の位置**: design → review-design → (**fix** → verify) → implement

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

2. **レビュー内容の取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   最新の「設計レビュー結果」を取得。

3. **設計書の現状確認**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

### Step 2: 対応方針の検討

各指摘事項について検討します。

#### 一次情報の追記を求められた場合

設計書に「参照情報（Primary Sources）」セクションを追加：

```markdown
## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| (公式ドキュメント名) | (URL) | (設計判断の裏付けとなる引用または要約) |
```

#### その他の指摘事項

- **A: 修正する (Agree)**
  - 指摘により設計がより明確になる、矛盾が解消される場合。

- **B: 反論する/議論する (Discuss)**
  - 指摘が要件定義から逸脱している、実装コストが過大になる場合。
  - 設計には「正解」がないことが多いため、**Rationale（根拠）** を明確にして回答する。

### Step 3: 設計書の更新

指摘を受け入れる場合、設計書を修正します。

### Step 4: コミット

```bash
cd [worktree-absolute-path] && git add draft/design/ && git commit -m "docs: update design for #[issue-number]"
```

### Step 5: 結果報告

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計修正報告

## 対応済み

- **(指摘内容)**
  - 修正: (どのように設計を変更したか)

## 議論/見送り

- **(指摘内容)**
  - 理由: (なぜその設計を維持するのか、トレードオフの説明)

## 次のステップ

`/issue-verify-design [issue-number]` で修正確認をお願いします。
EOF
```

### Step 6: 完了報告

```
## 設計修正完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 対応済み | N 件 |
| 見送り | M 件 |

### 次のステップ

`/issue-verify-design [issue-number]` で修正確認を実施してください。
```
