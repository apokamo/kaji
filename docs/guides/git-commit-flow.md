# Git コミット戦略ガイド

git absorb + `--no-ff` マージによるコミット履歴管理戦略。

## 概要

このワークフローは以下を両立する：

- **意味のあるコミット単位**: 機能・修正ごとにコミット
- **レビュー指摘の自動吸収**: `git absorb` で過去コミットに自動fixup
- **ブランチの可視化**: `--no-ff` マージでブランチの分岐・合流が明確

## なぜこの戦略か

マージ戦略には3つの選択肢がある。それぞれにトレードオフがある：

| 戦略 | コミット履歴 | レビュー修正 | 問題点 |
|------|-------------|-------------|--------|
| squash merge | 1コミットに圧縮 | 痕跡なし | **履歴が消失**。何をどの順で作ったか追えない |
| 通常 merge | 全コミット保持 | 修正コミットが残る | **ノイズ増加**。`fix: review feedback` が散乱 |
| **git absorb + `--no-ff`** | 意味のある単位で保持 | 元コミットに吸収 | ツール導入が必要 |

この戦略は squash と通常 merge の中間を取る：

- **squash のように綺麗**: レビュー修正は元コミットに吸収され、ノイズが残らない
- **通常 merge のように詳細**: 機能・修正ごとのコミット単位が保持される
- **`--no-ff` でブランチ構造も可視化**: いつ分岐し、いつ合流したかが明確

## 推奨ツール

[git-absorb](https://github.com/tummychow/git-absorb) のインストールを推奨する（未インストールの場合、`/issue-pr` スキルでは absorb ステップがスキップされる）:

```bash
# macOS
brew install git-absorb

# Ubuntu/Debian
apt install git-absorb

# Cargo (Rust)
cargo install git-absorb
```

## ワークフロー

### 1. 作業中（意味のある単位でコミット）

```bash
git commit -m "feat: ユーザー認証機能を追加"
git commit -m "feat: ログアウト機能を追加"
git commit -m "fix: 認証トークンの有効期限チェックを修正"
```

機能・修正ごとに意味のあるコミットを作成する。

### 2. レビュー指摘対応（git absorb で自動吸収）

> **⚠️ コミットしない**: 修正はステージのみ。コミットすると `git absorb` で吸収できなくなる。

```bash
# レビュー指摘に対応してファイルを修正
vim src/auth.py

# 修正をステージ（コミットしない！）
git add src/auth.py

# 適切な過去コミットに自動吸収
git absorb --and-rebase
```

`git absorb` は **ステージされた変更のみ** を対象とする。誤ってコミットした場合は「[リカバリー](#リカバリー)」を参照。

### 3. PR作成

```bash
gh pr create --title "feat: 新機能" --body "..."
```

### 4. マージ（ブランチ可視化維持）

#### kaji での運用（PR ベース）

kaji では PR を作成し、GitHub 上で merge commit を用いてマージする:

```bash
# PR 作成（/issue-pr スキルで自動化）
gh pr create --title "feat: 新機能" --body "..."

# マージ（/issue-close スキルで自動化）
gh pr merge --merge --delete-branch
```

`--merge` オプションにより merge commit が作成され、`--no-ff` と同等のブランチ構造が維持される。

#### Git 一般論（ローカルマージ）

PR を使わないプロジェクトでは、ローカルで `--no-ff` マージを行う:

```bash
git switch main
git merge --no-ff feature-branch
git push
```

**重要**: `--no-ff` を必ず使用する。

## なぜ `--no-ff` か

### Fast-forward マージ（デフォルト）

```
main:    A---B---C---D---E  (feature commits absorbed)
```

ブランチの存在が履歴から消える。

### No-fast-forward マージ

```
main:    A---B-----------M
              \         /
feature:       C---D---E
```

ブランチの分岐・合流が `git log --graph` で確認可能。

## コミットメッセージ規約

[Conventional Commits](https://www.conventionalcommits.org/) に従う：

| Prefix | 用途 |
|--------|------|
| feat | 新機能 |
| fix | バグ修正 |
| docs | ドキュメント |
| test | テスト |
| refactor | リファクタリング |
| chore | その他（ビルド、CI等） |

例：
```
feat: ユーザー認証機能を追加
fix: ログイン時のエラーハンドリングを修正
docs: READMEにインストール手順を追加
```

## 禁止事項

- `git merge` のデフォルト（fast-forward）使用
- squash マージ（履歴が失われる）
- main ブランチへの直接コミット

## git absorb の動作原理

1. ステージされた変更を分析
2. 各変更がどの過去のコミットに属するか判定
3. 自動で `fixup!` コミットを作成
4. `--and-rebase` オプションで自動リベース

### 例

```bash
# 3つのコミットがある状態
# commit A: file1.py に関数追加
# commit B: file2.py に関数追加
# commit C: file3.py に関数追加

# file1.py と file2.py を修正
vim file1.py file2.py
git add .
git absorb --and-rebase

# 結果: 修正がそれぞれ commit A, B に吸収される
```

## リカバリー

### 誤ってコミットした場合

レビュー修正を別コミットにしてしまった場合の対処法。

#### 方法1: コミットを取り消してやり直す（推奨）

```bash
# 直前のコミットを取り消し、変更をステージ状態に戻す
git reset --soft HEAD~1

# git absorb で吸収
git absorb --and-rebase
```

#### 方法2: rebase で統合する

既にプッシュ済み、または複数コミットを整理する場合。

```bash
# まず対象コミットの位置を確認
git log --oneline main..HEAD

# インタラクティブrebaseで統合
# ⚠️ '2s/pick/fixup/' の行番号は対象コミットの位置に合わせること
GIT_SEQUENCE_EDITOR="sed -i '2s/pick/fixup/'" git rebase -i main

# force push（プッシュ済みの場合）
# ⚠️ 単独作業ブランチでのみ使用。共同作業中はレビュアーに通知すること
git push --force-with-lease
```

## 参考資料

- [git-absorb GitHub](https://github.com/tummychow/git-absorb)
- [Conventional Commits](https://www.conventionalcommits.org/)
- [GitHub CLI `gh pr merge`](https://cli.github.com/manual/gh_pr_merge)
