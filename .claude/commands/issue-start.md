---
description: イシュー着手時に使用。worktreeで分離された開発環境を構築し、Issue本文にメタ情報を追記する
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

- `issue-number` (必須): Issue番号 (例: 6)
- `prefix` (任意): ブランチプレフィックス (デフォルト: feat)
  - 例: docs, fix, feat, refactor, test

## 命名規則

- **ブランチ名**: `[prefix]/[issue-number]` (例: `docs/6`)
- **ディレクトリ**: `../[prefix]-[issue-number]` (例: `../docs-6`)

## 実行手順

### Step 0: 引数の解析

$ARGUMENTS から issue-number と prefix を取得してください。
- prefix が指定されていない場合は `feat` をデフォルトとする

### Step 1: ブランチとWorktreeの作成

現在のディレクトリ（/home/aki/dev/dev-agent-orchestra/main）から実行:

```bash
git worktree add -b [prefix]/[issue-number] ../[prefix]-[issue-number] main
```

### Step 2: venv シンボリックリンク作成

main の `.venv` へのシンボリックリンクを作成:

```bash
ln -s ../main/.venv ../[prefix]-[issue-number]/.venv
```

これにより `ruff`、`mypy`、`pytest` が即座に実行可能になります。

### Step 3: Worktreeの確認

```bash
git worktree list
```

ワークツリーが正しく作成されたことを確認してください。

### Step 4: Issue本文にメタ情報を追記

Issue本文の先頭にWorktree情報を追記します:

```bash
# 現在のIssue本文を取得
CURRENT_BODY=$(gh issue view [issue-number] --json body -q '.body')

# メタ情報を先頭に追加した新しい本文を作成
NEW_BODY=$(cat <<EOF
> [!NOTE]
> **Worktree**: \`../[prefix]-[issue-number]\`
> **Branch**: \`[prefix]/[issue-number]\`

$CURRENT_BODY
EOF
)

# Issue本文を更新
gh issue edit [issue-number] --body "$NEW_BODY"
```

### Step 5: セットアップ完了報告

以下の形式で報告してください:

```
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| ブランチ | [prefix]/[issue-number] |
| ディレクトリ | ../[prefix]-[issue-number] |
| 基点ブランチ | main |
| .venv | main へのシンボリックリンク |
| メタ情報 | Issue本文に追記済み |

### 注意事項

⚠️ `.venv` は main のシンボリックリンクです:
- `pip install` は main に影響します
- pyproject.toml を変更する場合は個別 venv を作成してください:
  ```bash
  rm .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
  ```

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../[prefix]-[issue-number]

### クリーンアップ（作業完了後）

作業が完了したら `/issue-close [issue-number] [prefix]` を実行してください。
```
