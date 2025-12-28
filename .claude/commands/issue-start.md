---
description: イシュー着手時に使用。ブランチ作成ではなくworktreeで分離された開発環境を構築する
---

# Issue Start

イシュー対応を開始するためのworktreeをセットアップします。

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
- **ディレクトリ**: `../kamo2-[prefix]-[issue-number]` (例: `../kamo2-docs-247`)

## 実行手順

### Step 0: 引数の解析

$ARGUMENTS から issue-number と prefix を取得してください。
- prefix が指定されていない場合は `feat` をデフォルトとする

### Step 1: ブランチとWorktreeの作成

現在のディレクトリ（/home/aki/claude/kamo2）から実行:

```bash
git worktree add -b [prefix]/[issue-number] ../kamo2-[prefix]-[issue-number] main
```

### Step 2: Worktreeの確認

```bash
git worktree list
```

ワークツリーが正しく作成されたことを確認してください。

### Step 3: セットアップ完了報告

以下の形式で報告してください:

```
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| ブランチ | [prefix]/[issue-number] |
| ディレクトリ | ../kamo2-[prefix]-[issue-number] |
| 基点ブランチ | main |

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../kamo2-[prefix]-[issue-number]

### クリーンアップ（作業完了後）

作業が完了したら、以下のコマンドでworktreeを削除できます:

cd /home/aki/claude/kamo2
git worktree remove ../kamo2-[prefix]-[issue-number]
git branch -d [prefix]/[issue-number]  # マージ済みの場合
```
