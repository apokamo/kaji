---
description: kaji の release 作業（version bump / CHANGELOG / tag / GitHub Release ページ）を skill で完結させる。CI を使わず maintainer 手元で対話的に進める。
name: release
---

# Release

kaji の release を maintainer の手元で完結させる skill。
forge CI (GitHub Actions) に依存せず、各 step で user 承認を挟みながら version bump → CHANGELOG → tag → GitHub Release ページ作成まで進める。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| kaji の release（version bump + tag + Release ページ）を切る | ✅ 必須 |
| dry-run（push / Release ページ作成手前まで確認したい） | ✅ `--dry-run` 経路 |
| PyPI publish / dist build | ❌ 対象外（現運用で未使用） |
| `.github/workflows/release-please.yml` の再有効化 | ❌ 対象外（本 skill は maintainer 手元実行前提） |

## 入力

```
/release            # 通常実行（push と Release ページ作成まで進める）
/release --dry-run  # ローカル状態のみ更新（push / gh release create はスキップ）
```

引数なしで起動し、skill 側で git / pyproject の状態を読んで判定する。

## 前提

- 実行場所: kaji 本体の **main worktree** で実行する（release branch は切らない、main 直接 update する運用）
- **`gh`** CLI を使う（GitHub 運用）
- GitHub を指す git remote が 1 つ存在すること。Step 1 で `git remote -v` の URL から動的に抽出する。`.kaji/config.toml` の `provider.github.git_remote` 値と整合する remote 名であることが前提
- 解決した GitHub remote に対して `git push` できる権限を maintainer が持っていること
- `uv` / `make check` が走る環境（kaji 開発環境セットアップ済み）

> **Note**: 現状 skill は `git remote -v` の URL grep でのみ remote を解決し、`.kaji/config.toml` の `provider.github.git_remote` は直接読まない。config 値を変えただけでは動作は変わらない点に注意。将来 `kaji config git-remote` 相当の CLI を追加する余地あり。

## 実行手順

### Step 1: Pre-flight check

以下を順に確認する。1 つでも失敗した時点で停止し、user に復旧手順を提示する。

```bash
# 1-0. GitHub remote 名を解決
#      `git remote -v` から URL に github.com を含む push remote を動的抽出する。
#      `.kaji/config.toml` の `provider.github.git_remote` 値と整合する
#      remote 名であることが前提（skill は config を直接読まない）。
GITHUB_REMOTE=$(git remote -v | awk '/github\.com.*\(push\)/{print $1; exit}')
# 上記で見つからない場合、`origin` が GitHub を指していれば fallback
if [ -z "$GITHUB_REMOTE" ]; then
    if git remote get-url origin 2>/dev/null | grep -q github; then
        GITHUB_REMOTE=origin
    else
        echo "ABORT: no git remote pointing to github.com found"
        exit 1
    fi
fi
echo "Resolved GitHub remote: $GITHUB_REMOTE"

# 1-1. main checkout 状態
git rev-parse --abbrev-ref HEAD   # → "main" であること

# 1-2. working tree clean
git status --porcelain            # → 空であること

# 1-3. 上流 sync
git fetch "$GITHUB_REMOTE"
git rev-list --left-right --count "$GITHUB_REMOTE/main"...HEAD
# → "0\t0"（ahead/behind ともに 0）であること

# 1-4. 解決した remote が GitHub を指していることを再確認
git remote get-url "$GITHUB_REMOTE"   # → github.* を含むこと

# 1-5. gh CLI 認証済み
gh auth status                        # → "Logged in" 表示
```

**失敗時のガイド例**:

- GitHub remote 未発見 → `.kaji/config.toml` の `provider.github.git_remote` および `git remote -v` 出力を user に提示し、追加方法を相談（`git remote add origin <github-url>`）
- main 以外 → `git checkout main && git pull --ff-only "$GITHUB_REMOTE" main`
- dirty tree → user に commit / stash を促す（skill 側では一切触らない）
- behind → `git pull --ff-only "$GITHUB_REMOTE" main`
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

**release 必要性の追加確認**: 候補 commit が `docs:` / `test:` / `chore:` のみで `feat:` / `fix:` / BREAKING CHANGE を一切含まない場合、patch bump 候補となるが consumer 側 lockfile を無用に更新させるため SemVer 的には冗長になりがち。Step 2 の user 承認時に「今回の bump 候補は docs/test/chore のみ。本当に release するか」と明示的に問いかけ、user 判断で release skip / continue を決める。

### Step 3: CHANGELOG 生成

`CHANGELOG.md` の `## [Unreleased]` セクションを基に、新 version のエントリを作成する。

```markdown
## [X.Y.Z] - YYYY-MM-DD

### BREAKING CHANGE
- **壊れる契約**: 何を前提にしていた何が動かなくなるか
  - **影響の判定方法**: 下流 repo が影響を受けるかを確認する手段（grep 等の 1 コマンドが理想）
  - **適用指針**: 未カスタマイズなら再コピーで可の旨。カスタマイズ済み repo 向けには契約変更点の説明と上流 commit / PR への参照

### Added
- feat: ... entries

### Fixed
- fix: ... entries

### Changed / Docs / Internal
- その他 (docs / refactor / chore / test) entries
```

**BREAKING エントリの 3 要素（ADR 008 決定 2・必須）**: `### BREAKING CHANGE` に記載する
各項目は、**壊れる契約 / 影響の判定方法 / 適用指針** の 3 要素を必ず含める。kaji は
後方互換レイヤを提供しない（ADR 008）ため、破壊的変更の伝達は release notes 側の責務で
あり、この 3 要素が下流 repo の唯一の移行ガイドになる。3 要素が揃わない BREAKING
エントリのまま release を進めてはならない（参照:
[`docs/adr/008-no-backward-compat-layer.md`](../../../docs/adr/008-no-backward-compat-layer.md) /
[`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § 後方互換（共通））。

進め方:

1. `## [Unreleased]` 配下の既存記述を新 version セクションへ移動
2. 不足分（Unreleased に未記載の commit）があれば commit message から要約を追記
3. `### BREAKING CHANGE` に項目がある場合、各項目が 3 要素（壊れる契約 / 影響の判定方法 / 適用指針）を満たすか確認し、欠けていれば補う
4. 末尾の比較リンク（存在する場合）を更新
5. `CHANGELOG.md` の **diff を user に提示** して承認待ち

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

Step 1 で解決した `$GITHUB_REMOTE` に対して、main と tag を **atomic push** する。

```bash
# main と tag を 1 トランザクションで push（どちらか失敗すれば両方拒否される）
git push --atomic "$GITHUB_REMOTE" main vX.Y.Z
```

`--atomic` を使うことで、main は push 成功 / tag push 失敗のような **片方だけ remote に反映された中途半端な状態** を防ぐ。

push 失敗時:

- `non-fast-forward` → main が他者 push で進んでいる。`--atomic` のため tag も remote には残らない。**force push は禁止**。一旦 stop し、user に状況を共有 → 必要なら Step 1 から再実行（commit と tag を一度ローカルで rollback、後述）
- tag 関連で reject（同名 tag が既に存在する等）→ atomic のため main も remote 反映されていない。`git tag -d vX.Y.Z` で local tag を消し、原因確認後に再試行

### Step 7: GitHub Release ページ作成

```bash
# CHANGELOG の該当 section を抜粋し、--notes に渡す
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes "<CHANGELOG.md の [X.Y.Z] section 本文>"
```

完了後、`gh release view vX.Y.Z` で URL を取得し user に提示する。

### Step 8: 完了報告

user に以下を提示して終了:

- 採番した version と tag URL
- GitHub Release ページ URL
- consumer 側に `uv lock --upgrade-package kaji` を案内する一文（kamo2 等の dependency consumer 向け）

## Dry-run 経路（`--dry-run`）

Step 1 → 5 まで実行し、Step 6 (push) と Step 7 (Release ページ) を **スキップ**。

dry-run 終了時に skill が必ず提示する内容:

1. **作成された commit と tag**: `git show vX.Y.Z --stat` の要点
2. **rollback 手順**（dry-run のみ。本番経路では使わない）:

   > ⚠️ 以下は `git reset --hard` を含む破壊操作。skill が自動実行してはならない（ガードレール § も参照）。
   > **必ず user の明示承認を取ってから実行する**。コピペで一度に流す前提のブロックではない。

   ```bash
   git tag -d vX.Y.Z
   git reset --hard HEAD~1   # release commit を破棄（破壊操作: user 承認後のみ）
   # CHANGELOG.md / pyproject.toml / uv.lock の変更を確認後、必要なら git restore で戻す
   ```
3. **本番実行への進み方**:
   - dry-run の結果に問題がなければ `git push --atomic "$GITHUB_REMOTE" main vX.Y.Z` を手で実行 → Step 7 を手で実行、
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

`git push --atomic` を使うため、reject 時は **main と tag のどちらも remote には反映されていない**。ローカル side だけ巻き戻せばよい。

```bash
# 状況確認
git fetch "$GITHUB_REMOTE"
git log --oneline "$GITHUB_REMOTE/main"..HEAD       # 自分のローカル commits を確認
git log --oneline HEAD.."$GITHUB_REMOTE/main"       # 他者の commits を確認

# 他者 commits を取り込む必要がある場合（force push 禁止）:
# 1. tag を一旦削除
git tag -d vX.Y.Z
# 2. release commit を一旦巻き戻す
git reset --hard "$GITHUB_REMOTE/main"
# 3. main を最新化してから /release を再実行
git pull --ff-only "$GITHUB_REMOTE" main
```

> **絶対禁止**: `git push --force "$GITHUB_REMOTE" main` / `git push --force "$GITHUB_REMOTE" vX.Y.Z`。
> tag を上書き push (`--force`) すると consumer 側の lockfile / cache 整合が壊れる。tag は不変前提で運用する。

### Step 6 まで成功し Step 7 (Release ページ) で失敗

tag と main commit は既に push 済み。**rollback ではなく Release ページのみ再試行**する:

```bash
gh release view vX.Y.Z 2>/dev/null || \
  gh release create vX.Y.Z --title "vX.Y.Z" --notes "<本文>"
```

それでも作成できない場合、GitHub UI から手動で Release ページを作る選択肢を user に提示する。

### 既に push 済みの release を撤回したい（緊急）

原則として撤回しない。撤回する場合は別 issue で意思決定を残し、以下を user の明示同意付きで行う:

```bash
# tag をリモートから削除
git push "$GITHUB_REMOTE" --delete vX.Y.Z
# Release ページを削除
gh release delete vX.Y.Z
# 必要なら revert commit を main に乗せる（force push はしない）
git revert <release-commit-sha>
git push "$GITHUB_REMOTE" main
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
