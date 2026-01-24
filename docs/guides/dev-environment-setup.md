# 開発環境構築ガイド

dev-agent-orchestra の開発環境を Bare Repository パターンで構築する手順書。

## 前提条件

| ツール | 確認コマンド | インストール |
|--------|-------------|-------------|
| Git | `git --version` | 必須 |
| GitHub CLI | `gh --version` | [cli.github.com](https://cli.github.com/) |
| Python 3.12+ | `python3 --version` | 必須 |
| python3-venv | - | `sudo apt install python3.12-venv` |
| git-absorb | `git absorb --version` | `sudo apt install git-absorb` |

GitHub CLI は認証済みであること:
```bash
gh auth status
```

## クイックスタート

以下をコピー＆ペーストで実行:

```bash
# 1. プロジェクトコンテナ作成
mkdir -p ~/dev/dev-agent-orchestra
cd ~/dev/dev-agent-orchestra

# 2. Bare repository としてクローン
gh repo clone apokamo/dev-agent-orchestra .bare -- --bare

# 3. .git ポインタファイル作成
echo "gitdir: ./.bare" > .git

# 4. fetch 設定追加
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"

# 5. main worktree 作成
git worktree add main main

# 6. 追跡ブランチ設定
cd main
git branch --set-upstream-to=origin/main main

# 7. Python 仮想環境セットアップ
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 構築後のディレクトリ構成

```
~/dev/dev-agent-orchestra/          # プロジェクトコンテナ
├── .bare/                          # bare git repository (実データ)
├── .git                            # ポインタファイル → .bare を参照
└── main/                           # worktree (main ブランチ)
    ├── .venv/                      # Python 仮想環境
    ├── src/
    ├── tests/
    ├── docs/
    └── ...
```

## セットアップ確認

```bash
# worktree 確認
git worktree list
# 出力例:
# /home/user/dev/dev-agent-orchestra/.bare  (bare)
# /home/user/dev/dev-agent-orchestra/main   xxxxxxx [main]

# 仮想環境確認
cd ~/dev/dev-agent-orchestra/main
source .venv/bin/activate
which python  # .venv/bin/python であること

# 開発ツール確認
ruff --version
mypy --version
pytest --version
```

## 開発の開始

### main ブランチでの作業

```bash
cd ~/dev/dev-agent-orchestra/main
source .venv/bin/activate
```

### 新規 feature ブランチの作成

```bash
# プロジェクトルートから実行
cd ~/dev/dev-agent-orchestra

# worktree 作成（新規ブランチ）
git worktree add -b feature/xxx ./feature-xxx main

# .venv を共有（シンボリックリンク）
ln -s ../main/.venv ./feature-xxx/.venv

# 作業開始
cd feature-xxx
source .venv/bin/activate
```

### ブランチ間の移動

```bash
# git checkout は使わない
# ディレクトリ移動で切り替え
cd ../main
cd ../feature-xxx
```

## トラブルシューティング

### `python3 -m venv` が失敗する

```
Error: Command 'python3 -m venv .venv' returned non-zero exit status 1.
```

**解決策**: venv パッケージをインストール

```bash
sudo apt install python3.12-venv
```

### `gh repo clone` が認証エラー

```
error: gh auth login required
```

**解決策**: GitHub CLI で認証

```bash
gh auth login
```

### worktree 作成時に "already checked out" エラー

```
fatal: 'main' is already checked out at '/path/to/somewhere'
```

**解決策**: 既存の worktree を確認・削除

```bash
git worktree list
git worktree remove /path/to/somewhere
```

## 参考資料

- [Git Worktree ガイド](git-worktree.md) - Bare Repository パターンの詳細
- [Git コミット戦略](git-commit-flow.md) - コミットワークフロー
