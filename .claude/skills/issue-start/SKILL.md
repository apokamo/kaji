---
description: イシュー着手時に使用。worktreeで分離された開発環境を構築し、Issue本文にメタ情報を追記する
name: issue-start
---

# Issue Start

イシュー対応を開始するためのworktreeをセットアップし、Issue本文にメタ情報を追記します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| コード/ドキュメント変更を伴うイシュー着手 | ✅ 必須 |
| 設計のみ（ファイル変更なし） | ⚠️ 任意 |
| 調査・リサーチのみ | ❌ 不要 |

**重要**: PRを作成する際やイシュー対応でコミットが必要な場合、`git branch` ではなくこのスキルを使用してください。

## 引数

```
$ARGUMENTS = <issue_id>
```

- `issue_id` (必須): Issue番号 (例: 247 / `local-pc1-3` / `gh:153`)

第 2 引数は **廃止** されました（issue local-pc5090-17）。ブランチ prefix は
`kaji issue context` が返す `branch_prefix`（frontmatter `branch_prefix` →
`type:*` ラベル → `chore` fallback の優先順）から自動決定します。

## 命名規則

`kaji issue context <issue_id>` の出力（`provider.resolve_issue_context()` が正本）から
取得する `branch_name` / `worktree_dir` をそのまま使います。

- **ブランチ名**: `<branch_prefix>/<issue_id>` (例: `fix/247`)
- **ディレクトリ**: `<repo_root>/../kaji-<branch_prefix>-<issue_id>` (例: `../kaji-fix-247`)

## 実行手順

### Step 0: 引数の検査

`$ARGUMENTS` から `issue_id` を取得してください。第 2 引数（旧 `prefix`）が
渡された場合は **ABORT** verdict を出して停止し、廃止アナウンスをユーザに返してください
（label / frontmatter からの自動導出に一本化されているため）。

### Step 1: context 正本の取得

```bash
CTX=$(kaji issue context [issue_id] --json branch_prefix,branch_name,worktree_dir)
PREFIX=$(echo "$CTX" | jq -r '.branch_prefix')
BRANCH=$(echo "$CTX" | jq -r '.branch_name')
WT=$(echo "$CTX" | jq -r '.worktree_dir')
```

`worktree_dir` は絶対パスで返ります。以降の手順では上記 3 変数を使います。

### Step 2: ブランチとWorktreeの作成

メインリポジトリのルートから実行:

```bash
MAIN_REPO=$(git rev-parse --show-toplevel)
git worktree add -b "$BRANCH" "$WT" main
```

### Step 2.5: venv シンボリックリンク作成

main プロジェクトの `.venv` へのシンボリックリンクを作成:

```bash
ln -s "$MAIN_REPO/.venv" "$WT/.venv"
```

これにより `make check` が即座に実行可能になります。

### Step 3: Worktreeの確認

```bash
git worktree list
```

ワークツリーが正しく作成されたことを確認してください。

### Step 4: Issue本文にメタ情報を追記

Issue本文の先頭にWorktree情報を追記します:

```bash
# 現在のIssue本文を取得
CURRENT_BODY=$(kaji issue view [issue_id] --json body -q '.body')

# メタ情報を先頭に追加した新しい本文を作成
WT_BASENAME=$(basename "$WT")
NEW_BODY=$(cat <<EOF
> [!NOTE]
> **Worktree**: \`../$WT_BASENAME\`
> **Branch**: \`$BRANCH\`

$CURRENT_BODY
EOF
)

# Issue本文を更新
kaji issue edit [issue_id] --commit --body "$NEW_BODY"
```

### Step 5: セットアップ完了報告

以下の形式で報告してください（`$PREFIX` / `$BRANCH` / `$WT` の値で埋めること）:

```
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| ブランチ | $BRANCH |
| ディレクトリ | ../$(basename "$WT") |
| 基点ブランチ | main |
| venv | シンボリックリンク作成済み |
| メタ情報 | Issue本文に追記済み |

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../$(basename "$WT")

### クリーンアップ（作業完了後）

作業が完了したら `/issue-close [issue_id]` を実行してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  Worktree 構築成功
evidence: |
  worktree 作成、venv symlink 済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Worktree 構築成功 |
| ABORT | 構築失敗 / 第 2 引数（旧 `prefix`）が渡された |
