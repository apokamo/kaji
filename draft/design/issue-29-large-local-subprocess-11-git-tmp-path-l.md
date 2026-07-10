# [設計] large_local subprocess テスト 11 件の非 git tmp_path fixture に git init を追加する

Issue: #29

## 概要

`tests/test_phase3e_large_local.py` / `test_phase4_large_local.py` / `test_verdict_e2e.py`
の 3 fixture が **非 git の `tmp_path`** 上で `provider.type='local'` を subprocess 実行する
ため、`resolve_main_worktree()` が `LocalProviderError` を raise し、計 11 件のテストが
FAIL する。3 fixture それぞれに `git init -q --initial-branch=main` を追加し、gl:21
で確立した「系統 A（実 git init fixture）」へ揃える。

## 背景・目的

### Observed Behavior (OB)

`main`（commit `40f479f` 以降）で `source .venv/bin/activate && pytest -q` を実行すると
11 件が FAIL する（`1372 passed, 26 skipped, 11 failed`）。全件が同一例外で落ちる。
本設計作業中に再現確認した実出力（`pytest -m large_local -q`、worktree `fix/29`,
commit `40f479f`）:

```
kaji_harness.providers.local.LocalProviderError: 'git -C
/tmp/pytest-of-aki/pytest-16/.../repo worktree list' failed (exit 128).
provider.type='local' requires a git repository; run from a git worktree
(or 'git init' first), or switch provider.type to a non-local value.
stderr: fatal: not a git repository (or any of the parent directories): .git
```

call stack（`pytest -m large_local` の traceback より）:
`cli_main._handle_issue` → `providers.get_provider()` → `_worktree.resolve_main_worktree()`
→ `proc.returncode != 0`（git exit 128）→ `LocalProviderError` raise。

**FAIL 11 件の内訳**（再現確認済み）:

| ファイル | マーカー | 失敗テスト |
|----------|----------|------------|
| `test_phase3e_large_local.py` | `large`, `large_local` | `test_issue_create_writes_frontmatter` / `test_issue_list_includes_local_id` / `test_issue_close_writes_close_reason` |
| `test_phase4_large_local.py` | `large`, `large_local` | `test_config_provider_type_local` / `test_pr_create_under_local_exits_2` / `test_pr_list_under_local_exits_2` / `test_pr_review_comments_under_local_exits_2` / `test_run_github_workflow_under_local_exits_2` |
| `test_verdict_e2e.py` | `large` のみ | `test_kaji_run_strict_verdict_single_step` / `test_kaji_run_relaxed_verdict_single_step` / `test_kaji_run_multi_step_workflow` |

`pytest -m large_local` 単体では 8 件（前 2 ファイル）、`test_verdict_e2e.py` の 3 件は
`@pytest.mark.large` のみで `large_local` 未付与のため `pytest -q`（全体）で合算 11 件。

### Expected Behavior (EB)

上記 11 件が PASS する。`make check`（`pytest -m "not large_gitlab"`）の FAIL が 0 件になる。

根拠（一次情報）:
- これら 11 件は commit `3aed88d`（gl:21）以前は PASS していた。当時の
  `resolve_main_worktree()` は非 git ディレクトリで `start_dir.resolve()` を返す
  test-compat fallback を持っていた（gl:21 設計書
  `draft/design/issue-21-refactor-drop-test-compat-fallback-in-re.md` § 方針）。
- `provider.type='local'` が git repository を要求するのは gl:21 で確定した
  **正規仕様**。本 Issue はこの仕様を維持し、テスト fixture 側を仕様に合わせる
  （完了条件: `kaji_harness/` 本番コードを変更しない）。

### 再現手順（Steps to Reproduce）

1. 前提: `main`（commit `40f479f` 以降）、`uv sync` 済み、`source .venv/bin/activate`
2. 実行: `pytest -q`（全 11 件）または `pytest -m large_local -q`（8 件のみ再現）
3. 観測: 上記 11 件（または 8 件）が `LocalProviderError` で FAIL（OB の例外）

### 根本原因（Root Cause）

#### なぜ壊れているか

3 fixture が生成する作業ディレクトリは **`git init` されていない**:

| ファイル | fixture | 生成物 | provider 状態 |
|----------|---------|--------|---------------|
| `test_phase3e_large_local.py` | `fresh_repo` | `tmp_path/repo`（`mkdir` のみ） | fixture 時点は `[provider]` 無し。FAIL する 3 テストは `_init_local()`（= `kaji local init`）後に `[provider] type='local'` が追記され、後続の `kaji issue create/list/close` が local provider 解決を踏む |
| `test_phase4_large_local.py` | `local_repo` | `tmp_path/repo`（`mkdir` のみ）→ `kaji local init` | fixture 内で `kaji local init` 済み → `[provider] type='local'` あり |
| `test_verdict_e2e.py` | `_setup_fake_agent_env` の `workdir` | `tmp_path/project`（`mkdir` のみ） | config に `[provider] type='local'` を直書き |

`provider.type='local'` の dispatch（`_handle_issue` / `_handle_pr` / `kaji run`）は
`get_provider()` 内で `resolve_main_worktree()` を呼ぶ。`resolve_main_worktree()` は
`git -C <repo_root> worktree list --porcelain` を実行し、exit != 0（非 git repo は
exit 128）なら `LocalProviderError` を raise する（`kaji_harness/providers/_worktree.py:72-80`）。
非 git の `tmp_path` 上で local provider subprocess を実行する 3 fixture は、この
fail-fast に必ず衝突する。

#### いつから壊れているか

commit `3aed88d` "refactor: drop test-compat fallback in resolve_main_worktree (gl:21)"
（2026-05-14）が、production 不到達の非 git fallback（`returncode != 0` →
`start_dir.resolve()`）を撤去し、両経路で `LocalProviderError` を raise する fail-fast
仕様に変更した。それ以前は fallback が非 git tmp_path を吸収していたため顕在化して
いなかった（= 3 fixture は元から `git init` していないが、fallback に救われていた）。

#### gl:21 の連鎖影響テスト対処と、本件が漏れた経緯

gl:21 は 2 コミットで連鎖影響テストを処理した:

1. `3aed88d`: 連鎖影響テストを「系統 A（実 git init fixture）」と
   「系統 B（`tests/conftest.py` の autouse mock fixture
   `_stub_resolve_main_worktree_for_non_git`）」に分類して対処。
2. `79e62f8` "fix: drop autouse fallback stub and migrate impacted tests for gl:21":
   conftest の autouse mock fixture を **削除**（test infrastructure 層で production
   fallback を再導入してしまう設計だったため）。系統 B 対象テストを、(i) 実 `git init`
   へ移行（大半）、(ii) `patch('kaji_harness.providers.resolve_main_worktree')` の
   明示 mock へ移行（in-process な 2 テスト）に再編した。

> **Issue 本文との差異（明示）**: Issue 本文「根本原因」節は系統 B を「conftest の
> autouse mock fixture が現存する」前提で記述しているが、これは `3aed88d` 時点の
> 状態であり、後続の `79e62f8` で当該 fixture は撤去済み。現行 `tests/conftest.py`
> に `_stub_resolve_main_worktree_for_non_git` は存在しない（`grep` で確認済み）。
> ただし「3 fixture が系統 A 分類から漏れた」という結論は変わらない。

本件の 3 fixture は **subprocess E2E**（別プロセスで `kaji` を起動）であり、
in-process mock（系統 B）は構造上適用不可能 → 系統 A（実 git init）一択。にもかかわらず
`3aed88d` / `79e62f8` いずれの系統 A ファイル列挙からも漏れた。`79e62f8` は
`test_phase4_provider_type.py` 等を移行したが、別ファイルである
`test_phase4_large_local.py` / `test_phase3e_large_local.py` / `test_verdict_e2e.py`
は対象外だった。これが分類漏れの実体。

#### 同根の他の壊れ箇所の調査

本設計作業中に `pytest -q`（全体）を確認した結果、`LocalProviderError` 起因の FAIL は
**上記 11 件のみ**（他ファイルへの波及なし）。`79e62f8` で移行済みの in-process 系
テスト（`test_phase4_provider_type.py` 等）は既に系統 A 化されており再発していない。
したがって修正対象は 3 fixture に閉じる。

## インターフェース

### 入力

本番 IF の変更なし。修正対象は pytest fixture のみ。

### 出力

本番 IF の変更なし。`resolve_main_worktree()` の fail-fast 仕様（gl:21）は維持する。

### 変更後の fixture 挙動

各 fixture が生成する作業ディレクトリが git repository（unborn `main` ブランチ）に
なる。subprocess の `kaji` が `resolve_main_worktree()` を踏んでも、
`git worktree list --porcelain` が当該 worktree を `branch refs/heads/main` として
返すため、解決が成功する。後方互換性: fixture を使う既存の通過テスト（`fresh_repo` の
`test_local_init_*` / `test_failfast_*`、`local_repo` の
`test_run_any_workflow_under_local_passes_provider_match` 等）は `git init` を足しても
provider type も config 内容も変わらないため挙動不変。

## 制約・前提条件

- **本番コード（`kaji_harness/`）を変更しない**。`_worktree.py` の fail-fast 仕様は
  gl:21 の正規仕様であり維持する（Issue 完了条件）。
- subprocess E2E テストでは `subprocess.run` の名前空間 patch を行わない。
  `docs/dev/testing-convention.md` § `subprocess.run` patch スコープ の表に従い、
  dispatch / provider 結合テストは **系統 A（`git init -q --initial-branch=<default_branch>`
  fixture）** で対処する（系統 B の in-process mock は別プロセスには届かない）。
- `default_branch` は 3 fixture いずれも `main`:
  - `fresh_repo`: `_init_local()` 経由の `kaji local init` が overlay に
    `default_branch` を書く（既定 `main`）。
  - `local_repo`: 同上（`kaji local init` 既定）。
  - `_setup_fake_agent_env`: config.toml に `[provider.local] default_branch = "main"`
    を直書き済み。
  よって `git init` は `--initial-branch=main` で揃える。`resolve_main_worktree()` は
  `branch refs/heads/main` を完全一致で探すため、初期ブランチ名の一致が必須。
- **commit は不要**。`git worktree list --porcelain` は commit 0 件・unborn `main` の
  repo でも `branch refs/heads/main` を exit 0 で出力する（§ 参照情報の検証ログ参照）。
  したがって `git config user.*` / `git commit` は不要で、fixture は `git init` のみで
  足りる。
- git CLI は実行環境の PATH 上に既知で存在する（`make check` 前提環境）。
  `_setup_fake_agent_env` は subprocess に `PATH` を **prepend**（`tmp_path/bin` を
  先頭に追加、`os.environ["PATH"]` は保持）するため git は引き続き解決可能。
  fixture 自身が呼ぶ `git init`（pytest プロセス側 `subprocess.run`）も通常 env で動く。

## 方針

gl:21 系統 A のパターン（`tests/test_config.py:501` 等の
`subprocess.run(["git", "init", "-q", "--initial-branch=main", str(<dir>)], check=True)`）を
3 fixture に転記する。

### 修正 1: `test_phase3e_large_local.py` の `fresh_repo`

`repo.mkdir()` 直後に `git init` を追加する。

```python
@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # gl:21: provider.type='local' requires a git repo.
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    ...  # 以降の .kaji/config.toml / .gitignore 生成は不変
```

`subprocess` は同ファイルで import 済み（`_run_kaji` が使用）。

### 修正 2: `test_phase4_large_local.py` の `local_repo`

`repo.mkdir()` 直後、`_write_base_config()` / `kaji local init` の前に `git init` を追加。

```python
@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # gl:21: provider.type='local' requires a git repo.
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    _write_base_config(repo)
    rc = _run_kaji(repo, "local", "init", "--machine-id", "pc1", "--non-interactive")
    ...
```

`subprocess` は同ファイルで import 済み。`github_repo` fixture は provider が `github`
で `resolve_main_worktree()` を踏まないため変更不要（過剰修正を避ける）。

### 修正 3: `test_verdict_e2e.py` の `_setup_fake_agent_env`

`workdir.mkdir()` 直後、`.kaji/` 生成の前に `git init` を追加する。`resolve_main_worktree()`
の `start_dir` は config 探索で確定する `repo_root = workdir`（`.kaji/config.toml` の
親）であり、`git init` 対象は `workdir`。

```python
workdir = tmp_path / "project"
workdir.mkdir()
# gl:21: provider.type='local' requires a git repo.
subprocess.run(["git", "init", "-q", "--initial-branch=main", str(workdir)], check=True)
config_dir = workdir / ".kaji"
...
```

`test_verdict_e2e.py` は `subprocess` をモジュールレベルで import していない
（各テストメソッド内で局所 import）。`_setup_fake_agent_env` で使うため、モジュール
レベルに `import subprocess` を追加する（既存の局所 import は本 Issue のスコープ外
として触らない＝最小侵襲）。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

**テストコードのみの変更（test-fixture-only）**。`kaji_harness/` の実行時 production
コードは一切変更しない。`docs/dev/testing-convention.md` の 4 区分（実行時コード変更 /
docs-only / metadata-only / packaging-only）には直接該当しないが、性質は「production
振る舞いを変えない fixture 整備」であり、恒久回帰テストの新規追加は不要に最も近い。

### 再現テスト（bug 固有ルール: 修正前 Red → 修正後 Green）

`docs/dev/design-by-type/bug.md` § 8 は「修正前に Red になる再現テストを 1 本以上」を
要求する。本件では **既存の失敗 11 テストそのものが再現テストを兼ねる**:

- 修正前: 11 件が `LocalProviderError` で FAIL（OB を再現済み、§ 背景・目的）。
- 修正後: 11 件が PASS（EB）。

これらは OB（`provider.type='local'` subprocess が非 git tmp で落ちる）を直接 assert
する形になっており、Red→Green 遷移が修正の検証になる。**新規の恒久回帰テストは追加
しない**。理由（`testing-convention.md` § docs-only/... の 4 条件に準じて記載）:

1. 独自ロジックの追加なし — fixture に `git init` の 1 行を足すのみ。production
   ロジック・条件分岐は不変。
2. 想定不具合パターンは既存 11 テストで捕捉済み — fixture が非 git のまま回帰すれば
   この 11 件が再び FAIL する。
3. 新規テストを追加しても回帰検出情報が増えない — 「fixture が git repo であること」
   は 11 テストの前提条件であり、11 テストの PASS がそのまま当該前提を保証する。
4. テスト未追加の理由はレビュー可能（本節）。

### Small テスト

不要。新規の純粋ロジックを追加しないため。`resolve_main_worktree()` 自体の
fail-fast 分岐は gl:21 で追加済みの `TestResolveMainWorktreeFailFast`（Small）が既に
カバーしており、本 Issue では production 不変。

### Medium テスト

不要。fixture が触る `git init` / ファイル I/O は、修正対象の 11 件（Large）の前段
セットアップとして検証される。独立した Medium 層の新規検証対象はない。

### Large テスト

修正対象の 11 件がそのまま Large（`large` / `large_local` マーカー、subprocess E2E）。
新規追加はせず、既存 11 件の Red→Green を検証エビデンスとする。

### 検証手順

1. 修正前: `pytest -q` で 11 件 FAIL を確認（実施済み、§ 背景・目的）。
2. 修正後: `pytest -m large_local -q`（8 件）/ `pytest tests/test_verdict_e2e.py -q`
   （3 件）が green。
3. `make check`（`ruff` → `ruff format` → `mypy` → `pytest -m "not large_gitlab"`）
   が FAIL 0 件で通過。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ不変 |
| docs/dev/ | なし | `docs/dev/testing-convention.md` § `subprocess.run` patch スコープ は「系統 A: `git init` fixture」を**ファイル名を列挙せず**規約として記述済み。本修正は当該規約への準拠であり、文面更新を要しない |
| docs/reference/ | なし | API 仕様・規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| CLAUDE.md | なし | 規約変更なし |

> gl:21 設計書（`draft/design/issue-21-refactor-drop-test-compat-fallback-in-re.md`）の
> 系統 A ファイル列挙は本件 3 ファイルを欠くが、これは close 済み Issue の歴史的
> 成果物であり遡及修正しない（Issue gl:29 完了条件にも含まれない）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `kaji_harness/providers/_worktree.py:72-80` | リポジトリ内 | `resolve_main_worktree()` は `git worktree list --porcelain` が `returncode != 0` のとき `LocalProviderError` を raise する fail-fast 実装。非 git repo（exit 128）で必ず衝突する |
| gl:21 設計書 | `draft/design/issue-21-refactor-drop-test-compat-fallback-in-re.md` | 非 git fallback 撤去（fail-fast 化）の正規仕様と、連鎖影響テストの「系統 A / 系統 B」分類の原典 |
| commit `3aed88d` | `git show 3aed88d` | "drop test-compat fallback in resolve_main_worktree (gl:21)"。fallback 撤去と系統 A/B 初期分類。本件 fixture 漏れの起点 |
| commit `79e62f8` | `git show 79e62f8` | "drop autouse fallback stub and migrate impacted tests for gl:21"。conftest の autouse mock fixture を撤去し系統 B を再編。本件 3 ファイルは移行対象外（漏れ） |
| `docs/dev/testing-convention.md` § `subprocess.run` patch スコープ | リポジトリ内 | dispatch / provider 結合テストでは名前空間 patch を禁止し、系統 A（`git init -q --initial-branch=<default_branch>` fixture）で対処する規約 |
| `tests/test_config.py:501` 他 | リポジトリ内 | 系統 A の既存実装パターン: `subprocess.run(["git", "init", "-q", "--initial-branch=main", str(<dir>)], check=True)`。本修正はこれを転記する |
| `git worktree list --porcelain` 検証ログ | 本設計作業中に実行 | unborn `main`（commit 0 件）の `git init` 直後 repo で `git worktree list --porcelain` が `worktree <path>` / `HEAD 0000...` / `branch refs/heads/main` を **exit 0** で出力することを確認。よって `resolve_main_worktree()` は commit 不要で解決でき、fixture は `git init` のみで足りる |
