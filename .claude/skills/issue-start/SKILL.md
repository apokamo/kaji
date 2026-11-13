---
description: イシュー着手時に使用。worktreeで分離された開発環境を構築し、Issue本文にメタ情報を追記する
name: issue-start
---

# Issue Start

イシュー対応を開始するためのworktreeをセットアップし、Issue本文にメタ情報を追記します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| コード/ドキュメント変更を伴うイシュー着手 | ✅ 必須 |
| 設計のみ（ファイル変更なし） | ⚠️ 任意 |
| 調査・リサーチのみ | ❌ 不要 |

**重要**: PRを作成する際やイシュー対応でコミットが必要な場合、`git branch` ではなくこのスキルを使用してください。

## 引数

```
$ARGUMENTS = <issue_id>
```

- `issue_id` (必須): Issue番号 (例: 247 / `gh:153`)

第 2 引数は **廃止** されました。ブランチ prefix は
`kaji issue context` が返す `branch_prefix`（`type:*` ラベル → `chore` fallback の
優先順）から自動決定します。

## 命名規則

`kaji issue context <issue_id>` の出力（`provider.resolve_issue_context()` が正本）から
取得する `branch_name` / `worktree_dir` をそのまま使います。

- **ブランチ名**: `<branch_prefix>/<issue_id>` (例: `fix/247`)
- **ディレクトリ**: `<repo_root>/../kaji-<branch_prefix>-<issue_id>` (例: `../kaji-fix-247`)

## 実行手順

### Step 0: 引数の検査

`$ARGUMENTS` から `issue_id` を取得してください。第 2 引数（旧 `prefix`）が
渡された場合は **ABORT** verdict を出して停止し、廃止アナウンスをユーザに返してください
（`type:*` ラベルからの自動導出に一本化されているため）。

### Step 1: context 正本の取得

system `jq` バイナリ依存を持ち込まないため、`kaji issue context` の `-q` (Python jq) を使う:

```bash
PREFIX=$(kaji issue context [issue_id] -q '.branch_prefix')
BRANCH=$(kaji issue context [issue_id] -q '.branch_name')
WT=$(kaji issue context [issue_id] -q '.worktree_dir')
REMOTE=$(kaji issue context [issue_id] -q '.git_remote')
BASE=$(kaji issue context [issue_id] -q '.default_branch')
```

`worktree_dir` は絶対パスで返ります。`git_remote` / `default_branch` は provider 設定
（`provider.<type>.git_remote` / `provider.<type>.default_branch`）から解決された値です。
以降の手順では上記 5 変数を使います。

### Step 2: ブランチとWorktreeの作成

worktree の基点は **fetch した直後の `$REMOTE/$BASE`**（PR の base である remote 側の default branch）。
ローカルの `$BASE` は remote のキャッシュにすぎず、kaji の外（GitHub UI での merge、別 machine の push 等）で
remote が進んでいると古いため、基点にしない。

メインリポジトリのルートから、次の block をそのまま 1 回で実行:

```bash
# kaji-issue-start-worktree
MAIN_REPO=$(git rev-parse --show-toplevel) || exit 1
git -C "$MAIN_REPO" fetch "$REMOTE" "$BASE" || { echo "error: git fetch $REMOTE $BASE failed" >&2; exit 1; }
FETCHED=$(git -C "$MAIN_REPO" rev-parse --verify --quiet "FETCH_HEAD^{commit}") || { echo "error: FETCH_HEAD is not resolvable after fetch" >&2; exit 1; }
TRACKING=$(git -C "$MAIN_REPO" rev-parse --verify --quiet "refs/remotes/$REMOTE/$BASE^{commit}") || { echo "error: $REMOTE/$BASE is not resolvable after fetch" >&2; exit 1; }
[ "$FETCHED" = "$TRACKING" ] || { echo "error: $REMOTE/$BASE ($TRACKING) does not match fetched $BASE ($FETCHED)" >&2; exit 1; }
git -C "$MAIN_REPO" worktree add --no-track -b "$BRANCH" "$WT" "$REMOTE/$BASE"
```

- **fetch → 照合 → 作成の順を崩さない**。`FETCH_HEAD` との照合は、fetch refspec が `$REMOTE/$BASE` を
  更新しない設定（非標準の `remote.<name>.fetch`）で古い remote-tracking ref から作ることを防ぐ。
- `--no-track`: 新しい branch に upstream を設定しない（現行どおり。upstream は `/i-pr` の
  `git push -u [git_remote] HEAD` で設定する）。`$REMOTE/$BASE` を upstream にすると、素の `git push` が
  default branch へ向かいうる。
- ローカルの `$BASE` は更新しない（main checkout は作業中でありうる）。追随は `/issue-close` の
  `git pull` か保守者の操作に任せる。
- **block が非ゼロで終了した場合（fetch 失敗、`$REMOTE/$BASE` 未解決・不一致、worktree 作成失敗）は
  ABORT**: ローカルの `$BASE` など古い基点で再試行・続行せず、以降の Step を実行しない。
  verdict の evidence に失敗したコマンド・終了コード・stderr を書いて stdout に出力し、終了する。
  worktree 作成前に止まるため半端な branch / worktree は残らず、原因を解消した後の再実行は安全。

成功したら、完了報告用に基点の SHA と、ローカル `$BASE` にある未 push の commit 数を取得する:

```bash
BASE_SHA=$(git -C "$MAIN_REPO" rev-parse --short "$REMOTE/$BASE")
LOCAL_AHEAD=$(git -C "$MAIN_REPO" rev-list --count "$REMOTE/$BASE..refs/heads/$BASE" 2>/dev/null || echo 0)
```

`LOCAL_AHEAD` が 0 でない場合、ローカル `$BASE` の未 push commit は基点に **含まれない**（PR の base は
remote 側のため）。停止はせず、Step 5 の完了報告に注意として記載する。

### Step 2.5: venv シンボリックリンク作成

main プロジェクトの `.venv` へのシンボリックリンクを作成:

```bash
ln -s "$MAIN_REPO/.venv" "$WT/.venv"
```

これにより `make check` が即座に実行可能になります。

### Step 2.6: config overlay (`.kaji/config.local.toml`) シンボリックリンク作成

`.kaji/config.local.toml` は gitignored のため `git worktree add` では worktree に転写されない。
overlay 不在の worktree では `kaji` CLI が `.kaji/config.toml` だけで動き、main 側で上書きした
マシン固有設定（`[execution]` 等）が worktree 配下の実行に反映されない。

main の overlay が存在する場合のみ、worktree の `.kaji/` にシンボリックリンクで共有する:

```bash
if [ -f "$MAIN_REPO/.kaji/config.local.toml" ]; then
  ln -sf "$MAIN_REPO/.kaji/config.local.toml" "$WT/.kaji/config.local.toml"
fi
```

`-f` で再作成を許容（既存 symlink を上書き）。`.gitignore` に登録済みのパスなので
worktree 側 git status には現れない。main に overlay が無い場合は何も起きない。

### Step 3: Worktreeの確認

```bash
git worktree list
```

ワークツリーが正しく作成されたことを確認してください。

### Step 4: Issue本文にメタ情報を追記

Issue本文の先頭にWorktree情報を追記します。本文合成は `kaji` 内部の決定的な
Python 経路（`build_worktree_note_body`）が担うため、エージェントは単一トークン
引数を 3 つ渡すだけでよい（multi-line 本文を自前で組み立てない）:

```bash
WT_BASENAME=$(basename "$WT")
kaji issue prepend-note [issue_id] --worktree "$WT_BASENAME" --branch "$BRANCH" --commit
```

`kaji issue prepend-note` は現在の Issue 本文を取得し、`> [!NOTE]` メタブロックと
本文の間に **空行ちょうど 1 行** を保証して合成・更新する。blank line がエージェント
ではなく Python 文字列リテラルに固定されるため、どのモデルでも blockquote と本文
heading が密着しない（Issue #200）。`--commit` は github provider では silent に無視される（CLI 互換のため付与を維持）。

### Step 5: セットアップ完了報告

Worktree 構築に成功した場合だけ、以下の形式で Issue にコメントを投稿し、同じ内容を stdout にも報告してください
（`$PREFIX` / `$BRANCH` / `$WT` / `$REMOTE` / `$BASE` / `$BASE_SHA` / `$LOCAL_AHEAD` の値で埋めること）。
このコメントは PASS のときだけ投稿するため、`--verdict-step start --verdict-status PASS` を常に付ける
（1 行目の verdict marker は `kaji issue comment` が付与する。
[shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md) § verdict マーカー契約）。
ABORT のときはコメントを投稿せず、verdict を stdout に出力して終了する。
heredoc は quote しているため `$...` は展開されない。値に置き換えてから投稿すること。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step start --verdict-status PASS \
  --body-file - <<'EOF'
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| ブランチ | $BRANCH |
| ディレクトリ | ../$(basename "$WT") |
| 基点 | $REMOTE/$BASE（$BASE_SHA） |
| venv | シンボリックリンク作成済み |
| config overlay | シンボリックリンク作成済み / 不要（main に未配置） |
| メタ情報 | Issue本文に追記済み |

（`LOCAL_AHEAD` が 0 でない場合のみ追記）
> 注意: ローカル `$BASE` に `$REMOTE/$BASE` へ未 push の commit が $LOCAL_AHEAD 件あります。
> これらは worktree の基点に含まれていません。

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../$(basename "$WT")

### クリーンアップ（作業完了後）

作業が完了したら `/issue-close [issue_id]` を実行してください。

(末尾に、下記「Verdict 出力」で返す内容と同じ verdict block を `---VERDICT---` / `---END_VERDICT---` で囲んで付ける)
EOF
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  Worktree 構築成功
evidence: |
  fetch 後の $REMOTE/$BASE（$BASE_SHA）を基点に worktree 作成、venv symlink 済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Worktree 構築成功 |
| ABORT | 構築失敗（Step 2 の `# kaji-issue-start-worktree` block の非ゼロ終了: `git fetch` 失敗、`$REMOTE/$BASE` の未解決・FETCH_HEAD との不一致を含む） / 第 2 引数（旧 `prefix`）が渡された |
