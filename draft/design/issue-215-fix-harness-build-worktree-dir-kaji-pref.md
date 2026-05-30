# [設計] build_worktree_dir の `kaji-` prefix ハードコードを config option 化する

Issue: #215

## 概要

`kaji_harness/providers/context.py` の `build_worktree_dir()` が worktree ディレクトリ名の prefix を `kaji-` でハードコードしているため、worktree prefix を別名に設定した consumer プロジェクトで `KAJI_WORKTREE_DIR` を `cwd` として参照する `review-poll` exec_script が `FileNotFoundError` でクラッシュする。prefix を `[paths].worktree_prefix` config option として外部化し、harness の算出値を consumer の実 worktree と一致させる。

## 背景・目的

### Observed Behavior（OB）

consumer プロジェクト kamo2 で `full-cycle.yaml` を Issue #1159 に対し実行したところ、`pr` ステップは PR #1162 の作成まで正常完了（VERDICT: PASS）したが、直後の `review-poll` exec_script ステップが exit code 1 でクラッシュしワークフローが ERROR 終了した。Issue #215 本文に引用された実世界障害ログ（kamo2 Issue #1159 / PR #1162、2026-05-30 UTC）:

```
Error: Step 'review-poll' exec_script 'kaji_harness.scripts.review_poll_entry' exited with code 1: Traceback (most recent call last):
  ...
  File ".../kaji_harness/scripts/review_poll_entry.py", line 99, in main
    remote_url = subprocess.run(
        ["git", "remote", "get-url", git_remote],
        ...
        cwd=worktree_dir or None,
    ).stdout.strip()
  ...
FileNotFoundError: [Errno 2] No such file or directory: '/home/aki/dev/kaji-refactor-1159'
```

`review_poll_entry.py` は `KAJI_WORKTREE_DIR` env を `worktree_dir` として読み、`git remote get-url <remote>` を `cwd=worktree_dir` で実行する。この値が存在しないパス `/home/aki/dev/kaji-refactor-1159` になっており、`subprocess` が cwd の chdir に失敗して `FileNotFoundError` を送出する。

実ログ（kamo2 リポジトリ側、本リポジトリからは参照不可のため上記トレースバックを引用）:
- `.kaji/artifacts/1159/runs/2605301248/review-poll/stderr.log`（完全なトレースバック）
- `.kaji/artifacts/1159/runs/2605301248/run.log`（各ステップの VERDICT）

### Expected Behavior（EB）

harness が算出する worktree パス（`IssueContext.worktree_dir`）は、consumer の `issue-start` skill が実際に作る worktree と常に一致しなければならない。worktree prefix は consumer ごとに異なり得る（kaji 本体は `kaji-`、kamo2 は `kamo2-`）ため、ハードコードではなく設定可能であるべき。無設定時は後方互換で `kaji-` を維持する。

EB の根拠（一次情報）:
- `kaji_harness/providers/context.py:75-81`（`build_worktree_dir` の現行ハードコード実装。本リポジトリ内で検証可能）
- consumer 規約: kamo2 `.claude/skills/issue-start/SKILL.md:33,57`（`../kamo2-<prefix>-<issue_id>` を作成 = harness が一致させるべき実体）

### 根本原因（Root Cause）

worktree ディレクトリ名の **命名規約が二重化** しており、harness 内部の算出値と実体が食い違う。

| コンポーネント | 算出パス | 実在 |
|---|---|---|
| consumer の `issue-start` skill（kamo2 `SKILL.md:33,57`） | `../kamo2-refactor-1159` | ✅ 実在（実際に作られた worktree） |
| harness `build_worktree_dir`（`context.py:81`） | `../kaji-refactor-1159` | ❌ 存在しない |

`context.py:75-81`（現行）:

```python
def build_worktree_dir(branch_prefix: str, issue_id: str, repo_root: Path) -> str:
    # 既存規約: <repo_parent>/kaji-<prefix>-<issue_id>
    return str(repo_root.parent / f"kaji-{branch_prefix}-{issue_id}")
```

prefix `kaji-` は kaji 自身の dev 規約に由来するが、consumer は各自の `issue-start/SKILL.md` で別 prefix を用いる。harness は consumer の skill が実際に作る worktree 名を知り得ないのに、prefix を決め打ちしているため両者が乖離する。

**なぜ `review-poll` だけがクラッシュするのか**:
- `start` / `design` / `implement` / `review-code` / `final-check` / `pr` は **agent dispatch** ステップ。各 skill が自前で正しい `kamo2-...` パスへ `cd` するため `KAJI_WORKTREE_DIR` に依存せず動作する。
- `review-poll` だけが **exec_script** ステップ。`runner.py:380` が `KAJI_WORKTREE_DIR = issue_context.worktree_dir`（= `build_worktree_dir` の誤算出値）を env 注入し、`review_poll_entry.py` が無検証で `subprocess(cwd=...)` に渡すため即死する。

結果として、worktree prefix を `kaji-` 以外に設定した全 consumer で `review-poll` 段（`review-cycle` / `review-close` / `full-cycle`）が **構造的に必ず失敗** する。

**同根の他壊れ箇所の調査**: `build_worktree_dir` の呼び出し元は本リポジトリ全体で `providers/github.py:328` と `providers/local.py:743` の 2 箇所のみ（`grep -rn build_worktree_dir` で確認）。いずれも `IssueContext.worktree_dir` の算出に使われ、同じ prefix ハードコードの影響を受ける。今回の修正で両 provider 経路を一括是正する。`review-poll` 以外で `worktree_dir` を `cwd` 実体参照する exec_script は現状存在しないが、将来の exec_script でも同じ前提（算出 == 実体）が必要になるため、prefix 整合は構造的に修正すべき。

## インターフェース

bug 修正のため IF は原則維持。`build_worktree_dir` のみ後方互換なシグネチャ拡張を行う。

### 入力

`build_worktree_dir` に `worktree_prefix` パラメータを追加（**キーワード引数 / デフォルト値付き**で後方互換）:

```python
def build_worktree_dir(
    branch_prefix: str,
    issue_id: str,
    repo_root: Path,
    worktree_prefix: str = "",
) -> str: ...
```

- `worktree_prefix`: 空文字 `""`（無設定）の場合は `"kaji"` にフォールバック。非空ならその値を採用。
- `PathsConfig` に `worktree_prefix: str = ""` フィールドを追加し、`.kaji/config.toml` の `[paths].worktree_prefix` を既存の dict-filter（`config.py:130-132`）で自動パース。

### 出力

```python
repo_root.parent / f"{worktree_prefix or 'kaji'}-{branch_prefix}-{issue_id}"
```

- 無設定: `<repo_parent>/kaji-<prefix>-<id>`（現行と完全一致 = 後方互換）
- `worktree_prefix = "kamo2"`: `<repo_parent>/kamo2-<prefix>-<id>`

副作用なし（純粋関数のまま）。

### 変更前 / 変更後（後方互換性評価）

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| `build_worktree_dir("feat","153",repo)` | `.../kaji-feat-153` | `.../kaji-feat-153`（不変） |
| `build_worktree_dir("feat","153",repo,"")` | （引数なし） | `.../kaji-feat-153` |
| `build_worktree_dir("feat","153",repo,"kamo2")` | （引数なし） | `.../kamo2-feat-153` |
| `PathsConfig` 既存 config（`worktree_prefix` 未記載） | — | `worktree_prefix=""` → `kaji-` 維持 |

既存呼び出し側（位置引数 3 つ）はそのまま動作する。`frozen=True` dataclass へのデフォルト付きフィールド追加も既存生成箇所を壊さない。

### 使用例

```python
# consumer .kaji/config.toml
# [paths]
# worktree_prefix = "kamo2"

config = KajiConfig.discover()
# config.paths.worktree_prefix == "kamo2"
provider = get_provider(config)  # worktree_prefix を provider へ注入
ctx = provider.issue_context("1159")
# ctx.worktree_dir == "/home/aki/dev/kamo2-refactor-1159"  ← 実体と一致
```

## 制約・前提条件

- 技術的制約: `build_worktree_dir` の呼び出し元は `providers/github.py:328` / `providers/local.py:743` の 2 箇所。両 provider dataclass（`GitHubProvider` / `LocalProvider`）に `worktree_prefix` フィールドを追加し、`get_provider`（`providers/__init__.py:96-123`）の構築時に `worktree_prefix=config.paths.worktree_prefix` を注入する plumbing が必要。
- 後方互換制約: kaji 本体・既存 consumer は `worktree_prefix` 無設定で `kaji-` を維持しなければならない（fallback `or 'kaji'`）。
- 不採用案: `repo_root.name` からの prefix 導出は **不採用**。kaji 本体は main worktree が `/home/aki/dev/kaji/main` のため `repo_root.name == "main"` となり `main-<prefix>-<id>` に化けて `kaji-` 規約を壊す。consumer ごとに異なる値であり設定で持つのが正しい。
- スコープ制約: `review_poll_entry.py` 側の cwd 防御（存在しないパスへの fail-loud 検証）は本 Issue のスコープ外。本 Issue は「算出値を実体と一致させる」根本原因の是正に限定し、リファクタ混在を避ける。

## 方針

最小侵襲。4 層の plumbing で config 値を `build_worktree_dir` まで伝搬する。

1. **`config.py` `PathsConfig`**: `worktree_prefix: str = ""` フィールドを追加。既存の dict-filter（`{k: v for k, v in paths_data.items() if k in PathsConfig.__dataclass_fields__}`）が `[paths].worktree_prefix` を自動的に拾うため、追加のパースロジックは不要。
2. **`context.py` `build_worktree_dir`**: `worktree_prefix: str = ""` 引数を追加し、`f"{worktree_prefix or 'kaji'}-{branch_prefix}-{issue_id}"` でフォーマット。docstring も更新。
3. **`providers/github.py` `GitHubProvider` / `providers/local.py` `LocalProvider`**: dataclass に `worktree_prefix: str = ""` フィールドを追加し、`build_worktree_dir(..., worktree_prefix=self.worktree_prefix)` で受け渡す。
4. **`providers/__init__.py` `get_provider`**: `GitHubProvider(...)` / `LocalProvider(...)` 構築時に `worktree_prefix=config.paths.worktree_prefix` を注入。

```python
# context.py
def build_worktree_dir(branch_prefix, issue_id, repo_root, worktree_prefix=""):
    return str(repo_root.parent / f"{worktree_prefix or 'kaji'}-{branch_prefix}-{issue_id}")
```

## テスト戦略

> 変更タイプ: **実行時コード変更**（純粋関数のロジック分岐 + config パース + provider plumbing）。bug 固有ルールに従い恒久回帰テストを追加する。

### 変更タイプ
- 実行時コード変更（`build_worktree_dir` のロジック / `PathsConfig` パース / provider dataclass 伝搬）

### 実装前 Red の扱い（escape clause）

bug 固有ルールでは修正前に Red になる再現テストが必須だが、本 Issue 本文に OB を直接示す実世界障害ログ（`FileNotFoundError` トレースバック・exit code 1・kamo2 Issue #1159 / PR #1162）が引用されている。恒久回帰テストはその OB に対応する EB（prefix 設定時に算出値が実体 `kamo2-` 系と一致すること）を検証するため、[`bug.md`](../../.claude/skills/_shared/design-by-type/bug.md) L66 の escape clause を適用し、実装前 FAIL ログの作成は省略する。**恒久回帰テスト自体（修正後 Green）は省略しない**。

#### Small テスト
純粋ロジック中心のため Small が主軸。`tests/test_providers_context.py` に追加:
- **① `worktree_prefix` 設定時の算出**: `build_worktree_dir("refactor", "1159", repo, "kamo2")` が `<parent>/kamo2-refactor-1159` を返す（OB の逆 = EB を assert）。
- **② 無設定時の後方互換**: `build_worktree_dir("feat", "153", repo)` および `build_worktree_dir("feat", "153", repo, "")` が `<parent>/kaji-feat-153` を返す（既存 `test_worktree_dir` を維持・補強）。
- **③ config パース**: `[paths].worktree_prefix = "kamo2"` を含む `config.toml` を `tmp_path` に書き、`KajiConfig.discover()` 後の `config.paths.worktree_prefix == "kamo2"` を検証。無記載時に `""`（デフォルト）になることも確認。

#### Medium テスト
- provider plumbing の伝搬（`get_provider(config).worktree_prefix` → `issue_context().worktree_dir` が prefix を反映）を検証。`get_provider` 経路は dispatch/provider 結合に該当し `subprocess.run` 名前空間 patch 禁止（[testing-convention](../../docs/dev/testing-convention.md) § patch スコープ）。GitHubProvider は外部 API 非依存な `worktree_dir` 算出のみを対象にすれば Small で足りるため、Medium は plumbing 結線の確認に必要な最小限に留める（GitHubProvider dataclass を直接構築し `issue_context` 相当の worktree_dir 算出を検証、または config → get_provider の field 伝搬を mock 最小で確認）。実 git/API 疎通は不要。

#### Large テスト
- 不要。本変更は外部 API / E2E の新規疎通を伴わず、prefix 算出と config パースは Small/Medium で完結する。`review-poll` exec_script の実 E2E 再現は kamo2 側実ログで既に OB が立証されており、kaji 本体側で実 GitHub 疎通を伴う Large を追加しても回帰検出情報がほとんど増えない（[testing-convention](../../docs/dev/testing-convention.md) 4 条件のうち「既存ゲート / 実ログで捕捉済み」「回帰検出情報増分が小さい」に該当）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし（既存 config 機構の拡張） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ構造は不変 |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし |
| docs/reference/ | なし | Python 規約・API 仕様の変更なし |
| docs/cli-guides/ | **あり** | `docs/cli-guides/github-mode.md:54-56` / `local-mode.md:31-33` の `[paths]` config 例に `worktree_prefix` の説明を追記（任意 option として、無設定時 `kaji-` 維持を明記） |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `build_worktree_dir` 現行実装 | `kaji_harness/providers/context.py:75-81` | `return str(repo_root.parent / f"kaji-{branch_prefix}-{issue_id}")` — prefix が `kaji-` でハードコードされている根本原因箇所 |
| `PathsConfig` / dict-filter パース | `kaji_harness/config.py:17-23, 130-132` | `PathsConfig` は `{k: v for k, v in paths_data.items() if k in PathsConfig.__dataclass_fields__}` でパースされ、フィールド追加だけで `[paths].worktree_prefix` を自動取得できる |
| provider 構築（plumbing 注入点） | `kaji_harness/providers/__init__.py:96-123` | `GitHubProvider(...)` / `LocalProvider(...)` 構築箇所。ここで `worktree_prefix=config.paths.worktree_prefix` を注入する |
| `build_worktree_dir` 呼び出し元 | `kaji_harness/providers/github.py:328` / `kaji_harness/providers/local.py:743` | `worktree_dir=build_worktree_dir(prefix, issue.id, self.repo_root)` — 伝搬すべき 2 箇所（`grep -rn build_worktree_dir` で全件確認済み） |
| `KAJI_WORKTREE_DIR` env 注入 | `kaji_harness/runner.py:380`（Issue #215 引用） | `KAJI_WORKTREE_DIR = issue_context.worktree_dir` を exec_script env に注入。誤算出値がそのまま review-poll の cwd になる経路 |
| OB 実世界障害ログ | Issue #215 本文引用（kamo2 `.kaji/artifacts/1159/runs/2605301248/review-poll/stderr.log`） | `FileNotFoundError: [Errno 2] No such file or directory: '/home/aki/dev/kaji-refactor-1159'` — 算出値と実体の乖離を立証。kamo2 repo は本リポジトリから参照不可のためトレースバックを本設計書に引用 |
| consumer worktree 命名規約 | kamo2 `.claude/skills/issue-start/SKILL.md:33,57`（Issue #215 引用） | `../kamo2-<prefix>-<issue_id>` を実際に作成 = harness が一致させるべき実体 prefix |
| 既存テスト規約 | `tests/test_providers_context.py:102-106` | `test_worktree_dir` の既存 assert 形式（`build_worktree_dir(...) == str(tmp_path / "kaji-feat-153")`）に倣う |
| bug escape clause | `.claude/skills/_shared/design-by-type/bug.md:66` | 実世界障害ログを実装前 Red 証跡の代替として扱える条件（恒久回帰テスト自体は省略不可） |
