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
| `previous_verdict` | str | `resume` または `inject_verdict: true` 指定ステップ | 前ステップの verdict |
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

1. **テスト規約**: `docs/dev/testing-convention.md`
2. **コーディング規約**: `docs/reference/python/python-style.md`
   - 必要に応じて `docs/reference/python/naming-conventions.md` /
     `type-hints.md` / `docstring-style.md` / `error-handling.md` /
     `logging.md` を追加読込
3. **開発ワークフロー**: `docs/dev/development_workflow.md`

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

**一次情報の例:**
- 公式ドキュメント（Python、Pydantic、typer 等）
- RFC、仕様書
- ライブラリのソースコード（GitHub URL）
- API仕様書（OpenAPI等）

**根拠の書き方:**
- 引用: 「〜である」（原文ママ）
- 要約: 〜が推奨されている（要約）

**アクセス可能性ルール:**

> レビュワー（agent）がアクセスできない一次情報は使用できません。

| 情報の種類 | 対応方法 |
|------------|----------|
| 公開URL | そのまま記載（推奨） |
| ログイン必須/有償 | ローカルにダウンロードしてリポジトリに配置、または該当箇所を引用 |
| 社内限定/NDA | 使用不可。公開版ドキュメントを探すか、該当箇所のスクリーンショット・引用で代替 |

#### その他の指摘事項

- **A: 修正する (Agree)**
  - 指摘により設計がより明確になる、矛盾が解消される、使い勝手が向上する場合。

- **B: 反論する/議論する (Discuss)**
  - 指摘が要件定義から逸脱している、実装コストが過大になる、あるいは別のトレードオフがある場合。
  - 設計には「正解」がないことが多いため、なぜその設計にしたかの **Rationale（根拠）** を明確にして回答する。

### Step 3: 設計書の更新

指摘を受け入れる場合、設計書を修正します。

### Step 4: コミット

```bash
cd [worktree-absolute-path] && git add draft/design/ && git commit -m "docs: update design for #[issue-number]"
```

### Step 5: 結果報告

Issueにコメントします:

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

以下の形式で報告してください:

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

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  修正完了
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
| ABORT | 修正不可能 |
