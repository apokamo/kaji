# [設計] LocalProvider を main worktree に固定して local issue 操作の cwd 依存を解消する

Issue: gl:11

## 概要

`kaji issue {comment,edit,create,close}` の LocalProvider 経路を「**bare repo の primary worktree (= main worktree)** のファイルツリーで一貫して行う」よう固定する。cwd が feature worktree に置かれていても、`.kaji/issues/<id>/` の書き込みと付随する `git add` / `git commit --only` が main worktree に対して実行されるようにする。

## 背景・目的

### Observed Behavior (OB)

`provider.type='local'` 設定下で feature worktree (e.g. `/home/aki/dev/kaji/kaji-fix-11`) を cwd として:

```bash
kaji issue comment local-p1-23 --commit --body "test"
```

を実行すると、以下が発生する。

1. `KajiConfig.discover()` が cwd から walk-up し、**feature worktree 配下の** `.kaji/config.toml` を見つける（[`kaji_harness/config.py:109-119`](../../kaji_harness/config.py)）
2. `repo_root = path.parent.parent` により `repo_root = /home/aki/dev/kaji/kaji-fix-11`（feature worktree のルート）になる
3. `LocalProvider.comment_issue()` が `repo_root / .kaji / issues / <id> / comments / <ts>-<machine>.md` に atomic write する → **feature worktree のファイルツリーにファイルが落ちる**（[`kaji_harness/providers/local.py:615-640`](../../kaji_harness/providers/local.py)）
4. `_commit_local_issue_change()` が `cwd=provider.repo_root` で `git add` / `git commit --only` を呼ぶ → **feature branch (`fix/N`) に commit が積まれる**（[`kaji_harness/cli_main.py:1212-1234`](../../kaji_harness/cli_main.py)）
5. main worktree のファイルツリーには反映されず、main HEAD も進まない
6. `cd ../main && kaji issue view local-p1-23 --comments` でそのコメントは見えない

実例（`local-p1-23` で再現済み）: commit `e286c13` が `fix/local-p1-23` に積まれ、`/i-dev-final-check` が「verify-code 未実施」と誤判定して BACK で停止。復旧として cherry-pick (`c60ff6e`) が必要だった（Issue 本文 § Observed Behavior）。

### Expected Behavior (EB)

local issue (`local-*` ID) に対する LocalProvider の **すべての write 操作**（`create_issue` / `edit_issue` / `comment_issue` / `close_issue`）と、CLI 層が紐付ける `git add` / `git commit --only` は、cwd と無関係に **`provider.local.default_branch` を checkout している worktree (= main worktree)** に対して実行される。

read 操作（`view_issue` / `list_issues`）も同じ main worktree のファイルツリーを参照する。これは「local issue の正本は main branch」という [`CLAUDE.md` § Git & GitHub](../../CLAUDE.md) の規約と整合する:

> 以下は **main 直コミット許容**:
> - `chore(local)`: kaji local Issue ファイル (`.kaji/issues/`) の追加・更新...

GitHub / GitLab provider（数値 ID / `gh:N` / `gl:N`）の経路は外部 API 呼び出しが主であり、ローカルファイル書き込みは `.kaji/cache/` の read-only ファイル参照に限られるため、本変更の対象外（既存挙動維持）。

### 再現手順（steps-to-reproduce）

1. 前提:
   - `.kaji/config.local.toml` で `provider.type='local'` / `provider.local.default_branch='main'` を設定
   - bare repo + worktree 構成（`main` worktree と `fix/N` worktree が共存）
   - main 上に `local-pc1-23` の Issue ディレクトリが存在
2. `cd /path/to/feature-worktree` （feature worktree を cwd にする）
3. `kaji issue comment local-pc1-23 --commit --body "test"` を実行
4. **観測される出力 (OB)**:
   - `feature-worktree/.kaji/issues/local-pc1-23-*/comments/<ts>-pc1.md` が作成される
   - `git log --oneline -1` （feature worktree）に `chore(local): comment for local-pc1-23` が積まれる
   - `git -C /path/to/main log --oneline -1` には反映されない

## 根本原因（Root Cause）

| # | 箇所 | 問題 |
|---|------|------|
| 1 | [`kaji_harness/config.py:109-119`](../../kaji_harness/config.py) `KajiConfig.discover()` | cwd から walk-up して最初に見つかった `.kaji/config.toml` の親ディレクトリを `repo_root` とする。worktree 内では feature worktree がヒットする |
| 2 | [`kaji_harness/providers/__init__.py:101-114`](../../kaji_harness/providers/__init__.py) `get_provider()` の local 分岐 | `repo_root=config.repo_root` を **そのまま** LocalProvider に渡す。main worktree 解決は行わない |
| 3 | [`kaji_harness/providers/local.py:332-340`](../../kaji_harness/providers/local.py) `LocalProvider._issues_dir` / `_counter_path` | `self.repo_root` をベースに path を組む。`repo_root` が feature worktree のままなら書き込み先も feature worktree |
| 4 | [`kaji_harness/cli_main.py:1212-1234`](../../kaji_harness/cli_main.py) `_commit_local_issue_change()` | `cwd=provider.repo_root` で `git add` / `git commit --only` を呼ぶ。`repo_root` が feature worktree のままなら commit 先も feature branch |

「いつから壊れているか」: LocalProvider 導入時（Phase 3-ab, commit `dc2cce7`, 2025 末）から構造的に存在。Phase 3-c で CLI dispatch が確定 / Phase 3-d で commit 動線 (`--commit` フラグ) が確定したが、main worktree への redirect は一度も実装されていない。

「他に同じ原因で壊れている箇所」: `create_issue` / `edit_issue` / `close_issue` も `repo_root` 経由でファイル書き込みするため、`--commit` フラグが無くても **file 書き込み先が feature worktree** になる点で同根。`create` / `close` は `--commit` が無いため commit 時のずれは発生しないが、その代わり「main worktree から見えない Issue ディレクトリが feature worktree に作られる」という形で問題が顕在化する。

`view_issue` / `list_issues` も同じく `repo_root` 経由で読むため、cwd によって見える Issue が変わる split-brain も同根（Issue 本文「同根調査」要件）。

## インターフェース

CLI 表層の I/F は変更しない（破壊的変更なし）。`LocalProvider` の構築点を変える。

### 変更前

```python
# kaji_harness/providers/__init__.py
return LocalProvider(
    repo_root=config.repo_root,           # cwd から discover した worktree root
    machine_id=local_cfg.machine_id,
    default_branch=local_cfg.default_branch,
    git_remote=local_cfg.git_remote,
)
```

### 変更後

```python
# kaji_harness/providers/__init__.py
main_root = resolve_main_worktree(
    start_dir=config.repo_root,
    default_branch=local_cfg.default_branch,
)
return LocalProvider(
    repo_root=main_root,                  # main worktree の絶対パス
    machine_id=local_cfg.machine_id,
    default_branch=local_cfg.default_branch,
    git_remote=local_cfg.git_remote,
)
```

#### `resolve_main_worktree()` の契約

新規 helper（配置: `kaji_harness/providers/_worktree.py` 等の小モジュール、または `local.py` 末尾 module-level）。

- **入力**:
  - `start_dir: Path` — `git -C` の作業ディレクトリ（`config.repo_root` を渡す）
  - `default_branch: str` — `provider.local.default_branch`（既定 `"main"`）
- **出力**: `Path` — `default_branch` を checkout している worktree の絶対パス
- **動作**:
  1. `git -C <start_dir> worktree list --porcelain` を `subprocess.run` で実行
  2. porcelain 出力をブロック単位（空行区切り）でパースし、`worktree <path>` と `branch refs/heads/<name>` を抽出
  3. `<name> == default_branch` であるブロックの `<path>` を返す
- **失敗ケース**:
  | 条件 | 振る舞い |
  |------|---------|
  | `git CLI が PATH 上に無い`（`FileNotFoundError`） | `start_dir.resolve()` を返す（fallback）。downstream の `git add` / `git commit` がより明確な「git: command not found」エラーで失敗する。後方互換のための明示仕様（既存テスト fixture が非 git tmp_path に対し `get_provider()` を呼ぶ経路を維持）|
  | `git worktree list` が exit != 0（非 git repo） | `start_dir.resolve()` を返す（fallback）。production の `provider.type='local'` 利用では下記「`default_branch` に一致する worktree が無い」のほうが先に hit するため実害が無い。後方互換のための明示仕様 |
  | `default_branch` に一致する worktree が無い（=作業者が main worktree を作っていない） | `LocalProviderError` を raise。`git worktree add <path> <default_branch>` を案内 |
  | 同じ branch を複数 worktree が checkout している（git で通常起きないが防御） | 最初に見つかったものを採用。stderr に warning を 1 行出す |
  | porcelain 出力が parse 不能 | 上記 `default_branch` 不一致経路に合流（一致 0 件として `LocalProviderError`） |

  **fallback 採用の根拠**: production の `provider.type='local'` 利用者は git repo + main worktree を必ず持つ前提（§ 制約・前提条件）。一方、kaji harness の medium テストは「config 解析 / dispatch 経路 / preflight」を検証するため非 git tmp_path に対し `get_provider()` を呼ぶ fixture が 20+ ファイル存在する。fail-fast にすると test 側に `git init` + `git worktree add` setup の追加が必要になり、本 Issue scope を超える広域改修になる。git CLI 不在 / 非 git repo は production では到達せず、到達した場合も「main worktree が見つからない」error message のほうが actionable であるため、fallback 採用は機能仕様として正当。
- **副作用**: stdout / stderr は変えない（warning 経路のみ stderr 1 行）。ファイル書き込み無し
- **冪等性**: 同じ git 状態に対して呼ぶたびに同じ Path を返す

#### `LocalProvider.repo_root` の意味の変更

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 何を指すか | cwd から discover した worktree のルート | `default_branch` を checkout している worktree のルート |
| `_issues_dir` の起点 | feature worktree | main worktree |
| `_counter_path` の起点 | feature worktree | main worktree |
| `_cache_dir` の起点 | feature worktree | main worktree |
| `_commit_local_issue_change` の `git -C <cwd>` | feature worktree | main worktree |

`KajiConfig.repo_root` は変更しない（config discovery の起点 / `paths.artifacts_dir` 解決などはそのまま）。`LocalProvider.repo_root` のみ main worktree に固定する。これにより:

- GitHub / GitLab provider の `repo_root` は従来通り cwd 起点（`gh` / `glab` CLI の `cwd` 期待値に変化なし）
- LocalProvider の `repo_root` は main worktree 固定（local issue ファイルの正本は常に main）

### 使用例

```bash
# feature worktree を cwd にしていても、main worktree に commit される
cd /home/aki/dev/kaji/kaji-fix-11
kaji issue comment local-pc1-23 --commit --body "intermediate finding"
# → /home/aki/dev/kaji/main/.kaji/issues/local-pc1-23-*/comments/<ts>-pc1.md が作られ、
#    main branch に commit される

cd /home/aki/dev/kaji/main
git log --oneline -1
# → chore(local): comment for local-pc1-23
```

skill 側からは「main worktree に居る／居ない」を意識する必要がなくなる。

## 制約・前提条件

- **git CLI が PATH 上にあること**（既存の `_commit_local_issue_change` と同じ前提）
- **`default_branch` を checkout している worktree が存在すること**。bare repo であっても primary worktree (`main`) は明示的に `git worktree add` で作る運用（[`docs/guides/git-worktree.md:54-60`](../../docs/guides/git-worktree.md)）であり、これが無い構成は kaji local mode の動作前提を満たさない → 起動時に fail-fast
- **`default_branch` は同時に 1 つの worktree でのみ checkout されている**（git の通常制約）
- **GitHub / GitLab provider への影響なし**: `GitHubProvider` / `GitLabProvider` は `gh` / `glab` 経由で API を叩くため `repo_root` 依存が最小（`gh issue comment` は cwd と無関係に動く）
- **`.kaji/config.toml` が main / feature worktree で乖離していないこと**: `provider.local.default_branch` の値が両方の worktree で一致している前提。乖離した場合は discovery が拾った値（= cwd 側の値）を採用する。これは「設定ファイルは branch 横断で同期する」git 利用上の通常前提
- **検証期間中の単一 user 前提**: 同 repo の複数 worktree から並行に `kaji issue comment local-*` を投げると、common な `.kaji/counters/<machine>.txt` への flock + atomic file write で衝突は保護される（既存実装）

## 方針

### 1. `resolve_main_worktree()` 実装

`kaji_harness/providers/_worktree.py` を新規追加（または `local.py` に module-level helper として収める）。

```python
def resolve_main_worktree(*, start_dir: Path, default_branch: str) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start_dir), "worktree", "list", "--porcelain"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        # git CLI not on PATH → fallback (§ 失敗ケース表)
        return start_dir.resolve()
    if proc.returncode != 0:
        # 非 git repo → fallback (§ 失敗ケース表)。production の provider.type='local'
        # は git repo 前提（§ 制約・前提条件）。test fixture 互換のための明示仕様。
        return start_dir.resolve()
    target = f"refs/heads/{default_branch}"
    current: dict[str, str] = {}
    matches: list[Path] = []
    for line in proc.stdout.splitlines() + [""]:
        if line == "":
            if current.get("branch") == target and "worktree" in current:
                matches.append(Path(current["worktree"]))
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if not matches:
        raise LocalProviderError(
            f"no worktree found for branch {default_branch!r}. "
            f"Run 'git worktree add ../main {default_branch}' (or adjust "
            f"provider.local.default_branch)."
        )
    if len(matches) > 1:
        sys.stderr.write(
            f"warning: multiple worktrees checking out {default_branch!r}; "
            f"using {matches[0]}\n"
        )
    return matches[0].resolve()
```

実装の要点:

- porcelain 出力は「ブロック区切り = 空行」「`key value` 行」の単純フォーマット。`git-worktree(1)` 公式（[Primary Sources](#参照情報primary-sources) 参照）
- `--porcelain` を使うことで人間向け表示揺れ（`(bare)` カラム等）を避ける
- `start_dir` を `git -C` で渡すことで「Python の cwd」と「git の cwd」を切り離す（Bash cwd リセット問題への耐性）

### 2. `get_provider()` で main worktree を解決して LocalProvider に渡す

[`kaji_harness/providers/__init__.py:101-114`](../../kaji_harness/providers/__init__.py) の local 分岐内で `resolve_main_worktree()` を呼び、戻り値を `LocalProvider(repo_root=...)` に渡す。`LocalProvider` 自身のシグネチャは変えない（呼び出し側で確定したものを受け取る）。

理由:

- 検証はモジュール境界で 1 度だけ。LocalProvider 内に呼び出すと「直接 `LocalProvider(...)` を組む既存テスト fixture が壊れる」（[`tests/test_local_issue_commit_flag.py:51`](../../tests/test_local_issue_commit_flag.py)）
- 既存テストは `LocalProvider(repo_root=tmp_path, ...)` で local mode を構成しており、これらは 1 worktree しかない通常 git repo として動く（= `default_branch` を checkout した worktree が 1 つ存在）。テスト側は無改修で通る

### 3. CLI 層の修正は不要

[`_commit_local_issue_change()`](../../kaji_harness/cli_main.py) は既に `cwd=provider.repo_root` を使っている。`provider.repo_root` が main worktree に固定されるため、CLI 層の修正は不要。

### 4. skill SKILL.md の整理

Issue 本文の完了条件「commit 先の cwd を意識する必要なし」を反映する形で、関連 skill の文言を見直す。

- [`/issue-close` SKILL.md` Step 2 "メインリポジトリのパスを特定"](../../.claude/skills/issue-close/SKILL.md): `kaji issue {comment,edit}` を呼ぶ前に `cd $MAIN_REPO` する記述があれば不要 / 推奨外と明記
- 他 skill（`/issue-design` / `/issue-fix-code` / `/i-dev-final-check` 等）で `kaji issue comment local-*` を呼ぶ箇所: 「main worktree に移動する必要なし」を 1 行追記
- ただし **git の編集系操作（`git add` / `git commit` を skill が直接叩く場面）には適用しない**。あくまで `kaji issue ...` CLI を経由する場合の規約

詳細な書き換え対象は実装時に grep で網羅する（影響ドキュメント表 § skill）。

### 5. 関連挙動の意図的非対応

- `KajiConfig.discover()` の挙動は変えない。`.kaji/config.toml` は worktree 間で（git 経由で）同期している前提。「main から overlay が乖離している」エッジは外部運用問題として扱う
- GitHub / GitLab provider の repo_root は据え置き
- `.kaji/cache/` は LocalProvider が触る範囲では main worktree 配下を読む（read-only）。`kaji sync from-gitlab` 等の書き込み経路も main worktree を更新するように redirect される（同じ provider が掴むので自動的に整合）

## テスト戦略

### 変更タイプ
- **実行時コード変更**（Python 実装の変更）

### Small テスト

[`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) の判定基準「外部依存なし。純粋なロジック」に該当する範囲を対象とする。

- `resolve_main_worktree()` の porcelain parser 単体テスト
  - 単一 worktree: 1 ブロックで branch 一致 → そのパスを返す
  - 複数 worktree（main + feature N 件）: branch 一致は 1 件のみ → main のパス
  - bare repo の場合の `bare\n` 行を含むブロック: skip される（branch 行が無いため）
  - branch 一致が 0 件: `LocalProviderError`
  - branch 一致が複数件（防御的入力）: 1 件目を返し stderr に warning
  - porcelain 出力末尾の改行有無を含むパース堅牢性

parser だけを切り出して subprocess を mock / 引数で `output: str` を直接受ける形にすれば Small で網羅可能。

### Medium テスト（bug 固有: 再現テスト必須）

[`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) の判定基準「ファイル I/O / サブプロセス / 内部サービス結合」に該当（実 git CLI + tmp_path を使う）。**bug 固有ルール: 修正前に Red、修正後に Green になる再現テストを必ず含める**。

検証観点:

1. **再現テスト（regression）**: `tmp_path` で bare repo + 2 worktree (`main`, `feature/x`) を組み、`feature` 配下を Python の cwd にして `_local_issue_comment(provider, [issue_id, "--body", "...", "--commit"])` を呼ぶ。修正前は feature worktree に commit が積まれることを assert（OB の再現）→ 修正後は **main worktree** に commit が積まれ、feature worktree の HEAD は不変であることを assert
2. **read-side（split-brain 防止）**: feature 配下を Python cwd にして `view_issue` / `list_issues` を呼んだ結果が、main worktree 配下のファイル内容と一致すること（cwd によって結果が変わらないこと）
3. **forge-backed 経路の回帰**: 同じ feature worktree から `kaji issue` の `gl:N` / 数値 ID 経路を呼んだ場合に、`GitLabProvider` / `GitHubProvider` の挙動が一切変化しないこと（mock 経由でも OK。実 API は叩かない）
4. **`create` / `edit --commit` / `close` の対称性**: feature worktree cwd から各操作を呼び、ファイル書き込み先が main worktree であること（`create`: ディレクトリ作成、`edit`: `issue.md` 更新、`close`: frontmatter `state=closed`）
5. **main worktree 不在のエラー経路**: `default_branch` の worktree が存在しない構成で `LocalProviderError` が raise されること、message に remediation 案内が含まれること
6. **`default_branch` を非 `main` に設定したケース**: `release` 等の別 branch を default にした overlay 構成でも対応 worktree に redirect されること

### Large テスト

不要。判断根拠:

1. 実外部 API 疎通は本変更スコープに含まれない（LocalProvider は外部 API を叩かない）
2. 既存 `tests/test_phase3e_large_local.py` / `test_phase4_large_local.py` の E2E 系は、subprocess で `kaji` CLI を呼ぶ既存の Large カバレッジ。本変更で挙動が変わるのは「同 CLI を feature worktree cwd から叩いたとき」だけであり、これは Medium で十分に再現できる
3. [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) の 4 条件:
   - 独自ロジック追加は git porcelain parser に限られ Small でカバー
   - 想定不具合（cwd 取り違え）は Medium 再現テストで捕捉
   - Large 追加で増える回帰検出シグナルが小さい
   - Large 不要の理由をレビュー可能な形で本セクションに記載

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし。既存規約 (`CLAUDE.md` の main 直コミット) への準拠を実装で担保するのみ |
| `docs/ARCHITECTURE.md` | なし（実装時に再確認） | LocalProvider / `repo_root` への言及が既存 ARCHITECTURE.md に無く、provider 抽象の章自体が存在しない。本 Issue の修正点は LocalProvider 内部実装に閉じるため、ARCHITECTURE 側の新規セクション追加は本 Issue scope 外。利用者向け案内は `docs/cli-guides/local-mode.md` に集約する |
| `docs/dev/` | なし（実装時に再確認） | `development_workflow.md` / `workflow_guide.md` は workflow 構造を説明する文書で、`kaji issue comment local-*` の具体手順は記載されていない。`cwd 不問` は CLI 利用者向け案内であり `docs/cli-guides/local-mode.md` に集約済み |
| `docs/reference/` | なし | Python 規約 / docstring style への影響なし |
| `docs/cli-guides/local-mode.md` | あり | LocalProvider の「main worktree 固定」挙動と、`default_branch` 未存在エラーのトラブルシュート節を追加 |
| `CLAUDE.md` | なし | 規約は変えない。本変更は既存規約をツール側で守れるようにする実装 |
| `.claude/skills/*/SKILL.md` | なし | 実装時の grep（`kaji issue X local` 形式 / `cd $MAIN_REPO`）で該当 skill 記述は hit せず。`/issue-close` の `cd $MAIN_REPO` は git ops (`git merge` / `git branch -d`) 用途で残置妥当。skill 側で「main worktree に cd」を案内している記述は存在しない |

**注**: 設計時点では `docs/ARCHITECTURE.md` / `docs/dev/` / `.claude/skills/*/SKILL.md` を「あり」と評価したが、実装時の調査で当該記述が存在しないことが判明したため、影響表を実態に合わせて更新した。Pre-Handoff Review および `/issue-review-code` の Must Fix #2 指摘に対応する整合性更新。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `git-worktree(1)` 公式 — `--porcelain` 出力仕様 | https://git-scm.com/docs/git-worktree#_porcelain_format | "A line-oriented format ... Each attribute is listed on a separate line, with the attribute name first, followed by a space, then the attribute value. ... A blank line separates each worktree." → 空行区切りブロックパーサで安定して読める一次仕様。`worktree <path>` と `branch <ref>` を抽出すれば良い |
| `git-worktree(1)` 公式 — primary worktree の概念 | https://git-scm.com/docs/git-worktree#_description | "The repository ... is called the 'main worktree'. ... Other worktrees are called 'linked worktrees'." → kaji が指す「main worktree」と git の主 worktree 概念が一致することの根拠 |
| `git-commit(1)` 公式 — `--only` の挙動 | https://git-scm.com/docs/git-commit#Documentation/git-commit.txt---only | "Make a commit by first updating the index ... only including the paths specified on the command line, and ignoring any contents that have been staged so far." → `_commit_local_issue_change()` の atomic commit 動線の根拠（既存実装）。本変更は `cwd` を main worktree に切り替えるだけで `--only` 契約は変わらない |
| kaji `CLAUDE.md` § Git & GitHub | [`../../CLAUDE.md`](../../CLAUDE.md) | "`chore(local)`: kaji local Issue ファイル (`.kaji/issues/`) の追加・更新（`kaji issue create/edit/comment/close` の永続化）" → main 直コミットの規約。本変更でツール側がこの規約を守れるようにする |
| kaji `docs/guides/git-worktree.md` § kaji プロジェクトでの運用 | [`../../docs/guides/git-worktree.md`](../../docs/guides/git-worktree.md) | "kaji では Bare Repository パターンではなく、**通常リポジトリ + worktree** パターンを採用している。Issue ごとに worktree を作成し、並列開発を実現する。" → 想定する worktree 配置の一次情報（main / kaji-prefix-N の命名規則を含む） |
| 既存実装 — `_commit_local_issue_change` | [`../../kaji_harness/cli_main.py:1187-1234`](../../kaji_harness/cli_main.py) | `cwd=provider.repo_root` で `git add` / `git commit --only` を呼んでいる箇所。本変更は `provider.repo_root` の値を変えることで間接的に動かす |
| 既存実装 — `LocalProvider._issues_dir` | [`../../kaji_harness/providers/local.py:332`](../../kaji_harness/providers/local.py) | `self.repo_root / ".kaji" / "issues"`。`repo_root` を main worktree に固定すれば全 path 解決が連動して redirect する |
