# [設計] KajiConfig.artifacts_dir の解決基準を main worktree に固定する

Issue: #177

## 概要

`KajiConfig.artifacts_dir` の相対パス解決基準を、現在の「`.kaji/config.toml` を発見した
ディレクトリ（= cwd 起点で discover した worktree のルート）」から「main worktree」に
変更し、feature worktree 内で `kaji run` してもログ／artifacts が main worktree 配下に
集約されるようにする。

## 背景・目的

### Observed Behavior (OB)

feature worktree (`<worktree>`) 内で `kaji run` を実行すると、`paths.artifacts_dir` が
相対パス（デフォルト `.kaji-artifacts`）の場合、run ログは
`<worktree>/.kaji-artifacts/<issue_id>/` に書かれる。`/issue-close` で
`git worktree remove <worktree>` を実行するとログごと消失する。

実コード根拠（`kaji_harness/config.py:110-116`）:

```python
@property
def artifacts_dir(self) -> Path:
    """Absolute path to artifacts directory."""
    expanded = Path(self.paths.artifacts_dir).expanduser()
    if expanded.is_absolute():
        return expanded
    return self.repo_root / self.paths.artifacts_dir
```

`self.repo_root` は `KajiConfig.discover()` が `cwd` から walk-up で最初に発見した
`.kaji/` ディレクトリの親であり、`git worktree add` 由来の worktree でも
`.kaji/config.toml` は tracked のため worktree 配下に存在する。よって feature worktree
内で起動すると `repo_root = <worktree>` となる。

実運用での観測（Issue 本文より）: 現状の `/home/aki/dev/kaji/main/.kaji-artifacts/` には
50+ の issue 別ディレクトリが残っているが、worktree 内で実行され削除済みの worktree と
一緒に消えた run ログは存在しない（復元不可）。

### Expected Behavior (EB)

worktree のどこで `kaji run` しても、相対指定の `paths.artifacts_dir` は **main worktree
（`provider.<type>.default_branch` を checkout している worktree）** 基準で解決され、
ログは `<main>/.kaji-artifacts/<issue_id>/` に集約される。`/issue-close` で feature
worktree を削除してもログは残る。

根拠（一次情報）:

- **gl:11 で `LocalProvider.repo_root` は既に main worktree 固定化済み**
  （[`kaji_harness/providers/__init__.py:113-126`](../../kaji_harness/providers/__init__.py)）。
  artifacts は workflow run の永続化対象であり、worktree のライフサイクルから
  独立させるべき。`LocalProvider` の I/O 正本固定と同じポリシーをそのまま `artifacts_dir`
  にも適用する。
- `git worktree add` は `.kaji/config.toml` を tracked ファイルとして引き継ぐが、
  `.kaji-artifacts/` は `.gitignore` 対象。よって「config の discover 起点 = artifacts の
  書き込み起点」という現在の対応関係は、worktree 文脈で破綻している。

### 再現手順

1. **前提条件**:
   - kaji リポジトリで `git worktree add ../kaji-feat fix/x` により feature worktree 作成
   - `.kaji/config.toml` の `paths.artifacts_dir = ".kaji-artifacts"`（相対指定）
2. **操作**: feature worktree (`/home/aki/dev/kaji/kaji-feat`) に `cd` → `kaji run workflows/x.yaml 123` を実行
3. **観測**: run ログは `/home/aki/dev/kaji/kaji-feat/.kaji-artifacts/123/` に書かれる
   （`/home/aki/dev/kaji/main/.kaji-artifacts/123/` ではない）
4. `git worktree remove /home/aki/dev/kaji/kaji-feat` → ログごと削除される

## 根本原因（Root Cause）

`KajiConfig.discover()` は `Path.cwd()` から `.kaji/config.toml` を walk-up で探索し、
発見した位置を `repo_root` とする（`kaji_harness/config.py:118-129`）。一方
`.kaji-artifacts/` は worktree のライフサイクルから独立させたい永続物。両者の解決基準を
同一視している点が誤り。

**いつから壊れているか**: 初版から構造的に存在。`LocalProvider.repo_root` は gl:11 で
main worktree 固定化されたが、その時 `KajiConfig.artifacts_dir` は対象に含まれず、
ポリシーの不整合が残っていた。

**同根の他壊れ箇所の調査**:

- `kaji_harness/state.py:48,60,77` の `SessionState` は `artifacts_dir` を引数で受け取る
  ため、`KajiConfig.artifacts_dir` の解決を修正すれば自動的に追従する（独立した解決
  経路を持たない）
- `kaji_harness/runner.py:64,198,201,249,253` も `artifacts_dir` を constructor 引数で
  受け取る（`cli_main.py:400` で注入）。同様に自動追従する
- `kaji_harness/cli_main.py:400` が `artifacts_dir=config.artifacts_dir` で値を渡す唯一
  の production 起点。本 issue の修正対象はこの源泉
- `provider.<type>.repo_root` のうち GitHub/GitLab provider は **cwd 起点のまま**で意図
  された設計（`kaji_harness/providers/__init__.py:99-104,131-137`）。これは外部 API
  経由で issue/PR 操作するため worktree 文脈を保つことが正しい。本 issue の修正は
  artifacts のローカル書き込み経路のみが対象で、provider の `repo_root` は変更しない

## インターフェース

bug 修正のため `KajiConfig` の公開 IF は維持する。`cli_main._cmd_run` のみが利用する
artifacts 専用解決ヘルパを新規追加し、`discover()` には副作用を入れない。

### 変更前

```python
# kaji_harness/config.py
@dataclass(frozen=True)
class KajiConfig:
    repo_root: Path
    paths: PathsConfig
    ...

    @property
    def artifacts_dir(self) -> Path:
        expanded = Path(self.paths.artifacts_dir).expanduser()
        if expanded.is_absolute():
            return expanded
        return self.repo_root / self.paths.artifacts_dir

# kaji_harness/cli_main.py:396-400
runner = WorkflowRunner(
    ...
    artifacts_dir=config.artifacts_dir,
    ...
)
```

### 変更後

`KajiConfig` 自体は touch しない（既存テストおよび `cmd_run` 以外の callsite から
見える property の semantics を変えない）。`cli_main._cmd_run` だけが、新規ヘルパ
`resolve_artifacts_dir(config)` を経由して artifacts path を取得する。

```python
# kaji_harness/artifacts.py（新規モジュール）

from __future__ import annotations

from pathlib import Path

from .config import KajiConfig


def resolve_artifacts_dir(config: KajiConfig) -> Path:
    """`kaji run` の artifacts 書き込み先を main worktree 基準で解決する。

    挙動:

    - `paths.artifacts_dir` が絶対パス / `~` 展開後絶対パス → そのまま返す（PR #102 互換）
    - 相対パス + main worktree 解決成功 → ``<main_worktree>/<artifacts_dir>``
    - 相対パス + main worktree 解決失敗（非 git / `default_branch` 未 checkout / git
      CLI 不在 / `provider` 未設定）→ legacy fallback として ``config.artifacts_dir``
      を返す（= ``repo_root/<artifacts_dir>``）

    本関数は `cmd_run` の唯一の callsite から呼ばれることを想定する。他の callsite
    （`cmd_validate` / `kaji issue` dispatch / `provider-type` / `sync` 系）は
    `config.artifacts_dir` を従来通り使ってよい — これらは artifacts への書き込み
    を行わないため、main worktree 解決の subprocess コストを払う必要がない。
    """
    expanded = Path(config.paths.artifacts_dir).expanduser()
    if expanded.is_absolute():
        return expanded
    main_root = _try_resolve_main_worktree(config)
    if main_root is None:
        return config.artifacts_dir  # legacy: repo_root / 相対パス
    return main_root / config.paths.artifacts_dir


def _try_resolve_main_worktree(config: KajiConfig) -> Path | None:
    """provider 情報から main worktree を best-effort 解決する。失敗時 ``None``。

    `provider_overlay_divergence_warning` の best-effort pattern と同じ意図:
    git CLI 不在 / `default_branch` 未 checkout / `provider == None` のいずれも
    raise せず None を返す。
    """
    if config.provider is None:
        return None
    default_branch = getattr(config.provider, config.provider.type).default_branch
    from .providers._worktree import resolve_main_worktree
    from .providers.local import LocalProviderError

    try:
        return resolve_main_worktree(
            start_dir=config.repo_root,
            default_branch=default_branch,
        )
    except LocalProviderError:
        return None
```

```python
# kaji_harness/cli_main.py:396-400（変更後）

from .artifacts import resolve_artifacts_dir

runner = WorkflowRunner(
    ...
    artifacts_dir=resolve_artifacts_dir(config),  # ← 変更点はここだけ
    ...
)
```

**後方互換性**:

- 絶対パス指定時の挙動は不変（PR #102 既対応）
- `KajiConfig.artifacts_dir` property の semantics は不変。`tests/test_config.py` の
  既存テスト群（L238-395）はそのまま PASS
- `cmd_validate` / `kaji issue|pr` dispatch / `provider-type` / `sync` 系（`cli_main.py:252,284,843,2041,2083,2137,2170` の `KajiConfig.discover()` callsite）は
  従来通り `config.artifacts_dir` 経由で repo_root 基準のパスを得る。これらは
  artifacts への実 I/O を行わないため挙動差は表面化しない。`resolve_main_worktree()`
  の subprocess 起動は `cmd_run` のみで発生し、stdout/stderr 契約も保たれる
- `cmd_run` のみが新挙動。`config.artifacts_dir` を直接読まなくなるが、`SessionState` /
  `WorkflowRunner` 内部は constructor 引数 `artifacts_dir` で受け取るため API 変更なし

**設計判断（discover() に副作用を入れなかった理由）**:

レビュー指摘の通り `KajiConfig.discover()` は `cmd_run` 以外にも複数の callsite を
持つ（`cli_main.py:252,284,843,2041,2083,2137,2170`）。`discover()` 自体に `git
worktree list --porcelain` 起動を仕込むと:

1. `cmd_validate` / `kaji issue list` 等の read-only 経路で不要な subprocess コスト
2. `resolve_main_worktree()` は複数一致時に stderr へ warning を書く（`providers/_worktree.py:92-95`）。
   `kaji issue` の stdout/stderr 契約に副作用を持ち込みうる
3. callsite が増えれば fallback 失敗時の影響範囲を予測しにくくなる

これらを避けるため、副作用は「artifacts を実際に書き込む唯一の callsite」である
`cmd_run` に局所化する。

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| **新規** `kaji_harness/artifacts.py` | `resolve_artifacts_dir(config) -> Path` + 内部 helper `_try_resolve_main_worktree(config)` |
| `kaji_harness/cli_main.py` | `_cmd_run` で `config.artifacts_dir` → `resolve_artifacts_dir(config)` に 1 行差し替え。`from .artifacts import resolve_artifacts_dir` を追加 |
| `tests/test_config.py` | 既存テスト群は変更不要（property semantics 不変）。確認のみ |
| **新規** `tests/test_artifacts_dir.py` | Small（resolve_artifacts_dir の 4 分岐網羅 + provider=None 経路）+ Medium（bare + main + feature worktree fixture で run 経路まで検証）|
| `tests/test_resolve_main_worktree.py` の fixture | `bare_with_two_worktrees` を `tests/conftest.py` に移管するか、`test_artifacts_dir.py` 側で同一定義をコピーするかは後述の Should Fix 1 で明示 |
| `docs/ARCHITECTURE.md` | § セッション管理と再開（L221-230 付近）に「`artifacts_dir` の解決基準は main worktree」明記 |
| `docs/dev/workflow-authoring.md` | `artifacts_dir` の解決基準を 1〜2 行で言及（worktree 内 `kaji run` の挙動）|

Scope 外:

- 絶対パス指定時の挙動（PR #102 既対応）
- `.kaji-artifacts/` に溜まった既存ログのマイグレーション（Issue 本文「スコープ外」）
- `LocalProvider` / `GitHubProvider` / `GitLabProvider` の `repo_root` 解決ロジック
  （artifacts とは独立した責務）
- `KajiConfig.artifacts_dir` property の semantics 変更（cmd_run 以外の callsite 影響
  を出さない方針）

## 方針（修正アプローチ）

### Step 1: `kaji_harness/artifacts.py` 新規モジュール

上記「変更後」スニペットの通り `resolve_artifacts_dir(config)` と
`_try_resolve_main_worktree(config)` を実装する。

設計判断:

1. **専用モジュールとして切り出す理由**: `config.py` に閉じ込めると import order
   問題（`config.py` → `providers._worktree` の依存）が生じる。`config.py` を pure に
   保ち、artifacts 解決という workflow run 固有の責務は別モジュールに置く
2. **`LocalProviderError` 握り潰し**: gl:28 の `provider_overlay_divergence_warning`
   と同じ best-effort パターン。fallback 先は legacy の `config.artifacts_dir`
   （= `repo_root / 相対パス`）。非 git 環境や `default_branch` 未 checkout の既存
   ユーザーへの破壊的変更を避ける
3. **遅延 import**: `_try_resolve_main_worktree` 内で `providers._worktree` を import。
   `KajiConfig._load()` の純粋性を保つ（test fixture が `kaji_harness.config` を
   import するだけで provider 経路が連鎖ロードされないよう既存パターンを踏襲）

### Step 2: `cli_main._cmd_run` の差し替え

`config.artifacts_dir` → `resolve_artifacts_dir(config)` の 1 行変更 +
import 追加。他の `KajiConfig.discover()` callsite には触れない。

### Step 3: ドキュメント更新

- `docs/ARCHITECTURE.md` § セッション管理と再開（L221-230 付近）: `<artifacts_dir>` の
  解釈を「`paths.artifacts_dir` が相対なら main worktree 基準、絶対ならそのまま」と
  明記。注として「`kaji run` 経路のみが main worktree 解決を行う」を添える
- `docs/dev/workflow-authoring.md`: `kaji run` の artifacts 書き込み先について
  worktree 内実行時の挙動を 1〜2 行で言及

## テスト戦略

### 変更タイプ
- **実行時コード変更**（`KajiConfig` の property 解決ロジック変更 + `discover()` の挙動追加）

### Small テスト

新規 `tests/test_artifacts_dir.py` に追加:

1. **`resolve_artifacts_dir` 分岐網羅**（`_try_resolve_main_worktree` を `unittest.mock.patch`
   で mock）:
   - 絶対パス指定 → そのまま返す（mock を呼ばない経路の確認）
   - `~/...` 指定 → `expanduser()` 後絶対化（既存 PR #102 互換）
   - 相対パス + `_try_resolve_main_worktree` が `Path("/some/main")` を返す → `/some/main/<artifacts_dir>`
   - 相対パス + `_try_resolve_main_worktree` が `None` を返す → fallback で `config.artifacts_dir`（= `repo_root` 基準）
2. **`_try_resolve_main_worktree` 分岐網羅**:
   - `config.provider is None` → `None`
   - `resolve_main_worktree` が `LocalProviderError` → `None`（握り潰し確認）
   - 正常系 → `resolve_main_worktree` の返値をそのまま返す

これらは frozen dataclass + mock だけで完結（subprocess 不要 → Small）。

### Medium テスト（bug 固有: 再現テスト必須）

新規 `tests/test_artifacts_dir.py`（Medium セクション）。fixture 共有方針は Should Fix 1
の決定に従う（暫定: 新規 `tests/conftest.py` に `bare_with_two_worktrees` を移管）。

3. **再現テスト1（resolve 経路）**: bare + main worktree + feature worktree 構成。
   両 worktree に `.kaji/config.toml` を配置（`paths.artifacts_dir = ".kaji-artifacts"` +
   `[provider] type="local"` + `[provider.local] machine_id="pc1" default_branch="main"`）。
   feature worktree から `KajiConfig.discover(start_dir=feat_wt)` → `cfg` 取得 →
   `resolve_artifacts_dir(cfg) == main_wt / ".kaji-artifacts"` を assert
   - **修正前に FAIL**（`resolve_artifacts_dir` がそもそも存在しない、または現実装
     `config.artifacts_dir` だと `feat_wt / ".kaji-artifacts"` が返る）
   - **修正後に PASS**

4. **再現テスト2（run 経路でログ残存・Issue 完了条件のテスト）**: 同 fixture を使い、
   `WorkflowRunner` 経路で artifacts への実書き込みが main 側に出ること、worktree
   削除後もファイルが残ることを assert する。

   実装方針（重い agent 実行を回避するため `SessionState` の直接駆動で代替）:

   1. feature worktree から `KajiConfig.discover(start_dir=feat_wt)` → `cfg`
   2. `artifacts_path = resolve_artifacts_dir(cfg)` → `main_wt / ".kaji-artifacts"`
      を確認（再現テスト1 と同じ assert を兼ねる）
   3. `WorkflowRunner` に `artifacts_dir=artifacts_path` を渡せばその先は不変なので、
      runner ではなく `SessionState.load_or_create("test-issue", artifacts_path)` を
      直接呼んで `state.persist()`（または `save()` 等、`state.py` の I/O API）を実行。
      これにより `<main_wt>/.kaji-artifacts/test-issue/session-state.json` が作られる
   4. `assert (main_wt / ".kaji-artifacts" / "test-issue" / "session-state.json").exists()`
   5. `assert not (feat_wt / ".kaji-artifacts").exists()`（feature 側には作られない）
   6. `subprocess.run(["git", "-C", str(bare), "worktree", "remove", "--force", str(feat_wt)], check=True)`
   7. 削除後も再度 `assert (main_wt / ".kaji-artifacts" / "test-issue" / "session-state.json").exists()`

   **何を mock し何を本物で駆動するか**:

   - 本物: `KajiConfig.discover` / `resolve_artifacts_dir` / `resolve_main_worktree`
     / `SessionState` の I/O 全経路 / `git worktree` 操作
   - mock しない: agent / step 実行（WorkflowRunner の step driver は呼ばない。これは
     LLM subprocess を起動して重く・恒久 CI で不安定なため）
   - この検証によって「`cmd_run` から WorkflowRunner に渡る artifacts_dir が main 基準
     になっている」「その artifacts_dir で書かれたファイルが worktree 削除後も残る」
     という Issue 完了条件 2 つの不具合シグナルを Medium 1 本で押さえる

5. **fallback 経路**: 非 git ディレクトリ（`tmp_path` 直下に `.kaji/config.toml` のみ
   配置）で `discover()` → `resolve_artifacts_dir(cfg)` を呼び、`_try_resolve_main_worktree`
   が `None` を返して legacy fallback（`tmp_path / "<artifacts_dir>"`）になることを assert
6. **`provider == None` 経路**: `.kaji/config.toml` に `[provider]` セクションを書かず
   discover →（Phase 3-e で `[provider]` 必須化済みのため実装時に `_load()` レベルで弾かれる可能性あり。
   その場合は `KajiConfig` を直接構築して `provider=None` を渡す test として実装。
   `_try_resolve_main_worktree` が早期 `None` を返すことの assert）

### Large テスト

不要。外部 API (GitHub/GitLab/Claude) との疎通は本変更に含まれない。`testing-convention.md`
§「正当化できる理由」の「実行時ロジック変更がなく、変更固有検証で十分」には該当しないが、
「物理的に作成不可」相当: 本変更は git worktree 配置と内部 I/O のみで完結し、外部 API
を touch しないため Large 観点が定義不能。Medium で完全に網羅される。

### 既存テストへの影響確認

- `tests/test_config.py` の既存 `artifacts_dir` 系テスト（L238-395）: `KajiConfig`
  の property semantics は不変のため修正不要（全 PASS のはず）
- `tests/test_runner.py:173` / `tests/test_preflight.py:75` は `cfg.artifacts_dir` を
  WorkflowRunner に渡すが fixture は tmp_path ベース → 影響なし
- `cmd_run` 経路を駆動する既存テスト（`tests/test_workflow_execution.py` 等）は
  tmp_path に `.kaji/config.toml` を置く構造で git worktree fixture は使わないため、
  `_try_resolve_main_worktree` が `None` を返し fallback で legacy 挙動に落ちる →
  既存 PASS を維持

## 改善提案 (Should Fix) への対応

- **SF1: fixture 共有方針**:
  `bare_with_two_worktrees` fixture（現在 `tests/test_resolve_main_worktree.py:77-108`
  に inline 定義）を `tests/conftest.py` に移管し、新規 `tests/test_artifacts_dir.py`
  と `tests/test_resolve_main_worktree.py` の両方から自動共有する。pytest の
  conftest fixture 解決ルールに従い import 不要 / decorator 重複なし。
- **SF2: `dataclasses.replace` の import 問題**:
  本改訂で `KajiConfig` に新規 field を追加しない方針に変更したため、`dataclasses.replace`
  は不要となった。本論点は moot。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 既存方針（gl:11 の main worktree 固定化ポリシー）の拡張適用であり、新規 ADR は不要 |
| `docs/ARCHITECTURE.md` | あり | § セッション管理と再開（L221-230 付近）に `artifacts_dir` の解決基準を追記 |
| `docs/dev/workflow-authoring.md` | あり（軽微） | `artifacts_dir` の解決基準を 1〜2 行で言及（worktree 内 `kaji run` の挙動）|
| `docs/dev/development_workflow.md` | なし | workflow 全体フローには影響なし（artifacts は内部実装） |
| `docs/dev/testing-convention.md` | なし | テスト規約自体は変更なし（新規テストが規約に従う） |
| `docs/reference/` | なし | API 仕様変更なし（公開 IF 不変） |
| `docs/cli-guides/local-mode.md` / `github-mode.md` / `gitlab-mode.md` | あり（軽微） | `artifacts_dir` の例示が 3 ファイルに存在（`docs/cli-guides/local-mode.md:32` 等）。解決基準への注記が望ましい場合は追記検討 |
| `CLAUDE.md` | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 該当コード（修正対象） | [`kaji_harness/config.py:110-116`](../../kaji_harness/config.py) | `return self.repo_root / self.paths.artifacts_dir` — 相対パスを `repo_root` 基準で解決している現実装 |
| `discover()` 実装 | [`kaji_harness/config.py:118-129`](../../kaji_harness/config.py) | `cwd` 起点で walk-up し `.kaji/config.toml` 発見ディレクトリを `repo_root` とする |
| `cmd_run` の artifacts 配線（唯一の write callsite） | [`kaji_harness/cli_main.py:396-400`](../../kaji_harness/cli_main.py) | `runner = WorkflowRunner(... artifacts_dir=config.artifacts_dir ...)` — 本修正の差し替え対象 |
| `discover()` の非-run callsite 一覧 | `kaji_harness/cli_main.py:252,284,843,2041,2083,2137,2170` | `cmd_validate` / `kaji issue|pr` dispatch / `provider-type` / `sync` 系。これらは artifacts に書き込まないため `discover()` に main worktree 解決を仕込まない設計判断の根拠 |
| `resolve_main_worktree` 複数一致時の stderr 副作用 | [`kaji_harness/providers/_worktree.py:92-95`](../../kaji_harness/providers/_worktree.py) | `sys.stderr.write(f"warning: multiple worktrees ...")` — `kaji issue` 系の stdout/stderr 契約に副作用を持ち込みたくない根拠 |
| gl:11 関連実装（LocalProvider main worktree 固定） | [`kaji_harness/providers/__init__.py:113-126`](../../kaji_harness/providers/__init__.py) | `# gl:11: cwd 起点 discover では feature worktree が repo_root になりうるため、LocalProvider の I/O 正本を ... main worktree に固定する。` — 本 issue が拡張適用する既存ポリシー |
| 既存 helper `resolve_main_worktree` | [`kaji_harness/providers/_worktree.py:41-96`](../../kaji_harness/providers/_worktree.py) | `start_dir` + `default_branch` から main worktree の絶対パスを返す。本修正で再利用 |
| best-effort fallback の前例 | [`kaji_harness/providers/__init__.py:204-210`](../../kaji_harness/providers/__init__.py) | `provider_overlay_divergence_warning` が `LocalProviderError` を握り潰す既存パターン |
| `git worktree` porcelain 仕様 | [git-worktree(1) — Porcelain Format](https://git-scm.com/docs/git-worktree#_porcelain_format) | `worktree`/`HEAD`/`branch` の key-value 行＋空行区切り。`resolve_main_worktree` が依存する出力契約 |
| Issue 本文（OB/EB/再現手順） | `kaji issue view 177` 出力（本設計書冒頭に引用） | 再現条件と期待挙動の一次情報 |
| 関連 PR #102 | [GH #102](https://github.com/apokamo/kaji/pull/102) | 絶対パス指定時の挙動は本 PR で対応済み（scope 外の確認） |
| docs-as-code テスト規約 | [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) § 変更タイプごとの期待値 | 実行時コード変更なので Small/Medium/Large の観点定義が必要。Large は外部 API 疎通が無いため不要と明記 |
