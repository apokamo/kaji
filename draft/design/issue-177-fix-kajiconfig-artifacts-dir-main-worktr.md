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

bug 修正のため公開 IF は維持する。内部実装の変更のみ。

### 変更前

```python
@dataclass(frozen=True)
class KajiConfig:
    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig
    provider: ProviderConfig | None = None
    provider_overlay_present: bool = False

    @property
    def artifacts_dir(self) -> Path:
        expanded = Path(self.paths.artifacts_dir).expanduser()
        if expanded.is_absolute():
            return expanded
        return self.repo_root / self.paths.artifacts_dir
```

### 変更後

```python
@dataclass(frozen=True)
class KajiConfig:
    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig
    provider: ProviderConfig | None = None
    provider_overlay_present: bool = False
    # 新規: main worktree (default_branch を checkout している worktree) の絶対パス。
    # discover() が best-effort で populate する。None の場合は repo_root に fallback。
    main_worktree_root: Path | None = None

    @property
    def artifacts_dir(self) -> Path:
        expanded = Path(self.paths.artifacts_dir).expanduser()
        if expanded.is_absolute():
            return expanded
        base = self.main_worktree_root or self.repo_root
        return base / self.paths.artifacts_dir
```

**後方互換性**:

- 絶対パス指定時の挙動は不変（PR #102 で対応済みの経路）
- `_load()` を直接呼ぶ既存テスト（`tests/test_config.py` 等）は `main_worktree_root=None`
  のまま動くため `repo_root` 基準のまま → 既存挙動維持
- production 起点である `KajiConfig.discover()` のみが `main_worktree_root` を
  populate する。`cli_main._cmd_run` から `config.artifacts_dir` を呼んだ際だけ
  main worktree 解決が効く

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/config.py` | `KajiConfig` に `main_worktree_root` field 追加。`artifacts_dir` property の base 切替。`discover()` で best-effort 解決を追加 |
| `tests/test_config.py` | 既存 `artifacts_dir` 関連テストの不変性を確認。新規テスト追加（property の分岐 + discover 経由の解決）|
| 新規 `tests/test_artifacts_dir_worktree.py`（または既存 `tests/test_config.py` に追記）| Medium: bare + main worktree + feature worktree の fixture を組み、feature 配下から `discover()` → `artifacts_dir` が main worktree 配下を指すことを検証 |
| `docs/ARCHITECTURE.md` | § セッション管理と再開 に「`artifacts_dir` の解決基準は main worktree」明記 |
| `docs/dev/workflow-authoring.md` | `artifacts_dir` の解決基準を 1〜2 行で言及（worktree 内 `kaji run` の挙動）|

Scope 外:

- 絶対パス指定時の挙動（PR #102 既対応）
- `.kaji-artifacts/` に溜まった既存ログのマイグレーション（Issue 本文「スコープ外」）
- `LocalProvider` / `GitHubProvider` / `GitLabProvider` の `repo_root` 解決ロジック
  （artifacts とは独立した責務）

## 方針（修正アプローチ）

### Step 1: `discover()` で main worktree を best-effort 解決

`KajiConfig.discover()` は `_load()` で構築した config から `provider.<type>.default_branch`
を取り出し、`resolve_main_worktree()` を呼んで main worktree root を populate する。
失敗時は `None` のまま継続（fallback path で legacy 挙動）。

```python
@classmethod
def discover(cls, start_dir: Path | None = None) -> KajiConfig:
    current = (start_dir or Path.cwd()).resolve()
    while True:
        candidate = current / ".kaji" / "config.toml"
        if candidate.is_file():
            cfg = cls._load(candidate)
            return cls._with_main_worktree(cfg)
        parent = current.parent
        if parent == current:
            raise ConfigNotFoundError(start_dir or Path.cwd())
        current = parent

@staticmethod
def _with_main_worktree(cfg: KajiConfig) -> KajiConfig:
    """Best-effort で main worktree root を populate."""
    if cfg.provider is None:
        return cfg
    # 各 provider config から default_branch を取り出す（属性名は type に対応）
    default_branch = getattr(cfg.provider, cfg.provider.type).default_branch
    try:
        from .providers._worktree import resolve_main_worktree
        from .providers.local import LocalProviderError
        main_root = resolve_main_worktree(
            start_dir=cfg.repo_root,
            default_branch=default_branch,
        )
    except LocalProviderError:
        return cfg  # main worktree 解決失敗 → fallback (legacy: repo_root)
    return dataclasses.replace(cfg, main_worktree_root=main_root)
```

**設計判断**:

1. **discover() 内に local 解決を仕込む理由**: production の唯一の entry point は
   `cli_main._cmd_run` → `KajiConfig.discover()`。`_load()` は test direct call 経路
   を含むため、subprocess 起動を入れると test に git 環境を要求してしまう。
   discover() に閉じ込めれば test fixture との分離が保てる
2. **`LocalProviderError` を握り潰す理由**: 非 git 環境 / main worktree 未構成では
   legacy 挙動（`repo_root` 基準）に fallback する。`gl:28` の `provider_overlay_divergence_warning`
   と同じパターン（best-effort）。エラー時に fail する設計は破壊的変更（既存運用が
   非 git ディレクトリで実行している可能性を考慮）
3. **`resolve_main_worktree` 自体は touch しない**: 既存の `_worktree.py` は LocalProvider
   特化 docstring を持つが API（`start_dir` + `default_branch`）は generic。reuse する。
   ただし import は遅延（discover() 内）にして config.py の `import` 順序を変えない

### Step 2: `artifacts_dir` property の base 切替

`main_worktree_root or repo_root` で fallback。1 行修正。

### Step 3: ドキュメント更新

- `docs/ARCHITECTURE.md` の `artifacts_dir` 説明箇所（既存記述 L224-229）に「relative
  path は main worktree 基準で解決される」旨を追記
- `docs/dev/workflow-authoring.md` で必要なら言及

## テスト戦略

### 変更タイプ
- **実行時コード変更**（`KajiConfig` の property 解決ロジック変更 + `discover()` の挙動追加）

### Small テスト

新規追加（`tests/test_config.py` への追記想定）:

1. `KajiConfig.artifacts_dir` property の分岐網羅
   - 絶対パス → そのまま返す（既存テスト互換）
   - 相対パス + `main_worktree_root=None` → `repo_root / 相対パス` に解決（既存挙動）
   - 相対パス + `main_worktree_root=Path("/some/main")` → `/some/main/相対パス` に解決（新規）
   - `~/...` パス → `expanduser()` 後に絶対化（既存テスト互換）

これは frozen dataclass 引数の組合せだけで検証可能（subprocess 不要 → Small）。

### Medium テスト（bug 固有: 再現テスト必須）

新規追加（既存 `tests/test_resolve_main_worktree.py` の `bare_with_two_worktrees`
fixture を流用想定）:

2. **再現テスト（regression）**: bare + main worktree + feature worktree 構成で、
   feature worktree 内に `.kaji/config.toml` を配置し `paths.artifacts_dir = ".kaji-artifacts"`
   と設定。`KajiConfig.discover(start_dir=feat_wt)` を呼び、
   `config.artifacts_dir == main_wt / ".kaji-artifacts"` を assert
   - **このテストは修正前に FAIL** すること（property が `repo_root` を返すため
     `feat_wt / ".kaji-artifacts"` が返り、assert 失敗）
   - **修正後に PASS** すること（`discover()` が `main_worktree_root` を populate し
     property が main 基準で解決）
3. fallback 経路: 非 git ディレクトリで `discover()` 呼出時、`main_worktree_root` が
   `None` のまま `artifacts_dir` は `repo_root` 基準で解決されることを確認
   （`LocalProviderError` 握り潰しの挙動）
4. `provider == None` 経路: `[provider]` 未設定の `.kaji/config.toml` で `discover()`
   呼出 → `main_worktree_root=None` 維持 → 既存挙動

### Large テスト

不要。外部 API (GitHub/GitLab/Claude) との疎通は本変更に含まれない。`docs/dev/testing-convention.md`
の「物理的に作成不可」条件には該当しないが、「不正当な理由」リストの観点でも該当しない
（変更が touch しないため）。

### 既存テストへの影響確認

- `tests/test_config.py` の既存 `artifacts_dir` 系テスト（L238-395）は `_load()` を
  直接呼ぶか `discover()` を非 git tmp_path で呼ぶため、いずれも `main_worktree_root=None`
  になり既存挙動が保たれる → **修正不要**
- `tests/test_runner.py:173` / `tests/test_preflight.py:75` は `cfg.artifacts_dir` を
  WorkflowRunner に渡すが fixture は tmp_path ベース → 影響なし

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
| gl:11 関連実装（LocalProvider main worktree 固定） | [`kaji_harness/providers/__init__.py:113-126`](../../kaji_harness/providers/__init__.py) | `# gl:11: cwd 起点 discover では feature worktree が repo_root になりうるため、LocalProvider の I/O 正本を ... main worktree に固定する。` — 本 issue が拡張適用する既存ポリシー |
| 既存 helper `resolve_main_worktree` | [`kaji_harness/providers/_worktree.py:41-96`](../../kaji_harness/providers/_worktree.py) | `start_dir` + `default_branch` から main worktree の絶対パスを返す。本修正で再利用 |
| best-effort fallback の前例 | [`kaji_harness/providers/__init__.py:204-210`](../../kaji_harness/providers/__init__.py) | `provider_overlay_divergence_warning` が `LocalProviderError` を握り潰す既存パターン |
| `git worktree` porcelain 仕様 | [git-worktree(1) — Porcelain Format](https://git-scm.com/docs/git-worktree#_porcelain_format) | `worktree`/`HEAD`/`branch` の key-value 行＋空行区切り。`resolve_main_worktree` が依存する出力契約 |
| Issue 本文（OB/EB/再現手順） | `kaji issue view 177` 出力（本設計書冒頭に引用） | 再現条件と期待挙動の一次情報 |
| 関連 PR #102 | [GH #102](https://github.com/apokamo/kaji/pull/102) | 絶対パス指定時の挙動は本 PR で対応済み（scope 外の確認） |
| docs-as-code テスト規約 | [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) § 変更タイプごとの期待値 | 実行時コード変更なので Small/Medium/Large の観点定義が必要。Large は外部 API 疎通が無いため不要と明記 |
