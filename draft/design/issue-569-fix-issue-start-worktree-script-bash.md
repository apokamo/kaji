# [設計] issue-start の worktree 構築を決定論的 script 化し、Bash 変数消失と部分成功に対応する

Issue: #569

## 概要

`start` step（`issue-start` skill）が agent に複数の Bash block を順に実行させているため、block 間で
シェル変数が失われて worktree 作成前に ABORT する（#560）。また agent 手書きの判定が崩れると副作用完了後に
step が ERROR になり、再開も禁止される（#551）。context 取得から PASS 報告・verdict 出力までを
1 プロセスの Python script に移し、正常時の Git/Issue 副作用と順序を維持したまま、途中停止後の
同一 worktree の検証付き再利用を可能にする。

## 背景・目的

### Observed Behavior（OB）

1. **#560 run `261113010347`（変数消失）**: `start/attempt-001/verdict.yaml`（main checkout の
   `.kaji-artifacts/560/runs/261113010347/steps/start/attempt-001/verdict.yaml`）より引用:

   ```text
   Step 2 の git fetch が終了コード 1 で失敗
   直前の context 取得と fetch block は別 shell で実行され、REMOTE と BASE が block 内で未設定だった
   stderr: fatal: no path specified; see 'git help pull' for valid url syntax
   stderr: error: git fetch   failed
   ```

   SKILL.md Step 1 の `REMOTE=$(kaji issue context ...)` と Step 2 の `# kaji-issue-start-worktree`
   block を agent が別々の Bash tool 呼び出しで実行し、`git -C "$MAIN_REPO" fetch "$REMOTE" "$BASE"`
   が `git fetch "" ""` 相当になった。

2. **#551 run `261113020532`（部分成功後の非再開停止）**: `recovery.json` より引用:

   ```text
   "decision": "not_resumable", "failed_step": "start",
   "reason": "non-resumable skill step; auto recovery and manual resume are blocked:
              non_resumable_skill (failed_step=start, skill=issue-start)",
   "run.log: workflow_end status=ERROR error=VerdictParseError: YAML parse error in verdict block ...
              line 4, column 2:  evidence: |"
   ```

   worktree・Issue NOTE・PASS 報告コメントは作成済みだったが、agent が書いた verdict の `evidence:` の
   字下げが 1 文字ずれて parse 不能となり ERROR。`--from baseline` による手動復旧を要した。
   手動で `/issue-start` を再実行しても、現行手順は `git worktree add -b` が既存 branch で失敗し、
   NOTE / コメントは重複する（再実行が安全でない）。

### Expected Behavior（EB）

Issue #569 本文「Expected Behavior」と「完了条件」を正本とする。

- `kaji issue context` の正本（`provider.resolve_issue_context()`）が返す remote / default branch /
  branch / worktree を使い、fetch → `FETCH_HEAD` と remote-tracking ref の一致確認 → `--no-track`
  worktree 作成 → `.venv` / config overlay の symlink → Issue NOTE → `step=start status=PASS` 報告
  コメント → verdict の順序と内容を保つ。
- context と Git 操作を 1 プロセスで扱い、呼び出し間のシェル変数引き継ぎを不要にする。verdict は
  #568 の検証済み直列化を通して出力する。
- 期待するパス/ブランチの worktree が既に登録されている場合は、reset/削除せず一致を検証して
  未完了のセットアップだけを再実行する。不一致、fetch/ref 照合失敗、既存 symlink の異なる参照先は
  ABORT。別の base に silent fallback しない。
- 自動 recovery の安全ゲート（`non_resumable_skill`）は緩めない。

## 再現手順（Steps to Reproduce）

1. 前提: `origin` を持つ kaji repo の main checkout。Issue に `type:*` ラベルがある。
2. `.claude/skills/issue-start/SKILL.md` Step 1 の 5 行（`PREFIX=... BASE=...`）を 1 回目の
   `bash -c` で実行する。
3. Step 2 の `# kaji-issue-start-worktree` block を 2 回目の `bash -c` で実行する。
4. 観測: `REMOTE` / `BASE` が空のまま `git fetch "" ""` となり、
   `fatal: no path specified; see 'git help pull' for valid url syntax` で exit 1（OB 1 と同一）。

部分成功（OB 2）の再現: 現行手順で worktree 作成・NOTE 追記・PASS コメント投稿まで終えた後に
手順を再実行すると、`git worktree add -b <branch>` が `a branch named '<branch>' already exists`
で失敗し、NOTE とコメントは成功時に二重化する。

## 根本原因（Root Cause）

### なぜ壊れるか

- **状態の運搬を agent の実行形態に依存している**: SKILL.md は「Step 1 で変数を作り、Step 2 で使う」
  という 1 シェル前提の手順だが、agent の Bash tool は呼び出しごとに新しいプロセスで、どの block を
  まとめて実行するかは agent が決める。手順の正しさが LLM の実行粒度に依存し、決定論的でない。
- **判断を要しない機械的手順を agent に委ねている**: start に agent の判断は不要（context の値は
  決定的、分岐は失敗時 ABORT のみ）。それでも agent が verdict を組み立てるため、#551 のような
  書式崩れが副作用完了後に起きる。
- **再実行を想定していない**: `git worktree add -b` は既存 branch で失敗し、`kaji issue prepend-note`
  （`build_worktree_note_body`）は既存 NOTE を見ずに先頭へ追加し、コメントは無条件に投稿される。
  途中停止後の再実行が「失敗する」か「重複する」かのどちらかになる。

### いつから

- 変数を Step 1 で作って後続 block で使う形は `kaji issue context` 導入以来の構成。
  `REMOTE` / `BASE` を fetch block で使う形は `739a690`（#549: fetched remote default branch を
  基点にする）で導入され、ここで「Step 1 の変数を消失すると fetch が空引数になる」状態が成立した。
- `non_resumable_skill` gate（`kaji_harness/recovery/models.py:33` の
  `NON_RESUMABLE_SKILLS = {"issue-start", "i-pr", "issue-close"}`）は #349 以来の安全ゲートで、
  再実行の安全性が無いため start の自動再開を禁止している。本 Issue でもこれは維持する。

### 同根箇所の調査

`^[A-Z_]+=\$\(` で変数を作る SKILL.md を走査した結果（件数は代入行数）:

| skill | 件数 | 扱い |
|-------|------|------|
| issue-close | 14 | #551（`7a6af8f`: carry issue-close follow-up ID across Bash calls）で対処済み。本 Issue の範囲外 |
| issue-start | 11 | 本 Issue で script 化 |
| release / issue-design / review / issue-review-code ほか | 1〜3 | 同一 block 内で完結する用法または人手実行の手順。本 Issue の範囲外（Issue 本文「範囲外」の方針に従い、横断的な script 化はしない） |

## 現在の start 副作用と失敗時の安全ゲート（整理）

| # | 現行の操作（SKILL.md） | 副作用 | 現行の失敗時挙動 |
|---|------------------------|--------|------------------|
| 0 | 第 2 引数の検査 | なし | ABORT |
| 1 | `kaji issue context` ×5 | なし（GitHub 読み取り） | 規定なし（変数が空のまま進みうる = #560） |
| 2 | `git fetch $REMOTE $BASE` → `FETCH_HEAD` / `refs/remotes/$REMOTE/$BASE` 解決・一致確認 → `git worktree add --no-track -b $BRANCH $WT $REMOTE/$BASE` | remote-tracking ref 更新、branch・worktree 作成 | 非ゼロで ABORT。worktree 作成前に止まるので安全 |
| 2' | `BASE_SHA` / `LOCAL_AHEAD` 取得 | なし | 規定なし |
| 3 | `ln -s $MAIN_REPO/.venv $WT/.venv` | symlink | 規定なし（既存ディレクトリがあると内側に `.venv/.venv` を作る） |
| 4 | main に overlay がある場合 `ln -sf ... $WT/.kaji/config.local.toml` | symlink（上書き） | 規定なし（異なる参照先を黙って上書き） |
| 5 | `git worktree list` | なし | 目視確認のみ |
| 6 | `kaji issue prepend-note` | Issue 本文更新 | 規定なし（再実行で NOTE 二重化） |
| 7 | `kaji issue comment --verdict-step start --verdict-status PASS` | コメント投稿 | 規定なし（再実行で重複） |
| 8 | verdict を stdout に出力（agent 経路は #568 以降 `kaji verdict publish`） | verdict artifact | 書式崩れで ERROR（#551） |

安全ゲート: (a) step 内 — #2 の fetch/照合/作成失敗で ABORT（古い base で続行しない）。
(b) workflow — `on: ABORT: end`。(c) recovery — `non_resumable_skill` で start の自動再開・
`kaji recover --retry-failed-step`・series resume を拒否。

## インターフェース

### 入力

| 入力 | 経路 | 説明 |
|------|------|------|
| Issue ID | workflow: env `KAJI_ISSUE_ID` / 手動: `kaji issue start <issue_id>` の位置引数 | 数値 / `gh:N`。`normalize_id` で正規化 |
| context | `get_provider(config).resolve_issue_context(issue_id)` を script 内で 1 回呼ぶ | `branch_name` / `worktree_dir` / `git_remote` / `default_branch` / `branch_prefix` の正本（`kaji issue context` と同一関数） |
| `KAJI_WORKTREE_DIR` / `KAJI_BRANCH_NAME` / `KAJI_GIT_REMOTE` / `KAJI_DEFAULT_BRANCH` | workflow のみ（runner が注入） | 存在する場合は context と完全一致を検査し、不一致なら ABORT（runner が state / discovery で context を上書きしたケースで、どちらかを黙って選ばない） |
| `KAJI_VERDICT_PATH` | workflow のみ | 存在すれば pure YAML verdict を原子的に書く |
| cwd | runner の step workdir / 手動実行時のカレント | `git rev-parse --show-toplevel` を `MAIN_REPO` とする（現行 SKILL.md と同じ） |

手動経路で旧第 2 引数（prefix）を渡した場合、`kaji issue start` は usage error（exit 2）で
何もせず終了する（現行 Step 0 の ABORT と同じく副作用なし）。手動 `/issue-start` で agent が
読む SKILL.md 本文は、第 2 引数がある場合は ABORT verdict を出して停止する現行規則を維持する。

### 出力

正常時（順序は現行どおり。0 は書き込みを伴わない事前検証で、現行 Step 1 の context 取得と同じ位置）:

0. **事前検証（Git / Issue への書き込み前）**: context 取得、env との一致確認、報告コメントの
   marker（固定値 `step=start status=PASS`）を `build_kaji_verdict_marker` で構築、報告の同一性キー
   （ブランチ = `branch_name`、ディレクトリ = `../<worktree_dir の basename>`）の導出、Issue コメントの
   読み取りと既存 start PASS 報告の分類（下記「報告コメントの重複判定」）。ここで失敗・識別不能が
   あれば fetch より前に ABORT し、Git にも Issue にも何も書かない
1. `git fetch <remote> <base>`、`FETCH_HEAD` と `refs/remotes/<remote>/<base>` の一致確認
2. 新規時: `git worktree add --no-track -b <branch> <worktree_dir> <remote>/<base>`
   再利用時: 下記「再利用の判定」を満たすことを検証し、Git には何も書かない
3. `<worktree_dir>/.venv` → `<MAIN_REPO>/.venv` の symlink（未作成時のみ作成）
4. main に `.kaji/config.local.toml` がある場合のみ `<worktree_dir>/.kaji/config.local.toml` の
   symlink（未作成時のみ作成）
5. `git worktree list --porcelain` で `<worktree_dir>` が `<branch>` で登録されていることを再確認
6. Issue 本文先頭に NOTE（`build_worktree_note_body` と同一文字列）。同一 NOTE が既に先頭にあれば省略
7. 報告コメント（現行 SKILL.md Step 5 の本文と同じ表・注意・次のステップ・verdict block）。
   1 行目 marker は現行と同じ `<!-- kaji-verdict: step=start status=PASS -->`（metadata を付けない）。
   0 で「同一の start PASS 報告あり」と分類した場合は投稿しない
8. verdict（PASS）: `KAJI_VERDICT_PATH` へ pure YAML、stdout へ `---VERDICT---` block。exit 0

ABORT 時:

- verdict（ABORT、`suggestion` 必須）を同じ 2 経路へ出力し **exit 0**（runner の `on: ABORT` 遷移を
  成立させるため。exec_script の非ゼロ終了は `ScriptExecutionError` = ERROR になり ABORT 遷移しない）
- evidence に「失敗した操作・コマンド・終了コード・stderr 末尾」と「この時点で完了済みの副作用」
  （例: worktree 作成済み / NOTE 済み）を書く。suggestion に再実行コマンド
  `kaji issue start <issue_id>` を含める
- ABORT 時はコメントを投稿しない（現行どおり）
- 事前検証（0）の ABORT は書き込み前に止まるため、evidence の完了済み副作用は「なし」となる

想定外の例外（バグ）は捕捉せず traceback + 非ゼロ終了とし、既存の exec_script fail-loud 契約に従う。

### 再利用の判定（部分成功からの再実行）

`git worktree list --porcelain` と `git show-ref` から次の状態を判定する（`worktree_dir` は
realpath 比較）。

| 状態 | 判定 |
|------|------|
| `worktree_dir` 未登録・パス不在・`refs/heads/<branch>` 不在 | 新規作成 |
| `worktree_dir` が `branch refs/heads/<branch>` で登録済み、ディレクトリ実在 | 再利用（Git 書き込みなし。reset / checkout / clean / 削除をしない） |
| `worktree_dir` が別 branch / detached で登録済み | ABORT |
| `<branch>` が別パスの worktree で checkout 済み | ABORT |
| `<branch>` は存在するが worktree 未登録 | ABORT（既存 branch を自動で attach しない） |
| `worktree_dir` が未登録だがパスが存在 | ABORT |
| 登録済みだがディレクトリ不在（prunable） | ABORT |

symlink（`.venv` / overlay 共通）:

| 状態 | 判定 |
|------|------|
| 不在 | 作成 |
| symlink で `os.readlink` が期待パス（`<MAIN_REPO>/.venv` 等の絶対パス）と一致 | そのまま |
| symlink で参照先が異なる、または実ファイル / ディレクトリ | ABORT（上書き・削除しない） |

再利用時でも fetch と照合は必ず実行し、失敗したら ABORT する（ゲート順序を変えない）。
報告の「基点」は、新規時は現行どおり `<remote>/<base>` の短縮 SHA。再利用時は作成時の基点を
記録した証跡が無い（branch の reflog は利用者の操作で変わりうる）ため作成時の基点を主張せず、
`git merge-base <branch> <remote>/<base>` の短縮 SHA を「現在の `<remote>/<base>` との共通祖先」と
明記して表示する（利用者が新しい base を merge / rebase していれば作成時の基点とは異なりうる）。
再利用である旨を報告コメントの表に 1 行追加する。

### 使用例

```bash
# workflow（runner が exec_script として起動。agent は起動しない）
#   .kaji/wf/official/dev.yaml
#   - id: start
#     skill: issue-start
#     on: { PASS: design, ABORT: end }

# 手動（main checkout で実行。/issue-start もこのコマンドを 1 回呼ぶだけ）
kaji issue start 569
# 途中で止まった場合も同じコマンドを再実行する（同一 worktree を検証して再利用）
kaji issue start 569
```

```python
# kaji_harness/scripts/issue_start.py（entry）
def main(argv: list[str] | None = None) -> int:
    """KAJI_ISSUE_ID / 位置引数から 1 プロセスで start を実行し、verdict を出力して 0 を返す。"""
```

## 制約・前提条件

- Python 3.11+ / 既存の `kaji_harness` 規約（ruff / mypy strict / Google docstring）。
- Git 操作は `subprocess.run([...], shell=False)` で行い、引数はリストで渡す（シェル展開しない）。
- `gh` 直叩きをしない。Issue 操作は `IssueProvider`（`view_issue` / `edit_issue` / `comment_issue`）を使う。
- ローカルの `<base>` は更新しない（現行どおり）。`git pull` / `reset` / `clean` / `branch -D` /
  `worktree remove` を一切呼ばない。
- verdict は #568 の `normalize_verdict_fields` → `validate_verdict_fields`（`{"PASS", "ABORT"}`）→
  `dump_verdict_yaml` を通し、`fsio.atomic_write` で書く（手組みの YAML を書かない）。
- `start` は `NON_RESUMABLE_SKILLS` の対象のまま（workflow step は `skill: issue-start` を保持する）。
- 依存: #568（main `004114b` で取り込み済み）。

## 方針

### 構成

| ファイル | 変更 |
|----------|------|
| `kaji_harness/issue_start.py`（新規） | 中核ロジック。`StartPlan`（context と導出値）、worktree 状態判定、symlink 判定、NOTE 判定、報告本文生成、`run_issue_start(...) -> StartOutcome` |
| `kaji_harness/scripts/issue_start.py`（新規） | exec_script / CLI 共通 entry。引数・env 解決、provider 構築、verdict 出力 |
| `kaji_harness/commands/issue.py` | `kaji issue start <issue_id>` を追加し entry の `main` へ委譲（`kaji pr review-poll` → `review_poll_entry.main` と同じ形） |
| `.claude/skills/issue-start/SKILL.md` | frontmatter に `exec_script: kaji_harness.scripts.issue_start`。本文は script の契約のミラー（入出力・副作用順・ABORT 条件・再実行）と、手動時の `kaji issue start` 1 コマンド手順に置換。複数 block の変数受け渡しを削除 |
| `.kaji/wf/official/{dev,docs}.yaml`、`.kaji/wf/custom/dev/{dev-small,dev-thorough,dev-thorough-codex}.yaml`、`.kaji/wf/custom/docs/{docs-codex,docs-thorough-codex}.yaml` | `start` から `agent` / `model` / `effort` を削除（`skill: issue-start` と `on:` は不変） |
| `kaji_harness/providers/markers.py` | 変更なし（`build_kaji_verdict_marker` / `parse_kaji_verdict_marker` を metadata なしで使う） |

### 処理の流れ（疑似コード）

```python
def run_issue_start(issue_id, provider, cwd, env) -> StartOutcome:
    ctx = provider.resolve_issue_context(issue_id)            # 正本は 1 回だけ取得
    check_env_matches(ctx, env)                               # 不一致 → Abort
    identity = ReportIdentity(ctx.branch_name, f"../{Path(ctx.worktree_dir).name}")
    marker = build_kaji_verdict_marker("start", "PASS")       # 書き込み前に構築（固定値）
    existing = classify_start_reports(provider.view_issue(issue_id).comments, identity)
    if existing is UNIDENTIFIABLE: Abort                      # 書き込み前に停止
    main_repo = git_toplevel(cwd)
    fetch(main_repo, ctx.git_remote, ctx.default_branch)      # 失敗 → Abort
    fetched = rev_parse(main_repo, "FETCH_HEAD^{commit}")
    tracking = rev_parse(main_repo, f"refs/remotes/{remote}/{base}^{{commit}}")
    if fetched != tracking: Abort                             # silent fallback しない
    state = classify_worktree(main_repo, ctx.worktree_dir, ctx.branch_name)
    if state is NEW: worktree_add_no_track(...)
    elif state is not REUSABLE: Abort
    ensure_symlink(wt / ".venv", main_repo / ".venv")         # 異なる参照先 → Abort
    if (main_repo / ".kaji/config.local.toml").is_file():
        ensure_symlink(wt / ".kaji/config.local.toml", ...)
    confirm_registered(main_repo, wt, branch)
    base_sha, local_ahead = report_values(...)
    ensure_note(provider, issue_id, wt.name, branch)          # 同一 NOTE が先頭なら省略
    if existing is not SAME:                                  # 同一報告があれば省略
        provider.comment_issue(issue_id, marker + "\n" + render_report(identity, ...))
    return Pass(...)
```

各操作は `Abort(reason, evidence, completed_side_effects)` を返す（例外ではなく値で返し、entry で
verdict に変換する）。provider 呼び出しの `GitHubProviderError` は entry で ABORT に変換する。

### 報告コメントの重複判定

**同一性の鍵は報告本文の表に置き、marker metadata は使わない。** 現行テンプレート（SKILL.md Step 5）
が出力してきた報告にも、本 Issue の script が出力する報告にも、同じ 2 行が必ずある:

```text
| ブランチ | <branch_name> |
| ディレクトリ | ../<worktree_dir の basename> |
```

旧形式（agent 投稿、例: [#569 の start コメント](https://github.com/apokamo/kaji/issues/569#issuecomment-6880885684)
は `| ブランチ | fix/569 |` / `| ディレクトリ | ../kaji-fix-569 |`）と新形式を **同じ関数** で生成・照合する
（生成は `render_report`、照合は `parse_report_identity`。行ラベルと値の書式を 1 箇所の定数で共有）。
marker metadata を使わないのは、worktree basename が `[paths].worktree_prefix` の許容域
（`config.py` `_validate_worktree_prefix`: `[A-Za-z0-9._-]+`、`.` / `..` 以外）により `_` / `.` / `-` で
始まりうる一方、marker metadata 値の文法（`markers.py`: `^[A-Za-z0-9][A-Za-z0-9._/-]*$`）はそれを
表現できないため。表の値は自由文字列で、許容域内のすべての basename と branch
（`<branch_prefix>/<issue_id>`）をそのまま表現でき、`|` や backtick を含まないので表の解析も崩れない。

判定手順（事前検証 0 で実行、Issue への書き込み前）:

1. 各コメントの 1 行目を `parse_kaji_verdict_marker` で解析し、`step == "start" and status == "PASS"` の
   ものだけを候補とする（metadata の有無・内容は問わない）。marker の無いコメント、他 step、
   `status != PASS` は対象外
2. 候補ごとに本文から `| ブランチ | … |` 行と `| ディレクトリ | … |` 行を抽出する。セル値は前後の空白と、
   値全体を囲む 1 組の backtick を除いて比較する（旧形式で agent が backtick を付けた場合を許容）
3. 候補を分類する:

| 候補の状態 | 分類 |
|------------|------|
| 2 行とも 1 件ずつあり、値が `branch_name` と `../<basename>` に完全一致 | SAME（同一報告） |
| 2 行とも 1 件ずつあり、どちらかの値が異なる | OTHER（別の start の報告。同一扱いしない） |
| どちらかの行が無い、または同じラベルの行が複数あり値が食い違う | UNIDENTIFIABLE |

4. Issue 全体の判定: SAME が 1 件以上 → **投稿しない**（他に UNIDENTIFIABLE があっても重複の危険は
   無いため続行）。SAME が無く UNIDENTIFIABLE がある → **事前検証で ABORT**（同一か判定できない報告がある
   状態で新規投稿すると重複しうるため。metadata が無いことだけでは UNIDENTIFIABLE にならない）。
   それ以外（候補なし / OTHER のみ）→ 処理後に 1 件投稿する

UNIDENTIFIABLE による ABORT の suggestion: 「該当コメント（URL / 作成日時）を確認し、同一 start の報告なら
表の `ブランチ` / `ディレクトリ` 行を現在の値で補うか、別物なら 1 行目の marker を外してから
`kaji issue start <issue_id>` を再実行する」。この ABORT は書き込み前なので再実行は安全。

marker は現行の `step=start status=PASS` から変えないため、consumer（`issue-design` Step 1.6 の BACK 検出は
`status=(BACK|BACK_DESIGN)` のみ照合）と `docs/dev/shared_skill_rules.md` の marker 契約に影響しない。

### NOTE の重複判定

`build_worktree_note_body` が生成する NOTE 文字列（3 行）を `current_body.lstrip("\n")` が先頭に
持つ場合は編集しない。それ以外は現行どおり `build_worktree_note_body` で先頭に追加する
（先頭に別 branch の NOTE がある場合の挙動も現行と同じ = 追加）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 自動化の範囲 | start の機械的手順を script 化し、正常時の Git/Issue 副作用・順序・workflow 遷移を維持 | Issue 本文「重要判断」1 項目目（保守者の決定、2026-11-13） | 副作用表（本書「現在の start 副作用」）の #1〜#8 を 1 プロセスに移し、順序を「出力」1〜8 で固定。`on:` 遷移は全 7 workflow で不変 |
| 部分成功の扱い | 期待パス/ブランチが一致する worktree のみ検証して再利用。利用者の変更を破壊しない。不一致は ABORT | Issue 本文「重要判断」2 項目目、EB 3 項目目（保守者の決定） | 「再利用の判定」表で 7 状態を列挙。Git 書き込み禁止コマンドを制約に明記。NOTE / コメントの重複防止条件を定義 |
| 自動 recovery gate | 緩めない | Issue 本文「重要判断」2 項目目・「範囲外」（保守者の決定） | workflow step の `skill: issue-start` を保持し `NON_RESUMABLE_SKILLS` の判定対象に残す。`recovery/` は変更しない |
| script / CLI の構成 | `exec_script` skill + `kaji issue start` CLI が同一 entry に合流 | Issue 本文「重要判断」3 項目目で「詳細な script/CLI 構成は設計レビューで決める」と委任。**AI の仮定**: 前例 `review-poll`（`exec_script` と `kaji pr review-poll` が `review_poll_entry.main` に合流）に倣う。検査先: review-design | `exec:` step 化ではなく `skill:` + `exec_script` を選ぶ理由 = non_resumable gate が skill 名で判定するため。CLI 追加は加算的で既存 CLI を変えない（two-way door） |
| ABORT の出力形態 | 想定内の失敗は ABORT verdict + exit 0、想定外の例外は非ゼロ終了 | **AI の仮定**: 現行 SKILL.md は失敗時 ABORT verdict を出す契約で、exec_script の非ゼロ終了は ERROR となり `on: ABORT` 遷移が成立しない（`script_exec.py` docstring / `baseline_precheck._emit_guard_abort` の前例）。検査先: review-design / review-code | provider エラー（NOTE / コメント失敗）も ABORT とし、完了済み副作用と再実行コマンドを evidence / suggestion に書く |
| overlay symlink の既存参照先 | `ln -sf` の上書きをやめ、異なる参照先は ABORT | Issue 本文 EB「既存 symlink の異なる参照先は ABORT する」（保守者の決定） | 同一参照先は no-op、実ファイルも ABORT。`.venv` にも同じ規則を適用 |
| env と provider context の不一致 | ABORT（どちらも採らない） | **AI の仮定**: Issue 本文は `kaji issue context` を正本と定める一方、runner は state / discovery で worktree を上書きしうる（`runner.py` `_load_session_state`）。黙って片方を選ぶと別 worktree を作りうる。検査先: review-design | 通常の初回 run では両者は同値（runner も `resolve_issue_context` を使う）なので正常系の挙動は変わらない |
| 同一 start PASS 報告の判定 | marker `step=start status=PASS`（metadata 不問）+ 本文表の `ブランチ` / `ディレクトリ` 行の値で判定。旧形式・新形式を同じ規則で扱い、識別不能な報告しか無い場合は書き込み前に ABORT | 決定: Issue 完了条件「既に投稿済みの同一 start PASS 報告を重複させない」（保守者の決定、形式を限定していない）。**AI の仮定**（判定鍵の選択）: 現行テンプレートが両行を必ず出力しており、旧形式にも同一性の根拠がある（review-design 指摘 R1 で #569 の実コメントを確認）。検査先: verify-design / review-code | 分類表（SAME / OTHER / UNIDENTIFIABLE）と Issue 全体の判定規則、値の正規化（空白・囲み backtick）を定義 |
| marker metadata を使わない | marker は現行の `step=start status=PASS` のまま | 既存契約: `[paths].worktree_prefix` の許容域（`config.py` `_validate_worktree_prefix`）を狭めない（Issue「範囲外: branch naming の変更」）。**AI の仮定**: 許容域の basename（`_` / `.` / `-` 始まり）は metadata 値文法で表現できないため、符号化を足すより既存の表に鍵を置く方が生成・照合の規則が 1 つで済む（review-design 指摘 R2）。検査先: verify-design | 生成（`render_report`）と照合（`parse_report_identity`）で行ラベル・値書式の定数を共有。marker 構築は事前検証で行う |
| 再利用時の報告基点 | `merge-base <branch> <remote>/<base>` の短縮 SHA を「現在の共通祖先」と明記して表示し、作成時の基点とは主張しない | **AI の仮定**: 作成時の基点を記録した証跡が無いため、事実として言える値だけを出す（review-design 改善提案 S1、[git merge-base](https://git-scm.com/docs/git-merge-base)）。検査先: review-code | 新規時は現行どおり `<remote>/<base>` の短縮 SHA |

one-way door の未決: 該当なし。公開 CLI は加算のみ（既存 `kaji issue` サブコマンドの引数・終了コードを
変更しない）、永続化 schema（`verdict.yaml` / state / run.log）は不変、workflow 遷移は不変、
recovery gate は不変。いずれも Issue 本文の決定範囲内の詳細化または後段で安く直せる仮定である。

## テスト戦略

### 変更タイプ

- 実行時コード変更（`kaji_harness/` 新規 module・CLI 追加・workflow YAML・skill frontmatter）

### 再現テスト（bug 固有）

- **実装前 Red の証跡**: #560 の実ログ（OB 1、`verdict.yaml` の `fatal: no path specified`）を
  escape clause に基づく実装前 Red の代替とする。実ログは「SKILL.md の複数 block 間で変数が失われる」
  という OB を直接示し、下記恒久テストはその EB（1 プロセスで context → worktree 作成が完結する）を検証する。
- **恒久回帰テスト（修正前 FAIL / 修正後 PASS）**:
  1. Small: `issue-start/SKILL.md` の bash block 群について、ある block で代入したシェル変数を別の
     block で参照していないこと（現行は Step 1 の `REMOTE` / `BASE` を Step 2 block が参照するため FAIL）、
     かつ frontmatter が `exec_script: kaji_harness.scripts.issue_start` を宣言すること（現行 FAIL）。
  2. Large(local): `KAJI_*` env を一切持たない状態で entry `main(["<id>"])` を呼び、fake provider
     （context のみ返す）と bare remote + clone の git fixture で、remote の main だけが進んだ状態から
     fetched `origin/main` を基点に `--no-track` の worktree が作られること（現行には entry が無く FAIL）。

### 実行時コード変更の場合

#### Small テスト

- worktree 状態判定（porcelain 文字列 + ref 有無 → NEW / REUSABLE / 各 ABORT 理由）の 7 状態
- symlink 判定（不在 / 同一参照先 / 異なる参照先 / 実ファイル）
- NOTE 判定（同一 NOTE が先頭 / 無し / 別 branch の NOTE が先頭 / 空本文）
- 報告の同一性判定（`classify_start_reports` / `parse_report_identity`、指摘 R1）:
  - 旧形式: 現行テンプレートで agent が埋めた本文（#569 の実 start コメント本文を fixture 化）が、同じ
    branch / `../<basename>` に対して SAME、別 branch または別ディレクトリに対して OTHER
  - 新形式: `render_report` の出力を `parse_report_identity` に通すと同じ鍵に戻る（生成と照合の往復）
  - 値の正規化: 囲み backtick・前後空白付きの旧形式も SAME
  - UNIDENTIFIABLE: ブランチ行欠落 / ディレクトリ行欠落 / 同ラベル行が複数で値が食い違う
  - 対象外: marker 無し、`step=start status=ABORT`、他 step の PASS、marker が 2 行目以降にあるもの
  - Issue 全体: SAME+UNIDENTIFIABLE → 投稿しない（続行）、UNIDENTIFIABLE のみ → ABORT、OTHER のみ / 候補なし → 投稿
- 合法な worktree prefix（指摘 R2）: `worktree_prefix` が `kaji` / `_kaji` / `.kaji` / `-kaji`（および
  `build_worktree_dir` の既定）の各 basename について、`build_kaji_verdict_marker("start", "PASS")`・
  `render_report`・`parse_report_identity` の往復・PASS verdict の `validate_verdict_fields` がいずれも
  例外なく成立すること
- 報告本文生成: 現行 SKILL.md Step 5 の項目（Issue / ブランチ / ディレクトリ / 基点 / venv /
  config overlay / メタ情報 / LOCAL_AHEAD 注意 / 次のステップ / クリーンアップ / verdict block）を含むこと
- env と context の不一致検出
- verdict 変換: PASS / ABORT の 4 フィールドが `validate_verdict_fields({"PASS","ABORT"})` を通り、
  ABORT は suggestion 非空・再実行コマンドを含むこと
- 静的契約: 全 7 workflow の `start` が `skill: issue-start` を持ち `agent` / `model` / `effort` を
  持たず、`on:` が変更前と同じ（dev 系 `PASS: design` / dev-small `PASS: baseline` / docs 系
  `PASS: doc-update`、`ABORT: end`）。`non_resumable_skill_hits(workflow, "start", "start")` が hit すること
- 既存 `tests/test_skill_issue_start_base.py` の静的テストは block 抽出を前提としているため、script の
  契約（fetch → 照合 → `--no-track` 作成の順、ローカル `<base>` 非基点）を検証する形へ置き換える。
  差分範囲の同根箇所テスト（`_DIFF_RANGE_USERS`）は無関係なので維持する
- `tests/test_skill_verdict_marker_producers.py` は issue-start を「SKILL.md 本文に
  `--verdict-step start --verdict-status PASS` を持つ PASS 専用 reporter」として検査している。
  marker 付与の正本が script に移るため、issue-start は script 側の Small テスト（marker 文字列と
  PASS 時のみ投稿）で検証し、本テストの対象からは外す（他 skill の検査は維持）
- `tests/test_skill_harness_adaptation.py` 等 issue-start を参照する既存テストは、SKILL.md 本文の
  置換に合わせて期待値を更新する。recovery 系（`test_recovery_models.py` / `test_recovery_plan.py` /
  `test_retry_failed_step.py` / `test_series_resume.py`）は skill 名で gate を検査しており、
  変更せず PASS し続けることを gate 非緩和の証跡とする

#### Medium テスト

- runner 結合（`tests/test_runner_exec_script_dispatch.py` の型）: `skill: issue-start`（exec_script）
  の start step が `execute_script(module="kaji_harness.scripts.issue_start")` で dispatch され、
  agent CLI を起動しないこと。stdout の PASS で次 step、ABORT で `end` に遷移すること
- 同上を `agent_runner = "interactive_terminal"`（Herdr）設定で実行し、`execute_interactive_terminal`
  が呼ばれず exec_script 経路になること（headless / Herdr の代表 workflow）
- `kaji issue start` CLI: 位置引数 1 個で entry `main` に委譲し、第 2 引数で usage error（exit 2）・
  副作用なし（provider / git を呼ばない）
- preflight: 変更後の全 7 workflow が `kaji validate` 相当の検証（agent 無し + exec_script 有り）を通ること

#### Large テスト

`large_local`（実 git subprocess、ネットワーク無し）。fixture は `outside_project_tmp_path` に作る
（既存 `test_skill_issue_start_base.py` と同じ理由）。provider は fake（`view_issue` / `edit_issue` /
`comment_issue` / `resolve_issue_context` を記録）。

- 正常系: 上記回帰テスト 2 に加え、`.venv` symlink、main に overlay がある場合/無い場合の overlay
  symlink、NOTE 1 回、`<!-- kaji-verdict: step=start status=PASS -->` marker 付きコメント 1 回、
  `KAJI_VERDICT_PATH` の PASS verdict、操作順序（fake provider の呼び出し記録と git 状態で確認）
- LOCAL_AHEAD: ローカル main に未 push commit がある場合、基点に含まれず報告に注意が出る
- fetch 失敗（到達不能 remote）: ABORT、branch / worktree / symlink 無し、provider の書き込み呼び出し 0 回
- tracking ref 不一致（非標準 `remote.origin.fetch`）: ABORT、同上
- worktree のパス/ブランチ不一致（別 branch で登録 / branch が別パスで checkout / branch のみ存在 /
  未登録パスが存在）: ABORT、既存 branch・パスの内容が不変、書き込み呼び出し 0 回
- symlink 衝突（`.venv` が別参照先 / overlay が別参照先）: ABORT、既存 symlink 不変、NOTE・コメント無し
- 部分成功からの再実行:
  - worktree 作成済み・symlink 未作成・利用者 commit と未 commit 変更あり → PASS、commit と変更が保持、
    symlink 作成、NOTE 1 回、コメント 1 回
  - NOTE 済み・コメント未投稿（provider の `comment_issue` を 1 回目だけ失敗させて ABORT）→ 再実行で
    PASS、NOTE は増えず、コメントは 1 件
  - コメント投稿済み → 再実行で PASS、コメントは増えない
  - **旧形式の PASS 報告が既にある（#551 型、指摘 R1）**: 現行テンプレートの本文で同じ branch /
    `../<basename>` の start PASS コメントと NOTE を fake provider に事前配置し、worktree に利用者の commit と
    未 commit 変更を置いて再実行 → PASS、NOTE・コメントとも増えない、commit と変更が保持される
  - 旧形式で **別 branch / 別ディレクトリ** の start PASS 報告だけがある → 新規作成・再利用いずれでも
    1 件投稿される（同一扱いしない）
  - 識別不能な start PASS 報告だけがある → ABORT、fetch を含む Git 操作・NOTE・コメントが 0 回
    （remote-tracking ref と branch / worktree が不変）
- 合法な worktree prefix（指摘 R2）: `[paths].worktree_prefix` を `_kaji` / `.kaji` / `-kaji` に設定した
  fixture（parametrize）で、初回 PASS（worktree `../<prefix>-fix-<id>`、branch `fix/<id>`）、報告 1 件、
  再実行で PASS かつコメント非重複、branch / worktree 名が context と一致して不変
- 正常時にローカル `<base>` の ref が変わらないこと

外部 API（GitHub）との疎通は既存 provider の責務でテスト済みのため、本 Issue では追加しない
（fake provider で呼び出し契約を検証する。provider 実装自体は不変）。

### 長時間 acceptance の配置

該当なし。nested `kaji run`・外部 agent を含む dogfood は完了条件に無く、追加テストはすべてローカル
git fixture と fake provider で数秒〜数十秒に収まる（既存 `test_skill_issue_start_base.py` の
large_local と同程度）。配置先 implement step の timeout の 25% を超える検証は無い。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（exec_script / CLI 合流は既存パターン） |
| docs/ARCHITECTURE.md | あり | deterministic script 一覧・start の dispatch 経路（agent → exec_script）の記述を更新 |
| docs/dev/workflow_guide.md | あり | start step が agent ではなく script で動くこと、再実行方法（`kaji issue start`） |
| docs/dev/development_workflow.md / workflow_overview.md / docs_maintenance_workflow.md | あり（該当記述があれば） | issue-start を agent step として説明している箇所の整合 |
| docs/dev/skill-authoring.md | あり | exec_script skill の例に issue-start を追加 |
| docs/dev/shared_skill_rules.md | なし（marker 契約） | start の marker は現行の `step=start status=PASS` のまま。issue-start が SKILL.md からコメントを投稿しなくなる旨の記述が producer 一覧にあれば、script が付与する形へ整合させる |
| docs/guides/git-worktree.md | あり | worktree 作成手順の正本記述を script 契約・再実行規則へ更新 |
| docs/cli-guides/failure-recovery.md(.ja) | あり | `non_resumable_skill`（start）で停止した場合の手動復旧として `kaji issue start <id>` の再実行を案内 |
| docs/reference/configuration.md(.ja) | あり（該当記述があれば） | `kaji issue context` / issue-start の説明の整合 |
| docs/reference/ | なし（上記以外） | API 規約の変更なし |
| docs/cli-guides/（上記以外） | あり | `kaji issue start` サブコマンドの追加を `kaji issue` の説明箇所へ記載 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| .claude/skills/issue-start/SKILL.md | あり | 本 Issue の主対象 |
| managed starter | 本 Issue では変更しない | release 後の `/update-starter` 追随で反映（既存運用） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #569 本文 | https://github.com/apokamo/kaji/issues/569 | EB・完了条件・重要判断（自動化範囲 / 部分成功 / グリル不要）・範囲外（自動 recovery gate 緩和、一般コメント統合） |
| #560 実障害ログ | `.kaji-artifacts/560/runs/261113010347/steps/start/attempt-001/verdict.yaml`（main checkout） | 「別 shell で実行され、REMOTE と BASE が block 内で未設定」「fatal: no path specified」 |
| #551 実障害ログ | `.kaji-artifacts/551/runs/261113020532/recovery.json`（main checkout） | `VerdictParseError ... evidence: |` と `non_resumable_skill (failed_step=start, skill=issue-start)` |
| 現行 start 契約 | `.claude/skills/issue-start/SKILL.md` | Step 1〜5 と verdict 規則。本書「現在の start 副作用」の出典 |
| exec_script dispatch | `kaji_harness/script_exec.py`（module docstring）、`kaji_harness/runner.py` `_dispatch_kind` / `_dispatch` / `_build_context_env` | 非ゼロ終了は `ScriptExecutionError`（verdict 有無を問わない）。exec_script は agent_runner に関係なく subprocess 実行。`KAJI_*` env の注入 |
| runner の context 上書き | `kaji_harness/runner.py` `_load_session_state`（state / `discover_existing_worktree` による worktree 上書き） | env と provider context が一致しない可能性の根拠 |
| recovery gate | `kaji_harness/recovery/models.py:33`、`kaji_harness/recovery/gates.py` `non_resumable_skill_hits` / `_step_skill` | gate は step の `skill` 名で判定 → `skill: issue-start` を保持する理由 |
| 合流の前例 | `.claude/skills/review-poll/SKILL.md`、`kaji_harness/scripts/review_poll_entry.py` | exec_script と `kaji pr review-poll` が同一 entry `main` に合流 |
| ABORT 出力の前例 | `kaji_harness/scripts/baseline_precheck.py` `_emit_verdict` / `_emit_guard_abort` | ポリシー ABORT を verdict 出力 + `return 0` で返す |
| verdict 検証（#568） | `kaji_harness/verdict.py` `normalize_verdict_fields` / `validate_verdict_fields` / `dump_verdict_yaml` | 読み込み側と同一規則で正規化・検証し、読み戻し一致を確認して直列化 |
| marker 文法 | `kaji_harness/providers/markers.py` `build_kaji_verdict_marker` / `parse_kaji_verdict_marker` | metadata 値は `^[A-Za-z0-9][A-Za-z0-9._/-]*$`（英数字始まり）。`_` / `.` / `-` 始まりの basename を表現できない（R2 の根拠）。parse は 1 行全体を照合し metadata の有無を問わない |
| worktree_prefix の許容域 | `kaji_harness/config.py` `_validate_worktree_prefix`、`kaji_harness/providers/context.py` `build_worktree_dir` | `[A-Za-z0-9._-]+`（`.` / `..` 以外）を許容し、basename は `<prefix or "kaji">-<branch_prefix>-<issue_id>`。パイプ記号と backtick を含まない（表の解析が崩れない根拠） |
| 旧形式の start PASS 報告 | https://github.com/apokamo/kaji/issues/569#issuecomment-6880885684 | 1 行目 `<!-- kaji-verdict: step=start status=PASS -->`、表に `ブランチ` 行 = `fix/569`、`ディレクトリ` 行 = `../kaji-fix-569`。旧形式にも同一性の鍵がある根拠 |
| git merge-base | https://git-scm.com/docs/git-merge-base | 2 つの commit の最良の共通祖先を返す。branch の作成時点を記録するものではない（S1 の根拠） |
| NOTE 合成 | `kaji_harness/providers/context.py` `build_worktree_note_body` | 既存 NOTE を見ずに先頭へ追加する（再実行で二重化する根拠） |
| git worktree | https://git-scm.com/docs/git-worktree | `add -b` は既存 branch で失敗。`list --porcelain` の `worktree` / `branch` / `detached` / `prunable` 行で登録状態を機械判定できる |
| git fetch / FETCH_HEAD | https://git-scm.com/docs/git-fetch | `FETCH_HEAD` は fetch した ref を記録。remote-tracking ref の更新は refspec 設定に依存（照合が必要な根拠、#549） |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義（large_local = subprocess あり / ネットワーク無し）、workflow 内 `tmp_path` と project 外 fixture |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | 再現テスト必須、実ログによる実装前 Red 代替の escape clause |
