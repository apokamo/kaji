---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ安全削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
PR マージ、worktree 削除、ブランチ削除、Issue クローズを一括実行します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

**ワークフロー内の位置**: implement → review-code → i-dev-final-check → i-pr → **close**

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

**provider 解決時に追加で注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `provider_type` | str | `github` / `gitlab` / `local` のいずれか。本 Skill の経路分岐に使用 |
| `default_branch` | str | ベースブランチ名（`main` 等）。local 経路で merge / push の引数に使用 |
| `branch_name` | str | フィーチャーブランチ名（`feat/<id>` 等） |
| `worktree_dir` | str | worktree 絶対パス |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

## 前提条件

- `/i-pr` でPRが作成済みであること
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

`[provider_type]` に応じて手順が分岐する。

- `[provider_type]` が `github` / `gitlab`（または未注入の legacy 環境）→ 既存の
  Step 1〜6 を順に実行する（`kaji pr merge` / worktree 削除 / branch 削除 /
  `git pull` / `kaji issue close` / 報告）。`gitlab` 配下では `kaji pr merge` の
  写像層が `glab mr merge <iid>` に変換し、`kaji issue close` が `glab issue close`
  に変換する。skill 側のコマンドは forge を問わず同じ。
- `[provider_type]` が `local` → 後述の **provider=local の場合** セクションへ
  ジャンプし、design.md § local mode における /issue-close の手順 (6 step) を
  実行する。github / gitlab 用の Step 1〜6 は実行しない（PR / MR 概念が無いため）。

### provider=github / gitlab の場合

### Step 1: Worktree情報の取得

Issue本文からWorktree情報を取得します:

```bash
kaji issue view [issue_id] --json body -q '.body'
```

以下の情報を抽出:
- `> **Worktree**: \`../kaji-[prefix]-[issue_id]\`` → worktree パス
- `> **Branch**: \`[prefix]/[issue_id]\`` → ブランチ名

### Step 2: メインリポジトリのパスを特定

`git worktree list` の最初の行が常に main worktree（bare repository のルート）を示す:

```bash
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
```

> **注意**: `git rev-parse --show-toplevel` は現在の worktree のルートを返すため、
> worktree 内から実行すると main repo を取得できない。必ず `git worktree list` を使うこと。

worktree 内にいる場合は main repo に移動:

```bash
cd "$MAIN_REPO"
```

### Step 3: PRのマージ

```bash
kaji pr merge [branch_name]
```

マージコミットを作成してブランチ履歴を保持する。ブランチ削除は worktree 削除後に Step 4.5 で行う。

> **結果を記録**: `pr_merge_result` = 「マージ済み」。この値は Step 6 で使用する。

### Step 4: worktree削除

```bash
git worktree remove "$MAIN_REPO/../kaji-[prefix]-[issue_id]"
```

> `$MAIN_REPO` は Step 2 で取得済み。

> **結果を記録**: `worktree_result` = 「削除済み」。この値は Step 6 で使用する。

### Step 4.5: ブランチ削除

worktree 削除後にローカル・リモートブランチを削除する。
`git fetch [git_remote]` で `[git_remote]/[default_branch]` を最新化してから、`merge-base --is-ancestor` でマージ済み判定を行い、安全に削除する。
ローカル削除とリモート削除は独立して実行し、片方の失敗がもう片方をブロックしない。
ブランチが既に存在しない場合はスキップする。

```bash
# 1. fetch して [git_remote]/[default_branch] を更新
git fetch [git_remote]

# 2. ローカルブランチ削除: 存在確認 → マージ済み判定 → 安全な -D
if git show-ref --verify --quiet refs/heads/[branch_name]; then
    if git merge-base --is-ancestor [branch_name] [git_remote]/[default_branch]; then
        git branch -D [branch_name]
    else
        echo "WARNING: branch not merged into [git_remote]/[default_branch], skipping local delete"
    fi
fi

# 3. リモートブランチ削除（ローカル削除の成否に依存しない）
git ls-remote --exit-code --heads [git_remote] [branch_name] >/dev/null 2>&1
LS_EXIT=$?
if [ "$LS_EXIT" -eq 0 ]; then
    if ! git push [git_remote] --delete [branch_name]; then
        echo "ERROR: git push [git_remote] --delete failed"
        exit 1
    fi
elif [ "$LS_EXIT" -eq 2 ]; then
    echo "INFO: remote branch already deleted"
else
    echo "ERROR: git ls-remote failed (exit $LS_EXIT)"
    exit 1
fi

# 4. stale remote-tracking ref を掃除
git fetch --prune [git_remote]
```

> **結果を記録**:
> - `local_branch_result` = 「削除済み」/「未存在を確認」/「未マージのためスキップ」
> - `remote_branch_result` = 「削除済み」/「未存在を確認」/「削除失敗（要手動対応）」
>
> これらの値は Step 6 で使用する。

### Step 5: mainを最新化

```bash
git pull [git_remote] [default_branch]
```

> **結果を記録**: `pull_result` = 「最新化済み」。この値は Step 6 で使用する。

### Step 5.5: Issue クローズ

```bash
kaji issue close [issue_id] --reason completed
```

> **結果を記録**: `close_result` = 「クローズ済み」/「クローズ失敗（要手動対応）」。この値は Step 6 で使用する。
>
> **重要**: `kaji issue close` が失敗した場合は verdict を **ABORT** にすること。Issue が未クローズのまま残ることは許容しない。

### Step 6: 完了報告

Step 3〜5.5 の結果を使って、**stdout への報告**と **Issue タイムラインへのコメント投稿**の両方を行う。

#### 6a. Issue コメント投稿

各ステップで記録した結果変数を使い、コメント内容を動的に組み立てて投稿する:

```bash
kaji issue comment [issue_id] --commit --body-file - <<'COMMENT_EOF'
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| Issue | [close_result] |
COMMENT_EOF

```

> `[pr_merge_result]` 等のプレースホルダーは、実際の実行結果に置き換えること。ハードコードしない。

#### 6b. stdout 報告

以下の形式で報告してください:

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | [issue_ref] |
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| Issue 状態 | [close_result] |
```

### provider=local の場合

`[provider_type]` が `local` のとき、PR 概念が無いため
design.md § local mode における /issue-close の手順 (6 step) を実行する。

> **重要 (worktree 運用)**: bare repository + worktree パターンでは
> `[default_branch]` は **feature worktree とは別の worktree**（通常 main repo 側）
> で checkout されている。そのため merge / close commit は **base worktree 側
> で実行**し、feature worktree (`[worktree_dir]`) はその後で削除する。
> feature worktree 内で `git switch [default_branch]` を実行しても、別 worktree
> がそのブランチを保持しているため Git に拒否される。

#### Step 1: Preflight check（feature worktree で確認）

```bash
cd [worktree_dir]
test -z "$(git status --porcelain)" || { echo "ABORT: uncommitted changes in [worktree_dir]"; exit 1; }
git rev-parse --abbrev-ref HEAD | grep -qE "^[a-z]+/local-[a-z0-9]+-[0-9]+(-[a-z0-9-]+)?$" || { echo "ABORT: not on feature branch"; exit 1; }
```

未コミット変更 / feature ブランチ外なら ABORT。

#### Step 2: Base worktree を特定し、base branch を最新化

`git worktree list --porcelain` から `[default_branch]` を checkout している
worktree を抽出する。見つからなければ user が手動で base 側を準備する必要が
あるため ABORT。

```bash
# [default_branch] を checkout している worktree を取得
BASE_WT=$(git worktree list --porcelain | awk -v b="[default_branch]" '
    /^worktree / { wt=$2 }
    $0 == "branch refs/heads/" b { print wt; exit }
')
test -n "$BASE_WT" || { echo "ABORT: no worktree has [default_branch] checked out. Run 'git worktree add <path> [default_branch]' or 'git switch [default_branch]' in your main checkout first."; exit 1; }

cd "$BASE_WT"

# Step 2.1: 3 段ガードによる救済 commit 判定（Issue local-p1-16 B）
#
# 標準動線（各 skill での `kaji issue {comment,edit} --commit`）が機能していれば
# base worktree は clean。蓄積が残っている場合は LocalProvider 永続化由来の path
# のみ救済し、それ以外は ABORT する。
#
# 救済対象 (LocalProvider 命名規則):
#   - .kaji/issues/<issue_id>-<slug>/issue.md
#   - .kaji/issues/<issue_id>-<slug>/comments/<4桁seq>-<machine_id>.md
# 出典: kaji_harness/providers/local.py:586 / providers/context.py:17 / providers/local.py:36
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
    # 条件 1: dirty path がすべて [issue_id] の永続化 whitelist に一致するか検査
    ISSUE_DIR_RE='^\.kaji/issues/[issue_id]-[a-z0-9-]+/(issue\.md|comments/[0-9]{4}-[a-z0-9]{1,16}\.md)$'
    UNRELATED=$(printf '%s\n' "$DIRTY" | awk -v re="$ISSUE_DIR_RE" '
        {
            # rename / copy ("R  old -> new") は救済対象外として ABORT に倒す
            if (match($0, /->/)) { print; next }
            # 先頭 3 文字 (status + space) を除いた残りを path として扱う
            path = substr($0, 4)
            # quoted path (path に空白等あり) は対応外として ABORT
            if (substr(path, 1, 1) == "\"") { print; next }
            if (path !~ re) { print }
        }
    ')
    if [ -n "$UNRELATED" ]; then
        echo "ABORT: dirty files outside LocalProvider persistence whitelist in base worktree $BASE_WT:"
        printf '%s\n' "$UNRELATED"
        echo "  Allowed pattern: $ISSUE_DIR_RE"
        exit 1
    fi
    # 条件 2: whitelist 命名規則の glob で限定 add + atomic commit
    #   - `comments/` ディレクトリ全体を add してはならない (note.txt 等を巻き込む)
    #   - `git commit --only` で他の staged change を HEAD に混入させない
    git add \
        ".kaji/issues/[issue_id]-"*"/issue.md" \
        ".kaji/issues/[issue_id]-"*"/comments/"[0-9][0-9][0-9][0-9]-*.md \
        2>/dev/null || true
    git commit --only \
        -m "chore(local): salvage uncommitted issue files for [issue_ref]" \
        -- \
        ".kaji/issues/[issue_id]-"*"/issue.md" \
        ".kaji/issues/[issue_id]-"*"/comments/"[0-9][0-9][0-9][0-9]-*.md \
        || { echo "ABORT: salvage commit failed"; exit 1; }
    # 条件 3: 救済後の残差を再検証 (rename/copy/rm 等の取りこぼしを検出)
    test -z "$(git status --porcelain)" || {
        echo "ABORT: residual dirty files after salvage commit in base worktree $BASE_WT:"
        git status --porcelain
        exit 1
    }
fi

# remote 設定がある場合のみ fetch + ff-only merge。
# fetch 失敗 (network 断 / 認証エラー / suspended account 等) は WARNING で skip し、
# local-only で close を完結させる (Step 6 の push も同様に warning で続行する設計と整合)。
# `kaji run` 非対話モードでは AskUserQuestion 経由のリカバリ不可のため、
# deterministic に local fallback すること。手動 push は remote 復旧後に実施。
if git remote get-url [git_remote] >/dev/null 2>&1; then
    if git fetch [git_remote] [default_branch] 2>&1; then
        git merge --ff-only "[git_remote]/[default_branch]" || { echo "ABORT: ff-only merge failed in base worktree"; exit 1; }
    else
        echo "WARNING: git fetch [git_remote] [default_branch] failed; proceeding with local-only close (manual push needed after remote recovery)"
    fi
fi
```

ABORT 条件:
- fast-forward できない (ローカル [default_branch] が [git_remote]/[default_branch] から分岐) → resolve 後に再実行
- base worktree 側に LocalProvider 永続化 whitelist 外の dirty file が残存 → 手動コミット後に再実行

WARNING 継続条件:
- `git fetch` 失敗 (remote 到達不可 / 認証失敗) → local merge は実行、push は Step 6 で warning skip

標準動線で各 skill が `kaji issue {comment,edit} --commit` を使っていれば、ここまで到達した時点で
base worktree は clean のはず。救済 commit は標準動線が機能しなかった場合の安全装置として残す。

#### Step 3: Merge 実行（base worktree 上で）

```bash
git merge --no-ff --no-edit [branch_name] || { echo "ABORT: merge conflict, resolve manually in $BASE_WT then retry"; exit 1; }
```

衝突したら ABORT。Issue は open のまま、user が手動 resolve した後で再実行する。

#### Step 4: Issue frontmatter 更新 + commit（base worktree 上で）

```bash
kaji issue close [issue_id] --reason completed
git add .kaji/issues/[issue_id]-*/issue.md
git commit -m "chore(issue): close [issue_ref]" || { echo "ABORT: commit failed"; exit 1; }
```

`--reason completed` は明示で書く（`LocalProvider.close_issue` の default も
`completed` だが、Skill markdown 上で明示することで読み手の予期外を減らす）。

**Step 4 完了で Issue close は確定**。以降の失敗は警告のみ。

#### Step 5: Cleanup（base worktree から feature worktree を削除）

base worktree に居る状態で feature worktree を削除する。`cwd == 削除対象` を
回避するため、Step 2 の `cd "$BASE_WT"` は維持したまま実行する。

```bash
git worktree remove [worktree_dir] || echo "WARNING: worktree remove failed for [worktree_dir]; manual cleanup needed"
git branch -d [branch_name] || echo "WARNING: branch delete failed for [branch_name]; manual cleanup needed"
```

#### Step 6: Push（remote 設定がある場合、base worktree から）

```bash
if git remote get-url [git_remote] >/dev/null 2>&1; then
    git push [git_remote] [default_branch] || echo "WARNING: push failed; manual push needed"
fi
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  クローズ完了
evidence: |
  PR マージ・worktree 削除・main 最新化・Issue クローズ済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| ABORT | クローズ失敗（`kaji issue close` 失敗を含む / local merge 衝突） |
