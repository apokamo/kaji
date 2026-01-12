# Git Worktree ガイド

Bare Repository + Worktree パターンによる並列開発環境の構築・運用ガイド。

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

## Python 仮想環境

### 初回セットアップ（main worktree）

```bash
cd /home/user/dev/project-name/main
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 新規 worktree での .venv 共有

新しい worktree を作成したら、main の `.venv` へシンボリックリンクを作成:

```bash
# プロジェクトルートから実行
cd /home/user/dev/project-name
ln -s ../main/.venv ./feature-xxx/.venv
```

これにより `ruff`、`mypy`、`pytest` が即座に実行可能になる。

### 注意事項

⚠️ `.venv` は main のシンボリックリンク:
- `pip install` は main に影響する
- pyproject.toml の依存関係を変更する場合は個別 venv を作成:
  ```bash
  cd ./feature-xxx
  rm .venv
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e ".[dev]"
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

# .venv シンボリックリンク作成
ln -s ../main/.venv ./feature-new-feature/.venv
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

## 運用ルール

### Do

- ディレクトリ移動でブランチ切り替え (`cd ../feature-xxx`)
- worktree管理はプロジェクトルートから実行
- 各worktreeでupstream設定 (`git branch --set-upstream-to=origin/xxx`)

### Don't

- `git checkout` を使わない（ディレクトリ移動で対応）
- プロジェクトルートで一般的なgitコマンドを実行しない

## 参考資料

- [How to use git worktree and in a clean way](https://morgan.cugerone.com/blog/how-to-use-git-worktree-and-in-a-clean-way/)
- [Bare Git Worktrees AGENTS.md](https://gist.github.com/ben-vargas/fd99be9bbce6d485c70442dd939f1a3d)
- [Git Worktree Best Practices and Tools](https://gist.github.com/ChristopherA/4643b2f5e024578606b9cd5d2e6815cc)
- [incident.io: Shipping faster with Claude Code and Git Worktrees](https://incident.io/blog/shipping-faster-with-claude-code-and-git-worktrees)
- [Parallel AI Coding with Git Worktrees](https://docs.agentinterviews.com/blog/parallel-ai-coding-with-gitworktrees/)
