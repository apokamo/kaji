---
description: イシュー完了時に使用。設計書アーカイブ・PRマージ・worktree削除・ブランチ削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
設計書がある場合は Issue 本文にアーカイブしてから worktree を削除します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

**ワークフロー内の位置**: implement → review-code → doc-check → pr → **close**

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

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

- `/issue-pr` でPRが作成済みであること
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

### Step 1: Worktree情報の取得

Issue本文からWorktree情報を取得します:

```bash
gh issue view [issue-number] --json body -q '.body'
```

以下の情報を抽出:
- `> **Worktree**: \`../dao-[prefix]-[number]\`` → worktree パス
- `> **Branch**: \`[prefix]/[number]\`` → ブランチ名

### Step 2: メインリポジトリのパスを特定

```bash
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
```

> **注意**: `git rev-parse --show-toplevel` は現在の worktree のルートを返すため、
> worktree 内から実行すると main repo を取得できない。必ず `git worktree list` を使うこと。

worktree 内にいる場合は main repo に移動:

```bash
cd "$MAIN_REPO"
```

### Step 3: 設計書の Issue 本文アーカイブ

worktree 削除前に、設計書を Issue 本文に保存します。

1. **`draft/design/` の存在確認**:
   ```bash
   WORKTREE_PATH=$(realpath "$MAIN_REPO/../dao-[prefix]-[number]")
   ls "$WORKTREE_PATH/draft/design/" 2>/dev/null
   ```

2. **設計書がある場合**:
   - Issue 本文に `## 設計書` セクションが**既に存在するか**確認（冪等性担保）:
     ```bash
     gh issue view [issue-number] --json body -q '.body' | grep -q '^## 設計書'
     ```
   - 既存の場合はスキップ
   - 未存在の場合のみ、設計書の内容を読み込み Issue 本文に `<details>` タグで追記:

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

3. **追記失敗時のフォールバック**:
   本文サイズ上限超過等で追記に失敗した場合は、Issue **コメント** に設計書全文を投稿し、本文には `## 設計書` セクションとコメントへのリンクのみ追記する。

4. **設計書がない場合**: このステップをスキップ

### Step 4: PRのマージ

```bash
gh pr merge [branch-name] --merge --delete-branch
```

マージコミットを作成してブランチ履歴を保持する。

### Step 5: .venv シンボリックリンク削除

worktree 削除前に `.venv` シンボリックリンクを削除（untracked files エラー回避）:

```bash
rm "$WORKTREE_PATH/.venv"
```

### Step 6: worktree削除

```bash
git worktree remove "$WORKTREE_PATH"
```

### Step 7: mainを最新化

```bash
git pull origin main
```

### Step 8: 完了報告

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | #[issue-number] |
| 設計書 | Issue本文にアーカイブ済み / なし |
| PR | マージ済み |
| .venv symlink | 削除済み |
| worktree | 削除済み |
| リモートブランチ | 削除済み (--delete-branch) |
| main | 最新化済み |
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS | RETRY | ABORT
reason: |
  (判定理由)
evidence: |
  (具体的根拠)
suggestion: |
  (ABORT/RETRY時は必須)
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| RETRY | マージ失敗等 |
| ABORT | 重大な問題 |
