---
description: kaji の release 作業（version bump / CHANGELOG / tag / GitLab Release ページ）を skill で完結させる。CI を使わず maintainer 手元で対話的に進める。
name: release
---

# Release

kaji の release を maintainer の手元で完結させる skill。
forge CI (GitLab CI / GitHub Actions) に依存せず、各 step で user 承認を挟みながら version bump → CHANGELOG → tag → GitLab Release ページ作成まで進める。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| kaji の release（version bump + tag + Release ページ）を切る | ✅ 必須 |
| dry-run（push / Release ページ作成手前まで確認したい） | ✅ `--dry-run` 経路 |
| PyPI publish / dist build | ❌ 対象外（現運用で未使用） |
| `.github/workflows/release-please.yml` の再有効化 | ❌ 対象外（GitHub 運用は停止中、本 skill は GitLab 前提） |

## 入力

```
/release            # 通常実行（push と Release ページ作成まで進める）
/release --dry-run  # ローカル状態のみ更新（push / glab release create はスキップ）
```

引数なしで起動し、skill 側で git / pyproject の状態を読んで判定する。

## 前提

- 実行場所: kaji 本体の **main worktree** で実行する（release branch は切らない、main 直接 update する運用）
- `gh` ではなく **`glab`** CLI を使う（GitLab 運用）
- GitLab を指す git remote が 1 つ存在すること（remote 名は `provider.gitlab.git_remote` config に従う。hybrid setup では `origin`=GitHub, `gitlab`=GitLab の構成になる）。Step 1 で動的に解決する
- 解決した GitLab remote に対して `git push` できる権限を maintainer が持っていること
- `uv` / `make check` が走る環境（kaji 開発環境セットアップ済み）

## 実行手順

### Step 1: Pre-flight check

以下を順に確認する。1 つでも失敗した時点で停止し、user に復旧手順を提示する。

```bash
# 1-0. GitLab remote 名を解決（hybrid setup 対応）
#      `provider.gitlab.git_remote` config が解決元。手動実行では
#      git remote から gitlab.com を指すものを動的抽出する。
GITLAB_REMOTE=$(git remote -v | awk '/gitlab\.com.*\(push\)/{print $1; exit}')
# 上記で見つからない場合、`origin` が GitLab を指していれば fallback
if [ -z "$GITLAB_REMOTE" ]; then
    if git remote get-url origin 2>/dev/null | grep -q gitlab; then
        GITLAB_REMOTE=origin
    else
        echo "ABORT: no git remote pointing to gitlab.com found"
        exit 1
    fi
fi
echo "Resolved GitLab remote: $GITLAB_REMOTE"

# 1-1. main checkout 状態
git rev-parse --abbrev-ref HEAD   # → "main" であること

# 1-2. working tree clean
git status --porcelain            # → 空であること

# 1-3. 上流 sync
git fetch "$GITLAB_REMOTE"
git rev-list --left-right --count "$GITLAB_REMOTE/main"...HEAD
# → "0\t0"（ahead/behind ともに 0）であること

# 1-4. 解決した remote が GitLab を指していることを再確認
git remote get-url "$GITLAB_REMOTE"   # → gitlab.* を含むこと

# 1-5. glab CLI 認証済み
glab auth status                      # → "Logged in" 表示
```

**失敗時のガイド例**:

- GitLab remote 未発見 → `.kaji/config.toml` の `provider.gitlab.git_remote` および `git remote -v` 出力を user に提示し、追加方法を相談（`git remote add gitlab <gitlab-url>`）
- main 以外 → `git checkout main && git pull --ff-only "$GITLAB_REMOTE" main`
- dirty tree → user に commit / stash を促す（skill 側では一切触らない）
- behind → `git pull --ff-only "$GITLAB_REMOTE" main`
- ahead → 未 push commit がある旨を user に伝え、release に含めるべきか確認
- remote 不一致 → 別 remote (`upstream` 等) を見るべきか user に確認

### Step 2: Next version の提案

```bash
# 直近 tag を取得
LAST_TAG=$(git describe --tags --abbrev=0)

# tag 以降の commit を取得（マージ commit 含む / メッセージ全文）
git log "${LAST_TAG}..HEAD" --pretty=format:'%H%n%B%n---END---'
```

取得した commit list を Conventional Commits で分類し、以下のルールで bump 種別を判定する。

| 検出 | bump |
|------|------|
| commit message body に `BREAKING CHANGE:` 行 / footer / `<type>!:` 形式 | major |
| `feat:` / `feat(...)` が 1 件以上 | minor |
| `fix:` / `fix(...)` / その他 (`docs:` / `chore:` / `refactor:` / `test:`) のみ | patch |
| commit が 0 件（tag 以降変更なし） | **ABORT** — release する変更が無い |

判定根拠（どの commit が major / minor の決定打になったか）を必ず user に提示する。

**user 承認待ち**: 提案 version (例: `v0.9.1 → v0.10.0`) と判定根拠を出力し、user の承認を待つ。
user が異なる version を希望する場合はそちらを採用する（初期運用では Conventional Commits 解釈ミスを警戒し、必ず承認を挟む）。

### Step 3: CHANGELOG 生成

`CHANGELOG.md` の `## [Unreleased]` セクションを基に、新 version のエントリを作成する。

```markdown
## [X.Y.Z] - YYYY-MM-DD

### BREAKING CHANGE
- ...

### Added
- feat: ... entries

### Fixed
- fix: ... entries

### Changed / Docs / Internal
- その他 (docs / refactor / chore / test) entries
```

進め方:

1. `## [Unreleased]` 配下の既存記述を新 version セクションへ移動
2. 不足分（Unreleased に未記載の commit）があれば commit message から要約を追記
3. 末尾の比較リンク（存在する場合）を更新
4. `CHANGELOG.md` の **diff を user に提示** して承認待ち

**user 承認待ち**: user が文面修正を希望する場合は再 edit → 再提示。

### Step 4: Version bump

```bash
# 4-1. pyproject.toml の version を編集（Edit tool 経由で 1 箇所のみ書き換え）
#      version = "X.Y.Z"

# 4-2. lockfile 同期
uv lock

# 4-3. 品質チェック
source .venv/bin/activate && make check
```

`make check` が失敗した場合は **Step 5 に進まず停止**。原因を user に提示し、修正方針を相談する。

### Step 5: Commit と tag

```bash
# 5-1. ステージング
git add pyproject.toml uv.lock CHANGELOG.md

# 5-2. release commit
git commit -m "chore(release): vX.Y.Z"

# 5-3. tag 付与（annotated tag を推奨）
git tag -a vX.Y.Z -m "Release vX.Y.Z"
```

`--dry-run` 経路の場合、ここまでで停止し、後述の **dry-run 終了処理** を案内する。

### Step 6: Push

Step 1 で解決した `$GITLAB_REMOTE` に対して push する。

```bash
git push "$GITLAB_REMOTE" main
git push "$GITLAB_REMOTE" vX.Y.Z
```

push 失敗時:

- `non-fast-forward` → main が他者 push で進んでいる。**force push は禁止**。一旦 stop し、user に状況を共有 → 必要なら Step 1 から再実行（commit と tag を一度ローカルで rollback、後述）
- tag push のみ失敗 → main は既に push 済み。再試行で push できれば続行。それでも失敗するなら user に glab / GitLab UI で原因確認を依頼

### Step 7: GitLab Release ページ作成

```bash
# CHANGELOG の該当 section を抜粋し、--notes に渡す
glab release create vX.Y.Z \
  --name "vX.Y.Z" \
  --notes "<CHANGELOG.md の [X.Y.Z] section 本文>"
```

完了後、`glab release view vX.Y.Z` で URL を取得し user に提示する。

### Step 8: 完了報告

user に以下を提示して終了:

- 採番した version と tag URL
- GitLab Release ページ URL
- consumer 側に `uv lock --upgrade-package kaji` を案内する一文（kamo2 等の dependency consumer 向け）

## Dry-run 経路（`--dry-run`）

Step 1 → 5 まで実行し、Step 6 (push) と Step 7 (Release ページ) を **スキップ**。

dry-run 終了時に skill が必ず提示する内容:

1. **作成された commit と tag**: `git show vX.Y.Z --stat` の要点
2. **rollback 手順**（dry-run のみ。本番経路では使わない）:
   ```bash
   git tag -d vX.Y.Z
   git reset --hard HEAD~1   # release commit を破棄
   # CHANGELOG.md / pyproject.toml / uv.lock の変更を確認後、必要なら git restore で戻す
   ```
3. **本番実行への進み方**:
   - dry-run の結果に問題がなければ `git push "$GITLAB_REMOTE" main && git push "$GITLAB_REMOTE" vX.Y.Z` を手で実行 → Step 7 を手で実行、
   - または rollback してから `/release`（dry-run なし）で再実行

## 失敗時の rollback 手順（共通）

各 step ごとに、失敗 → 復旧の流れを以下に集約する。

### Step 5 (commit/tag) の途中で失敗

```bash
# tag を作る前に commit が失敗した場合
git restore --staged pyproject.toml uv.lock CHANGELOG.md
git restore pyproject.toml uv.lock CHANGELOG.md

# commit はできたが tag 付与で失敗した場合
git tag -d vX.Y.Z   # 失敗していれば存在しない
git reset --hard HEAD~1
```

### Step 6 (push) で remote 拒否

```bash
# main の push が non-fast-forward で拒否されたら
git fetch "$GITLAB_REMOTE"
git log --oneline "$GITLAB_REMOTE/main"..HEAD       # 自分のローカル commits を確認
git log --oneline HEAD.."$GITLAB_REMOTE/main"       # 他者の commits を確認

# 他者 commits を取り込む必要がある場合（force push 禁止）:
# 1. tag を一旦削除
git tag -d vX.Y.Z
# 2. release commit を一旦巻き戻す
git reset --hard "$GITLAB_REMOTE/main"
# 3. main を最新化してから /release を再実行
git pull --ff-only "$GITLAB_REMOTE" main
```

> **絶対禁止**: `git push --force "$GITLAB_REMOTE" main` / `git push --force "$GITLAB_REMOTE" vX.Y.Z`。
> tag を上書き push (`--force`) すると consumer 側の lockfile / cache 整合が壊れる。tag は不変前提で運用する。

### Step 6 まで成功し Step 7 (Release ページ) で失敗

tag と main commit は既に push 済み。**rollback ではなく Release ページのみ再試行**する:

```bash
glab release view vX.Y.Z 2>/dev/null || \
  glab release create vX.Y.Z --name "vX.Y.Z" --notes "<本文>"
```

それでも作成できない場合、GitLab UI から手動で Release ページを作る選択肢を user に提示する。

### 既に push 済みの release を撤回したい（緊急）

原則として撤回しない。撤回する場合は別 issue で意思決定を残し、以下を user の明示同意付きで行う:

```bash
# tag をリモートから削除
git push "$GITLAB_REMOTE" --delete vX.Y.Z
# Release ページを削除
glab release delete vX.Y.Z
# 必要なら revert commit を main に乗せる（force push はしない）
git revert <release-commit-sha>
git push "$GITLAB_REMOTE" main
```

## ガードレール

- main 以外の branch では起動しない（Step 1 で reject）
- working tree が dirty な状態で commit を作らない
- `git push --force` / tag の force push を skill 側からは絶対に実行しない
- `make check` を pass しないまま Step 5 以降に進まない
- user 承認ポイント（Step 2 version / Step 3 CHANGELOG）を勝手にスキップしない
- 失敗時は停止して user にガイドする（自動 retry / 自動 rollback はしない、commit/tag の rollback は user 承認後に skill が実行可）

## 出力（verdict）

```text
---VERDICT---
status: PASS | ABORT
reason: |
  release を完了した（または dry-run を完了した）
evidence: |
  - 採番 version: vX.Y.Z
  - commit sha: ...
  - tag URL / Release ページ URL（本番経路のみ）
  - dry-run の場合: 作成済み commit/tag と rollback 手順を提示済み
suggestion: |
  consumer 側で `uv lock --upgrade-package kaji` を実行するよう案内（本番経路のみ）
---END_VERDICT---
```

`ABORT` を返すケース:

- Step 1 の pre-flight check が修復困難（remote 不一致 / 認証不能 等）
- Step 2 で release 対象 commit が 0 件
- Step 4 で `make check` が失敗し、user が修正を保留した
- Step 6 で push が継続的に拒否され、原因究明が release skill の責務を超える
