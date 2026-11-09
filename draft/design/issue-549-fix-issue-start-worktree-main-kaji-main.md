# [設計] issue-start の worktree 基点を fetch 後の `<remote>/<default_branch>` にし、diff 範囲の基点もそろえる

Issue: #549

## 概要

`issue-start` が worktree を **ローカルの `main`** から作るため、kaji の外で remote の `main` が進んでいると古い基点から作業が始まる。
fetch した直後の `[git_remote]/[default_branch]` を基点にし、fetch に失敗したら worktree を作らずに止める。
あわせて、feature branch の差分範囲をローカルの `main` / `[default_branch]` で決めている skill・script を同じ基点にそろえる。

## 背景・目的

### Observed Behavior（OB）

- `.claude/skills/issue-start/SKILL.md:66` は fetch なしで `git worktree add -b "$BRANCH" "$WT" main` を実行する（現行 HEAD `24f9bec` で確認）。
- Issue 本文「実行証跡」（2026-11-09、git 2.43.0、隔離環境）: remote の `main` だけを 1 commit 進めた状態で上記コマンドを実行すると、
  `local main = worktree HEAD = e9e442c`、`origin/main = 54457aa`、`git -C "$WT" rev-list --count HEAD..origin/main` が `1`、
  kaji の外で入った変更（`f.txt` の `v2`）が worktree に含まれない。

### Expected Behavior（EB）

- worktree の基点は fetch 直後の `<remote>/<default_branch>`。remote 名と default branch は provider 設定から解決する（Issue 本文 EB）。
- Issue 本文の比較ログ: fetch 後に `origin/main` から作った worktree は `rev-list --count HEAD..origin/main` が `0`。
- fetch に失敗した場合（到達不能 remote で `exit=128`）は、古い基点で続けずに止まる（Issue 本文 完了条件 2）。

### 再現手順（steps-to-reproduce）

Issue 本文「再現手順」「実行証跡」をそのまま採用する（最小構成）。

1. bare remote と clone を 2 つ作る。片方（保守者の checkout）は `main` を古いまま残し、もう片方から remote の `main` を 1 commit 進めて push する。
2. 保守者の checkout で `issue-start/SKILL.md` Step 2 のコマンド（`git worktree add -b "$BRANCH" "$WT" main`）を実行する。
3. 観測: `git -C "$WT" rev-list --count HEAD..origin/main` が `1`（`origin/main` を見えるようにするための fetch は観測用に別途行う）。

### 根本原因（Root Cause）

1. **なぜ間違っているか**: `issue-start` は「ローカルの `main` が remote の先端と一致している」ことを暗黙の前提にしているが、それを確かめも保証もしない。
   kaji の workflow 内では `issue-close` が merge 後に `git pull [git_remote] [default_branch]`（`issue-close/SKILL.md:572`）でローカル `main` を進めるため、
   kaji 経由の直列運用ではこの前提が偶然成り立つ。GitHub UI での merge、別 machine / 協力者の push、`.kaji/` だけの直コミットの push など、
   kaji の外で remote の `main` が進むと前提が崩れる。基点の正本は PR の base である remote 側の default branch であり、ローカル `main` はその
   キャッシュにすぎない。
2. **いつから**: `git log -S 'worktree add -b' -- .claude/skills/issue-start/SKILL.md` の最古 commit は `6bf52fc`（2026-02-23、
   kamo2 からの skill 移植 #51）で、導入時から `main` 固定・fetch なし。その後の `git_remote` placeholder 化（gl:6。`i-pr` / `issue-close` が対象）
   でも本行は対象外だった。
3. **同じ根で壊れている／壊れうる箇所**（下記「同根箇所の調査結果」）: feature branch の差分範囲をローカルの `main` / `[default_branch]` で
   決めている箇所。現状は基点と差分範囲がどちらもローカル `main` なので **整合しているが**、本修正で基点だけを `<remote>/<default_branch>` に
   変えると、ローカル `main` が古い場合に `main...HEAD` の merge-base が古いローカル `main` になり、**kaji の外で入った commit がレビュー対象差分・
   実装 commit 判定に混入する**。したがって基点と差分範囲は同時にそろえる必要がある。

#### 同根箇所の調査結果

調査コマンド: `grep -rnE "(main|\[default_branch\])\.\.\.?HEAD|worktree add" .claude/ kaji_harness/ docs/`（`draft/` と過去設計書は除外）。

| # | 箇所 | 現状 | 本修正後にローカル `main` が古いと起きること | 対応 |
|---|------|------|------|------|
| 1 | `.claude/skills/issue-start/SKILL.md:66` | `git worktree add ... main`（fetch なし） | 本 Issue の OB | **修正**（本設計の主目的） |
| 2 | `.claude/skills/issue-review-code/SKILL.md:85` | `git diff main...HEAD` | 外部 commit がレビュー対象差分に混入 | **そろえる** |
| 3 | `.claude/skills/i-doc-review/SKILL.md:64` | `git diff main...HEAD` | 同上 | **そろえる** |
| 4 | `.claude/skills/issue-implement/references/pre-handoff-review.md:6,21,45,86` | `git diff main...HEAD` | PHR の入力差分に混入 | **そろえる** |
| 5 | `.claude/agents/kaji-code-reviewer.md:43` | 入力契約 `## Diff` = `git diff main...HEAD` | #4 の入力契約の正本。#4 と食い違う | **そろえる** |
| 6 | `.claude/skills/_shared/report-unrelated-issues.md:13` | 無関係判定の基準 `git diff main...HEAD` | 外部 commit のファイルを「関係あり」と誤判定 | **そろえる** |
| 7 | `.claude/skills/issue-small-change-review/SKILL.md:56,93,96,113,122,181,243` | `[default_branch]...HEAD` / `[default_branch]..HEAD` | 全体レビュー範囲・auto-close 検査・`--scope` 評価に外部 commit/path が混入 | **そろえる**（Issue 本文が名指し） |
| 8 | `.claude/skills/issue-small-change-execute/SKILL.md:54` | `git log --oneline [default_branch]..HEAD` | 状態確認に外部 commit が混入 | **そろえる** |
| 9 | `.claude/skills/issue-design/SKILL.md:181-182`（Step 1.6 観測 2） | `[default_branch]..HEAD` で TOTAL / NON_DESIGN | 外部 commit を「設計後コミット」と数え、BACK 再入判定や fail-safe ABORT を誤発火 | **そろえる** |
| 10 | `kaji_harness/scripts/baseline_precheck.py:62-73`（`_has_implementation_commit`）と `main()`（`KAJI_DEFAULT_BRANCH` のみ参照） | `f"{default_branch}..HEAD"` | 外部の非設計 commit を「実装 commit あり」と判定 → 初回 baseline でも artifact 不在で `baseline_unrecoverable_post_implement` ABORT | **そろえる**（実行時コード） |
| 11 | `docs/dev/baseline-check.md:72` | `issue-small-change-review` の scope を `[default_branch]...HEAD` と記述 | #7 と食い違う | **そろえる**（docs） |
| — | `.claude/skills/review/SKILL.md:136-137` | すでに `git fetch [git_remote] [default_branch]` → `[git_remote]/[default_branch]...HEAD` | — | 対応不要（既に EB の形。整合の参照元） |
| — | `.claude/skills/issue-close/SKILL.md:533-572` | `[git_remote]/[default_branch]` で merge 済み判定、最後に `git pull` | — | 対応不要（ローカル `main` を進める処理は残す） |
| — | `.claude/skills/release/SKILL.md` | `"$GITHUB_REMOTE/main"` で fetch / 比較、`pull --ff-only` | — | 対象外（release は main checkout 上の作業で feature branch の差分範囲ではない） |
| — | `kaji_harness/providers/_worktree.py`（`resolve_main_worktree`） | `refs/heads/<default_branch>` を checkout している worktree を探す | — | 対象外（差分範囲ではなく main checkout の所在解決） |
| — | `docs/guides/git-commit-flow.md:259`、`docs/guides/git-worktree.md:93` | 人間向けの一般的な git 手順例 | — | 対象外（workflow の基点契約ではない。下記「影響ドキュメント」参照） |

## インターフェース

### 入力

- `issue-start`: 既存どおり `issue_id` のみ。`kaji issue context [issue_id]` の出力（`provider.resolve_issue_context()` が正本）から、
  既存の `branch_prefix` / `branch_name` / `worktree_dir` に加えて **`git_remote` / `default_branch`** を取得する。
  どちらも `kaji_harness/config.py`（`provider.github.git_remote` 既定 `origin`、`provider.github.default_branch` 既定 `main`）由来で、
  `kaji issue context` の JSON にすでに含まれている（実測: `"default_branch": "main", "git_remote": "origin"`）。新しい CLI・設定は追加しない。
- review 系 skill: 既存の placeholder `[git_remote]` / `[default_branch]`（`kaji_harness/prompt.py:119-120` が全 skill に注入）を使う。
- `baseline_precheck.py`: 既存の環境変数 `KAJI_GIT_REMOTE`（`kaji_harness/runner.py:667` が注入済み、既定 `origin`）と `KAJI_DEFAULT_BRANCH` を使う。

### 出力

- `issue-start`
  - 変更前: ローカル `main` を基点に worktree を作る。
  - 変更後: `git fetch "$REMOTE" "$BASE"` に成功し、`$REMOTE/$BASE` がいま fetch した commit と一致することを確かめてから、
    `$REMOTE/$BASE` を基点に upstream なし（`--no-track`）で branch と worktree を作る。
  - fetch 失敗、または `$REMOTE/$BASE` が解決できない／fetch 結果と一致しない場合: worktree・branch を作らず `ABORT` verdict を出す。
  - 完了報告の「基点ブランチ」は固定文字列 `main` ではなく `$REMOTE/$BASE`（短縮 SHA 付き）を表示する。
  - ローカル `$BASE` に `$REMOTE/$BASE` へ未 push の commit がある場合は、件数を完了報告に **注意として表示する**（停止はしない。下記方針 4）。
- review 系 skill: 差分範囲の基点表記が `main` / `[default_branch]` から `[git_remote]/[default_branch]` に変わる。範囲の意味（merge-base からの差分）は不変。
- `baseline_precheck.py`: 実装 commit 判定の範囲が `<KAJI_DEFAULT_BRANCH>..HEAD` から `<KAJI_GIT_REMOTE>/<KAJI_DEFAULT_BRANCH>..HEAD` に変わる。
  基点 ref が解決できない場合は silent に初回扱いせず、fail-loud で `ABORT` verdict を出す。verdict の status 集合と artifact 形式は不変。

### 使用例

```bash
# issue-start Step 1（追加分）
REMOTE=$(kaji issue context [issue_id] -q '.git_remote')
BASE=$(kaji issue context [issue_id] -q '.default_branch')

# issue-start Step 2（疑似コード。最終的な文言は implement で確定）
# kaji-issue-start-worktree
MAIN_REPO=$(git rev-parse --show-toplevel)
git fetch "$REMOTE" "$BASE" || exit 1
FETCHED=$(git rev-parse --verify "FETCH_HEAD^{commit}") || exit 1
TRACKING=$(git rev-parse --verify "$REMOTE/$BASE^{commit}") || exit 1
[ "$FETCHED" = "$TRACKING" ] || { echo "error: $REMOTE/$BASE was not updated by fetch" >&2; exit 1; }
git worktree add --no-track -b "$BRANCH" "$WT" "$REMOTE/$BASE"
```

```bash
# review 系（例: issue-review-code Step 1）
cd [worktree_dir] && git diff [git_remote]/[default_branch]...HEAD
```

## 制約・前提条件

- 外部依存の追加なし。git の標準機能のみ（`git fetch <remote> <branch>`、`git worktree add --no-track -b`、`git rev-parse --verify`）。
  `git worktree add --[no-]track` は Issue 実測環境の git 2.43.0 の `git worktree add -h` に存在することを確認済み。
- 既存の失敗系（branch / worktree ディレクトリが既に存在する、`kaji issue context` が失敗する）の扱いは変えない。
- `git fetch <remote> <branch>` は、remote に `refs/remotes/<remote>/*` を更新する fetch refspec が設定されていれば remote-tracking ref も更新する
  （git の "opportunistic remote-tracking branch update"）。非標準の refspec 設定では更新されないことがあるため、FETCH_HEAD との一致検査で
  「いま fetch した commit が基点」であることを保証する（一致しなければ止まる）。
- worktree は ref を main checkout と共有するため、後続 step の worktree 内でも `[git_remote]/[default_branch]` を参照できる。
- `[git_remote]/[default_branch]` は fetch でしか前進しない。後で誰かが fetch して先へ進んでも、`A...HEAD`（merge-base 基準）と `A..HEAD`
  （HEAD から到達可能で A から到達不能な commit）はどちらも feature branch 固有の差分だけを指すため、差分範囲は壊れない。
  したがって review 系 skill に fetch を新たに追加する必要はない（`review` skill の既存 fetch はそのまま）。
- ローカル `main` は更新しない（保守者の main checkout は作業中でありうる。`git fetch <remote> main:main` は checkout 中の branch を更新できない）。
  ローカル `main` の追随は従来どおり `issue-close` の `git pull`、または保守者の操作に任せる。
- `issue-start` はハーネスの `NON_RESUMABLE_SKILLS`（`tests/test_recovery_models.py:45`）に含まれる副作用 skill。fetch は冪等なので、
  fetch 失敗で止まった後の再実行は安全。worktree 作成前に止まるため、半端な branch / worktree は残らない。
- 公開 CLI・設定・artifact 形式・verdict 語彙は変更しない。

## 方針

1. **issue-start（主修正）**
   - Step 1 で `REMOTE` / `BASE` を `kaji issue context` から取得する（既存 3 変数と同じ取得方法）。
   - Step 2 を上の使用例の形にする。worktree 作成手順は 1 つの fenced block（先頭行 `# kaji-issue-start-worktree`）にまとめ、
     テストが block を抽出して実行できるようにする（#548 の `# kaji-skill-tmp-resolve` と同じ方式）。
   - 失敗時（block が非ゼロ終了）の扱いを明記する: 古い基点で再試行・続行せず、`ABORT` verdict を stdout に出して終了する。
     verdict の evidence に失敗したコマンドと終了コード・stderr を書く。`status の選択基準` の ABORT 条件に「fetch 失敗／基点 ref 不一致」を追加する。
   - `--no-track` を付ける（下記「重要判断 provenance」）。
   - Step 5 の報告表の「基点ブランチ」を `$REMOTE/$BASE`（`git rev-parse --short` の SHA 付き）にする。
   - 方針 4: worktree 作成後に `git rev-list --count "$REMOTE/$BASE..$BASE"` を表示し、0 でなければ「ローカル `$BASE` の未 push commit は基点に含まれない」
     と完了報告に注意書きを出す（停止しない）。
2. **review 系 skill / agent 定義**（同根箇所 #2〜#9）
   - 差分範囲の基点表記を `[git_remote]/[default_branch]` に機械的に置換する。範囲演算子（`...` / `..`）は現状のまま維持する。
   - 手動起動時に placeholder の値が分かるよう、各 skill の入力変数表に `git_remote` / `default_branch` が無ければ追記する（`i-pr` の記述形式に合わせる）。
   - `kaji-code-reviewer.md` は agent 定義なので placeholder は注入されない。`## Diff` の説明を「`git diff <git_remote>/<default_branch>...HEAD`
     の全文（main session が `[git_remote]/[default_branch]` を解決して取得・貼付）」とし、#4 と同じ範囲を指すことを明記する。
3. **baseline_precheck.py**（同根箇所 #10）
   - `_has_implementation_commit(worktree, base_ref)` に引数の意味を変える（ref 文字列を受ける。呼び出し側で `f"{git_remote}/{default_branch}"` を組み立てる）。
   - `main()` で `KAJI_GIT_REMOTE`（既定 `origin`）を読み、`_measure` に基点 ref を渡す。`resolve_main_worktree` / `_post_comment` に渡す
     `default_branch` は従来どおり（main checkout の所在解決であり差分範囲ではない）。
   - 基点 ref が `git rev-parse --verify` で解決できない場合は、例外で落ちる・初回扱いに倒すのではなく、既存の `_emit_guard_abort` 経路で
     理由付き `ABORT` verdict を出す（理由コードは既存の命名規則に合わせて implement で決める）。
4. **docs**: `docs/dev/baseline-check.md:72` を #7 に合わせて更新する。

不採用案:
- **ローカル `main` を fast-forward してから従来どおり `main` を基点にする**: 保守者の main checkout が checkout 中の branch は
  `git fetch <remote> main:main` で更新できず、`git -C <main checkout> merge --ff-only` は作業中の checkout を変更する副作用がある。
  また Issue 本文 EB が「基点は `<remote>/main`」と定めているため採らない。
- **review 系を据え置き、issue-start だけ直す**: 上記「根本原因 3」のとおり差分範囲に外部 commit が混入するため採らない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| worktree の基点 | fetch 直後の `<remote>/<default_branch>` | Issue 本文「Expected Behavior」「完了条件 2」（人間決定） | default branch 名も `main` 固定をやめ `kaji issue context` の `default_branch` で解決。FETCH_HEAD との一致検査を追加 |
| remote 名の解決方法 | provider 設定（`kaji issue context` の `git_remote`）から解決 | Issue 本文 EB「remote の名前は provider の設定（`kaji config`）から解決する」（人間決定） | `kaji config` サブコマンドには remote 取得が無いため、同じ config を正本とする `kaji issue context`（`provider.resolve_issue_context()`）の `git_remote` を使う。新 CLI は追加しない |
| fetch 失敗時の挙動 | worktree を作らず止まる（`ABORT`） | Issue 本文「完了条件 2」（人間決定） | 基点 ref 未解決・FETCH_HEAD 不一致も同じく停止条件に含める |
| upstream 設定（`--no-track`） | 付ける（現行と同じく upstream なし） | AI の仮定。Issue 本文「設計で扱う点」が設計に委任。根拠: 現行（ローカル branch 起点）は upstream なしで、`i-pr` は `git push -u [git_remote] HEAD`（`i-pr/SKILL.md:140`）で upstream を設定する。`origin/main` を upstream にすると `push.default=upstream` 等の設定下で素の `git push` が default branch へ向かう危険がある。検査先: review-design、Large テスト（upstream 未設定の確認） | `git worktree add --no-track -b` |
| 同根箇所の範囲 | 表 #2〜#11 をそろえる | Issue 本文「完了条件 3」（人間決定: 調査して必要ならそろえる。issue-small-change-review を例示） | 「必要」の判定基準を「本修正後にローカル `main` が古いと差分・判定に外部 commit が混入するか」と定義し、#10（Python）を含めた |
| ローカル未 push commit の扱い | 基点に含めず、件数を注意表示（停止しない） | AI の仮定。根拠: PR の base は remote 側で、未 push commit を feature branch に含めると PR に混入する。停止すると docs 直コミット運用（`docs/guides/git-commit-flow.md`）で頻繁に止まる。検査先: review-design、review-code | 完了報告に件数を出すだけ |
| behavior テストのサイズ | `large` + `large_local` | AI の仮定。Issue 本文は「Medium」と記載したが、実 `git` subprocess を使う skill block 実行テストは #548（`tests/test_skill_tmp_paths.py`）で `large` + `large_local` に分類しており、`docs/reference/testing-size-guide.md` の `large_local`（subprocess あり / ネットワーク無し）に当たる。`make check`（`pytest` 全 marker）で実行されるため検証範囲は Issue の意図と同じ。検査先: review-design | — |

one-way door の未決: なし（公開 CLI・設定・artifact 形式・verdict 語彙は不変。skill 手順と内部 script の判定範囲の修正で、後段 review で直せる）。

## テスト戦略

### 変更タイプ

- 実行時コード変更（`kaji_harness/scripts/baseline_precheck.py`）＋ skill 手順（instruction）の変更。

### 再現テスト（bug 固有、修正前 Red 必須）

- **静的契約テスト（Small）** `tests/test_skill_issue_start_base.py`（新規、名称は implement で確定）:
  - `issue-start/SKILL.md` の `# kaji-issue-start-worktree` block がちょうど 1 つ存在する。
  - block 内で `git fetch "$REMOTE" "$BASE"` が `git worktree add` より前にあり、fetch 失敗で非ゼロ終了する（`|| exit` 等）。
  - `git worktree add` の start-point が `"$REMOTE/$BASE"` で、`--no-track` を持つ。素の `main` を start-point にしていない。
  - Step 1 で `REMOTE` / `BASE` を `kaji issue context` の `.git_remote` / `.default_branch` から取得している。
  - 失敗時に `ABORT` verdict を出す記述がある。
  - 修正前の `SKILL.md`（block なし、基点 `main`、fetch なし）では FAIL する（実装前 Red。Issue 本文の実行証跡も OB の実ログとして Red の根拠になる）。
- **振る舞いテスト（Large / `large_local`）** 同ファイル: `outside_project_tmp_path` に bare remote・保守者 clone・別 clone を作り（Issue 本文の証跡と同じ構成）、
  block を抽出して実 `bash` で `REMOTE` / `BASE` / `BRANCH` / `WT` を与えて実行する。
  - remote の `main` だけを進めた状態: worktree HEAD が remote の先端と一致（`rev-list --count HEAD..origin/main` が 0）、進んだ側のファイル内容が見える、
    作られた branch に upstream が無い（`git rev-parse --abbrev-ref @{u}` が失敗）。
  - remote の URL を存在しないパスにした状態: block が非ゼロ終了し、`$WT` が存在せず、`$BRANCH` も作られていない。
  - 修正前の手順（`git worktree add -b "$BRANCH" "$WT" main`）では 1 件目が FAIL することを実装時に確認し、実装報告に記録する。

### Small テスト

- 上記の静的契約テスト。
- 同根箇所 #2〜#9 の静的検査: 対象ファイルに `main...HEAD` / `main..HEAD` / `[default_branch]...HEAD` / `[default_branch]..HEAD`（`[git_remote]/` が前に付かないもの）
  が残っていないこと、`[git_remote]/[default_branch]` を含むこと（`tests/test_skill_remote_placeholder.py` と同じ形式）。
  `kaji-code-reviewer.md` は `<git_remote>/<default_branch>` 表記の検査とする。

### Medium テスト

- `tests/test_baseline.py`（既存、実 git の一時 repo を使う medium）:
  - 既存の `_has_implementation_commit` テストは引数を基点 ref に読み替えて維持する。fixture（`_init_baseline_repo` 等）に
    `refs/remotes/origin/main` を `git update-ref` で用意し、`main()` 経由のテストは `KAJI_GIT_REMOTE` を設定する。
  - 新規（再現）: ローカル `main` が `origin/main` より古く、HEAD が `origin/main` から設計書 commit だけ進んでいる状態で、
    `_has_implementation_commit` が `False`（外部 commit を実装 commit と数えない）。修正前の `main..HEAD` 判定では `True` になり FAIL する。
  - 新規: 基点 ref（`origin/main`）が存在しない場合に guard ABORT verdict を出し、初回扱いで計測に進まない。

### Large テスト

- 上記 issue-start block の振る舞いテスト（`large` + `large_local`。外部ネットワーク無し、ローカル bare remote のみ）。
- review 系 skill の差分範囲は skill 手順の文言であり、実 agent を起動する Large は追加しない（`docs/dev/testing-convention.md` の 4 条件:
  独自ロジックなし／置換漏れは Small の静的検査で捕捉／実 agent 起動は回帰情報をほとんど増やさない／理由を本節に記録）。
  `[git_remote]/[default_branch]...HEAD` が外部 commit を除外することは git の merge-base の仕様であり、issue-start の振る舞いテスト内で
  `git diff --name-only origin/main...HEAD` が feature 固有の path だけを返すことを 1 assertion 追加して確認する。

### 長時間 acceptance の配置

該当なし。追加する検証は Small / Medium の pytest、ローカル `git init` / bare remote だけを使う `large_local`（1 件数秒）と `make check` のみ。
nested `kaji run`、外部 agent を含む dogfood、fresh 環境での繰り返し acceptance は含まず、いずれも implement step の timeout の 25% を大きく下回る。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | あり | `docs/dev/baseline-check.md:72` の scope 範囲表記を `[git_remote]/[default_branch]...HEAD` に更新。`docs/dev/workflow_guide.md` は issue-start の基点を記述していないことを implement 時に再確認（記述があれば更新） |
| docs/reference/ | なし | 設定キー（`provider.github.git_remote` / `default_branch`）は既存。`docs/reference/configuration*.md` の説明に「issue-start の基点にも使う」旨があれば追記を検討（implement で確認） |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| docs/guides/ | なし | `git-commit-flow.md:259` / `git-worktree.md:93` は人間向けの一般手順例で、workflow の基点契約ではない |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| managed starter | なし（本 Issue では） | starter への反映は Release 後の `/update-starter` の責務 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #549 本文 | https://github.com/apokamo/kaji/issues/549 | OB の実行証跡（local main = worktree HEAD = `e9e442c`、`origin/main` = `54457aa`、`rev-list --count` = 1）、EB、完了条件、`--no-track` を設計で決める旨 |
| issue-start 現行手順 | `.claude/skills/issue-start/SKILL.md:52-67,132` | fetch なしで `git worktree add -b "$BRANCH" "$WT" main`。報告の基点が固定文字列 `main` |
| issue context の正本 | `kaji_harness/providers/models.py:87-108`、`kaji_harness/config.py:74-75,551-560` | `git_remote`（既定 `origin`）・`default_branch`（既定 `main`）は `provider.github.*` config 由来。`kaji issue context 549` の実出力にも両キーが含まれる |
| placeholder / env 注入 | `kaji_harness/prompt.py:119-120`、`kaji_harness/runner.py:665-667` | 全 skill に `[git_remote]` / `[default_branch]`、exec step に `KAJI_GIT_REMOTE` / `KAJI_DEFAULT_BRANCH` を注入 |
| 既存の EB 形 | `.claude/skills/review/SKILL.md:136-137` | `git fetch [git_remote] [default_branch]` → `git diff [git_remote]/[default_branch]...HEAD` |
| issue-close | `.claude/skills/issue-close/SKILL.md:527-572` | `[git_remote]/[default_branch]` で merge 済み判定。最後に `git pull [git_remote] [default_branch]` でローカルを進める（kaji 内では基点が古くならない理由） |
| i-pr の push | `.claude/skills/i-pr/SKILL.md:140` | `git push -u [git_remote] HEAD` で upstream を設定する（`--no-track` 採用の根拠） |
| baseline_precheck | `kaji_harness/scripts/baseline_precheck.py:62-73,286-312,454` | `f"{default_branch}..HEAD"` で実装 commit を判定、実装 commit ありで artifact 不在なら `baseline_unrecoverable_post_implement` |
| git worktree | https://git-scm.com/docs/git-worktree | `add [-b <new-branch>] <path> [<commit-ish>]`、`--[no-]track`: 「When creating a new branch, if `<commit-ish>` is a branch, mark it as "upstream" from the new branch. This is the default if `<commit-ish>` is a remote-tracking branch.」 |
| git fetch | https://git-scm.com/docs/git-fetch | § CONFIGURED REMOTE-TRACKING BRANCHES: コマンドラインで refspec を与えた fetch でも、`remote.<name>.fetch` に一致する remote-tracking branch は更新される（opportunistic update）。FETCH_HEAD に取得した ref が記録される |
| git revisions | https://git-scm.com/docs/gitrevisions | `A...B`（diff では merge-base(A,B) から B への差分）、`A..B`（B から到達可能で A から到達不能な commit） |
| git branch | https://git-scm.com/docs/git-branch | `branch.autoSetupMerge`: 既定では start-point が remote-tracking branch のとき upstream を設定する |
| テスト規約 | `docs/dev/testing-convention.md`、`docs/reference/testing-size-guide.md` § Large の細分マーカー | `large_local` = subprocess あり / ネットワーク無し。project 外 fixture は `outside_project_tmp_path` |
| 先行例（block 抽出テスト） | `tests/test_skill_tmp_paths.py`（#548） | skill の fenced block を先頭行マーカーで抽出し、実 git fixture で実行する `large` + `large_local` テスト |
| 利用側の実装 | https://github.com/apokamo/kamo2/issues/1647 | fetch 後の `<remote>/main` を worktree 基点・小修正 review の差分基点にそろえた運用報告（Issue 本文・review-ready コメントで参照確認済み） |
