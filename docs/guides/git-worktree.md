# Git Worktree ガイド

Bare Repository + Worktree パターンによる並列開発環境の構築・運用ガイド。

> **本ドキュメントの構成**: 前半は汎用的な Bare Repository パターン、後半（「[kaji プロジェクトでの運用](#kaji-プロジェクトでの運用)」）は kaji 固有の通常リポジトリ + worktree パターンを記載している。

## 概要

Git Worktree を使用することで、1つのリポジトリで複数のブランチを同時に作業ディレクトリとして展開できる。これにより：

- **並列開発**: 複数のブランチで同時に作業可能
- **コンテキスト切り替え不要**: `git checkout` なしでディレクトリ移動のみ
- **AI並列開発**: 各worktreeで独立したClaude Codeセッション実行可能

## 推奨構成: Bare Repository パターン

```
/home/user/dev/project-name/        # プロジェクトコンテナ
├── .bare/                          # bare git repository (実データ)
├── .git                            # ポインタファイル → .bare を参照
├── main/                           # worktree (main ブランチ)
├── feature-xxx/                    # worktree (feature-xxx ブランチ)
└── issue-42/                       # worktree (issue-42 ブランチ)
```

### 構成のメリット

| 観点 | メリット |
|------|----------|
| 整理性 | 1リポジトリ = 1ディレクトリ、他リポジトリと混ざらない |
| 分離 | bare repo は純粋なGitデータ、worktree がファイル操作 |
| AI並列開発 | 各worktreeで独立したClaude Codeセッション実行可能 |
| コンテキスト保持 | ブランチごとに会話履歴・状態が保持される |

## セットアップ手順

### 新規リポジトリの場合

```bash
# 1. プロジェクトコンテナ作成
mkdir -p /home/user/dev/project-name
cd /home/user/dev/project-name

# 2. GitHubリポジトリ作成（READMEを含めて初期コミットを作成）
gh repo create username/project-name --public \
  --description "Project description" \
  --add-readme

# 3. bare repository として初期化
git clone --bare git@github.com:username/project-name.git .bare

# 4. .git ポインタファイル作成
echo "gitdir: ./.bare" > .git

# 5. fetch 設定追加
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"

# 6. main worktree 作成
git worktree add main main
```

> **Note**: `--add-readme` オプションで初期コミットが作成される。
> これがないと空リポジトリとなり、`git worktree add main main` が失敗する。

### 既存リポジトリの移行

```bash
# 1. 既存リポジトリをbare形式でクローン
cd /home/user/dev
mkdir project-name
cd project-name
git clone --bare git@github.com:username/project-name.git .bare

# 2. .git ポインタファイル作成
echo "gitdir: ./.bare" > .git

# 3. fetch 設定追加
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"

# 4. main worktree 作成
git worktree add main main
```

## 日常運用

### Worktree の作成

```bash
# プロジェクトルートから実行
cd /home/user/dev/project-name

# 新規ブランチでworktree作成
git worktree add -b feature/new-feature ./feature-new-feature main

# 既存ブランチでworktree作成
git worktree add ./hotfix-123 hotfix/123
```

### Worktree の一覧表示

```bash
git worktree list
```

### Worktree の削除

```bash
# worktreeディレクトリを削除
git worktree remove ./feature-new-feature

# ブランチも削除する場合（マージ済み）
git branch -d feature/new-feature

# ブランチも削除する場合（強制）
git branch -D feature/new-feature
```

### ブランチ切り替え

```bash
# git checkout は使わない
# 代わりにディレクトリ移動
cd ../feature-xxx
```

## kaji プロジェクトでの運用

kaji では Bare Repository パターンではなく、**通常リポジトリ + worktree** パターンを採用している。
Issue ごとに worktree を作成し、並列開発を実現する。

### ディレクトリ構成

```
/home/user/dev/
├── kaji/                           # メインリポジトリ (main ブランチ)
├── kaji-feat-42/                   # worktree (feat/42 ブランチ)
├── kaji-fix-73/                    # worktree (fix/73 ブランチ)
└── kaji-docs-79/                   # worktree (docs/79 ブランチ)
```

### 命名規則

| 項目 | パターン | 例 |
|------|----------|-----|
| ブランチ名 | `[prefix]/[issue-number]` | `feat/42` |
| ディレクトリ | `../kaji-[prefix]-[issue-number]` | `../kaji-feat-42` |

### スキルによる自動化

worktree のライフサイクルはスキルで管理される:

- `/issue-start [issue-number]`: worktree 作成、`.venv` シンボリックリンク、Issue 本文にメタ情報追記
- `/pr-fix [issue-number]`: PR レビュー指摘対応を **同じ worktree** で実行
- `/pr-verify [issue-number]`: 指摘修正の収束確認を行う（新規指摘は禁止）
- `/issue-close [issue-number]`: `.venv` symlink 削除、worktree 削除、ブランチ削除、PR マージ

手動で worktree を削除する場合は、`.venv` シンボリックリンクを先に削除する必要がある（untracked file があると `git worktree remove` が失敗する）:

```bash
rm ../kaji-feat-42/.venv
git worktree remove ../kaji-feat-42
git branch -d feat/42
```

### worktree のスコープ運用ルール

PR 作成は worktree のゴールではなく中間チェックポイントである。`/issue-close` を実行するまでは worktree を残し、PR レビュー指摘対応も同一 worktree 上で完結させる。

- **PR 作成後も worktree は残す**: `/issue-pr` 完了時点では worktree を削除しない。`/issue-close` の実行までが worktree のスコープである
- **PR レビュー指摘対応は同じ worktree で実施**: 別 worktree や `main` ブランチに切り替えず、`/pr-fix` を **同じ worktree 内で** 実行する。これにより branch / venv / artifacts の整合が崩れない
- **`/issue-close` を経由してから削除**: `gh pr merge` を直接叩くのではなく `/issue-close` を経由することで、`.venv` symlink 削除 → worktree 削除 → ブランチ安全削除の順序が保証される

### .venv の共有

各 worktree はメインリポジトリの `.venv` へのシンボリックリンクを使用する:

```bash
ln -s /home/user/dev/kaji/.venv /home/user/dev/kaji-feat-42/.venv
```

> **⚠️ 注意**: `.venv` を共有しているため、worktree 内での `uv pip install` はメインリポジトリの環境にも影響する。`pyproject.toml` の依存関係を変更する場合は、個別の venv を作成して検証すること。

## 運用ルール

### Do

- ディレクトリ移動でブランチ切り替え (`cd ../feature-xxx`)
- worktree管理はプロジェクトルートから実行
- 各worktreeでupstream設定 (`git branch --set-upstream-to=origin/xxx`)

### Don't

- `git checkout` を使わない（ディレクトリ移動で対応）
- プロジェクトルートで一般的なgitコマンドを実行しない（Bare Repository パターンの場合。通常リポジトリでは問題ない）

## 参考資料

- [Git 公式 `git-worktree` マニュアル](https://git-scm.com/docs/git-worktree)
- [How to use git worktree and in a clean way](https://morgan.cugerone.com/blog/how-to-use-git-worktree-and-in-a-clean-way/)
- [Bare Git Worktrees AGENTS.md](https://gist.github.com/ben-vargas/fd99be9bbce6d485c70442dd939f1a3d)
- [Git Worktree Best Practices and Tools](https://gist.github.com/ChristopherA/4643b2f5e024578606b9cd5d2e6815cc)
- [incident.io: Shipping faster with Claude Code and Git Worktrees](https://incident.io/blog/shipping-faster-with-claude-code-and-git-worktrees)
- [Parallel AI Coding with Git Worktrees](https://docs.agentinterviews.com/blog/parallel-ai-coding-with-gitworktrees/)
