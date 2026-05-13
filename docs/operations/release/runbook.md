# Release Runbook (`/release` skill)

kaji のリリース運用 runbook。`/release` skill を使った **CI 非依存 / maintainer 手元実行** のフロー。

- **対象**: リリース担当者（maintainer）
- **前提**: kaji 開発環境セットアップ済み（`uv sync` / `.venv` 有効化可）。`glab` CLI が認証済み（`glab auth status`）
- **方針**: forge CI に依存せず、maintainer が手元で `/release` skill を起動して release を進める。`.gitlab-ci.yml` は作成しない（gl:19 で決定）

## リリースフロー全体像

```
[日常]
releasable な MR を main に merge
  ※ Conventional Commits 厳守（feat: → minor、fix: → patch、BREAKING CHANGE → major）

[リリース時]
maintainer が main worktree で `/release` を起動
    ↓
skill が pre-flight check → version 提案 → CHANGELOG → version bump → make check
    ↓ user 承認
skill が commit + tag → push（main / tag）→ glab release create
    ↓
consumer (kamo2 等) が `uv lock --upgrade-package kaji` で新版取得
```

## リリース担当者の操作

```bash
# main worktree で skill を起動
cd /path/to/kaji  # main worktree（feature branch ではない）
# GitLab を指す remote 名は skill が Step 1 で `git remote -v` の URL から動的解決する
# （hybrid setup では `gitlab`、単独設定では `origin` 等。`.kaji/config.toml` の
#  `provider.gitlab.git_remote` 値と整合する remote 名であることが前提）
git checkout main && git pull --ff-only "$GITLAB_REMOTE" main

# 通常実行
/release

# dry-run（push と Release ページ作成手前まで確認）
/release --dry-run
```

skill 側で以下を guide する:

1. pre-flight check（GitLab remote 解決 / main / clean / sync / glab 認証）
2. 直近 tag からの commit を Conventional Commits で解釈 → 次 version 提案（**user 承認**）
3. CHANGELOG.md の `[Unreleased]` を新 version section に整える（**user 承認**）
4. `pyproject.toml` の version を書き換え → `uv lock` → `make check`
5. `chore(release): vX.Y.Z` で commit + annotated tag
6. `git push --atomic "$GITLAB_REMOTE" main vX.Y.Z`（main と tag を 1 トランザクションで push）
7. `glab release create vX.Y.Z --notes "<CHANGELOG 抜粋>"`

詳細は [`.claude/skills/release/SKILL.md`](../../../.claude/skills/release/SKILL.md) を参照。

## バージョン管理対象

| ファイル | 用途 | 更新主体 |
|---------|------|----------|
| `pyproject.toml` | Python パッケージバージョン（**source of truth**） | `/release` skill が user 承認後に書き換え |
| `CHANGELOG.md` | リリースノート | `/release` skill が user 承認後に追記 |
| `uv.lock` | Python 依存 lockfile | `/release` skill が `uv lock` で同期 |
| git tag `vX.Y.Z` | release marker | `/release` skill が annotated tag を作成 |
| GitLab Release ページ | consumer 向け配布点 | `/release` skill が `glab release create` で公開 |

> **Note**: `kaji_harness/__init__.py` の `__version__` は存在しない。`kaji --version` は `importlib.metadata.version("kaji")` 経由で `pyproject.toml` の値を読む。手動編集は非推奨。

## Conventional Commits とバージョン判定

skill が直近 tag (`git describe --tags --abbrev=0`) 〜 HEAD の commit を解釈する。

| 検出条件 | bump |
|---------|------|
| commit body に `BREAKING CHANGE:` または `<type>!:` 形式 | major |
| `feat:` / `feat(...)` を 1 件以上含む | minor |
| `fix:` / `docs:` / `chore:` / `refactor:` / `test:` 等のみ | patch |
| commit 0 件 | ABORT（release 対象なし） |

判定根拠（どの commit が決定打か）は skill が user に提示し、最終判断は user 承認で決定する。Conventional Commits 違反の commit は判定対象から外れるため、commit message 規約を main merge 時に徹底すること。

> **release 必要性の確認**: 候補 commit が `docs:` / `test:` / `chore:` のみ（`feat:` / `fix:` / BREAKING CHANGE を含まない）の場合、patch bump は SemVer 的には冗長で consumer 側 lockfile を無用に更新させる。skill は Step 2 の user 承認時に「本当に release するか」を明示的に問いかける。release を見送る場合は ABORT を選択し、次の `feat:` / `fix:` を待つ運用が望ましい。

## Dry-run 手順

本番 push 前にローカルで動作確認したい場合:

```bash
/release --dry-run
```

skill が Step 1-5 までを実行し、Step 6 (push) と Step 7 (Release ページ) を **スキップ**。
終了時に skill が以下を提示する:

- 作成された commit と tag の確認方法（`git show vX.Y.Z --stat`）
- rollback 手順（dry-run のみ。本番経路では使わない）
- 本番実行への進み方（dry-run 結果に問題がなければ手で push、または rollback してから `/release` 再実行）

## 緊急時の手動フォールバック

`/release` skill が使えない（claude harness が起動しない等）場合の手動手順。**通常運用では使わない**。

事前に GitLab を指す remote 名を `git remote -v` で確認しておく（hybrid setup では `gitlab`、単独設定では `origin` 等）。以下は `$GITLAB_REMOTE` に解決済み remote 名を入れた前提。

```bash
# 0. GitLab remote を特定（hybrid setup 例: GITLAB_REMOTE=gitlab）
GITLAB_REMOTE=$(git remote -v | awk '/gitlab\.com.*\(push\)/{print $1; exit}')

# 1. main を最新化
git checkout main && git pull --ff-only "$GITLAB_REMOTE" main

# 2. CHANGELOG.md と pyproject.toml の version を手で編集
#    [Unreleased] → [X.Y.Z] - YYYY-MM-DD に整える

# 3. lockfile 同期 + 品質チェック
uv lock
source .venv/bin/activate && make check

# 4. commit + tag
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore(release): vX.Y.Z"
git tag -a vX.Y.Z -m "Release vX.Y.Z"

# 5. push（force push 禁止 / main と tag を atomic に push）
git push --atomic "$GITLAB_REMOTE" main vX.Y.Z

# 6. GitLab Release ページ
glab release create vX.Y.Z --name "vX.Y.Z" --notes "<CHANGELOG 抜粋>"
```

> **絶対禁止**: `git push --force "$GITLAB_REMOTE" main` / tag の force push。tag を上書きすると consumer 側 lockfile が壊れる。

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `/release` が Step 1 で stop（main 以外） | `git checkout main && git pull --ff-only "$GITLAB_REMOTE" main` してから再実行（`GITLAB_REMOTE` は Step 1 で skill が動的解決する remote 名） |
| `/release` が Step 1 で stop（GitLab remote 未発見） | `.kaji/config.toml` の `provider.gitlab.git_remote` と `git remote -v` 出力を確認し、必要なら `git remote add gitlab <gitlab-url>` で remote を追加 |
| Step 1 で working tree dirty | 別 branch / stash で退避してから再実行（skill は破壊操作を一切しない） |
| Step 2 で commit 0 件 ABORT | 前回 tag 以降に release 対象の変更が無い。merge を待つ |
| Step 4 で `make check` 失敗 | release を中断し、修正 commit を main に入れてから再実行 |
| Step 6 で `non-fast-forward` 拒否 | 他者 push で main が進んでいる。`--atomic` のため tag も remote には残らない。`/release` を一度中断 → tag/commit を rollback → `git pull --ff-only` → `/release` 再実行（force push は禁止） |
| Step 6 で 同名 tag 衝突 / 権限拒否で reject | `--atomic` のため main も remote 反映されていない。`git tag -d vX.Y.Z` で local tag を消し、原因確認後に再試行 |
| Step 7 で `glab release create` 失敗 | tag は push 済みなので rollback しない。`glab release create` を再試行、または GitLab UI から Release ページを手動作成 |
| consumer 側で新版が取れない | consumer に `uv lock --upgrade-package kaji` を案内。GitLab の git URL 経由で取得するため、tag が push されていれば取得可能 |

## 過去資産（参考）

`/release` skill 導入前は以下の方式を採用していた。現在は **非運用**。

- **release-please (GitHub Actions)**: `.github/workflows/release-please.yml` / `release-please-lock.yml`。GitHub 運用停止に伴い停止（gl:12 で GitLab CI への移植を検討するも close not-planned → gl:19 で skill 主導に転換）
- **GitHub Release ページ + `gh release create`**: GitHub 運用停止に伴い停止

GitHub workflow ファイル群は今後も削除しない方針（gl:19 非目的に明記）。GitHub 運用を再開する場合は再有効化を検討する。

## 関連

- skill 本体: [`.claude/skills/release/SKILL.md`](../../../.claude/skills/release/SKILL.md)
- 関連 issue: gl:12 (CI 移植、close not-planned) / gl:19 (`/release` skill 新設)
