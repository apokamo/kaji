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
$ARGUMENTS = <issue-number> [prefix]
```

- `issue-number` (必須): Issue番号 (例: 247)
- `prefix` (任意): ブランチプレフィックス (デフォルト: feat)
  - 例: docs, fix, feat, refactor, test

## 命名規則

- **ブランチ名**: `[prefix]/[issue-number]` (例: `docs/247`)
- **ディレクトリ**: `../kaji-[prefix]-[issue-number]` (例: `../kaji-docs-247`)

## 実行手順

### Step 0: 引数の解析

$ARGUMENTS から issue-number と prefix を取得してください。
- prefix が指定されていない場合は `feat` をデフォルトとする

### Step 1: ブランチとWorktreeの作成

メインリポジトリのルートから実行:

```bash
MAIN_REPO=$(git rev-parse --show-toplevel)
git worktree add -b [prefix]/[issue-number] "$MAIN_REPO/../kaji-[prefix]-[issue-number]" main
```

### Step 1.5: venv シンボリックリンク作成

main プロジェクトの `.venv` へのシンボリックリンクを作成:

```bash
MAIN_REPO=$(git rev-parse --show-toplevel)
ln -s "$MAIN_REPO/.venv" "$MAIN_REPO/../kaji-[prefix]-[issue-number]/.venv"
```

これにより `make check` が即座に実行可能になります。

### Step 2: Worktreeの確認

```bash
git worktree list
```

ワークツリーが正しく作成されたことを確認してください。

### Step 3: Issue本文にメタ情報を追記

Issue本文の先頭にWorktree情報を追記します:

```bash
# 現在のIssue本文を取得
CURRENT_BODY=$(gh issue view [issue-number] --json body -q '.body')

# メタ情報を先頭に追加した新しい本文を作成
NEW_BODY=$(cat <<EOF
> [!NOTE]
> **Worktree**: \`../kaji-[prefix]-[issue-number]\`
> **Branch**: \`[prefix]/[issue-number]\`

$CURRENT_BODY
EOF
)

# Issue本文を更新
gh issue edit [issue-number] --body "$NEW_BODY"
```

### Step 4: セットアップ完了報告

以下の形式で報告してください:

```
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| ブランチ | [prefix]/[issue-number] |
| ディレクトリ | ../kaji-[prefix]-[issue-number] |
| 基点ブランチ | main |
| venv | シンボリックリンク作成済み |
| メタ情報 | Issue本文に追記済み |

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../kaji-[prefix]-[issue-number]

### クリーンアップ（作業完了後）

作業が完了したら `/issue-close [issue-number]` を実行してください。
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
| ABORT | 構築失敗 |
