# [設計] runner.py / providers/local.py の内部責務分割（R5）

Issue: #323

## 概要

`kaji_harness/runner.py` の `WorkflowRunner.run()`（666行）と
`kaji_harness/providers/local.py` の `LocalProvider`（609行・27メソッド）を、
公開 IF・CLI 挙動・artifact 形式を一切変えずに、責務ごとの名前付き内部コンポーネントへ分割する。
structural refactor に限定し、機能追加・既知バグ修正は扱わない（#291 の R5）。

## 背景・目的

### 現状の問題（観測値。2026-07-14、branch point main `19c305e`）

| 対象 | 観測値 | 集中している責務 |
|---|---:|---|
| `kaji_harness/runner.py` | 1,076行 | run artifact 採番、attempt 終了記録、preflight、context 解決、step 実行、verdict 遷移 |
| `WorkflowRunner`（`runner.py:217-1076`） | 860行・8メソッド | runner の主要 orchestration |
| `WorkflowRunner.run()`（`runner.py:411-1076`） | 666行 | preflight / run setup / step dispatch / verdict 解決 / 例外記録 / 遷移 / 終了記録が単一メソッドに集中 |
| `kaji_harness/providers/local.py` | 926行 | local issue storage、frontmatter、comment、git commit、GitHub cache、context 解決 |
| `LocalProvider`（`local.py:269-877`） | 609行・27メソッド | `IssueProvider` facade と storage / comment / cache / git の内部実装が同一クラスに集中 |

再計測コマンド（誰が実行しても同じ値が出る形）:

```bash
wc -l kaji_harness/runner.py kaji_harness/providers/local.py
rg -n '^class |^(async )?def |^    (async )?def ' \
  kaji_harness/runner.py kaji_harness/providers/local.py
# メソッド数はクラス定義行から次の top-level 定義までの `^    def ` を数える
```

追加の設計制約となる観測値（本設計の分割方式を決めた一次事実）:

```bash
# runner の collaborator を patch するテスト箇所数（module namespace 契約）
rg -o 'patch\("kaji_harness\.runner\.[a-zA-Z_]+' tests/ -n | wc -l   # → 約 200 箇所
# providers.local の内部を patch するテスト箇所数
rg -o 'patch\("kaji_harness\.providers\.local\.[a-zA-Z_]+' tests/ -n | wc -l   # → 0 箇所
```

`execute_cli` / `execute_script` / `execute_exec` / `execute_interactive_terminal` /
`validate_skill_exists` / `load_skill_metadata` / `create_verdict_formatter` /
`get_provider` / `discover_existing_worktree` / `datetime` / `os` が
**`kaji_harness.runner` module namespace で patch されている**（例:
`tests/test_workflow_execution.py:199`、`tests/test_verdict_artifact_runner.py:381`、
`tests/conftest.py:249`）。`unittest.mock` の patch は「参照が look up される
namespace の属性を置換する」ため（公式ドキュメント "Where to patch"）、
dispatch 呼び出しコードを別 module へ移すとこれら約 200 箇所の patch が
一斉に実行経路から外れ、振る舞い保存を担保する safety net 自体が壊れる。
一方 `providers/local.py` には patch 依存が 0 件であり、module 分割の自由度が高い。

### 改善指標（Issue #323 の測定可能な目標）

1. `WorkflowRunner.run()` を **250行以下** の orchestration に縮小する。
2. `LocalProvider` を **400行以下・20メソッド以下** の facade に縮小し、
   local storage / comment 永続化 / GitHub cache の内部責務を分離する。
3. 新設する**単一 callable に 250行超の処理を移さない**（blob 移動の禁止）。
4. `WorkflowRunner.run()`・`IssueProvider`・`LocalProvider` の既存公開 IF、
   CLI 出力、exit code、artifact 形式を**不変**とする。
5. `tests/test_private_imports.py` / `tests/test_layer_imports.py` の違反 **0 件維持**。

行数は責務集中の検出ガードであり、合否は本設計書の責務対応表・依存方向・
既存テストの全 PASS を併せて判定する。

## ベースライン計測

実装フェーズ冒頭で以下を再計測して Issue コメントに記録し、改修後と比較する:

```bash
# 1. 行数・構造
wc -l kaji_harness/runner.py kaji_harness/providers/local.py
rg -n '^class |^(async )?def |^    (async )?def ' \
  kaji_harness/runner.py kaji_harness/providers/local.py

# 2. テスト全体のベースライン（新規 FAILED/ERROR 0 件判定の基準）
source .venv/bin/activate && pytest -q 2>&1 | tail -5

# 3. 変更対象モジュールのカバレッジ（safety net 要否判定の入力）
pytest -q --cov=kaji_harness.runner --cov=kaji_harness.providers.local \
  --cov-report=term-missing 2>&1 | tail -30

# 4. fitness test の独立実行
pytest -q tests/test_private_imports.py tests/test_layer_imports.py

# 5. 品質ゲート
make check
```

着手時点（main `19c305e`）の既知値: runner.py = 1,076行、local.py = 926行、
`run()` = 666行、`WorkflowRunner` = 860行・8メソッド、`LocalProvider` = 609行・27メソッド。

## インターフェース

### 公開 IF は不変（宣言）

以下をすべて変更しない:

- `WorkflowRunner` の構築引数・`run() -> SessionState`・
  `canonical_issue_id` / `canonical_issue_ref` / `last_run_dir` 属性
- `kaji_harness.runner` から import 可能な既存公開名
  （`WorkflowRunner` / `allocate_run_dir` / `allocate_attempt_dir` / `RUN_ID_FORMAT`。
  利用側: `kaji_harness/commands/run.py:26`、`tests/test_verdict_artifact_runner.py:30`）
- `IssueProvider` protocol（`providers/base.py`）と `LocalProvider` の全 public メソッド署名
- `kaji_harness.providers.local` から import 可能な既存公開名
  （`LocalProvider` / `LocalProviderError` / `IssueNotFoundError` / `IssueReadOnlyError` /
  `validate_machine_id` / `MAX_COMMENT_WRITE_RETRIES`。
  利用側: `commands/issue.py:14`、`local_init.py:19`、`config.py:376`、
  `artifacts.py:52`、`providers/_worktree.py:17`、`providers/__init__.py:21`）
- CLI command、stdout/stderr、exit code、`runs/<run_id>/` artifact layout、
  `.kaji/issues/` on-disk format

### 内部 IF の新構造

#### 入力 / 出力

リファクタであり外部入出力は変わらない。変わるのは内部の呼び出し構造のみ。

**runner.py（module 分割しない。module 内コンポーネント抽出）**:

```python
# kaji_harness/runner.py（同一 module 内に追加される module-private 構造）

@dataclass(frozen=True)
class _StepOutcome:
    """1 attempt の実行結果。遷移判断の入力（遷移自体は run() が行う）。"""
    verdict: Verdict
    cost: CostInfo | None
    dispatched: bool          # cycle exhaust 合成 verdict なら False

@dataclass
class _StepExecutor:
    """1 step attempt の実行（採番 → dispatch → verdict 解決 → 終了記録）。

    per-run の不変参照（workflow / config / provider / run_ctx / run_dir /
    logger / state）を保持する。SessionState への書き込みは
    save_session_id / record_step（_record_attempt_end 経由）に限定し、
    遷移決定・cycle カウント・barrier 判定には関与しない。
    """
    def execute(self, step, cycle, metadata, issue_context) -> _StepOutcome: ...
    # 内部 helper（各 250 行以下、目安 30-120 行）:
    #   _dispatch(...)          # exec / exec_script / interactive_terminal / cli の 4 分岐
    #   _build_context_env(...) # script 系の KAJI_* env 構築
    #   _resolve_step_verdict(...)  # resolve_verdict 呼び出し + 正規化保存
    #   _record_dispatch_failure(...)  # 例外時の合成 ABORT 記録（re-raise 前提）

class WorkflowRunner:
    def run(self) -> SessionState: ...   # ≤250 行の orchestration に縮小
    # 抽出される private helper（いずれも self のフィールドを読む）:
    #   _collect_skill_metadata() -> dict[str, SkillMetadata | None]   # 現 Step 0
    #   _validate_before_step() -> None                                # 現 1.5
    #   _resolve_start_step() -> Step                                  # 現 Step 4
    #   _load_session_state(run_ctx) -> tuple[SessionState, IssueContext, Verdict | None]
    #                                    # 現 Step 3 + #218 backfill + ambiguous 検出
    #   _emit_ambiguous_worktree_abort(...) -> SessionState            # 現 561-587 早期終了
```

重要な設計決定: **`execute_cli` 等 collaborator の名前解決は
`kaji_harness.runner` module namespace に残す**。`_StepExecutor` は同一 module 内の
クラスであり、module global（`execute_cli` / `create_verdict_formatter` 等）を
呼び出し時に look up するため、既存テスト約 200 箇所の
`patch("kaji_harness.runner.<collaborator>")` がそのまま実行経路に効き続ける。
`tests/test_layer_imports.py` の `MODULE_LAYERS` も変更不要
（`kaji_harness.runner` は application 層 module のまま）。

**providers/local.py（package 内 private module へ分割）**:

```text
kaji_harness/providers/
  local.py            # LocalProvider facade（≤400行・≤20メソッド）
  _local_common.py    # 共有語彙: LocalProviderError / IssueNotFoundError /
                      #   IssueReadOnlyError / validate_machine_id /
                      #   frontmatter serialize・parse・validate /
                      #   _expected_id_from_dirname / 正規表現定数
  _local_store.py     # LocalIssueStore: dir layout・_resolve_issue_dir・
                      #   counter/flock 採番・issue.md read/write・labels 変換
  _local_comments.py  # LocalCommentStore: comments/ の read・atomic write
                      #   （+1s retry）・comment ref・MAX_COMMENT_WRITE_RETRIES
  _local_cache.py     # GitHubCacheReader: .kaji/cache/gh-*.json の read-only
                      #   組み立て（view / list / payload 整形）
```

```python
# providers/_local_store.py（署名イメージ。詳細は実装フェーズの裁量）
@dataclass
class LocalIssueStore:
    repo_root: Path
    machine_id: str
    def resolve_issue_dir(self, issue_id: str) -> Path: ...
    def next_local_id(self) -> int: ...
    def read_issue(self, issue_dir: Path) -> Issue: ...      # comments 込み
    def write_issue_md(self, issue_dir: Path, meta: dict, body: str) -> None: ...
```

依存方向（一方向のみ。逆流・循環なし）:

```text
local.py (facade)
  ├→ _local_store.py ─→ _local_comments.py ─→ _local_common.py
  ├→ _local_cache.py ──────────────────────→ _local_common.py
  └→ context.py / cache_guard.py / models.py（既存 public、変更なし）
```

`local.py` は `_local_common.py` の error 群・`validate_machine_id` を import して
自 namespace に bind する。これにより
`from kaji_harness.providers.local import IssueNotFoundError` 等の既存 import と
`except` の class identity は完全に維持される。これは ADR 008 が禁じる
「後方互換 shim の新設」ではなく、`local.py` が従来から持つ public module surface の
維持であり、ADR 009 決定 2 の「同一 package 内 private import = 許容」に該当する。

### 使用例（公開 IF が不変であることの確認）

```python
# 利用側コードは 1 文字も変わらない
from kaji_harness.providers.local import LocalProvider, IssueNotFoundError
from kaji_harness.runner import WorkflowRunner

provider = LocalProvider(repo_root=repo, machine_id="pc1")
issue = provider.create_issue(title="t", body="b", labels=["type:bug"])

runner = WorkflowRunner(
    workflow=wf, issue_number="323", project_root=root,
    artifacts_dir=artifacts, config=config,
)
state = runner.run()
```

## 責務対応表（抽出先・依存方向・状態所有者）

### `WorkflowRunner.run()` の責務分類

| 現行 run() 内区間（`runner.py` 行） | 責務 | 抽出先 | 依存方向 | 状態所有者 |
|---|---|---|---|---|
| 424-452 | skill 存在 + metadata preflight（L2） | `_collect_skill_metadata()` | runner → skill | なし（dict を返すのみ） |
| 454-463 | workflow / `--before` / `--reset-cycle` 検証 | `_validate_before_step()`＋既存 `_validate_cycle_reset()` | runner → workflow | なし（純粋検証） |
| 465-475 | canonical issue context + provider 構築 | 既存 `_resolve_run_issue_context()` を継続利用 | runner → providers | `RunIssueContext`（frozen） |
| 477-516 | state ロード + #218 worktree backfill / override | `_load_session_state()` | runner → state / worktree_discovery | `SessionState` は `WorkflowRunner` が所有 |
| 518-533 | 開始 step 決定 | `_resolve_start_step()` | runner → workflow | なし |
| 535-549 | run_dir 採番 + recovery chain + RunLogger 生成 | run() に残す（10 行程度） | runner → logger / recovery | run_dir / logger は `WorkflowRunner` が所有 |
| 561-587 | ambiguous worktree の ABORT 早期終了 | `_emit_ambiguous_worktree_abort()` | runner → logger / state | `SessionState.last_transition_verdict` |
| 656-942 | attempt 実行（PR context / session id / attempt 採番 / dispatch 4 分岐 / verdict 解決・正規化保存 / 例外時合成 ABORT 記録） | `_StepExecutor.execute()`（同一 module 内 dataclass） | runner → cli / script_exec / interactive_terminal / prompt / verdict / result | attempt-scoped 状態（attempt_dir / 時刻 / exit_code）を所有。`SessionState` へは `save_session_id` / `record_step` 経由のみ |
| 594-654, 944-1034 | loop 骨格（barrier / worktree capture / cycle exhaust / cycle count / 遷移決定） | run() に残す | runner → models | `current_step` / `last_verdict` / `issue_context` |
| 1036-1076 | 終了処理（barrier missed WARN / end status / `workflow_end`） | run() の try/finally に残す | runner → logger | `end_status` / `end_error` |

module-level の `allocate_run_dir` / `allocate_attempt_dir` / `_record_attempt_end` は
既に独立した cohesive helper であり移動しない（`patch("kaji_harness.runner.os")` /
`patch("kaji_harness.runner.datetime")` の patch 契約も維持される）。

### `LocalProvider` の責務分類

| 現行シンボル（`local.py`） | 責務 | 抽出先 | 依存方向 | 所有データ |
|---|---|---|---|---|
| `LocalProviderError` / `IssueNotFoundError` / `IssueReadOnlyError` / `validate_machine_id` / `_serialize_frontmatter` / `_parse_frontmatter` / `_validate_issue_meta` / `_expected_id_from_dirname` / 正規表現定数 | 共有語彙・検証 | `_local_common.py` | 外部依存 yaml のみ（package 内 leaf） | なし（純粋関数） |
| `_issues_dir` / `_counter_path` / `_resolve_issue_dir` / `_existing_local_max` / `_next_local_id` / `_counter_lock` / `_emit_windows_warning` / `_read_issue` / `_build_issue_md` / `_labels_from_meta` | local issue storage | `_local_store.py`（`LocalIssueStore`） | → `_local_common` / `_local_comments` / `fsio` | `.kaji/issues/` と `.kaji/counters/` |
| `_read_comments` / `comment_issue` の write 実体（atomic create + retry）/ `_comment_ref` / `MAX_COMMENT_WRITE_RETRIES` / `_COMMENT_FILENAME_RE` | comment 永続化 | `_local_comments.py`（`LocalCommentStore`） | → `_local_common` / `fsio` | issue dir 配下の `comments/` |
| `_cache_dir_root` / `_github_cache_path` / `view_cached_issue` の実体 / `_list_cached_github_issues` / `_cached_github_issue_from_payload` | GitHub cache reader | `_local_cache.py`（`GitHubCacheReader`） | → `_local_common` / `cache_guard` | `.kaji/cache/gh-*.json`（read-only） |
| `create_issue` / `view_issue` / `edit_issue` / `close_issue` / `comment_issue` / `list_issues` / `list_labels` / `resolve_issue_context` / `resolve_pr_context` / `view_cached_issue` / `commit_issue_change` / `is_readonly` / `is_readonly_id` / `__post_init__` | `IssueProvider` facade（CRUD の meta 編集 orchestration・IssueContext 組み立て・git commit・read-only 判定） | `local.py` に残す | → `_local_store` / `_local_comments` / `_local_cache` / `context` | config 由来値（`machine_id` / `default_branch` / `git_remote` / `worktree_prefix`） |

facade 残置メソッドは上記 14 + 内部コンポーネント保持用 property 数個で
**20 メソッド以下**に収まる。`commit_issue_change`（git 経路、約 35 行）は
「Issue 変更を repo に記録する」という facade の公開契約そのものなので facade に残す
（`kaji issue comment --commit` の CLI 挙動不変を最短距離で保証する）。
facade が 400 行を超過しそうな場合は、`edit_issue` / `close_issue` の
frontmatter 更新実体を `LocalIssueStore` 側へ寄せて調整する（公開署名は不変）。

## 制約・前提条件

- **振る舞い非変更が絶対要件**: CLI command、stdout/stderr、exit code、
  `runs/` artifact、`.kaji/issues/` on-disk format、state file を変えない。
- **テスト patch 契約**: `patch("kaji_harness.runner.<collaborator>")` 約 200 箇所を
  無効化しない。したがって runner は module 分割せず、module 内コンポーネント抽出とする
  （`unittest.mock` "Where to patch" が根拠）。この patch 集中自体の解消は
  本 Issue の scope 外（必要なら別 Issue 起票）。
- **ADR 009 遵守**: 新 module はすべて `kaji_harness.providers` package 内の
  private module（`_local_*.py`）。`MODULE_LAYERS` は prefix 一致
  （`tests/test_layer_imports.py:72-81` の longest-prefix matching）で
  `kaji_harness.providers.*` を provider 層に自動分類するため**変更不要**。
  package 外から `_local_*.py` を import することは禁止
  （`tests/test_private_imports.py` が強制）。
- **ADR 008 遵守**: deprecated 互換レイヤを新設しない。`local.py` の名前 bind は
  既存 public surface の維持（上記「内部 IF の新構造」参照）。
- **混在禁止**: feat / bug 修正 / provider 抽象の再設計 / CLI 仕様変更を混ぜない。
  作業中に発見したバグは修正せず別 Issue に起票する。
- **単一 callable 上限**: 新設 callable はいずれも 250 行以下
  （現行最大の移動対象は `comment_issue` write 実体 約 65 行、
  `_list_cached_github_issues` 約 59 行、`_StepExecutor.execute()` は
  内部 helper 分解により 250 行以下に抑える）。
- 依存追加なし（stdlib + 既存依存のみ）。

## 変更スコープ

- 変更: `kaji_harness/runner.py`（module 内再構成）、`kaji_harness/providers/local.py`（facade 化）
- 新規: `kaji_harness/providers/_local_common.py` / `_local_store.py` /
  `_local_comments.py` / `_local_cache.py`
- テスト: 既存テストは原則無変更（公開 IF 経由のため）。抽出コンポーネントの
  直接テストを必要最小限追加（下記テスト戦略）。
- docs: `docs/ARCHITECTURE.md` の module tree（97行目 / 104行目付近）に
  providers 内部 module を 1-2 行追記。ADR 新設なし。
- 段階分割: 2 対象は独立しているため、実装は
  「local.py 分割 → 全テスト → runner.py 再構成 → 全テスト」の 2 段で行う
  （どちらかで問題が出ても切り戻し範囲が半分で済む）。PR は 1 本にまとめる。

## 方針（Before / After と移行ステップ）

### Before → After（runner.py）

```text
Before: run() 666 行
  [preflight 40行][検証 10行][context 10行][state+backfill 40行][開始step 16行]
  [run_dir+logger 15行][ambiguous早期終了 27行][main loop 440行][終了処理 40行]

After: run() ≤250 行（骨格のみ）
  run() ── _collect_skill_metadata()
       ├── validate_workflow / _validate_before_step / _validate_cycle_reset
       ├── _resolve_run_issue_context()（既存）
       ├── _load_session_state() → (state, issue_context, ambiguous_abort)
       ├── _resolve_start_step()
       ├── ambiguous_abort → _emit_ambiguous_worktree_abort() で早期 return
       └── while loop:
             barrier / worktree capture / cycle exhaust 判定（run() に残す）
             outcome = _StepExecutor.execute(step, cycle, metadata, issue_context)
             cycle count / 遷移決定 / barrier（run() に残す）
```

### Before → After（providers/local.py）

```text
Before: local.py 926 行（module 関数 + LocalProvider 609行・27メソッド）

After:  local.py ≈ facade のみ
  LocalProvider ── LocalIssueStore（_local_store.py）── LocalCommentStore（_local_comments.py）
              ├── GitHubCacheReader（_local_cache.py）
              └── context.py builders（既存・変更なし）
  共有語彙は _local_common.py（errors / frontmatter / validate）
```

### 移行ステップ（実装フェーズの順序）

1. ベースライン計測（上記 § ベースライン計測の 1〜5）を実行し Issue コメントに記録
2. カバレッジ計測結果から safety net の不足箇所を特定し、**不足があれば先に**
   characterization test を追加（現時点の評価では主要経路は既存テストで保護済み。
   下記テスト戦略参照）
3. `providers/local.py` 分割（patch 依存 0 件のため先行）: `_local_common` →
   `_local_cache` → `_local_comments` → `_local_store` の順に leaf から抽出し、
   各段で `pytest -q` 全実行
4. `runner.py` module 内再構成: private helper 抽出 → `_StepExecutor` 抽出。
   コード移動は機械的に行い、ロジック変更を混ぜない
5. 再計測（行数・メソッド数・fitness test・`make check`）し、改善指標との差分を
   Issue コメントに記録
6. `docs/ARCHITECTURE.md` の module tree を実差分に合わせて更新

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 詳細は [テスト規約](../../docs/dev/testing-convention.md) 参照。

### 変更タイプ

実行時コード変更（structural refactor / 振る舞い非変更）。

### 実行時コード変更の場合

refactor 固有ルール（`_shared/design-by-type/refactor.md`）に従い、
**既存テストを bridging test として流用**し、新規テストは
「抽出により独立して検証可能になった単位」への最小追加に限定する。

#### 既存テストによる振る舞い保存（bridging / safety net の主体）

| 保護対象経路 | 既存テスト（エビデンス） |
|---|---|
| runner step 実行 / 遷移 / cycle | `tests/test_workflow_execution.py`、`tests/test_cycle_limit.py`、`tests/test_runner.py` |
| verdict 解決順・artifact 正規化 | `tests/test_verdict_artifact_runner.py`、`tests/test_verdict_integration.py` |
| dispatch 4 分岐 | `tests/test_exec_step_dispatch.py`、`tests/test_runner_exec_script_dispatch.py`、`tests/test_runner_interactive_dispatch.py` |
| barrier / reset-cycle / worktree backfill | `tests/test_runner_before.py`、`tests/test_runner_reset_cycle.py`、`tests/test_runner_worktree_persistence.py` |
| 失敗記録 / recovery events（synthetic ABORT・ambiguous worktree 含む） | `tests/test_recovery_runner_events.py`、`tests/test_recovery_cli.py` |
| local provider CRUD / comment / context | `tests/test_providers_local.py`、`tests/test_comment_ref.py`、`tests/test_providers_context.py` |
| GitHub cache 経路 | `tests/test_sync_from_github.py`、`tests/test_legacy_forge_cache_detection.py` |
| CLI / exit code / artifact 形式（E2E） | `tests/test_local_cli_large_local.py`、`tests/test_exec_step_e2e_large_local.py`、`tests/test_verdict_artifact_e2e_large_local.py`、`tests/test_recovery_e2e_large_local.py` |

実装フェーズ冒頭のカバレッジ計測で `runner.py` / `local.py` に未カバー区間が
見つかった場合、**その区間に触れる抽出を行う前に** characterization test を追加する
（safety net 先行の原則）。

#### Small テスト（新規・最小限）

- `_StepOutcome` / `_StepExecutor` の dispatch 種別決定（exec / exec_script / agent の
  3 値決定ロジック）— 抽出後に単体で検証可能になる純粋判定
- `_local_common.py` の frontmatter parse / validate — 既存
  `test_providers_local.py` の該当ケースが facade 経由で通ることを確認し、
  import 先変更が必要なテストのみ書き換え（検証内容は変えない）

#### Medium テスト（新規・最小限）

- `LocalIssueStore` / `LocalCommentStore` の filesystem 経路（tmp_path 上の
  採番・atomic write・retry）— 既存 facade 経由テストが同経路を覆っているため、
  **既存テストで検証できない差分が生じた場合のみ**追加する（機械的な全 S/M/L
  要求はしない。理由: 振る舞い非変更 refactor で facade 経由の既存 Medium テストが
  同一コードパスを実行し続けるため、新規テストの回帰検出情報の増分が小さい）

#### Large テスト

- 新規追加なし。既存 `large_local` suite（上表）と全 suite 実行で CLI /
  exit code / artifact / on-disk format の非変更を確認する。
  実 GitHub API 疎通（`large_forge`）は本変更が provider の GitHub 経路に
  触れないため対象外。

#### fitness test（独立実行）

```bash
pytest -q tests/test_private_imports.py tests/test_layer_imports.py
```

新 private module の追加後も違反 0 件・未分類 module 0 件であることを確認する。

#### 合否判定

- ベースラインに対する新規 FAILED / ERROR が 0 件
- `make check`（ruff / ruff format / mypy / pytest）全 PASS
- 再計測値が改善指標（run() ≤250行、LocalProvider ≤400行・≤20メソッド、
  新設 callable ≤250行）を満たす

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。ADR 009 決定 2（package 内 private module + facade 公開）の適用例が増えるのみで規約自体は不変。実装後に ADR 009 の記述と矛盾が出ないことを確認する |
| docs/ARCHITECTURE.md | あり（軽微） | module tree（`runner.py` 97行目 / `local.py` 104行目付近）に providers 内部 module 構成を追記 |
| docs/dev/ | なし | ワークフロー・開発手順に変更なし |
| docs/reference/ | なし | API 仕様・規約に変更なし |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #323（本 Issue） | https://github.com/apokamo/kaji/issues/323 | 改善指標（run() ≤250行、LocalProvider ≤400行・≤20メソッド、単一 callable ≤250行、公開 IF 不変、fitness 違反 0 件）と scope 境界の正本 |
| 親 Issue #291 | https://github.com/apokamo/kaji/issues/291 | R0〜R4 完了後の再計測で runner.py / local.py が最大責務単位として残存し、R5 を別 Issue 化した経緯 |
| ADR 009 | `docs/adr/009-module-boundary-private-import.md` | 決定 1「依存の向きを層で固定」（providers は foundation のみに依存）、決定 2「同一 package 内の private import は許容、package 外へは `__init__.py` facade で最小公開」、決定 3「境界の強制は fitness test が担う」— `_local_*.py` 分割方式の直接の根拠 |
| ADR 008（ADR 009 内参照） | `docs/adr/009-module-boundary-private-import.md` L103 | 「本規約は互換レイヤを新規に書かない」— 互換 shim を作らず public module の名前 bind 維持で対応する判断の根拠 |
| unittest.mock 公式「Where to patch」 | https://docs.python.org/3/library/unittest.mock.html#where-to-patch | 「patch は対象が look up される場所（namespace）を patch する」— `patch("kaji_harness.runner.execute_cli")` 約 200 箇所を無効化しないため、runner を module 分割せず module 内抽出とする設計決定の根拠 |
| layer fitness test | `tests/test_layer_imports.py:72-81` | `layer_of()` は longest-prefix matching。`kaji_harness.providers.*` は既存 entry `"kaji_harness.providers": "provider"` で自動分類され `MODULE_LAYERS` 変更不要 |
| private import fitness test | `tests/test_private_imports.py` | `pkg(M) == pkg(T)` の private import は許容分類 — providers package 内の `_local_*.py` 相互 import が違反 0 件で成立する根拠 |
| 対象コード | `kaji_harness/runner.py:411-1076` / `kaji_harness/providers/local.py:269-877` | 分割対象の現行実装（責務対応表の行番号根拠） |
| テスト patch 実態 | `tests/test_workflow_execution.py:199` ほか約 200 箇所 / `tests/conftest.py:249,259,289` | `kaji_harness.runner` namespace への patch 集中の観測（本文の rg コマンドで再現可能） |
| Refactoring カタログ（Fowler） | https://refactoring.com/catalog/extractClass.html | Extract Class:「一部のデータとメソッドのまとまりが分離可能ならクラスとして抽出する」— `_StepExecutor` / `LocalIssueStore` 等の抽出手法の一般的根拠 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準、「変更の性質に応じて恒久回帰テストと変更固有検証を切り分ける」、refactor での既存テスト流用の妥当性判断 |
| R4 設計書（シリーズ前例） | Issue #286 / `draft/design/issue-286-refactor-cli-domain-separation-r4.md`（worktree 消滅後は Issue #286 本文 NOTE 直下の添付を参照） | command 層分離で確立した「責務対応表 + fitness test + 段階 commit」の進め方を踏襲 |
