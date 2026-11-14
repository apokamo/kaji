---
description: イシュー着手時に使用。worktreeで分離された開発環境を構築し、Issue本文にメタ情報を追記する
name: issue-start
exec_script: kaji_harness.scripts.issue_start
---

# Issue Start

イシュー対応を開始するためのworktreeをセットアップし、Issue本文にメタ情報を追記します。

本 skill は LLM agent を起動しない deterministic skill です。context 取得から worktree 作成・
symlink・Issue NOTE・報告コメント・verdict 出力までを、1 プロセスの Python entry
`kaji_harness.scripts.issue_start`（処理本体は `kaji_harness/issue_start.py`）が実行します
（Issue #569。複数の Bash 呼び出し間でシェル変数が失われる #560 と、手書き verdict の書式崩れ #551 の対策）。

起動経路は 2 系統あり、いずれも同じ entry `main` に合流します:

- **workflow の `start` step**: frontmatter の `exec_script` により、harness が entry を
  subprocess 実行します（`agent_runner` が headless / Herdr のどちらでも agent は起動しない）。
  step 定義は `skill: issue-start` と `on:` だけを持ち、`agent` / `model` / `effort` を書きません。
- **手動**: main checkout で `kaji issue start [issue_id]` を 1 回実行します（下記「手動実行」）。

入力・副作用・verdict の正本は entry module であり、本ファイルの記述はその契約のミラーです。

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

- `issue_id` (必須): Issue番号 (例: 247 / `gh:153`)。workflow では `KAJI_ISSUE_ID` から取得する

第 2 引数（旧 `prefix`）は **廃止** されました。ブランチ prefix は
`kaji issue context` と同じ `provider.resolve_issue_context()` が返す `branch_prefix`
（`type:*` ラベル → `chore` fallback の優先順）から自動決定します。
`kaji issue start` に第 2 引数を渡すと usage error（exit 2）で何もせず終了します。
手動 `/issue-start` で第 2 引数が渡された場合は、コマンドを実行せず ABORT verdict を出して停止し、
廃止アナウンスをユーザに返してください。

## 命名規則

`provider.resolve_issue_context()`（`kaji issue context` と同じ関数）が返す `branch_name` /
`worktree_dir` をそのまま使います。

- **ブランチ名**: `<branch_prefix>/<issue_id>` (例: `fix/247`)
- **ディレクトリ**: `<repo_root>/../<worktree_prefix>-<branch_prefix>-<issue_id>` (例: `../kaji-fix-247`)

## 手動実行

main checkout（worktree ではない）で次の 1 コマンドを実行し、stdout 末尾の verdict block を
そのまま報告してください。途中で止まった場合も、原因を解消してから同じコマンドを再実行します。

```bash
kaji issue start [issue_id]
```

## 処理内容（entry の契約）

正常時は次の順で実行します（現行の副作用・順序を維持）:

0. **事前検証（Git / Issue への書き込み前）**: context を 1 回取得し、workflow では runner が注入した
   `KAJI_WORKTREE_DIR` / `KAJI_BRANCH_NAME` / `KAJI_GIT_REMOTE` / `KAJI_DEFAULT_BRANCH` との一致を
   検査する。Issue コメントを読み、既存の start PASS 報告を分類する（下記「再実行」）
1. `git fetch <git_remote> <default_branch>` の後、`FETCH_HEAD` と
   `refs/remotes/<git_remote>/<default_branch>` の一致を確認する（fetch refspec が base を
   remote-tracking ref へ写さない設定で、古い ref から作ることを防ぐ）
2. `git worktree add --no-track -b <branch_name> <worktree_dir> <git_remote>/<default_branch>`。
   基点は fetch 直後の remote 側 default branch。ローカルの `<default_branch>` は基点にせず、更新もしない。
   `--no-track` のため upstream は設定しない（`/i-pr` の `git push -u` で設定する）
3. `<worktree_dir>/.venv` → `<main checkout>/.venv` の symlink（これにより `make check` が即座に実行可能）
4. main に `.kaji/config.local.toml`（gitignored の overlay）がある場合のみ、
   `<worktree_dir>/.kaji/config.local.toml` → main の overlay の symlink
5. `git worktree list --porcelain` で `<worktree_dir>` が `<branch_name>` で登録されていることを再確認
6. Issue 本文先頭に NOTE（`build_worktree_note_body` が生成する `> [!NOTE]` / Worktree / Branch の 3 行
   と空行 1 行）
7. 報告コメント（1 行目 marker `<!-- kaji-verdict: step=start status=PASS -->`、表に Issue / ブランチ /
   ディレクトリ / 基点 / venv / config overlay / メタ情報、ローカル `<default_branch>` に未 push の
   commit がある場合はその件数の注意、次のステップ、クリーンアップ、末尾に verdict block）。
   marker の契約は [shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md) § verdict マーカー契約
8. verdict: workflow では `KAJI_VERDICT_PATH` へ pure YAML を原子的に書き、同じ内容を stdout の
   `---VERDICT---` block に出す（#568 の正規化・検証・直列化を通す）

`git pull` / `reset` / `checkout` / `clean` / `branch -D` / `worktree remove` は一切実行しません。

## 再実行（部分成功からの再開）

途中で止まった後に同じコマンドを再実行すると、次のように扱います。fetch と照合は毎回実行します。

| 状態 | 扱い |
|------|------|
| `worktree_dir` 未登録・パス不在・branch 不在 | 新規作成 |
| `worktree_dir` が `branch_name` で登録済み・ディレクトリ実在 | 検証して再利用（Git に書き込まない。利用者の commit / 未 commit 変更はそのまま） |
| `worktree_dir` が別 branch / detached で登録済み | ABORT |
| `branch_name` が別パスの worktree で checkout 済み | ABORT |
| `branch_name` は存在するが worktree 未登録 | ABORT（既存 branch を自動で attach しない） |
| `worktree_dir` が未登録だがパスが存在 | ABORT |
| 登録済みだがディレクトリ不在（prunable） | ABORT |
| symlink が既に期待どおり | そのまま |
| symlink の参照先が異なる / 実ファイル・ディレクトリがある | ABORT（上書き・削除しない） |
| 同一 NOTE が本文先頭にある | 追記しない |

再利用時の報告の「基点」は、作成時の基点ではなく、現在の `<git_remote>/<default_branch>` との
共通祖先（`git merge-base`）の短縮 SHA として表示し、表に「再利用」行を加えます。

報告コメントの重複判定: 1 行目 marker が `step=start status=PASS`（metadata の有無は問わない）の
コメントを候補とし、表の `ブランチ` 行と `ディレクトリ` 行の値（前後の空白と、値全体を囲む 1 組の
backtick を除く）で分類します。旧形式（agent が投稿した報告）も同じ規則で判定します。

| 既存の候補 | 扱い |
|-----------|------|
| 同じ `branch_name` と `../<worktree_dir の basename>` の報告がある | 投稿しない |
| 同一報告が無く、`ブランチ` / `ディレクトリ` 行が欠落・矛盾する報告がある | 書き込み前に ABORT（重複しうるため） |
| 候補なし / 別の branch・ディレクトリの報告のみ | 1 件投稿する |

## Verdict 出力

verdict は entry が出力します（agent は書きません）。

```
---VERDICT---
status: PASS
reason: |
  Worktree 構築成功
evidence: |
  fetch 直後の origin/main（abc1234）と FETCH_HEAD の一致を確認し、fix/247 を ../kaji-fix-247 に --no-track で作成
  （以降、venv / config overlay / Issue NOTE / 報告コメントの扱い）
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | worktree の作成または検証付き再利用、symlink、NOTE、報告（同一報告が既存なら省略）が完了 |
| ABORT | context 取得失敗、`KAJI_*` と context の不一致、識別不能な start PASS 報告、`git fetch` 失敗、`<git_remote>/<default_branch>` の未解決・`FETCH_HEAD` との不一致、worktree のパス / branch 不一致、symlink 衝突、Issue NOTE / 報告コメントの投稿失敗 / 第 2 引数（旧 `prefix`）が渡された |

ABORT の evidence には失敗した操作・コマンド・終了コード・stderr 末尾と「完了済みの副作用」を、
suggestion には再実行コマンド `kaji issue start <issue_id>` を書きます。ABORT のときは報告コメントを
投稿しません。別の base（ローカルの `<default_branch>` 等）へ fallback して続行することはありません。

`start` は自動 recovery の対象外（`non_resumable_skill`）のままです。workflow が ABORT / ERROR で
止まった場合は、上記の再実行で start を完了させてから、後続 step から手動で再開してください
（[failure-recovery.md](../../../docs/cli-guides/failure-recovery.md)）。
