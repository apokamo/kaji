---
description: PR前の包括的品質ゲート。全ステップのエビデンス集約、品質チェック実行、設計書の Issue 本文添付
name: i-dev-final-check
---

# I Dev Final Check

PR 作成前の最終品質ゲート。全フェーズのエビデンスを集約し、品質チェックを実行し、設計書を Issue 本文にアーカイブする。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| コードレビュー承認後、PR 作成前 | ✅ 必須 |
| コードレビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: implement → review-code → doc-check → **i-dev-final-check** → i-pr → close

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提条件

- `/issue-implement` → `/issue-review-code` → `/issue-doc-check` が完了していること
- worktree が存在すること

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。

### Step 2: エビデンス集約

Issue コメントから以下のエビデンスを収集・確認する:

| フェーズ | 確認事項 |
|----------|----------|
| 設計 | `/issue-design` 完了コメントが存在すること |
| 設計レビュー | `/issue-review-design` で PASS 判定が出ていること |
| 実装 | `/issue-implement` 完了コメントが存在すること |
| コードレビュー | `/issue-review-code` で PASS 判定が出ていること |
| ドキュメントチェック | `/issue-doc-check` 完了コメントが存在すること |

いずれかが欠けている場合は BACK verdict を出す。

### Step 3: 品質チェック実行

#### 3a. Lint / Format / 型チェック

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && ruff check kaji_harness/ tests/ && ruff format --check kaji_harness/ tests/ && mypy kaji_harness/
```

#### 3b. テスト実行

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest
```

全パス必須（baseline failure がある場合は `issue-implement` の判定基準に従う）。

### Step 4: 設計書の Issue 本文アーカイブ

1. **`draft/design/` の存在確認**:
   ```bash
   WORKTREE_PATH=[worktree-absolute-path]
   ls "$WORKTREE_PATH/draft/design/" 2>/dev/null
   ```

2. **Issue 本文に `## 設計書` セクションが既に存在するか確認**（冪等性）:
   ```bash
   gh issue view [issue-number] --json body -q '.body' | grep -q '^## 設計書'
   ```

3. **既存の場合**: スキップ（冪等）

4. **未存在の場合**: 設計書の内容を読み込み Issue 本文に `<details>` タグで追記:
   ```bash
   CURRENT_BODY=$(gh issue view [issue-number] --json body -q '.body')
   DESIGN_CONTENT=$(cat "$WORKTREE_PATH"/draft/design/issue-[number]-*.md)

   gh issue edit [issue-number] --body "$(cat <<BODY_EOF
   $CURRENT_BODY

   ---

   ## 設計書

   <details>
   <summary>クリックして展開</summary>

   $DESIGN_CONTENT

   </details>
   BODY_EOF
   )"
   ```

5. **追記失敗時のフォールバック**:
   本文サイズ上限超過等で追記に失敗した場合は、Issue **コメント** に設計書全文を投稿し、本文には `## 設計書` セクションとコメントへのリンクのみ追記する。

6. **設計書がない場合**: このステップをスキップ

### Step 5: 結果を Issue にコメント

```bash
gh issue comment [issue-number] --body "$(cat <<'COMMENT_EOF'
## Final Check 結果

### エビデンス確認

| フェーズ | 状態 |
|----------|------|
| 設計 | ✅ / ❌ |
| 設計レビュー | ✅ / ❌ |
| 実装 | ✅ / ❌ |
| コードレビュー | ✅ / ❌ |
| ドキュメントチェック | ✅ / ❌ |

### 品質チェック結果

```
(ruff check + ruff format --check + mypy + pytest の出力)
```

### 設計書アーカイブ

- Issue 本文にアーカイブ済み / スキップ（既存） / なし

### 判定

PASS / RETRY / BACK
COMMENT_EOF
)"
```

### Step 6: 完了報告

```
## Final Check 完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| エビデンス | 全フェーズ確認済み |
| 品質チェック | すべてパス |
| 設計書 | Issue 本文にアーカイブ済み |

### 次のステップ

`/i-pr [issue-number]` で PR を作成してください。
```

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  全フェーズのエビデンス確認・品質チェック全パス・設計書アーカイブ完了
evidence: |
  pytest 全パス、ruff/mypy エラーなし、設計書を Issue 本文にアーカイブ
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全エビデンス確認・品質チェック全パス |
| RETRY | 品質チェック失敗（修正可能） |
| BACK | エビデンス不足（前フェーズに差し戻し） |
| ABORT | 重大な問題 |
