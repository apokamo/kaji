# GitLab ミラー初期化ログ

セッション日: 2026-05-08
作業者: apokamo (sa782511r@gmail.com)
作業ホスト: WSL2 (`aki@test`)

## 背景

GitHub が利用不可となったため、kaji リポジトリを GitLab にホストする。

採用方針:
- **Phase 1（本セッションで実施）**: ソースを GitLab にミラー。GitHub の `origin` はそのまま残す
- **Phase 3（後日検討）**: 両方運用 / GitLab メイン化。kaji 開発ワークフロー自体（`gh` 依存スキル群、`.github/workflows/`、`.github/labels.yml`）の移植が必要

## 実施手順

### 1. SSH 鍵の準備

- ローカルに既存鍵 `~/.ssh/id_ed25519` を確認（2026-02-01 生成、コメントは元 LAN ホスト 192.168.11.77 用途）
- パスフレーズ無し
- `ssh-keygen -y -f ~/.ssh/id_ed25519` で公開鍵を導出
- GitLab → User Settings → SSH Keys → Add new key で登録
- 疎通確認: `ssh -T git@gitlab.com` → `Welcome to GitLab, @apokamo!`

### 2. GitLab プロジェクト作成

- `https://gitlab.com/projects/new` から blank project 作成
- Project name: `kaji`、URL: `git@gitlab.com:apokamo/kaji.git`
- **Initialize repository with a README** をチェックしたままにしてしまい、Initial commit が自動生成された（後段で対処）

### 3. リモート追加と初回 push

```bash
git remote add gitlab git@gitlab.com:apokamo/kaji.git
git push -u gitlab main
# → rejected (fetch first): GitLab 側に Initial commit があり履歴が衝突
```

- `git fetch gitlab` で確認 → `df7445d Initial commit` が `README.md` のみ含む孤立履歴と判明
- 価値ゼロのため上書き方針に決定

### 4. 保護ブランチ設定の一時緩和

- 初回 force push が GitLab デフォルトの保護で拒否される
- Settings → Repository → Protected branches → `main` → **Allowed to force push** を ON
- push 完了後、OFF に戻す TODO を残す

### 5. force push 実行

```bash
git push -u gitlab main --force-with-lease
# → forced update: df7445d...a0269ee, branch 'main' set up to track 'gitlab/main'
git push gitlab --tags
# → v0.8.0, v0.9.0, v0.9.1 全て push
```

### 6. 検証

```bash
git ls-remote gitlab
# HEAD → a0269ee (= local main)
# refs/heads/main → a0269ee
# refs/tags/v0.8.0, v0.9.0, v0.9.1
```

## 結果

| 項目 | 値 |
|------|---|
| GitLab URL | `https://gitlab.com/apokamo/kaji` |
| SSH URL | `git@gitlab.com:apokamo/kaji.git` |
| `main` HEAD | `a0269ee` (Merge feat/local-phase4) |
| 同期済みタグ | v0.8.0, v0.9.0, v0.9.1 |
| `main` upstream tracking | `gitlab/main`（`origin/main` から変更） |

## 設計判断と理由

### 既存 SSH 鍵の再利用

公開鍵暗号の性質上、公開鍵を何箇所に登録しても秘密鍵漏洩リスクは不変。「サービス信頼度」と「鍵再利用可否」は本質的に独立。LAN ホスト用途で生成した鍵を GitLab で流用しても安全性は変わらないため、新規生成せず再利用した。

### パスフレーズ無しの維持

kaji の自動化用途（CI / 無人スクリプト / エージェント）でパスフレーズプロンプトは運用を阻害する。代替として以下の前提を満たすことでリスク許容:

- WSL2 ホストのディスク暗号化（BitLocker 等）
- `~/.ssh/` をクラウド同期 / git 管理対象から除外
- 信頼できないリモートに `ssh -A` でログインしない

### force push 採用

GitLab 側 Initial commit は自動生成 README のみで開発履歴ではない。`--force-with-lease` を使い、想定外の更新が入っていた場合の保険を確保。

### upstream を `gitlab/main` に切替

`git push -u gitlab main` の `-u` で main の追跡先が `gitlab/main` に変更された。GitHub 不可の現状では妥当。GitHub 復帰後に origin に戻す場合:

```bash
git branch --set-upstream-to=origin/main main
```

## 残 TODO

1. **GitLab 保護設定戻し**: Settings → Repository → Protected branches → `main` の **Allowed to force push** を **OFF** に戻す
2. **他ローカルブランチの選別 push**: 以下が GitHub にもなくローカルのみ
   - `chore/128`, `docs/107`, `docs/111`, `docs/113`, `feat/124`, `feat/133`, `fix/122`
   - `feat/local-phase3c`, `feat/local-phase3d`, `feat/local-phase3e`, `feat/local-phase4`
   - 必要なものだけ `git push gitlab <branch>` で個別 push
3. **Phase 3 検討開始時の影響範囲洗い出し**:
   - `.claude/skills/` 配下で `gh` を呼ぶスキル群の特定（`grep -r "gh " .claude/skills/`）
   - `.github/workflows/` → `.gitlab-ci.yml` の移植要否評価
   - `.github/labels.yml` の GitLab ラベル化（API か手動か）
   - `kaji_harness/` 内の `gh` / GitHub URL ハードコード箇所の調査

## 参考

- [Git Worktree ガイド](../../../docs/guides/git-worktree.md)
- [Git コミット戦略](../../../docs/guides/git-commit-flow.md)
